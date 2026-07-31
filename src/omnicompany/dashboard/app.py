# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.app.fastapi_assembler.py"
"""FastAPI dashboard for omnicompany — 仅做 lifespan + 路由装载 + 静态资源.

阶段 9 ([2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE) 把原本 1100+ 行的 app.py 拆成
controlplane/ 子模块. 本文件 ≤ 100 行 (lifespan + 路由装载 + 静态资源 + index 路由).

控制面 router 全部走 controlplane/<topic>.py + 反向代理走 controlplane/cc_proxy.py
(chat / pty 真业务在独立 ccdaemon 进程, 8201). dashboard 进程开 --reload 安全自更新.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import pathlib
import subprocess
import sys
import time
from pathlib import Path

# 显式加载项目根 .env (THE_COMPANY_API_KEY 等), 跟 cli/main.py 行为一致
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parents[3] / ".env")
except ImportError:
    pass

# Windows: 隐藏本进程(8210) spawn 的所有子进程 console 窗口。本进程被 DETACHED_PROCESS 启动
# (无 console), 故任何 git/pytest/python 子进程都会被分配新前台窗口抢焦点(用户硬规则: 禁止前台跳窗)。
# ccdaemon 早有此 monkey-patch 但只在 8201 进程装; 8210 一直漏。在任何 router 装载前装一次, 非 win 自动 noop。
try:
    from omnicompany.dashboard.ccdaemon import _subprocess_hide as _sub_hide
    _sub_hide.install_subprocess_hide()
except Exception:  # noqa: BLE001 — 隐藏窗口尽力而为, 失败不该挡 dashboard 启动
    pass

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

logger = logging.getLogger(__name__)

_DASHBOARD_ROOT = Path(__file__).resolve().parent
_STATIC_DIR = _DASHBOARD_ROOT / "static"

# 网页审阅 — 同源反向代理目标。把运行中的 walker-game 开发服务(base=/walker-game/, 默认 5176)
# 挂到 dashboard 同源路径 /walker-game/*, 让审阅 iframe 与 dashboard 同源, 圈选元素/快照才能
# 读到 iframe 内容(浏览器同源策略)。游戏侧用 `npm run dev:dashboard` 启动。
# 这是"生产缺口"的补丁: vite dev 有代理, 但用户实际看的是后端 serve 的构建版, 故后端也要代理。
_WALKER_GAME_UPSTREAM = os.environ.get("OMNI_WALKER_GAME_URL", "http://127.0.0.1:5176").rstrip("/")
_VILO_DEMO_UPSTREAM = os.environ.get("OMNI_VILO_DEMO_URL", "http://127.0.0.1:8892").rstrip("/")
_VILO_OS_UPSTREAM = os.environ.get("OMNI_VILO_OS_URL", "http://127.0.0.1:5186").rstrip("/")
# 叙事工作室 narrative_studio(:8330)同源反向代理目标 —— 让审阅 iframe 与 dashboard 同源,
# 圈选/快照能读到内容;strip 前缀 + 转发全方法(落地层编辑写回 wiki 走 POST/PUT/DELETE)。
_NARRATIVE_STUDIO_UPSTREAM = os.environ.get("OMNI_NARRATIVE_STUDIO_URL", "http://127.0.0.1:8330").rstrip("/")
# 共享、带连接池/keep-alive 的 httpx 客户端 —— vite dev 把页面拆成几百个小模块逐个请求,
# 若每个请求新建 client(无 keep-alive)会慢到十几秒; 共享池后回到 ~1-2s。懒建, shutdown 关。
_walker_client: "httpx.AsyncClient | None" = None
_vilo_demo_client: "httpx.AsyncClient | None" = None
_vilo_os_client: "httpx.AsyncClient | None" = None
_narrative_client: "httpx.AsyncClient | None" = None


def _get_walker_client() -> "httpx.AsyncClient":
    global _walker_client
    if _walker_client is None:
        _walker_client = httpx.AsyncClient(
            base_url=_WALKER_GAME_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
        )
    return _walker_client


def _get_vilo_demo_client() -> "httpx.AsyncClient":
    global _vilo_demo_client
    if _vilo_demo_client is None:
        _vilo_demo_client = httpx.AsyncClient(
            base_url=_VILO_DEMO_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
        )
    return _vilo_demo_client


def _get_vilo_os_client() -> "httpx.AsyncClient":
    global _vilo_os_client
    if _vilo_os_client is None:
        _vilo_os_client = httpx.AsyncClient(
            base_url=_VILO_OS_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
        )
    return _vilo_os_client


def _get_narrative_client() -> "httpx.AsyncClient":
    global _narrative_client
    if _narrative_client is None:
        _narrative_client = httpx.AsyncClient(
            base_url=_NARRATIVE_STUDIO_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
        )
    return _narrative_client


# ── 网页审阅托管中心: 懒启动注册表 ─────────────────────────────────────────────
# 审阅 iframe 打开某 app 的 live_url 时, 若其后端没在跑, 由本进程按标准命令把它拉起来
# (无窗口——subprocess.Popen 已被上面的 _subprocess_hide 全局加 CREATE_NO_WINDOW),
# 探活就绪后再代理。这样"应用意外关闭→点开审阅材料→自动按标准方式启动并打开"。
# 参考 AIWorkSpace app 中心 ports.json 的 lazy-spawn 范式(ready_path 探活 / 点击才起)。
_REPO_ROOT = _DASHBOARD_ROOT.parents[2]  # .../omnicompany
_WS_ROOT = _REPO_ROOT.parent             # .../WindowsWorkspace(webworks 等是 omnicompany 的同级)

_HOSTED_APPS: "dict[str, dict]" = {
    "narrative-studio": {
        # v2 四期 D7(DEC-2026-07-05-025/030): 网页壳已退役, 本条目只作为"叙事内容引擎"的
        # 启动配置存在(api/* 反代的数据通路), 不再是托管中心的页面条目(page_retired)。
        "name": "叙事内容引擎(narrative-studio, 仅 API)",
        "page_retired": True,
        "upstream": _NARRATIVE_STUDIO_UPSTREAM,
        "ready_path": "/api/project",
        "start": [sys.executable, "-m", "omnicompany.packages.narrative_studio",
                  "serve", "--port", "8330"],
        "cwd": str(_REPO_ROOT),
        "env": {"PYTHONPATH": "src"},  # PYTHONPATH 的相对值按仓根解析(见下)
    },
    "walker-game": {
        "name": "行者无乡 walker-game",
        "upstream": _WALKER_GAME_UPSTREAM,        # :5176
        "ready_path": "/walker-game/",             # vite --base /walker-game/
        "start": "npm run dev:dashboard",          # vite --host 127.0.0.1 --port 5176 --base /walker-game/
        "shell": True,                              # npm 在 Windows 是 .cmd, 需经 shell
        "cwd": str(_WS_ROOT / "webworks" / "apps" / "walker-game"),
        "wait_secs": 60.0,                          # vite 冷启可能慢, 放宽
    },
    "vilo-demo": {
        "name": "Vilo demo (tabletop)",
        "upstream": _VILO_DEMO_UPSTREAM,           # :8892
        "ready_path": "/",                          # http.server 根目录列表=200
        "start": [sys.executable, "-m", "http.server", "8892"],
        "cwd": str(_WS_ROOT / "webworks"),         # 从 webworks 根起服务
    },
    "vilo-os": {
        "name": "Vilo OS on Web",
        "upstream": _VILO_OS_UPSTREAM,
        "ready_path": "/vilo-os/",
        "start": "npm run dev:dashboard",
        "shell": True,
        "cwd": str(_WS_ROOT / "webworks" / "apps" / "vilo-os"),
        "wait_secs": 60.0,
    },
}

_host_locks: "dict[str, asyncio.Lock]" = {}


async def _host_health_ok(appcfg: dict) -> bool:
    """轻量探活: ready_path 返回 <500 即视为就绪。"""
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(appcfg["upstream"] + appcfg.get("ready_path", "/"))
            return r.status_code < 500
    except Exception:  # noqa: BLE001
        return False


async def _ensure_hosted_app(app_id: str, *, wait_secs: float = 30.0) -> bool:
    """确保托管 app 在跑: 已就绪→True; 没跑→按注册表命令无窗口拉起, 探活至就绪。

    并发安全: 每 app 一把锁, 拿不到锁的请求等首个请求把它拉起来即可。
    """
    appcfg = _HOSTED_APPS.get(app_id)
    if not appcfg:
        return False
    if await _host_health_ok(appcfg):
        return True
    if not appcfg.get("start"):
        return False  # 注册了但没给启动命令, 无法托管
    lock = _host_locks.setdefault(app_id, asyncio.Lock())
    async with lock:
        if await _host_health_ok(appcfg):  # 等锁期间别的请求已把它拉起
            return True
        env = dict(os.environ)
        for k, v in appcfg.get("env", {}).items():
            env[k] = str(_REPO_ROOT / v) if k == "PYTHONPATH" else v
        try:
            # Popen 已被 _subprocess_hide 全局 patch 成 CREATE_NO_WINDOW, 不弹控制台。
            # shell=True 用于 npm 这类 Windows .cmd 命令(start 为字符串)。
            # Windows 下经 `cmd /c start /b` 分离父子关系: `omni dashboard restart` 用
            # taskkill /T 按进程树杀, 不分离的话每次重启 dashboard 都连带杀掉托管 app
            # (2026-07-04 实锤: 工作室被反复误杀 → 下个访问者付 30-60s 冷启动 + 死 502 面板)。
            start_cmd = appcfg["start"]
            use_shell = bool(appcfg.get("shell"))
            if os.name == "nt":
                if use_shell:
                    start_cmd = f'start "" /b {start_cmd}'
                else:
                    start_cmd = ["cmd.exe", "/d", "/c", "start", "", "/b", *[str(a) for a in start_cmd]]
            subprocess.Popen(
                start_cmd, cwd=appcfg.get("cwd"), env=env,
                shell=use_shell,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:  # noqa: BLE001
            logger.exception("lazy-start failed for hosted app %s", app_id)
            return False
        deadline = time.monotonic() + float(appcfg.get("wait_secs", wait_secs))
        while time.monotonic() < deadline:
            await asyncio.sleep(0.5)
            if await _host_health_ok(appcfg):
                logger.info("lazy-started hosted app %s", app_id)
                return True
        logger.warning("hosted app %s spawned but not ready in %ss", app_id, wait_secs)
        return False


def _lazy_boot_page(display_name: str) -> Response:
    """上游未起时给 HTML 导航的自愈页: 立即返回, 每 2s 自动重试, 直到上游就绪。

    以前是同步等 ensure(最长 30-60s)再回 502 —— iframe 拿到 502 就永远停在死页,
    用户视角"连接无法起效"(2026-07-04 实锤)。现在: 秒回启动页 + 后台拉起 + 自动刷新自愈。
    """
    html = (
        '<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="2">'
        "<style>body{background:#14161a;color:#8b94a3;font:13px 'Segoe UI',sans-serif;"
        "display:grid;place-items:center;height:100vh;margin:0}"
        ".spin{width:16px;height:16px;border:2px solid #334155;border-top-color:#6ea8fe;"
        "border-radius:50%;display:inline-block;vertical-align:-3px;margin-right:8px;"
        "animation:s .9s linear infinite}@keyframes s{to{transform:rotate(360deg)}}</style></head>"
        f'<body><div><span class="spin"></span>{display_name} 启动中… 本页每 2 秒自动重试</div></body></html>'
    )
    return Response(content=html, status_code=200, media_type="text/html; charset=utf-8")


def _wants_html(request: Request) -> bool:
    return request.method == "GET" and "text/html" in (request.headers.get("accept") or "")


def _load_domains_on_startup() -> None:
    """启动时加载私域节点 (可拔插). 无 OMNI_DOMAINS 时静默跳过."""
    try:
        from omnicompany.runtime.storage.domain_loader import load_domains_from_env, load_all_domains
        from omnicompany.dashboard.controlplane._db_helpers import resolve_db_dir

        sem_db = resolve_db_dir() / "semantic_network.db"
        if not sem_db.exists():
            return
        results = load_domains_from_env(sem_db)
        # 兜底: 项目本地 domains/ (config/domains.yaml)
        local_cfg = Path.cwd() / "config" / "domains.yaml"
        if local_cfg.exists() and not os.environ.get("OMNI_DOMAINS"):
            extra = load_all_domains(local_cfg, sem_db, base_dir=Path.cwd())
            results.update(extra)
        if results:
            total = sum(len(v) for v in results.values())
            logger.info("dashboard: loaded %d private-domain nodes from %d domain(s)", total, len(results))
    except Exception as e:
        logger.debug("dashboard: domain load skipped: %s", e)


app = FastAPI(title="OmniCompany Dashboard", version="0.3.1")

# CORS: 默认放行 vite dev(5173) + LOFA 安卓端 WebView origin。
# LOFA(局域网 only)的 Capacitor 壳从 capacitor://localhost / http://localhost 跨源调本机 API
# (仅当 App 走"打包 SPA"路线; M1 走"WebView 直指 PC URL"为同源, 无需 CORS, 但提前放行无害)。
# 可用 OMNI_DASHBOARD_CORS_ORIGINS(逗号分隔)覆盖默认。
_default_cors_origins = [
    "http://localhost:5173",
    "http://localhost",
    "https://localhost",
    "capacitor://localhost",
    "ionic://localhost",
    # poof(Tauri v2)悬浮层 webview origin —— 统一捕获从 poof fetch 本机 captures 端点。
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
]
_cors_env = os.environ.get("OMNI_DASHBOARD_CORS_ORIGINS", "").strip()
_cors_origins = (
    [o.strip() for o in _cors_env.split(",") if o.strip()] if _cors_env else _default_cors_origins
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ImmutableStaticFiles(StaticFiles):
    """/assets 下的文件是 vite 产物, 文件名带内容哈希(改内容必改名), 可放心打永久缓存,
    远程(WLAN)访问不必每次都重新拉 2.5MB 主 JS 包。"""

    def file_response(self, *args, **kwargs) -> Response:
        # 注意: 基类 file_response 是同步方法(被 async get_response 里同步调用、不 await),
        # 这里若误写成 async def, self.file_response(...) 会返回一个没被 await 的 coroutine
        # 而不是 Response, 静态文件请求会全部炸掉 —— 必须保持同步签名。
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class PathScopedGZip:
    """纯 ASGI 中间件 —— 只对静态资源 + SPA 入口页做 gzip, 其余一律直通。

    这里不能用全局 GZipMiddleware: dashboard 里挂了 SSE(/api/... 流式)和反向代理路由,
    全局 gzip 会缓冲/破坏这些流式响应。白名单只列可安全压缩的路径(而非拉黑名单排除流式路径),
    避免以后新增 SSE/代理路由时被漏判进压缩。
    """

    def __init__(self, app) -> None:
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=1024, compresslevel=6)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and (
            scope["path"].startswith("/assets/")
            or scope["path"] in ("/", "/chat-standalone", "/review-stage")
        ):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


app.add_middleware(PathScopedGZip)

# Production build assets (output of `npm run build` in frontend/)
_assets_dir = _STATIC_DIR / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", ImmutableStaticFiles(directory=str(_assets_dir)), name="assets")

# 静态 icon (LLM provider logo SVG 等), 跟 claudecodeui 上游路径对齐 (/icons/*.svg)
_icons_dir = _STATIC_DIR / "icons"
if _icons_dir.is_dir():
    app.mount("/icons", StaticFiles(directory=str(_icons_dir)), name="icons")


# ── 控制面路由装载 ([2026-05-09] D1 + 阶段 9 拆离) ──
# 全部走 controlplane/ 子模块. cc_proxy 反向代理到 ccdaemon 进程, 其他端点本进程跑.
# (module_path, attr_name, prefix) — prefix=None 表 router 自身定义了 prefix.
_CONTROLPLANE_ROUTERS: list[tuple[str, str, str | None]] = [
    # 已存在
    ("omnicompany.dashboard.controlplane.ide",         "ide_router",         "/api/v2"),
    ("omnicompany.dashboard.controlplane.workers",     "workers_router",     "/api"),
    ("omnicompany.dashboard.controlplane.plans",       "plans_router",       "/api"),
    ("omnicompany.dashboard.controlplane.annotations", "annotations_router", "/api"),
    ("omnicompany.dashboard.controlplane.catalogue",   "catalogue_router",   "/api"),
    ("omnicompany.dashboard.controlplane.notes",       "notes_router",       "/api"),
    ("omnicompany.dashboard.controlplane.system",      "system_router",      "/api"),
    ("omnicompany.dashboard.controlplane.cc_proxy",    "cc_proxy_router",    None),  # 自身 /api/cc
    ("omnicompany.dashboard.controlplane.boss_sight_proxy", "boss_sight_proxy_router", None),  # 自身 /api/boss-sight
    ("omnicompany.dashboard.controlplane.registry",    "registry_router",    "/api/v2"),
    # 探索路径可视化/决策树 ([2026-06-27] 见 controlplane/material_graph.py + EXPLORATION-PATH-VIZ)
    ("omnicompany.dashboard.controlplane.material_graph", "material_graph_router", "/api/v2"),
    ("omnicompany.dashboard.controlplane.lock",        "lock_router",        "/api/v2"),
    ("omnicompany.dashboard.controlplane.sandbox",     "sandbox_router",     "/api/v2"),
    ("omnicompany.dashboard.controlplane.meta_io",     "meta_io_router",     "/api/v2"),
    ("omnicompany.dashboard.controlplane.llm",         "llm_router",         "/api/v2"),
    ("omnicompany.dashboard.controlplane.chatinterface_stubs", "chatinterface_stubs_router", None),
    ("omnicompany.dashboard.controlplane.external_agents", "external_agents_router", "/api/v2"),
    # 阶段 9 拆离
    ("omnicompany.dashboard.controlplane.events",      "events_router",      "/api"),
    # 免重启更新: ui/ext 版本信号 ([2026-06-11], 见 controlplane/dev_reload.py)
    ("omnicompany.dashboard.controlplane.dev_reload",  "dev_reload_router",  "/api"),
    # 项目工作板 ([2026-06-12] 首页重置为项目卡片, 见 controlplane/projects.py)
    ("omnicompany.dashboard.controlplane.projects",    "projects_router",    "/api"),
    # 技能+管线清单 ([2026-07-06] 项目页「技能」页签, 见 controlplane/skills.py)
    ("omnicompany.dashboard.controlplane.skills",      "skills_router",      "/api"),
    # 项目文件目录树 ([2026-07-06] 项目页「文件」页签重做, 见 controlplane/project_fs.py)
    ("omnicompany.dashboard.controlplane.project_fs",  "project_fs_router",  "/api"),
    # 统一定时调度「定时任务」视图 ([2026-06-24] 见 controlplane/cron.py)
    ("omnicompany.dashboard.controlplane.cron",        "cron_router",        "/api"),
    ("omnicompany.dashboard.controlplane.lan_access",  "lan_access_router",  None),
    ("omnicompany.dashboard.controlplane.local_services", "local_services_router", None),
    # plan audit 网页端点 ([2026-06-19] 三点菜单「跑 audit」, 见 controlplane/plan_audit_routes.py)
    ("omnicompany.dashboard.controlplane.plan_audit_routes", "plan_audit_router", "/api"),
    ("omnicompany.dashboard.controlplane.nodes",       "nodes_router",       "/api"),
    ("omnicompany.dashboard.controlplane.traces",      "traces_router",      "/api"),
    ("omnicompany.dashboard.controlplane.health",      "health_router",      "/api"),
    ("omnicompany.dashboard.controlplane.evolution",   "evolution_router",   "/api"),
    ("omnicompany.dashboard.controlplane.semantic",    "semantic_router",    "/api"),
    # LOFA 安卓远程端日志回传 ([2026-06-25] 见 controlplane/android.py)
    ("omnicompany.dashboard.controlplane.android",     "android_router",     None),
    # LOFA 实机操作台反代: devview/ws-scrcpy 收进 8210, 对外只一个口 ([2026-06-28] 见 lofa_proxy.py)
    ("omnicompany.dashboard.controlplane.lofa_proxy",  "lofa_proxy_router",  None),
    # 远程节点一键引导: bootstrap.bat 下载口 + 落地页 + 装完回报 register + 节点列表 ([2026-07-25] 见 controlplane/remote_nodes.py)
    ("omnicompany.dashboard.controlplane.remote_nodes", "remote_nodes_router", None),
    # overlay-shell 笔记 HTTP 桥: 网页/手机端共用桌面 overlay-shell 的 BlockSuite 笔记.
    ("omnicompany.dashboard.controlplane.overlay_notes",  "overlay_notes_router",  None),
    # 受控文件桥: 远端上传到固定暂存区 + 许可根目录的只读反向浏览.
    ("omnicompany.dashboard.controlplane.file_bridge",  "file_bridge_router",  None),
]

for _mod_path, _attr, _prefix in _CONTROLPLANE_ROUTERS:
    try:
        _mod = importlib.import_module(_mod_path)
        _router = getattr(_mod, _attr)
        if _prefix is None:
            app.include_router(_router)
        else:
            app.include_router(_router, prefix=_prefix)
    except Exception as _e:
        logger.warning("controlplane router not loaded: %s.%s (%s: %s)",
                       _mod_path, _attr, type(_e).__name__, _e)

@app.on_event("startup")
async def _startup() -> None:
    _load_domains_on_startup()

    # 初始化 IDE 事件总线和会话管理器
    # Move 8: 不再传 db_path, 由引擎落到 unified data/ide_events.db
    try:
        from omnicompany.bus.sqlite import SQLiteBus
        from omnicompany.dashboard.controlplane.ide_session import IDESessionManager

        bus = SQLiteBus(basename="ide_events.db")
        await bus.connect()
        app.state.ide_bus = bus
        app.state.ide_session_manager = IDESessionManager(bus)
    except Exception as e:
        logger.warning("IDE bus init failed: %s", e)

    # 预热日常主力托管 app: 别让第一个访问者付 30-60s 冷启动(2026-07-04 实锤)。
    # 后台任务, 不阻塞 dashboard 启动; 已在跑则 ensure 是零成本探活。
    try:
        asyncio.get_running_loop().create_task(_ensure_hosted_app("narrative-studio"))
    except Exception as e:  # noqa: BLE001
        logger.warning("hosted app prewarm failed: %s", e)

    # 审阅材料封面在提交时落持久队列；8210 Dashboard 只负责后台消费。
    # worker 的初始化/渲染失败均不得影响 Dashboard 启动与现有控制链路。
    try:
        from omnicompany.dashboard.boss_sight.reviewstage.preview_queue import (
            run_preview_worker,
        )
        from omnicompany.dashboard.boss_sight.reviewstage.routes import (
            get_store as get_reviewstage_store,
        )

        preview_stop = asyncio.Event()
        app.state.review_preview_stop = preview_stop
        app.state.review_preview_task = asyncio.create_task(
            run_preview_worker(get_reviewstage_store().root, stop_event=preview_stop),
            name="reviewstage-preview-worker",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("review preview worker init failed: %s", e)


@app.on_event("shutdown")
async def _shutdown() -> None:
    preview_stop = getattr(app.state, "review_preview_stop", None)
    preview_task = getattr(app.state, "review_preview_task", None)
    if preview_stop is not None:
        preview_stop.set()
    if preview_task is not None:
        preview_task.cancel()
        try:
            await preview_task
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("review preview worker shutdown failed: %s", e)
    bus = getattr(app.state, "ide_bus", None)
    if bus:
        await bus.close()
    global _walker_client, _vilo_demo_client, _vilo_os_client
    if _walker_client is not None:
        await _walker_client.aclose()
        _walker_client = None
    if _vilo_demo_client is not None:
        await _vilo_demo_client.aclose()
        _vilo_demo_client = None
    if _vilo_os_client is not None:
        await _vilo_os_client.aclose()
        _vilo_os_client = None


# 产物缺失时返回的"构建中"自愈页(而非裸 503 JSON)。
# 背景: vite build 配 emptyOutDir=true, 每次重建会先清空 static/ 再写, 中间有个
# index.html 缺失的窗口; 此时 `/` 返回裸 503 → iframe 里没有任何 JS → 卡死, 用户只能
# 重开 VSCode 扩展。这里改成返回一个会自轮询 /api/dev/versions 的小页面: 产物一回来
# (ui token 不再是 absent) 就自刷, 不需要手动重开。devReload.ts 是产物就绪后的常态自刷,
# 本页是"产物缺失窗口"的兜底自愈, 两者互补。
_BUNDLE_BUILDING_HTML = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OmniChat — 构建中</title>
<style>
  html,body{margin:0;height:100%;background:#0f0f0f;color:#d6deeb;
    font-family:var(--vscode-font-family,Segoe UI,system-ui,sans-serif)}
  .wrap{height:100%;display:grid;place-items:center;padding:24px;box-sizing:border-box}
  .panel{width:min(460px,100%);border:1px solid #233047;background:#111827;border-radius:8px;padding:20px}
  .spin{width:18px;height:18px;border-radius:50%;border:2px solid #334155;border-top-color:#60a5fa;
    animation:spin .9s linear infinite;margin-bottom:12px}
  .t{font-size:15px;font-weight:650;margin-bottom:6px}
  .m{font-size:13px;color:#9fb0c6;line-height:1.6}
  code{color:#cbd5e1;background:#0b1220;padding:1px 5px;border-radius:4px}
  @keyframes spin{to{transform:rotate(360deg)}}
</style></head>
<body><div class="wrap"><div class="panel">
  <div class="spin"></div>
  <div class="t">前端产物正在构建</div>
  <div class="m">正在等待 <code>npm run build</code> 产物就绪 / 后端重启完成。
    <b>本页会自动刷新</b>, 无需手动重开扩展。</div>
</div></div>
<script>
// 每 1.2s 探一次产物版本; ui token 不再以 'absent' 开头(产物已落盘)就刷新本页。
(function(){
  var POLL=1200;
  function tick(){
    fetch('/api/dev/versions',{cache:'no-store'})
      .then(function(r){return r.ok?r.json():null;})
      .then(function(d){ if(d&&typeof d.ui==='string'&&d.ui.indexOf('absent')!==0){location.reload();} })
      .catch(function(){});
  }
  setInterval(tick,POLL); tick();
})();
</script></body></html>"""


