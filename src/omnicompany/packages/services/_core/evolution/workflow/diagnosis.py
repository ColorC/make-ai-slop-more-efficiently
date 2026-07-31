# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.deep_diagnosis_agent.py"
"""B.2 深度诊断 Agent

接收 HypothesisBoard 中置信度最高的 focus 假设，
加载该假设局部范围的 context（按预算），
调用 LLM 输出结构化 DiagnosisReport。

DiagnosisReport 必须填写所有字段，不允许空泛描述。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.bus.sqlite import SQLiteBus
from omnicompany.packages.services._core.evolution.workflow.hypothesis import Hypothesis, HypothesisBoard
from omnicompany.packages.services._core.evolution.workflow.hypothesis_store import HypothesisBoardStore
from omnicompany.protocol.anchor import ValidatorKind
from omnicompany.protocol.events import FactoryEvent
from omnicompany.protocol.team import NodeKind, TeamSpec
from omnicompany.runtime.llm.llm import LLMClient

logger = logging.getLogger(__name__)

# ── 诊断报告数据结构 ──


@dataclass
class ProposedChange:
    """单条修改建议"""

    target_node: str
    change_type: str
    """prompt | logic | format | insert_node | split_node"""

    change_description: str
    """具体改什么（不是抽象策略）"""

    expected_effect: str
    """预期效果（可量化/可验证）"""

    risk_level: str = "low"
    """low | medium | high"""

    blast_radius: str = ""
    """可能影响的其他节点/管线"""

    target_method: str = ""
    """logic 类变更时：具体要修改的方法名（如 'run' / '_translate_interfaces'）"""

    error_category: str = "llm_processing"
    """错误分类:
    - llm_processing: LLM 节点的 prompt/处理方式/分段策略有问题，本工作流可自动修复
    - tool_programming: 确定性工具节点有代码 bug，应转发给代码修复工作流
    - format_definition: Format 定义本身不能准确表达任务需求，需修改 Format 定义
    - needs_user_clarification: 无法确定根因/节点意图不清楚，需要用户澄清后再继续
    """


@dataclass
class DiagnosisReport:
    """深度诊断报告

    由诊断 Agent 产出，作为受控实验的输入。
    """

    # ── 核心诊断（必填，无默认值）──
    root_cause_node: str
    """根因节点 ID"""

    root_cause_explanation: str
    """具体原因（不允许'LLM表现不佳'这类空话）"""

    comparison_with_success: str
    """和成功 case 的具体对比（为空表示无可对比数据）"""

    confidence: float
    """诊断整体置信度 0.0~1.0"""

    uncertainty: str
    """不确定的地方"""

    # ── 列表字段（有 field(default_factory)）──
    evidence_from_traces: list[str] = field(default_factory=list)
    """引用具体 trace 事件中的内容作为证据"""

    refined_hypotheses: list[dict] = field(default_factory=list)
    """对原假设的精化，每条包含 statement + confidence + falsification_test"""

    what_not_to_change: list[str] = field(default_factory=list)
    """改这些地方可能影响其他 case，本次不碰"""

    proposed_changes: list[ProposedChange] = field(default_factory=list)
    """按优先级排序，第一条将作为受控实验的对象"""

    format_adequacy_check: list[dict] = field(default_factory=list)
    """各节点 Format_out 充分性检查，每条含 node/format_out/is_adequate/missing_constraints"""

    # ── 分类与元信息（有默认值）──
    error_category: str = "llm_processing"
    """错误根本分类: llm_processing | tool_programming | format_definition | needs_user_clarification"""

    user_inquiry: str = ""
    """当 error_category=needs_user_clarification 时，具体需要向用户问什么"""

    focus_hypothesis_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Context 加载 ──

_CONTEXT_BUDGET = 60_000  # tokens，约 context 窗口的 60%


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（1 token ≈ 4 字符）"""
    return len(text) // 4


def _extract_class_source(content: str, class_name: str) -> str:
    """从源文件中提取指定 class 的完整代码（使用 AST end_lineno）。"""
    import ast as _ast
    try:
        tree = _ast.parse(content)
        lines = content.splitlines()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.ClassDef) and node.name == class_name:
                start = node.lineno - 1
                end = getattr(node, "end_lineno", node.lineno + 200)
                return "\n".join(lines[start:end])
    except Exception:
        pass
    # fallback: 找到 class 定义行，取后 200 行
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if f"class {class_name}" in line:
            return "\n".join(lines[i: i + 200])
    return ""


