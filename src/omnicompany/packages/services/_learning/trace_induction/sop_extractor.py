# [OMNI] origin=claude-code domain=trace_induction/sop_extractor.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:learning.trace_induction.sop_extractor.engine.py"
"""sop_extractor — 从历史 trace 中提取操作步骤（SOP）

SOP 提取是需求文档撰写的前置步骤。
从 intent_steps 表中读取一个或多个 trace 的完整操作记录，
通过 LLM 标注每步的重要性并合并共同模式，输出结构化 SOP。

设计来源: DESIGN-trace-induction.md §SOP提取详细流程
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from omnicompany.runtime.storage.db_access import open_db

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────

@dataclass
class TraceStep:
    """intent_steps 表中的一条记录。"""
    step_num: int
    tool_name: str
    desc: str
    rationale: str
    tool_args_summary: str
    tool_result: str
    tool_exit_ok: int      # -1=pending, 0=fail, 1=success
    action_class: str      # execute|acquire|summarize|think
    input_types: list[str]
    output_types: list[str]


@dataclass
class SOPStep:
    """SOP 中的一个步骤。"""
    id: int
    action: str                    # 做什么
    tool: str                      # 用什么工具
    tool_args_pattern: str = ""    # 工具参数模板
    input: str = ""                # 输入
    output: str = ""               # 输出
    notes: str = ""                # 注意事项
    rules: list[str] = field(default_factory=list)  # 业务规则


@dataclass
class SOPErrorHandler:
    """SOP 中的错误处理规则。"""
    condition: str
    action: str


@dataclass
class SOP:
    """结构化 SOP — 从历史 trace 提炼的操作规范。"""
    purpose: str                             # 做什么（一句话）
    preconditions: list[str]                 # 前置条件
    steps: list[SOPStep]                     # 步骤列表
    error_handling: list[SOPErrorHandler]    # 错误处理
    derived_from: list[str]                  # 来源 trace_ids
    extraction_method: str                   # single-trace | multi-trace-merge

    def to_yaml_str(self) -> str:
        """输出 YAML 格式字符串（供需求文档使用）。"""
        import yaml
        data = {
            "sop": {
                "purpose": self.purpose,
                "preconditions": self.preconditions,
                "steps": [
                    {
                        "id": s.id,
                        "action": s.action,
                        "tool": s.tool,
                        **({"tool_args_pattern": s.tool_args_pattern} if s.tool_args_pattern else {}),
                        **({"input": s.input} if s.input else {}),
                        **({"output": s.output} if s.output else {}),
                        **({"notes": s.notes} if s.notes else {}),
                        **({"rules": s.rules} if s.rules else {}),
                    }
                    for s in self.steps
                ],
                "error_handling": [
                    {"condition": e.condition, "action": e.action}
                    for e in self.error_handling
                ],
                "derived_from": {
                    "trace_ids": self.derived_from,
                    "extraction_method": self.extraction_method,
                },
            }
        }
        try:
            return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except ImportError:
            return json.dumps(data, ensure_ascii=False, indent=2)


# ── Trace 读取 ────────────────────────────────────────────────────────────

def read_trace_steps(
    db_path: str,
    trace_ids: list[str],
) -> dict[str, list[TraceStep]]:
    """从 intent_steps 表读取指定 trace 的步骤记录。

    Returns:
        dict: trace_id → [TraceStep, ...]，按 step_num 排序
    """
    if not trace_ids:
        return {}

    placeholders = ",".join("?" * len(trace_ids))
    query = f"""\
        SELECT trace_id, step_num, tool_name, desc, rationale,
               tool_args_summary, tool_result, tool_exit_ok,
               action_class, input_types, output_types
        FROM intent_steps
        WHERE trace_id IN ({placeholders})
        ORDER BY trace_id, step_num
    """

    result: dict[str, list[TraceStep]] = {tid: [] for tid in trace_ids}
    with open_db(db_path, readonly=True) as conn:
        rows = conn.execute(query, trace_ids).fetchall()

    for row in rows:
        tid = row["trace_id"]
        try:
            in_types = json.loads(row["input_types"]) if row["input_types"] else []
        except (json.JSONDecodeError, TypeError):
            in_types = []
        try:
            out_types = json.loads(row["output_types"]) if row["output_types"] else []
        except (json.JSONDecodeError, TypeError):
            out_types = []

        step = TraceStep(
            step_num=row["step_num"],
            tool_name=row["tool_name"],
            desc=row["desc"] or "",
            rationale=row["rationale"] or "",
            tool_args_summary=row["tool_args_summary"] or "",
            tool_result=row["tool_result"] or "",
            tool_exit_ok=row["tool_exit_ok"],
            action_class=row["action_class"] or "",
            input_types=in_types,
            output_types=out_types,
        )
        if tid in result:
            result[tid].append(step)

    return result


# ── LLM Prompts ───────────────────────────────────────────────────────────

NOISE_FILTER_PROMPT = """\
以下是 agent 执行"{purpose}"时的完整操作记录。
请标注每一步是：
- ESSENTIAL（核心步骤，必须保留）
- EXPLORATION（探索/尝试，可能有价值但非必要）
- MISTAKE（走弯路/错误尝试，应排除）
- RETRY（重试之前的失败，保留最终成功版本）

