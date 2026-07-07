# [OMNI] origin=ai-ide ts=2026-06-11 type=infra
# [OMNI] material_id="material:dashboard.controlplane.dev_reload.hot_update_bus.py"
"""dev_reload — 免重启更新的版本信号总线.

三层免重启更新 ([2026-06-11]) 的后端枢纽:
- 网页层: 前端 (lib/devReload.ts) 每 3s 轮询 GET /api/dev/versions, ui token 变了
  就 location.reload() — iframe 内自刷新, 不碰 VSCode.
- 扩展层: vscode-chat-sidebar loader 每 5s 轮询同一接口, ext token 变了就热换
  out/impl.js — 不重启扩展宿主.
- token = 产物文件哈希 + 持久化 epoch. 哈希随真实构建变 (rebuild 自动触发刷新),
  epoch 由 POST /api/dev/bump 手动顶 (强制刷新, 不需要重新构建).

epoch 落盘 data/runtime/dev_reload.json — 进程重启不丢, 避免 dashboard 一重启
扩展就误判 ext 变更而无谓热换.

触发入口走 CLI 黄金范式: `omni dashboard ui-reload` / `ext-update` (cli/commands/dashboard.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from omnicompany.core.config import omni_workspace_root

dev_reload_router = APIRouter()

_DASHBOARD_ROOT = Path(__file__).resolve().parents[1]
_UI_INDEX = _DASHBOARD_ROOT / "static" / "index.html"
_EXT_IMPL = _DASHBOARD_ROOT / "extensions" / "vscode-chat-sidebar" / "out" / "impl.js"

_VALID_TARGETS = ("ui", "ext")


def _epoch_path() -> Path:
    return omni_workspace_root() / "data" / "runtime" / "dev_reload.json"


def _read_epochs() -> dict[str, int]:
    p = _epoch_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return {t: int(data.get(t, 0)) for t in _VALID_TARGETS}
    except (OSError, ValueError):
        return {t: 0 for t in _VALID_TARGETS}


def _write_epochs(epochs: dict[str, int]) -> None:
    p = _epoch_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(epochs, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_hash(path: Path) -> str:
    """产物文件短哈希; 文件缺失返回 'absent' (仍是合法 token, 构建后自然变化)."""
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    except OSError:
        return "absent"


def _tokens() -> dict[str, str]:
    epochs = _read_epochs()
    return {
        "ui": f"{_file_hash(_UI_INDEX)}:{epochs['ui']}",
        "ext": f"{_file_hash(_EXT_IMPL)}:{epochs['ext']}",
    }


@dev_reload_router.get("/dev/versions")
def dev_versions() -> dict[str, Any]:
    """当前 ui / ext 版本 token. 客户端只做字符串比较, 不解析内部结构."""
    return _tokens()


class BumpRequest(BaseModel):
    target: str


@dev_reload_router.post("/dev/bump")
def dev_bump(req: BumpRequest) -> dict[str, Any]:
    """强制顶一次 epoch — 对应客户端无条件刷新/热换 (不需要重新构建)."""
    if req.target not in _VALID_TARGETS:
        raise HTTPException(status_code=400, detail=f"target 必须是 {_VALID_TARGETS} 之一")
    epochs = _read_epochs()
    epochs[req.target] += 1
    _write_epochs(epochs)
    return {"ok": True, "target": req.target, **_tokens()}


# ── "在 VSCode 打开"的免深链通道:后端排队,扩展轮询领取 ─────────────────────
# 为什么不靠 vscode:// 深链: URI 送达时 VSCode 会弹"允许扩展打开此 URI?"确认框,
# 且弹在不抢焦点的后台窗口里 — 用户视角就是"点了也没有页签"(2026-07-04 实锤)。
# 轮询通道无确认框、不依赖 webview 消息桥; 扩展 impl 每 2.5s 领取一次。
_PENDING_OPENS: list[dict[str, Any]] = []
_PENDING_EVENT = asyncio.Event()


class RequestOpenBody(BaseModel):
    # kind: material=材料页签(host.openMaterialPanel) / file=本地文件或目录(openLocalFile,
    #       id=绝对路径, 可带 :行[:列]; 2026-07-06 起接入 —— "在 VSCode 打开"文件也走此中转,
    #       不再依赖 webview 消息桥或 vscode:// 网页链接)。扩展侧分支见 impl.ts pollPendingOpens。
    kind: str = "material"
    id: str
    title: str | None = None


@dev_reload_router.post("/dev/request-open")
def dev_request_open(req: RequestOpenBody) -> dict[str, Any]:
    """页面点"在 VSCode 打开"→ 入队; 运行中的 VSCode 扩展长轮询领取并开页签/文件(先到先得)."""
    if not req.id:
        raise HTTPException(status_code=400, detail="缺 id")
    _PENDING_OPENS.append({"kind": req.kind, "id": req.id, "title": req.title or req.id})
    del _PENDING_OPENS[:-20]
    _PENDING_EVENT.set()
    return {"ok": True, "queued": len(_PENDING_OPENS)}


@dev_reload_router.get("/dev/pending-opens")
async def dev_pending_opens(consume: int = 0, wait: float = 0) -> dict[str, Any]:
    """扩展领取口(consume=1 取走清空; wait>0 长轮询挂住直到有活, 点击到开页签近零延迟).

    多窗口并发领取安全: 读队列+清空之间无 await, 在事件循环内原子 — 先醒者得, 后醒者拿空表.
    consume=0 只窥视(诊断用)."""
    if wait and not _PENDING_OPENS:
        try:
            await asyncio.wait_for(_PENDING_EVENT.wait(), timeout=min(float(wait), 25.0))
        except asyncio.TimeoutError:
            pass
    items = list(_PENDING_OPENS)
    if consume:
        _PENDING_OPENS.clear()
        _PENDING_EVENT.clear()
    return {"items": items}


class OpenVscodeUriRequest(BaseModel):
    uri: str


class OpenFileBody(BaseModel):
    path: str
    line: int | None = None


@dev_reload_router.post("/dev/open-file")
def dev_open_file(req: OpenFileBody) -> dict[str, Any]:
    """本机直开文件/目录 — "在 VSCode 打开"的最短路(2026-07-06 用户: 点了就该马上打开, 不要轮询).

    后端与 VSCode 同机, 直接执行 `code --goto <文件:行>`(落最近活跃窗口)或 `code <目录>`
    (开文件夹窗口, 不带 -r 绝不替换用户当前工作区)。零消息桥、零队列、零确认框、点击即开;
    队列中转(request-open kind=file)与 vscode:// 协议只作为本端点不可用时的前端兜底。
    材料页签仍走队列 —— 那要在扩展 webview 里开面板, CLI 做不了; 纯文件 CLI 就是正解。
    """
    raw = (req.path or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="缺 path")
    p = Path(raw)
    if not p.is_absolute():
        raise HTTPException(status_code=400, detail="只接受绝对路径")
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"路径不存在: {raw}")
    code_cli = shutil.which("code") or shutil.which("code.cmd")
    if not code_cli:
        raise HTTPException(status_code=404, detail="本机找不到 code CLI(VSCode 未装或未进 PATH)")
    if p.is_dir():
        args = [str(p)]
    else:
        args = ["--goto", f"{p}:{req.line}" if req.line and req.line > 0 else str(p)]
    # .cmd 批处理必须经 cmd.exe; CREATE_NO_WINDOW 保证零控制台闪窗(本机硬规则)。
    if sys.platform == "win32":
        cmd = ["cmd.exe", "/d", "/c", code_cli, *args]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        cmd = [code_cli, *args]
        flags = 0
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"code 启动失败: {exc}") from exc
    return {"ok": True, "path": str(p), "dir": p.is_dir()}


_ALLOWED_URI_PREFIX = "vscode://omnicompany.omni-chat/"


@dev_reload_router.post("/dev/open-vscode-uri")
def dev_open_vscode_uri(req: OpenVscodeUriRequest) -> dict[str, Any]:
    """本机代开 vscode:// 深链 — "在 VSCode 打开"的不依赖 postMessage 桥的通道.

    页面在 Simple Browser / 普通浏览器 / 桥接失联的 webview 里时, postMessage 会被静默丢弃
    (2026-07-04 用户"点了完全没反应"的根因类). 这里由后端执行 `code --open-url <uri>`,
    扩展的 UriHandler 收链后开材料页签. 只放行本扩展的深链, 不做通用协议启动器.
    """
    uri = (req.uri or "").strip()
    if not uri.startswith(_ALLOWED_URI_PREFIX):
        raise HTTPException(status_code=400, detail=f"只放行 {_ALLOWED_URI_PREFIX}* 深链")
    code_cli = shutil.which("code") or shutil.which("code.cmd")
    if not code_cli:
        raise HTTPException(status_code=404, detail="本机找不到 code CLI(VSCode 未装或未进 PATH)")
    # .cmd 批处理必须经 cmd.exe; CREATE_NO_WINDOW 保证零控制台闪窗(本机硬规则)。
    if sys.platform == "win32":
        cmd = ["cmd.exe", "/d", "/c", code_cli, "--open-url", uri]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        cmd = [code_cli, "--open-url", uri]
        flags = 0
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"code --open-url 启动失败: {exc}") from exc
    return {"ok": True, "uri": uri}
