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
import hashlib
import importlib
import json
import logging
import os
import pathlib
import re
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

import anyio
import httpx
import websockets
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

_DASHBOARD_ROOT = Path(__file__).resolve().parent
_STATIC_DIR = _DASHBOARD_ROOT / "static"

# 网页审阅 — 同源反向代理目标。把运行中的 walker-game 开发服务(base=/walker-game/, 默认 5176)
# 挂到 dashboard 同源路径 /walker-game/*, 让审阅 iframe 与 dashboard 同源, 圈选元素/快照才能
# 读到 iframe 内容(浏览器同源策略)。游戏侧用 `npm run dev:dashboard` 启动。
# 这是"生产缺口"的补丁: vite dev 有代理, 但用户实际看的是后端 serve 的构建版, 故后端也要代理。
_WALKER_GAME_UPSTREAM = os.environ.get("OMNI_WALKER_GAME_URL", "http://127.0.0.1:5176").rstrip("/")
_VILO_DEMO_UPSTREAM = os.environ.get("OMNI_VILO_DEMO_URL", "http://127.0.0.1:8992").rstrip("/")
_VILO_OS_UPSTREAM = os.environ.get("OMNI_VILO_OS_URL", "http://127.0.0.1:5186").rstrip("/")
# 叙事工作室 narrative_studio(:8330)同源反向代理目标 —— 让审阅 iframe 与 dashboard 同源,
# 圈选/快照能读到内容;strip 前缀 + 转发全方法(落地层编辑写回 wiki 走 POST/PUT/DELETE)。
_NARRATIVE_STUDIO_UPSTREAM = os.environ.get("OMNI_NARRATIVE_STUDIO_URL", "http://127.0.0.1:8330").rstrip("/")
# voxelcraft 资产库只读浏览 API(:8331)同源反向代理目标 —— 项目级资产审阅视图后端半边,
# 仅 api/*(page_retired), 让审阅前端与 dashboard 同源读到库数据/预览。
_voxelcraft_ASSETS_UPSTREAM = os.environ.get("OMNI_voxelcraft_ASSETS_URL", "http://127.0.0.1:8331").rstrip("/")
# voxelcraft 检阅台(:8332)同源反向代理目标 —— proto-world 的 web 检阅入口(取代桌面启动器):
# 状态/起停/进入世界/重置世界/e2e/wiki/证据 集中一页, 服务本体在 proto-world 仓 tools/console。
_voxelcraft_CONSOLE_UPSTREAM = os.environ.get("OMNI_voxelcraft_CONSOLE_URL", "http://127.0.0.1:8332").rstrip("/")
_PROTO_WORLD_ROOT = os.environ.get("OMNI_voxelcraft_PROTO_WORLD", "C:/workspace/game-project")
# Spark WebTwin 实时宿主(:9001)同源反向代理目标 —— 浏览器里直接跑生产 JsSrc 的可玩页面。
# 端口治理规矩 1: 给人用的界面不发新端口, 挂网关路径; 本体绑回环, 这里 strip 前缀转发。
# 页面里所有绝对路径都从 import.meta.url 派生, import map 地址也是相对的, 所以 strip 前缀成立。
_SPARK_WEBTWIN_UPSTREAM = os.environ.get("OMNI_SPARK_WEBTWIN_URL", "http://127.0.0.1:9001").rstrip("/")
_SPARK_WEBTWIN_ROOT = os.environ.get("OMNI_SPARK_WEBTWIN_ROOT", "C:/workspace/spark.workshop/webtwin")  # 2026-09-17 WebTwin 迁出 P4 区（旧路径留 junction）
# `/bside/` 是历史兼容 URL；真实产品和唯一 writer 已切到 Workshop(:5460)。
# 环境变量也改用 Workshop 名，避免旧 OMNI_BSIDE_URL 把路由意外指回退役进程。
_BSIDE_UPSTREAM = os.environ.get("OMNI_WORKSHOP_URL", "http://127.0.0.1:5460").rstrip("/")
# 蓝绿切换（业主 2026-09-21：版本重启尽量不中断别人正在做的内容）：Workshop 的 lifecycle 工具把新实例起在备用端口后，
# 往这个文件写 {"url": "http://127.0.0.1:5470"}，这里按 mtime 感知并换掉 httpx 客户端；文件不在就用上面的常量。
_BSIDE_UPSTREAM_FILE = Path(os.environ.get("OMNI_WORKSHOP_UPSTREAM_FILE", str(Path.home() / "codeweb" / "workshop-upstream.json")))
_bside_upstream_cache: "tuple[float, str] | None" = None


def _bside_upstream() -> str:
    global _bside_upstream_cache
    try:
        mtime = _BSIDE_UPSTREAM_FILE.stat().st_mtime
    except OSError:
        return _BSIDE_UPSTREAM
    if _bside_upstream_cache is not None and _bside_upstream_cache[0] == mtime:
        return _bside_upstream_cache[1]
    url = ""
    try:
        url = str(json.loads(_BSIDE_UPSTREAM_FILE.read_text("utf-8")).get("url") or "").rstrip("/")
    except Exception:
        url = ""
    value = url if url.startswith("http") else _BSIDE_UPSTREAM
    _bside_upstream_cache = (mtime, value)
    return value
# 树屋工作室 treehouse-studio(vite preview :5178)同源反代目标 —— 统一出战挂 8210 /treehouse/ 下,
# strip 前缀转发; 构建产物 base="./" 资源相对引用, 子路径挂载成立。端口治理同 spark-webtwin 规矩。
_TREEHOUSE_UPSTREAM = os.environ.get("OMNI_TREEHOUSE_URL", "http://127.0.0.1:5178").rstrip("/")
# 共享、带连接池/keep-alive 的 httpx 客户端 —— vite dev 把页面拆成几百个小模块逐个请求,
# 若每个请求新建 client(无 keep-alive)会慢到十几秒; 共享池后回到 ~1-2s。懒建, shutdown 关。
_walker_client: "httpx.AsyncClient | None" = None
_treehouse_client: "httpx.AsyncClient | None" = None
_vilo_demo_client: "httpx.AsyncClient | None" = None
_vilo_os_client: "httpx.AsyncClient | None" = None
_narrative_client: "httpx.AsyncClient | None" = None
_voxelcraft_assets_client: "httpx.AsyncClient | None" = None
_voxelcraft_console_client: "httpx.AsyncClient | None" = None
_spark_webtwin_client: "httpx.AsyncClient | None" = None
_bside_client: "httpx.AsyncClient | None" = None
_host_health_client: "httpx.AsyncClient | None" = None


