# [OMNI] origin=claude-code domain=evolution/workflow ts=2026-04-08T03:23:38Z
# [OMNI] material_id="material:core.evolution.workflow.pain_signal_model.py"
"""疼痛信号数据结构

两种疼痛模式：
- QualityPainSignal: 质量慢性痛（管线跑完但输出不满足期望）
- RedLinePainSignal: 红线急性痛（节点触犯硬约束，即时停止）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class QualityPainSignal:
    """质量疼痛信号 — 触发 Flow B（慢性痛诊断流）

    由管线末尾的质检节点在判定输出不符合预期时发出。
    """

    trace_id: str
    """触发进化的那次运行的 trace_id"""

    pipeline_id: str
    """哪条管线"""

    failing_node_id: str
    """质检节点 ID（谁判定失败的）"""

    quality_verdict: str
    """怎么不行（语义描述，自然语言）
    例：'计划步骤过于笼统，缺少具体工具调用' """

    expected_format: str
    """期望的 Format 语义（format_out 的 id 或描述）"""

    actual_output_summary: str
    """实际输出节录（不超过 2000 字符）"""

    severity: str = "soft"
    """soft = LLM 判定质量不达标 | hard = 硬规则判定违规"""

    bus_path: str = ""
    """SQLiteBus 的数据库文件路径（空=使用默认路径）"""

    pipeline_input: dict = field(default_factory=dict)
    """触发本次运行的原始管线输入，用于 B.3 重跑实验"""

    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class RedLinePainSignal:
    """红线疼痛信号 — 触发 Flow A（急性痛修复流）

    由 GuardianHook 或 SafetyRouter 在检测到硬约束违反时发出。
    位置即原因，不需要深度诊断。
    """

    trace_id: str
    pipeline_id: str

    violating_node_id: str
    """哪个节点产生了违规操作"""

    operation_type: str
    """违规操作类型（如 'fs.delete', 'process.kill', 'env.modify'）"""

    operation_detail: str
    """具体的违规内容"""

    constraint_violated: str
    """被触犯的约束（自然语言描述）"""

    bus_path: str = ""
    created_at: datetime = field(default_factory=_utcnow)
