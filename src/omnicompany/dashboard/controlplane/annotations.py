# [OMNI] origin=claude-code ts=2026-05-01 type=infra
# [OMNI] material_id="material:dashboard.annotations_api.crud_endpoint.py"
"""KB annotations — paragraph-level comments, sidecar JSON storage.

Anchor: { hash, snippet }
- hash: FNV-1a (32-bit) of normalized paragraph text (lowercase, whitespace-collapsed, ≤200 chars)
- snippet: first 60 chars of original text (for soft recovery / display)

Storage: data/kb_annotations/<safe_note_id>.json
where safe_note_id replaces "/" with "__".
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

annotations_router = APIRouter()


def _data_root() -> Path:
    p = Path(__file__).resolve().parents[3] / "data" / "kb_annotations"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_id(note_id: str) -> str:
    if any(p in note_id for p in ("..", "\\")):
        raise HTTPException(status_code=400, detail="invalid note id")
    return note_id.replace("/", "__")


def _file_for(note_id: str) -> Path:
    return _data_root() / (_safe_id(note_id) + ".json")


def _load(note_id: str) -> list[dict[str, Any]]:
    f = _file_for(note_id)
    if not f.is_file():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _save(note_id: str, anns: list[dict[str, Any]]) -> None:
    f = _file_for(note_id)
    f.write_text(json.dumps(anns, ensure_ascii=False, indent=2), encoding="utf-8")


class Anchor(BaseModel):
    hash: str
    snippet: str


class AnnotationCreate(BaseModel):
    anchor: Anchor
    comment: str
    author: str | None = None


@annotations_router.get("/notes/{note_id:path}/annotations")
def list_annotations(note_id: str) -> dict[str, Any]:
    items = _load(note_id)
    return {"items": items, "total": len(items)}


@annotations_router.post("/notes/{note_id:path}/annotations")
def create_annotation(note_id: str, body: AnnotationCreate) -> dict[str, Any]:
    if not body.comment.strip():
        raise HTTPException(status_code=400, detail="empty comment")
    items = _load(note_id)
    new = {
        "id": "ann-" + uuid.uuid4().hex[:12],
        "anchor": {"hash": body.anchor.hash, "snippet": body.anchor.snippet},
        "comment": body.comment.strip(),
        "author": body.author or "user",
        "created_at": time.time(),
        "resolved": False,
    }
    items.append(new)
    _save(note_id, items)
    return new


@annotations_router.delete("/notes/{note_id:path}/annotations/{ann_id}")
def delete_annotation(note_id: str, ann_id: str) -> dict[str, Any]:
    items = _load(note_id)
    new_items = [a for a in items if a.get("id") != ann_id]
    if len(new_items) == len(items):
        raise HTTPException(status_code=404, detail="annotation not found")
    _save(note_id, new_items)
    return {"ok": True, "remaining": len(new_items)}


@annotations_router.patch("/notes/{note_id:path}/annotations/{ann_id}")
def patch_annotation(note_id: str, ann_id: str, body: dict[str, Any]) -> dict[str, Any]:
    items = _load(note_id)
    found = None
    for a in items:
        if a.get("id") == ann_id:
            found = a
            break
    if found is None:
        raise HTTPException(status_code=404, detail="annotation not found")
    if "comment" in body:
        c = str(body["comment"]).strip()
        if not c:
            raise HTTPException(status_code=400, detail="empty comment")
        found["comment"] = c
    if "resolved" in body:
        found["resolved"] = bool(body["resolved"])
    _save(note_id, items)
    return found
