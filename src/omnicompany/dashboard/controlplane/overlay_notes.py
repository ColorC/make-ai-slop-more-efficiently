# [OMNI] origin=ai-ide ts=2026-06-28 type=infra
"""controlplane/overlay_notes.py — overlay note store(BlockSuite/Yjs) 的 HTTP 桥。

overlay-shell 桌面端把笔记落在 C:/workspace/overlay-note-store(docs/<id>.ydoc 等),走 Tauri 命令读写。
LOFA 网页/手机端没有 Tauri,这里把同一套命令做成 HTTP,落同一个目录。对外只走 8210 一个口。
Legacy compatibility: `/lofa/poof/*` 仅作为旧 URL alias 保留；canonical 路由是 `/lofa/overlay/*`。

镜像的命令(契约对齐 src-tauri/src/notesstore.rs):
  notes_root / notes_doc_{get,put,del,keys} / notes_blob_{get,put,del,keys}
  notes_md_{put,del} / notes_index_put / notes_version_{put,all,del_one,del_all}
  nb_pending / nb_respond(旧 BlockSuite 笔记文件队列桥)
  cb_pending / cb_respond / cb_push(新材料画布活画布 ops 文件队列桥, 按 canvas id 定向认领)
  notes_snapshot_capture(材料卡冻结: 活 web 页烘焙成单文件静态 HTML, 见 overlay_snapshot.py)

并发: 与桌面 overlay-shell 都用 原子写(临时文件+rename); 快照级 last-write-wins(暂不做 Yjs 合并)。
"""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

overlay_notes_router = APIRouter(tags=["overlay-note-store"])

_ROOT = Path(
    os.environ.get("OVERLAY_NOTE_STORE_ROOT")
    or os.environ.get("POOF_NOTES_ROOT", r"C:/workspace/overlay-note-store")
)
# 唯一画布前端产物: 2026-09-14 起 dashboard 与 Spark Workshop 共用同一个画布内核
# webworks/apps/material-canvas(dist-web)。dashboard/notes-web 是 08-17 迁入后分叉的旧副本, 不再服务。
# 默认路径按本文件位置推导(controlplane → dashboard → omnicompany → src → 仓根 → 工作区)。
_WEB_DIR = Path(
    os.environ.get("OVERLAY_SHELL_WEB_DIR")
    or os.environ.get("POOF_WEB_DIR")
    or str(Path(__file__).resolve().parents[5] / "webworks" / "apps" / "material-canvas" / "dist-web")
)
_SEARCH_HTTP_URL = os.environ.get("OVERLAY_SHELL_HTTP_URL", "http://127.0.0.1:8732").rstrip("/")
_MAX_FILE_TEXT_BYTES = 2 * 1024 * 1024
_MAX_DIRECTORY_ITEMS = 500
_MAX_CANVAS_BYTES = 4 * 1024 * 1024
_TEXT_EXTS = {
    ".bat", ".c", ".cc", ".cfg", ".cmd", ".conf", ".cpp", ".cs", ".css", ".csv",
    ".env", ".go", ".h", ".hpp", ".html", ".ini", ".java", ".js", ".json", ".jsx",
    ".log", ".lua", ".md", ".mjs", ".ps1", ".py", ".rs", ".scss", ".sh", ".sql",
    ".svg", ".toml", ".ts", ".tsx", ".txt", ".uxml", ".vue", ".xml", ".yaml", ".yml",
}
_TEXT_NAMES = {"license", "makefile", "readme"}
_OS_JUNK = {"$recycle.bin", "system volume information"}
_BRIDGE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CANVAS_LOCKS: dict[str, threading.RLock] = {}
_CANVAS_LOCKS_GUARD = threading.Lock()
_CANVAS_MUTATION_HISTORY = 128


class OverlaySearchUnavailable(RuntimeError):
    """The local Overlay Shell index process is not ready/reachable."""


class OverlayConflict(RuntimeError):
    """The canvas on disk changed underneath a stale client (optimistic concurrency)."""


def _docs() -> Path: return _ROOT / "docs"
def _blobs() -> Path: return _ROOT / "blobs"
def _versions(doc_id: str) -> Path: return _ROOT / "versions" / doc_id
def _canvases() -> Path: return _ROOT / "canvases"
def _text_materials() -> Path: return _ROOT / "materials"
def _snapshots() -> Path: return _ROOT / "snapshots"


def _notes_bridge_dir() -> Path:
    """Return the command queue shared with ``omni notes`` and overlay-shell."""
    base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or ".")
    path = base / "overlay-shell" / "notes-bridge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _notes_bridge_pending() -> list[list[str]]:
    """Atomically claim queued CLI requests for the Dashboard note webview.

    The native overlay may be absent or its hidden WebView may be suspended.  The
    Dashboard embeds the same BlockSuite collection, so it can execute the same
    targeted operations while the note tab is open.  Renaming before reading
    prevents two open Dashboard tabs from processing one request twice.
    """
    out: list[list[str]] = []
    bridge = _notes_bridge_dir()
    for request_path in sorted(bridge.glob("req-*.json")):
        request_id = request_path.name[4:-5]
        if not _BRIDGE_ID_RE.fullmatch(request_id):
            continue
        claimed = bridge / f".claimed-{request_id}-{os.getpid()}-{time.time_ns()}"
        try:
            os.replace(request_path, claimed)
        except OSError:
            continue
        try:
            out.append([request_id, claimed.read_text(encoding="utf-8")])
        except OSError:
            pass
        finally:
            claimed.unlink(missing_ok=True)
    return out


def _notes_bridge_respond(args: dict) -> None:
    request_id = str(args.get("id") or "")
    if not _BRIDGE_ID_RE.fullmatch(request_id):
        raise ValueError("invalid note bridge request id")
    body = args.get("body")
    if not isinstance(body, str):
        raise ValueError("note bridge response body must be a string")
    _atomic_write(
        _notes_bridge_dir() / f"res-{request_id}.json",
        body.encode("utf-8"),
    )


