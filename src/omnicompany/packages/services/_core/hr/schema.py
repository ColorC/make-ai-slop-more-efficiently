# [OMNI] origin=claude-code ts=2026-08-17T00:00:00+08:00 type=schema
# [OMNI] summary="LLM 人事系统归一化事件 schema —— 五家 CLI transcript / 文件系统 / git 的共同出口格式"
# [OMNI] why="P1 五棒并行开发，schema 必须先于实现钉死，否则各 collector 各造一套字段"
# [OMNI] tags=hr,schema,attribution,provenance
"""LLM 人事系统 —— 归一化事件 schema（唯一真源）。

权威依据: docs/plans/agent-framework/[2026-08-17]LLM-HR-PERFORMANCE-ATTRIBUTION/plan.md
          （含 §1.5 P0 实证修正）与同目录 P1-implementation-contract.md。

设计约束（沿用 token_ledger 范式）:
  * 纯确定性，无 LLM 调用；
  * 全部数据源根目录可注入，禁止隐式硬编码到真实家目录（测试走 tmp_path）；
  * 只提字段，不复制内容 —— 不落文件正文、不落命令全文；
  * 流式逐行读，禁止对整份文件 .read()/json.load()（最大单文件实测 1.66 GB）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

EVENT_SCHEMA = "omnicompany.hr-event.v1"
WATERMARK_SCHEMA = "omnicompany.hr-watermark.v1"

# ── 值域（闭集，实现方不得自行扩充；需要新值先改本文件） ──────────────────

Provider = Literal["claude", "codex", "codebuddy", "grok", "kiro", "qoder"]
PROVIDERS: tuple[str, ...] = ("claude", "codex", "codebuddy", "grok", "kiro", "qoder")

#   write   —— 写入类工具调用（Write/Edit/MultiEdit/NotebookEdit 及各家等价物）
#   command —— shell/exec 类调用
#   denial  —— 被守卫/权限规则拒绝
#   stray   —— 文件系统巡检发现的坏产物（顶层游离/空文件/乱码路径名）
#   commit  —— git 提交
Kind = Literal["write", "command", "denial", "stray", "commit"]
KINDS: tuple[str, ...] = ("write", "command", "denial", "stray", "commit")

#   applied —— 确认生效
#   denied  —— 被拒绝
#   error   —— 工具报错
#   unknown —— 该源无法判定
Verdict = Literal["applied", "denied", "error", "unknown"]
VERDICTS: tuple[str, ...] = ("applied", "denied", "error", "unknown")

#   high    —— 字段直读（claude/codebuddy/qoder 的 message.model 等）
#   low     —— 推断或大量缺失（kiro: 11/15 会话 model_info 为 null）
#   unknown —— 完全取不到
Confidence = Literal["high", "low", "unknown"]
CONFIDENCES: tuple[str, ...] = ("high", "low", "unknown")

#   direct  —— 目标路径来自结构化字段（claude/codebuddy/qoder/kiro）
#   parsed  —— 目标路径由命令文本启发式解析而来（codex 唯一可用方式）
#   none    —— 该事件不涉及路径
TargetConfidence = Literal["direct", "parsed", "none"]
TARGET_CONFIDENCES: tuple[str, ...] = ("direct", "parsed", "none")


@dataclass(frozen=True)
class HrEvent:
    """一条归一化人事事件。五家 collector 与 fs/vcs collector 的共同出口。

    字段填写纪律:
      * 取不到的字符串字段一律填 ""，**禁止填 None、禁止编造、禁止用默认值冒充实测**；
      * `model_confidence` / `target_confidence` 必须如实反映取值方式，
        kiro 的 model 缺失时填 ("", "unknown")，不得回填全局默认模型快照；
      * `evidence_ref` 必填，格式 "<绝对路径>#L<行号>"，用于任何数字回溯到原始行。
    """

    ts: str                      # ISO8601 UTC，形如 2026-08-17T05:33:42.911Z
    provider: str                # PROVIDERS 之一
    session_id: str
    kind: str                    # KINDS 之一
    evidence_ref: str            # "<abs_path>#L<lineno>"，必填
    operation_id: str = ""
    event_id: str = ""

    model: str = ""
    model_confidence: str = "unknown"     # CONFIDENCES 之一
    parent_session_id: str = ""           # subagent 的父会话；无则 ""
    is_subagent: bool = False
    cwd: str = ""
    git_branch: str = ""

    tool: str = ""                        # 原始工具名，保留各家原词
    target: str = ""                      # 归一化绝对路径；非路径事件填 ""
    target_confidence: str = "none"       # TARGET_CONFIDENCES 之一
    verdict: str = "unknown"              # VERDICTS 之一

    denial_kind: str = ""                 # 来自 claude 的 toolDenialKind，如 "permission-rule"
    guard: str = ""                       # 解析出的守卫名，如 "lock_pretooluse"；不确定填 ""
    note: str = ""                        # 简短备注，禁止放文件正文/命令全文

    def __post_init__(self) -> None:
        if self.event_id:
            return
        identity = {
            "provider": self.provider,
            "session_id": self.session_id,
            "operation": self.operation_id or self.evidence_ref,
            "target": self.target,
            "tool": self.tool,
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:24]
        object.__setattr__(self, "event_id", f"hr-{digest}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CollectResult:
    """单个 collector 一次运行的产出。

    `coverage` 是 P0 立下的硬要求：解析不了的必须如实计数并列出样例，
    禁止用"大部分能解析"糊过去。覆盖率下降必须能被看见（见 plan.md §4 风险表）。
    """

    provider: str
    events: list[HrEvent] = field(default_factory=list)
    files_seen: int = 0
    files_parsed: int = 0
    files_empty: int = 0          # 0 字节会话单独计，算"空会话"不算"解析失败"
    files_failed: int = 0
    lines_scanned: int = 0
    failures: list[str] = field(default_factory=list)   # 样例，最多留 20 条
    watermark: dict[str, Any] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        """可解析文件占非空文件的比例。分母排除 0 字节空会话。"""
        denom = self.files_seen - self.files_empty
        return (self.files_parsed / denom) if denom > 0 else 1.0


def validate_event(event: HrEvent) -> list[str]:
    """返回问题列表，空列表 = 合法。collector 落盘前必须自检。"""
    problems: list[str] = []
    if event.provider not in PROVIDERS:
        problems.append(f"provider not in {PROVIDERS}: {event.provider!r}")
    if event.kind not in KINDS:
        problems.append(f"kind not in {KINDS}: {event.kind!r}")
    if event.verdict not in VERDICTS:
        problems.append(f"verdict not in {VERDICTS}: {event.verdict!r}")
    if event.model_confidence not in CONFIDENCES:
        problems.append(f"model_confidence not in {CONFIDENCES}: {event.model_confidence!r}")
    if event.target_confidence not in TARGET_CONFIDENCES:
        problems.append(f"target_confidence not in {TARGET_CONFIDENCES}: {event.target_confidence!r}")
    if not event.ts:
        problems.append("ts is required")
    if not event.session_id:
        problems.append("session_id is required")
    if not event.evidence_ref or "#L" not in event.evidence_ref:
        problems.append(f"evidence_ref must be '<abs_path>#L<lineno>': {event.evidence_ref!r}")
    if event.kind == "write" and event.target_confidence == "none":
        problems.append("write event must carry target_confidence direct|parsed")
    if event.model and event.model_confidence == "unknown":
        problems.append("model present but model_confidence=unknown")
    return problems


__all__ = [
    "EVENT_SCHEMA",
    "WATERMARK_SCHEMA",
    "PROVIDERS",
    "KINDS",
    "VERDICTS",
    "CONFIDENCES",
    "TARGET_CONFIDENCES",
    "HrEvent",
    "CollectResult",
    "validate_event",
]
