# [OMNI] origin=claude-code domain=services/_focus ts=2026-06-27T00:00:00Z type=runner
# [OMNI] material_id="material:services._focus.plan_progress_recorder.sync.py"
"""plan-progress 全量同步驱动器 —— 让"新计划自动纳管 + 进度不及时就刷新"成为定时能力。

口径(对齐 poof-note「批量与定时的语义空间健康治理·方面一」):
  进度只在 whatnow(:8230)这一处管。本驱动器遍历 docs/plans 下所有活跃计划:
    - 在 whatnow 查无对应 task 的 → 新计划, 自动建 task(归属由 plan_id 顶段映射到北极星, 兜底「新计划待归类」)并评估进度。
    - 已纳管但计划文件比 task 上次更新还新的 → 进度可能过时, 重新评估刷新。
    - 已纳管且未变动的 → 跳过(省 LLM)。
  每条计划走统一的 plan-progress-recorder(engine=event)做评估+落地, 不另搭。

用法:  omni governance plans-sync [--all] [--limit N] [--dry-run]
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable

from omnicompany.core.config import omni_workspace_root

WHATNOW = "http://127.0.0.1:8230"
TRIAGE_GOAL = "uncat-plans"  # 兜底归属: 新计划待归类

# 这些计划以「目标(goal)」而非 task 形态在 whatnow 里跟踪(北极星宪章), 别再给它们建 task。
_SKIP_PLANS = {"[2026-06-25]SEMANTIC-OS"}

# 顶段映射不够时, 个别计划显式归到某北极星(优先于 _SEG_TO_GOAL)。如 SEMANTIC-OS 的执行件 child。
# 2026-07-02 用户定调: 语义OS 是 omnicompany 的新定位, 两份计划是它下面的两大任务——
# 直接挂 semantic-os, 不再经中转 goal(semantic-space-buildout 已并入 semantic-os 并从数据里移除)。
_PLAN_TO_GOAL = {
    "omnicompany-governance/[2026-06-27]SEMANTIC-SPACE-HEALTH": "semantic-os",
    "format-material/[2026-06-27]SEMANTIC-FILESYSTEM-ALL-MATERIAL": "semantic-os",
}

# plan_id 顶段 → whatnow 北极星 goal_id(最佳努力; 查不到落 TRIAGE_GOAL, 由人/总控再归位)
_SEG_TO_GOAL = {
    "agent-framework": "omni-autopilot", "agent-orchestration": "omni-autopilot",
    "diagnosis": "omni-autopilot", "format-material": "omni-autopilot",
    "guardian": "omni-autopilot", "productization": "omni-autopilot",
    "dashboard": "aios-operating-layer", "omnidashboard-os": "aios-operating-layer",
    "remote-client": "aios-operating-layer", "universal-entry": "aios-operating-layer",
    "inbox": "aios-operating-layer",
    "omnicompany-调研吸收": "omni-true-autopilot", "reasoning-network": "omni-true-autopilot",
    "narrative": "auto-narrative", "vilo": "vilo", "walker": "walker", "voxelcraft": "mc-world",
}


def _get(path: str) -> dict:
    with urllib.request.urlopen(WHATNOW + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _post(path: str, body: dict) -> dict:
    req = urllib.request.Request(WHATNOW + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _discover_plans(plans_root: Path) -> list[str]:
    """docs/plans 下所有含 plan.md 的活跃计划(排除 _archive / _scratch)。"""
    out: list[str] = []
    for dp, _dn, fn in os.walk(plans_root):
        rel = os.path.relpath(dp, plans_root).replace("\\", "/")
        if rel == ".":
            continue
        low = "/" + rel.lower() + "/"
        if "/_archive/" in low or "/_scratch/" in low or low.startswith("/_archive") or low.startswith("/_scratch"):
            continue
        if any(f.lower() == "plan.md" for f in fn) and rel not in _SKIP_PLANS:
            out.append(rel)
    return sorted(out)


def _plan_mtime(plans_root: Path, plan_id: str) -> float:
    """计划目录里文档的最新修改时间(秒)。"""
    d = plans_root / plan_id
    latest = 0.0
    for f in d.rglob("*.md"):
        try:
            latest = max(latest, f.stat().st_mtime)
        except OSError:
            pass
    return latest


def _board_index(board: dict) -> dict[str, dict]:
    """plan_id → task(取 goal_id / updated_at, 含已归档)。"""
    idx: dict[str, dict] = {}
    for c in board.get("clusters", []):
        for g in c.get("goals", []):
            for t in g.get("tasks", []):
                pid = (t.get("plan_id") or "").strip()
                if pid:
                    idx[pid] = t
    return idx


def _goal_for(plan_id: str) -> str:
    return _PLAN_TO_GOAL.get(plan_id) or _SEG_TO_GOAL.get(plan_id.split("/")[0], TRIAGE_GOAL)


def _ensure_triage_goal(board: dict) -> None:
    if any(g.get("id") == TRIAGE_GOAL for c in board.get("clusters", []) for g in c.get("goals", [])):
        return
    _post("/api/goals", {
        "id": TRIAGE_GOAL, "cluster_id": "omnicompany", "title": "新计划待归类",
        "kind": "里程碑", "line": "side", "status": "active",
        "objective": "plans-sync 自动纳管但还没判定归属哪条北极星的新计划, 在这里暂存待人/总控再归位。",
        "source": "manual", "ord": 99,
    })


async def sync_all(*, only_changed: bool = True, limit: int | None = None,
                   dry_run: bool = False, echo: Callable[[str], None] = print) -> dict[str, Any]:
    plans_root = Path(omni_workspace_root()) / "docs" / "plans"
    try:
        board = _get("/api/board?archived=1")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"whatnow 不可达(:8230): {e}"}
    if not dry_run:
        _ensure_triage_goal(board)
    idx = _board_index(board)
    plans = _discover_plans(plans_root)

    todo: list[tuple[str, str, str]] = []  # (plan_id, goal_id, reason)
    skipped = 0
    for pid in plans:
        task = idx.get(pid)
        if task is None:
            todo.append((pid, _goal_for(pid), "new"))
            continue
        if not only_changed:
            todo.append((pid, (task.get("goal_id") or _goal_for(pid)), "forced"))
            continue
        mt = _plan_mtime(plans_root, pid)
        upd = (task.get("updated_at") or 0) / 1000.0
        if mt > upd + 1:  # 计划文档比 task 上次更新还新 → 进度可能过时
            todo.append((pid, (task.get("goal_id") or _goal_for(pid)), "stale"))
        else:
            skipped += 1
    if limit:
        todo = todo[:limit]

    echo(f"[plans-sync] 活跃计划 {len(plans)} | 已最新跳过 {skipped} | 待处理 {len(todo)}"
         f"{' (DRY-RUN)' if dry_run else ''}")
    results: list[dict] = []
    if dry_run:
        for pid, goal, why in todo:
            echo(f"  - {why:6} {goal:20} {pid}")
        return {"ok": True, "active": len(plans), "skipped": skipped,
                "todo": [{"plan_id": p, "goal_id": g, "reason": w} for p, g, w in todo]}

    from omnicompany.core.dispatch import dispatch
    from omnicompany.core.registry import discover
    discover()  # 填充注册表, 否则 dispatch 找不到 plan-progress-recorder(event team)
    created = updated = failed = 0
    for pid, goal, why in todo:
        try:
            res = await dispatch("plan-progress-recorder", {"plan_id": pid, "goal_id": goal})
            rec = _extract_recorded(res)
            if rec.get("created"):
                created += 1
                _write_binding_skeleton(pid, rec.get("task_id"), echo)
            elif rec.get("recorded"):
                updated += 1
            else:
                failed += 1
            echo(f"  [{why}] {pid} -> {rec.get('note', rec)}")
            results.append({"plan_id": pid, **rec})
        except Exception as e:  # noqa: BLE001
            failed += 1
            echo(f"  [FAIL] {pid}: {e}")
            results.append({"plan_id": pid, "error": str(e)})
    summary = {"ok": True, "active": len(plans), "skipped": skipped,
               "created": created, "updated": updated, "failed": failed}
    echo(f"[plans-sync] 新建 {created} | 刷新 {updated} | 失败 {failed}")
    return {**summary, "results": results}


def _write_binding_skeleton(plan_id: str, whatnow_task: str | None, echo: Callable[[str], None]) -> None:
    """新计划纳管成功后顺手写一条绑定注册表骨架登记(不阻断主流程, 失败只记日志)。

    仅在"新建"(created)分支调用一次; "stale"(仅刷新进度)分支不重复调用,
    与 write_skeleton() 自身的幂等契约呼应, 也省一次 IO。
    """
    try:
        from omnicompany.packages.services._core.registry import plan_bindings
        plan_bindings.write_skeleton(plan_id, whatnow_task=whatnow_task)
    except Exception as e:  # noqa: BLE001 — 骨架登记失败不能拖垮计划纳管
        echo(f"  [WARN] {plan_id} 绑定骨架登记失败(不影响纳管): {e}")


def _extract_recorded(res: Any) -> dict:
    """从 event 管线返回 {"sinks":[payload...], "events":[...]} 里取 planprog.recorded 的纯 dict payload。

    只取 sinks(纯 dict), 绝不返回 events(FactoryEvent 不可 JSON 序列化)。"""
    try:
        if isinstance(res, dict):
            sinks = res.get("sinks") or []
            for s in sinks:
                if isinstance(s, dict) and ("recorded" in s or "task_id" in s or "note" in s):
                    return {k: v for k, v in s.items() if isinstance(v, (str, int, float, bool, type(None)))}
            if sinks and isinstance(sinks[0], dict):
                return {k: v for k, v in sinks[0].items()
                        if isinstance(v, (str, int, float, bool, type(None)))}
    except Exception:  # noqa: BLE001
        pass
    return {"recorded": True}


def run_sync(only_changed: bool = True, limit: int | None = None, dry_run: bool = False,
             echo: Callable[[str], None] = print) -> dict[str, Any]:
    return asyncio.run(sync_all(only_changed=only_changed, limit=limit, dry_run=dry_run, echo=echo))
