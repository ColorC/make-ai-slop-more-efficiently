# [OMNI] origin=codex domain=dashboard/controlplane ts=2026-09-01 type=route status=active
# [OMNI] summary="任务总线 (task-core) Todo 面板 API: /api/tbus/board 聚合 + create/status/update/archive/bind 轻写 + /api/tbus/intake 任意产物提单入口, 全走 LiTe CLI 信封"
# [OMNI] why="用户 2026-09-01: sidepanel '集群'页改为 Todo, 人类看界面不看 CLI; 看板/列表双视图按项目聚合, 显示会话绑定与进行情况, 可增删改查"
"""任务总线（task-core）Todo 面板数据面。

消费方纪律同 task-core.md §7：只经 LiTe CLI 信封，不读宿主 SQLite/RPC。
删 = cancelled + archive（总线无硬删，审计留痕）；单写者仍是宿主。
"""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from omnicompany.cli.commands import tbus as bridge

task_bus_router = APIRouter(prefix="/api/tbus", tags=["tbus"])

LANES = ["inbox", "todo", "in-progress", "paused", "blocked", "in-review", "completed", "cancelled"]

# 个人附加层(task-core §10)的唯一真源。词表只有这一份, UI 与 CLI 都是它的投影 ——
# 前端再抄一份常量, 就会出现「界面说 3 级、CLI 说 P1」。
_PROFILE_PATH = Path(__file__).resolve().parents[4] / "data" / "task_core_profile.json"


def _fail(code: str, message: str) -> JSONResponse:
    status = 503 if code == "runtime_unavailable" else 502
    return JSONResponse({"ok": False, "code": code, "message": message}, status_code=status)


def _run(args: list[str]):
    code, payload, stderr = bridge._run_cli(bridge._resolve_dir(None), args)
    failure = bridge._envelope_error(payload)
    if failure or code != 0 or not payload:
        message = failure[1] if failure else ((stderr or "").strip() or f"exit={code}")
        return None, (failure[0] if failure else "runtime_error"), message
    return payload, None, None


def _result(payload) -> dict:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    return result if isinstance(result, dict) else {}


# location links 没有批量 RPC（tasks.listLocations 只吃单个 id），一条任务一次 node 冷启，
# 50 条就是 10s+ 的白屏。宿主契约不动的前提下只能在消费侧解决：线程池并发 + 短 TTL 缓存。
# TTL 取 3s：短于面板 20s 轮询，看得见别处的写入；又能吃掉一次刷新内的重复取。
_LINKS_TTL_S = 3.0
_LINKS_MAX = 512
_links_cache: dict[str, tuple[float, list]] = {}
_links_lock = threading.Lock()

def _links_for(task_id: str) -> list:
    now = time.monotonic()
    with _links_lock:
        hit = _links_cache.get(task_id)
        if hit and now - hit[0] < _LINKS_TTL_S:
            return hit[1]
    payload, _code, _message = _run(["task", "location", "list", "--id", task_id, "--json"])
    links = [l for l in (_result(payload).get("links") or []) if isinstance(l, dict)] if payload else []
    with _links_lock:
        if len(_links_cache) > _LINKS_MAX:
            stale = [k for k, (at, _v) in _links_cache.items() if now - at >= _LINKS_TTL_S]
            for key in stale:
                _links_cache.pop(key, None)
        _links_cache[task_id] = (now, links)
    return links


# ─── 提单入口 ──────────────────────────────────────────────────────────
# 「暴露一个入口, 各个产物自行处理」: 总线只定信封与落点, 触发方式由产物自己定
# (快捷键 / 右键 / 崩溃钩子 / 截图后追加都行)。本质是"把问题存着一起解决"。
#
# 跨源: dashboard 直连口(8210)没有鉴权中间件, 但浏览器侧有 CORS。全局
# CORSMiddleware 只放行固定 origin 列表, 预检会被它先拦掉 —— 所以这里给本路由
# 单独回 `*`(Starlette 的 MutableHeaders.update 是覆盖不是追加, 不会出双头),
# 并建议产物用 text/plain 或 sendBeacon 发, 走"简单请求"绕开预检。
# 要让任意 origin 也能读回执, 设 OMNI_DASHBOARD_CORS_ORIGINS。
_INTAKE_CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "600",
}

