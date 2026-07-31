# [OMNI] origin=ai-ide domain=dashboard/cc_wrapper/hooks ts=2026-05-04T00:00:00Z type=hook status=active
# [OMNI] summary="UserPromptSubmit hook — 切 plan 后只补一次最小目录与入口文件指针"
# [OMNI] why="活跃会话切换计划后需要更新定位信息，但不能重复注入计划正文、清单和退出条件"
# [OMNI] tags=cc-wrapper,hook,plan-binding,reinject
# [OMNI] material_id="material:dashboard.cc_wrapper.hooks.user_prompt_submit_plan_reinject.implementation.py"
"""UserPromptSubmit hook — re-inject minimal pointers after a plan switch.

Triggered before each user prompt is sent to the LLM. Compares
`active_plan_changed_ts` vs `last_plan_inject_ts` in `cc_sessions.json[pty_id]`;
if a switch happened since last injection, emit `additionalContext` with the
new plan's directory/start-file pointers, then advance the marker so we don't re-inject
on subsequent turns.

This is alive-session re-injection (option b in CC-PLAN-SESSION-CONTEXT 段二
审议). 不破系统提示词缓存 (additionalContext 是 per-turn 注入, 不进 system),
也不需要用户主动 /clear.

No-op when:
  - OMNI_CC_PTY_ID not set (claude not spawned via dashboard wrapper)
  - cc_sessions.json missing or pty_id absent
  - active_plan_changed_ts <= last_plan_inject_ts (already injected or never switched)
"""
from __future__ import annotations

import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

from . import _shared as sh


def _build_plan_summary(plan_id: str, plan_meta: dict, payload: dict | None = None) -> str:
    """Return the same bounded pointer set used at SessionStart."""

    root = sh.repo_root()
    plan = root / "docs" / "plans" / plan_id
    return sh.build_minimal_context(
        cwd=str((payload or {}).get("cwd") or root),
        plan=plan if plan.is_dir() else None,
        payload=payload,
        plan_meta=plan_meta,
        reason="plan switched",
    )


def _plan_reinject(payload: dict) -> str | None:
    """切 plan 后下一条 turn 重注入 plan_meta(PTY + native provider ledger)。"""
    pty_id = os.environ.get("OMNI_CC_PTY_ID")
    if not pty_id:
        try:
            from omnicompany.packages.services._core.identity import (
                get_session_binding,
                update_session_binding,
            )
            trace_id = sh.trace_id_for(payload)
            binding = get_session_binding(trace_id)
            plan_id = binding.get("active_plan")
            if not plan_id or binding.get("last_plan_inject_id") == plan_id:
                return None
            plan_md = sh.repo_root() / "docs" / "plans" / str(plan_id) / "plan.md"
            plan_meta: dict = {}
            if plan_md.is_file():
                from omnicompany.dashboard.controlplane.plans import parse_plan_frontmatter

                plan_meta = parse_plan_frontmatter(plan_md) or {}
            text = _build_plan_summary(str(plan_id), plan_meta, payload)
            update_session_binding(trace_id, last_plan_inject_id=plan_id)
            sh.append_audit("user_prompt_submit_plan_reinject", {
                "trace_id": trace_id,
                "provider": sh.binding_provider(payload),
                "plan_id": plan_id,
                "context_chars": len(text),
            })
            return text
        except Exception:
            return None

    try:
        from omnicompany.dashboard.ccdaemon.pty import (
            _mutate_meta_store,
            _read_meta_store,
        )

        store = _read_meta_store(use_cache=False)
    except (ImportError, OSError, ValueError):
        return None
    entry = store.get(pty_id) or {}
    changed_ts = entry.get("active_plan_changed_ts") or 0
    last_inject_ts = entry.get("last_plan_inject_ts") or 0
    plan_id = entry.get("active_plan")
    if not changed_ts or not plan_id or changed_ts <= last_inject_ts:
        return None  # no switch since last injection

    root = sh.repo_root()
    plan_md = root / "docs" / "plans" / plan_id / "plan.md"
    plan_meta: dict = {}
    if plan_md.is_file():
        try:
            from omnicompany.dashboard.controlplane.plans import parse_plan_frontmatter
            plan_meta = parse_plan_frontmatter(plan_md) or {}
        except Exception:
            plan_meta = {}

    text = _build_plan_summary(plan_id, plan_meta, payload)

    claimed = False

    def claim_current_switch(current: dict) -> None:
        nonlocal claimed
        current_entry = current.get(pty_id) or {}
        # The plan may change again while the bounded context is being built.
        # Claim only the exact generation we inspected. If it changed, the next
        # prompt builds and injects the newer plan instead of acknowledging it
        # with stale context.
        if (
            current_entry.get("active_plan") == plan_id
            and (current_entry.get("active_plan_changed_ts") or 0) == changed_ts
            and (current_entry.get("last_plan_inject_ts") or 0) < changed_ts
        ):
            current_entry["last_plan_inject_ts"] = changed_ts
            current[pty_id] = current_entry
            claimed = True

    try:
        _mutate_meta_store(claim_current_switch)
    except OSError as e:
        print(f"[ccdaemon] last_plan_inject_ts write failed: {e}", file=sys.stderr)
        return None
    if not claimed:
        return None

    sh.append_audit("user_prompt_submit_plan_reinject", {
        "pty_id": pty_id, "plan_id": plan_id, "changed_ts": changed_ts, "context_chars": len(text),
    })
    return text