注意：
- 如果某一步失败（tool_exit_ok=0）但之后有成功的重试，标记失败步为 RETRY
- agent 的 think 步骤如果包含有价值的推理，标记为 ESSENTIAL
- grep/ls 等探索性操作，如果其结果被后续步骤使用，标记为 ESSENTIAL

操作记录：
{steps_text}

输出严格 JSON（不要 markdown 代码块）：
{{
  "annotations": [
    {{"step_num": 1, "label": "ESSENTIAL", "reason": "..."}},
    ...
  ]
}}
"""

MERGE_TRACES_PROMPT = """\
以下是 {n} 次执行同一类任务的核心步骤序列。
请提取它们的共同操作模式（SOP）：
- 找出每次都出现的步骤（核心流程）
- 找出有时出现有时不出现的步骤（条件分支）
- 找出步骤间的依赖关系（A 的输出是 B 的输入）
- 标注每步使用的工具和关键参数

{traces_text}

输出严格 JSON（不要 markdown 代码块）：
{{
  "purpose": "一句话描述",
  "preconditions": ["前置条件1", ...],
  "steps": [
    {{
      "id": 1,
      "action": "做什么",
      "tool": "工具名",
      "tool_args_pattern": "参数模板（如有）",
      "input": "输入描述",
      "output": "输出描述",
      "notes": "注意事项（如有）",
      "rules": ["业务规则（如有）"]
    }}
  ],
  "error_handling": [
    {{"condition": "错误场景", "action": "处理方式"}}
  ]
}}
"""

SINGLE_TRACE_SOP_PROMPT = """\
以下是 agent 执行一次任务的核心步骤（已过滤噪音）。
请将其转化为结构化 SOP。

任务目的：{purpose}

核心步骤：
{essential_steps_text}

输出严格 JSON（不要 markdown 代码块）：
{{
  "purpose": "一句话描述",
  "preconditions": ["前置条件1", ...],
  "steps": [
    {{
      "id": 1,
      "action": "做什么",
      "tool": "工具名",
      "tool_args_pattern": "参数模板（如有）",
      "input": "输入描述",
      "output": "输出描述",
      "notes": "注意事项（如有）",
      "rules": ["业务规则（如有）"]
    }}
  ],
  "error_handling": [
    {{"condition": "错误场景", "action": "处理方式"}}
  ]
}}
"""


# ── 辅助函数 ──────────────────────────────────────────────────────────────

def _format_steps_for_prompt(steps: list[TraceStep]) -> str:
    """将 trace 步骤格式化为可读文本。"""
    lines = []
    for s in steps:
        exit_str = {-1: "pending", 0: "FAIL", 1: "ok"}.get(s.tool_exit_ok, "?")
        lines.append(
            f"Step {s.step_num}: [{s.action_class}] {s.tool_name} — {s.desc}\n"
            f"  rationale: {s.rationale}\n"
            f"  args: {s.tool_args_summary[:200]}\n"
            f"  result: {s.tool_result[:200]}\n"
            f"  exit: {exit_str}"
        )
    return "\n\n".join(lines)


def _parse_json_response(raw: str) -> dict | None:
    """解析 LLM 返回的 JSON（处理 markdown 包裹）。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        logger.warning("[sop_extractor] JSON parse failed: %s", text[:200])
        return None


def _build_sop_from_json(data: dict, trace_ids: list[str], method: str) -> SOP:
    """从 LLM 返回的 JSON 构造 SOP 对象。"""
    steps = []
    for s in data.get("steps", []):
        steps.append(SOPStep(
            id=s.get("id", 0),
            action=s.get("action", ""),
            tool=s.get("tool", ""),
            tool_args_pattern=s.get("tool_args_pattern", ""),
            input=s.get("input", ""),
            output=s.get("output", ""),
            notes=s.get("notes", ""),
            rules=s.get("rules", []),
        ))

    error_handling = []
    for e in data.get("error_handling", []):
        error_handling.append(SOPErrorHandler(
            condition=e.get("condition", ""),
            action=e.get("action", ""),
        ))

    return SOP(
        purpose=data.get("purpose", ""),
        preconditions=data.get("preconditions", []),
        steps=steps,
        error_handling=error_handling,
        derived_from=trace_ids,
        extraction_method=method,
    )


# ── 主接口 ────────────────────────────────────────────────────────────────

