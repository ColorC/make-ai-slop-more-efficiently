"""Controlled browser/app file bridge for the Dashboard host.

The bridge deliberately has two narrow capabilities:

* multipart uploads land in a dedicated staging directory and return the
  machine-local absolute path that an agent can use;
* read-only browsing stays below explicitly configured roots.

No delete, rename, edit, execute, arbitrary absolute-path, or whole-home
endpoint is exposed.
"""
from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import hmac
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import threading
import time
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from omnicompany.core.config import omni_workspace_root
from omnicompany.dashboard.session_workdir import default_session_cwd


file_bridge_router = APIRouter(
    prefix="/lofa/file-bridge",
    tags=["file-bridge"],
)

_REQUEST_HEADER = "x-omni-file-bridge"
_REQUEST_HEADER_VALUE = "1"
_MAX_DIRECTORY_ITEMS = 500
_MAX_TEXT_PREVIEW_BYTES = 2 * 1024 * 1024
_DEFAULT_MAX_FILE_BYTES = 512 * 1024 * 1024
_DEFAULT_MAX_BATCH_BYTES = 1024 * 1024 * 1024
_DEFAULT_MAX_FILES = 50
_TOKEN_TTL_SECONDS = 30 * 60
_CHUNK_BYTES = 1024 * 1024
_MAX_HISTORY_ITEMS = 200
_HISTORY_LOCK = threading.Lock()
_OS_JUNK = {"$recycle.bin", "system volume information"}
_TEXT_EXTS = {
    ".bat", ".c", ".cc", ".cfg", ".cmd", ".conf", ".cpp", ".cs", ".css", ".csv",
    ".env", ".go", ".h", ".hpp", ".html", ".ini", ".java", ".js", ".json", ".jsx",
    ".log", ".lua", ".md", ".mjs", ".ps1", ".py", ".rs", ".scss", ".sh", ".sql",
    ".svg", ".toml", ".ts", ".tsx", ".txt", ".uxml", ".vue", ".xml", ".yaml", ".yml",
}
_TEXT_NAMES = {"license", "makefile", "readme"}
_SAFE_INLINE_MIME_PREFIXES = ("image/", "audio/", "video/")
_SAFE_INLINE_MIMES = {"application/pdf"}
_WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


class BridgeLocation(BaseModel):
    root_id: str
    path: str = ""


def _staging_root() -> Path:
    configured = os.environ.get("OMNI_FILE_BRIDGE_STAGING_ROOT", "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser().resolve()
    return (Path(default_session_cwd()) / "temp" / "omni-file-bridge").resolve()


def _root_specs() -> list[dict[str, Any]]:
    roots: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(root_id: str, label: str, path: Path, *, writable: bool = False) -> None:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return
        key = os.path.normcase(str(resolved))
        if key in seen or (not writable and not resolved.is_dir()):
            return
        seen.add(key)
        roots.append({
            "id": root_id,
            "label": label,
            "path": str(resolved),
            "writable": writable,
            "available": resolved.is_dir(),
        })

    add("staging", "上传暂存区", _staging_root(), writable=True)
    add("workspace", "WindowsWorkspace", Path(default_session_cwd()))
    add("aiworkspace", "AIWorkSpace", Path(r"D:\P4\main\AIWorkSpace"))

    configured = os.environ.get("OMNI_FILE_BRIDGE_ROOTS", "")
    for index, value in enumerate(configured.split(";"), start=1):
        raw = value.strip()
        if not raw:
            continue
        add(f"extra-{index}", Path(raw).name or raw, Path(os.path.expandvars(raw)))
    return roots


def _root_by_id(root_id: str) -> dict[str, Any]:
    root = next((item for item in _root_specs() if item["id"] == root_id), None)
    if root is None:
        raise HTTPException(status_code=404, detail="未知或当前不可用的浏览根目录")
    return root


def _require_bridge_request(request: Request) -> None:
    """Reject form/iframe CSRF while keeping browser and LOFA same-origin use simple."""
    if request.headers.get(_REQUEST_HEADER) != _REQUEST_HEADER_VALUE:
        raise HTTPException(status_code=403, detail="缺少文件桥同源请求标记")


def _safe_relative_path(raw: str) -> PurePosixPath:
    value = str(raw or "").replace("\\", "/").strip("/")
    if not value:
        return PurePosixPath(".")
    if "\x00" in value:
        raise HTTPException(status_code=400, detail="非法路径")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(":" in part for part in relative.parts)
    ):
        raise HTTPException(status_code=400, detail="路径必须是根目录内的相对路径")
    return relative


