# [OMNI] origin=ai-ide ts=2026-05-09 type=infra
# [OMNI] material_id="material:dashboard.app.fastapi_assembler.py"
"""FastAPI dashboard for omnicompany — 仅做 lifespan + 路由装载 + 静态资源.

阶段 9 ([2026-05-09]DASHBOARD-DOGFOOD-RESILIENCE) 把原本 1100+ 行的 app.py 拆成
controlplane/ 子模块. 本文件 ≤ 100 行 (lifespan + 路由装载 + 静态资源 + index 路由).

控制面 router 全部走 controlplane/<topic>.py + 反向代理走 controlplane/cc_proxy.py
(chat / pty 真业务在独立 ccdaemon 进程, 8201). dashboard 进程开 --reload 安全自更新.
"""

from __future__ import annotations

import importlib
import logging
import os
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

logger = logging.getLogger(__name__)

_DASHBOARD_ROOT = Path(__file__).resolve().parent
_STATIC_DIR = _DASHBOARD_ROOT / "static"

# 网页审阅 — 同源反向代理目标。把运行中的 walker-game 开发服务(base=/walker-game/, 默认 5176)
# 挂到 dashboard 同源路径 /walker-game/*, 让审阅 iframe 与 dashboard 同源, 圈选元素/快照才能
# 读到 iframe 内容(浏览器同源策略)。游戏侧用 `npm run dev:dashboard` 启动。
# 这是"生产缺口"的补丁: vite dev 有代理, 但用户实际看的是后端 serve 的构建版, 故后端也要代理。
_WALKER_GAME_UPSTREAM = os.environ.get("OMNI_WALKER_GAME_URL", "http://127.0.0.1:5176").rstrip("/")
_VILO_DEMO_UPSTREAM = os.environ.get("OMNI_VILO_DEMO_URL", "http://127.0.0.1:8892").rstrip("/")
# 共享、带连接池/keep-alive 的 httpx 客户端 —— vite dev 把页面拆成几百个小模块逐个请求,
# 若每个请求新建 client(无 keep-alive)会慢到十几秒; 共享池后回到 ~1-2s。懒建, shutdown 关。
_walker_client: "httpx.AsyncClient | None" = None
_vilo_demo_client: "httpx.AsyncClient | None" = None


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Production build assets (output of `npm run build` in frontend/)
_assets_dir = _STATIC_DIR / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="assets")

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
    # plan audit 网页端点 ([2026-06-19] 三点菜单「跑 audit」, 见 controlplane/plan_audit_routes.py)
    ("omnicompany.dashboard.controlplane.plan_audit_routes", "plan_audit_router", "/api"),
    ("omnicompany.dashboard.controlplane.nodes",       "nodes_router",       "/api"),
    ("omnicompany.dashboard.controlplane.traces",      "traces_router",      "/api"),
    ("omnicompany.dashboard.controlplane.health",      "health_router",      "/api"),
    ("omnicompany.dashboard.controlplane.evolution",   "evolution_router",   "/api"),
    ("omnicompany.dashboard.controlplane.semantic",    "semantic_router",    "/api"),
    # voxelcraft NPC dialog (跨 packages/domains/, 但挂 dashboard 进程上)
    ("omnicompany.packages.domains.voxelcraft.dialog.route", "voxelcraft_dialog_router", "/api"),
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


@app.on_event("shutdown")
async def _shutdown() -> None:
    bus = getattr(app.state, "ide_bus", None)
    if bus:
        await bus.close()
    global _walker_client, _vilo_demo_client
    if _walker_client is not None:
        await _walker_client.aclose()
        _walker_client = None
    if _vilo_demo_client is not None:
        await _vilo_demo_client.aclose()
        _vilo_demo_client = None


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
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"walker-game upstream unreachable ({_WALKER_GAME_UPSTREAM}): {exc}. "
                "在游戏仓库跑 `npm run dev:dashboard` 后重试。"
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
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Vilo demo upstream unreachable ({_VILO_DEMO_UPSTREAM}): {exc}. "
                "在 tabletop-simulator 跑 `python -m http.server 8892 --bind 127.0.0.1` 后重试。"
            ),
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
