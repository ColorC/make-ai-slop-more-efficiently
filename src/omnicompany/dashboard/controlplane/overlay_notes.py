# [OMNI] origin=ai-ide ts=2026-06-28 type=infra
"""controlplane/overlay_notes.py — overlay note store(BlockSuite/Yjs) 的 HTTP 桥。

overlay-shell 桌面端把笔记落在 C:/workspace/overlay-note-store(docs/<id>.ydoc 等),走 Tauri 命令读写。
LOFA 网页/手机端没有 Tauri,这里把同一套命令做成 HTTP,落同一个目录。对外只走 8210 一个口。
`/lofa/poof/*` 保留为旧路由兼容;新路由是 `/lofa/overlay/*`。

镜像的命令(契约对齐 src-tauri/src/notesstore.rs):
  notes_root / notes_doc_{get,put,del,keys} / notes_blob_{get,put,del,keys}
  notes_md_{put,del} / notes_index_put / notes_version_{put,all,del_one,del_all}

并发: 与桌面 overlay-shell 都用 原子写(临时文件+rename); 快照级 last-write-wins(暂不做 Yjs 合并)。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

overlay_notes_router = APIRouter(tags=["overlay-note-store"])

_ROOT = Path(
    os.environ.get("OVERLAY_NOTE_STORE_ROOT")
    or os.environ.get("POOF_NOTES_ROOT", r"C:/workspace/overlay-note-store")
)
_WEB_DIR = Path(
    os.environ.get("OVERLAY_SHELL_WEB_DIR")
    or os.environ.get("POOF_WEB_DIR", r"C:/workspace/overlay-shell\dist-web")
)
_SEARCH_HTTP_URL = os.environ.get("OVERLAY_SHELL_HTTP_URL", "http://127.0.0.1:8732").rstrip("/")
_MAX_FILE_TEXT_BYTES = 2 * 1024 * 1024
_MAX_DIRECTORY_ITEMS = 500
_TEXT_EXTS = {
    ".bat", ".c", ".cc", ".cfg", ".cmd", ".conf", ".cpp", ".cs", ".css", ".csv",
    ".env", ".go", ".h", ".hpp", ".html", ".ini", ".java", ".js", ".json", ".jsx",
    ".log", ".lua", ".md", ".mjs", ".ps1", ".py", ".rs", ".scss", ".sh", ".sql",
    ".svg", ".toml", ".ts", ".tsx", ".txt", ".uxml", ".vue", ".xml", ".yaml", ".yml",
}
_TEXT_NAMES = {"license", "makefile", "readme"}
_OS_JUNK = {"$recycle.bin", "system volume information"}


class OverlaySearchUnavailable(RuntimeError):
    """The local Overlay Shell index process is not ready/reachable."""


def _docs() -> Path: return _ROOT / "docs"
def _blobs() -> Path: return _ROOT / "blobs"
def _versions(doc_id: str) -> Path: return _ROOT / "versions" / doc_id


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
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_bytes(data)
    os.replace(tmp, path)


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


def _overlay_http_token() -> str:
    configured = os.environ.get("OVERLAY_SHELL_HTTP_TOKEN", "").strip()
    if configured:
        return configured
    home = Path(os.environ.get("USERPROFILE") or Path.home())
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


def _dispatch(cmd: str, a: dict):
    if cmd == "search":
        return _overlay_search(a)
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


@overlay_notes_router.get("/lofa/poof/app")
@overlay_notes_router.get("/lofa/poof/app/{path:path}")
@overlay_notes_router.get("/lofa/overlay/app")
@overlay_notes_router.get("/lofa/overlay/app/{path:path}")
async def poof_web(path: str = ""):
    """服务 overlay-shell 笔记网页静态产物(dist-web), 经 8210 一个口。看板/LOFA app 都嵌这个。"""
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
            return JSONResponse({"error": "overlay-shell web not built (run vite build --config vite.web.config.ts)"}, status_code=404)
    headers = {"Cache-Control": "no-store"} if target.name == "notes-web.html" else None
    return FileResponse(str(target), headers=headers)


@overlay_notes_router.post("/lofa/poof/invoke")
@overlay_notes_router.post("/lofa/overlay/invoke")
async def poof_invoke(req: Request):
    try:
        body = await req.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
    cmd = str(body.get("cmd") or "")
    args = body.get("args") or {}
    try:
        # 文件索引走本机 HTTP 服务，放在线程池避免阻塞 FastAPI 事件循环；笔记的短磁盘操作
        # 保持原同步路径，避免扩大现有行为差异。
        result = await asyncio.to_thread(_dispatch, cmd, args) if cmd in {"search", "file_inspect"} else _dispatch(cmd, args)
        return {"ok": True, "result": result}
    except OverlaySearchUnavailable as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)


@overlay_notes_router.get("/lofa/overlay/file/{token}")
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
    if _file_preview_kind(path, media_type) not in {"image", "pdf", "audio", "video"}:
        return JSONResponse({"error": "该文件类型不通过流式预览接口提供"}, status_code=415)
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return FileResponse(str(path), media_type=media_type, headers=headers)