def _load_node_source(node_id: str, pipeline_id: str) -> str:
    """尝试加载节点对应的 Router 类源代码（只取目标类，不返回整个文件）

    Post-2026-04-07 migration: Router implementations live under
    src/omnicompany/packages/<domain>/routers/ or runtime/nodes/, not
    under primitives_impl/ (retired) or runtime/evolution/ (retired).
    """
    # parents: [0]=workflow [1]=evolution [2]=packages [3]=omnicompany
    _omnicompany = Path(__file__).resolve().parents[4]
    search_roots = [
        _omnicompany / "packages",
        _omnicompany / "runtime" / "nodes",
    ]
    class_name = "".join(w.capitalize() for w in node_id.split("_")) + "Router"
    for root in search_roots:
        if not root.exists():
            continue
        for py_file in root.rglob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8")
                if class_name not in content:
                    continue
                cls_src = _extract_class_source(content, class_name)
                if cls_src:
                    return f"# 来源: {py_file} ({class_name})\n{cls_src}"
            except Exception:
                continue
    return f"# 未找到 {node_id} 对应的 Router 源码（{class_name}）"


def _load_trace_context(
    events: list[FactoryEvent],
    focus_node_id: str,
    budget: int,
) -> str:
    """从 trace 事件中提取与 focus 节点相关的上下文"""
    EXIT_TYPES = {"agent.tool.result", "agent.llm.response", "agent.state.change"}

    sections: list[str] = []
    remaining = budget

    for ev in events:
        if ev.event_type not in EXIT_TYPES:
            continue
        payload = ev.payload
        node_id = payload.get("node")
        if not node_id:
            continue
        if payload.get("verdict") is None:
            continue  # enter event

        verdict = payload.get("verdict", "")
        output_summary = payload.get("output_summary") or payload.get("diagnosis") or ""
        format_out = payload.get("format_out") or ""
        desc = payload.get("description") or node_id

        # focus 节点：详细展示
        if node_id == focus_node_id:
            detail = (
                f"[节点 {node_id}] verdict={verdict} format_out={format_out}\n"
                f"描述: {desc}\n"
                f"输出: {output_summary[:1000]}\n"
            )
            diag = payload.get("diagnosis")
            if diag:
                detail += f"诊断: {diag}\n"
        else:
            # 其他节点：简要展示
            detail = f"[节点 {node_id}] verdict={verdict} {output_summary[:200]}\n"

        tokens = _estimate_tokens(detail)
        if tokens > remaining:
            break
        sections.append(detail)
        remaining -= tokens

    return "\n".join(sections)


def _load_pipeline_spec(pipeline_id: str) -> TeamSpec | None:
    """动态加载 pipeline spec（用于分析上游依赖）"""
    import importlib
    # pipeline_input 可能含 pipeline_module 字段；否则从 pipeline_id 推导
    domain = pipeline_id.replace("-pipeline", "").replace("-", "_")
    # 尝试常见模块路径
    # Post-2026-04-07: domain impls live at packages/<domain>/
    candidates = [
        f"omnicompany.packages.{domain}.pipeline",
    ]
    for mod_path in candidates:
        try:
            mod = importlib.import_module(mod_path)
            return mod.build_pipeline()
        except (ImportError, AttributeError):
            continue
    return None


def _is_hard_validator(node_id: str, spec: TeamSpec) -> bool:
    """判断节点是否为确定性 HARD 校验器（没有诊断价值，需要看上游）"""
    for node in spec.nodes:
        if node.id != node_id:
            continue
        if node.kind == NodeKind.TRANSFORMER:
            return True  # 确定性 Transformer 没有 prompt
        if node.kind == NodeKind.ANCHOR and node.anchor:
            validator = node.anchor.validator
            if validator and validator.kind == ValidatorKind.HARD:
                return True
    return False