@app.get("/")
async def index() -> Response:
    """Serve the production vite build (output of `npm run build` in frontend/).

    For dev, run vite at http://localhost:5173 which proxies /api to here.
    For production / no-frontend-installed, this returns the built static bundle.
    产物缺失(常见于 rebuild 清空 static/ 的窗口)时返回会自愈的"构建中"页, 而非裸 503。
    """
    index_html = _STATIC_DIR / "index.html"
    if not index_html.is_file():
        return HTMLResponse(_BUNDLE_BUILDING_HTML, status_code=503)
    return FileResponse(str(index_html))


@app.get("/omni-capture-beacon.js")
async def omni_capture_beacon() -> Response:
    """统一捕获信标(根级静态 JS)。index.html 用 <script src="/omni-capture-beacon.js"> 引它,
    但 dashboard 只 mount 了 /assets 与 /icons, 根级文件没路由 → 之前 404 → 信标永不运行 →
    surfaces 一直空 → 截图 MD 解析不出 page/target。这里补上路由。"""
    f = _STATIC_DIR / "omni-capture-beacon.js"
    if not f.is_file():
        return Response(status_code=404)
    # 信标是"活"脚本(随捕获能力升级), 绝不缓存 —— 否则浏览器缓存旧版, 普通刷新拿不到新信标,
    # 就看不到新增的 content_els/hover 字段(实测踩过: 服务重启了、页面刷了却仍是旧信标)。
    return FileResponse(
        str(f), media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"},
    )


