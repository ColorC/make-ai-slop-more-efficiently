# [OMNI] origin=claude-code domain=trace_induction/routers.py ts=2026-04-08T03:23:37Z
# [OMNI] material_id="material:learning.trace_induction.deprecated_router_definitions.archive.py"
# OMNI-024 ALLOW: _archive/ 归档文件，不在标准位置属预期
# [OMNI] DEPRECATED 2026-04-22 — Stage 3 Clean Migration 完成, 业务代码已迁到 workers/*.py:
#   TraceReaderRouter    → workers/trace_reader.py    (TraceReaderWorker)
#   NoiseFilterRouter    → workers/noise_filter.py    (NoiseFilterWorker)
#   SOPGeneratorRouter   → workers/sop_generator.py   (SOPGeneratorWorker)
#   ReqWriterRouter      → workers/req_writer.py      (ReqWriterWorker)
#   WFCallerRouter       → workers/wf_caller.py       (WFCallerWorker)
#   RegistrarRouter      → workers/registrar.py       (RegistrarWorker)
# 本文件仅保留作为历史参考, 不再被 workers/__init__.py 继承。
"""trace_induction routers — 6 个节点的 Router 实现 (DEPRECATED, 见文件头)

trace_reader (HARD)      — 确定性 DB 读取
noise_filter (SOFT)      — LLM 标注噪音步骤
sop_generator (SOFT)     — LLM 生成结构化 SOP
req_writer (SOFT)        — LLM 生成需求文档
wf_caller (SubPipeline)  — 调用 workflow-factory
registrar (HARD)         — 确定性注册到 pipeline_index
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.runtime.routing.router import Router
from omnifactory.runtime.exec.sub_pipeline import SubPipelineRouter

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# [1] trace_reader — 确定性 DB 读取 (HARD)
# ═══════════════════════════════════════════════════════════

class TraceReaderRouter(Router):
    """从 intent_steps 表读取原始 trace 步骤数据。

    输入 ti.task: {purpose, trace_ids, db_path}
    输出 ti.trace-data: {traces, purpose, trace_count, domain, db_path}

    确定性操作：SQL 查询 + JSON 解析。
    """

    FORMAT_IN = "ti.task"
    FORMAT_OUT = "ti.trace-data"
    DESCRIPTION = (
        "从 intent_steps 表确定性读取指定 trace_ids 的完整操作记录。"
        "每条记录包含 tool_name、desc、rationale、tool_args_summary、"
        "tool_result、tool_exit_ok、action_class 等字段。"
        "按 trace_id 分组、step_num 排序输出。"
    )

    def run(self, input_data: Any) -> Verdict:
        purpose = input_data.get("purpose", "")
        trace_ids = input_data.get("trace_ids", [])
        # CLI 传入逗号分隔字符串时自动拆分
        if isinstance(trace_ids, str):
            trace_ids = [t.strip() for t in trace_ids.split(",") if t.strip()]
        db_path = input_data.get("db_path", "data/intent_traces.db")
        domain = input_data.get("domain", "")

        if not purpose or not trace_ids:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis="purpose 和 trace_ids 不能为空",
            )

        from omnifactory.packages.services._learning.trace_induction.sop_extractor import read_trace_steps
        traces_raw = read_trace_steps(db_path, trace_ids)

        # 转为 plain dict（TraceStep dataclass → dict）
        traces = {}
        for tid, steps in traces_raw.items():
            if steps:
                traces[tid] = [asdict(s) for s in steps]

        if not traces:
            return Verdict(
                kind=VerdictKind.FAIL,
                output=input_data,
                diagnosis=f"未找到 trace 数据：{trace_ids}",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "traces": traces,
                "purpose": purpose,
                "trace_count": len(traces),
                "domain": domain,
                "db_path": db_path,
            },
            diagnosis=f"读取到 {len(traces)} 个 trace，共 {sum(len(v) for v in traces.values())} 步",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════
# [2] noise_filter — LLM 标注噪音 (SOFT)
# ═══════════════════════════════════════════════════════════

_NOISE_FILTER_PROMPT = """\
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
{{"annotations": [{{"step_num": 1, "label": "ESSENTIAL", "reason": "..."}}]}}
"""


class NoiseFilterRouter(Router):
    """LLM 标注每步为 ESSENTIAL/EXPLORATION/MISTAKE/RETRY，过滤保留核心步骤。

    输入 ti.trace-data: {traces, purpose}
    输出 ti.essential: {essential_steps, purpose, trace_count}

    对每个 trace 单独做噪音过滤（可能多次 LLM 调用），
    然后合并所有 ESSENTIAL 步骤。
    """

    FORMAT_IN = "ti.trace-data"
    FORMAT_OUT = "ti.essential"
    DESCRIPTION = (
        "对每个 trace 的步骤调用 LLM 标注重要性等级。"
        "保留 ESSENTIAL 步骤，过滤 EXPLORATION/MISTAKE/RETRY。"
        "多 trace 时对每个 trace 单独过滤后合并。"
    )

    def __init__(self, *, client=None):
        self._client = client

    async def run(self, input_data: Any) -> Verdict:
        traces = input_data.get("traces", {})
        purpose = input_data.get("purpose", "")

        if not traces:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="无 trace 数据可过滤",
            )

        all_essential = []
        for tid, steps in traces.items():
            steps_text = _format_steps(steps)
            prompt = _NOISE_FILTER_PROMPT.format(purpose=purpose, steps_text=steps_text)

            try:
                resp = self._client.call(
                    messages=[{"role": "user", "content": prompt}],
                    system="你是一个操作流程分析专家。",
                )
                raw = resp.content[0].text
                data = _parse_json(raw)
                if not data:
                    continue

                essential_nums = {
                    a["step_num"] for a in data.get("annotations", [])
                    if a.get("label") == "ESSENTIAL"
                }
                for s in steps:
                    if s["step_num"] in essential_nums:
                        all_essential.append({**s, "_trace_id": tid})
            except Exception as e:
                logger.warning("[noise_filter] trace %s failed: %s", tid, e)
                continue

        if not all_essential:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="过滤后无 ESSENTIAL 步骤",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "essential_steps": all_essential,
                "purpose": purpose,
                "trace_count": input_data.get("trace_count", 1),
                "domain": input_data.get("domain", ""),
                "db_path": input_data.get("db_path", ""),
            },
            diagnosis=f"保留 {len(all_essential)} 个 ESSENTIAL 步骤",
        )


# ═══════════════════════════════════════════════════════════
# [3] sop_generator — LLM 生成结构化 SOP (SOFT)
# ═══════════════════════════════════════════════════════════

_SOP_GEN_PROMPT = """\
以下是执行"{purpose}"时的核心操作步骤（已过滤噪音）。
请将其转化为结构化 SOP。

