# [OMNI] origin=claude domain=dashboard/controlplane ts=2026-09-04 type=route status=active
# [OMNI] summary="收件箱 HTTP 面: 订阅矩阵只读、候选预览(不写总线)、同步(带 source/surface/limit 范围控制)。逻辑全在 services/_focus/inbox, 这里只是壳。"
# [OMNI] why="用户 2026-09-03: 专注就完成全部工作的地方; 逻辑不放 dashboard 是因为 CLI 也要用同一份(arch_gate R2 禁 CLI 依赖 dashboard 层)。"
"""收件箱的 HTTP 面。真正的东西在 `packages/services/_focus/inbox`。"""
from __future__ import annotations

import asyncio
import logging


from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from omnicompany.packages.services._focus import inbox as core
from omnicompany.packages.services._focus import session_attention

logger = logging.getLogger(__name__)

inbox_router = APIRouter(prefix="/api/inbox", tags=["inbox"])


# ─── 会话完成投影(阶段 2.2b)────────────────────────────────────────────
#
# 「跑完一轮却还没被看的会话」是队列里最重要的一类, 但它的判据(has_user_turn /
# last_submit_at / working)只在 ccdaemon 进程里是活的, 而 packages 层不许 import
# dashboard(arch_gate R2)。所以由**这一层**把会话快照取回来、算成投影写下去,
# 收件箱与 CLI 再从投影读 —— 方向始终是 dashboard 往 packages 写。
#
# 注: 更理想的发布点是 ccdaemon 列会话时顺手写(省掉这一跳 HTTP)。那处代码当前被
# 另一个会话的写入声明占着, 等释放后可以搬过去 —— 搬的时候只是换调用点,
# build_rows / publish_projection 这两个纯函数原样复用。


async def _refresh_session_projection() -> None:
    """取一次活会话 → 发布投影。**永不抛**: 这是收件箱的附带产物, 它坏掉不该让
    收件箱本身 500。指纹集没变时 publish_projection 自己会跳过落盘。"""
    try:
        from .cc_proxy import _daemon_http_base, _daemon_http_client

        client = await _daemon_http_client()
        resp = await client.request(
            method="GET",
            url=f"{_daemon_http_base()}/cc/sessions?include_recoverable=false",
        )
        if not resp.is_success:
            return
        items = resp.json().get("items")
        if not isinstance(items, list):
            return
        # chat 会话也是自家的一类, /cc/sessions 不含它们 —— 漏掉就是「不全」。
        chats: list = []
        try:
            chat_resp = await client.request(
                method="GET", url=f"{_daemon_http_base()}/cc/chat/sessions"
            )
            if chat_resp.is_success:
                payload = chat_resp.json()
                found = payload if isinstance(payload, list) else payload.get("items") or []
                chats = [c for c in found if isinstance(c, dict)]
        except Exception:
            chats = []
        rows = session_attention.build_rows(items, chats)
        await asyncio.to_thread(session_attention.publish_projection, rows)
    except Exception:  # 附带产物: daemon 没起、超时、格式变了, 一律吞掉
        logger.debug("session completion projection refresh failed", exc_info=True)


@inbox_router.get("/subscriptions")
def subscriptions():
    subs = core.profile().get("subscriptions")
    if not subs:
        return JSONResponse(
            {"ok": False, "code": "not_found", "message": "个人附加层里没有 subscriptions"},
            status_code=404,
        )
    return {"ok": True, "subscriptions": subs, "surfaces": list(core.SURFACES)}


@inbox_router.get("/preview")
async def preview():
    """只跑适配器与订阅判定, 不写总线 —— 调订阅矩阵时先看结果再落地。"""
    await _refresh_session_projection()
    candidates = core.collect()
    by_surface: dict[str, int] = {}
    for cand in candidates:
        surface = str(cand.get("surface") or "log")
        by_surface[surface] = by_surface.get(surface, 0) + 1
    return {"ok": True, "candidates": candidates, "bySurface": by_surface}