def _canvas_bridge_dir() -> Path:
    """活画布(material canvas)定向 ops 的命令队列, 供 `omni canvas` CLI ⇄ notes-web 页。"""
    base = Path(os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or ".")
    path = base / "overlay-shell" / "canvas-bridge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _canvas_bridge_pending(canvas_id: str) -> list[list[str]]:
    """认领发往指定画布的待执行 op(原子 rename, 防并发页重复执行)。

    与 nb 桥不同: 多个 notes-web 页(每会话画布一个)可能同时轮询,
    每条请求按 body 里的 ``canvas`` 字段定向认领, 不是自己的放回原位。
    """
    out: list[list[str]] = []
    bridge = _canvas_bridge_dir()
    for request_path in sorted(bridge.glob("req-*.json")):
        request_id = request_path.name[4:-5]
        if not _BRIDGE_ID_RE.fullmatch(request_id):
            continue
        claimed = bridge / f".claimed-{request_id}-{os.getpid()}-{time.time_ns()}"
        try:
            os.replace(request_path, claimed)
        except OSError:
            continue
        try:
            body = claimed.read_text(encoding="utf-8")
            addressed = json.loads(body).get("canvas")
        except (OSError, json.JSONDecodeError, AttributeError):
            body, addressed = "", None
        if addressed != canvas_id:
            try:
                os.replace(claimed, request_path)  # 不是给自己的: 放回原位
            except OSError:
                pass
            continue
        claimed.unlink(missing_ok=True)
        if body:
            out.append([request_id, body])
    return out


def _canvas_bridge_respond(args: dict) -> None:
    request_id = str(args.get("id") or "")
    if not _BRIDGE_ID_RE.fullmatch(request_id):
        raise ValueError("invalid canvas bridge request id")
    body = args.get("body")
    if not isinstance(body, str):
        raise ValueError("canvas bridge response body must be a string")
    _atomic_write(
        _canvas_bridge_dir() / f"res-{request_id}.json",
        body.encode("utf-8"),
    )


def _canvas_bridge_push(args: dict) -> dict:
    """排入一条活画布 op 并等页面回结果(服务端长等, CLI 只走 HTTP 不碰队列文件)。"""
    op = str(args.get("op") or "")
    if not op:
        raise ValueError("缺 op")
    canvas = str(args.get("canvas") or "")
    if not canvas:
        raise ValueError("缺 canvas")
    try:
        timeout = max(1.0, min(float(args.get("timeout") or 15.0), 60.0))
    except (TypeError, ValueError):
        raise ValueError("timeout must be a number") from None
    op_args = args.get("args")
    if op_args is not None and not isinstance(op_args, dict):
        raise ValueError("args must be an object")
    request_id = uuid.uuid4().hex[:12]
    bridge = _canvas_bridge_dir()
    req = bridge / f"req-{request_id}.json"
    res = bridge / f"res-{request_id}.json"
    payload = json.dumps({"op": op, "canvas": canvas, "args": op_args or {}}, ensure_ascii=False)
    tmp = req.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, req)  # 原子, 轮询方不会读到半截
    deadline = time.time() + timeout
    while time.time() < deadline:
        if res.exists():
            try:
                data = json.loads(res.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.2)
                continue
            res.unlink(missing_ok=True)
            return data if isinstance(data, dict) else {"ok": True, "result": data}
        time.sleep(0.3)
    req.unlink(missing_ok=True)
    raise ValueError("画布没响应(notes-web 页没在开这个画布, 或版本过旧不含 canvas ops)")


def _did(a: dict) -> str:
    """版本命令 doc_id: JS 端传 camelCase docId(Tauri 会转 snake), HTTP 桥两种都收。"""
    return a.get("doc_id") or a.get("docId") or ""


def _safe(name: str) -> str:
    """对齐 Rust safe(): 字母数字 . _ - = , 长度<=200, 杜绝路径穿越。"""
    if (not name) or len(name) > 200 or not all(
            c.isascii() and (c.isalnum() or c in "._-=") for c in name):
        raise ValueError(f"非法存储键: {name}")
    return name


def _ensure() -> None:
    _docs().mkdir(parents=True, exist_ok=True)
    _blobs().mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A fixed ``.tmp`` name races when the browser and an Agent write at once.
    # Each writer gets a private staging file; replace remains the commit point.
    tmp = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_b64(path: Path):
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except FileNotFoundError:
        return None


def _list_keys(d: Path, strip_ext: str | None):
    out = []
    if not d.is_dir():
        return out
    for p in d.iterdir():
        name = p.name
        if name.startswith("."):
            continue
        if strip_ext is None:
            out.append(name)
        elif name.endswith(strip_ext):
            out.append(name[: -len(strip_ext)])
    return out


def _canvas_lock(canvas_id: str) -> threading.RLock:
    with _CANVAS_LOCKS_GUARD:
        return _CANVAS_LOCKS.setdefault(canvas_id, threading.RLock())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ── 画布域透明转发(2026-08-16 彻底 TS 化裁决 + 零依赖修正) ──────────────────
# 本桥不再复刻任何画布契约语义(revision/CAS/幂等/实体时钟/正文分居链全部归
# @omni/canvas-store)。转发目标 = dashboard 自持的 canvas-store 服务实例
# (canvas_store_service 拉起, root=overlay-note-store) —— 不经 workshop:
# workshop 必须可单独部署、对 dashboard 零依赖(2026-08-16 用户裁决修正)。
# 这里只做 invoke 命令 → HTTP 转发 + 错误映射(OverlayConflict → 409 链路不变)。


def _store_base() -> str:
    from . import canvas_store_service

    return canvas_store_service.store_url()


def _store_bearer() -> str:
    from . import canvas_store_service

    return canvas_store_service.token_value()


def _builtin_api(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"{_store_base()}/api{path}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    bearer = _store_bearer()
    if bearer:
        request.add_header("Authorization", f"Bearer {bearer}")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=8.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            return resp.status, (payload if isinstance(payload, dict) else {})
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8", errors="replace"))
        except Exception:
            detail = {}
        return error.code, (detail if isinstance(detail, dict) else {})


def _canvas_get(a: dict) -> str | None:
    status, payload = _builtin_api("GET", f"/canvases/{urllib.parse.quote(_safe(a['id']), safe='')}")
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"builtin canvas 读失败: HTTP {status} {payload.get('error', '')}")
    document = payload.get("document")
    return None if not isinstance(document, dict) else json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def _canvas_head(a: dict) -> dict | None:
    status, payload = _builtin_api("GET", f"/canvases/{urllib.parse.quote(_safe(a['id']), safe='')}")
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"builtin canvas 读失败: HTTP {status} {payload.get('error', '')}")
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    return {
        "revision": document.get("revision", 0),
        "updatedAt": document.get("updatedAt"),
        "lastMutation": document.get("lastMutation"),
    }