def _find_upstream_llm_nodes(node_id: str, spec: TeamSpec, max_depth: int = 6) -> list[str]:
    """从给定节点往上游走，找最近的 LLM（ANCHOR SOFT）节点 ID 列表"""
    # 构建逆向邻接表
    rev: dict[str, list[str]] = {}
    for edge in spec.edges:
        rev.setdefault(edge.target, []).append(edge.source)

    llm_nodes: list[str] = []
    visited: set[str] = set()
    queue = [(node_id, 0)]

    while queue:
        cur, depth = queue.pop(0)
        if cur in visited or depth > max_depth:
            continue
        visited.add(cur)

        if cur != node_id:  # 不检查起点自身
            for node in spec.nodes:
                if node.id != cur:
                    continue
                if node.kind == NodeKind.ANCHOR and node.anchor:
                    validator = node.anchor.validator
                    if validator and validator.kind == ValidatorKind.SOFT:
                        llm_nodes.append(cur)
                        continue  # 找到 LLM 节点，不再往上
                break

        # 继续往上游走
        for upstream in rev.get(cur, []):
            queue.append((upstream, depth + 1))

    return llm_nodes


_BLOB_FIELDS = frozenset({
    "source_code", "generated_code", "supply_context", "demand_section",
    "_feedback", "compile_errors", "style_errors", "style_warnings",
})
_BLOB_PREVIEW_LEN = 120


def _summarize_data(data: Any, max_chars: int = 3000) -> str:
    """序列化 dict 时将大字符串字段截短，保留结构化字段完整可见。

    目的：避免 source_code 等大字段把 public_interfaces 之类的关键元数据截掉。
    """
    if not isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False)[:max_chars]

    compact: dict = {}
    for k, v in data.items():
        if k in _BLOB_FIELDS and isinstance(v, str) and len(v) > _BLOB_PREVIEW_LEN:
            compact[k] = f"<{len(v)} chars: {v[:_BLOB_PREVIEW_LEN]}...>"
        else:
            compact[k] = v
    return json.dumps(compact, ensure_ascii=False)[:max_chars]


def _load_io_context(
    events: list[FactoryEvent],
    node_ids: list[str],
    budget: int,
) -> str:
    """从 record_io=True 产生的事件中提取指定节点的全量输入输出。

    只有启用了 record_io 的 trace 才会有 input_data / output_data 字段。
    未找到时返回空字符串（不影响诊断，退化为只有 output_summary）。
    大字段（source_code 等）会被截短，确保结构化字段（如 public_interfaces）可见。
    """
    node_set = set(node_ids)
    sections: list[str] = []
    remaining = budget

    for ev in events:
        payload = ev.payload
        node_id = payload.get("node")
        if not node_id or node_id not in node_set:
            continue

        has_input = "input_data" in payload
        has_output = "output_data" in payload
        if not has_input and not has_output:
            continue

        lines: list[str] = [f"=== 节点 {node_id} ==="]
        if has_input:
            lines.append(f"[输入]\n{_summarize_data(payload['input_data'], max_chars=3000)}")
        if has_output:
            lines.append(f"[输出]\n{_summarize_data(payload['output_data'], max_chars=3000)}")

        section = "\n".join(lines)
        tokens = _estimate_tokens(section)
        if tokens > remaining:
            break
        sections.append(section)
        remaining -= tokens

    return "\n\n".join(sections)


async def _load_events(bus_path: str, trace_id: str) -> list[FactoryEvent]:
    bus = SQLiteBus(bus_path)
    await bus.connect()
    try:
        return await bus.read_trace(trace_id)
    finally:
        await bus.close()


# ── LLM 诊断 prompt ──

