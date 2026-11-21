# [OMNI] origin=claude-code domain=runtime/agent_crystallize/protocol ts=2026-04-15T00:00:00Z
# [OMNI] material_id="material:runtime.agent_crystallize.protocol.data_model.py"
"""ExperienceCrystallizer 插件协议 + 数据结构.

核心对象:
  AgentLoopTrace        — AgentNodeLoop 运行的结构化快照
  CrystallizerObservation — 单个 crystallizer 的观察结果
  SpecPatch             — 提议的规范变更 (写进 pending/ 等人审)
  ExperienceCrystallizer — 插件接口 (Protocol)
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCallRecord:
    """Agent 一次工具调用的记录."""
    turn: int
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    result_preview: str = ""  # 前 500 字符
    error: str | None = None


@dataclass
class AgentLoopTrace:
    """AgentNodeLoop 运行的结构化 trace.

    由 build_agent_loop_trace(loop) 在 loop 结束后构造.
    crystallizer 基于此推断模式.
    """

    node_id: str
    """节点 id (如 'module_explorer')."""

    router_class: str
    """Router 类名 (如 'ModuleExplorerRouter')."""

    format_in: str
    format_out: str
    description: str
    """该节点的规范信息, crystallizer 对照这些判断"是否该更新"."""

    total_turns: int
    """Agent 循环总轮数."""

    finished_reason: str
    """'finish_tool' / 'no_tool_calls' / 'budget_exhaust' / 其他."""

    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    """按时序的工具调用序列."""

    external_node_accesses: list[str] = field(default_factory=list)
    """Agent 访问了哪些其他节点的输出 (从 tool args 推断).
    例: 发现 args.node_id == 'repo_mapper' → 'repo_mapper'."""

    upstream_input_keys: list[str] = field(default_factory=list)
    """Agent 起手时 input_data 里的字段 keys."""

    final_answer_preview: str = ""
    """最终答案前 2000 字."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0

    meta: dict[str, Any] = field(default_factory=dict)
    """其他元数据 (pipeline_id / trace_id / 时间戳等)."""


@dataclass
class CrystallizerObservation:
    """单个 crystallizer 对 trace 的观察.

    独立类型, 让每种 crystallizer 可以写自己感兴趣的字段.
    最终 propose() 把观察转为 SpecPatch.
    """

    crystallizer: str  # 产生观察的 crystallizer 名
    facts: dict[str, Any] = field(default_factory=dict)
    """ crystallizer-specific 的关键事实.
    例: TraceSummarizer 会写 tool_usage_counts / repeated_args.
    """
    narrative: str = ""  # 人类可读的观察叙述 (给 SpecPatch 用)


@dataclass
class SpecPatch:
    """提议的规范变更候选 (等人审, 不自动应用).

    patch 的生命周期:
      1. crystallizer.propose() 产出
      2. pending_queue.write_pending_patch() 落盘到 data/crystallize/pending/
      3. 人审: 批准 → patch_applier.apply();  拒绝 → 移动到 rejected/
    """

    patch_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    crystallizer: str = "?"
    target_router: str = "?"
    """Router 类名, 如 'SpecParserRouter'."""
    patch_type: str = "description_refine"
    """description_refine / format_components_add / tool_manual / other."""
    title: str = ""
    """20 字以内标题."""
    rationale: str = ""
    """为什么建议这么改 (from observation narrative)."""
    target_file: str | None = None
    """目标文件 (若已知). 可选."""
    current_value: Any = None
    """当前值 (如 DESCRIPTION 的当前内容). 人审时可参照."""
    proposed_value: Any = None
    """建议新值."""
    evidence: list[str] = field(default_factory=list)
    """证据 bullet (from trace: agent 做了什么)."""
    confidence: float = 0.5
    """crystallizer 对本 patch 建议的自信度 0-1."""
    created_ts: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)


class ExperienceCrystallizer(Protocol):
    """ crystallizer 插件协议.

    name:    唯一 slug (trace / format / description / tools).
    observe: 从 trace 提炼观察, 不产出 patch.
    propose: 基于 observation + 下游评价, 产出 patch 列表 (可为空).

    下游评价 downstream_eval 形态例:
        {"pipeline_verdict": "PASS", "final_findings_count": 10}
    """

    name: str

    def observe(self, trace: AgentLoopTrace) -> CrystallizerObservation: ...

    def propose(
        self,
        observation: CrystallizerObservation,
        downstream_eval: dict[str, Any],
    ) -> list[SpecPatch]: ...