def _canvas_keys() -> list[str]:
    status, payload = _builtin_api("GET", "/canvases")
    if status != 200:
        raise RuntimeError(f"builtin canvas 列表失败: HTTP {status} {payload.get('error', '')}")
    return [str(item.get("document_id")) for item in payload.get("canvases") or [] if isinstance(item, dict)]


def _canvas_recent(a: dict) -> dict:
    """Read a bounded, explainable projection from the modern canvas store.

    The TS store intentionally exposes only document reads.  Keep the recent
    query here as a read-only projection over that API; it must never fall back
    to the retired notes store or infer card authorship from file timestamps.
    """
    try:
        limit = int(a.get("limit") or 20)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer") from None
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    from omnicompany.packages.services.canvas.canvas_observation import recent_canvas_projection

    status, payload = _builtin_api("GET", "/canvases")
    if status != 200:
        raise RuntimeError(f"builtin canvas recent 失败: HTTP {status} {payload.get('error', '')}")
    rows = [item for item in payload.get("canvases") or [] if isinstance(item, dict)]
    rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    items: list[dict] = []
    for row in rows[:limit]:
        canvas_id = str(row.get("document_id") or "")
        if not canvas_id:
            continue
        detail_status, detail = _builtin_api(
            "GET", f"/canvases/{urllib.parse.quote(canvas_id, safe='')}"
        )
        if detail_status == 404:
            continue
        if detail_status != 200:
            raise RuntimeError(
                f"builtin canvas recent 读取 {canvas_id} 失败: "
                f"HTTP {detail_status} {detail.get('error', '')}"
            )
        document = detail.get("document")
        if isinstance(document, dict):
            items.append(recent_canvas_projection(canvas_id, document))
    return {
        "source": "modern-canvas-store",
        "items": items,
        "count": len(items),
        "limit": limit,
    }


def _canvas_mutations(a: dict) -> dict:
    """写入历史（身份 / 目的 / 触碰的卡）。?card= 只看触碰过这张卡的写入。"""
    canvas_id = _safe(a["id"])
    params: dict[str, str] = {}
    if isinstance(a.get("card"), str) and a["card"]:
        params["card"] = str(a["card"])
    if isinstance(a.get("since"), (int, float)):
        params["since"] = str(int(a["since"]))
    if isinstance(a.get("limit"), (int, float)):
        params["limit"] = str(int(a["limit"]))
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    status, payload = _builtin_api(
        "GET", f"/canvases/{urllib.parse.quote(canvas_id, safe='')}/mutations{query}")
    if status == 404:
        raise ValueError(f"canvas 不存在: {canvas_id}")
    if status != 200:
        raise RuntimeError(f"builtin canvas 读写入历史失败: HTTP {status} {payload.get('error', '')}")
    return payload


def _canvas_annotate(a: dict) -> dict:
    """后补写入目的 / 批号；不推进 revision。"""
    canvas_id = _safe(a["id"])
    body = {key: a[key] for key in ("mutation_ids", "from_sequence", "to_sequence", "purpose", "batch") if a.get(key) is not None}
    status, payload = _builtin_api(
        "POST", f"/canvases/{urllib.parse.quote(canvas_id, safe='')}/mutations/annotate", body)
    if status == 404:
        raise ValueError(f"canvas 不存在: {canvas_id}")
    if status != 200:
        raise RuntimeError(f"builtin canvas 后补目的失败: HTTP {status} {payload.get('detail') or payload.get('error', '')}")
    return payload


def _write_identity_facts() -> dict:
    """给浏览器端的服务器侧身份事实：本机绑定的 owner 与主机。"""
    from omnicompany.packages.services._core.identity.write_identity import (
        machine_identity, read_machine_identity_file,
    )

    machine = machine_identity()
    raw = read_machine_identity_file() or {}
    return {
        "owner": machine.get("owner"),
        "owner_label": raw.get("owner_label"),
        "host": {"id": machine.get("id"), "label": machine.get("label")},
    }


def _canvas_mutate(a: dict) -> dict:
    canvas_id = _safe(a["id"])
    mutation_id = _safe(str(a.get("mutation_id") or ""))
    actor = str(a.get("actor") or "unknown")[:160]
    patch = a.get("patch")
    if not isinstance(patch, dict):
        raise ValueError("canvas mutation patch must be an object")
    session_id = str(a.get("session_id") or "")
    requested_workspace_ref = a.get("workspace_ref")
    current_status, current_payload = _builtin_api(
        "GET", f"/canvases/{urllib.parse.quote(canvas_id, safe='')}")
    if current_status not in {200, 404}:
        raise RuntimeError(
            f"builtin canvas 读 workspace_ref 失败: HTTP {current_status} "
            f"{current_payload.get('error', '')}")
    current_workspace_ref = (current_payload.get("workspace_ref")
                             if current_status == 200 and isinstance(current_payload, dict)
                             else None)
    workspace_ref = (current_workspace_ref
                     if isinstance(current_workspace_ref, dict)
                     else requested_workspace_ref
                     if isinstance(requested_workspace_ref, dict)
                     else {"kind": "workspace", "ref": session_id}
                     if session_id
                     else {"kind": "canvas", "ref": f"canvas:{canvas_id}"})
    requested_actor_ref = a.get("actor_ref")
    actor_ref = (requested_actor_ref if isinstance(requested_actor_ref, dict)
                 else {"kind": "agent", "ref": actor})
    from omnicompany.packages.services.canvas.canvas_observation import decorate_mutation_patch

    patch = decorate_mutation_patch(
        patch,
        actor_ref=actor_ref,
        mutation_id=mutation_id,
    )
    identity = a.get("identity")
    status, payload = _builtin_api(
        "POST", f"/canvases/{urllib.parse.quote(canvas_id, safe='')}/mutations",
        {
            "workspace_ref": workspace_ref,
            "actor_ref": actor_ref,
            "base_revision": a.get("base_revision"),
            "idempotency_key": mutation_id,
            # 写入身份原样透传（机器 / 运行时 / 厂商原生会话 id / 目的），由 canvas-store 校验盖章。
            **({"identity": identity} if isinstance(identity, dict) else {}),
            "patch": patch,
        },
    )
    if status == 409:
        raise OverlayConflict(str(payload.get("detail") or "canvas conflict"))
    if status != 200:
        raise RuntimeError(f"builtin canvas 写失败: HTTP {status} {payload.get('error', '')}")
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    return {
        "json": json.dumps(document, ensure_ascii=False, separators=(",", ":")),
        "revision": payload.get("revision"),
        "updatedAt": document.get("updatedAt"),
        "lastMutation": document.get("lastMutation"),
        "merged": bool(payload.get("merged")),
        "duplicate": bool(payload.get("duplicate")),
    }


