# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="暖会话执行器: 一个 ClaudeSDKClient 连一次, 对 plan 的耦合 task 在同会话里逐个 query(上下文累积不重探索), 抄 task-master/vibe-kanban 暖会话模型; 顺带量 token"
# [OMNI] why="WORK-REPORT 反思: 之前每 task 冷启会话重新探索代码=浪费; 业界(task-master/BMAD/Cline)是细粒度追踪+暖会话执行"
# [OMNI] tags=lifecycle,warm-session,resume,token,dispatch
# [OMNI] material_id="material:services._core.lifecycle.warm_runner.py"
"""暖会话执行器 —— 同一会话顺序走完耦合 task(不每个冷启重探索)。

业界共识(task-master / vibe-kanban / BMAD / Cline 一致):**会话边界按独立性划, 不按 task 数划**;
耦合的、线性接力的 task 应在**一个暖会话**里做完, 上下文累积、代码只探索一次、验证合并。

实现: 一个 `ClaudeSDKClient` connect 一次 → 对每个 ready task `client.query()`(同会话, 不重置 session_id,
对话历史累积)→ 收集 final_text + 改动文件 + **token usage** → 编排端推进状态 + 提物料 → 下一个。
复用 external_workers.claude_code 的权限/can_use_tool/diff/事件解析内部件, 不另造一套 SDK 封装。
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

from omnicompany.packages.services._core.lifecycle.dispatch_task import (
    compose_task_prompt,
    submit_run_material,
)
from omnicompany.packages.services._core.lifecycle.task import TaskStore


def _cc():
    from omnicompany.packages.services._core.agent.external_workers import claude_code as cc
    return cc


def _git_changed(cwd: str) -> set[str]:
    try:
        return set(_cc()._git_changed_files(cwd))
    except Exception:
        return set()


def _warm_prompt(task: Any, plan_id: str, first: bool) -> str:
    base = compose_task_prompt(task, plan_id)
    if first:
        return base
    # 续接提示: 告诉 agent 同会话继续, 别重新通读代码(这是省时间的关键)
    return (
        "[同一会话继续] 你已在本仓做完上一个 task, 已读过的文件/已建立的代码理解都还在上下文里, "
        "**不要重新从头通读或大范围 grep**, 直接做下面这个 task(只在确需时看具体文件):\n\n" + base
    )


def _finish_task(store: TaskStore, plan_id: str, task: Any, ok: bool,
                 final_text: str, changed: list[str], hold_at_review: bool) -> dict[str, Any]:
    material_id = None
    if ok:
        try:
            store.set_status(task.id, "review", plan_id)
        except Exception:
            pass
        sub = submit_run_material(task, plan_id, final_text=final_text, changed_files=changed)
        material_id = sub.get("material_id")
        if not hold_at_review:
            try:
                store.set_status(task.id, "done", plan_id)
            except Exception:
                try:
                    store.update(task.id, plan_id=plan_id, status="done")
                except Exception:
                    pass
    else:
        try:
            store.set_status(task.id, "blocked", plan_id)
        except Exception:
            pass
    return {"task_id": task.id, "title": task.title, "ok": ok, "material_id": material_id,
            "status": "succeeded" if ok else "failed", "changed_files": changed,
            "summary": f"{'✓' if ok else '✗'} task[{task.id}] 暖会话 (changed={len(changed)}, material={material_id or '-'})"}


async def _warm_loop(plan_id: str, *, cwd: str, max_steps: int | None, until_task: str | None,
                     hold_at_review: bool, keep_going: bool,
                     emit: Callable[[dict], None]) -> dict[str, Any]:
    cc = _cc()
    casdk = cc._import_claude_agent_sdk()
    from omnicompany.packages.services._core.agent.external_workers.base import (
        ExternalAgentPermissionMode,
    )
    opts = casdk.ClaudeAgentOptions(
        system_prompt={"type": "preset", "preset": "claude_code"},
        tools={"type": "preset", "preset": "claude_code"},
        setting_sources=[],
        permission_mode=cc._CLAUDE_PERMISSION_BY_MODE[ExternalAgentPermissionMode.WORKSPACE_WRITE],
        cwd=str(cwd),
        can_use_tool=cc._workspace_write_can_use_tool,
        disallowed_tools=list(cc._HEADLESS_INCOMPATIBLE_TOOLS),
    )
    store = TaskStore()
    client = casdk.ClaudeSDKClient(options=opts)
    await client.connect()
    dispatched: list[dict] = []
    steps = 0
    stopped = "completed"
    prev_id: str | None = None
    stuck = 0
    first = True
    tok_in = tok_out = 0
    session_id: str | None = None
    try:
        while True:
            if max_steps is not None and steps >= max_steps:
                stopped = "max_steps"; break
            task = store.next_task(plan_id)
            if task is None:
                stopped = "no_ready_task"; break
            if task.id == prev_id:
                stuck += 1
                if stuck >= 2:
                    stopped = "stuck_no_progress"; emit({"event": "stuck", "task_id": task.id}); break
            else:
                stuck = 0
            prev_id = task.id
            steps += 1
            emit({"event": "task_start", "task_id": task.id, "title": task.title, "step": steps})

            before = _git_changed(cwd)
            await client.query(_warm_prompt(task, plan_id, first))  # 同会话续接(不重置 session)
            first = False
            final_parts: list[str] = []
            ok = True
            async for msg in client.receive_response():
                ev = cc._message_to_event(msg)
                if ev.type == "assistant.text" and ev.message:
                    final_parts.append(ev.message)
                if ev.type == "result":
                    pl = ev.payload or {}
                    u = pl.get("usage") or {}
                    tok_in += int(u.get("input_tokens") or 0)
                    tok_out += int(u.get("output_tokens") or 0)
                    if pl.get("session_id"):
                        session_id = pl.get("session_id")
                    if cc._result_event_error(pl):
                        ok = False
                    if pl.get("result"):
                        final_parts.append(str(pl.get("result")))
            final_text = cc._dedupe_text_parts(final_parts)
            changed = sorted(_git_changed(cwd) - before)
            rec = _finish_task(store, plan_id, task, ok, final_text, changed, hold_at_review)
            dispatched.append(rec)
            emit({"event": "task_done", **rec})

            if not ok and not keep_going:
                stopped = "task_failed"; break
            if hold_at_review:
                stopped = "hold_at_review"; break
            if until_task and task.id == until_task:
                stopped = "reached_until_task"; break
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    return {
        "dispatched": dispatched, "steps": steps, "stopped_reason": stopped,
        "session_id": session_id,
        "tokens": {"input": tok_in, "output": tok_out, "total": tok_in + tok_out},
    }


def run_tasks_warm(plan_id: str, *, cwd: str | None = None, max_steps: int | None = None,
                   until_task: str | None = None, hold_at_review: bool = False,
                   keep_going: bool = False,
                   emit: Callable[[dict], None] | None = None) -> dict[str, Any]:
    """同步入口: 在一个暖会话里跑完 plan 的 ready task 序列。"""
    from omnicompany.core.config import omni_workspace_root
    cwd = cwd or str(omni_workspace_root())

    def _emit(ev: dict) -> None:
        if emit:
            try:
                emit(ev)
            except Exception:
                pass

    return asyncio.run(_warm_loop(
        plan_id, cwd=cwd, max_steps=max_steps, until_task=until_task,
        hold_at_review=hold_at_review, keep_going=keep_going, emit=_emit,
    ))


__all__ = ["run_tasks_warm"]