_SYSTEM_DIAGNOSIS = """\
你是一个 AI 管线诊断专家。你的任务是对一个失败的管线运行进行深度根因分析。

## 诊断流程（必须按顺序执行）

### Step 1：节点分类
根据提示中的"节点分类表"，区分 SOFT（LLM 节点，有 prompt，可修改）和 HARD（确定性工具节点，无 LLM）。

**关键规则：HARD 节点永远不是根因的最终归属，只是症状的指示器。**
HARD 节点只有在以下情况才可以被标记为 tool_programming 错误：
- 你读取了该节点的实际源码
- 你对比了其设计意图（docstring / class 名 / format 定义）和实际实现逻辑
- 确认实现与意图明显不符（不是输入数据导致的正确行为）
- 并且你对此非常确信（confidence > 0.85）

如果不确定，不要轻易标记 tool_programming——宁可标记 needs_user_clarification。

### Step 2：三步先验归因（对任何可疑节点，必须按此顺序排除）

在归因任何节点自身的处理问题之前，必须按顺序检查以下三项：

**2-A. 输入是否有问题？**（最高优先级）
- 检查该节点的 input_data 中每个关键字段的值是否合理、自洽
- 例：若 public_interfaces 列出了类方法与类名并列，说明上游提取逻辑有误，而不是当前节点的问题
- 若输入有问题 → 根因在产出该字段的上游节点，切换焦点，不要改当前节点
- 若输入正确 → 继续 2-B

**2-B. 信息是否严重不完整？**（仅当信息真的不够用时考虑）
- 区分"信息不够丰富"（不算缺失）和"关键字段缺失/为空"（才算缺失）
- 若确实有关键信息缺失 → format_definition 或 insert_node（补充信息来源）
- 若信息足够 → 继续 2-C

**2-C. 输出 format/意图与下游需要是否匹配？**
- 该节点的 Format_out 是否准确表达了下游真正需要的约束？
- 节点输出了"符合 Format 的内容"，但 Format 本身约束不足以保证任务正确？
- 若不匹配 → format_definition 问题
- 若匹配 → 节点自身处理有问题（继续 Step 3）

**只有三项都排除后，才归因节点自身**：prompt 偏差 / 处理过载 / 需要切割

### Step 3：错误分类
根据前两步的分析，判断根本错误类型：
- **llm_processing**：SOFT 节点输入正确、信息完整、Format 匹配，但 prompt 不完整、指令不清晰、遗漏某类情况。本工作流可以自动修复。
- **tool_programming**：HARD 节点的代码逻辑确认有 bug（Step 2-A 排除输入问题后，读了源码仍确认实现与意图不符）。需转发给代码修复工作流。
- **format_definition**：Format 定义本身约束不足（2-C 判断）。需修改 Format 定义。
- **needs_user_clarification**：无法确定根因 / 节点意图不清楚 / 对 tool_programming 没有把握。在 user_inquiry 写具体问题。

### Step 4：Format 充分性检查
对每个相关节点，检查其 Format_out 是否完整描述了任务真正的输出约束。

### Step 5：提出修改建议
只对确定有问题的节点提建议：
- llm_processing → prompt 或 logic 类变更，target_node 必须是 SOFT 节点
- tool_programming → 标记但不提 prompt 修改，交给代码修复流程
- format_definition → change_type="format"
- 每次只提最高置信度的一条建议（防止过度修改）
- 永远不要建议修改 HARD 节点的 prompt（它们没有 prompt）

---

## 请求查看源码（read_source 动作）[READ_SOURCE_SECTION]

如果在 Step 2 中，你怀疑某个上游节点的代码实现有问题（输入数据表明上游提取/处理有误），
但当前上下文中没有该节点的源码，你可以**先输出以下 JSON 请求查看源码，而不是给出最终诊断**：

```json
{"action": "read_source", "node": "节点ID", "reason": "为什么需要看这个节点的源码"}
```

系统会自动加载该节点源码并提供给你，然后你再给出完整诊断。
每次只能请求一个节点的源码。最多可以请求 2 次。

[/READ_SOURCE_SECTION]

---

最终诊断输出必须是严格的 JSON（不含其他文字）：
{
    "root_cause_node": "节点ID（必须存在的 SOFT 节点，或 needs_user_clarification 时填怀疑的 SOFT 节点）",
    "root_cause_explanation": "具体原因，必须引用节点的实际输入/输出内容，不允许空泛描述",
    "error_category": "llm_processing | tool_programming | format_definition | needs_user_clarification",
    "user_inquiry": "error_category=needs_user_clarification 时：向用户提出的具体问题，其他时候留空字符串",
    "input_completeness_check": "简述对根因节点输入完整性的检查结论（输入是否完整、是否有上游问题）",
    "format_adequacy_check": [
        {
            "node": "节点ID",
            "format_out": "Format名称",
            "is_adequate": true或false,
            "missing_constraints": "缺少哪些约束（adequate时留空）"
        }
    ],
    "evidence_from_traces": ["引用具体trace内容的证据，每条必须包含节点名和具体数值/内容"],
    "comparison_with_success": "和成功case的对比（没有数据则留空字符串）",
    "refined_hypotheses": [
        {
            "statement": "精化后的假设陈述",
            "confidence": 0.0到1.0,
            "falsification_test": "什么实验会推翻这个假设"
        }
    ],
    "what_not_to_change": ["不能碰的节点/原因——尤其是正确运作的工具节点"],
    "proposed_changes": [
        {
            "target_node": "节点ID（必须是 SOFT/LLM 节点，除非 tool_programming）",
            "change_type": "prompt | logic | format | insert_node | split_node",
            "target_method": "logic类型时填方法名，其他留空",
            "change_description": "具体改什么（可操作的具体指令，不是策略描述）",
            "expected_effect": "可验证的预期效果",
            "risk_level": "low | medium | high",
            "blast_radius": "可能影响哪些其他节点/case",
            "error_category": "llm_processing | tool_programming | format_definition"
        }
    ],
    "confidence": 0.0到1.0,
    "uncertainty": "还不确定的地方，以及如果确定了会怎么影响判断"
}
"""