@app.get("/chat-standalone")
async def chat_standalone() -> FileResponse:
    """裸聊天界面 — 同一 SPA bundle, 前端 main.tsx 按 pathname 分流到 ChatStandalone.

    SPA 路由: 后端只负责返回 index.html, 前端 JS 看 window.location.pathname
    决定渲染哪个根组件 (App 完整外壳 vs ChatStandalone 裸 chat).

    用途: 浏览器或 VSCode Simple Browser 嵌入时单独看 chat, 不带 IDE 形态外壳.
    """
    return await index()


@app.get("/review-stage")
async def review_stage() -> FileResponse:
    """老审阅台路由兼容 — 同一 SPA bundle; standalone 审阅台已退役 (R4).

    前端 entryRoute 把 /review-stage?material=X 重定向成驾驶舱 deeplink
    (?open_type=review_material&open_id=X; 无 material 参数则开审阅队列),
    路由保留只为让历史 open_ref / 书签链接不死.
    """
    return await index()


@app.api_route("/walker-game", methods=["GET"])
@app.api_route("/walker-game/{path:path}", methods=["GET"])
async def walker_game_proxy(request: Request, path: str = "") -> Response:
    """同源反向代理到运行中的 walker-game 开发服务(见 _WALKER_GAME_UPSTREAM)。

    上游以 base=/walker-game/ 提供, 故资源 URL 都在 /walker-game/* 下; 这里整体转发,
    保持同源, 让网页审阅面板的圈选元素/快照可用。HMR websocket 不代理(审阅用不到),
    游戏侧热更新失效不影响查看, 面板自带刷新按钮。上游不可达时返回 502。
    """
    upstream = f"/walker-game/{path}"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    try:
        # identity 编码避免上游 gzip 与我们重写 content-length 冲突
        resp = await _get_walker_client().get(upstream, headers={"accept-encoding": "identity"})
    except httpx.RequestError:
        # 上游没在跑。HTML 导航 → 秒回自愈启动页(同叙事工作室, 见 _lazy_boot_page); 其余阻塞拉起重试。
        if _wants_html(request):
            asyncio.get_running_loop().create_task(_ensure_hosted_app("walker-game"))
            return _lazy_boot_page("行者无乡 walker-game")
        if await _ensure_hosted_app("walker-game"):
            try:
                resp = await _get_walker_client().get(upstream, headers={"accept-encoding": "identity"})
            except httpx.RequestError as exc2:
                raise HTTPException(status_code=502, detail=f"walker-game 已尝试启动但仍不可达: {exc2}")
        else:
            raise HTTPException(
                status_code=502,
                detail="walker-game 启动失败或超时。手动: 在 webworks/apps/walker-game 跑 `npm run dev:dashboard`",
            )
    drop = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
    )


