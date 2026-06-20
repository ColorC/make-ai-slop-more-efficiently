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

import hashlib
import json
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
