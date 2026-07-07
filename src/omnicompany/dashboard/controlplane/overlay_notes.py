# [OMNI] origin=ai-ide ts=2026-06-28 type=infra
"""controlplane/overlay_notes.py — overlay note store(BlockSuite/Yjs) 的 HTTP 桥。

overlay-shell 桌面端把笔记落在 E:\\WindowsWorkspace\\overlay-note-store(docs/<id>.ydoc 等),走 Tauri 命令读写。
LOFA 网页/手机端没有 Tauri,这里把同一套命令做成 HTTP,落同一个目录。对外只走 8210 一个口。
`/lofa/poof/*` 保留为旧路由兼容;新路由是 `/lofa/overlay/*`。

镜像的命令(契约对齐 src-tauri/src/notesstore.rs):
  notes_root / notes_doc_{get,put,del,keys} / notes_blob_{get,put,del,keys}
  notes_md_{put,del} / notes_index_put / notes_version_{put,all,del_one,del_all}

并发: 与桌面 overlay-shell 都用 原子写(临时文件+rename); 快照级 last-write-wins(暂不做 Yjs 合并)。
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

overlay_notes_router = APIRouter(tags=["overlay-note-store"])

_ROOT = Path(
    os.environ.get("OVERLAY_NOTE_STORE_ROOT")
    or os.environ.get("POOF_NOTES_ROOT", r"E:\WindowsWorkspace\overlay-note-store")
)
_WEB_DIR = Path(
    os.environ.get("OVERLAY_SHELL_WEB_DIR")
    or os.environ.get("POOF_WEB_DIR", r"E:\WindowsWorkspace\overlay-shell\dist-web")
)


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


def _dispatch(cmd: str, a: dict):
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
    return FileResponse(str(target))


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
        result = _dispatch(cmd, args)
        return {"ok": True, "result": result}
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)