def _text_material_put(a: dict) -> dict:
    material_id = _safe(a["id"])
    content = a.get("content")
    if not isinstance(content, str):
        raise ValueError("text material content must be a string")
    status, payload = _builtin_api(
        "PUT", f"/material-notes/text/{urllib.parse.quote(material_id, safe='')}",
        {"content": content},
    )
    if status != 200:
        raise RuntimeError(f"builtin text 写失败: HTTP {status} {payload.get('error', '')}")
    return payload


def _text_material_get(a: dict) -> dict:
    material_id = _safe(a["id"])
    status, payload = _builtin_api("GET", f"/material-notes/text/{urllib.parse.quote(material_id, safe='')}")
    if status == 404:
        raise FileNotFoundError(f"text material not found: {material_id}")
    if status != 200:
        raise RuntimeError(f"builtin text 读失败: HTTP {status} {payload.get('error', '')}")
    return payload

def _text_material_get_many(a: dict) -> dict:
    """批量取正文(画布一次挂载十几张文字卡只走一次桥往返), 契约同 Workshop canvas-bridge:
    {items: {id: 正文载荷}, missing: [id]}; 单条缺失进 missing, 不拖垮整批。"""
    ids = a.get("ids") if isinstance(a.get("ids"), list) else []
    if len(ids) > 200:
        raise ValueError(f"notes_text_get_many 一次最多 200 条, 收到 {len(ids)}")
    items: dict[str, dict] = {}
    missing: list[str] = []
    for raw in ids:
        material_id = str(raw or "")
        if not material_id:
            continue
        try:
            items[material_id] = _text_material_get({"id": material_id})
        except Exception:
            missing.append(material_id)
    return {"items": items, "missing": missing}


def _snapshot_capture(a: dict) -> dict:
    """冻结捕获: 把活 web 页烘焙成单文件静态 HTML 快照(衍生物, 重冻即覆盖重建)。"""
    from omnicompany.dashboard.controlplane.overlay_snapshot import capture_page_to_single_html

    snapshot_id = _safe(a["id"])
    url = str(a.get("url") or "")
    path = _snapshots() / f"{snapshot_id}.html"
    stats = capture_page_to_single_html(url, path)
    token = _sign_file_path(str(path))
    return {
        **_stat_payload(path, token=token),
        "snapshotId": snapshot_id,
        "stats": stats,
    }


