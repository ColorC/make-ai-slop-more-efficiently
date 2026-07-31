# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.uvicorn_entry_app.py"
"""ccdaemon FastAPI 入口 — 独立 uvicorn 进程, 跟 dashboard 控制面进程拆开.

启动方式
--------
python -m uvicorn omnicompany.dashboard.ccdaemon.main:app --host 127.0.0.1 --port 8201

或通过 CLI:
omni cc daemon start [--port 8201]

CLI 启动时会自动管理 data/cc_daemon.pid + data/cc_daemon.port + data/cc_daemon.log,
并把 stdout/stderr 重定向到 log 文件. 直接用 uvicorn 跑则 pid/port 由 lifespan 写盘.

阶段二骨架版本: 仅暴露 health 端点跟 echo WebSocket, 验进程拆分跟反向代理.
阶段三填 chat / pty / installer / hooks 真业务路由.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

# 必须在 import claude_agent_sdk / chat / pty_routes 之前 install — patch anyio.open_process
# 让 Windows 下 SDK spawn claude.cmd 子进程不弹空 console 窗口
from . import _subprocess_hide
_subprocess_hide.install_subprocess_hide()
from . import _windows_accept
_windows_accept.install_resilient_accept()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from . import lifecycle

logger = logging.getLogger(__name__)


# 显式加载项目根 .env (THE_COMPANY_API_KEY / NVIDIA_API_KEY 等).
try:
    from dotenv import load_dotenv as _load_dotenv
    from omnicompany.core.config import omni_workspace_root
    _candidate = omni_workspace_root() / ".env"
    if _candidate.is_file():
        _load_dotenv(_candidate)
except ImportError:
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Daemon 启动时写 pid/port + 注册 atexit; 关闭时清 pid.

    BOSS SIGHT 块 1 重构后: 总控不再是常驻 FastAPI service, 而是按需的
    AgentNodeLoop 子类. omni-chat 创 controller session 时由 OmniAgentProvider
    实例化并驱动. 这里 lifespan 不需要单独启停总控.

    BOSS SIGHT 块 3 新增: 进程级注册 ControllerWaker — 订阅 chat_manager 内
    subagent.* 事件, 转成 controller session 的 user.message inject. 也更新
    SubagentStatusAggregator. lifespan 只负责挂钩, 不起独立后台 task.
    """
    import asyncio as _asyncio

    _windows_accept.install_exception_filter(_asyncio.get_running_loop())
    pid = os.getpid()
    # uvicorn 解析后实际监听端口由 server config 决定; 没有官方钩子拿端口, 走环境变量
    port = int(os.environ.get("OMNI_CC_DAEMON_PORT", str(lifecycle.DEFAULT_PORT)))
    # Phase 2A: lifespan 阶段的 logger.info 通常被 uvicorn 的 logger 配置吞掉 (不 propagate
    # 到 root). 关键诊断 info 改用 print(flush=True) — print 一定进 stdout, 而 stdout
    # 在 `omni cc daemon start` 时已被 tee 到 data/cc_daemon.log, 这样能确保留痕.
    print(f"[ccdaemon] lifespan starting pid={pid} port={port}", flush=True)
    lifecycle.write_pid(pid)
    lifecycle.write_port(port)
    lifecycle.install_atexit_hook()
    # P0 性能批 (2026-07): 事件落库改批量 — emit_event 原实现每条事件新建连接+
    # insert+commit, 每条聊天消息 ≥2 次, 同步 sqlite IO 堵事件循环。启用后 push 进
    # 队列, 后台线程 ~100ms/50 条批量 executemany (hooks/_shared.py, atexit 兜底 flush)。
    # hooks 子进程不经过这里, 保持同步直写不变。
    try:
        from .hooks._shared import enable_batched_events
        enable_batched_events()
    except Exception:  # noqa: BLE001
        logger.exception("ccdaemon: enable_batched_events failed (事件落库退回同步直写)")
    started_at = time.time()
    app.state.started_at = started_at
    app.state.daemon_pid = pid
    app.state.daemon_port = port
    resource_monitor = None

    # BOSS SIGHT 块 3: 挂 ControllerWaker + SubagentStatusAggregator
    try:
        from .chat import get_chat_manager
        from ..boss_sight.services.controller_waker import ControllerWaker
        from ..boss_sight.aggregator.subagent_status_aggregator import (
            SubagentStatusAggregator,
        )

        from omnicompany.core.config import omni_workspace_root
        workspace_root = omni_workspace_root()
        aggregator = SubagentStatusAggregator(workspace_root=workspace_root)
        try:
            aggregator.refresh_from_cc_sessions()
        except Exception:  # noqa: BLE001
            logger.exception("ccdaemon: SubagentStatusAggregator initial refresh failed")
        waker = ControllerWaker(
            chat_manager=get_chat_manager(),
            aggregator=aggregator,
        )
        waker.attach()
        app.state.boss_sight_waker = waker
        app.state.boss_sight_aggregator = aggregator
        print("[ccdaemon] BOSS SIGHT ControllerWaker + aggregator wired", flush=True)

        # N2d: workflow 编排器 — 订阅 subagent.completed 推进 fan-out → 综合。
        try:
            from ..boss_sight.services.workflow_orchestrator import get_orchestrator
            orch = get_orchestrator()
            get_chat_manager().subscribe_events(orch.on_event)
            app.state.boss_sight_workflow = orch
            print("[ccdaemon] BOSS SIGHT WorkflowOrchestrator wired", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[ccdaemon] ERROR failed to wire WorkflowOrchestrator: {e!r}", flush=True)
            logger.exception("ccdaemon: failed to wire WorkflowOrchestrator")
    except Exception as e:  # noqa: BLE001
        print(
            f"[ccdaemon] ERROR failed to wire BOSS SIGHT waker (subagent uplink offline): {e!r}",
            flush=True,
        )
        logger.exception("ccdaemon: failed to wire BOSS SIGHT waker (subagent uplink offline)")

    # BOSS SIGHT 块 4: reviewstage hub attach loop + store init
    try:
        import asyncio as _asyncio
        from ..boss_sight.reviewstage.routes import get_hub, get_store
        hub = get_hub()
        hub.attach_loop(_asyncio.get_running_loop())
        store = get_store()
        app.state.boss_sight_review_store = store
        app.state.boss_sight_review_hub = hub
        print(
            f"[ccdaemon] BOSS SIGHT reviewstage hub + store wired (root={store.root})",
            flush=True,
        )

        # M2 Phase 2 步骤 3: 桥 verdict_changed / comment_added → ControllerWaker.
        # 不动 store.set_verdict / add_comment 本体 (单一职责), 走 subscriber 单点.
        waker_ref = getattr(app.state, "boss_sight_waker", None)
        if waker_ref is not None:
            from ..boss_sight.services.controller_waker import make_reviewstage_bridge
            bridge = make_reviewstage_bridge(waker_ref)
            store.subscribe(bridge)
            app.state.boss_sight_review_to_waker_bridge = bridge
            print(
                "[ccdaemon] BOSS SIGHT reviewstage -> ControllerWaker bridge wired "
                "(comment_added -> reviewstage.comment; verdict 不唤起总控)",
                flush=True,
            )
        else:
            print(
                "[ccdaemon] WARN no waker on app.state, reviewstage bridge NOT wired "
                "(verdict/comment 不会唤起总控)",
                flush=True,
            )
    except Exception as e:  # noqa: BLE001
        print(
            f"[ccdaemon] ERROR failed to wire BOSS SIGHT reviewstage hub: {e!r}",
            flush=True,
        )
        logger.exception("ccdaemon: failed to wire BOSS SIGHT reviewstage hub")

    # 加载提速(2026-06-04 用户反馈"每次加载都很久"): 后台预热实体索引 + 材料登记缓存, 让首个驾驶舱
    # 请求(briefing/workflow/material-registry 都吃这层缓存)不必现扫工作区(~1000+ md, 冷扫数秒)。
    # 后台线程 + 吞异常, 不阻塞也不影响启动。
    try:
        import threading as _threading

        def _prewarm() -> None:
            try:
                from ..boss_sight.material_registry import build_material_registry
                build_material_registry(limit=1)
            except Exception:  # noqa: BLE001
                pass
            # 顺手把首屏三大慢扫描端点的快照建好(routes._snapshot_cached 首建即入缓存):
            # 否则 daemon 冷启后第一个访问者要现付全套冷扫(实测可达 20s+)。
            try:
                from ..boss_sight import routes as _bs_routes
                _bs_routes._snapshot_cached("plans", 15.0, _bs_routes._list_plans_sync)
                _bs_routes._snapshot_cached("briefing", 15.0, _bs_routes._get_briefing_sync)
                _bs_routes._snapshot_cached(
                    "workflow-summary:40", 15.0, lambda: _bs_routes._get_workflow_summary_sync(40)
                )
            except Exception:  # noqa: BLE001
                pass

        _threading.Thread(target=_prewarm, name="boss-sight-prewarm", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass

    # Read-only process/resource observation. It records attribution and
    # confirmed anomalies, but intentionally exposes no kill/suspend action.
    try:
        from .resource_monitor import get_resource_monitor

        resource_monitor = get_resource_monitor()
        resource_monitor.start()
        app.state.resource_monitor = resource_monitor
        print(
            f"[ccdaemon] resource monitor started interval={resource_monitor.interval_s}s "
            "(read-only, cleanup requires human confirmation)",
            flush=True,
        )
    except Exception:  # noqa: BLE001
        logger.exception("ccdaemon: resource monitor failed to start")

    print(f"[ccdaemon] started pid={pid} port={port}", flush=True)
    try:
        yield
    finally:
        if resource_monitor is not None:
            await resource_monitor.stop()
        lifecycle.clear_pid()
        print(f"[ccdaemon] stopped pid={pid}", flush=True)


app = FastAPI(title="omnicompany ccdaemon", version="0.1.0", lifespan=lifespan)


# ── 业务路由装载 ──
# chat (claude-agent-sdk) + pty (winpty) + installer 全在本进程, 不开 reload.
# dashboard 控制面通过 controlplane/cc_proxy.py 反向代理过来.
try:
    from .chat import cc_chat_router
    app.include_router(cc_chat_router)
except ImportError as e:
    logger.warning("ccdaemon: chat router not loaded: %s", e)

# N2d: workflow 编排路由 (/cc/workflow)
try:
    from .workflow_routes import workflow_router
    app.include_router(workflow_router)
except ImportError as e:
    logger.warning("ccdaemon: workflow router not loaded: %s", e)

try:
    from .pty_routes import cc_router
    app.include_router(cc_router)
except ImportError as e:
    logger.warning("ccdaemon: pty router not loaded: %s", e)

try:
    from .resource_routes import resource_router
    app.include_router(resource_router)
except ImportError as e:
    logger.warning("ccdaemon: resource monitor router not loaded: %s", e)

# 载入已有会话 (#2 / A1): 列出本机 Claude Code / Codex 历史会话供 BOSS SIGHT 载入续接。
try:
    from .import_routes import import_sessions_router
    app.include_router(import_sessions_router)
except ImportError as e:
    logger.warning("ccdaemon: import-sessions router not loaded: %s", e)

# BOSS SIGHT 路由 (块 1 · 总控本体 + 总控和人对接)
try:
    from ..boss_sight.routes import boss_sight_router
    app.include_router(boss_sight_router)
except ImportError as e:
    logger.warning("ccdaemon: boss_sight router not loaded: %s", e)

# BOSS SIGHT 块 4 审阅台
try:
    from ..boss_sight.reviewstage.routes import reviewstage_router
    app.include_router(reviewstage_router)
except ImportError as e:
    logger.warning("ccdaemon: reviewstage router not loaded: %s", e)

# BOSS SIGHT 用户捕获(圈选/快照/调试交接 提交=存文件, 不进审阅; 2026-06-03)
try:
    from ..boss_sight.captures.routes import captures_router
    app.include_router(captures_router)
except ImportError as e:
    logger.warning("ccdaemon: captures router not loaded: %s", e)

# BOSS SIGHT Vilo 草稿区文件 CRUD (创作者工作台草稿面板; 2026-06-14)
try:
    from ..boss_sight.vilo_drafts.routes import vilo_drafts_router
    app.include_router(vilo_drafts_router)
except ImportError as e:
    logger.warning("ccdaemon: vilo-drafts router not loaded: %s", e)

# BOSS SIGHT 统一自撰内容(札记): 评论/草稿/llm输入归一的中心 store (2026-06-14)
try:
    from ..boss_sight.authored.routes import notes_router
    app.include_router(notes_router)
except ImportError as e:
    logger.warning("ccdaemon: authored notes router not loaded: %s", e)


@app.get("/health")
async def daemon_health() -> dict:
    """Daemon 整体健康端点 — 跟 pty_routes 的 /cc/health 错开 (后者是 PTY 模块状态).

    用途:
    - start_dashboard_dev.py 等 daemon ready 用这个 (路径不跟业务路由冲突)
    - omni cc daemon status 综合查 (走 lifecycle.read_status 不走 HTTP)
    - cc_proxy 默认仍透传 /api/cc/health → /cc/health (= pty_routes 那个), 不影响.
    - BOSS SIGHT 块 3: 报告 waker / aggregator 接通状态 (`boss_sight` 字段) 给 e2e 测试断言用
    """
    chat_count = pty_count = 0
    try:
        from .chat import get_chat_manager
        chat_count = len(get_chat_manager().list_meta())
    except Exception:
        pass
    try:
        from .pty import get_manager
        pty_count = len(get_manager().list_meta())
    except Exception:
        pass
    # 块 3: 报告 waker 接通情况
    boss_sight = {
        "waker_attached": bool(getattr(app.state, "boss_sight_waker", None)),
        "aggregator_attached": bool(getattr(app.state, "boss_sight_aggregator", None)),
        "subscriber_count": 0,
    }
    try:
        from .chat import get_chat_manager
        boss_sight["subscriber_count"] = len(get_chat_manager()._event_subscribers)
    except Exception:
        pass
    return {
        "status": "ok",
        "daemon_pid": app.state.daemon_pid,
        "daemon_port": app.state.daemon_port,
        "started_at": app.state.started_at,
        "uptime_s": time.time() - app.state.started_at,
        "chat_session_count": chat_count,
        "pty_session_count": pty_count,
        "boss_sight": boss_sight,
    }


@app.websocket("/cc/echo")
async def cc_echo(ws: WebSocket) -> None:
    """阶段二骨架 echo WebSocket — 给反向代理跑双向桥接基线测试用.

    阶段三跑通后这个端点保留, 阶段六加进 dogfood 测试场景做 RTT 基线.
    """
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_text()
            await ws.send_text(f"echo:{msg}")
    except WebSocketDisconnect:
        return
