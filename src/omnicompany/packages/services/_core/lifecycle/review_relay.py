# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="审阅意见回流桥: 用户带反馈的驳回/批注→定位 material 源 plan/task→注入回原会话继续执行; 修当前断掉的回路"
# [OMNI] why="WORK-LIFECYCLE-AND-DISPATCH N-review-loop: omni review push/驳回的意见到不了原 agent 会话, 补这座桥"
# [OMNI] tags=lifecycle,review,feedback,relay,human-in-the-loop
# [OMNI] material_id="material:services._core.lifecycle.review_relay.py"
"""审阅意见回流桥。

断点(grounding 实测): reviewstage 的 verdict/push 只 _notify 本地 + 推前端, **到不了原 agent 会话**。
本桥补回路: material(带 source_plan_id) → 查 task 绑定找 agent → 注入反馈消息 → 会话据此继续。
驳回带反馈(非 yes/no), 学 HumanLayer; 注入复用 dispatch_task.inject_to_task(winjump, 不弹窗)。
"""
from __future__ import annotations

import time
from typing import Any

from omnicompany.packages.services._core.lifecycle.dispatch_task import (
    _load_bindings,
    inject_to_task,
)


def _material_source(material_id: str) -> dict[str, Any]:
    """best-effort 从 reviewstage 拿 material 的 source_plan_id / source_subagent_id。"""
    try:
        from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store
        m = get_store().get(material_id)
        if m is None:
            return {}
        return {
            "source_plan_id": getattr(m, "source_plan_id", None),
            "source_subagent_id": getattr(m, "source_subagent_id", None),
            "title": getattr(m, "title", None),
            "status": str(getattr(getattr(m, "status", ""), "value", getattr(m, "status", "")) or ""),
        }
    except Exception:
        return {}


def _find_task_for(plan_id: str | None, task_id: str | None) -> dict[str, Any] | None:
    """定位回流目标的 task 绑定。优先 task_id; 否则取该 plan 下最近投递的 task。"""
    binds = _load_bindings()
    if task_id and task_id in binds:
        return binds[task_id]
    if plan_id:
        cand = [b for b in binds.values() if b.get("plan_id") == plan_id]
        if cand:
            cand.sort(key=lambda b: b.get("bound_at", 0), reverse=True)
            return cand[0]
    return None


def relay_feedback(material_id: str, reason: str, *, plan_id: str | None = None,
                   task_id: str | None = None, verdict: str = "rejected",
                   inject: bool = True) -> dict[str, Any]:
    """把审阅意见回流到原会话。返回 {ok, target_task, injected, summary}。"""
    src = _material_source(material_id)
    pid = plan_id or src.get("source_plan_id")
    binding = _find_task_for(pid, task_id)
    if not binding:
        return {
            "ok": False, "material_id": material_id, "plan_id": pid,
            "summary": f"找不到 material {material_id} 对应的在跑 task 绑定 "
                       f"(plan={pid}); 先 omni task dispatch 把 task 投给 agent。",
        }
    tid = binding.get("task_id")
    msg = (
        f"[审阅回流] material {material_id} 被{'驳回' if verdict == 'rejected' else '批注'}。\n"
        f"意见: {reason}\n"
        f"请据此修改, 改完重新 `omni review submit --plan-id {pid} ...` 推审阅, "
        f"勿跳过。(task #{tid})"
    )
    injected = False
    inj_summary = "(未注入)"
    if inject:
        r = inject_to_task(tid, msg, plan_id=pid)
        injected = bool(r.get("ok"))
        inj_summary = r.get("summary", "")

    # best-effort 发事件, 给看板/审计留痕
    try:
        from omnicompany.packages.services._core.omnicompany.material_events import (
            publish_material_event,
        )
        publish_material_event(
            "material.feedback_relayed",
            {"material_id": material_id, "plan_id": pid, "task_id": tid,
             "verdict": verdict, "reason": reason, "ts": time.time()},
        )
    except Exception:
        pass

    return {
        "ok": injected or not inject,
        "material_id": material_id, "plan_id": pid, "task_id": tid,
        "agent": binding.get("agent"), "injected": injected,
        "summary": f"✓ 审阅意见回流 → task#{tid} @{binding.get('agent')}: {inj_summary}",
    }


__all__ = ["relay_feedback"]
