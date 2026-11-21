# [OMNI] origin=claude-code domain=dashboard/controlplane ts=2026-08-21T00:00:00+08:00 type=route status=active
# [OMNI] summary="工作室集群端点: 项目聚合 → 会话(provider/进行中/主从) + 任务线 → todo 四层; 另给不再活跃任务线与 provider 分色板。"
# [OMNI] why="用户 2026-08-21 指认会话栏新视图: 以项目分类会话抽屉, 会话标题=当前进行中的 todo, 唤起关系视作主从, 额外入口列未归档但不再活跃的任务线。"
# [OMNI] material_id="material:dashboard.controlplane.studio_cluster"
"""工作室集群（studio cluster）聚合端点。

判据是用户点出的六轴：项目聚合 / 任务线聚合 / 任务感知 / 信息层级 / 多 agent 关系聚合 / provider 表现。

数据来源（全部复用现成真源，不另造存储）：
- 活会话：ccdaemon 的 `/cc/sessions`（字段 alive / working / provider / active_plan / project）
- 任务线：`lifecycle/taskline_store`
- todo：plan-groups 的 `_todo` 组
- 计划中文标题：`data/registry/plan_governance.json` 的 title_zh
- 项目中文名：`data/registry/projects.json`
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

studio_cluster_router = APIRouter(prefix="/api/studio", tags=["studio-cluster"])

TODO_GROUP = "_todo"

# provider 分色：用户明确「各个 icon 用不同颜色，而不是用 provider 原来的 icon 颜色」。
# 色板从蓝图主题的冷底+暖点派生，彼此可辨，不取任何品牌色。
PROVIDER_COLORS: dict[str, str] = {
    # 取值全部来自 frostpane 蓝图色板 (--fp-bp-* / --fp-link / --fp-violet / --fp-accent-2),
    # 彼此可辨且与图纸底一致; 不取任何 provider 品牌色。
    "claude_code": "#d3ac62",   # brass-hi
    "claude_2": "#c98f74",      # brass-hi 偏暖的第二档
    "codex": "#6fa8ff",         # link
    "codex_2": "#5ad0e0",       # accent-2
    "codex_the_company": "#a78bfa",  # violet
    "codex_glm": "#7fd4a8",     # ok
    "kimi": "#e2c05a",          # warn
    "kiro": "#e0685f",          # err
    "grok": "#8fa4c8",          # stamp-idle
    "zcode": "#b08d3e",         # brass
    "opencode": "#a3bce0",      # text-2
}
PROVIDER_FALLBACK = "#8b97a4"


def _root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return Path(omni_workspace_root())


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _plan_titles() -> dict[str, str]:
    raw = _read_json(_root() / "data" / "registry" / "plan_governance.json") or {}
    out: dict[str, str] = {}
    for pid, entry in (raw.get("plans") or {}).items():
        if isinstance(entry, dict) and entry.get("title_zh"):
            out[str(pid)] = str(entry["title_zh"])
    return out


def _project_names() -> dict[str, str]:
    raw = _read_json(_root() / "data" / "registry" / "projects.json") or []
    items = raw if isinstance(raw, list) else (raw.get("projects") or raw)
    if isinstance(items, dict):
        items = list(items.values())
    out: dict[str, str] = {}
    for p in items or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("project_id") or p.get("id") or "")
        if not pid:
            continue
        out[pid] = str(p.get("short") or p.get("name") or p.get("title") or pid)
    return out


# 活会话列表冷启动实测 6.8s(ccdaemon 磁盘扫描), 命中它自己的缓存则 0ms。
# 面板绝不能被这一段拖住 —— 超时就降级返回空, 界面显示"数据不全"而不是一直转。
LIVE_SESSIONS_TIMEOUT_S = 6.0

# 上一次成功拿到的会话列表。超时就退回这份陈旧数据 ——
# 给空列表等于把整面板清空, 比显示"稍旧但正确"糟得多。
_SESSIONS_LKG: dict[str, Any] = {"ts": 0.0, "items": []}


async def _live_sessions() -> tuple[list[dict[str, Any]], float | None]:
    """活会话。返回 (会话, 陈旧秒数)。陈旧秒数为 None 表示这次是新鲜的。"""
    try:
        from omnicompany.dashboard.ccdaemon.pty_routes import list_sessions

        payload = await asyncio.wait_for(
            list_sessions(include_recoverable=False), timeout=LIVE_SESSIONS_TIMEOUT_S
        )
    except (TimeoutError, asyncio.TimeoutError, Exception):
        last = _SESSIONS_LKG
        if last["items"]:
            return list(last["items"]), round(time.time() - float(last["ts"]), 1)
        return [], -1.0
    items = payload.get("items") if isinstance(payload, dict) else payload
    rows = [r for r in (items or []) if isinstance(r, dict)]
    _SESSIONS_LKG.update(ts=time.time(), items=rows)
    return rows, None


# 聚合结果短 TTL 缓存: 反复开合面板不该每次都重算一遍。
_CACHE: dict[str, Any] = {"ts": 0.0, "payload": None}
CACHE_TTL_S = 4.0


_PARENTS_CACHE: dict[str, Any] = {"mtime": -1.0, "value": {}}


def _parents() -> dict[str, str]:
    """唤起关系 → 主从。历史台账里的 resumed_from_pty_id 是父会话。

    按 mtime 缓存: 整份台账 610 条, 每次全读要 200ms+。
    """
    path = _root() / "data" / "cc_sessions.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _PARENTS_CACHE["mtime"] == mtime:
        return _PARENTS_CACHE["value"]
    raw = _read_json(path) or []
    records = raw if isinstance(raw, list) else (raw.get("sessions") or [])
    if isinstance(records, dict):
        records = list(records.values())
    out: dict[str, str] = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        child, parent = rec.get("id"), rec.get("resumed_from_pty_id")
        if child and parent:
            out[str(child)] = str(parent)
    _PARENTS_CACHE.update(mtime=mtime, value=out)
    return out


def _todos() -> list[dict[str, Any]]:
    from omnicompany.packages.services._core.lifecycle import plan_groups_store as pgs

    payload = pgs.load_group(TODO_GROUP) or {}
    out = []
    for t in payload.get("tasks", []):
        legacy = t.get("legacy") or {}
        out.append({
            "id": t.get("id"),
            "title": t.get("title"),
            "status": t.get("status"),
            "archived": bool(t.get("archived")),
            "plan_id": t.get("plan_id"),
            "memo": legacy.get("memo"),
            "material_id": legacy.get("material_id"),
            "session_trace_id": legacy.get("session_trace_id"),
            "updated_at": t.get("updated_at") or 0,
        })
    out.sort(key=lambda r: r["updated_at"], reverse=True)
    return out


def _tasklines() -> list[dict[str, Any]]:
    from omnicompany.packages.services._core.lifecycle import taskline_store

    return taskline_store.list_all(include_archived=False)


def _current_todo_of(trace: str, todos: list[dict[str, Any]]) -> dict[str, Any] | None:
    """会话标题 = 它当前进行中的 todo（用户明确：会话不再是当前标题）。"""
    mine = [t for t in todos if t.get("session_trace_id") == trace and not t["archived"]]
    for t in mine:
        if t.get("status") == "in_progress":
            return t
    return mine[0] if mine else None


@studio_cluster_router.get("/api/cluster")
async def get_cluster(inactive: str | None = Query(default=None, description="1 = 只要不再活跃的任务线")) -> JSONResponse:
    want_inactive = inactive in ("1", "true", "yes")
    if not want_inactive:
        cached = _CACHE.get("payload")
        if cached is not None and (time.monotonic() - float(_CACHE["ts"])) <= CACHE_TTL_S:
            return JSONResponse(cached)
    plan_titles = _plan_titles()
    proj_names = _project_names()
    todos = _todos()
    lines = _tasklines()
    sessions, stale_age = await _live_sessions()
    parents = _parents()

    # 进程意义上仍在活跃的会话所主管的任务线 = 活跃任务线。
    live_traces = {str(s.get("id")) for s in sessions if s.get("alive")}
    live_plans = {str(s.get("active_plan")) for s in sessions if s.get("alive") and s.get("active_plan")}

    def line_is_live(line: dict[str, Any]) -> bool:
        if any(p in live_plans for p in line.get("plans") or []):
            return True
        ids = set(line.get("todos") or [])
        return any(t["id"] in ids and t.get("session_trace_id") in live_traces for t in todos)

    def line_node(line: dict[str, Any]) -> dict[str, Any]:
        ids = set(line.get("todos") or [])
        mine = [t for t in todos if t["id"] in ids]
        return {
            "id": line["id"],
            "title": line.get("title"),
            "status": line.get("status"),
            "project": line.get("project"),
            "summary": line.get("summary"),
            "plans": [
                {"plan_id": p, "title": plan_titles.get(p, p)} for p in (line.get("plans") or [])
            ],
            "todos": [t for t in mine if not t["archived"]],
            "doc_paths": line.get("doc_paths") or [],
            "work_paths": line.get("work_paths") or [],
            "session_files": line.get("session_files") or [],
            "forked_from": line.get("forked_from"),
            "split_from": line.get("split_from"),
            "live": line_is_live(line),
        }

    if want_inactive:
        # 额外入口：未归档、但没有任何进程意义上活跃的会话主管它。
        return JSONResponse({
            "inactive_tasklines": [line_node(l) for l in lines if not line_is_live(l)],
        })

    def session_node(s: dict[str, Any]) -> dict[str, Any]:
        sid = str(s.get("id"))
        plan = s.get("active_plan")
        cur = _current_todo_of(sid, todos)
        provider = str(s.get("provider") or "")
        return {
            "id": sid,
            "provider": provider,
            "color": PROVIDER_COLORS.get(provider, PROVIDER_FALLBACK),
            "alive": bool(s.get("alive")),
            "working": bool(s.get("working")),
            "parent": parents.get(sid),
            "plan_id": plan,
            "plan_title": plan_titles.get(str(plan)) if plan else None,
            # 标题兜底链: 当前进行中的 todo > 会话话题 > 计划中文名 > 会话显示名 > provider。
            # 只到 provider 就等于没信息 —— 那是"任务感知"最差的形态, 尽量往前兜。
            "title": (
                (cur or {}).get("title")
                or s.get("topic")
                or (plan_titles.get(str(plan)) if plan else None)
                or s.get("summary_title")
                or s.get("display_title")
                or s.get("provider_title")
                or provider
            ),
            "current_todo_id": (cur or {}).get("id"),
            "todos": [t for t in todos if t.get("session_trace_id") == sid and not t["archived"]],
        }

    buckets: dict[str, dict[str, Any]] = {}

    def bucket(pid: str | None) -> dict[str, Any]:
        key = str(pid or "_unassigned")
        if key not in buckets:
            buckets[key] = {
                "project": None if key == "_unassigned" else key,
                "title": "未归项目" if key == "_unassigned" else proj_names.get(key, key),
                "sessions": [],
                "tasklines": [],
            }
        return buckets[key]

    for s in sessions:
        # 只收进程意义上活着的会话 —— 不活的历史会话进视图就是"比会话列表多一倍"的噪音
        if not s.get("alive"):
            continue
        bucket(s.get("project")).get("sessions").append(session_node(s))
    # provider 内序号: 同一 provider 在同一抽屉里连续编号 (全抽屉流水号会让 CC 出现 1 和 4, 无意义)
    for b in buckets.values():
        seen: dict[str, int] = {}
        for node in b["sessions"]:
            key = node["provider"]
            seen[key] = seen.get(key, 0) + 1
            node["ordinal"] = seen[key]
    for line in lines:
        bucket(line.get("project")).get("tasklines").append(line_node(line))

    # 有活跃会话的项目在前（视图默认展开这些）
    ordered = sorted(
        buckets.values(),
        key=lambda b: (
            -sum(1 for x in b["sessions"] if x["working"]),
            -len(b["sessions"]),
            str(b["title"]),
        ),
    )
    payload = {
        "projects": ordered,
        "provider_colors": PROVIDER_COLORS,
        "stale_age_s": stale_age,
        "counts": {
            "projects": len(ordered),
            "sessions": len(sessions),
            "working": sum(1 for s in sessions if s.get("working")),
            "tasklines": len(lines),
            "inactive_tasklines": sum(1 for l in lines if not line_is_live(l)),
            "todos": sum(1 for t in todos if not t["archived"]),
        },
    }
    # 降级(尤其是连旧数据都没有)的结果不进缓存 —— 否则一次超时会让面板空 4 秒,
    # 而下一次请求本来可能就成功了。
    if stale_age is None:
        _CACHE.update(ts=time.monotonic(), payload=payload)
    return JSONResponse(payload)


@studio_cluster_router.post("/api/spawn-for-tasklines")
async def spawn_for_tasklines(body: dict[str, Any]) -> JSONResponse:
    """为选中的任务线备好新会话要载入的上下文包。

    产物：`data/services/tasklines/_context/<stamp>.md`，含每条线的摘要、todo、
    文档路径、工作路径与历史会话文件路径；并把该文件回绑到各任务线的 doc_paths，
    这样伴随会话后续维护的是同一份真源。

    **刻意不在这里起进程**：建 PTY 会话要 provider / cwd 等参数，且 pty_routes 明确
    记录"面板自行 spawn worker"是要能追责的行为。这里只备料，起会话仍走既有入口。
    """
    from omnicompany.packages.services._core.lifecycle import taskline_store

    ids = [str(x) for x in (body.get("taskline_ids") or []) if str(x).strip()]
    if not ids:
        return JSONResponse({"ok": False, "error": "没有给 taskline_ids"}, status_code=400)

    lines = []
    for tid in ids:
        rec = taskline_store.load(tid)
        if rec is None:
            return JSONResponse({"ok": False, "error": f"任务线不存在: {tid}"}, status_code=404)
        lines.append(rec)

    todos = {t["id"]: t for t in _todos()}
    plan_titles = _plan_titles()

    out: list[str] = ["# 任务线上下文（新建会话自动载入）", ""]
    out.append(f"共 {len(lines)} 条任务线。此文件由工作室集群生成，后续由伴随会话维护。")
    for rec in lines:
        out += ["", f"## {rec.get('title')}", "", f"- 任务线 id: `{rec['id']}`",
                f"- 状态: {rec.get('status')}", f"- 项目: {rec.get('project') or '-'}"]
        if rec.get("summary"):
            out.append(f"- 摘要: {rec['summary']}")
        for label, key in (("plan", "plans"), ("文档路径", "doc_paths"),
                           ("工作路径", "work_paths"), ("历史会话文件", "session_files")):
            items = rec.get(key) or []
            if not items:
                continue
            out += ["", f"### {label}"]
            for it in items:
                shown = f"{plan_titles.get(it, it)}（`{it}`）" if key == "plans" else f"`{it}`"
                out.append(f"- {shown}")
        line_todos = [todos[t] for t in (rec.get("todos") or []) if t in todos]
        out += ["", "### todo"]
        if not line_todos:
            out.append("- （这条线上还没有 todo）")
        for t in line_todos:
            memo = f" — 备忘：{t['memo']}" if t.get("memo") else ""
            out.append(f"- [{t.get('status')}] {t.get('title')}{memo}")

    root = _root() / "data" / "services" / "tasklines" / "_context"
    root.mkdir(parents=True, exist_ok=True)
    stamp = max(int(rec.get("updated_at") or 0) for rec in lines)
    name = f"context-{stamp}-{len(lines)}line.md"
    path = root / name
    path.write_text(chr(10).join(out) + chr(10), encoding="utf-8")

    rel = f"data/services/tasklines/_context/{name}"
    for rec in lines:
        try:
            taskline_store.bind(rec["id"], doc_paths=[rel])
        except Exception:
            pass

    return JSONResponse({
        "ok": True,
        "context_path": rel,
        "tasklines": [rec["id"] for rec in lines],
        "note": "上下文已备好并回绑到各任务线的 doc_paths；起会话仍走 CLI Hub 的新建入口。",
    })