async def extract_sop(
    trace_ids: list[str],
    purpose: str,
    *,
    db_path: str = "data/intent_traces.db",
    llm_call=None,
) -> SOP | None:
    """从历史 trace 中提取 SOP。

    Args:
        trace_ids: 一个或多个 trace ID
        purpose: 任务目的描述
        db_path: intent_traces 数据库路径
        llm_call: async callable(messages, system) -> str

    Returns:
        SOP 对象，或 None（提取失败时）
    """
    if llm_call is None:
        logger.error("[sop_extractor] llm_call is required")
        return None

    # Step 1: 读取 trace 数据
    traces = read_trace_steps(db_path, trace_ids)
    non_empty = {tid: steps for tid, steps in traces.items() if steps}

    if not non_empty:
        logger.warning("[sop_extractor] No trace data found for %s", trace_ids)
        return None

    if len(non_empty) == 1:
        return await _extract_single_trace_sop(
            list(non_empty.values())[0],
            purpose, list(non_empty.keys()), llm_call,
        )
    else:
        return await _extract_multi_trace_sop(
            non_empty, purpose, llm_call,
        )


async def _extract_single_trace_sop(
    steps: list[TraceStep],
    purpose: str,
    trace_ids: list[str],
    llm_call,
) -> SOP | None:
    """从单个 trace 提取 SOP。"""
    # Step 2: 过滤噪音
    steps_text = _format_steps_for_prompt(steps)
    prompt = NOISE_FILTER_PROMPT.format(purpose=purpose, steps_text=steps_text)

    try:
        raw = await llm_call(
            [{"role": "user", "content": prompt}],
            "你是一个操作流程分析专家。",
        )
    except Exception:
        logger.warning("[sop_extractor] LLM noise filter failed", exc_info=True)
        return None

    annotations = _parse_json_response(raw)
    if not annotations:
        return None

    # 筛选 ESSENTIAL 步骤
    essential_nums = set()
    for ann in annotations.get("annotations", []):
        if ann.get("label") == "ESSENTIAL":
            essential_nums.add(ann.get("step_num"))

    essential_steps = [s for s in steps if s.step_num in essential_nums]
    if not essential_steps:
        logger.warning("[sop_extractor] No essential steps found")
        return None

    # Step 3: 生成结构化 SOP
    essential_text = _format_steps_for_prompt(essential_steps)
    prompt2 = SINGLE_TRACE_SOP_PROMPT.format(
        purpose=purpose, essential_steps_text=essential_text,
    )

    try:
        raw2 = await llm_call(
            [{"role": "user", "content": prompt2}],
            "你是一位技术文档工程师。",
        )
    except Exception:
        logger.warning("[sop_extractor] LLM SOP generation failed", exc_info=True)
        return None

    sop_data = _parse_json_response(raw2)
    if not sop_data:
        return None

    return _build_sop_from_json(sop_data, trace_ids, "single-trace")


async def _extract_multi_trace_sop(
    traces: dict[str, list[TraceStep]],
    purpose: str,
    llm_call,
) -> SOP | None:
    """从多个 trace 合并提取 SOP。"""
    # 先对每个 trace 单独过滤噪音
    all_essential: dict[str, list[TraceStep]] = {}

    for tid, steps in traces.items():
        steps_text = _format_steps_for_prompt(steps)
        prompt = NOISE_FILTER_PROMPT.format(purpose=purpose, steps_text=steps_text)

        try:
            raw = await llm_call(
                [{"role": "user", "content": prompt}],
                "你是一个操作流程分析专家。",
            )
        except Exception:
            logger.warning("[sop_extractor] Noise filter failed for %s", tid)
            continue

        annotations = _parse_json_response(raw)
        if not annotations:
            continue

        essential_nums = set()
        for ann in annotations.get("annotations", []):
            if ann.get("label") == "ESSENTIAL":
                essential_nums.add(ann.get("step_num"))

        essential = [s for s in steps if s.step_num in essential_nums]
        if essential:
            all_essential[tid] = essential

    if not all_essential:
        logger.warning("[sop_extractor] No essential steps from any trace")
        return None

    # 合并多个 trace 的 essential 步骤
    traces_text_parts = []
    for i, (tid, steps) in enumerate(all_essential.items(), 1):
        traces_text_parts.append(
            f"=== Trace {i} ({tid}) ===\n{_format_steps_for_prompt(steps)}"
        )
    traces_text = "\n\n".join(traces_text_parts)

    prompt = MERGE_TRACES_PROMPT.format(
        n=len(all_essential), traces_text=traces_text,
    )

    try:
        raw = await llm_call(
            [{"role": "user", "content": prompt}],
            "你是一位技术文档工程师。",
        )
    except Exception:
        logger.warning("[sop_extractor] Multi-trace merge failed", exc_info=True)
        return None

    sop_data = _parse_json_response(raw)
    if not sop_data:
        return None

    return _build_sop_from_json(
        sop_data, list(traces.keys()), "multi-trace-merge",
    )