_NARRATIVE_PAGE_RETIRED_HTML = (
    '<!doctype html><html><head><meta charset="utf-8"><title>已退役</title>'
    "<style>body{background:#14161a;color:#8b94a3;font:14px 'Segoe UI',sans-serif;"
    "display:grid;place-items:center;height:100vh;margin:0;text-align:center;line-height:1.8}"
    "b{color:#cdd6e4}</style></head><body><div>"
    "<b>叙事工作室页面已退役</b>(统一设计工作室 v2)<br>"
    "浏览/审阅走驾驶舱: 项目 <b>vilo</b> → <b>阅读视图</b>(材料展示框架·叙事展示区)。<br>"
    "内容引擎 API(/narrative-studio/api/*)永久保留。"
    "</div></body></html>"
)


@app.api_route("/narrative-studio", methods=["GET", "POST", "PUT", "DELETE"])
@app.api_route("/narrative-studio/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def narrative_studio_proxy(request: Request, path: str = "") -> Response:
    """叙事内容引擎反代(默认 :8330)。

    v2 四期 D7(DEC-2026-07-05-025/030): **页面代理已退役** —— 只保留 api/* 反代
    (阅读视图叙事渲染器的数据通路, 引擎未跑时自动拉起); 非 api 路径返回 410 指路页。
    strip 前缀: /narrative-studio/api/x -> /api/x。转发全方法 + 请求体
    (agent 经 API 做结构化编辑/写回 vilo wiki 的通道不变)。
    """
    if not (path == "api" or path.startswith("api/")):
        return Response(content=_NARRATIVE_PAGE_RETIRED_HTML, status_code=410,
                        media_type="text/html; charset=utf-8")
    upstream = f"/{path}" if path else "/"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    body = await request.body()
    fwd = {k: v for k, v in request.headers.items()
           if k.lower() not in {"host", "accept-encoding", "content-length"}}
    fwd["accept-encoding"] = "identity"
    try:
        resp = await _get_narrative_client().request(
            request.method, upstream, content=body, headers=fwd,
        )
    except httpx.RequestError:
        # 上游没在跑。HTML 导航(iframe/页签首请求)→ 秒回自愈启动页, 后台拉起, 页自刷到就绪;
        # 其余(API/资源)→ 阻塞拉起后重试一次(老行为)。
        if _wants_html(request):
            asyncio.get_running_loop().create_task(_ensure_hosted_app("narrative-studio"))
            return _lazy_boot_page("叙事工作室")
        if await _ensure_hosted_app("narrative-studio"):
            try:
                resp = await _get_narrative_client().request(
                    request.method, upstream, content=body, headers=fwd,
                )
            except httpx.RequestError as exc2:
                raise HTTPException(
                    status_code=502,
                    detail=f"narrative-studio 已尝试启动但仍不可达: {exc2}",
                )
        else:
            raise HTTPException(
                status_code=502,
                detail=(
                    "narrative-studio 启动失败或超时。手动: "
                    "PYTHONPATH=src python -m omnicompany.packages.narrative_studio serve --port 8330"
                ),
            )
    drop = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
    )