def _build_node_classification_table(
    events: list[FactoryEvent],
    spec: "TeamSpec | None",
) -> str:
    """生成节点 SOFT/HARD 分类表，供诊断 LLM 参考"""
    executed_nodes: list[str] = []
    seen: set[str] = set()
    for ev in events:
        node = ev.payload.get("node")
        if node and node not in seen:
            seen.add(node)
            executed_nodes.append(node)

    if not spec:
        return "\n".join(f"- {n}: 未知类型（无 spec）" for n in executed_nodes)

    lines: list[str] = []
    for node_id in executed_nodes:
        node_spec = next((n for n in spec.nodes if n.id == node_id), None)
        if not node_spec:
            lines.append(f"- {node_id}: 未知")
            continue
        if node_spec.kind == NodeKind.TRANSFORMER:
            lines.append(f"- {node_id}: HARD（确定性 Transformer，无 LLM）")
        elif node_spec.kind == NodeKind.ANCHOR and node_spec.anchor:
            v = node_spec.anchor.validator
            if v and v.kind == ValidatorKind.HARD:
                lines.append(f"- {node_id}: HARD（确定性 ANCHOR HARD，无 LLM）")
            elif v and v.kind == ValidatorKind.SOFT:
                lines.append(f"- {node_id}: SOFT（LLM 节点，可修改 prompt/logic）")
            else:
                lines.append(f"- {node_id}: SOFT（LLM 节点）")
        else:
            lines.append(f"- {node_id}: 未知")
    return "\n".join(lines)


def _build_experiment_history(board: "HypothesisBoard") -> str:
    """从黑板构建实验历史摘要，供 B.2 诊断参考"""
    if not board.experiment_log:
        return ""

    lines = ["本次进化会话的实验历史（请勿重复这些方向）："]
    for rec in board.experiment_log:
        outcome_label = {
            "improved": "✓ 有效",
            "regression": "✗ 引起回归",
            "unchanged": "- 无效",
            "requires_human": "? 无法自动执行",
            "failed_to_apply": "! 应用失败",
        }.get(rec.outcome or "", f"? {rec.outcome}")

        lines.append(
            f"  [{outcome_label}] 节点={rec.locked_node} 类型={rec.change_type}"
            f"\n    改了什么: {rec.change_description[:100]}"
        )
        if rec.causal_explanation:
            lines.append(f"    原因分析: {rec.causal_explanation[:100]}")
        if rec.anti_pattern:
            lines.append(f"    反模式: {rec.anti_pattern[:100]}")

    # 已排除的假设
    eliminated = [
        h for h in board.hypotheses
        if h.status.value == "eliminated"
    ]
    if eliminated:
        lines.append("\n已被实验证伪的假设方向（不要再往这些方向找）：")
        for h in eliminated:
            lines.append(f"  - [{h.suspect_node}] {h.statement[:100]}")
            if h.anti_pattern:
                lines.append(f"    反模式: {h.anti_pattern[:100]}")

    return "\n".join(lines)


