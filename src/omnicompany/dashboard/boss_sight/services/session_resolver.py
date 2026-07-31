# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-25T00:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.session_resolver.py"
"""把聚焦的 Claude Code / Codex 对话页签解析成具体 session_id。

权威: omnicompany/docs/plans/dashboard/[2026-06-24]MULTIAGENT-AND-REVIEW-REDESIGN/plan.md (P1)

原生插件不暴露稳定的 tab↔session 绑定,故只能启发式:
  1) 缩到 claude_code/codex + cwd 在当前 workspace 下的候选;
  2) 页签标题(tab.label)精确/包含匹配某会话的 title/current_task/preview → 高置信;
  3) 否则取最近活跃(mtime 最新)→ 单候选 medium、多候选 low。
OCR/UIA(poof)只在"低置信且多候选"时升级:它们给的不是新逻辑,而是一个**更准的 label/文本**
再喂回本函数。故本函数对"label 从哪来"无关,纯逻辑、可单测。
"""
from __future__ import annotations

import time
from typing import Any


_NATIVE_PROVIDERS = {"claude", "claude_code", "claude-code", "codex"}


def _norm(s: Any) -> str:
    return " ".join(str(s or "").lower().split())


def _norm_path(p: Any) -> str:
    return str(p or "").replace("\\", "/").rstrip("/").lower()


def _cwd_under(session_cwd: Any, workspace_cwd: Any) -> bool:
    s = _norm_path(session_cwd)
    w = _norm_path(workspace_cwd)
    if not w:
        return True  # 无 workspace 约束就不过滤
    if not s:
        return False
    return s == w or s.startswith(w + "/") or w.startswith(s + "/")


def resolve(
    tab_label: str | None,
    workspace_cwd: str | None,
    candidates: list[dict[str, Any]],
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """挑出聚焦页签最可能对应的会话。

    candidates: [{session_id, provider, cwd, mtime, title?, current_task?, preview?}, ...]
    返回 {session_id, confidence(high|medium|low), reason} 或 None(无候选)。
    """
    now = now if now is not None else time.time()  # noqa: F841 — 预留(时间衰减可后加)

    native = [
        c for c in candidates
        if str(c.get("provider") or "").lower() in _NATIVE_PROVIDERS and c.get("session_id")
    ]
    if not native:
        return None

    pool = [c for c in native if _cwd_under(c.get("cwd"), workspace_cwd)]
    if not pool:
        pool = native  # cwd 都不匹配就放开(总比啥都不返回强)

    label = _norm(tab_label)
    if label:
        # 标题字段越"像用户起的名字"越优先: title > current_task > preview
        for field in ("title", "current_task", "preview"):
            for c in pool:
                t = _norm(c.get(field))
                if t and (t == label or label in t or t in label):
                    return {"session_id": c["session_id"], "confidence": "high", "reason": f"label~{field}"}

    best = max(pool, key=lambda c: float(c.get("mtime") or 0))
    confidence = "medium" if len(pool) == 1 else "low"
    return {"session_id": best["session_id"], "confidence": confidence, "reason": "most-recent-mtime"}


def needs_escalation(result: dict[str, Any] | None) -> bool:
    """是否该升级到 UIA/OCR(低置信 = 多个同 cwd 候选靠 mtime 蒙的)。"""
    return result is not None and result.get("confidence") == "low"