def _resolve_location(root_id: str, relative_path: str, *, must_exist: bool = True) -> tuple[Path, Path]:
    root_info = _root_by_id(root_id)
    root = Path(root_info["path"]).resolve()
    relative = _safe_relative_path(relative_path)
    try:
        target = (root / Path(*relative.parts)).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        raise HTTPException(status_code=403, detail="路径超出允许的浏览根目录") from None
    if must_exist and not target.exists():
        raise HTTPException(status_code=404, detail="路径不存在")
    return root, target


def _relative_to_root(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    return "" if str(relative) == "." else relative.as_posix()


def _preview_kind(path: Path, mime: str) -> str:
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


def _decode_text(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def _token_key_path() -> Path:
    configured = os.environ.get("OMNI_FILE_BRIDGE_TOKEN_KEY_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return omni_workspace_root() / "data" / "runtime" / "file_bridge.token"


def upload_history_path() -> Path:
    """Single durable upload ledger shared by the web UI and every CLI."""
    configured = os.environ.get("OMNI_FILE_BRIDGE_HISTORY_FILE", "").strip()
    if configured:
        return Path(os.path.expandvars(configured)).expanduser().resolve()
    return omni_workspace_root() / "data" / "runtime" / "file_bridge_uploads.jsonl"


def _append_upload_history(record: dict[str, Any]) -> None:
    path = upload_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    with _HISTORY_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()


def read_upload_history(limit: int = 20) -> list[dict[str, Any]]:
    """Return newest-first valid records without making the dashboard a dependency."""
    bounded = max(1, min(int(limit), _MAX_HISTORY_ITEMS))
    try:
        lines = upload_history_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    records: list[dict[str, Any]] = []
    known_batches: set[str] = set()
    for line_index, line in enumerate(lines):
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("batch_id"):
            value["_history_order"] = line_index
            records.append(value)
            known_batches.add(str(value["batch_id"]))

    # The bridge predates its durable ledger. Surface existing staging batches
    # as reconstructed records so uploads made before this upgrade stay visible.
    staging = _staging_root()
    try:
        batches = sorted(
            (path for path in staging.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:_MAX_HISTORY_ITEMS]
    except OSError:
        batches = []
    for batch in batches:
        if batch.name in known_batches:
            continue
        try:
            files = [path for path in batch.iterdir() if path.is_file() and not path.name.endswith(".uploading")]
            items = [_entry_payload(staging, path) for path in files]
            modified = datetime.fromtimestamp(batch.stat().st_mtime).astimezone()
        except OSError:
            continue
        records.append({
            "batch_id": batch.name,
            "uploaded_at": modified.isoformat(timespec="seconds"),
            "batch_path": str(batch),
            "root_id": "staging",
            "total_bytes": sum(int(item.get("size") or 0) for item in items),
            "items": items,
            "reconstructed": True,
            "_history_order": -1,
        })
    records.sort(
        key=lambda item: (
            str(item.get("uploaded_at") or ""),
            int(item.get("_history_order") or -1),
        ),
        reverse=True,
    )
    for record in records:
        record.pop("_history_order", None)
    return records[:bounded]


def _token_key() -> bytes:
    configured = os.environ.get("OMNI_FILE_BRIDGE_TOKEN_KEY", "").strip()
    if configured:
        return hashlib.sha256(configured.encode("utf-8")).digest()
    path = _token_key_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if not existing:
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = secrets.token_urlsafe(32)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(existing + "\n", encoding="utf-8")
        os.replace(temporary, path)
    return hashlib.sha256(existing.encode("utf-8")).digest()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign_content_location(root_id: str, relative_path: str) -> str:
    payload = json.dumps(
        {
            "v": 1,
            "r": root_id,
            "p": relative_path,
            "exp": int(time.time()) + _TOKEN_TTL_SECONDS,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_token_key(), payload, hashlib.sha256).digest()
    return f"{_b64url_encode(payload)}.{_b64url_encode(signature)}"


def _location_from_token(token: str) -> tuple[str, str]:
    try:
        payload_text, signature_text = token.split(".", 1)
        payload = _b64url_decode(payload_text)
        signature = _b64url_decode(signature_text)
        expected = hmac.new(_token_key(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        decoded = json.loads(payload.decode("utf-8"))
        if decoded.get("v") != 1 or int(decoded.get("exp") or 0) < int(time.time()):
            raise ValueError
        return str(decoded["r"]), str(decoded["p"])
    except (
        KeyError, TypeError, ValueError, UnicodeDecodeError,
        json.JSONDecodeError, base64.binascii.Error,
    ):
        raise HTTPException(status_code=403, detail="文件下载令牌无效或已过期") from None


def _entry_payload(root: Path, path: Path) -> dict[str, Any]:
    stat = path.stat()
    is_dir = path.is_dir()
    mime = "inode/directory" if is_dir else (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    relative = _relative_to_root(root, path)
    return {
        "name": path.name or str(path),
        "path": str(path),
        "relative_path": relative,
        "kind": "folder" if is_dir else "file",
        "size": None if is_dir else stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "mime": mime,
        "preview": "directory" if is_dir else _preview_kind(path, mime),
        "accessible": True,
    }


def _safe_upload_name(raw: str | None) -> str:
    name = Path(str(raw or "").replace("\\", "/")).name.strip().rstrip(". ")
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", name)
    if not name:
        name = "upload.bin"
    stem = name.split(".", 1)[0].lower()
    if stem in _WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    if len(name) > 180:
        suffix = Path(name).suffix[:24]
        name = f"{Path(name).stem[: max(1, 180 - len(suffix))]}{suffix}"
    return name


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except (TypeError, ValueError):
        return default


@file_bridge_router.get("/roots")
def file_bridge_roots(request: Request) -> dict[str, Any]:
    _require_bridge_request(request)
    # The dedicated staging directory is the bridge's sole writable boundary;
    # initialize it when the feature is first opened so its empty state browses
    # normally before the first upload.
    _staging_root().mkdir(parents=True, exist_ok=True)
    return {"items": _root_specs()}


@file_bridge_router.post("/browse")
def file_bridge_browse(location: BridgeLocation, request: Request) -> dict[str, Any]:
    _require_bridge_request(request)
    root, target = _resolve_location(location.root_id, location.path)
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="目标不是目录")
    try:
        raw_children = [
            child for child in target.iterdir()
            if child.name.lower() not in _OS_JUNK and not child.name.endswith(".uploading")
        ]
    except PermissionError:
        raise HTTPException(status_code=403, detail="当前系统账号没有读取该目录的权限") from None
    except OSError as error:
        raise HTTPException(status_code=400, detail=f"无法读取目录：{error}") from error

    items: list[dict[str, Any]] = []
    for child in raw_children:
        try:
            child.resolve().relative_to(root)
            items.append(_entry_payload(root, child))
        except (OSError, ValueError):
            # Broken links, denied children, and junctions escaping the root are
            # omitted rather than broadening the browsing capability.
            continue
    items.sort(key=lambda item: (item["kind"] != "folder", item["name"].lower()))
    return {
        "root_id": location.root_id,
        "root_path": str(root),
        "path": str(target),
        "relative_path": _relative_to_root(root, target),
        "items": items[:_MAX_DIRECTORY_ITEMS],
        "truncated": len(items) > _MAX_DIRECTORY_ITEMS,
    }


@file_bridge_router.post("/inspect")
def file_bridge_inspect(location: BridgeLocation, request: Request) -> dict[str, Any]:
    _require_bridge_request(request)
    root, target = _resolve_location(location.root_id, location.path)
    try:
        result = _entry_payload(root, target)
    except PermissionError:
        raise HTTPException(status_code=403, detail="当前系统账号没有读取该文件的权限") from None
    if target.is_dir():
        return result

    result["content_token"] = _sign_content_location(location.root_id, result["relative_path"])
    if result["preview"] == "text":
        try:
            with target.open("rb") as handle:
                raw = handle.read(_MAX_TEXT_PREVIEW_BYTES + 1)
        except PermissionError:
            raise HTTPException(status_code=403, detail="当前系统账号没有读取该文件的权限") from None
        result["truncated"] = len(raw) > _MAX_TEXT_PREVIEW_BYTES
        result["content"], result["encoding"] = _decode_text(raw[:_MAX_TEXT_PREVIEW_BYTES])
    return result


@file_bridge_router.post("/upload")
async def file_bridge_upload(
    request: Request,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    _require_bridge_request(request)
    max_files = _int_env("OMNI_FILE_BRIDGE_MAX_FILES", _DEFAULT_MAX_FILES)
    max_file_bytes = _int_env("OMNI_FILE_BRIDGE_MAX_FILE_BYTES", _DEFAULT_MAX_FILE_BYTES)
    max_batch_bytes = _int_env("OMNI_FILE_BRIDGE_MAX_BATCH_BYTES", _DEFAULT_MAX_BATCH_BYTES)
    if not files:
        raise HTTPException(status_code=400, detail="没有收到文件")
    if len(files) > max_files:
        raise HTTPException(status_code=413, detail=f"一次最多上传 {max_files} 个文件")

    staging = _staging_root()
    staging.mkdir(parents=True, exist_ok=True)
    batch_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    batch = staging / batch_id
    batch.mkdir(parents=False, exist_ok=False)

    written: list[Path] = []
    total_bytes = 0
    try:
        for upload in files:
            name = _safe_upload_name(upload.filename)
            target = batch / name
            duplicate = 1
            while target.exists() or target.with_suffix(target.suffix + ".uploading").exists():
                target = batch / f"{Path(name).stem}-{duplicate}{Path(name).suffix}"
                duplicate += 1
            temporary = target.with_suffix(target.suffix + ".uploading")
            file_bytes = 0
            with temporary.open("xb") as handle:
                while True:
                    chunk = await upload.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    file_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if file_bytes > max_file_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"{name} 超过单文件上限 {max_file_bytes // (1024 * 1024)} MB",
                        )
                    if total_bytes > max_batch_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=f"本批上传超过上限 {max_batch_bytes // (1024 * 1024)} MB",
                        )
                    handle.write(chunk)
            os.replace(temporary, target)
            written.append(target)
            await upload.close()
    except Exception:
        for path in [*written, *batch.glob("*.uploading")]:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            batch.rmdir()
        except OSError:
            pass
        raise

    item_payloads = [
        {
            **_entry_payload(staging, path),
            "content_token": _sign_content_location(
                "staging",
                _relative_to_root(staging, path),
            ),
        }
        for path in written
    ]
    response = {
        "batch_id": batch_id,
        "uploaded_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "batch_path": str(batch),
        "root_id": "staging",
        "total_bytes": total_bytes,
        "items": item_payloads,
    }
    _append_upload_history({
        **response,
        "items": [
            {key: value for key, value in item.items() if key != "content_token"}
            for item in item_payloads
        ],
    })
    return response


@file_bridge_router.get("/history")
def file_bridge_history(
    request: Request,
    limit: int = Query(default=30, ge=1, le=_MAX_HISTORY_ITEMS),
) -> dict[str, Any]:
    _require_bridge_request(request)
    return {
        "items": read_upload_history(limit),
        "history_path": str(upload_history_path()),
        "staging_path": str(_staging_root()),
        "query_command": f"omni dashboard uploads --limit {limit}",
    }


@file_bridge_router.get("/content/{token}")
def file_bridge_content(token: str, inline: bool = False) -> FileResponse:
    root_id, relative_path = _location_from_token(token)
    _root, target = _resolve_location(root_id, relative_path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="目标不是文件")
    mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    allow_inline = inline and (
        mime in _SAFE_INLINE_MIMES
        or mime.startswith(_SAFE_INLINE_MIME_PREFIXES)
    )
    return FileResponse(
        str(target),
        media_type=mime,
        filename=None if allow_inline else target.name,
        content_disposition_type="inline" if allow_inline else "attachment",
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