def _build_diagnosis_prompt(
    hypothesis: Hypothesis,
    board: HypothesisBoard,
    trace_context: str,
    node_source: str,
    io_context: str = "",
    node_classification: str = "",
    user_answer: str | None = None,
    force_diagnosis: bool = False,
) -> str:
    io_section = f"\n### 节点全量输入/输出（record_io 数据）\n{io_context}\n" if io_context else ""
    classification_section = (
        f"\n### 节点分类表（SOFT=LLM节点可修改，HARD=确定性工具不可通过prompt修改）\n{node_classification}\n"
        if node_classification else ""
    )
    experiment_history = _build_experiment_history(board)
    history_section = (
        f"\n### 实验历史（已尝试过的方向，禁止重复）\n{experiment_history}\n"
        if experiment_history else ""
    )
    user_answer_section = (
        f"\n### 用户对上一轮诊断问题的回答（重要：基于此继续诊断）\n{user_answer}\n"
        if user_answer else ""
    )
    force_section = (
        "\n⚠️ **强制最终诊断**：source_read 次数已用尽。"
        "不得再输出 read_source 动作。必须基于目前已有信息直接输出最终诊断 JSON。\n"
        if force_diagnosis else ""
    )
    return f"""## 进化诊断任务
{force_section}

### 疼痛信号
管线: {board.pipeline_id}
Trace: {board.trace_id}
问题描述: {board.quality_verdict}

### 当前 Focus 假设
节点: {hypothesis.suspect_node}
置信度: {hypothesis.confidence:.2f}
假设陈述: {hypothesis.statement}
可证伪性测试: {hypothesis.falsification_test}
{classification_section}{history_section}{user_answer_section}
### Trace 执行上下文
（按执行顺序；关注每个节点的输入是否完整、HARD节点只是症状指示器）

{trace_context}
{io_section}
### 相关节点源代码（已自动加载）
（focus 节点的源码或其上游 LLM 节点源码已加载；若你怀疑其他节点的代码实现有问题，
可以输出 read_source 动作请求查看，最多 2 次）

{node_source[:12000]}

### 诊断要求

请严格按照诊断流程执行：
1. 【Step 1】确认每个节点的 SOFT/HARD 分类，HARD 节点不能作为最终根因（除非确认实现 bug）
2. 【Step 2】对怀疑的节点先检查其输入完整性——输入有问题则归因上游
3. 【Step 3】分类错误类型（llm_processing / tool_programming / format_definition / needs_user_clarification）
4. 【Step 4】检查 Format_out 是否充分描述了任务真实约束
5. 【Step 5】提出和实验历史不重复的具体修改建议，target_node 必须是 SOFT 节点
"""


# ── 主入口 ──


