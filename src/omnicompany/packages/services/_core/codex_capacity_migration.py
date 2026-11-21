# [OMNI] origin=claude-code domain=core type=module status=active
# [OMNI] summary="Codex 容量链反复失败后的一次性继承迁移: 判据、交接正文与继任种子"
# [OMNI] tags=codex,capacity,migration,handoff,session
"""One-shot successor migration for a Codex capacity failure chain.

The in-place retry (``codex_capacity_retry``) stays the primary behaviour: low
frequency, same session, same model.  This module only decides whether one
failure chain has earned its single migration to a fresh formal session, and
assembles the durable inheritance text.  It performs no I/O and knows nothing
about a terminal, so every threshold is testable with controlled time.

Two triggers, both requiring *current* provider-owned evidence:

* two consecutive in-place attempts about 15 minutes apart that are still met
  by a real capacity error;
* one recovery that is then followed by 30 minutes without any real provider
  progress.

Everything else — difficulty, slowness, a failing test, unfinished work, or the
capacity error itself — is explicitly not a business block and never migrates
under a "blocked" label.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


#: Two in-place attempts must be about 15 minutes apart.  The retry interval is
#: 900s; a scheduler tick can land a few seconds early, so accept 5% jitter and
#: nothing more.  A shorter gap is not "two attempts", it is one burst.
MIGRATION_MIN_ATTEMPT_GAP_SECONDS = 855.0

#: A recovered chain that produces no real provider progress for this long is
#: treated as a silent stall.
RECOVERY_SILENCE_SECONDS = 1800.0

#: A migration that cannot be completed is retried at most this many times, so
#: a broken spawn path cannot turn into an endless migration loop.
MAX_MIGRATION_FAILURES = 2

MIGRATION_STATE_NONE = "none"
MIGRATION_STATE_RUNNING = "migrating"
MIGRATION_STATE_DONE = "migrated"
MIGRATION_STATE_INHERITED = "inherited"
MIGRATION_STATE_EXHAUSTED = "exhausted"

#: The only reasons that count as a real business block.  A real block is not
#: fixed by a new session, so it also stops a migration.
REAL_BLOCK_REASONS: dict[str, str] = {
    "permission": "permission",
    "permission_denied": "permission",
    "no_permission": "permission",
    "access_denied": "permission",
    "user_decision": "user_decision",
    "awaiting_user": "user_decision",
    "needs_user_decision": "user_decision",
    "user_input_required": "user_decision",
    "external_dependency_unavailable": "external_dependency_unavailable",
    "dependency_unavailable": "external_dependency_unavailable",
    "upstream_unavailable": "external_dependency_unavailable",
    "service_unavailable": "external_dependency_unavailable",
    "identity_unsafe": "identity_unsafe",
    "identity_unknown": "identity_unsafe",
    "identity_mismatch": "identity_unsafe",
    "unsafe_identity": "identity_unsafe",
}

#: Named here only so the rule is visible where it is enforced; the classifier
#: is a strict allow-list and does not consult this set.
NEVER_A_BLOCK = (
    "capacity", "at_capacity", "rate_limited", "hard", "difficult", "slow",
    "timeout", "test_failed", "tests_red", "build_failed", "incomplete",
    "not_done", "wip", "unfinished", "needs_more_time",
)

#: One口径, quoted verbatim into both the handoff document and the successor's
#: opening instruction, so the next session cannot re-invent it.
BLOCK_CRITERIA_TEXT = (
    "真实阻断只有四类: (1) 权限不足; (2) 需要用户裁决; (3) 外部依赖不可用; "
    "(4) 身份不安全/无法确认。困难、慢、测试不过、还没做完、以及容量错误本身"
    "都不是业务阻断, 不得以 blocked 名义停工或再申请迁移。"
)

NO_REDO_TEXT = (
    "交接文档「已完成且有实证」里的条目一律不重做; 只有拿到相反证据时才复核, "
    "并先在 handoff note 里写明证据再动手。"
)

NO_SECOND_MIGRATION_TEXT = (
    "本失败链的一次性迁移已经用掉。继任会话若立刻又遇到容量错误, 只回到低频原地"
    "等待重试, 不得再建第三个会话。"
)


def normalize_block_reason(reason: Any) -> str:
    text = str(reason or "").strip().lower().replace("-", "_").replace(" ", "_")
    while "__" in text:
        text = text.replace("__", "_")
    return text


def classify_block_reason(reason: Any) -> str:
    """Return the canonical real-block class, or ``""`` for everything else."""
    return REAL_BLOCK_REASONS.get(normalize_block_reason(reason), "")


def is_real_block(reason: Any) -> bool:
    return bool(classify_block_reason(reason))


@dataclass(frozen=True)
class MigrationDecision:
    eligible: bool
    reason: str
    trigger: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"eligible": self.eligible, "reason": self.reason, "trigger": self.trigger}


def _no(reason: str) -> MigrationDecision:
    return MigrationDecision(False, reason)


def evaluate_migration(record: Any, snapshot: Any, *, now: float) -> MigrationDecision:
    """Decide whether this exact failure chain may migrate once, right now.

    ``record`` is a ``RetryRecord``; ``snapshot`` is the live ``SessionSnapshot``
    or ``None``.  Every unknown fails closed: a migration spawns a real session
    and cannot be undone by waiting.
    """

    state = getattr(record, "migration_state", MIGRATION_STATE_NONE)
    if state != MIGRATION_STATE_NONE:
        return _no(f"migration-{state}")
    if int(getattr(record, "migration_failures", 0) or 0) >= MAX_MIGRATION_FAILURES:
        return _no("migration-failed-too-many")
    if record.state not in {"waiting", "succeeded"}:
        return _no(f"record-{record.state}")
    if snapshot is None:
        return _no("session-missing")
    if not snapshot.identity_known or not snapshot.permission_known:
        return _no("identity-or-permission-unknown")
    if snapshot.generation != record.generation or snapshot.provider_session_id != record.provider_session_id:
        return _no("stale-session-generation")
    if not snapshot.alive or snapshot.human_closed:
        return _no("closed-session")
    if snapshot.human_paused:
        return _no("human-paused")
    if not snapshot.working_known:
        return _no("activity-unknown")
    if snapshot.working:
        return _no("busy")
    if snapshot.pending_input or snapshot.input_at > float(record.first_seen_at or 0):
        return _no("superseded-input")
    blocked = classify_block_reason(getattr(snapshot, "block_reason", ""))
    if blocked:
        # A real block needs a person or an external system, not a new session.
        return _no(f"real-block:{blocked}")

    if record.state == "waiting":
        return _evaluate_repeated_capacity(record, snapshot)
    return _evaluate_silent_recovery(record, snapshot, now=now)


def _evaluate_repeated_capacity(record: Any, snapshot: Any) -> MigrationDecision:
    if int(record.attempts or 0) < 2:
        return _no("fewer-than-two-attempts")
    previous = float(getattr(record, "prev_attempt_at", 0) or 0)
    latest = float(record.last_attempt_at or 0)
    if previous <= 0 or latest - previous < MIGRATION_MIN_ATTEMPT_GAP_SECONDS:
        return _no("attempts-too-close")
    failure = getattr(snapshot, "capacity_failure", None)
    if failure is None:
        return _no("capacity-evidence-unavailable")
    if not failure:
        return _no("no-current-capacity-failure")
    if float(failure.get("failed_at") or 0) < latest:
        # Stale waiting: the newest owned failure predates the latest attempt,
        # so we have no evidence the second attempt was met by capacity again.
        return _no("stale-capacity-evidence")
    return MigrationDecision(True, "two-consecutive-capacity-failures", "repeated-capacity")


#: A silent session only has something to inherit while its goal says work
#: remains.  A finished, absent, or unreadable goal is not a stall.
UNFINISHED_GOAL_STATUSES = frozenset({"active", "blocked", "usage_limited", "budget_limited", "paused"})


def _evaluate_silent_recovery(record: Any, snapshot: Any, *, now: float) -> MigrationDecision:
    recovered_at = float(getattr(record, "recovered_at", 0) or 0)
    if recovered_at <= 0:
        return _no("no-recovery-timestamp")
    if now - recovered_at < RECOVERY_SILENCE_SECONDS:
        return _no("recovery-silence-too-short")
    if not snapshot.goal_known:
        return _no("goal-unknown")
    status = str((snapshot.goal_state or {}).get("status") or "").strip().lower()
    if status not in UNFINISHED_GOAL_STATUSES:
        # Silence after a finished or absent goal means the work ended, not
        # that it stalled; a successor would have nothing to continue.
        return _no(f"goal-not-unfinished:{status or 'none'}")
    if not getattr(snapshot, "progress_known", False):
        return _no("provider-progress-unknown")
    if float(getattr(snapshot, "last_provider_progress_at", 0) or 0) > recovered_at:
        return _no("provider-progress-after-recovery")
    return MigrationDecision(True, "recovered-then-silent", "silent-recovery")


# ── inheritance text ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MigrationContext:
    """Everything the successor must inherit, collected from durable sources."""

    handoff_id: str
    session_id: str
    provider: str
    provider_session_id: str
    runtime_host: str
    cwd: str
    trigger: str
    goal: Mapping[str, Any]
    binding: Mapping[str, Any]
    chain: Mapping[str, Any]
    plan_progress: Sequence[str] = ()
    completed_evidence: Sequence[str] = ()
    remaining_work: Sequence[str] = ()
    claims: Sequence[Mapping[str, Any]] = ()
    verify_commands: Sequence[str] = ()
    entry_paths: Sequence[str] = ()
    transcript: str = ""
    state_path: str = ""


def goal_line(goal: Mapping[str, Any]) -> str:
    if not goal or not goal.get("known"):
        return "原 goal: 未知(原生 goal 元数据不可读) —— 继任会话必须先自行确认目标, 不得臆造。"
    objective = str(goal.get("objective") or "").strip()
    status = str(goal.get("status") or "unknown")
    body = objective if objective else "(原生 goal 无 objective 文本)"
    return f"原 goal[{status}] 原文: {body}"


def _bullets(items: Sequence[Any], empty: str) -> list[str]:
    rows = [f"- {str(item).strip()}" for item in items if str(item or "").strip()]
    return rows or [f"- {empty}"]


def _claim_lines(claims: Sequence[Mapping[str, Any]]) -> list[str]:
    rows: list[str] = []
    for claim in claims:
        targets = ", ".join(
            str(target.get("path") or "") + ("/**" if target.get("recursive") else "")
            for target in claim.get("targets") or []
        )
        rows.append(
            f"- `{claim.get('id')}` status={claim.get('status')} purpose={claim.get('purpose')} "
            f"targets: {targets}"
        )
    return rows


def build_sections(ctx: MigrationContext) -> dict[str, str]:
    """Fill every mandatory handoff section from machine-collected facts."""

    binding = ctx.binding or {}
    chain = ctx.chain or {}
    bind_bits = " · ".join(
        f"{key}=`{binding.get(key)}`"
        for key in ("project", "workstream", "plan", "task", "topic")
        if str(binding.get(key) or "").strip()
    ) or "无业务绑定登记"

    status = [
        f"{ctx.provider} 会话 `{ctx.session_id}`(原生 `{ctx.provider_session_id}`, host {ctx.runtime_host})"
        f" 因 Codex 容量错误反复失败, 按 `{ctx.trigger}` 判据触发本失败链唯一一次继承迁移;"
        f" 原会话保留但不再自动重试, 后续由继任会话推进。",
        goal_line(ctx.goal),
        f"绑定: {bind_bits}",
    ]

    constraints = [
        f"- 迁移判据: {chain.get('trigger_detail') or ctx.trigger}; 每条失败链只允许迁移一次。",
        f"- {NO_SECOND_MIGRATION_TEXT}",
        f"- {NO_REDO_TEXT}",
        f"- 阻断口径(防伪): {BLOCK_CRITERIA_TEXT}",
        "- 低频原地重试仍是首选: 容量错误按 900 秒间隔原地重试, 不换模型/账号/工作目录。",
        "- 继承范围以本文档为准, 不扩范围; 需要写共享路径前先 `omni hr claim` 重新声明。",
    ]

    completed = [
        f"- 原生 goal 状态: {ctx.goal.get('status') if ctx.goal else 'unknown'}"
        f"(证据: 原生 goals_1.sqlite / `omni codex-capacity-retry status`)",
        f"- 容量链: 首次失败 {chain.get('first_seen_at')}, 已原地重试 {chain.get('attempts')} 次,"
        f" 最近一次 {chain.get('last_attempt_at')}, 最近一次真实容量错误 {chain.get('failed_at')}"
        f"(证据: `{ctx.state_path or 'data/_runtime/codex_capacity_retry/state.json'}`)",
        *_bullets(ctx.completed_evidence, "无自动可得的已完成条目; 继任会话须先复验再动手, 不得假设已完成"),
        *_bullets(ctx.plan_progress, "计划 progress 无可读条目"),
    ]
    if ctx.transcript:
        completed.append(f"- 上一棒原始 transcript(要深读读这个, 不以摘要代替): `{ctx.transcript}`")

    remaining = [
        "1. 读完本文档, 然后 `omni handoff adopt` 登记接手。",
        "2. 至少跑一条「复验命令」自证当前状态, 再动手。",
        "3. 需要写共享路径前重新 `omni hr claim`(原会话的 claim 已在下面列明并释放)。",
        "4. 继续原 goal, 不重做已完成项:",
        *[f"   - {str(item).strip()}" for item in ctx.remaining_work if str(item or "").strip()],
    ]
    if not any(str(item or "").strip() for item in ctx.remaining_work):
        remaining.append("   - 原 goal 未拆出机器可读的剩余项; 以 goal 原文与计划 progress.md 的未决项为准。")

    pitfalls = [
        f"- 已知失败: 该会话在 {chain.get('failed_at')} 前后反复命中 provider 容量错误"
        f"(`Selected model is at capacity.`), 原地重试 {chain.get('attempts')} 次未能推进。",
        "- 不要把容量错误、慢、测试不过、没做完当成业务阻断上报; 判据见「用户约束与口径」。",
        "- 不要凭陈旧 waiting 记录或旧 PTY 句柄向原会话补发输入; 身份/代际不符一律失败关闭。",
        "- 原会话进程未被终止, 也不会被自动清理; 不要向它重复投递同一句提示。",
    ]

    claim_rows = _claim_lines(ctx.claims) or ["- 原会话没有活跃写入 claim。"]
    pitfalls.append("- 原会话写入 claim 交接情况:")
    pitfalls.extend(f"  {row}" for row in claim_rows)

    verify_cmds = list(ctx.verify_commands) or ["omni codex-capacity-retry status"]
    verify = []
    for command in verify_cmds:
        verify.append("```bash")
        verify.append(str(command).strip())
        verify.append("```")
    if len(verify_cmds) < 2:
        verify += ["```bash", "omni handoff show " + ctx.handoff_id, "```"]

    entries = []
    seen: set[str] = set()
    for path in ctx.entry_paths:
        text = str(path or "").strip()
        if text and text not in seen:
            seen.add(text)
            entries.append(f"- `{text}`")

    return {
        "一句话状态": "\n".join(status),
        "用户约束与口径": "\n".join(constraints),
        "已完成且有实证": "\n".join(completed),
        "未完成与下一步": "\n".join(remaining),
        "坑与已知失败": "\n".join(pitfalls),
        "复验命令": "\n".join(verify),
        "必读入口": "\n".join(entries),
    }


def render_document(base_draft: str, sections: Mapping[str, str]) -> str:
    """Replace each ``TODO(填写)`` placeholder body with the collected content."""
    lines = base_draft.splitlines()
    out: list[str] = []
    current: str | None = None
    replaced: set[str] = set()
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
            out.append(line)
            continue
        if current in sections and line.lstrip().startswith("TODO(填写)"):
            if current not in replaced:
                out.append(sections[current])
                replaced.add(current)
            continue
        out.append(line)
    for name, body in sections.items():
        if name not in replaced:
            out += ["", f"## {name}", "", body]
    return "\n".join(out).rstrip() + "\n"


def successor_seed(base_seed: str, ctx: MigrationContext, *, max_chars: int = 11000) -> str:
    """Opening instruction for the new formal session: an explicit continuation goal."""
    binding = ctx.binding or {}
    lines = [
        base_seed.rstrip(),
        "",
        "── 本次是容量迁移接续(继承棒), 额外硬约束 ──",
        f"继续目标(continuation goal): {goal_line(ctx.goal)}",
        f"绑定: project={binding.get('project') or '-'} · workstream={binding.get('workstream') or '-'}"
        f" · plan={binding.get('plan') or '-'} · task={binding.get('task') or '-'}",
        f"先建立明确 goal 再开工: /goal 写清上面的继续目标; 并 `omni session bind` 保持同一绑定。",
        NO_REDO_TEXT,
        NO_SECOND_MIGRATION_TEXT,
        f"阻断口径: {BLOCK_CRITERIA_TEXT}",
        "写共享路径前先 `omni hr claim` 重新声明范围(上一棒的 claim 已释放, 文档里有清单)。",
    ]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


__all__ = [
    "BLOCK_CRITERIA_TEXT",
    "MAX_MIGRATION_FAILURES",
    "MIGRATION_MIN_ATTEMPT_GAP_SECONDS",
    "MIGRATION_STATE_DONE",
    "MIGRATION_STATE_EXHAUSTED",
    "MIGRATION_STATE_INHERITED",
    "MIGRATION_STATE_NONE",
    "MIGRATION_STATE_RUNNING",
    "MigrationContext",
    "MigrationDecision",
    "NEVER_A_BLOCK",
    "NO_REDO_TEXT",
    "NO_SECOND_MIGRATION_TEXT",
    "RECOVERY_SILENCE_SECONDS",
    "REAL_BLOCK_REASONS",
    "UNFINISHED_GOAL_STATUSES",
    "build_sections",
    "classify_block_reason",
    "evaluate_migration",
    "goal_line",
    "is_real_block",
    "normalize_block_reason",
    "render_document",
    "successor_seed",
]
