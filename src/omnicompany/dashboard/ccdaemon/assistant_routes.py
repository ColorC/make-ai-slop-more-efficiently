# [OMNI] origin=ai-ide ts=2026-09-05 type=dashboard
# [OMNI] material_id="material:dashboard.ccdaemon.assistant_routes.py"
"""assistant(机主的助理)会话专属的路由与监视: 额度问题监视 + 换档交接。

从 pty_routes.py 拆出来(R4 行数棘轮)。这里只放助理特有的部分; 会话创建仍在
pty_routes.create_agent_worker, 它按 topic=assistant 决定落脚目录与开场任务。

所有共享设施(会话管理器、投递、事件、台账)都在调用时经 ``pty_routes`` 取,
不在模块顶层 import: 一是避免循环 import, 二是让测试对 ``pty_routes.*`` 的
monkeypatch 对这里同样生效。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .assistant import (
    ASSISTANT_MODEL_LADDER,
    ASSISTANT_TOPIC,
    assistant_handoff_task,
    detect_billing_issue,
    mark_provider_exhausted,
    refresh_history_index,
    resolve_assistant_launch,
)

logger = logging.getLogger(__name__)

#: 挂在 pty_routes.cc_router 下(前缀 /cc 由父路由给)。
assistant_router = APIRouter(tags=["cc-assistant"])


def _routes() -> Any:
    from . import pty_routes

    return pty_routes


# ── 额度问题监视 ────────────────────────────────────────────────────────────
#
# 会话输出流(ring)里出现欠费/配额/余额类报错时给会话打 billing_issue 标记,
# 前端轮询 meta 见到标记才弹「换一档」按钮, 平时不提。只扫增量, ring 截断
# (popleft)时回扫尾窗, 不整段重建。
_BILLING_SCAN_POLL_S = 2.0
_BILLING_SCAN_CHUNKS = 240


def _previous_session_url(request: Request, session_id: str) -> str:
    source = request.headers.get("referer") or str(request.base_url)
    parsed = urlsplit(source)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        parsed = urlsplit("http://localhost:8210/")
    base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", "", ""))
    query = urlencode({
        "open_type": "cc_session",
        "open_id": session_id,
    })
    return f"{base}?{query}"


async def _monitor_billing_issue(sid: str) -> None:
    """轮询 assistant 会话输出尾窗, 识别额度问题并置 billing_issue 标记。"""
    manager = _routes().get_manager()
    cursor = 0
    while True:
        await asyncio.sleep(_BILLING_SCAN_POLL_S)
        sess = manager.get(sid)
        if sess is None or sess.closed:
            return
        chunks = list(sess.ring)
        if len(chunks) < cursor:
            # ring 截断重头扫: 只回扫尾窗, 避免漏报。
            start = max(0, len(chunks) - _BILLING_SCAN_CHUNKS)
        else:
            start = cursor
        cursor = len(chunks)
        if start >= len(chunks):
            continue
        tail = "".join(str(chunk) for chunk in chunks[start:])
        if not detect_billing_issue(tail):
            continue
        sess.billing_issue = True
        # 记一笔, 下次挑模型时跳过这一档。
        mark_provider_exhausted(str(sess.provider or ""), reason=tail[-300:])
        logger.warning(
            "assistant session %s hit billing issue (provider=%s); flagging for provider switch",
            sid,
            sess.provider,
        )
        return


# ── 换档交接 ────────────────────────────────────────────────────────────────

class SwitchProviderBody(BaseModel):
    """额度问题后的交接请求: 旧 assistant 会话被 kill, 阶梯上的下一档承接身份。

    ``to_provider`` 留空时按模型阶梯自动选(跳过刚报额度用尽的那一档); 显式给了就
    用给定的 provider/model。
    """

    session_id: str = Field(..., min_length=1, max_length=300)
    #: 留空 = 按模型阶梯自动选下一档(跳过刚报额度用尽的那一档)。
    to_provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=200)


@assistant_router.post("/agent-workers/switch-provider")
async def switch_provider(body: SwitchProviderBody, request: Request) -> dict[str, Any]:
    """杀掉旧 assistant 会话, 起阶梯下一档的新会话并播交接种子(自动交接)。"""
    routes = _routes()
    manager = routes.get_manager()
    sess = manager.get(body.session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"session not found: {body.session_id}")
    if sess.topic != ASSISTANT_TOPIC:
        raise HTTPException(
            status_code=400,
            detail="switch-provider is only allowed for assistant sessions",
        )
    old_provider = str(sess.provider or "")
    effort: str | None = None
    if body.to_provider:
        provider = routes.canonical_cli_provider_id(body.to_provider)
        model = body.model
        effort = next(
            (
                rung.effort
                for rung in ASSISTANT_MODEL_LADDER
                if rung.provider == provider and rung.model == model
            ),
            None,
        )
    else:
        # 自动交接: 记下这一档额度用尽, 按阶梯挑下一档。
        mark_provider_exhausted(old_provider, reason="billing_issue")
        rung = resolve_assistant_launch(exclude={old_provider})
        provider, model, effort = rung.provider, rung.model, rung.effort
        # 阶梯已耗尽时 resolver 会返回末档作为保底；不能在同一 provider
        # 上递归重建会话。
        if provider == old_provider:
            raise HTTPException(status_code=409, detail="no available assistant provider")
    if provider not in routes.AGENT_WORKER_PROVIDER_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"provider {provider!r} is not a CLI Agent Worker runtime",
        )
    from omnicompany.packages.services._core.agent.control_plane import build_provider_command

    old_id = sess.id
    old_cwd = sess.cwd
    old_origin = sess.origin
    old_visibility = sess.visibility

    try:
        refresh_history_index(Path(old_cwd) if old_cwd else None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("assistant history index refresh failed: %s", exc)

    try:
        cmd = build_provider_command(provider, model, effort=effort)
        new_sess = await manager.create(
            cmd=cmd,
            cwd=old_cwd,
            safe_mode=False,
            extra_env=routes.second_account_env(provider) or None,
            origin=old_origin,
            visibility=old_visibility,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 先确认新会话已创建，再以可恢复方式收口旧会话。交接只给旧会话
    # 深链，因此不能留下一个 recovery_disabled 的死链接。
    await manager.close_for_handoff(old_id)
    new_sess.topic = ASSISTANT_TOPIC
    routes.update_meta_field(new_sess.id, topic=ASSISTANT_TOPIC)
    manager.mark_billing_watched(new_sess.id)
    watcher = asyncio.create_task(
        routes._monitor_billing_issue(new_sess.id),
        name=f"assistant-billing-watch-{new_sess.id}",
    )
    routes._track_agent_worker_monitor(watcher)

    from omnicompany.packages.services._core.identity import update_session_binding

    update_session_binding(
        new_sess.id,
        session_id=new_sess.id,
        pty_id=new_sess.id,
        provider=provider,
        cwd=new_sess.cwd,
        topic=ASSISTANT_TOPIC,
        runtime_kind="cli",
        worker_kind="external_agent_worker",
        product_kind="agent_worker",
        binding_kind="unbound",
        session_origin=new_sess.origin,
        visibility=new_sess.visibility,
        model=model,
        source="agent_control_plane",
    )
    routes._emit_agent_worker_event(
        new_sess,
        "subagent.spawned",
        {
            "subagent_id": new_sess.id,
            "runtime_kind": "cli",
            "worker_kind": "external_agent_worker",
            "switched_from": old_id,
            "reason": "billing_issue",
        },
        from_controller=False,
    )
    seed = assistant_handoff_task(
        previous_session_url=_previous_session_url(request, old_id),
    )
    seeder = asyncio.create_task(
        routes._seed_agent_worker(
            new_sess.id,
            seed,
            active_plan=None,
            from_controller=False,
        ),
        name=f"assistant-handoff-seed-{new_sess.id}",
    )
    routes._track_agent_worker_monitor(seeder)
    routes._invalidate_session_list_cache()
    return {
        **new_sess.to_meta(),
        "worker_id": new_sess.id,
        "pty_id": new_sess.id,
        "runtime_kind": "cli",
        "worker_kind": "external_agent_worker",
        "product_kind": "agent_worker",
        "binding_kind": "unbound",
        "model": model,
        "visibility": new_sess.visibility,
        "switched_from": old_id,
        "reason": "billing_issue",
    }


__all__ = ["SwitchProviderBody", "_monitor_billing_issue", "assistant_router", "switch_provider"]