def _legacy_notes() -> list[dict]:
    """List old BlockSuite notes as read-only migration sources.

    The new canvas stores only the legacy id.  Markdown remains in the old
    note store, so importing a card does not create a second body authority.
    """
    try:
        payload = json.loads((_ROOT / "index.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as error:
        raise ValueError("legacy note index is invalid") from error
    notes = payload.get("notes") if isinstance(payload, dict) else None
    if not isinstance(notes, list):
        return []
    out = []
    for raw in notes:
        if not isinstance(raw, dict):
            continue
        note_id = str(raw.get("id") or "")
        try:
            safe_id = _safe(note_id)
        except ValueError:
            continue
        md_path = _docs() / f"{safe_id}.md"
        out.append({
            "id": note_id,
            "title": str(raw.get("title") or "未命名札记"),
            "updatedDate": raw.get("updatedDate"),
            "hasMarkdown": md_path.is_file(),
        })
    return out


def _legacy_note_get(a: dict) -> dict:
    note_id = _safe(a["id"])
    notes = {item["id"]: item for item in _legacy_notes()}
    if note_id not in notes:
        raise FileNotFoundError(f"legacy note not found: {note_id}")
    path = _docs() / f"{note_id}.md"
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = ""
    return {**notes[note_id], "content": content}


def _overlay_http_token() -> str:
    configured = os.environ.get("OVERLAY_SHELL_HTTP_TOKEN", "").strip()
    if configured:
        return configured
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    # Legacy compatibility: existing installs may only have ~/.poof/rec_token.
    for path in (home / ".overlay-shell" / "rec_token", home / ".poof" / "rec_token"):
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if token:
            return token
    raise OverlaySearchUnavailable("Overlay Shell 文件索引未连接：找不到本机访问令牌")


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical_file_path(raw: str) -> str:
    if not raw or "\x00" in raw:
        raise ValueError("非法文件路径")
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError("文件路径必须是绝对路径")
    return os.path.normpath(str(path))


def _file_token_key() -> bytes:
    token = _overlay_http_token().encode("utf-8")
    return hashlib.sha256(b"omnidashboard-overlay-file-v1\0" + token).digest()


def _sign_file_path(path: str, *, root: str | None = None) -> str:
    """为搜索命中及其目录子项签发只读网页打开令牌。

    令牌把路径绑定到最初命中的根目录。目录浏览只能向下，不能借一个子目录令牌
    越权跳到同盘其他位置；搜索命中的单文件则只允许读取该文件。
    """
    canonical = _canonical_file_path(path)
    canonical_root = _canonical_file_path(root or canonical)
    try:
        if os.path.commonpath([canonical, canonical_root]) != canonical_root:
            raise ValueError("文件路径超出搜索命中范围")
    except ValueError:
        raise ValueError("文件路径超出搜索命中范围") from None
    payload_obj = {"v": 1, "p": canonical}
    if canonical_root != canonical:
        payload_obj["r"] = canonical_root
    payload = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = hmac.new(_file_token_key(), payload, hashlib.sha256).digest()
    return f"{_b64url_encode(payload)}.{_b64url_encode(signature)}"


def _file_path_from_token(token: str) -> tuple[Path, str]:
    try:
        payload_text, signature_text = token.split(".", 1)
        payload = _b64url_decode(payload_text)
        signature = _b64url_decode(signature_text)
    except (ValueError, TypeError):
        raise ValueError("无效的文件打开令牌") from None
    expected = hmac.new(_file_token_key(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("无效的文件打开令牌")
    try:
        decoded = json.loads(payload.decode("utf-8"))
        if decoded.get("v") != 1:
            raise ValueError
        path = _canonical_file_path(str(decoded["p"]))
        root = _canonical_file_path(str(decoded.get("r") or path))
        if os.path.commonpath([path, root]) != root:
            raise ValueError
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("无效的文件打开令牌") from None
    return Path(path), root


def _file_mime(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _file_preview_kind(path: Path, mime: str) -> str:
    suffix = path.suffix.lower()
    stem = path.name.lower().split(".", 1)[0]
    if suffix in _TEXT_EXTS or stem in _TEXT_NAMES or mime.startswith("text/"):
        return "text"
    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    return "none"


def _decode_text_preview(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def _stat_payload(path: Path, *, token: str, kind: str | None = None) -> dict:
    stat = path.stat()
    mime = "inode/directory" if path.is_dir() else _file_mime(path)
    return {
        "name": path.name or str(path),
        "path": str(path),
        "kind": kind or ("folder" if path.is_dir() else "file"),
        "size": stat.st_size,
        "modified_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
        "mime": mime,
        "open_token": token,
    }


def _inspect_file(a: dict) -> dict:
    token = str(a.get("token") or "")
    path, root = _file_path_from_token(token)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{path}")
    result = _stat_payload(path, token=token)
    if path.is_dir():
        try:
            children = sorted(
                (child for child in path.iterdir() if child.name.lower() not in _OS_JUNK),
                key=lambda child: (not child.is_dir(), child.name.lower()),
            )
        except OSError as error:
            raise ValueError(f"无法读取目录：{error}") from error
        result["truncated"] = len(children) > _MAX_DIRECTORY_ITEMS
        items = []
        for child in children[:_MAX_DIRECTORY_ITEMS]:
            try:
                child_token = _sign_file_path(str(child), root=root)
                items.append(_stat_payload(child, token=child_token))
            except OSError:
                # 单个失效的快捷方式/竞态条目不应让整个目录打不开。
                continue
        result["items"] = items
        result["preview"] = "directory"
        return result

    preview = _file_preview_kind(path, result["mime"])
    result["preview"] = preview
    if preview == "text":
        with path.open("rb") as handle:
            raw = handle.read(_MAX_FILE_TEXT_BYTES + 1)
        result["truncated"] = len(raw) > _MAX_FILE_TEXT_BYTES
        content, encoding = _decode_text_preview(raw[:_MAX_FILE_TEXT_BYTES])
        result["content"] = content
        result["encoding"] = encoding
    return result


def _overlay_search(a: dict):
    query = str(a.get("query") or "").strip()
    if not query:
        return []
    try:
        limit = max(1, min(int(a.get("limit") or 40), 100))
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer") from None
    payload = json.dumps({"query": query, "limit": limit}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{_SEARCH_HTTP_URL}/search",
        data=payload,
        headers={
            "Authorization": f"Bearer {_overlay_http_token()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise OverlaySearchUnavailable(f"Overlay Shell 文件索引未连接：{error}") from error
    if not isinstance(result, list):
        raise OverlaySearchUnavailable("Overlay Shell 文件索引返回了无效结果")
    signed = []
    for raw_hit in result:
        if not isinstance(raw_hit, dict):
            continue
        try:
            hit = dict(raw_hit)
            hit["open_token"] = _sign_file_path(str(hit.get("path") or ""))
        except ValueError:
            continue
        signed.append(hit)
    return signed


def _clip_latest():
    """远程剪切板同步：只取工作站剪贴板最新一条（文本才带内容）。

    刻意不代理历史浏览/按 id 抓取——剪贴板历史里几乎必然有密码/token，
    暴露面收敛在 overlay-shell 侧（/clipboard/latest 只回最新一条）。
    """
    request = urllib.request.Request(
        f"{_SEARCH_HTTP_URL}/clipboard/latest",
        data=b"{}",
        headers={
            "Authorization": f"Bearer {_overlay_http_token()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise OverlaySearchUnavailable(f"Overlay Shell 剪贴板服务未连接：{error}") from error


def _dispatch(cmd: str, a: dict):
    if cmd == "nb_pending":
        return _notes_bridge_pending()
    if cmd == "nb_respond":
        _notes_bridge_respond(a); return None
    if cmd == "cb_pending":
        return _canvas_bridge_pending(str(a.get("canvas") or ""))
    if cmd == "cb_respond":
        _canvas_bridge_respond(a); return None
    if cmd == "cb_push":
        return _canvas_bridge_push(a)
    if cmd == "search":
        return _overlay_search(a)
    if cmd == "clip_latest":
        return _clip_latest()
    if cmd == "file_inspect":
        return _inspect_file(a)
    if cmd == "notes_root":
        _ensure(); return str(_ROOT)
    if cmd == "notes_doc_get":
        return _read_b64(_docs() / f"{_safe(a['id'])}.ydoc")
    if cmd == "notes_doc_put":
        _ensure(); _atomic_write(_docs() / f"{_safe(a['id'])}.ydoc", base64.b64decode(a["b64"])); return None
    if cmd == "notes_doc_del":
        (_docs() / f"{_safe(a['id'])}.ydoc").unlink(missing_ok=True); return None
    if cmd == "notes_doc_keys":
        return _list_keys(_docs(), ".ydoc")
    if cmd == "notes_canvas_get":
        return _canvas_get(a)
    if cmd == "notes_canvas_head":
        return _canvas_head(a)
    if cmd == "notes_canvas_mutate":
        return _canvas_mutate(a)
    if cmd == "notes_canvas_mutations":
        return _canvas_mutations(a)
    if cmd == "notes_canvas_annotate":
        return _canvas_annotate(a)
    if cmd == "notes_write_identity":
        return _write_identity_facts()
    if cmd == "notes_canvas_put":
        raise ValueError("notes_canvas_put 已随画布契约归档(2026-08-16): 写入一律走 notes_canvas_mutate")
    if cmd == "notes_canvas_del":
        raise ValueError("notes_canvas_del 已归档: 画布删除不再经本桥")
    if cmd == "notes_canvas_keys":
        return _canvas_keys()
    if cmd == "notes_canvas_recent":
        return _canvas_recent(a)
    if cmd == "notes_canvas_history":
        return _canvas_history(a)
    if cmd == "notes_canvas_resolve":
        return _canvas_resolve(a)
    if cmd == "notes_canvas_open":
        return _canvas_open(a)
    if cmd == "notes_canvas_favorite":
        return _canvas_favorite(a)
    if cmd == "notes_text_put":
        return _text_material_put(a)
    if cmd == "notes_text_get":
        return _text_material_get(a)
    if cmd == "notes_text_get_many":
        return _text_material_get_many(a)
    if cmd == "notes_text_del":
        raise ValueError("notes_text_del 已归档: 正文删除不再经本桥")
    if cmd == "notes_snapshot_capture":
        return _snapshot_capture(a)
    if cmd == "notes_legacy_list":
        return _legacy_notes()
    if cmd == "notes_legacy_get":
        return _legacy_note_get(a)
    if cmd == "notes_blob_get":
        return _read_b64(_blobs() / _safe(a["key"]))
    if cmd == "notes_blob_put":
        _ensure(); _atomic_write(_blobs() / _safe(a["key"]), base64.b64decode(a["b64"])); return None
    if cmd == "notes_blob_del":
        (_blobs() / _safe(a["key"])).unlink(missing_ok=True); return None
    if cmd == "notes_blob_keys":
        return _list_keys(_blobs(), None)
    if cmd == "notes_md_put":
        _ensure(); _atomic_write(_docs() / f"{_safe(a['id'])}.md", a["content"].encode("utf-8")); return None
    if cmd == "notes_md_del":
        (_docs() / f"{_safe(a['id'])}.md").unlink(missing_ok=True); return None
    if cmd == "notes_index_put":
        _ensure(); _atomic_write(_ROOT / "index.json", a["json"].encode("utf-8")); return None
    if cmd == "notes_version_put":
        d = _versions(_safe(_did(a))); d.mkdir(parents=True, exist_ok=True)
        _atomic_write(d / f"{_safe(a['ts'])}.json", a["json"].encode("utf-8")); return None
    if cmd == "notes_version_all":
        d = _versions(_safe(_did(a))); out = []
        if d.is_dir():
            for p in d.iterdir():
                if p.name.startswith(".") or not p.name.endswith(".json"):
                    continue
                try:
                    out.append({"ts": p.name[:-5], "json": p.read_text(encoding="utf-8")})
                except Exception:
                    pass
        return out
    if cmd == "notes_version_del_one":
        (_versions(_safe(_did(a))) / f"{_safe(a['ts'])}.json").unlink(missing_ok=True); return None
    if cmd == "notes_version_del_all":
        import shutil
        shutil.rmtree(_versions(_safe(_did(a))), ignore_errors=True); return None
    raise ValueError(f"unknown cmd: {cmd}")


async def _invoke_payload(body: object) -> tuple[dict, int]:
    """Run one note-authority command without blocking the ASGI event loop."""
    if not isinstance(body, dict):
        return {"ok": False, "error": "invalid_json"}, 400
    cmd = str(body.get("cmd") or "")
    raw_args = body.get("args")
    args = {} if raw_args is None else raw_args
    if not isinstance(args, dict):
        return {"ok": False, "error": "args must be an object"}, 400
    try:
        result = await asyncio.to_thread(_dispatch, cmd, args)
        return {"ok": True, "result": result}, 200
    except OverlayConflict as error:
        return {"ok": False, "error": str(error), "conflict": True}, 409
    except OverlaySearchUnavailable as error:
        return {"ok": False, "error": str(error)}, 503
    except ValueError as error:
        return {"ok": False, "error": str(error)}, 400
    except FileNotFoundError as error:
        return {"ok": False, "error": str(error)}, 404
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}, 500


@overlay_notes_router.get("/lofa/poof/app", include_in_schema=False)  # legacy URL alias
@overlay_notes_router.get("/lofa/poof/app/{path:path}", include_in_schema=False)
@overlay_notes_router.get("/lofa/overlay/app")
@overlay_notes_router.get("/lofa/overlay/app/{path:path}")
# 2026-08-18 统一应用鉴权: dashboard 嵌入面(右侧边栏画布/看板笔记页/quest 链接)一律走
# /api/embed/notes-web, 与其余 dashboard API 同一条网关鉴权路径, 不再单独撞
# /lofa/overlay/app 的 basic_auth 兜底(桌面端画布 iframe 里弹登录表单的根因)。
# /lofa/* 原路径保留给 overlay-shell 与 LOFA 客户端, 行为不变。
@overlay_notes_router.get("/api/embed/notes-web")
@overlay_notes_router.get("/api/embed/notes-web/{path:path}")
async def canvas_web(path: str = ""):
    """服务唯一画布前端 notes-web 静态产物(dashboard/notes-web/dist-web), 经 8210 一个口。看板/LOFA app 都嵌这个。"""
    rel = path or "notes-web.html"
    base = _WEB_DIR.resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    if not target.is_file():
        target = base / "notes-web.html"   # 兜底回 index
        if not target.is_file():
            return JSONResponse({"error": "canvas not built (run npm run build in webworks/apps/material-canvas)"}, status_code=404)
    # no-store: 入口页; no-cache: 资产(未哈希文件名, 必须每次回源校验, 否则刷新也拿旧构建)
    headers = {"Cache-Control": "no-store"} if target.name == "notes-web.html" else {"Cache-Control": "no-cache"}
    return FileResponse(str(target), headers=headers)


@overlay_notes_router.post("/lofa/poof/invoke", include_in_schema=False)  # legacy URL alias
@overlay_notes_router.post("/lofa/overlay/invoke")
@overlay_notes_router.post("/api/embed/lofa/overlay/invoke")
async def canvas_invoke(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    payload, status = await _invoke_payload(body)
    return JSONResponse(payload, status_code=status)


@overlay_notes_router.websocket("/lofa/poof/invoke-ws")  # legacy URL alias
@overlay_notes_router.websocket("/lofa/overlay/invoke-ws")
@overlay_notes_router.websocket("/api/embed/lofa/overlay/invoke-ws")
async def canvas_invoke_websocket(websocket: WebSocket):
    """Multiplex note commands over one connection to avoid HTTP/1 pool stalls."""
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                body = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                await websocket.send_json({
                    "id": None,
                    "ok": False,
                    "error": "invalid_json",
                    "status": 400,
                })
                continue
            request_id = body.get("id") if isinstance(body, dict) else None
            payload, status = await _invoke_payload(body)
            response = {"id": request_id, **payload}
            if status != 200:
                response["status"] = status
            await websocket.send_json(response)
    except WebSocketDisconnect:
        return


@overlay_notes_router.get("/lofa/overlay/file/{token}")
@overlay_notes_router.get("/api/embed/lofa/overlay/file/{token}")
async def overlay_file_content(token: str):
    """以签名令牌流式提供图片/PDF/音视频；不接受裸路径。"""
    try:
        path, _root = _file_path_from_token(token)
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=403)
    if not path.exists():
        return JSONResponse({"error": f"文件不存在：{path}"}, status_code=404)
    if not path.is_file():
        return JSONResponse({"error": "目标不是文件"}, status_code=400)
    media_type = _file_mime(path)
    # .html 放行给冻结快照材料(烘焙时已剥 script; 卡片内渲染另走 sandbox iframe 双保险)
    if (_file_preview_kind(path, media_type) not in {"image", "pdf", "audio", "video"}
            and path.suffix.lower() not in {".html", ".htm"}):
        return JSONResponse({"error": "该文件类型不通过流式预览接口提供"}, status_code=415)
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return FileResponse(str(path), media_type=media_type, headers=headers)


def _session_catalog() -> dict[str, dict[str, str]]:
    """PTY id -> title / plan / todo / transcript locators from durable stores."""
    catalog: dict[str, dict[str, str]] = {}

    def _put(sid: str, **fields: str) -> None:
        if not sid:
            return
        row = catalog.setdefault(sid, {})
        for key, value in fields.items():
            if value and not row.get(key):
                row[key] = value

    try:
        from omnicompany.core.config import omni_workspace_root
        root = omni_workspace_root() / "data"
    except Exception:
        root = Path()
    for name in ("cc_sessions.json", "cc_sessions.archive.json"):
        path = root / name if root else Path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for sid, meta in payload.items():
            if not isinstance(meta, dict):
                continue
            title = (
                str(meta.get("display_title") or "").strip()
                or str(meta.get("summary_title") or "").strip()
                or str(meta.get("provider_title") or "").strip()
                or str(meta.get("topic") or "").strip()
            )
            _put(
                str(sid),
                title=title,
                plan_id=str(meta.get("active_plan") or "").strip(),
                todo_id=str(meta.get("task_id") or "").strip(),
                provider=str(meta.get("provider") or "").strip(),
                provider_session_id=str(
                    meta.get("provider_session_id") or meta.get("claude_session_id") or ""
                ).strip(),
                cwd=str(meta.get("cwd") or "").strip(),
            )
    try:
        from omnicompany.packages.services._core.identity import all_session_bindings
        bindings = all_session_bindings() or {}
    except Exception:
        bindings = {}
    if isinstance(bindings, dict):
        for key, rec in bindings.items():
            if not isinstance(rec, dict):
                continue
            sid = str(
                rec.get("pty_id") or rec.get("session_id") or rec.get("claude_session_id") or key
            )
            _put(
                sid,
                title=str(rec.get("topic") or rec.get("display_title") or "").strip(),
                plan_id=str(rec.get("active_plan") or rec.get("plan_id") or "").strip(),
                todo_id=str(rec.get("task_id") or "").strip(),
            )
            _put(
                str(key),
                plan_id=str(rec.get("active_plan") or rec.get("plan_id") or "").strip(),
                todo_id=str(rec.get("task_id") or "").strip(),
                title=str(rec.get("topic") or "").strip(),
            )
    return catalog


def _session_scope(session_id: str, catalog: dict[str, dict[str, str]]) -> tuple[str, str]:
    from .canvas_session_index import bare_session_id
    pty = bare_session_id(session_id)
    row = catalog.get(pty) or catalog.get(session_id) or {}
    return str(row.get("plan_id") or ""), str(row.get("todo_id") or "")


def _session_excerpt(session_id: str, query: str, catalog: dict[str, dict[str, str]]) -> str:
    needle = str(query or "").strip()
    if not needle:
        return ""
    from .canvas_session_index import bare_session_id
    pty = bare_session_id(session_id)
    row = catalog.get(pty) or catalog.get(session_id) or {}
    provider = row.get("provider") or ""
    provider_session_id = row.get("provider_session_id") or ""
    cwd = row.get("cwd") or ""
    path = None
    try:
        from omnicompany.dashboard.ccdaemon.pty import _conversation_jsonl_for
        path = _conversation_jsonl_for(provider, cwd, provider_session_id)
    except Exception:
        path = None
    if path is None or not getattr(path, "is_file", lambda: False)():
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")[:256_000]
    except OSError:
        return ""
    return text if needle.casefold() in text.casefold() else ""


def _canvas_scope_args(a: dict) -> tuple[str, str, str]:
    from .canvas_session_index import bare_session_id
    session_id = str(a.get("session_id") or a.get("session") or "")
    catalog = _session_catalog()
    plan_id, todo_id = _session_scope(session_id, catalog)
    plan_id = str(a.get("plan_id") or plan_id)
    todo_id = str(a.get("todo_id") or a.get("task_id") or todo_id)
    return bare_session_id(session_id), plan_id, todo_id


def _canvas_resolve(a: dict) -> dict:
    from . import canvas_session_index as csi
    session_id, plan_id, todo_id = _canvas_scope_args(a)
    if not session_id:
        raise ValueError("session_id is required")
    with csi._LOCK:
        index = csi.load_index(_ROOT)
        result, next_index = csi.resolve_session_canvas(
            index, session_id=session_id, plan_id=plan_id, todo_id=todo_id,
        )
        csi.save_index(_ROOT, next_index)
    return result


def _canvas_open(a: dict) -> dict:
    from . import canvas_session_index as csi
    session_id, plan_id, todo_id = _canvas_scope_args(a)
    canvas_id = str(a.get("canvas_id") or a.get("id") or "")
    if not session_id:
        raise ValueError("session_id is required")
    if not canvas_id:
        raise ValueError("canvas_id is required")
    with csi._LOCK:
        index = csi.load_index(_ROOT)
        result, next_index = csi.open_session_canvas(
            index,
            session_id=session_id,
            canvas_id=canvas_id,
            plan_id=plan_id,
            todo_id=todo_id,
        )
        csi.save_index(_ROOT, next_index)
    return result


def _canvas_favorite(a: dict) -> dict:
    from . import canvas_session_index as csi
    canvas_id = str(a.get("canvas_id") or a.get("id") or "")
    with csi._LOCK:
        index = csi.load_index(_ROOT)
        result = csi.toggle_favorite(index, canvas_id)
        csi.save_index(_ROOT, result["index"])
    return {"canvas_id": result["canvas_id"], "favorite": result["favorite"]}


def _canvas_history(a: dict) -> dict:
    from omnicompany.packages.services.canvas.canvas_observation import history_canvases
    from . import canvas_session_index as csi
    try:
        limit = int(a.get("limit") or 50)
    except (TypeError, ValueError):
        raise ValueError("limit must be an integer") from None
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    query = str(a.get("query") or a.get("q") or "")
    session_id, _, _ = _canvas_scope_args(a)
    index = csi.load_index(_ROOT)
    current_id = ""
    if session_id:
        current = index.get("sessions", {}).get(session_id)
        if isinstance(current, dict):
            current_id = str(current.get("canvas_id") or "")
    status, payload = _builtin_api("GET", "/canvases")
    if status != 200:
        raise RuntimeError(f"builtin canvas history 失败: HTTP {status} {payload.get('error', '')}")
    rows = [item for item in payload.get("canvases") or [] if isinstance(item, dict)]
    catalog = _session_catalog()
    session_titles = {
        sid: str(row.get("title") or "")
        for sid, row in catalog.items()
        if row.get("title")
    }
    documents: dict[str, dict] = {}
    material_texts: dict[str, str] = {}
    session_excerpts: dict[str, str] = {}
    if query.strip():
        needle = query.strip().casefold()
        canvas_dir = _ROOT / "canvases"
        if canvas_dir.is_dir():
            for path in canvas_dir.glob("*.json"):
                canvas_id = path.stem
                try:
                    raw = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                try:
                    document = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(document, dict):
                    documents[canvas_id] = document
        materials_dir = _text_materials()
        if materials_dir.is_dir():
            for path in materials_dir.glob("*.md"):
                try:
                    body = path.read_text(encoding="utf-8")
                except OSError:
                    continue
                if needle in body.casefold():
                    material_texts[path.stem] = body
        unmatched = []
        for row in rows:
            canvas_id = str(row.get("document_id") or "")
            sid = csi.bare_session_id(canvas_id)
            title_bits = " ".join(filter(None, [
                session_titles.get(sid, ""),
                str(row.get("title") or ""),
            ])).casefold()
            if needle not in title_bits:
                unmatched.append(sid)
        for sid in unmatched[:40]:
            excerpt = _session_excerpt(sid, query, catalog)
            if excerpt:
                session_excerpts[sid] = excerpt
    items = history_canvases(
        rows,
        session_titles=session_titles,
        documents=documents,
        material_texts=material_texts,
        session_excerpts=session_excerpts,
        favorites=index.get("favorites") or [],
        current_id=current_id,
        query=query,
        limit=limit,
    )
    return {
        "source": "modern-canvas-store",
        "items": items,
        "count": len(items),
        "limit": limit,
        "current_id": current_id,
        "favorites": list(index.get("favorites") or []),
    }


def _session_from_canvas_key(key: str) -> str:
    """session-<base64url(external:<sid>)> 反解(与 cli/commands/canvas.py 同构)。"""
    try:
        encoded = key[len("session-"):]
        encoded += "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(encoded).decode("utf-8")
        return raw[len("external:"):] if raw.startswith("external:") else raw
    except Exception:
        return key


def iter_submission_cards():
    """遍历所有画布中带 submission 的卡 —— 转发到共享核心，本文件不再自持实现。

    2026-08-20 审阅库融合: 读源唯一真源移到 `services/_core/review_store`。
    在此之前这里是三套画布扫描之一（另两处是 boss_sight 简报读旧 reviewstage、
    material wiki 置顶跨层 import 本函数），三处口径不一致导致同一屏两个数字对不上。
    保留本函数名是为了不打断既有 dashboard 侧调用点；新增读源一律加到共享核心。

    历史坑（别再犯）: 2026-08-19 修过一次回归 —— 这里曾直读 canvases/*.json 并调用
    同一次提交里已删除的 _read_canvas_document，NameError 被 per-canvas except 静默吞掉,
    review-queue / mandatory 硬闸 / wiki 置顶组一律恒空 3 天。读源整体不可达要让它抛出去。
    """
    from omnicompany.packages.services._core import review_store

    return review_store.iter_submission_cards()


def _build_review_queue_payload() -> dict[str, object]:
    """Build the review queue without running its canvas scan on the event loop."""
    items: list[dict] = []
    for hit in iter_submission_cards():
        card = hit["card"]
        items.append({
            "canvas": hit["canvas"],
            "session_id": hit["session_id"],
            "card": card.get("id"),
            "title": card.get("title"),
            "kind": card.get("kind"),
            "adapter": card.get("adapter"),
            "canvasTitle": hit["canvasTitle"],
            # 内联预览用(M1b 全模态): file 卡的 path/token/mime 随行; 其它 kind 为 None
            "source": card.get("source") if isinstance(card.get("source"), dict) else None,
            "submission": hit["submission"],
        })
    items.sort(key=lambda item: str(item["submission"].get("submittedAt") or ""), reverse=True)
    return {"ok": True, "items": items, "count": len(items)}


@overlay_notes_router.get("/lofa/overlay/review-queue")
async def overlay_review_queue():
    """只读聚合(M1b): 所有画布里带 submission 的卡, 按 submittedAt 倒序。

    每项: canvas(画布 key) / session_id(反解的 pty 会话) / card / title / kind / adapter /
    canvasTitle / submission 全字段。跳回深链形态:
    /lofa/overlay/app/?session_id=<session_id>&focus=<card>。
    """
    # review_store scans every canvas and performs synchronous HTTP reads.  Keep
    # that whole read-side projection off the dashboard event loop; the cache
    # lock may still serialize concurrent scans, but it now only occupies a
    # worker thread instead of starving /api/cc and WebSocket traffic.
    return await asyncio.to_thread(_build_review_queue_payload)
