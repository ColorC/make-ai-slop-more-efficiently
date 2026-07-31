# [OMNI] origin=human ts=2026-04-08T03:23:35Z
# [OMNI] material_id="material:protocol.state_anchor.snapshot_model.py"
"""
LAP StateAnchor — 执行时的物理世界状态锚点

问题背景（来自实验 Explorer V4）：
  Agent 在执行时错误地将 LLM 声称的"产出类型"（如 feishu_message_output）
  当作文件系统中可寻址的实体，导致后续任务在 max_steps 内找不到数据而失败。

  根本原因：LAP V0.1/V0.2 只定义了"数据的语义类型"，没有定义
  "执行时的物理世界处于什么状态"，导致两类对象混淆：
    - agent_output  = LLM 声称产出的语义标签（仅在当前 trace 内有效）
    - state_anchor  = 可独立验证的物理事实（git hash、文件摘要、版本号）

核心规则（防止 Explorer V4 的幻觉）：
  1. 只有 StateKind != AGENT_OUTPUT 的锚点才能作为新 Pipeline 的起始 entry。
  2. AGENT_OUTPUT 类型的锚点若需晋升，须经 Hard Anchor 验证后才能提升为
     FILE_HASH 类型（写入磁盘并计算 sha256）。
  3. 每次 run_agent 开始时应快照当前状态，写入 task_state_snapshots 表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ── StateKind —— 按可靠性从高到低排列 ─────────────────────────


class StateKind(str, Enum):
    """物理状态锚点的种类，按可靠性排序。

    同一个概念在不同 Kind 下的可信度差异很大；系统应优先使用
    可信度高的锚点作为 Pipeline 的起始条件。
    """

    GIT_COMMIT = "git_commit"
    """不可变 Git commit hash。一旦推送，内容永不改变。
    注意：本地未提交的改动不属于此类型。"""

    FILE_HASH = "file_hash"
    """文件的 SHA-256 内容摘要。与路径无关，只依赖内容。
    适合验证"我操作的那个文件是否是预期版本"。"""

    P4_CHANGELIST = "p4_changelist"
    """Perforce Changelist 号。服务端真相，但用户可能在此 CL 基础上继续编辑。
    is_mutable=True 时需监控后续改动。"""

    SVN_REVISION = "svn_revision"
    """SVN 版本号。同 P4_CHANGELIST 的注意事项。"""

    API_SNAPSHOT = "api_snapshot"
    """外部服务的时刻状态快照（如 Feishu 消息列表、REST API 响应）。
    带时间戳；实时变化，时效性有限。"""

    AGENT_OUTPUT = "agent_output"
    """LLM 在本次 trace 中声称产出的语义标签。
    ⚠️  这不是 ground truth，只是实例。
    不得被用作新 Pipeline 的独立 entry input，
    除非已经被 Hard Anchor 验证并提升为 FILE_HASH。"""


# ── StateAnchor —— 单个锚点 ────────────────────────────────────


@dataclass
class StateAnchor:
    """物理世界状态的单个可验证锚点。

    Attributes:
        kind:        锚点类型，决定可靠性等级
        ref:         锚点的标识符（commit hash / sha256 / CL号 / API 返回的版本字段）
        path:        对应的文件路径或服务地址（可选）
        verified_at: 本锚点最后一次被验证的时间
        is_mutable:  True = 外部环境可能改变此锚点（如用户在 P4 CL 上继续编辑）
        trace_id:    产生此锚点的 trace（若来自某次 agent run）
        meta:        额外的上下文信息（如 API 端点、分支名等）

    Examples:
        # Git 提交（最可靠）
        StateAnchor(kind=StateKind.GIT_COMMIT, ref="abc123ef",
                    path="e:/WindowsWorkspace")

        # 文件摘要（适合验证文件内容未被意外修改）
        StateAnchor(kind=StateKind.FILE_HASH, ref="sha256:abcdef...",
                    path="data/feishu_v1_state.json")

        # Agent 产出（低可靠，后续任务不应以此为起点）
        StateAnchor(kind=StateKind.AGENT_OUTPUT, ref="feishu_message_output",
                    trace_id="01KM4Z2A...", is_mutable=True)
    """

    kind: StateKind
    ref: str
    path: str | None = None
    verified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_mutable: bool = False
    trace_id: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_trustworthy_entry(self) -> bool:
        """是否可以作为新 Pipeline 的独立 entry input（不依赖上下文）。

        AGENT_OUTPUT 永远返回 False，其他类型在 is_mutable=False 时返回 True。
        """
        if self.kind == StateKind.AGENT_OUTPUT:
            return False
        return not self.is_mutable


# ── StateSnapshot —— 一次任务执行的完整状态快照 ──────────────────


@dataclass
class StateSnapshot:
    """一次 run_agent 调用开始时的物理世界状态快照。

    设计目标：
    - 使 Pipeline 的执行结果可重现（给定相同 snapshot，应产出相同结果）
    - 记录"这次任务是在什么环境下运行的"，用于 Critic 分析和复盘
    - 区分"有 ground truth 支撑的 entry"和"仅依赖 LLM 输出的 entry"

    Attributes:
        task_id:      对应的任务 ULID
        trace_id:     对应的 trace ULID
        anchors:      本次快照包含的所有锚点列表
        assertion:    快照的可信度声明
                      'HARD' = 所有锚点均已验证（通过 Hard Anchor）
                      'SOFT' = 用户声称或 LLM 推断，未经独立验证
    """

    task_id: str
    trace_id: str
    anchors: list[StateAnchor] = field(default_factory=list)
    assertion: str = "SOFT"  # 'HARD' | 'SOFT'

    def has_trustworthy_entries(self) -> bool:
        """是否有至少一个可信的 entry 锚点。"""
        return any(a.is_trustworthy_entry for a in self.anchors)

    def agent_output_anchors(self) -> list[StateAnchor]:
        """返回所有 AGENT_OUTPUT 类型的锚点（幻觉风险来源）。"""
        return [a for a in self.anchors if a.kind == StateKind.AGENT_OUTPUT]

    def hard_anchors(self) -> list[StateAnchor]:
        """返回所有可信的非 AGENT_OUTPUT 锚点。"""
        return [a for a in self.anchors if a.is_trustworthy_entry]