# 重复投递不重复建单: 产物按下两次、或网络重试, 不该在收件箱里留两条一样的。
# 只在内存里记一小段时间 —— 个人规模下重复单子删起来也就一下, 不值得上持久化。
_INTAKE_TTL_S = 120.0
_intake_seen: dict[str, tuple[float, str]] = {}
_intake_lock = threading.Lock()


@task_bus_router.options("/intake")
def intake_preflight() -> Response:
    return Response(status_code=204, headers=_INTAKE_CORS)


@task_bus_router.post("/intake")
async def intake(req: Request):
    """任意产物提单 → 收件箱。

    只认一个必填字段 title；其余都是分诊时的便利。落点固定：`stateKey=inbox`、
    不定级、不指派、不归项目 —— 提单的价值就是"先存下别打断我"，当场分类
    等于当场打断（spec §2.1 的三个"不"）。
    """
    body = await _json_body(req)
    title = str(body.get("title") or "").strip()
    if not title:
        return JSONResponse(
            {"ok": False, "code": "invalid_argument", "message": "title 必填"},
            status_code=400, headers=_INTAKE_CORS,
        )
    source = body.get("source") if isinstance(body.get("source"), dict) else {}
    key = str(body.get("idempotencyKey") or "").strip()
    now = time.monotonic()
    if key:
        with _intake_lock:
            for stale in [k for k, (at, _v) in _intake_seen.items() if now - at >= _INTAKE_TTL_S]:
                _intake_seen.pop(stale, None)
            hit = _intake_seen.get(key)
        if hit:
            return JSONResponse(
                {"ok": True, "id": hit[1], "deduped": True, "sourceBound": None},
                headers=_INTAKE_CORS,
            )

    payload, code, message = _run(bridge.intake_create_args(title, body.get("note"), source))
    if code:
        return JSONResponse(
            {"ok": False, "code": code, "message": message},
            status_code=503 if code == "runtime_unavailable" else 502, headers=_INTAKE_CORS,
        )
    task = _result(payload).get("task") or {}
    task_id = str(task.get("id") or "")
    # 来源绑不上不算失败: 单子本身已经存下了。
    bound = False
    binding = bridge.intake_binding_args(task_id, source) if task_id else None
    if binding:
        bpayload, bcode, _bmsg = _run(binding)
        bound = bcode is None and bool(bpayload)
    if key and task_id:
        with _intake_lock:
            _intake_seen[key] = (now, task_id)
    return JSONResponse({"ok": True, "id": task_id, "deduped": False, "sourceBound": bound},
                        headers=_INTAKE_CORS)


@task_bus_router.get("/profile")
def profile():
    """个人附加层词表投影 —— 优先度阶梯 / 任务类型 / 标签 / 状态显示名 / 分诊规则。

    读 data/task_core_profile.json 原样吐出, 不在这里加工也不缓存: 文件是手改的,
    改完刷新页面就该生效; 个人规模下一次读盘的代价可以忽略。
    """
    try:
        return {"ok": True, "profile": json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))}
    except FileNotFoundError:
        return JSONResponse(
            {"ok": False, "code": "not_found", "message": f"缺 {_PROFILE_PATH.name} —— 个人附加层词表真源"},
            status_code=404,
        )
    except json.JSONDecodeError as exc:
        return JSONResponse(
            {"ok": False, "code": "invalid_argument", "message": f"{_PROFILE_PATH.name} 不是合法 JSON: {exc}"},
            status_code=500,
        )