@app.get("/api/host/apps")
async def host_apps() -> "dict":
    """托管中心: 列已注册 app 及其运行状态(供审阅前端展示卡片/在线点)。"""
    out = {}
    for aid, cfg in _HOSTED_APPS.items():
        if cfg.get("page_retired"):
            continue  # v2 四期 D7: 页面条目摘除(引擎启动配置保留, 只服务 api/* 反代)
        out[aid] = {
            "name": cfg.get("name"),
            "upstream": cfg.get("upstream"),
            "running": await _host_health_ok(cfg),
            "hostable": bool(cfg.get("start")),  # 有启动命令=可被托管中心拉起
        }
    return {"apps": out}


@app.post("/api/host/{app_id}/start")
async def host_start(app_id: str) -> "dict":
    """托管中心: 按标准命令把某 app 拉起(已在跑则直接 True)。点击/前端可调。"""
    if app_id not in _HOSTED_APPS:
        raise HTTPException(status_code=404, detail=f"未注册的托管 app: {app_id}")
    running = await _ensure_hosted_app(app_id)
    return {"app_id": app_id, "running": running}


@app.api_route("/vilo-os", methods=["GET"])
@app.api_route("/vilo-os/{path:path}", methods=["GET"])
async def vilo_os_proxy(request: Request, path: str = "") -> Response:
    """通过 dashboard 的 HTTPS 入口提供 Vilo OS，避免单独开放开发端口。"""
    upstream = f"/vilo-os/{path}"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    try:
        resp = await _get_vilo_os_client().get(upstream, headers={"accept-encoding": "identity"})
    except httpx.RequestError:
        if _wants_html(request):
            asyncio.get_running_loop().create_task(_ensure_hosted_app("vilo-os"))
            return _lazy_boot_page("Vilo OS on Web")
        if await _ensure_hosted_app("vilo-os"):
            try:
                resp = await _get_vilo_os_client().get(upstream, headers={"accept-encoding": "identity"})
            except httpx.RequestError as exc2:
                raise HTTPException(status_code=502, detail=f"Vilo OS 已尝试启动但仍不可达: {exc2}")
        else:
            raise HTTPException(
                status_code=502,
                detail="Vilo OS 启动失败或超时。手动: 在 webworks/apps/vilo-os 跑 `npm run dev:dashboard`",
            )
    drop = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
    )