def _get_walker_client() -> "httpx.AsyncClient":
    global _walker_client
    if _walker_client is None:
        _walker_client = httpx.AsyncClient(
            base_url=_WALKER_GAME_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
    return _walker_client


def _get_treehouse_client() -> "httpx.AsyncClient":
    global _treehouse_client
    if _treehouse_client is None:
        _treehouse_client = httpx.AsyncClient(
            base_url=_TREEHOUSE_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
    return _treehouse_client


def _get_vilo_demo_client() -> "httpx.AsyncClient":
    global _vilo_demo_client
    if _vilo_demo_client is None:
        _vilo_demo_client = httpx.AsyncClient(
            base_url=_VILO_DEMO_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
    return _vilo_demo_client


def _get_vilo_os_client() -> "httpx.AsyncClient":
    global _vilo_os_client
    if _vilo_os_client is None:
        _vilo_os_client = httpx.AsyncClient(
            base_url=_VILO_OS_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
    return _vilo_os_client


_bside_client_base: str = ""


def _get_bside_client() -> "httpx.AsyncClient":
    global _bside_client, _bside_client_base
    upstream = _bside_upstream()
    if _bside_client is not None and _bside_client_base != upstream:
        # 上游换了（蓝绿切换）：旧客户端异步关掉，新请求走新实例；在飞的请求由旧实例自己收尾。
        stale = _bside_client
        _bside_client = None
        try:
            asyncio.get_running_loop().create_task(stale.aclose())
        except RuntimeError:
            pass
    if _bside_client is None:
        _bside_client_base = upstream
        _bside_client = httpx.AsyncClient(
            base_url=upstream,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
    return _bside_client


def _get_narrative_client() -> "httpx.AsyncClient":
    global _narrative_client
    if _narrative_client is None:
        _narrative_client = httpx.AsyncClient(
            base_url=_NARRATIVE_STUDIO_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
    return _narrative_client


def _get_voxelcraft_assets_client() -> "httpx.AsyncClient":
    global _voxelcraft_assets_client
    if _voxelcraft_assets_client is None:
        _voxelcraft_assets_client = httpx.AsyncClient(
            base_url=_voxelcraft_ASSETS_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
    return _voxelcraft_assets_client


def _get_voxelcraft_console_client() -> "httpx.AsyncClient":
    global _voxelcraft_console_client
    if _voxelcraft_console_client is None:
        _voxelcraft_console_client = httpx.AsyncClient(
            base_url=_voxelcraft_CONSOLE_UPSTREAM,
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
    return _voxelcraft_console_client


def _get_spark_webtwin_client() -> "httpx.AsyncClient":
    global _spark_webtwin_client
    if _spark_webtwin_client is None:
        _spark_webtwin_client = httpx.AsyncClient(
            base_url=_SPARK_WEBTWIN_UPSTREAM,
            # 生产 ESM 不打包, 一次会话按需拉几百个小模块 + 资产按需回源, 连接池要给足。
            timeout=60.0,
            limits=httpx.Limits(max_keepalive_connections=32, max_connections=96),
            verify=False,
            trust_env=False,
        )
    return _spark_webtwin_client



async def _spark_raw_open(
    client: "httpx.AsyncClient",
    path: str,
    accept_encoding: str,
    conditional: dict[str, str] | None = None,
) -> "httpx.Response":
    """Open an upstream response without decoding or buffering its body.

    ``client.get()`` both decodes and buffers. During a Dashboard rebuild the
    WebTwin page can request thousands of modules/assets concurrently; buffering
    every response retained close to a gigabyte in the controller process.
    ``aiter_raw`` preserves the upstream content-encoding while applying normal
    ASGI backpressure all the way to the browser.

    为什么不在这里压: 试过在本路由里 gzip.compress(18MB), 同步压缩会把 uvicorn 事件循环卡住,
    实测把可玩页面从 1713 节点打到 824 节点、棋盘起不来。压缩要放在不阻塞控制面的地方。
    """
    # **条件请求头必须转发**。不转发的话浏览器每次带着 If-None-Match 来, 上游看不到它、
    # 恒回 200 全量体 —— 重校验永远不可能命中, 缓存等于没有。生产模块有 1108 个, 差别是
    # "重复打开近乎零传输" vs "每次重下一整套"(实测用户经代理那次 2423 个请求)。
    headers = {"accept-encoding": accept_encoding or "identity"}
    headers.update(conditional or {})
    request = client.build_request("GET", path, headers=headers)
    return await client.send(request, stream=True)


async def _relay_stream(resp: "httpx.Response"):
    """边收边转上游响应体；客户端断开（任务被取消）时也要把上游连接还回连接池。"""
    try:
        async for chunk in resp.aiter_raw():
            yield chunk
    finally:
        with anyio.CancelScope(shield=True):
            await resp.aclose()


# Workshop 统一下载服务：文件下载与分块上传是仅有的两条大体积路由（路径已剥掉 /bside 或 /workshop 前缀）。
_BSIDE_DOWNLOAD_FILE_RE = re.compile(r"^/api/workspace/downloads/[^/]+/file$")
_BSIDE_DOWNLOAD_CONTENT_RE = re.compile(r"^/api/workspace/downloads/[^/]+/content$")
# 读/写超时按“两次收发之间的空档”计，不是整次传输时长；慢网下一块 16 MiB 也有充足余量。
_BSIDE_TRANSFER_TIMEOUT = httpx.Timeout(30.0, read=600.0, write=600.0, pool=30.0)


async def _request_body_chunks(request: Request):
    """把客户端请求体边收边交给 httpx，不在驾驶舱里攒整块。"""
    async for chunk in request.stream():
        if chunk:
            yield chunk


def _spark_stream_response(resp: "httpx.Response") -> StreamingResponse:
    drop = {"transfer-encoding", "connection"}
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop}
    return StreamingResponse(
        resp.aiter_raw(),
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
        background=BackgroundTask(resp.aclose),
    )


async def _spark_forward(
    client: "httpx.AsyncClient",
    path: str,
    accept_encoding: str,
    conditional: dict[str, str] | None = None,
) -> Response:
    """取上游并原样转发。抽出来是为了让"换个新客户端重试一次"能复用同一套头处理。"""
    resp = await _spark_raw_open(client, path, accept_encoding, conditional)
    return _spark_stream_response(resp)

# ── 网页审阅托管中心: 懒启动注册表 ─────────────────────────────────────────────
# 审阅 iframe 打开某 app 的 live_url 时, 若其后端没在跑, 由本进程按标准命令把它拉起来
# (无窗口——subprocess.Popen 已被上面的 _subprocess_hide 全局加 CREATE_NO_WINDOW),
# 探活就绪后再代理。这样"应用意外关闭→点开审阅材料→自动按标准方式启动并打开"。
# 参考 AIWorkSpace app 中心 ports.json 的 lazy-spawn 范式(ready_path 探活 / 点击才起)。
_REPO_ROOT = _DASHBOARD_ROOT.parents[2]  # .../omnicompany
_WS_ROOT = _REPO_ROOT.parent             # .../WindowsWorkspace(webworks 等是 omnicompany 的同级)
_WORKSHOP_ROOT = _WS_ROOT / "spark.workshop" / "debug-agent" / "workshop"

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
    "voxelcraft-assets": {
        # 项目级资产审阅视图后端(仅 API): 库只读浏览 API, 页面壳由前端半边另做, 本条目
        # 只作数据通路(api/* 反代)的启动配置存在, 不作托管中心页面条目(page_retired)。
        "name": "voxelcraft 资产库浏览 API(仅 API)",
        "page_retired": True,
        "upstream": _voxelcraft_ASSETS_UPSTREAM,
        "ready_path": "/api/health",
        "start": [sys.executable, "-m",
                  "omnicompany.packages.domains.voxelcraft.content.assets.api",
                  "serve", "--port", "8331"],
        "cwd": str(_REPO_ROOT),
        "env": {"PYTHONPATH": "src"},
    },
    "voxelcraft": {
        # voxelcraft 检阅台(取代桌面启动器, 用户 2026-07-12 裁定): 状态/服务器起停/
        # 进入世界/重置世界/e2e/wiki/验证证据 集中一页; 本体在 proto-world 仓, 纯标准库。
        "name": "voxelcraft 检阅台",
        "upstream": _voxelcraft_CONSOLE_UPSTREAM,
        "ready_path": "/api/status",
        "start": [sys.executable, str(pathlib.Path(_PROTO_WORLD_ROOT) / "tools" / "console" / "app.py")],
        "cwd": _PROTO_WORLD_ROOT,
        "env": {"PYTHONUTF8": "1"},
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
    "treehouse": {
        # Treehouse 重建阶段直接复用唯一的 Spark Workshop/BSide Vue 真源。
        # 旧 treehouse-studio Vite 手写壳冻结为历史原型，不再作为用户入口。
        "name": "树屋 Treehouse（BSide canonical surface）",
        "upstream": _BSIDE_UPSTREAM,                # :5460
        "ready_path": "/api/health",
        "start": "npm start",
        "shell": True,                              # npm 在 Windows 是 .cmd, 需经 shell
        "cwd": str(_WORKSHOP_ROOT),
        # 2026-09-16: 两个 ChatGPT/Codex 账号额度耗尽(主号至 09-22, codex_2 至 09-19),
        # 跑测助手的 codex app-server 走 the_company 代理上的同款模型; 恢复账号后删掉即回默认。
        "env": {"PASSIVE_AGENT_MODEL_PROVIDER": "the_company"},
        "wait_secs": 30.0,
    },
    "vilo-demo": {
        "name": "Vilo demo (tabletop)",
        "upstream": _VILO_DEMO_UPSTREAM,           # :8992
        "ready_path": "/",                          # http.server 根目录列表=200
        "start": [sys.executable, "-m", "http.server", "8992"],
        "cwd": str(_WS_ROOT / "webworks"),         # 从 webworks 根起服务
    },
    "spark-webtwin": {
        # Spark WebTwin 实时宿主: 浏览器里执行生产 Binary/JsSrc 的 PuerTS ESM(零转译),
        # 鼠标可玩。服务本体在 P4 客户端仓 WebTwin/scripts/live-server.mjs, 只读。
        "name": "Spark WebTwin 实时宿主(可玩)",
        "upstream": _SPARK_WEBTWIN_UPSTREAM,        # :9001
        "ready_path": "/api/health",                # 回 service=spark-webtwin-live(验身份)
        "start": "npm run live-server",
        "shell": True,                               # npm 在 Windows 是 .cmd, 需经 shell
        "cwd": _SPARK_WEBTWIN_ROOT,
        "wait_secs": 45.0,
    },
    "bside": {
        # 键名为了兼容旧路由保留；启动对象已经是唯一 Workshop 产品。
        "name": "Workshop",
        "upstream": _BSIDE_UPSTREAM,               # :5460
        "ready_path": "/api/health",
        "start": "npm start",
        "shell": True,
        "cwd": str(_WORKSHOP_ROOT),
        # 2026-09-16: 两个 ChatGPT/Codex 账号额度耗尽(主号至 09-22, codex_2 至 09-19),
        # 跑测助手的 codex app-server 走 the_company 代理上的同款模型; 恢复账号后删掉即回默认。
        "env": {"PASSIVE_AGENT_MODEL_PROVIDER": "the_company"},
        "wait_secs": 30.0,
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


def _get_host_health_client() -> "httpx.AsyncClient":
    global _host_health_client
    if _host_health_client is None:
        # Every hosted upstream is controller-managed and local/private. Avoid
        # loading the Windows root store (several seconds under disk pressure)
        # just to probe an HTTP loopback URL, and reuse the connection pool.
        _host_health_client = httpx.AsyncClient(
            timeout=2.0,
            limits=httpx.Limits(max_keepalive_connections=16, max_connections=32),
            verify=False,
            trust_env=False,
        )
    return _host_health_client


async def _host_health_ok(appcfg: dict) -> bool:
    """轻量探活: ready_path 返回 <500 即视为就绪。"""
    try:
        r = await _get_host_health_client().get(
            appcfg["upstream"] + appcfg.get("ready_path", "/"),
        )
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
                # 新进程 = 旧连接全废。必须在这里丢池子(见 _reset_upstream_client 的说明)。
                await _reset_upstream_client(app_id)
                return True
        logger.warning("hosted app %s spawned but not ready in %ss", app_id, wait_secs)
        return False


# 托管 app → 它那个**单例**上游客户端的全局变量名。
_HOSTED_CLIENT_GLOBALS = {
    "walker-game": "_walker_client",
    "treehouse": "_treehouse_client",
    "vilo-demo": "_vilo_demo_client",
    "vilo-os": "_vilo_os_client",
    "narrative-studio": "_narrative_client",
    "voxelcraft-assets": "_voxelcraft_assets_client",
    "voxelcraft-console": "_voxelcraft_console_client",
    "spark-webtwin": "_spark_webtwin_client",
    "bside": "_bside_client",
}


async def _reset_upstream_client(app_id: str) -> None:
    """丢弃某个上游的单例 httpx 客户端(连同它的 keep-alive 连接池)。

    ⚠ 为什么必须有(2026-08-07 实锤, 用户在同一会话里为同一现象报障 3 次):

    这些代理客户端是**进程级单例**, 持有 keep-alive 连接池(spark-webtwin 是 32 条)。
    一旦上游进程被替换(崩溃后重拉、开发中重启、重新构建后重启), 池子里就全是**指向已死
    进程**的连接。而探活 `_host_health_ok` 用的是**每次新建**的临时客户端 —— 于是形成
    一个自相矛盾的死循环:

        真实请求 → 拿到死连接 → RequestError → 触发 ensure
        ensure   → 新建客户端探活 → 成功 → 报告"已就绪"
        重试     → 仍用**同一个坏池子** → 再撞一条死连接 → 又失败

    池里有多少条死连接就要撞多少次才能自愈, 表现就是"顽固地打不开"。
    而 `omni dashboard restart` 会重建整个进程、连带重建单例 —— 所以**每次重启都"修好了"**,
    然后上游一被替换就复发。三次报障、三次"修好", 修的其实都是同一件事的表象。

    修在根上: 上游进程被替换的那一刻(ensure 拉起成功) 就把池子整个丢掉;
    另外真实请求撞到 RequestError 时也丢一次, 这样即使不是 ensure 触发的也能一次自愈。
    """
    # 通用 hosted proxy 使用独立连接池；只清专用全局客户端会让
    # /treehouse/ 这类注册入口继续复用已失效的 keep-alive 连接。
    hosted_client = _hosted_proxy_clients.pop(app_id, None)
    if hosted_client is not None:
        try:
            await hosted_client.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("closing stale hosted proxy client for %s failed", app_id, exc_info=True)

    var = _HOSTED_CLIENT_GLOBALS.get(app_id)
    if not var:
        return
    client = globals().get(var)
    if client is None:
        return
    globals()[var] = None  # 先摘掉, 后续请求会拿到全新的客户端
    try:
        await client.aclose()
    except Exception:  # noqa: BLE001 — 关一个已经坏掉的池子, 失败无所谓
        logger.debug("closing stale upstream client for %s failed", app_id, exc_info=True)


def _lazy_boot_page(display_name: str) -> Response:
    """上游未起时给 HTML 导航的自愈页: 立即返回, 轮询探活, 就绪后自己刷新。

    以前是同步等 ensure(最长 30-60s)再回 502 —— iframe 拿到 502 就永远停在死页,
    用户视角"连接无法起效"(2026-07-04 实锤)。现在: 秒回启动页 + 后台拉起 + 就绪自愈。

    ⚠ 这里**绝不能用 `<meta http-equiv="refresh">`**(2026-08-07 实锤):
    meta refresh 会计入浏览器的**重定向链**, 而 Chrome 的上限是 20 次。自愈页每 2 秒刷一次,
    托管 app 的 `wait_secs` 是 45s(spark-webtwin)/60s(vilo-os) —— 冷启动要刷 22~30 次,
    **必然**超过上限, 于是 Chrome 弹"上的网页似乎有问题, 或者可能已永久移动到新的 Web 地址"。
    也就是说: 自愈机制自己把"服务正在启动"这个可诊断状态, 变成了一个看不出原因的浏览器错误页,
    而且**每次冷启动都必然发生**, 不是偶发。用户为此反复报障多次。

    改用 fetch 轮询: HTTP 请求不进重定向链, 只在真就绪时 `location.reload()` 一次。
    另外给一个明确的超时终点 —— 起不来就说起不来, 别永远转圈。
    """
    html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        "<style>body{background:#14161a;color:#8b94a3;font:13px 'Segoe UI',sans-serif;"
        "display:grid;place-items:center;height:100vh;margin:0;text-align:center}"
        ".spin{width:16px;height:16px;border:2px solid #334155;border-top-color:#6ea8fe;"
        "border-radius:50%;display:inline-block;vertical-align:-3px;margin-right:8px;"
        "animation:s .9s linear infinite}@keyframes s{to{transform:rotate(360deg)}}"
        "#t{opacity:.6;margin-top:6px;font-size:12px}</style></head>"
        f'<body><div><div><span class="spin" id="s"></span><span id="m">{display_name} 启动中…</span></div>'
        '<div id="t">正在探活，就绪后本页自动进入</div></div>'
        "<script>(function(){var n=0,max=90;"  # 90 × 2s = 180s，比最长 wait_secs(60s) 宽裕
        "function done(ok,msg){document.getElementById('s').style.display='none';"
        "document.getElementById('m').textContent=msg;"
        "document.getElementById('t').textContent=ok?'':'刷新本页可再试一次';}"
        "function tick(){n++;if(n>max){done(false,'启动超时，服务没能起来');return;}"
        "document.getElementById('t').textContent='正在探活（第 '+n+' 次）…';"
        # cache:no-store 很重要: 探活不能命中缓存, 否则永远拿到旧的失败响应。
        "fetch(location.pathname+(location.search?location.search+'&':'?')+'__probe='+n,"
        "{cache:'no-store',headers:{'accept':'application/octet-stream'}})"
        # 上游没起时代理回 502; 只有真拿到非 502 才算就绪, 这时整页重载一次(不是重定向)。
        ".then(function(r){if(r.status!==502){location.reload();return;}setTimeout(tick,2000);})"
        ".catch(function(){setTimeout(tick,2000);});}"
        "setTimeout(tick,1200);})();</script></body></html>"
    )
    # **必须 no-store**: 这是一张"服务正在启动"的临时页, 却挂在目标面自己的 URL 上。
    # 200 + 无缓存头 = 可被启发式缓存; 桌面版子 WebView 用的是磁盘上的持久 profile
    # (webview-profiles/*), 一旦把这张临时页缓存在目标 URL 上, 之后每次导航都从磁盘取,
    # **一个请求都不发** —— 服务早就起来了, 用户却永远停在"启动中"。
    # 与 401 空 body 是同一类故障: 用户看到的东西和真实状态无关, 且服务端怎么修都没用。
    return Response(
        content=html,
        status_code=200,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0", "Pragma": "no-cache"},
    )


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
    # Canvas 桌面嵌入面 origin —— 统一捕获从札记画布调用本机 captures 端点。
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


class RevalidatingStaticFiles(StaticFiles):
    """Serve Vite assets from cache, but always revalidate their ETag.

    Not every emitted filename is content-addressed: ``vite.config.ts`` gives
    lazy chunks a stable module-set name for reproducible builds.  Marking
    those URLs ``immutable`` can therefore pin an old implementation to one
    browser origin after a rebuild.  Revalidation normally costs only a 304,
    while still allowing a changed file at the same URL to heal itself.
    """

    def file_response(self, *args, **kwargs) -> Response:
        # 注意: 基类 file_response 是同步方法(被 async get_response 里同步调用、不 await),
        # 这里若误写成 async def, self.file_response(...) 会返回一个没被 await 的 coroutine
        # 而不是 Response, 静态文件请求会全部炸掉 —— 必须保持同步签名。
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
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
            or scope["path"] in ("/", "/chat-standalone", "/review-stage", "/config-center/spark")
        ):
            await self.gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


app.add_middleware(PathScopedGZip)


class SlowOrErrorRequestLog:
    """慢请求(≥2s)与 5xx 的结构化落盘 —— uvicorn --no-access-log 是刻意为之(高频轮询
    会刷爆日志), 但"哪条 API 慢了/挂了"在排障时是刚需, 之前只能从网关日志反推。
    只记 /api/ 与代理路径, 静态资源不记; 磁盘故障静默(观测不能反噬服务)。
    """

    _SLOW_SECONDS = 2.0

    @staticmethod
    def _resolve_log_file() -> pathlib.Path:
        override = os.environ.get("OMNI_SLOW_ERRORS_FILE")
        if override:
            return pathlib.Path(override)
        return _DASHBOARD_ROOT.parent.parent.parent / "logs" / "dashboard-slow-errors.jsonl"

    @staticmethod
    def _is_longpoll(path: str, query: str, duration: float) -> bool:
        # 长轮询按设计就是"客户端要求等满 wait_ms"——duration 贴着请求值不算慢, 治 41% 假阳性。
        # Desktop nodes 默认 wait 20s, 因此请求没显式带 query 也要识别；
        # Workshop CLI ``tasks/next`` 是另一条固定 25s 长轮询。wait_ms=0
        # 是即时探测，不能把它当成设计等待而吞掉真正的慢请求。
        is_desktop_poll = path.endswith("/poll")
        is_cli_task_wait = "/workspace-auth/cli/" in path and path.endswith("/tasks/next")
        if not is_desktop_poll and not is_cli_task_wait:
            return False
        try:
            wait_s = 25.0 if is_cli_task_wait else 20.0
            if "wait_ms=" in query:
                wait_s = int(query.split("wait_ms=", 1)[1].split("&", 1)[0]) / 1000.0
        except (ValueError, IndexError):
            return False
        if wait_s <= 0:
            return False
        return duration >= wait_s * 0.85 - 0.05

    def __init__(self, app) -> None:
        self.app = app

    @staticmethod
    def _append_record(log_file: pathlib.Path, record: dict) -> None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith(("/api/", "/bside", "/workshop")):
            await self.app(scope, receive, send)
            return
        started = time.time()
        from omnicompany.dashboard.controlplane.backend_diagnostics import (
            get_backend_latency_monitor,
        )
        backend_monitor = get_backend_latency_monitor()
        monitor_token = backend_monitor.begin_request()
        status_holder = {"status": 0}

        async def send_wrapped(message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapped)
        finally:
            duration = time.time() - started
            status = status_holder["status"]
            endpoint = scope.get("endpoint")
            handler = (
                f"{endpoint.__module__}.{endpoint.__qualname__}"
                if endpoint is not None
                else "unresolved"
            )
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in (scope.get("headers") or ())}
            request_path = scope.get("path") or ""
            session_id = headers.get("x-omni-session", "")
            if not session_id:
                session_match = re.match(r"^/api/cc/sessions/([^/]+)", request_path)
                if session_match:
                    session_id = session_match.group(1)
            query = (scope.get("query_string") or b"").decode("utf-8", "replace")[:200]
            designed_wait = self._is_longpoll(request_path, query, duration)
            monitor_record = backend_monitor.finish_request(
                monitor_token,
                status=status,
                method=scope.get("method") or "",
                path=request_path,
                handler=handler,
                instance=headers.get("x-omni-instance", ""),
                session=session_id,
                slow_seconds=self._SLOW_SECONDS,
                designed_wait=designed_wait,
            )
            if status >= 500 or duration >= self._SLOW_SECONDS:
                try:
                    log_file = self._resolve_log_file()
                    client = (scope.get("client") or ("", 0))[0]
                    record = {
                        "ts": started, "duration": round(duration, 3), "status": status,
                        "method": scope.get("method"), "path": scope.get("path"),
                        "query": query,
                        "client": client,
                        "handler": handler,
                        "request_id": monitor_record["request_id"],
                        "inflight_start": monitor_record["inflight_start"],
                        "executor_queue_start": monitor_record["executor_queue_start"],
                        "loop_lag_start_ms": monitor_record["loop_lag_start_ms"],
                    }
                    if designed_wait:
                        record["longpoll"] = True
                    if session_id:
                        record["session"] = session_id[:64]
                    # 归因头由前端遥测注入(同源请求), 有则记, 用于会话/实例级 join。
                    for hk, field in (("x-omni-instance", "instance"),
                                      ("x-omni-surface", "surface"),
                                      ("x-omni-session", "session")):
                        if headers.get(hk):
                            record[field] = headers[hk][:64]
                    # Request logging is diagnostic-only.  Disk stalls must not
                    # block the async dashboard event loop that serves PTY APIs.
                    await asyncio.to_thread(self._append_record, log_file, record)
                except Exception:
                    pass


app.add_middleware(SlowOrErrorRequestLog)

# Production build assets (output of `npm run build` in frontend/)
_assets_dir = _STATIC_DIR / "assets"
if _assets_dir.is_dir():
    app.mount("/assets", RevalidatingStaticFiles(directory=str(_assets_dir)), name="assets")

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
    # 网页影片审阅评论(锚点 = 某一秒 + 某一元素); 审阅壳 web_review_shell.html 消费
    ("omnicompany.dashboard.controlplane.video_review_comments", "video_review_comments_router", "/api"),
    ("omnicompany.dashboard.controlplane.catalogue",   "catalogue_router",   "/api"),
    ("omnicompany.dashboard.controlplane.notes",       "notes_router",       "/api"),
    ("omnicompany.dashboard.controlplane.system",      "system_router",      "/api"),
    ("omnicompany.dashboard.controlplane.cc_proxy",    "cc_proxy_router",    None),  # 自身 /api/cc
    ("omnicompany.packages.services.boss_sight.material_wiki", "material_wiki_router", None),  # Wiki 本体不依赖长期运行的 ccdaemon 版本
    ("omnicompany.packages.services.boss_sight.subscriptions", "dashboard_subscription_router", None),  # 订阅配额本进程跑, 不依赖重启 ccdaemon
    ("omnicompany.dashboard.controlplane.boss_sight_proxy", "boss_sight_proxy_router", None),  # 自身 /api/boss-sight
    ("omnicompany.packages.services.boss_sight.material_domains.routes", "material_domains_router", None),
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
    ("omnicompany.dashboard.controlplane.ai_learning_observatory", "ai_learning_observatory_router", "/api"),
    # 技能+管线清单 ([2026-07-06] 项目页「技能」页签, 见 controlplane/skills.py)
    ("omnicompany.dashboard.controlplane.skills",      "skills_router",      "/api"),
    # 项目文件目录树 ([2026-07-06] 项目页「文件」页签重做, 见 controlplane/project_fs.py)
    ("omnicompany.dashboard.controlplane.project_fs",  "project_fs_router",  "/api"),
    # 统一定时调度「定时任务」视图 ([2026-06-24] 见 controlplane/cron.py)
    ("omnicompany.dashboard.controlplane.cron",        "cron_router",        "/api"),
    ("omnicompany.dashboard.controlplane.lan_access",  "lan_access_router",  None),
    ("omnicompany.dashboard.controlplane.desktop_sessions", "desktop_sessions_router", None),
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
    # 桌面 Remote Execute Node：出站轮询 + 双向签名的结构化只读任务；不复用 SSH 任意命令通道。
    ("omnicompany.dashboard.controlplane.desktop_nodes", "desktop_nodes_router", None),
    # Keep this before the desktop release catch-all
    # /api/clients/desktop/{channel}/{version}/{asset_kind}.
    ("omnicompany.dashboard.controlplane.client_inspect", "client_inspect_router", None),
    # 首方客户端下载和签名更新目录：只读原子发布清单，不接受调用方传入文件路径。
    ("omnicompany.dashboard.controlplane.clients", "clients_router", None),
    # Spark 配置中心：现有 Omni 网关上的实时只读页面，不新增监听端口。
    ("omnicompany.dashboard.controlplane.config_center", "config_center_router", None),
    # 桌面客户端崩溃回传：远端主机静默退出后，下次启动把上一段会话的证据交回来。写入不鉴权(崩溃的机器可能已丢会话)，读取只限本机。
    ("omnicompany.dashboard.controlplane.client_diagnostics", "client_diagnostics_router", None),
    # 画布 Python 桥: 透明转发 canvas-store(:8212, TS 唯一权威)。overlay-shell/BlockSuite 已退役(2026-08-17)。
    ("omnicompany.dashboard.controlplane.overlay_notes",  "overlay_notes_router",  None),
    # 前端观测事件枢纽: 远端浏览器的错误/心跳/业务事件上报与查询(2026-08-16 画布排障对策).
    ("omnicompany.dashboard.controlplane.frontend_diagnostics", "frontend_diagnostics_router", None),
    ("omnicompany.dashboard.controlplane.backend_diagnostics", "backend_diagnostics_router", None),
    # 画布域注册与跨域感知: omnidashboard 为主体, workshop 等外部域只读聚合(§10.3 架构裁决).
    ("omnicompany.dashboard.controlplane.canvas_domains", "canvas_domains_router", None),
    # 受控文件桥: 远端上传到固定暂存区 + 许可根目录的只读反向浏览.
    ("omnicompany.dashboard.controlplane.file_bridge",  "file_bridge_router",  None),
    # game_observatory 域已出背板为独立托管服务(127.0.0.1:8213), 这里只挂薄反代;
    # 对外路径不变(/api/game-observatory/*, /game-observatory/*, /robots.txt)。
    # 注意 lofa_proxy 的 /game-observatory/live-media/* 注册在前, 保持相对顺序。
    ("omnicompany.dashboard.controlplane.game_observatory_proxy", "game_observatory_proxy_router", None),
    ("omnicompany.dashboard.controlplane.content_share_proxy", "content_share_proxy_router", None),
    ("omnicompany.dashboard.controlplane.bilibili_draft", "bilibili_draft_router", None),
    # M3b 任务真源切换: plan-groups 文件真源 board 端点(QuestBoard 双读兜底优先消费)。需重启 dashboard 生效。
    ("omnicompany.dashboard.controlplane.plan_groups_board", "plan_groups_board_router", None),
    ("omnicompany.dashboard.controlplane.studio_cluster", "studio_cluster_router", None),
    # task-core 任务总线 Todo 面板 (sidepanel 第四页数据面, 信封桥)。需重启 dashboard 生效。
    ("omnicompany.dashboard.controlplane.task_bus", "task_bus_router", None),
    # 收件箱: 多来源消息按订阅矩阵落成未提升的任务(消息不另立实体)。需重启 dashboard 生效。
    ("omnicompany.dashboard.controlplane.inbox", "inbox_router", None),
]

try:
    _dashboard_blas_threads = str(max(1, int(os.environ.get("OMNI_DASHBOARD_BLAS_THREADS", "1"))))
except ValueError:
    _dashboard_blas_threads = "1"
_blas_env_names = ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")
_blas_env_before = {name: os.environ.get(name) for name in _blas_env_names}
for _name in _blas_env_names:
    os.environ[_name] = _dashboard_blas_threads
_router_assembly_failures: list[dict[str, str]] = []
try:
    for _mod_path, _attr, _prefix in _CONTROLPLANE_ROUTERS:
        try:
            _mod = importlib.import_module(_mod_path)
            _router = getattr(_mod, _attr)
            if _prefix is None:
                app.include_router(_router)
            else:
                app.include_router(_router, prefix=_prefix)
        except Exception as _e:
            # ARCH-RATCHET-SURGERY P4.1: fail-open 是"模块坏了 API 面静默消失"的
            # 直接土壤——升 error + 落自检工件；OMNI_ROUTER_FAILFAST=1 时拒绝启动。
            _router_assembly_failures.append(
                {"module": _mod_path, "attr": _attr,
                 "error": f"{type(_e).__name__}: {_e}"})
            logger.error("controlplane router NOT LOADED: %s.%s (%s: %s)",
                         _mod_path, _attr, type(_e).__name__, _e)
    if _router_assembly_failures and os.environ.get("OMNI_ROUTER_FAILFAST") == "1":
        raise RuntimeError(
            "router assembly failed (OMNI_ROUTER_FAILFAST=1): "
            + "; ".join(f"{f['module']}.{f['attr']}" for f in _router_assembly_failures))
    try:
        _assembly_report = {
            "expected": len(_CONTROLPLANE_ROUTERS),
            "loaded": len(_CONTROLPLANE_ROUTERS) - len(_router_assembly_failures),
            "failures": _router_assembly_failures,
        }
        _assembly_path = Path(__file__).resolve().parents[3] / "var" / "runtime"
        _assembly_path.mkdir(parents=True, exist_ok=True)
        (_assembly_path / "router_assembly.json").write_text(
            json.dumps(_assembly_report, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        logger.warning("router assembly report write failed", exc_info=True)
finally:
    # The imported native pools keep their one-thread setting. Restore the
    # process environment so independently spawned services retain their own
    # compute policy instead of inheriting Dashboard's latency-oriented limit.
    for _name, _previous in _blas_env_before.items():
        if _previous is None:
            os.environ.pop(_name, None)
        else:
            os.environ[_name] = _previous

@app.on_event("startup")
async def _startup() -> None:
    from omnicompany.dashboard.asyncio_runtime import configure_default_executor

    dashboard_executor = configure_default_executor(
        asyncio.get_running_loop(),
        env_name="OMNI_DASHBOARD_EXECUTOR_WORKERS",
        default_workers=8,
        thread_name_prefix="dashboard",
    )
    from omnicompany.dashboard.controlplane.backend_diagnostics import (
        get_backend_latency_monitor,
    )
    app.state.dashboard_executor = dashboard_executor
    app.state.dashboard_latency_task = get_backend_latency_monitor().start(
        dashboard_executor,
    )
    _load_domains_on_startup()

    # builtin 画布存储 = dashboard 自持的 @omni/canvas-store 实例(2026-08-16 零依赖
    # 裁决修正: 不经 workshop, workshop 可单独部署)。桥的 notes_canvas_* 转发到这里。
    try:
        from omnicompany.dashboard.controlplane import canvas_store_service

        canvas_store_service.start()
    except Exception as e:
        logger.warning("canvas-store service start failed: %s", e)

    # game_observatory 域服务(域自治 P1): 独立进程托管, 8210 只留反代。
    try:
        from omnicompany.dashboard.controlplane import game_observatory_service

        game_observatory_service.start()
    except Exception as e:  # noqa: BLE001
        logger.warning("game-observatory service start failed: %s", e)

    try:
        from omnicompany.dashboard.controlplane import content_share_service

        content_share_service.start()
    except Exception as e:  # noqa: BLE001
        logger.warning("content-share service start failed: %s", e)

    # home 节点隧道自愈: 本机若是 home 盒子(存在 bootstrap 落盘的 tunnel_loop.ps1),
    # dashboard 每次启动顺手拉起被终止的 OmniHomeTunnel 计划任务。只拉既有任务:
    # Running 不动、missing 不新建, 永不重复起第二条隧道; 控制机零开销跳过。
    # (2026-08-25 事故: 任务被终止后无自愈路径, 孤儿 ssh 撑三天后全线失联)
    try:
        from omnicompany.dashboard.controlplane.remote_nodes_core import (
            ensure_home_tunnel_task,
        )

        _tunnel_result = await asyncio.get_running_loop().run_in_executor(
            None, ensure_home_tunnel_task
        )
        if _tunnel_result not in ("skip_not_home_node", "skip_not_windows", "running"):
            logger.info("home tunnel ensure: %s", _tunnel_result)
    except Exception as e:  # noqa: BLE001
        logger.warning("home tunnel ensure failed: %s", e)

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
        from omnicompany.packages.services.boss_sight.reviewstage.preview_queue import (
            run_preview_worker,
        )
        from omnicompany.packages.services.boss_sight.reviewstage.routes import (
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
    try:
        from omnicompany.dashboard.controlplane.backend_diagnostics import (
            get_backend_latency_monitor,
        )
        await get_backend_latency_monitor().stop()
    except Exception as e:  # noqa: BLE001
        logger.warning("dashboard latency monitor stop failed: %s", e)
    try:
        from omnicompany.dashboard.controlplane import canvas_store_service

        canvas_store_service.stop()
    except Exception as e:  # noqa: BLE001
        logger.warning("canvas-store service stop failed: %s", e)
    try:
        from omnicompany.dashboard.controlplane import game_observatory_service

        game_observatory_service.stop()
    except Exception as e:  # noqa: BLE001
        logger.warning("game-observatory service stop failed: %s", e)
    try:
        from omnicompany.dashboard.controlplane import content_share_service

        content_share_service.stop()
    except Exception as e:  # noqa: BLE001
        logger.warning("content-share service stop failed: %s", e)
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
    global _narrative_client, _voxelcraft_assets_client, _voxelcraft_console_client
    global _spark_webtwin_client, _bside_client, _host_health_client
    if _walker_client is not None:
        await _walker_client.aclose()
        _walker_client = None
    if _vilo_demo_client is not None:
        await _vilo_demo_client.aclose()
        _vilo_demo_client = None
    if _vilo_os_client is not None:
        await _vilo_os_client.aclose()
        _vilo_os_client = None
    if _narrative_client is not None:
        await _narrative_client.aclose()
        _narrative_client = None
    if _voxelcraft_assets_client is not None:
        await _voxelcraft_assets_client.aclose()
        _voxelcraft_assets_client = None
    if _voxelcraft_console_client is not None:
        await _voxelcraft_console_client.aclose()
        _voxelcraft_console_client = None
    if _spark_webtwin_client is not None:
        await _spark_webtwin_client.aclose()
        _spark_webtwin_client = None
    if _bside_client is not None:
        await _bside_client.aclose()
        _bside_client = None
    if _host_health_client is not None:
        await _host_health_client.aclose()
        _host_health_client = None
    # 通用 hosted app（包括 Treehouse→Workshop）的连接池不在专用全局表中，
    # 关闭时也要释放，避免 dashboard 重启后残留旧上游连接。
    for hosted_app, hosted_client in list(_hosted_proxy_clients.items()):
        try:
            await hosted_client.aclose()
        except Exception:  # noqa: BLE001
            logger.debug("closing hosted proxy client for %s failed", hosted_app, exc_info=True)
    _hosted_proxy_clients.clear()


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


_INDEX_ASSET_RE = re.compile(r'(?P<prefix>(?:src|href)=["\'])(?P<url>/assets/[^"\']+)(?P<suffix>["\'])')


def _versioned_index(index_html: Path) -> str:
    """Return the SPA entry with one build-generation query on root styles.

    The generation includes the index bytes plus the size and nanosecond mtime
    of every directly referenced asset.  Thus even a recovery build that
    rewrites CSS under an accidentally reused filename gets a new browser cache
    key without clearing cookies, local storage, or the global cache.

    Do not add a query to the ESM entry script. Lazy Rollup chunks import shared
    exports back from the canonical, unqueried entry URL; querying only the HTML
    entry makes the browser execute the same module graph under two identities
    and mounts two React roots (including two command palettes).
    """
    raw = index_html.read_bytes()
    text = raw.decode("utf-8")
    digest = hashlib.sha256(raw)
    urls = {match.group("url").split("?", 1)[0] for match in _INDEX_ASSET_RE.finditer(text)}
    for url in sorted(urls):
        asset = _STATIC_DIR / url.lstrip("/")
        try:
            stat = asset.stat()
        except OSError:
            digest.update(f"{url}:absent".encode("utf-8"))
        else:
            digest.update(f"{url}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8"))
    token = digest.hexdigest()[:16]

    def add_generation(match: re.Match[str]) -> str:
        if match.group("prefix").startswith("src="):
            return match.group(0)
        url = match.group("url")
        separator = "&" if "?" in url else "?"
        return f'{match.group("prefix")}{url}{separator}v={token}{match.group("suffix")}'

    return _INDEX_ASSET_RE.sub(add_generation, text)


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
    return HTMLResponse(
        _versioned_index(index_html),
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


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



@app.websocket("/workshop/api/whiteboards/{board_id}/sync")
@app.websocket("/bside/api/whiteboards/{board_id}/sync")
async def bside_whiteboard_ws_proxy(client_ws: WebSocket, board_id: str) -> None:
    """旧 Yjs writer 已冻结；统一札记改走 Workshop Material Note HTTP。"""
    await client_ws.accept()
    await client_ws.close(code=1008, reason="moved to Workshop Material Notes")


@app.websocket("/bside/lofa/overlay/invoke-ws")
@app.websocket("/workshop/lofa/overlay/invoke-ws")
@app.websocket("/bside/runtime-preview/{path:path}")
@app.websocket("/workshop/runtime-preview/{path:path}")
async def workshop_content_ws_proxy(client_ws: WebSocket, path: str = "") -> None:
    """Carry the caller's Workshop session through the same mount as HTTP."""
    mount = "/workshop" if client_ws.url.path.startswith("/workshop/") else "/bside"
    target = client_ws.url.path[len(mount):]
    if client_ws.url.query:
        target += "?" + client_ws.url.query
    # Keep the browser cookie intact. Workshop resolves a real session before
    # considering the optional local personal identity.
    caller_cookie = client_ws.headers.get("cookie", "")
    headers = {key: client_ws.headers[key] for key in ("origin", "referer", "x-workshop-project") if key in client_ws.headers}
    headers["cookie"] = caller_cookie
    headers["x-forwarded-prefix"] = mount
    authorization = client_ws.headers.get("authorization", "")
    if authorization:
        headers["authorization"] = authorization
    headers = _workshop_proxy_identity_headers(headers, request_is_owner_device(client_ws))
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:5460{target}", additional_headers=headers,
            proxy=None, open_timeout=8, max_size=16 * 1024 * 1024,
        ) as upstream:
            await client_ws.accept()

            async def client_to_upstream() -> None:
                while True:
                    message = await client_ws.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("bytes") is not None:
                        await upstream.send(message["bytes"])
                    elif message.get("text") is not None:
                        await upstream.send(message["text"])

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await client_ws.send_bytes(message)
                    else:
                        await client_ws.send_text(message)

            tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        # Authentication remains owned by Workshop; never retry as the host.
        pass
    finally:
        try:
            await client_ws.close(code=1008)
        except Exception:
            pass


@app.websocket("/workshop/api/delegate/term/{sid}")
@app.websocket("/bside/api/delegate/term/{sid}")
@app.websocket("/api/delegate/term/{sid}")
async def delegate_term_ws_proxy(client_ws: WebSocket, sid: str) -> None:
    """控制台后台实况 WS 透传: 浏览器(含 https 12443 入口) → workshop 同源 WS 代理。"""
    await client_ws.accept()
    try:
        async with websockets.connect(f"ws://127.0.0.1:5460/api/delegate/term/{sid}", max_size=None) as up:
            async def c2u() -> None:
                while True:
                    data = await client_ws.receive_text()
                    await up.send(data)

            async def u2c() -> None:
                while True:
                    data = await up.recv()
                    await client_ws.send_text(str(data))

            import asyncio as _a
            done, pending = await _a.wait(
                [_a.create_task(c2u()), _a.create_task(u2c())],
                return_when=_a.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
    except Exception:
        pass
    try:
        await client_ws.close()
    except Exception:
        pass


from omnicompany.dashboard.controlplane.desktop_sessions import request_is_owner_device

_workshop_token_state: dict[str, object] = {"at": 0.0, "token": None}


def _workshop_bearer_token() -> str | None:
    """读 canonical Workshop data/token.txt(带 5min 缓存); 供 /bside 写代理注入。"""
    import time as _t
    now = _t.time()
    if _workshop_token_state["at"] and now - float(_workshop_token_state["at"]) < 300:
        return _workshop_token_state["token"]  # type: ignore[return-value]
    token: str | None = None
    try:
        token = (_WORKSHOP_ROOT / "data" / "token.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        token = None
    _workshop_token_state.update({"at": now, "token": token})
    return token


def _workshop_proxy_identity_headers(headers: dict[str, str], owner_device: bool = False) -> dict[str, str]:
    """Apply the trusted Workshop identity envelope to a forwarded request.

    机主的个人身份只给机主设备(``owner_device``: 桌面会话 / LOFA 设备会话 / 本机回环)。
    其他访客不注入, 让 Workshop 自己的登录页(collab platform / 测试账号)接管 —— 2026-09-21 起。
    """
    forwarded = dict(headers)
    # This is an internal signal. Never accept a marker supplied by a caller.
    forwarded.pop("x-workshop-proxy-injected", None)
    authorization = forwarded.get("authorization", "")
    if authorization.lower().startswith("basic "):
        forwarded.pop("authorization", None)
        authorization = ""
    if not authorization and owner_device:
        token = _workshop_bearer_token()
        if token:
            forwarded["authorization"] = f"Bearer {token}"
            forwarded["x-workshop-proxy-injected"] = "1"
    return forwarded


@app.api_route("/workshop", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@app.api_route("/workshop/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@app.api_route("/bside", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@app.api_route("/bside/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def bside_proxy(request: Request, path: str = "") -> Response:
    """历史 `/bside/` URL 同源反向代理到唯一 Workshop，**strip 前缀**。

    /bside/assets/x → 上游 /assets/x; /bside/api/wiki/doc → 上游 /api/wiki/doc。
    前端以 base=/bside/ 构建, 故浏览器发出的绝对路径都带前缀, 剥掉正好对上上游根;
    旧链接继续有效，但不会启动或展示第二套 BSide UI。
    """
    # Workshop is built with relative ``./assets/*`` URLs. When the browser
    # opens the mount root without a trailing slash (``/bside``), URL
    # resolution incorrectly points those files at ``/assets/*`` and the app
    # boots into a blank page. Canonicalise document navigation before
    # proxying while leaving writes and preflight requests untouched.
    if not path and request.method in {"GET", "HEAD"} and not request.url.path.endswith("/"):
        target = f"{request.url.path}/"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(url=target, status_code=307)

    # Starlette's path parameter is decoded. Forward the original bytes so a
    # Material ID containing %2F remains one upstream route parameter.
    mount = b"/workshop" if request.url.path.startswith("/workshop") else b"/bside"
    raw_path = request.scope.get("raw_path", b"").split(b"?", 1)[0]
    upstream = (raw_path[len(mount):].decode("ascii")
                if raw_path.startswith(mount + b"/") else (f"/{path}" if path else "/"))
    # 统一下载服务的大文件（百 MB～数 GB）两头都要边收边转：整包读进内存既撑内存，又会被 30s 超时截断。
    download_file = request.method in {"GET", "HEAD"} and bool(_BSIDE_DOWNLOAD_FILE_RE.match(upstream))
    upload_content = request.method == "PUT" and bool(_BSIDE_DOWNLOAD_CONTENT_RE.match(upstream))
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    # POST 也要转发: T 面的「重生成」按钮打 POST /api/regen。只登记 GET 的话
    # 按钮会拿到驾驶舱自己的 405 —— 那是个点了没反应的按钮, 比没有按钮更坏。
    body = b"" if upload_content else await request.body()
    fwd = {k: v for k, v in request.headers.items()
           if k.lower() not in {"host", "accept-encoding", "content-length"}}
    fwd["accept-encoding"] = "identity"  # 避免上游 gzip 与我们重写 content-length 冲突
    # httpx clients retain response cookies. Always provide this visitor's
    # Cookie header, including an empty one, so the shared connection pool can
    # never lend another visitor's Workshop session to an anonymous request.
    fwd["cookie"] = request.headers.get("cookie", "")
    # Workshop resolves a real session before the optional local personal
    # identity. Keep caller credentials intact and add the trusted envelope
    # only when no caller authorization exists.
    fwd = _workshop_proxy_identity_headers(fwd, request_is_owner_device(request))
    fwd["x-forwarded-host"] = request.headers.get("host", request.url.netloc)
    fwd["x-forwarded-proto"] = request.url.scheme
    fwd["x-forwarded-prefix"] = "/workshop" if request.url.path.startswith("/workshop") else "/bside"
    # 推送（SSE）必须边收边转。整包读取时长连接永远读不完：客户端拿不到响应头。
    # Workshop 的 chat POST 固定返回 SSE，但官方 CLI 的请求 Accept 是 application/json；
    # 因此这两个明确的 chat 路由不能依赖 Accept 来识别流式响应。
    upstream_path = upstream.partition("?")[0]
    streaming = (
        request.method == "GET" and "text/event-stream" in request.headers.get("accept", "").lower()
    ) or (
        request.method == "POST"
        and upstream_path in {"/api/passive-agent/chat", "/api/workspace/passive-agent/chat"}
    )
    if upload_content and request.headers.get("content-length"):
        # 声明长度原样带上，httpx 就按定长发送而不改成 chunked；上游据此判断这一块是否收全。
        fwd["content-length"] = request.headers["content-length"]

    async def _send() -> "httpx.Response":
        client = _get_bside_client()
        if streaming:
            outgoing = client.build_request(request.method, upstream, content=body, headers=fwd,
                                            timeout=httpx.Timeout(30.0, read=300.0))
            return await client.send(outgoing, stream=True)
        if download_file:
            outgoing = client.build_request(request.method, upstream, content=body, headers=fwd,
                                            timeout=_BSIDE_TRANSFER_TIMEOUT)
            return await client.send(outgoing, stream=True)
        if upload_content:
            outgoing = client.build_request(request.method, upstream, content=_request_body_chunks(request),
                                            headers=fwd, timeout=_BSIDE_TRANSFER_TIMEOUT)
            return await client.send(outgoing)
        return await client.request(request.method, upstream, content=body, headers=fwd)

    try:
        resp = await _send()
    except ClientDisconnect:
        # 上传途中客户端断开：上游已按收到的偏移落盘，客户端重跑时从那里续传。
        return Response(status_code=499)
    except httpx.RequestError as exc:
        if upload_content:
            # 请求体已边收边转出去，无法重放；不走自愈重试，让客户端查询已收偏移后续传。
            raise HTTPException(status_code=502, detail=f"Workshop 上传转发中断: {exc}") from exc
        # 上游没在跑。HTML 导航 → 秒回自愈启动页; 其余(资源/API)阻塞拉起后重试。
        if _wants_html(request):
            asyncio.get_running_loop().create_task(_ensure_hosted_app("bside"))
            return _lazy_boot_page("Workshop")
        if await _ensure_hosted_app("bside"):
            try:
                resp = await _send()
            except httpx.RequestError as exc2:
                raise HTTPException(status_code=502, detail=f"Workshop 已尝试启动但仍不可达: {exc2}")
        else:
            raise HTTPException(
                status_code=502,
                detail=("Workshop 启动失败或超时。手动: 在 "
                        f"{_WORKSHOP_ROOT.as_posix()} 跑 `npm start`"),
            )
    drop = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    if download_file:
        # 原样转发原始字节，Content-Length / Content-Range / Content-Encoding 与字节一一对应，保留给浏览器显示进度与续传。
        drop = {"transfer-encoding", "connection"}
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop | {"set-cookie"}}
    response: Response
    if streaming or download_file:
        response = StreamingResponse(
            _relay_stream(resp),
            status_code=resp.status_code,
            headers=headers,
            media_type=resp.headers.get("content-type"),
        )
    else:
        response = Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=headers,
            media_type=resp.headers.get("content-type"),
        )
    # Set-Cookie is not comma-joinable (Expires itself contains a comma).
    for cookie in resp.headers.get_list("set-cookie"):
        response.headers.append("set-cookie", cookie)
    return response


@app.api_route("/runtime-preview", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
@app.api_route("/runtime-preview/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def workshop_runtime_proxy(request: Request, path: str = "") -> Response:
    """Workshop 内部 Runtime 的同端口只读/交互代理；外部不访问 5320。"""
    upstream = f"/runtime-preview/{path}" if path else "/runtime-preview/"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    body = await request.body()
    fwd = {k: v for k, v in request.headers.items()
           if k.lower() not in {"host", "accept-encoding", "content-length"}}
    fwd["accept-encoding"] = "identity"
    fwd["cookie"] = request.headers.get("cookie", "")
    try:
        resp = await _get_bside_client().request(request.method, upstream, content=body, headers=fwd)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Workshop Runtime 不可达: {exc}") from exc
    drop = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop}
    return Response(content=resp.content, status_code=resp.status_code,
                    headers=headers, media_type=resp.headers.get("content-type"))


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


@app.api_route("/voxelcraft-assets", methods=["GET"])
@app.api_route("/voxelcraft-assets/{path:path}", methods=["GET"])
async def voxelcraft_assets_proxy(request: Request, path: str = "") -> Response:
    """voxelcraft 资产库只读 API 反代(默认 :8331), 仅 api/*(page_retired)。"""
    if not (path == "api" or path.startswith("api/")):
        return Response(
            content="voxelcraft-assets 仅提供只读 API(/voxelcraft-assets/api/*), 无页面壳。",
            status_code=410,
            media_type="text/plain; charset=utf-8",
        )
    upstream = f"/{path}" if path else "/"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    fwd = {
        k: v for k, v in request.headers.items()
        if k.lower() not in {"host", "accept-encoding", "content-length"}
    }
    fwd["accept-encoding"] = "identity"
    try:
        resp = await _get_voxelcraft_assets_client().get(upstream, headers=fwd)
    except httpx.RequestError:
        if await _ensure_hosted_app("voxelcraft-assets"):
            try:
                resp = await _get_voxelcraft_assets_client().get(upstream, headers=fwd)
            except httpx.RequestError as exc2:
                raise HTTPException(
                    status_code=502,
                    detail=f"voxelcraft-assets 已尝试启动但仍不可达: {exc2}",
                )
        else:
            raise HTTPException(
                status_code=502,
                detail=(
                    "voxelcraft-assets 启动失败或超时。手动: "
                    "PYTHONPATH=src python -m "
                    "omnicompany.packages.domains.voxelcraft.content.assets.api serve --port 8331"
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


@app.api_route("/voxelcraft", methods=["GET", "POST"])
@app.api_route("/voxelcraft/{path:path}", methods=["GET", "POST"])
async def voxelcraft_console_proxy(request: Request, path: str = "") -> Response:
    """voxelcraft 检阅台反代(默认 :8332)，上游未起时自动按标准命令启动。"""
    upstream = f"/{path}" if path else "/"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    body = await request.body()
    fwd = {
        k: v for k, v in request.headers.items()
        if k.lower() not in {"host", "accept-encoding", "content-length"}
    }
    fwd["accept-encoding"] = "identity"
    try:
        resp = await _get_voxelcraft_console_client().request(
            request.method, upstream, content=body, headers=fwd,
        )
    except httpx.RequestError:
        if _wants_html(request):
            asyncio.get_running_loop().create_task(_ensure_hosted_app("voxelcraft"))
            return _lazy_boot_page("voxelcraft 检阅台")
        if await _ensure_hosted_app("voxelcraft"):
            try:
                resp = await _get_voxelcraft_console_client().request(
                    request.method, upstream, content=body, headers=fwd,
                )
            except httpx.RequestError as exc2:
                raise HTTPException(
                    status_code=502,
                    detail=f"voxelcraft 检阅台已尝试启动但仍不可达: {exc2}",
                )
        else:
            raise HTTPException(
                status_code=502,
                detail=(
                    "voxelcraft 检阅台启动失败或超时。手动: "
                    "python C:/workspace/game-project/tools/console/app.py"
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


@app.api_route("/spark-webtwin", methods=["GET"])
# POST 必须放行: 页面里的圈选/点选要把结果回传给实时服务落盘(`/spark-*/api/pick`)。
# 只放 GET 的后果是**静默失败** —— 浏览器发出去、代理回 405、页面这边没人看错误，
# 于是"按住 Alt 没反应"，而实际上手势跑了、只是结果没能落地。踩过一次，记在这里。
@app.api_route("/spark-webtwin/{path:path}", methods=["GET", "POST"])
# 别名 `/spark-play`: 与上面同一处理器、同一上游、同一鉴权, 只是换个从没被访问过的路径。
# 为什么需要它: 浏览器会把重定向缓存下来, 且命中缓存时**根本不发请求**(网关日志里因此
# 一条访问记录都没有)。此时无论服务端怎么正常, 用户看到的都是"网页似乎有问题, 或者可能
# 已永久移动到新的 Web 地址"。给一个全新路径可以绕开这条缓存, 也把"服务端好不好"与
# "浏览器缓存"这两件事分开验证。
@app.api_route("/spark-play", methods=["GET"])
@app.api_route("/spark-play/{path:path}", methods=["GET", "POST"])
async def spark_webtwin_proxy(request: Request, path: str = "") -> Response:
    """同源反向代理到 Spark WebTwin 实时宿主(:9001) —— 浏览器里跑生产 JsSrc 的可玩页面。

    strip 前缀: /spark-webtwin/jssrc/Main.mjs -> /jssrc/Main.mjs。上游是个只读静态服务,
    不知道前缀; 页面侧 import map 用相对地址、`/file` 端点从 import.meta.url 派生, 所以
    strip 成立(这是为挂网关路径专门改的, 见 WebTwin/scripts/build-live.mjs 与 live-browser-host.mts)。

    没有斜杠的 `/spark-webtwin` 必须先重定向到带斜杠 —— 否则文档 base URL 是 `/`,
    相对 import map 会把 1108 个生产模块解析到 dashboard 根上, 整页白屏。
    """
    if os.environ.get("OMNI_SPARK_WEBTWIN_ARCHIVED", "1") != "0":
        return Response("此游玩入口已关闭", status_code=410, media_type="text/plain; charset=utf-8")
    # 前缀取实际命中的那个(/spark-webtwin 或别名 /spark-play), 不写死 —— 否则从别名进来的
    # 请求会被重定向回主路径, 又撞上那条缓存。
    prefix = "/spark-play" if request.url.path.startswith("/spark-play") else "/spark-webtwin"
    if path == "" and not request.url.path.endswith("/"):
        target = f"{prefix}/"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        # 307 而不是 301: 永久重定向会被浏览器长期缓存, 一旦目标临时不可用就再也纠正不回来。
        return RedirectResponse(url=target, status_code=307)

    upstream = f"/{path}" if path else "/"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    client = _get_spark_webtwin_client()
    accept_encoding = request.headers.get("accept-encoding", "")
    # 浏览器的重校验凭据。少转发这两个头, 上游就永远看不到条件, 只能回全量。
    conditional = {
        name: request.headers[name]
        for name in ("if-none-match", "if-modified-since")
        if name in request.headers
    }
    # POST 只用于诊断回传(圈选结果落盘), body 直接转给上游, 不做任何解释。
    if request.method == "POST":
        body = await request.body()
        try:
            resp = await client.post(
                upstream,
                content=body,
                headers={"content-type": request.headers.get("content-type", "application/json")},
            )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Spark WebTwin 不可达: {exc}")
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=resp.headers.get("content-type"),
        )
    try:
        resp = await _spark_raw_open(client, upstream, accept_encoding, conditional)
    except httpx.RequestError:
        # 先丢掉这个池子再说: 失败最常见的原因不是"上游没起", 而是**上游被换过了**,
        # 池里躺着指向旧进程的 keep-alive 连接(见 _reset_upstream_client)。
        # 不丢的话, 下面无论探活多成功, 重试都还是从同一个坏池子里取连接。
        await _reset_upstream_client("spark-webtwin")
        # 上游其实活着(只是连接死了)时, 用全新的客户端立刻重试一次就能成 —— 不该让用户看启动页。
        if await _host_health_ok(_HOSTED_APPS["spark-webtwin"]):
            try:
                return await _spark_forward(_get_spark_webtwin_client(), upstream, accept_encoding, conditional)
            except httpx.RequestError:
                pass
        if _wants_html(request):
            asyncio.get_running_loop().create_task(_ensure_hosted_app("spark-webtwin"))
            return _lazy_boot_page("Spark WebTwin 实时宿主")
        if await _ensure_hosted_app("spark-webtwin"):  # 超时取注册表里的 wait_secs=45
            try:
                resp = await _spark_raw_open(_get_spark_webtwin_client(), upstream, accept_encoding, conditional)
            except httpx.RequestError as exc2:
                raise HTTPException(status_code=502, detail=f"Spark WebTwin 已尝试启动但仍不可达: {exc2}")
        else:
            raise HTTPException(
                status_code=502,
                detail="Spark WebTwin 启动失败或超时。手动: 在 E:/P4/spark-user/Client/WebTwin 跑 "
                       "`npm run build-live` 后 `npm run live-server`",
            )
    return _spark_stream_response(resp)


@app.api_route("/vilo-demo", methods=["GET"])
@app.api_route("/vilo-demo/{path:path}", methods=["GET"])
async def vilo_demo_proxy(request: Request, path: str = "") -> Response:
    """同源反向代理到 tabletop-simulator 里的 Vilo 静态 demo。

    tabletop-simulator 由普通 http.server 提供, 不知道 /vilo-demo 前缀, 所以这里需要
    strip prefix: /vilo-demo/data/x.json -> /data/x.json。保持同源后, 审阅 iframe
    才能读到卡牌、事件和聊天气泡 DOM。
    """
    # demo 已迁到 webworks 根下的 /apps/tabletop-simulator/，引擎走相对 ../../packages。
    # 8992 从 webworks 根起服务，所以 /vilo-demo/ 根打到的是目录列表，不是 demo。
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
                detail="Vilo demo 启动失败或超时。手动: 在 webworks 根跑 `python -m http.server 8992`",
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


# ===== 统一出站：托管 app 通用同源反代（注册即出战，不再手写路由） =====
# 任何 _HOSTED_APPS 条目自动获得 /<key>/ 与 /<key>/* 反代：strip 前缀转发全方法 + 上游不可达懒拉起。
# 新 app 出战只需两步：① _HOSTED_APPS 加条目（name/upstream/ready_path/start/cwd）；
# ② Caddyfile public 区加一段（LAN 匿名面；仅本机使用可省略）。
# 注意：本路由必须注册在文件最后（Starlette 按注册序匹配）——上面的专用路由
# （/walker-game、/workshop、/bside、/spark-webtwin 等上游自带 base 的）优先命中，不受影响；
# 通用路由 strip 前缀，适合上游从根路径起服务的 app（如 treehouse-studio preview）。
_hosted_proxy_clients: dict[str, "httpx.AsyncClient"] = {}


def _get_hosted_proxy_client(app_id: str) -> "httpx.AsyncClient":
    client = _hosted_proxy_clients.get(app_id)
    if client is None:
        client = httpx.AsyncClient(
            base_url=str(_HOSTED_APPS[app_id]["upstream"]),
            timeout=30.0,
            limits=httpx.Limits(max_keepalive_connections=24, max_connections=64),
            verify=False,
            trust_env=False,
        )
        _hosted_proxy_clients[app_id] = client
    return client


@app.api_route("/{hosted_app}", methods=["GET", "HEAD"])
@app.api_route("/{hosted_app}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def hosted_app_proxy(request: Request, hosted_app: str, path: str = "") -> Response:
    """统一出站通用反代（见上方区块注释）。未注册的第一段路径 → 404。"""
    if hosted_app not in _HOSTED_APPS:
        raise HTTPException(status_code=404, detail=f"未注册的托管 app: {hosted_app}")
    if path == "" and not request.url.path.endswith("/"):
        # 相对资源/相对 fetch 以页面 URL 为基准，无尾斜杠会解析到根 —— canonical 化
        return RedirectResponse(url=f"/{hosted_app}/", status_code=308)
    upstream = f"/{path}" if path else "/"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"
    body = await request.body()
    fwd = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "accept-encoding", "content-length"}}
    fwd["accept-encoding"] = "identity"
    try:
        resp = await _get_hosted_proxy_client(hosted_app).request(request.method, upstream, content=body, headers=fwd)
    except httpx.RequestError:
        # 上游没在跑：HTML 导航 → 秒回自愈启动页后台拉起；其余 → 阻塞拉起后重试一次（丢旧连接池）。
        if _wants_html(request):
            asyncio.get_running_loop().create_task(_ensure_hosted_app(hosted_app))
            return _lazy_boot_page(str(_HOSTED_APPS[hosted_app]["name"]))
        if await _ensure_hosted_app(hosted_app):
            _hosted_proxy_clients.pop(hosted_app, None)
            try:
                resp = await _get_hosted_proxy_client(hosted_app).request(request.method, upstream, content=body, headers=fwd)
            except httpx.RequestError as exc2:
                raise HTTPException(status_code=502, detail=f"{hosted_app} 已尝试启动但仍不可达: {exc2}")
        else:
            raise HTTPException(status_code=502, detail=f"{hosted_app} 启动失败或超时")
    drop = {"content-encoding", "content-length", "transfer-encoding", "connection"}
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in drop}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=headers,
        media_type=resp.headers.get("content-type"),
    )