@inbox_router.get("/board")
async def board():
    """收件箱一屏: 项目 ▸ 会话 两级聚合, 阶梯高的在前; 延后的不占视野只计数。"""
    await _refresh_session_projection()
    out = core.board()
    if not out.get("ok"):
        status = 503 if out.get("code") == "runtime_unavailable" else 502
        return JSONResponse(out, status_code=status)
    return out


@inbox_router.post("/act")
async def act(req: Request):
    """处理 / 往后拖 / 忽略 / 撤销 / 分诊出箱。

    键是 dedupeKey(source:remoteId) —— 列表实时取源, 消息本身不是任务, 只有 triage
    才会落成一条正经待办。
    """
    try:
        body = await req.json()
        body = body if isinstance(body, dict) else {}
    except Exception:
        body = {}
    key = str(body.get("key") or "")
    action = str(body.get("action") or "")
    if not key or not action:
        return JSONResponse(
            {"ok": False, "code": "invalid_argument", "message": "key/action 必填"},
            status_code=400,
        )
    out = core.act(key, action, until=(str(body["until"]) if body.get("until") else None))
    return out if out.get("ok") else JSONResponse(out, status_code=400)


@inbox_router.post("/mute")
async def mute(req: Request):
    """就地「以后不要这类」—— 学 Paradox 的 Ctrl+点消息, 不用去设置页翻。"""
    try:
        body = await req.json()
        body = body if isinstance(body, dict) else {}
    except Exception:
        body = {}
    source = str(body.get("source") or "")
    if not source:
        return JSONResponse({"ok": False, "code": "invalid_argument", "message": "source 必填"},
                            status_code=400)
    out = core.mute(source, str(body.get("kind") or "*"), str(body.get("surface") or "off"))
    return out if out.get("ok") else JSONResponse(out, status_code=400)


# ─── 会话注意力: 未看清单 + 已看记录 ────────────────────────────────────
#
# 这一段是阶段 2.2b 的要害: **已看记录必须持久在后端**。原先它只在浏览器
# localStorage 里, 换台设备就丢, 第二个端(litework)更是完全读不到。


@inbox_router.get("/sessions/attention")
async def sessions_attention():
    """跑完一轮、还没被你看的会话。前端用它画未读, litework 之后读同一份。"""
    await _refresh_session_projection()
    unseen = session_attention.list_unseen()
    return {
        "ok": True,
        "unseen": unseen,
        "unseenCount": len(unseen),
        "seen": session_attention.read_seen(),
    }


@inbox_router.post("/sessions/seen")
async def sessions_seen(req: Request):
    """记下「这一轮我看过了」。

    具体是哪一轮由后端说了算(前端那份可能比投影旧), 所以正常路径只要给 sessionId。
    """
    try:
        body = await req.json()
        body = body if isinstance(body, dict) else {}
    except Exception:
        body = {}
    session_id = str(body.get("sessionId") or "")
    if not session_id:
        return JSONResponse(
            {"ok": False, "code": "invalid_argument", "message": "sessionId 必填"},
            status_code=400,
        )
    await _refresh_session_projection()
    fingerprint = session_attention.mark_seen(
        session_id,
        (str(body["fingerprint"]) if body.get("fingerprint") else None),
    )
    return {"ok": True, "sessionId": session_id, "fingerprint": fingerprint}


@inbox_router.post("/sessions/seen/import")
async def sessions_seen_import(req: Request):
    """迁移: 把浏览器 localStorage 里那份整批收上来。**只补不覆盖** ——
    服务端已经记着的那一轮更可信, 浏览器那份可能是别的设备上的旧账。"""
    try:
        body = await req.json()
        body = body if isinstance(body, dict) else {}
    except Exception:
        body = {}
    entries = body.get("seen")
    if not isinstance(entries, dict):
        return JSONResponse(
            {"ok": False, "code": "invalid_argument", "message": "seen 必须是对象"},
            status_code=400,
        )
    added = session_attention.import_seen(
        {str(k): str(v) for k, v in entries.items() if isinstance(v, str)}
    )
    return {"ok": True, "added": added, "seen": session_attention.read_seen()}