@app.api_route("/vilo-demo", methods=["GET"])
@app.api_route("/vilo-demo/{path:path}", methods=["GET"])
async def vilo_demo_proxy(request: Request, path: str = "") -> Response:
    """同源反向代理到 tabletop-simulator 里的 Vilo 静态 demo。

    tabletop-simulator 由普通 http.server 提供, 不知道 /vilo-demo 前缀, 所以这里需要
    strip prefix: /vilo-demo/data/x.json -> /data/x.json。保持同源后, 审阅 iframe
    才能读到卡牌、事件和聊天气泡 DOM。
    """
    # demo 已迁到 webworks 根下的 /apps/tabletop-simulator/，引擎走相对 ../../packages。
    # 8892 从 webworks 根起服务，所以 /vilo-demo/ 根打到的是目录列表，不是 demo。
    # 把 demo 根重定向到真实子路径(2 层深，相对路径才能在 /vilo-demo/ 前缀下解析)。
    # 兼容历史审阅材料里登记的旧地址 /vilo-demo/?scenario=...，不依赖前端重建/store 重载。
    if path in ("", "/"):
        target = "/vilo-demo/apps/tabletop-simulator/"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=307)

    upstream = f"/{path}" if path else "/"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    try:
        resp = await _get_vilo_demo_client().get(upstream, headers={"accept-encoding": "identity"})
    except httpx.RequestError:
        # 上游没在跑: 托管中心从 webworks 根起 http.server, 就绪后重试一次。
        if await _ensure_hosted_app("vilo-demo"):
            try:
                resp = await _get_vilo_demo_client().get(upstream, headers={"accept-encoding": "identity"})
            except httpx.RequestError as exc2:
                raise HTTPException(status_code=502, detail=f"Vilo demo 已尝试启动但仍不可达: {exc2}")
        else:
            raise HTTPException(
                status_code=502,
                detail="Vilo demo 启动失败或超时。手动: 在 webworks 根跑 `python -m http.server 8892`",
            )
    # 加载链修复(2026-06-15): demo 引擎(ui.js/ui.css/index.js)无版本号, 而 http.server 只发
    # Last-Modified → 浏览器/webview 启发式缓存把旧引擎钉死, "改了看不见"。在代理层根治, 不动源码
    # (index.js 被 walker 的 Vite 共享, 给源码加 ?v= 会破坏它):
    #   1) 一律 no-store + 抹掉校验头(last-modified/etag/...), 引擎资源永不进缓存;
    #   2) 给经代理吐出的 index.html / 引擎入口注入一次性 ?v=<token>, 把已缓存死的旧模块链冲掉。
    token = str(int(time.time() * 1000))
    body = resp.content
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("text/html"):
        text = body.decode("utf-8", "replace")
        text = text.replace("packages/tabletop-engine/ui.css", f"packages/tabletop-engine/ui.css?v={token}")
        text = text.replace("packages/tabletop-engine/index.js", f"packages/tabletop-engine/index.js?v={token}")
        body = text.encode("utf-8")
    elif path.endswith("packages/tabletop-engine/index.js"):
        text = body.decode("utf-8", "replace")
        text = text.replace('"./ui.js"', f'"./ui.js?v={token}"')
        body = text.encode("utf-8")
    drop = {
        "content-encoding", "content-length", "transfer-encoding", "connection",
        "last-modified", "etag", "expires", "cache-control", "pragma",
    }
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop}
    headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    headers["Pragma"] = "no-cache"
    return Response(
        content=body,
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
    )