核心步骤：
{steps_text}

输出严格 JSON（不要 markdown 代码块）：
{{
  "purpose": "一句话描述",
  "preconditions": ["前置条件1"],
  "steps": [
    {{"id": 1, "action": "做什么", "tool": "工具名",
      "tool_args_pattern": "参数模板", "input": "输入",
      "output": "输出", "notes": "注意事项", "rules": ["规则"]}}
  ],
  "error_handling": [
    {{"condition": "错误场景", "action": "处理方式"}}
  ]
}}
"""


class SOPGeneratorRouter(Router):
    """LLM 将核心步骤合并为结构化 SOP。

    输入 ti.essential: {essential_steps, purpose}
    输出 ti.sop: {sop, derived_from, extraction_method}
    """

    FORMAT_IN = "ti.essential"
    FORMAT_OUT = "ti.sop"
    DESCRIPTION = (
        "将过滤后的核心步骤合并为结构化 SOP。单 trace 直接转换，"
        "多 trace 提取共同模式。输出完整的 SOP dict 包含 "
        "purpose/preconditions/steps/error_handling。"
    )

    def __init__(self, *, client=None):
        self._client = client

    async def run(self, input_data: Any) -> Verdict:
        essential_steps = input_data.get("essential_steps", [])
        purpose = input_data.get("purpose", "")

        if not essential_steps:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="无核心步骤可转化为 SOP",
            )

        steps_text = _format_steps(essential_steps)
        prompt = _SOP_GEN_PROMPT.format(purpose=purpose, steps_text=steps_text)

        try:
            resp = self._client.call(
                messages=[{"role": "user", "content": prompt}],
                system="你是一位技术文档工程师。",
            )
            sop_data = _parse_json(resp.content[0].text)
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"SOP 生成 LLM 调用失败: {e}",
            )

        if not sop_data or not sop_data.get("steps"):
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="SOP 生成结果为空或缺少 steps",
            )

        # 提取来源 trace_ids
        trace_ids = list({s.get("_trace_id", "") for s in essential_steps if s.get("_trace_id")})

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "sop": sop_data,
                "derived_from": trace_ids,
                "extraction_method": "single-trace" if len(trace_ids) <= 1 else "multi-trace-merge",
                "domain": input_data.get("domain", ""),
                "db_path": input_data.get("db_path", ""),
            },
            diagnosis=f"生成 SOP: {len(sop_data.get('steps', []))} 步",
        )


# ═══════════════════════════════════════════════════════════
# [4] req_writer — LLM 生成需求文档 (SOFT)
# ═══════════════════════════════════════════════════════════

class ReqWriterRouter(Router):
    """LLM 将 SOP 转化为 Workflow Factory 可消费的需求文档。

    输入 ti.sop: {sop, derived_from}
    输出 ti.requirement: {requirement_doc, purpose, domain}
    """

    FORMAT_IN = "ti.sop"
    FORMAT_OUT = "ti.requirement"
    DESCRIPTION = (
        "将结构化 SOP 转化为 Workflow Factory 可消费的 Markdown 需求文档。"
        "包含目标、触发场景、操作流程、数据流、错误处理、验证标准、约束。"
        "如果 LLM 不可用则降级为确定性模板填充。"
    )

    def __init__(self, *, client=None):
        self._client = client

    async def run(self, input_data: Any) -> Verdict:
        sop_dict = input_data.get("sop", {})
        derived_from = input_data.get("derived_from", [])
        domain = input_data.get("domain", "")

        if not sop_dict or not sop_dict.get("steps"):
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="SOP 数据为空，无法生成需求文档",
            )

        # 从 sop dict 重建 SOP 对象
        from omnifactory.packages.services._learning.trace_induction.sop_extractor import _build_sop_from_json
        sop_obj = _build_sop_from_json(sop_dict, derived_from, sop_dict.get("extraction_method", ""))

        # 生成需求文档
        llm_call = None
        if self._client:
            async def llm_call(messages, system):
                resp = self._client.call(messages=messages, system=system)
                return resp.content[0].text

        from omnifactory.packages.services._learning.trace_induction.requirement_writer import generate_requirement_with_llm
        req_doc = await generate_requirement_with_llm(
            sop_obj, llm_call=llm_call, domain_knowledge=domain,
        )

        if not req_doc or len(req_doc) < 50:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="需求文档生成失败或内容过短",
            )

        purpose = sop_dict.get("purpose", "")

        # 生成 ASCII pipeline name（WF 需要英文名，否则 sanitize 后变 "generated"）
        pipeline_name = _derive_pipeline_name(purpose, domain)

        # 在需求文档开头注入 pipeline 元信息（WF req_analyzer 可解析）
        meta_header = (
            f"**Pipeline Name**: {pipeline_name}\n"
            f"**Domain**: {domain or 'custom'}\n\n"
        )
        req_doc = meta_header + req_doc

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "requirement_doc": req_doc,
                "purpose": purpose,
                "domain": domain,
                "trace_ids": derived_from,
                "db_path": input_data.get("db_path", ""),
            },
            diagnosis=f"需求文档 {len(req_doc)} 字符, pipeline_name={pipeline_name}",
        )


# ═══════════════════════════════════════════════════════════
# [5] wf_caller — SubPipelineRouter 调用 workflow-factory (SOFT)
# ═══════════════════════════════════════════════════════════

class WFCallerRouter(SubPipelineRouter):
    """通过 SubPipelineRouter 标准接口调用 workflow-factory。

    输入 ti.requirement: {requirement_doc, purpose, domain}
    输出 ti.wf-result: {pipeline_name, package_path, files, purpose, domain}
    """

    TARGET_PIPELINE = "workflow-factory"
    TARGET_MAX_STEPS = 30

    FORMAT_IN = "ti.requirement"
    FORMAT_OUT = "ti.wf-result"
    DESCRIPTION = (
        "通过 SubPipelineRouter 标准接口调用 workflow-factory 元管线，"
        "共享父管线的 EventBus 保持事件可观测性。WF 内部执行需求分析 → "
        "Format 设计 → 节点规划 → 代码生成 → 编译/LAP/路由审计 → 产出代码包。"
    )

    def prepare_input(self, input_data: dict) -> dict:
        return {"text": input_data.get("requirement_doc", "")}

    def extract_output(self, sub_result: Any, input_data: dict) -> dict:
        if not isinstance(sub_result, dict):
            return input_data

        files = sub_result.get("files", {})
        required = {"formats.py", "routers.py", "pipeline.py", "run.py"}
        if not required.issubset(set(files.keys())):
            # 不够——让 run() 返回的 Verdict 处理
            return sub_result

        return {
            "pipeline_name": sub_result.get("pipeline_name", ""),
            "package_path": sub_result.get("package_path", ""),
            "files": files,
            "purpose": input_data.get("purpose", ""),
            "domain": input_data.get("domain", ""),
            "db_path": input_data.get("db_path", ""),
        }


# ═══════════════════════════════════════════════════════════
# [6] registrar — 确定性注册 (HARD)
# ═══════════════════════════════════════════════════════════

class RegistrarRouter(Router):
    """将 WF 产出注册到 pipeline_index 语义索引。

    输入 ti.wf-result: {pipeline_name, package_path, files, purpose, domain}
    输出 ti.done: {pipeline_name, package_path, registered, status, summary}
    """

    FORMAT_IN = "ti.wf-result"
    FORMAT_OUT = "ti.done"
    DESCRIPTION = (
        "将 Workflow Factory 产出的 pipeline 元信息注册到 pipeline_index 表。"
        "注册后可被 Pre-execution Search 检索，供后续任务复用。确定性操作。"
    )

    def run(self, input_data: Any) -> Verdict:
        pipeline_name = input_data.get("pipeline_name", "")
        package_path = input_data.get("package_path", "")
        purpose = input_data.get("purpose", "")
        domain = input_data.get("domain", "")
        db_path = input_data.get("db_path", "data/intent_traces.db")

        if not pipeline_name:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis="pipeline_name 为空，无法注册",
            )

        from omnifactory.runtime.storage.experience_search import register_pipeline_to_index
        try:
            register_pipeline_to_index(
                db_path,
                pipeline_name=pipeline_name,
                purpose=purpose,
                domain=domain or None,
                tags=_extract_tags(purpose, domain),
                source="trace_induction",
                test_status="untested",
            )
        except Exception as e:
            return Verdict(
                kind=VerdictKind.FAIL, output=input_data,
                diagnosis=f"注册失败: {e}",
            )

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "pipeline_name": pipeline_name,
                "package_path": package_path,
                "registered": True,
                "status": "registered",
                "summary": f"Pipeline '{pipeline_name}' 已注册 (domain={domain})",
            },
            diagnosis=f"已注册: {pipeline_name}",
            confidence=1.0,
        )


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _format_steps(steps: list[dict]) -> str:
    """将步骤列表格式化为可读文本。"""
    lines = []
    for s in steps:
        exit_str = {-1: "pending", 0: "FAIL", 1: "ok"}.get(s.get("tool_exit_ok", -1), "?")
        lines.append(
            f"Step {s.get('step_num', '?')}: [{s.get('action_class', '')}] "
            f"{s.get('tool_name', '?')} — {s.get('desc', '')}\n"
            f"  rationale: {s.get('rationale', '')}\n"
            f"  args: {str(s.get('tool_args_summary', ''))[:200]}\n"
            f"  result: {str(s.get('tool_result', ''))[:200]}\n"
            f"  exit: {exit_str}"
        )
    return "\n\n".join(lines)


def _parse_json(raw: str) -> dict | None:
    """解析 LLM 返回的 JSON（处理 markdown 包裹）。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _derive_pipeline_name(purpose: str, domain: str) -> str:
    """从 purpose 推导英文 pipeline name（kebab-case ASCII）。"""
    # 提取英文单词
    words = re.findall(r'[A-Za-z][a-z]*[A-Za-z]*', purpose)
    if not words:
        # 全中文 purpose：用 domain + 哈希
        import hashlib
        h = hashlib.md5(purpose.encode()).hexdigest()[:6]
        return f"{domain or 'auto'}-{h}"
    # 取前 4 个英文词做 kebab-case
    name = "-".join(w.lower() for w in words[:4])
    if domain:
        name = f"{domain}-{name}"
    return name


def _extract_tags(purpose: str, domain: str) -> list[str]:
    """从 purpose 和 domain 提取简单 tags。"""
    tags = []
    if domain:
        tags.append(domain)
    words = re.split(r'[\s,;，；。\-_/]+', purpose)
    for w in words:
        if len(w) >= 2 and w.isascii():
            tags.append(w.lower())
    return tags[:10]