class DiagnosisAgent:
    """B.2 深度诊断 Agent

    对黑板中 confidence 最高的 focus 假设进行深度诊断，
    产出 DiagnosisReport，并更新黑板。

    用法：
        agent = DiagnosisAgent(store)  # Move 8: unified path
        report = await agent.run(board)
    """

    def __init__(
        self,
        store: HypothesisBoardStore,
        bus_path: str | None = None,  # Move 8: None → unified data/events.db
        llm: LLMClient | None = None,
    ):
        self.store = store
        self.bus_path = bus_path
        self._llm = llm or LLMClient()

    async def run(
        self,
        board: HypothesisBoard,
        focus_hypothesis_id: str | None = None,
        user_answer: str | None = None,
    ) -> DiagnosisReport | None:
        """执行深度诊断"""

        # 选 focus 假设
        if focus_hypothesis_id:
            focus = board.get_hypothesis(focus_hypothesis_id)
        else:
            focus = board.focus_candidate()

        if not focus:
            logger.warning("No active hypothesis to diagnose")
            return None

        logger.info(
            "[diagnosis] Focus: node=%s confidence=%.2f",
            focus.suspect_node, focus.confidence,
        )

        # 加载 trace 事件
        bus_path = board.__dict__.get("_bus_path") or self.bus_path
        events = await _load_events(bus_path, board.trace_id)

        # 尝试加载 pipeline spec，用于判断节点类型和上游依赖
        spec = _load_pipeline_spec(board.pipeline_id)
        focus_node = focus.suspect_node or ""

        # 确定加载源码的目标节点：
        # 若 focus 是确定性 HARD 节点（无 prompt 可改），找上游 LLM 节点
        source_nodes = [focus_node]
        if spec and _is_hard_validator(focus_node, spec):
            upstream_llm = _find_upstream_llm_nodes(focus_node, spec)
            # 只保留本次 trace 中实际执行过的节点（避免加载未触发的修复节点）
            executed_nodes = {ev.payload.get("node") for ev in events if ev.payload.get("node")}
            upstream_llm_executed = [n for n in upstream_llm if n in executed_nodes]
            if upstream_llm_executed:
                source_nodes = upstream_llm_executed
            elif upstream_llm:
                source_nodes = upstream_llm[:2]  # fallback: 取最近2个
            logger.info(
                "[diagnosis] Focus %s is HARD validator, loading upstream LLM nodes: %s",
                focus_node, source_nodes,
            )

        node_source = "\n\n".join(
            _load_node_source(n, board.pipeline_id) for n in source_nodes
        )

        # 组装 trace 上下文（按预算）
        trace_context = _load_trace_context(
            events,
            focus_node_id=focus_node,
            budget=_CONTEXT_BUDGET // 3,
        )

        # 全量 I/O：包含 source_nodes 和 focus_node 的上下游关键节点
        io_nodes = list({focus_node, *source_nodes})
        io_context = _load_io_context(events, io_nodes, budget=_CONTEXT_BUDGET // 4)

        # 节点 SOFT/HARD 分类表
        node_classification = _build_node_classification_table(events, spec)

        # 若 focus 是 HARD 节点，也把其自身源码加入（供实现正确性判断）
        if spec and _is_hard_validator(focus_node, spec) and focus_node not in source_nodes:
            hard_source = _load_node_source(focus_node, board.pipeline_id)
            node_source = hard_source + "\n\n--- 上游 LLM 节点 ---\n\n" + node_source

        # 构建 prompt
        prompt = _build_diagnosis_prompt(
            focus, board, trace_context, node_source, io_context, node_classification,
            user_answer=user_answer,
        )

        logger.info("[diagnosis] Context assembled, calling LLM...")

        # 调用 LLM（支持 read_source 循环，最多 _MAX_SOURCE_READS 次额外请求 + 1 次最终诊断）
        _MAX_SOURCE_READS = 2
        extra_sources: list[str] = []
        source_reads_done = 0

        raw: dict = {}
        # range = MAX_SOURCE_READS + 2：留出"达到上限后的最终诊断"调用槽
        for _attempt in range(_MAX_SOURCE_READS + 2):
            current_source = node_source
            if extra_sources:
                current_source = (
                    node_source
                    + "\n\n--- 按请求追加的源码 ---\n\n"
                    + "\n\n".join(extra_sources)
                )

            current_prompt = _build_diagnosis_prompt(
                focus, board, trace_context, current_source, io_context,
                node_classification, user_answer=user_answer if _attempt == 0 else None,
                force_diagnosis=(source_reads_done >= _MAX_SOURCE_READS),
            )

            # 若已达 read_source 上限，使用去掉 read_source 段落的 system 消息，
            # 防止 LLM 仍尝试请求更多源码
            import re as _re
            _force = source_reads_done >= _MAX_SOURCE_READS
            if _force:
                system_msg = _re.sub(
                    r"\[READ_SOURCE_SECTION\].*?\[/READ_SOURCE_SECTION\]",
                    "",
                    _SYSTEM_DIAGNOSIS,
                    flags=_re.DOTALL,
                ).strip()
            else:
                system_msg = _SYSTEM_DIAGNOSIS

            try:
                response = self._llm.call(
                    messages=[{"role": "user", "content": current_prompt}],
                    system=system_msg,
                )
                text = ""
                if hasattr(response, "content"):
                    for block in response.content:
                        if hasattr(block, "text"):
                            text += block.text
                elif isinstance(response, str):
                    text = response

                text = text.strip()
                if "```" in text:
                    parts = text.split("```")
                    for part in parts:
                        if part.startswith("json"):
                            text = part[4:].strip()
                            break
                        elif "{" in part:
                            text = part.strip()
                            break

                parsed = json.loads(text)
            except Exception as e:
                logger.error("[diagnosis] LLM call or JSON parse failed: %s", e)
                return None

            # 检查是否为 read_source 请求
            if parsed.get("action") == "read_source":
                requested_node = parsed.get("node", "")
                reason = parsed.get("reason", "")
                if requested_node and source_reads_done < _MAX_SOURCE_READS:
                    source_reads_done += 1
                    logger.info(
                        "[diagnosis] LLM requests source for node=%s reason=%s (%d/%d)",
                        requested_node, reason[:60], source_reads_done, _MAX_SOURCE_READS,
                    )
                    src = _load_node_source(requested_node, board.pipeline_id)
                    extra_sources.append(f"# 节点 {requested_node} 源码（按请求加载）\n{src}")
                    continue  # 重新调用 LLM
                else:
                    # 已达上限或缺少 node 字段：force_diagnosis=True 已在 prompt 顶部注入警告
                    logger.warning(
                        "[diagnosis] read_source limit reached (done=%d), forcing final diagnosis",
                        source_reads_done,
                    )
                    continue  # 下一次循环 force_diagnosis=True 会在 prompt 顶部注入警告

            # 正式诊断报告
            raw = parsed
            break
        else:
            logger.error("[diagnosis] Exhausted all attempts without final diagnosis")
            return None

        # 构建 DiagnosisReport
        proposed_changes = [
            ProposedChange(
                target_node=c.get("target_node", focus.suspect_node or ""),
                change_type=c.get("change_type", "prompt"),
                change_description=c.get("change_description", ""),
                expected_effect=c.get("expected_effect", ""),
                risk_level=c.get("risk_level", "low"),
                blast_radius=c.get("blast_radius", ""),
                target_method=c.get("target_method", ""),
                error_category=c.get("error_category", "llm_processing"),
            )
            for c in raw.get("proposed_changes", [])
        ]

        report = DiagnosisReport(
            root_cause_node=raw.get("root_cause_node", focus.suspect_node or ""),
            root_cause_explanation=raw.get("root_cause_explanation", ""),
            error_category=raw.get("error_category", "llm_processing"),
            user_inquiry=raw.get("user_inquiry", ""),
            format_adequacy_check=raw.get("format_adequacy_check", []),
            evidence_from_traces=raw.get("evidence_from_traces", []),
            comparison_with_success=raw.get("comparison_with_success", ""),
            refined_hypotheses=raw.get("refined_hypotheses", []),
            what_not_to_change=raw.get("what_not_to_change", []),
            proposed_changes=proposed_changes,
            confidence=float(raw.get("confidence", 0.5)),
            uncertainty=raw.get("uncertainty", ""),
            focus_hypothesis_id=focus.id,
        )

        logger.info(
            "[diagnosis] Report: root_cause=%s confidence=%.2f changes=%d",
            report.root_cause_node,
            report.confidence,
            len(report.proposed_changes),
        )

        # 更新黑板上的假设（基于诊断精化）
        self._update_board_from_report(board, focus, report)
        self.store.save(board)

        return report

    def _update_board_from_report(
        self,
        board: HypothesisBoard,
        focus: Hypothesis,
        report: DiagnosisReport,
    ) -> None:
        """根据诊断报告更新黑板假设"""
        # 更新 focus 假设的置信度和陈述
        if report.refined_hypotheses:
            refined = report.refined_hypotheses[0]
            if refined.get("statement"):
                focus.statement = refined["statement"]
            if "confidence" in refined:
                focus.confidence = float(refined["confidence"])
            if refined.get("falsification_test"):
                focus.falsification_test = refined["falsification_test"]

        # 如果诊断指向不同节点，添加新假设
        if (report.root_cause_node and
                report.root_cause_node != focus.suspect_node and
                report.root_cause_node not in {h.suspect_node for h in board.hypotheses}):
            import uuid as _uuid
            from omnicompany.packages.services._core.evolution.workflow.hypothesis import HypothesisStatus
            new_h = Hypothesis(
                id=str(_uuid.uuid4()),
                statement=report.root_cause_explanation,
                suspect_node=report.root_cause_node,
                confidence=report.confidence * 0.9,
                status=HypothesisStatus.ACTIVE,
                relevant_nodes=[report.root_cause_node],
                relevant_traces=[board.trace_id],
                supporting_traces=[board.trace_id],
                falsification_test=(
                    report.refined_hypotheses[0].get("falsification_test", "")
                    if report.refined_hypotheses else ""
                ),
                created_by="deep_diagnosis",
                parent_hypothesis_id=focus.id,
            )
            board.hypotheses.append(new_h)
            logger.info(
                "[diagnosis] New hypothesis from diagnosis: node=%s",
                report.root_cause_node,
            )

        board.updated_at = datetime.now(timezone.utc)