@task_bus_router.get("/board")
def board(archived: bool = False, links: bool = False):
    """任务全量, 按项目聚合。

    **默认不带 location links**: 宿主没把 `tasks.listBoardProjection`(一次拿全部
    任务+链接)暴露到 CLI, 所以链接只能一条任务一次调用, 而每次调用都是一次 node
    冷启(~340ms)。35 条任务就是 1.4s 白屏, 而链接只用来在行尾画个"会话"小标 ——
    拿首屏去换一个次要标记是赔本的。要链接就单独走 /api/tbus/links, 首屏渲染完
    再合并进来(用户 2026-09-04: 读取任务总线的速度好慢)。
    """
    tasks, meta, code, message = bridge.cached_task_list(bridge._resolve_dir(None), archived)
    if code:
        return _fail(code, message)
    if links and tasks:
        with ThreadPoolExecutor(max_workers=8) as pool:
            fetched = list(pool.map(_links_for, [str(t.get("id")) for t in tasks]))
        for task, found in zip(tasks, fetched):
            task["locationLinks"] = found
    tasks.sort(key=lambda t: (t.get("orderKey") is None, str(t.get("orderKey") or ""), str(t.get("createdAt") or "")))
    projects: dict[str, dict] = {}
    for task in tasks:
        key = str(task.get("projectId") or "") or "未分组"
        projects.setdefault(key, {"projectId": key, "title": key, "tasks": []})["tasks"].append(task)
    ordered = sorted(projects.values(), key=lambda p: (p["projectId"] == "未分组", p["projectId"]))
    return {
        "ok": True,
        "runtimeId": meta.get("runtimeId"),
        "lanes": LANES,
        "projects": ordered,
        "phases": bridge._lifecycle_counts(tasks),
    }


@task_bus_router.get("/links")
def links(archived: bool = False):
    """全部任务的位置链接, 一次给完。首屏之后再取, 取回来前行尾少个小标而已。"""
    tasks, meta, code, message = bridge.cached_task_list(bridge._resolve_dir(None), archived)
    if code:
        return _fail(code, message)
    ids = [str(t.get("id")) for t in tasks]
    if not ids:
        return {"ok": True, "links": {}}
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(_links_for, ids))
    return {"ok": True, "links": {tid: found for tid, found in zip(ids, fetched)}}


@task_bus_router.get("/task/{task_id}")
def task_detail(task_id: str):
    payload, code, message = _run(["task", "show", "--id", task_id, "--json"])
    if code:
        return _fail(code, message)
    notes_payload, _nc, _nm = _run(["task", "note", "list", "--id", task_id, "--json"])
    notes = _result(notes_payload).get("notes") if notes_payload else []
    bindings_payload, _bc, _bm = _run(["task", "binding", "list", "--id", task_id, "--json"])
    bindings = _result(bindings_payload).get("bindings") if bindings_payload else []
    links_payload, _lc, _lm = _run(["task", "location", "list", "--id", task_id, "--json"])
    links = _result(links_payload).get("links") if links_payload else []
    return {
        "ok": True,
        "task": _result(payload).get("task"),
        "notes": notes or [],
        "bindings": bindings or [],
        "links": links or [],
    }


async def _json_body(req: Request) -> dict:
    try:
        body = await req.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


@task_bus_router.post("/task")
async def create_task(req: Request):
    body = await _json_body(req)
    title = str(body.get("title") or "").strip()
    if not title:
        return JSONResponse({"ok": False, "code": "invalid_argument", "message": "title 必填"}, status_code=400)
    args = ["task", "create", "--title", title, "--json"]
    if body.get("projectId") and body["projectId"] != "未分组":
        args += ["--project", str(body["projectId"])]
    if body.get("parentTaskId"):
        args += ["--parent", str(body["parentTaskId"])]
    if str(body.get("description") or "").strip():
        args += ["--description", str(body["description"])]
    if body.get("stateKey"):
        args += ["--state", str(body["stateKey"])]
    if body.get("priority") is not None:
        args += ["--priority", str(int(body["priority"]))]
    if body.get("dueAt"):
        args += ["--due-at", str(body["dueAt"])]
    payload, code, message = _run(args)
    if code:
        return _fail(code, message)
    bridge.invalidate_task_list()
    return {"ok": True, "task": _result(payload).get("task")}