def _bind_reminder(payload: dict) -> str | None:
    """中途"记得起来"提醒 + universal capture(对所有会话,不止 PTY 托管)。

    每轮轻量记一次 turns(顺带把会话补进台账);干了几轮还没绑 plan/task 且没提醒过 →
    只提醒一次。见 docs/plans/dashboard/[2026-07-09]SESSION-SELF-BINDING/plan.md (4.3)。
    """
    # 2026-07 总控止血「非确认不主动」: 中途注入默认静默。
    # 只有项目根 .omni/protection_policy.json 显式 "bind_reminder": true 才注入;
    # 缺省/false/读不到一律不注入。fail-open 保持: 任何异常都不挡用户输入。
    try:
        from omnicompany.packages.services._core.protection.policy import load_policy
        if not bool(load_policy().get("bind_reminder", False)):
            return None
    except Exception:
        return None
    try:
        from omnicompany.packages.services._core.identity import (
            get_session_binding,
            update_session_binding,
        )
    except Exception:
        return None
    session_id = payload.get("session_id") or payload.get("sessionId") or ""
    if not session_id and not os.environ.get("OMNI_CC_PTY_ID"):
        return None  # 无真身份, 别记成 cc_unknown 撞一条
    trace_id = sh.trace_id_for(payload)
    if not trace_id:
        return None
    cwd = payload.get("cwd") or ""
    provider = sh.binding_provider(payload)
    b = get_session_binding(trace_id)
    turns = int(b.get("turns") or 0) + 1
    try:
        # universal capture + turns 计数(只写台账, 不碰 active 指针 → 不清当前 plan)
        update_session_binding(
            trace_id,
            session_id=session_id or None,
            claude_session_id=session_id if provider == "claude_code" else None,
            provider=provider,
            cwd=cwd or None,
            turns=turns,
        )
    except Exception:
        pass
    bound = bool(b.get("active_plan") or b.get("task_id"))
    if turns < 3 or bound or b.get("reminded_bind"):
        return None
    try:
        update_session_binding(trace_id, reminded_bind=True)
    except Exception:
        pass
    sh.append_audit("user_prompt_submit_bind_reminder", {"trace_id": trace_id, "turns": turns})
    return "Durable work is still unbound; bind it once with `omni plan use <id>`."


def main() -> int:
    payload = sh.read_stdin_json()
    parts: list[str] = []
    try:
        r = _bind_reminder(payload)
    except Exception:  # noqa: BLE001 — 提醒失败绝不能挡住用户输入
        r = None
    if r:
        parts.append(r)
    p = _plan_reinject(payload)
    if p:
        parts.append(p)

    if not parts:
        return 0
    sh.emit_context("\n\n".join(parts), payload, "UserPromptSubmit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
