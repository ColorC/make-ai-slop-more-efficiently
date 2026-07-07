# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-24T00:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.rust_scanner_client.py"
"""Rust agent-scanner 的 Python 读取端 —— 把机器级会话索引从 Rust 取来。

权威设计: omnicompany/docs/plans/[2026-06-24]RUST-PYTHON-HYBRID/plan.md

取数三级回落(由 OMNI_AGENT_SCANNER 控制, 默认 auto):
  1. HTTP  GET http://127.0.0.1:<port>/agents   (Rust 服务在跑 → 最新)
  2. 快照   %USERPROFILE%/.poof/agents-index.json  (服务断了仍可读, 标 stale)
  3. None  → 调用方回落到现有 Python 扫描 (agent_registry.rebuild)

flag:
  OMNI_AGENT_SCANNER = auto | rust | python
    auto   (默认): 先 HTTP, 再快照, 都没有 → None
    rust         : 只走 Rust(HTTP→快照), 没有就 None(不回落 Python, 用于强制验证)
    python       : 直接 None(绕过 Rust, 用现有 Python 扫描)
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_DEFAULT_PORT = 8765
_HTTP_TIMEOUT = 1.5
_SNAPSHOT_MAX_AGE = 120.0  # 快照超过 120s 仍用但标 stale


def mode() -> str:
    return (os.environ.get("OMNI_AGENT_SCANNER") or "auto").strip().lower()


def _home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or ".")


def _port() -> int:
    raw = os.environ.get("OMNI_AGENT_SCANNER_PORT")
    if raw and raw.isdigit():
        return int(raw)
    addr = os.environ.get("AGENT_SCANNER_ADDR", "")
    if ":" in addr:
        tail = addr.rsplit(":", 1)[-1]
        if tail.isdigit():
            return int(tail)
    return _DEFAULT_PORT


def _token() -> str | None:
    tok = os.environ.get("AGENT_SCANNER_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    try:
        t = (_home() / ".poof" / "rec_token").read_text(encoding="utf-8").strip()
        return t or None
    except OSError:
        return None


def _snapshot_path() -> Path:
    raw = os.environ.get("AGENT_SCANNER_SNAPSHOT")
    if raw:
        return Path(raw)
    return _home() / ".poof" / "agents-index.json"


def _fetch_http() -> dict[str, Any] | None:
    port = _port()
    token = _token()
    url = f"http://127.0.0.1:{port}/agents"
    if token:
        url += f"?token={token}"
    try:
        req = urllib.request.Request(url, headers={"X-Token": token} if token else {})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and isinstance(data.get("agents"), list):
            return data
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    return None


def _read_snapshot() -> dict[str, Any] | None:
    p = _snapshot_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("agents"), list):
            age = time.time() - p.stat().st_mtime
            data["_stale"] = age > _SNAPSHOT_MAX_AGE
            data["_source"] = "snapshot"
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return None


def available() -> bool:
    """Rust 取数是否可用(HTTP 或快照任一)。"""
    if mode() == "python":
        return False
    return _fetch_http() is not None or _read_snapshot() is not None


def fetch() -> dict[str, Any] | None:
    """取 Rust 索引 {seq,count,agents,...};不可用 / 被 flag 禁用 → None。"""
    m = mode()
    if m == "python":
        return None
    http = _fetch_http()
    if http is not None:
        http["_source"] = "http"
        return http
    return _read_snapshot()  # rust + auto 都退快照


def residents() -> list[dict[str, Any]] | None:
    """只要 agents 列表;不可用 → None(调用方回落 Python 扫描)。"""
    data = fetch()
    if data is None:
        return None
    return data.get("agents") or []


def tail(session_id: str, n: int = 14) -> list[dict[str, Any]] | None:
    """某会话最近活动行 [{role,text}];Rust 不在/被禁 → None。"""
    if mode() == "python":
        return None
    port = _port()
    token = _token()
    url = f"http://127.0.0.1:{port}/sessions/{session_id}/tail?n={int(n)}"
    if token:
        url += f"&token={token}"
    try:
        req = urllib.request.Request(url, headers={"X-Token": token} if token else {})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and isinstance(data.get("lines"), list):
            return data["lines"]
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    return None