@task_bus_router.post("/task/status")
async def set_status(req: Request):
    body = await _json_body(req)
    if not body.get("id") or not body.get("stateKey"):
        return JSONResponse({"ok": False, "code": "invalid_argument", "message": "id/stateKey 必填"}, status_code=400)
    payload, code, message = _run([
        "task", "status", "set", "--id", str(body["id"]), "--state", str(body["stateKey"]), "--json"
    ])
    if code:
        return _fail(code, message)
    bridge.invalidate_task_list()
    return {"ok": True, "task": _result(payload).get("task")}


@task_bus_router.post("/task/update")
async def update_task(req: Request):
    body = await _json_body(req)
    if not body.get("id") or not body.get("revision") or not str(body.get("title") or "").strip():
        return JSONResponse({"ok": False, "code": "invalid_argument", "message": "id/revision/title 必填"}, status_code=400)
    args = [
        "task", "update", "--id", str(body["id"]), "--revision", str(body["revision"]),
        "--title", str(body["title"]),
    ]
    if body.get("description") is not None:
        args += ["--description", str(body["description"])]
    # 优先级要能改回「未定级」: 显式 null 走 --no-priority(传 --priority "" 会被
    # getOptionalStringFlag 吃成 undefined, 等于什么都没改)。
    if "priority" in body:
        if body["priority"] is None:
            args += ["--no-priority"]
        else:
            args += ["--priority", str(int(body["priority"]))]
    if "dueAt" in body and body["dueAt"] is None:
        args += ["--no-due-at"]
    elif body.get("dueAt"):
        args += ["--due-at", str(body["dueAt"])]
    if body.get("orderKey"):
        args += ["--order-key", str(body["orderKey"])]
    # 换项目：宿主 LocalTaskPatch/PATCH_COLUMNS 一直支持 project_id，
    # 之前是 RPC zod 与 CLI spec 两层壳没开口（2026-09-01 已补）。
    # projectId 显式传 null = 移出所有项目，用 --no-project 表达
    # （--project "" 会被 getOptionalStringFlag 吃成 undefined）。
    if "projectId" in body:
        if body["projectId"] in (None, "", "未分组"):
            args += ["--no-project"]
        else:
            args += ["--project", str(body["projectId"])]
    args += ["--json"]
    payload, code, message = _run(args)
    if code:
        return _fail(code, message)
    bridge.invalidate_task_list()
    return {"ok": True, "task": _result(payload).get("task")}


@task_bus_router.post("/task/archive")
async def archive_task(req: Request):
    body = await _json_body(req)
    if not body.get("id"):
        return JSONResponse({"ok": False, "code": "invalid_argument", "message": "id 必填"}, status_code=400)
    payload, code, message = _run(["task", "archive", "--id", str(body["id"]), "--json"])
    if code:
        return _fail(code, message)
    bridge.invalidate_task_list()
    return {"ok": True, "task": _result(payload).get("task")}


@task_bus_router.post("/task/restore")
async def restore_task(req: Request):
    """归档的反向操作。总线无硬删，归档/恢复都是可逆的状态位。"""
    body = await _json_body(req)
    if not body.get("id"):
        return JSONResponse({"ok": False, "code": "invalid_argument", "message": "id 必填"}, status_code=400)
    payload, code, message = _run(["task", "restore", "--id", str(body["id"]), "--json"])
    if code:
        return _fail(code, message)
    bridge.invalidate_task_list()
    return {"ok": True, "task": _result(payload).get("task")}


@task_bus_router.post("/task/bind-conversation")
async def bind_conversation(req: Request):
    body = await _json_body(req)
    if not body.get("id") or not body.get("conversationId"):
        return JSONResponse({"ok": False, "code": "invalid_argument", "message": "id/conversationId 必填"}, status_code=400)
    payload, code, message = _run([
        "task", "location", "bind", "--id", str(body["id"]), "--location-kind", "conversation",
        "--location-id", str(body["conversationId"]), "--role", "supporting", "--json"
    ])
    if code:
        return _fail(code, message)
    return {"ok": True, "link": _result(payload).get("link")}
