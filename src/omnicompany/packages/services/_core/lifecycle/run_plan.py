# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="plan 执行驱动: 过门→循环(next→dispatch→agent自主完成→自动提物料→推进)→持续跑完 OR 步进到某步; 给 CLI/网页/agent 共用一个入口"
# [OMNI] why="WORK-LIFECYCLE 用户诉求: 网页能启动plan执行, agent用CLI也能启动+观察, 可一次性持续完整完成或拆成task逐个到某步"
# [OMNI] tags=lifecycle,plan,run,driver,continuous,stepped,autonomous
# [OMNI] material_id="material:services._core.lifecycle.run_plan.py"
"""plan 执行驱动 —— 一个入口, CLI / 网页 / agent 共用。

模式:
- 持续(continuous): 跑到没有 ready task 为止(一次性完整完成)。
- 步进(max_steps=N): 只完成 N 个 task 后停(拆成 task 一个个到某步)。
- 到某步(until_task): 跑到指定 task 完成后停。

每步: 取 next_task → dispatch(默认 sdk headless, 安全不抢焦点) → agent 自主完成 →
编排端自动提交物料审阅 → (默认)推进 review→done 解锁后继。失败默认停, 留人工/外部兜底。
"""
from __future__ import annotations

import time
from typing import Any, Callable

from omnicompany.packages.services._core.lifecycle.dispatch_task import dispatch_task
from omnicompany.packages.services._core.lifecycle.task import DONE_STATUS, TaskStore
from omnicompany.packages.services._core.plan_audit.gate import check_plan_dispatch_gate


