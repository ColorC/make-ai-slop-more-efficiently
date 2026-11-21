# [OMNI] origin=kimi-code domain=dashboard/controlplane ts=2026-08-13 type=route status=active
# [OMNI] summary="plan-groups 文件真源的 dashboard 只读+轻写 endpoint(M3b): /api/plan-groups/board|pin|task/archive|progress, 与 whatnow :8230 同形同口径, 供 QuestBoard 双读兜底优先消费"
# [OMNI] why="NOTES-CANVAS-CONVERGENCE M3b: QuestBoard 真源从 :8230 切 plan-groups; 本 endpoint 挂在 dashboard 进程里, 需重启 dashboard 生效(重启前前端自动回退 :8230 代理, 行为不变)"
"""plan-groups board endpoint（M3b）。

与 whatnow(:8230) 的 /api/board、/api/pin、/api/task/archive、/api/progress
同形同口径（经 lifecycle.plan_groups_store）。sync/meego|multica 不接（外部渠道
入库仍走 :8230，M4 归档 whatnow 时再议）。
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

plan_groups_board_router = APIRouter(prefix="/api/plan-groups", tags=["plan-groups"])


def _store():
    from omnicompany.packages.services._core.lifecycle import plan_groups_store as pgs

    return pgs


@plan_groups_board_router.get("/api/board")
def get_board(archived: str | None = Query(default=None)) -> JSONResponse:
    include = archived in ("1", "true", "yes")
    return JSONResponse(_store().board(include))


@plan_groups_board_router.post("/api/pin")
async def post_pin(req: Request) -> JSONResponse:
    body = await req.json()
    pgs = _store()
    try:
        result = pgs.set_pin(
            str(body.get("subject_kind") or ""),
            str(body.get("subject_id") or ""),
            bool(body.get("pinned", True)),
            str(body.get("note") or ""),
        )
    except KeyError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return JSONResponse(result)


@plan_groups_board_router.post("/api/task/archive")
async def post_task_archive(req: Request) -> JSONResponse:
    body = await req.json()
    pgs = _store()
    try:
        pgs.set_archived(str(body.get("id") or ""), bool(body.get("archived", True)))
    except KeyError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return JSONResponse({"ok": True})


@plan_groups_board_router.post("/api/progress")
async def post_progress(req: Request) -> JSONResponse:
    body = await req.json()
    pgs = _store()
    try:
        rec = pgs.append_progress(
            str(body.get("subject_kind") or "task"),
            str(body.get("subject_id") or ""),
            str(body.get("text") or ""),
            str(body.get("source") or "dashboard"),
        )
    except KeyError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    return JSONResponse({"ok": True, "id": rec.get("id")})


# ── 任务视图 (2026-08-20 用户裁定: 会话紧凑列表旁加一个任务视图) ──
# 一次返回两段: 本会话在执行的 + 总体。每条都带 material 引用与中文标题。
# 不做细碎 todo 清单 —— 总体段只出活跃计划与未立计划的会话请求, 归档的另请。

TODO_GROUP = "_todo"


def _plan_titles() -> dict[str, str]:
    """plan_id → 中文标题 (治理覆盖表 title_zh 权威)。"""
    import json as _json
    from pathlib import Path

    from omnicompany.core.config import omni_workspace_root

    out: dict[str, str] = {}
    try:
        raw = _json.loads(
            (Path(omni_workspace_root()) / "data" / "registry" / "plan_governance.json").read_text(encoding="utf-8")
        )
        for pid, entry in (raw.get("plans") or {}).items():
            if isinstance(entry, dict) and entry.get("title_zh"):
                out[str(pid)] = str(entry["title_zh"])
    except Exception:
        pass
    return out


def _active_plans() -> list[dict[str, object]]:
    """活跃 plan (frontmatter status=active) + 中文标题。"""
    import re
    from pathlib import Path

    from omnicompany.core.config import omni_workspace_root

    titles = _plan_titles()
    root = Path(omni_workspace_root()) / "docs" / "plans"
    rows: list[dict[str, object]] = []
    if not root.is_dir():
        return rows
    for md in root.rglob("plan.md"):
        rel = md.parent.relative_to(root).as_posix()
        parts = rel.split("/")
        if "_archive" in parts or "_scratch" in parts:
            continue
        try:
            head = md.read_text(encoding="utf-8", errors="replace")[:4000]
        except Exception:
            continue
        status = (re.search(r"^status:\s*(.+)$", head, re.M) or [None, ""])[1].strip().strip("'\"")
        if status != "active":
            continue
        fm_title = (re.search(r"^title:\s*(.+)$", head, re.M) or [None, ""])[1].strip().strip("'\"")
        # touched_at = 该计划目录里 plan.md / progress.md 最近一次改动。
        # "最近动过"才是"我在做的"的可靠信号; 按标题字典序排等于每天看同一批。
        touched = 0.0
        for name in ("plan.md", "progress.md"):
            f = md.parent / name
            try:
                touched = max(touched, f.stat().st_mtime)
            except OSError:
                pass
        rows.append({
            "kind": "plan",
            "plan_id": rel,
            "title": titles.get(rel) or fm_title or rel,
            "status": status,
            "material_id": f"material:plans.{rel}",
            "touched_at": touched,
        })
    rows.sort(key=lambda r: r.get("touched_at") or 0, reverse=True)
    return rows


def _todos(archived: bool) -> list[dict[str, object]]:
    pgs = _store()
    payload = pgs.load_group(TODO_GROUP) or {}
    titles = _plan_titles()
    out: list[dict[str, object]] = []
    for t in payload.get("tasks", []):
        if bool(t.get("archived")) != archived:
            continue
        legacy = t.get("legacy") or {}
        pid = t.get("plan_id")
        out.append({
            "kind": "todo",
            "id": t.get("id"),
            "title": t.get("title"),
            "status": t.get("status"),
            "plan_id": pid,
            "plan_title": titles.get(str(pid)) if pid else None,
            "material_id": legacy.get("material_id"),
            "session_trace_id": legacy.get("session_trace_id"),
            "updated_at": t.get("updated_at"),
        })
    out.sort(key=lambda r: r.get("updated_at") or 0, reverse=True)
    return out



def _plan_of_session(trace: str) -> str | None:
    """按 trace/pty id 反查该会话绑的 plan。

    真源是会话绑定台账 (get_session_binding) —— 不是 cc_sessions.json (那是已结束会话的历史,
    活会话不在里面), 也不是 cc_session_active.json (那是全局"当前活跃会话", 多会话下是别人的)。
    画布传的是 external:<pty_id> 这类带前缀 id, 这里容忍带前缀的写法。
    """
    key = trace.split(":", 1)[-1] if ":" in trace else trace
    try:
        from omnicompany.packages.services._core.identity import resolver

        binding = resolver.get_session_binding(key) or {}
    except Exception:
        return None
    plan = binding.get("active_plan")
    return str(plan) if plan else None


@plan_groups_board_router.get("/api/task-view")
def get_task_view(
    trace_id: str | None = Query(default=None, description="会话 trace_id; 缺省用当前活跃会话"),
    archived: str | None = Query(default=None, description="1 = 只看已归档"),
) -> JSONResponse:
    want_archived = archived in ("1", "true", "yes")
    if want_archived:
        return JSONResponse({"archived": _todos(True)})

    # 会话身份必须由调用方带来 —— 回落到"当前活跃会话"在多会话下会解析成别人的会话
    # (2026-08-20 实测: 不带 trace_id 时 mine=0, 带上则 mine=6)。
    trace = trace_id
    session_plan = None
    if trace:
        session_plan = _plan_of_session(trace)
    else:
        try:
            from omnicompany.packages.services._core.identity import current_session_meta

            meta = current_session_meta() or {}
            trace = meta.get("trace_id")
            session_plan = meta.get("active_plan")
        except Exception:
            pass

    todos = _todos(False)
    plans = _active_plans()
    by_id = {str(p["plan_id"]): p for p in plans}

    mine: list[dict[str, object]] = []
    if session_plan and str(session_plan) in by_id:
        mine.append({**by_id[str(session_plan)], "reason": "本会话绑定的计划"})
    if trace:
        mine.extend(t for t in todos if t.get("session_trace_id") == trace)

    return JSONResponse({
        "trace_id": trace,
        "session_plan": session_plan,
        "mine": mine,
        "overall": {"plans": plans, "todos": [t for t in todos if not t.get("plan_id")]},
        "counts": {
            "mine": len(mine),
            "plans": len(plans),
            "todos": len(todos),
            "archived": len(_todos(True)),
        },
    })
