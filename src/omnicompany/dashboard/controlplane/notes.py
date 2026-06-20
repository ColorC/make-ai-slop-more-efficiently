# [OMNI] origin=claude-code ts=2026-05-01 type=infra
# [OMNI] material_id="material:dashboard.notes_kb.read_write_api.py"
"""KB notes API — scans omnicompany `docs/**/*.md`.

Skipped: _archive/ (历史归档大量), node_modules/, _legacy/.
Also extracts wiki-links `[[name]]` for backlink + graph use.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from omnicompany.core.config import omni_workspace_root

notes_router = APIRouter()


class NoteWrite(BaseModel):
    content: str

SKIP_DIRS = {"_archive", "node_modules", "_legacy", "venv", ".venv", ".git", "__pycache__"}

# `[[Name]]` or `[[Name|alias]]` or `[[Name#heading]]` or `![[Name]]` (embed)
WIKILINK_RE = re.compile(r"!?\[\[([^\[\]]+)\]\]")


# 2026-06-06 修: 原来用 Path(__file__).parents[3] 硬编码深度 = src/(少了一层, 因 notes.py
# 移进 controlplane/ 子目录), 指向不存在的 src/docs → /api/notes 扫到 0 篇 → 所有 plan/note
# 点开 404("具体计划无法访问")。改用唯一权威的 omni_workspace_root()(depth-independent,
# 见其 docstring 明确警告不要再写 parents[N] 散点逻辑)。
def _docs_root() -> Path:
    return omni_workspace_root() / "docs"


def _project_root() -> Path:
    return omni_workspace_root()


def _is_skipped(p: Path) -> bool:
    parts = p.parts
    return any(s in parts for s in SKIP_DIRS)


def _extract_links(text: str) -> list[str]:
    """Return list of normalized link targets (id only, no anchor / alias)."""
    out = []
    for m in WIKILINK_RE.finditer(text):
        raw = m.group(1).strip()
        if "|" in raw:
            raw = raw.split("|", 1)[0].strip()
        if "#" in raw:
            raw = raw.split("#", 1)[0].strip()
        if raw:
            out.append(raw)
    return out


@lru_cache(maxsize=1)
def _scan_cached(token: float) -> list[dict[str, Any]]:
    docs = _docs_root()
    items: list[dict[str, Any]] = []
    if not docs.is_dir():
        return items
    for path in docs.rglob("*.md"):
        if _is_skipped(path.relative_to(docs)):
            continue
        rel = path.relative_to(docs).with_suffix("")
        rel_str = str(rel).replace(os.sep, "/")
        try:
            stat = path.stat()
            items.append({
                "id": rel_str,
                "title": path.stem,
                "path": str(path.relative_to(_project_root())).replace(os.sep, "/"),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
        except OSError:
            continue
    items.sort(key=lambda x: x["id"])
    return items


@lru_cache(maxsize=1)
def _link_graph_cached(token: float) -> dict[str, Any]:
    """Build full link graph: outgoing (per note) + backlinks (per note) + edges list.

    Resolution: target = note id (path under docs without .md).
    Try direct match by full path; if not found, match by stem (title) — Obsidian-like
    ambiguity falls to first match.
    """
    items = _scan_cached(token)
    by_id = {it["id"]: it for it in items}
    by_stem: dict[str, list[str]] = {}
    for it in items:
        by_stem.setdefault(it["title"], []).append(it["id"])

    out_links: dict[str, list[str]] = {}  # note_id -> [resolved target ids]
    out_unresolved: dict[str, list[str]] = {}  # note_id -> [raw unresolved names]
    edges: list[tuple[str, str]] = []

    docs = _docs_root()
    for it in items:
        md_path = docs / (it["id"] + ".md")
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        targets_resolved: list[str] = []
        targets_unresolved: list[str] = []
        for raw in _extract_links(text):
            if raw in by_id:
                targets_resolved.append(raw)
            else:
                cand = by_stem.get(raw)
                if cand:
                    targets_resolved.append(cand[0])
                else:
                    targets_unresolved.append(raw)
        if targets_resolved:
            out_links[it["id"]] = targets_resolved
        if targets_unresolved:
            out_unresolved[it["id"]] = targets_unresolved
        for tgt in targets_resolved:
            edges.append((it["id"], tgt))

    backlinks: dict[str, list[str]] = {}
    for src, tgts in out_links.items():
        for tgt in tgts:
            backlinks.setdefault(tgt, []).append(src)

    return {
        "out_links": out_links,
        "out_unresolved": out_unresolved,
        "backlinks": backlinks,
        "edges": edges,
        "node_count": len(items),
        "edge_count": len(edges),
    }


def _docs_token() -> float:
    docs = _docs_root()
    return docs.stat().st_mtime if docs.exists() else 0.0


@notes_router.get("/notes")
def list_notes() -> dict[str, Any]:
    items = _scan_cached(_docs_token())
    return {"items": items, "total": len(items)}


@notes_router.get("/notes/_links")
def list_links() -> dict[str, Any]:
    """Global link graph (outgoing + backlinks + edge list)."""
    return _link_graph_cached(_docs_token())


@notes_router.get("/notes/_search")
def search_notes(q: str = "", limit: int = 30) -> dict[str, Any]:
    """Server-side full-text search across notes (sub-string, case-insensitive).

    Useful as fallback to MiniSearch on frontend when memory is constrained.
    """
    if not q:
        return {"items": [], "total": 0}
    docs = _docs_root()
    items = _scan_cached(_docs_token())
    ql = q.lower()
    hits: list[dict[str, Any]] = []
    for it in items:
        try:
            text = (docs / (it["id"] + ".md")).read_text(encoding="utf-8")
        except OSError:
            continue
        if ql in text.lower() or ql in it["id"].lower():
            idx = text.lower().find(ql)
            snippet = text[max(0, idx - 60):idx + 120].replace("\n", " ") if idx >= 0 else text[:180]
            hits.append({
                "id": it["id"],
                "title": it["title"],
                "snippet": snippet,
            })
            if len(hits) >= limit:
                break
    return {"items": hits, "total": len(hits)}


@notes_router.get("/notes/{note_id:path}/links")
def note_links(note_id: str) -> dict[str, Any]:
    """For a single note: its outgoing links + backlinks + unresolved targets."""
    g = _link_graph_cached(_docs_token())
    if note_id not in {it["id"] for it in _scan_cached(_docs_token())}:
        raise HTTPException(status_code=404, detail=f"note not found: {note_id}")
    return {
        "id": note_id,
        "outgoing": g["out_links"].get(note_id, []),
        "outgoing_unresolved": g["out_unresolved"].get(note_id, []),
        "backlinks": g["backlinks"].get(note_id, []),
    }


# ── asset endpoint (image embeds `![[image.png]]`) ─────────────────────────
#
# IMPORTANT: must be registered BEFORE `/notes/{note_id:path}` GET, or the latter's
# greedy `:path` swallows `/asset/<...>` as part of note_id.

from fastapi.responses import FileResponse
import mimetypes

# Allow only safe asset extensions to be served. Anything else 404s.
_ASSET_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico", ".avif",
    ".pdf",  # occasional embed
}


@notes_router.get("/notes/{note_id:path}/asset/{asset_path:path}")
def get_note_asset(note_id: str, asset_path: str):
    """Serve an asset (image, etc.) referenced by `![[asset_path]]` from a note.

    Resolution: `asset_path` is interpreted RELATIVE to the note's directory
    (so a note `foo/bar/baz.md` referencing `pic.png` resolves to `docs/foo/bar/pic.png`).
    Absolute paths starting with `/` are interpreted relative to docs root.
    Path escape (`..` going outside docs root) is blocked.
    """
    docs = _docs_root()
    md_path = docs / (note_id + ".md")
    if not md_path.is_file():
        raise HTTPException(status_code=404, detail=f"note not found: {note_id}")

    base = md_path.parent if not asset_path.startswith("/") else docs
    rel = asset_path.lstrip("/")
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(docs.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="asset path escape")
    if candidate.suffix.lower() not in _ASSET_EXTS:
        raise HTTPException(status_code=400, detail=f"unsupported asset extension: {candidate.suffix}")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"asset not found: {asset_path}")

    media_type, _ = mimetypes.guess_type(str(candidate))
    return FileResponse(str(candidate), media_type=media_type or "application/octet-stream")


@notes_router.get("/notes/{note_id:path}")
def get_note(note_id: str) -> dict[str, Any]:
    docs = _docs_root()
    md_path = docs / (note_id + ".md")
    try:
        md_path.resolve().relative_to(docs.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid note id")
    if not md_path.is_file():
        # 按名(stem)回退: 很多视图/wikilink 用基名造 note 引用, 而嵌套文件的真 id 带路径
        # (docs/standards/_global/distributed-docs.md → standards/_global/distributed-docs),
        # 精确匹配取不到 → 这是"点很多内容全 404"的根因。与 note_links 的 Obsidian 式按名解析口径一致:
        # 唯一同名才解析, 多义则报清楚让调用方用完整 id。
        stem = note_id.rsplit("/", 1)[-1]
        matches = [it["id"] for it in _scan_cached(_docs_token()) if it.get("title") == stem]
        if len(matches) == 1:
            note_id = matches[0]
            md_path = docs / (note_id + ".md")
        elif len(matches) > 1:
            raise HTTPException(status_code=404, detail=f"note '{stem}' 同名多份, 请用完整 id: {matches[:6]}")
    if not md_path.is_file():
        raise HTTPException(status_code=404, detail=f"note not found: {note_id}")
    try:
        content = md_path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    stat = md_path.stat()
    return {
        "id": note_id,
        "title": md_path.stem,
        "path": str(md_path.relative_to(_project_root())).replace(os.sep, "/"),
        "content": content,
        "mtime": stat.st_mtime,
        "size": stat.st_size,
    }


@notes_router.put("/notes/{note_id:path}")
def put_note(note_id: str, body: NoteWrite) -> dict[str, Any]:
    """Write back to existing note. Refuses path escape, refuses to create new files
    (use a separate POST endpoint later if needed). Caches reset on success.
    """
    docs = _docs_root()
    md_path = docs / (note_id + ".md")
    try:
        resolved = md_path.resolve()
        resolved.relative_to(docs.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid note id (path escape)")
    if not md_path.is_file():
        raise HTTPException(status_code=404, detail=f"note not found: {note_id} (no create via PUT)")
    try:
        md_path.write_text(body.content, encoding="utf-8", newline="\n")
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))
    # Invalidate caches so subsequent /api/notes and /api/notes/_links reflect changes
    _scan_cached.cache_clear()
    _link_graph_cached.cache_clear()
    stat = md_path.stat()
    return {
        "id": note_id,
        "ok": True,
        "mtime": stat.st_mtime,
        "size": stat.st_size,
    }