def run_plan(
    plan_id: str,
    *,
    carrier: str = "sdk",
    cwd: str | None = None,
    max_steps: int | None = None,
    until_task: str | None = None,
    hold_at_review: bool = False,
    keep_going_on_fail: bool = False,
    override_gate: bool = False,
    session_mode: str = "warm",
    parallel: int = 1,
    auto_report: bool = True,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """驱动一个 plan 的 task 渐进执行。返回 run 报告。

    Args:
        carrier: sdk(headless, 持续跑默认) / vscode(真实会话, 交互)。
        max_steps: 步进上限(None=持续跑完)。
        until_task: 跑到这个 task 完成后停。
        hold_at_review: 成功后停在 review 不自动 done(每个 task 之间留人工验收闸)。
        keep_going_on_fail: 某 task 失败后继续下一个(默认停)。
        override_gate: 旁路完成度硬门(默认硬阻断)。
        on_event: 进度回调(网页/观测用)。
    """
    def emit(ev: dict[str, Any]) -> None:
        if on_event:
            try:
                on_event(ev)
            except Exception:
                pass

    gate = check_plan_dispatch_gate(plan_id)
    if not gate["ok"] and not override_gate:
        return {"ok": False, "plan_id": plan_id, "stopped_reason": "gate_blocked",
                "gate": gate, "dispatched": [],
                "summary": f"❌ 完成度硬门未过, 拒绝执行: {gate['summary']}"}

    store = TaskStore()
    use_parallel = parallel and parallel > 1 and carrier == "sdk"
    is_warm = (not use_parallel) and carrier == "sdk" and session_mode == "warm"
    mode_label = f"parallel={parallel}" if use_parallel else ("warm" if is_warm else "cold")
    emit({"event": "run_start", "plan_id": plan_id, "carrier": carrier,
          "session_mode": mode_label,
          "mode": "continuous" if max_steps is None else f"steps={max_steps}"})

    tokens: dict[str, int] | None = None
    session_id: str | None = None
    if use_parallel:
        # 并行档: 文件范围不重叠的 ready task 同时跑(每个自己的会话), 几条管线一起推进
        dispatched, steps, stopped_reason = _run_parallel(
            plan_id, store, n=parallel, cwd=cwd, max_steps=max_steps,
            hold_at_review=hold_at_review, keep_going_on_fail=keep_going_on_fail, emit=emit,
        )
    elif is_warm:
        # 暖会话: 耦合 task 在一个 SDK 会话里接力做完(上下文累积, 不每个冷启重探索)
        from omnicompany.packages.services._core.lifecycle.warm_runner import run_tasks_warm
        warm = run_tasks_warm(
            plan_id, cwd=cwd, max_steps=max_steps, until_task=until_task,
            hold_at_review=hold_at_review, keep_going=keep_going_on_fail, emit=emit,
        )
        dispatched = warm["dispatched"]
        steps = warm["steps"]
        stopped_reason = warm["stopped_reason"]
        tokens = warm.get("tokens")
        session_id = warm.get("session_id")
    else:
        dispatched, steps, stopped_reason = _run_cold(
            plan_id, store, carrier=carrier, cwd=cwd, max_steps=max_steps,
            until_task=until_task, hold_at_review=hold_at_review,
            keep_going_on_fail=keep_going_on_fail, emit=emit,
        )

    remaining = [t.id for t in store.list_tasks(plan_id) if t.status in {"pending", "in_progress"}]
    report = {
        "ok": stopped_reason in {"completed", "no_ready_task", "max_steps",
                                 "reached_until_task", "hold_at_review"},
        "plan_id": plan_id, "carrier": carrier,
        "session_mode": mode_label,
        "steps": steps, "stopped_reason": stopped_reason,
        "dispatched": dispatched, "remaining_tasks": remaining,
        "tokens": tokens, "session_id": session_id,
        "summary": f"plan {plan_id}: 执行 {steps} 个 task, 停因={stopped_reason}, "
                   f"剩 {len(remaining)} 个未完{('; tokens=' + str(tokens.get('total'))) if tokens else ''}。",
        "ts": time.time(),
    }
    # 自动产工作报告 + 推审阅台。只对**真实、非 scratch 的 plan** 提交, 否则测试/试验会污染审阅库。
    from omnicompany.core.plans_catalogue import _plans_root as _pr
    is_real_plan = (not plan_id.startswith("_scratch/")) and (_pr() / plan_id / "plan.md").is_file()
    if auto_report and is_real_plan:
        try:
            from omnicompany.packages.services._core.lifecycle.work_report import (
                build_work_report,
                submit_versioned_report,
            )
            wr = build_work_report(plan_id, run_report=report)
            report["work_report_path"] = wr.get("path")
            # 版本化提交: 同 plan 旧报告归档 + 记成历史版本, 待审里只留最新一份(不再堆积)
            sub = submit_versioned_report(plan_id, wr.get("markdown", ""),
                                          title=f"工作报告: {plan_id}")
            report["work_report_material_id"] = sub.get("material_id")
            report["work_report_version"] = sub.get("version")
        except Exception:
            pass
    emit({"event": "run_end", **{k: report[k] for k in ("steps", "stopped_reason", "remaining_tasks")}})
    return report


def _run_cold(plan_id: str, store: TaskStore, *, carrier: str, cwd: str | None,
              max_steps: int | None, until_task: str | None, hold_at_review: bool,
              keep_going_on_fail: bool, emit: Callable[[dict[str, Any]], None]):
    """冷档: 每个 task 一个独立(冷启)会话。耦合 task 用此档会重复探索, 仅适合真独立/隔离需求。"""
    dispatched: list[dict[str, Any]] = []
    steps = 0
    stopped_reason = "completed"
    prev_id: str | None = None
    stuck = 0
    while True:
        if max_steps is not None and steps >= max_steps:
            stopped_reason = "max_steps"
            break
        nxt = store.next_task(plan_id)
        if nxt is None:
            stopped_reason = "no_ready_task"
            break
        if nxt.id == prev_id:
            stuck += 1
            if stuck >= 2:
                stopped_reason = "stuck_no_progress"
                emit({"event": "stuck", "task_id": nxt.id})
                break
        else:
            stuck = 0
        prev_id = nxt.id

        steps += 1
        emit({"event": "task_start", "task_id": nxt.id, "title": nxt.title, "step": steps})
        res = dispatch_task(nxt.id, plan_id=plan_id, carrier=carrier, cwd=cwd)
        ok = bool(res.get("ok"))
        rec = {"task_id": nxt.id, "title": nxt.title, "ok": ok,
               "material_id": res.get("material_id"), "status": res.get("status"),
               "summary": res.get("summary")}
        dispatched.append(rec)
        emit({"event": "task_done", **rec})

        if ok:
            if not hold_at_review:
                try:
                    store.set_status(nxt.id, "done", plan_id)
                except Exception:
                    try:
                        store.update(nxt.id, plan_id=plan_id, status="done")
                    except Exception:
                        pass
            else:
                stopped_reason = "hold_at_review"
                break
        else:
            try:
                store.set_status(nxt.id, "blocked", plan_id)
            except Exception:
                pass
            if not keep_going_on_fail:
                stopped_reason = "task_failed"
                break

        if until_task and nxt.id == until_task:
            stopped_reason = "reached_until_task"
            break
    return dispatched, steps, stopped_reason


def _pick_parallel_batch(store: TaskStore, plan_id: str, n: int) -> list:
    """挑一批能**同时跑**的 ready task: 依赖已满足 + 文件范围两两不重叠(不会改同一文件)。

    无 file_scope 的 task = 范围不明 = 不与任何并行(单独成批跑), 安全优先。
    """
    tasks = store.list_tasks(plan_id)
    done = {t.id for t in tasks if t.status in DONE_STATUS}
    ready = [t for t in tasks if t.status == "pending" and all(d in done for d in t.dependencies)]
    prio = {"high": 0, "medium": 1, "low": 2}
    ready.sort(key=lambda t: (prio.get(t.priority, 1), -(t.workload or 0),
                              int(t.id) if t.id.isdigit() else 1_000_000))
    batch: list = []
    used_scope: set[str] = set()
    for t in ready:
        sc = set(t.file_scope or [])
        if not sc:  # 范围不明: 不并行, 若批空就单跑它, 否则留下轮
            if not batch:
                return [t]
            continue
        if sc & used_scope:  # 与已选 task 文件重叠 → 不能同时跑, 下轮再说
            continue
        batch.append(t)
        used_scope |= sc
        if len(batch) >= n:
            break
    return batch


def _run_parallel(plan_id: str, store: TaskStore, *, n: int, cwd: str | None,
                  max_steps: int | None, hold_at_review: bool, keep_going_on_fail: bool,
                  emit: Callable[[dict[str, Any]], None]):
    """并行档: 每轮挑一批文件范围不重叠的 ready task, 各起自己的会话同时跑。

    安全靠 file_scope 不重叠(同一文件不会被两个会话同时改)。每个 task = 一次 sdk dispatch。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    dispatched: list[dict[str, Any]] = []
    steps = 0
    stopped_reason = "completed"
    prev_round: tuple[str, ...] | None = None
    stuck = 0
    while True:
        if max_steps is not None and steps >= max_steps:
            stopped_reason = "max_steps"
            break
        batch = _pick_parallel_batch(store, plan_id, n)
        if not batch:
            stopped_reason = "no_ready_task"
            break
        ids = tuple(sorted(t.id for t in batch))
        if ids == prev_round:
            stuck += 1
            if stuck >= 2:
                stopped_reason = "stuck_no_progress"
                emit({"event": "stuck", "tasks": list(ids)})
                break
        else:
            stuck = 0
        prev_round = ids
        emit({"event": "round_start", "tasks": list(ids), "parallel": len(batch)})

        round_results: list[tuple[Any, bool, dict]] = []
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            futs = {ex.submit(dispatch_task, t.id, plan_id=plan_id, carrier="sdk", cwd=cwd): t
                    for t in batch}
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001
                    res = {"ok": False, "summary": f"dispatch 异常: {e}"}
                ok = bool(res.get("ok"))
                steps += 1
                rec = {"task_id": t.id, "title": t.title, "ok": ok,
                       "material_id": res.get("material_id"), "status": res.get("status"),
                       "summary": res.get("summary")}
                dispatched.append(rec)
                round_results.append((t, ok, res))
                emit({"event": "task_done", **rec})

        any_fail = False
        for t, ok, _res in round_results:
            if ok and not hold_at_review:
                try:
                    store.set_status(t.id, "done", plan_id)
                except Exception:
                    try:
                        store.update(t.id, plan_id=plan_id, status="done")
                    except Exception:
                        pass
            elif not ok:
                any_fail = True
                try:
                    store.set_status(t.id, "blocked", plan_id)
                except Exception:
                    pass
        if hold_at_review:
            stopped_reason = "hold_at_review"
            break
        if any_fail and not keep_going_on_fail:
            stopped_reason = "task_failed"
            break
    return dispatched, steps, stopped_reason


__all__ = ["run_plan"]
