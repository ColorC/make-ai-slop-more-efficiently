# [OMNI] origin=claude-code domain=services/_core/lifecycle ts=2026-06-25T00:00:00Z type=infra status=active
# [OMNI] summary="overlay-note-store 只读读取器: 读浅路径 index.json + docs/<id>.md, 给 note→plan 流水线当入口, 不建第二套存储"
# [OMNI] why="WORK-LIFECYCLE-AND-DISPATCH N-note: note 真源=overlay-note-store, omni 只读"
# [OMNI] tags=lifecycle,note,overlay-note-store,readonly
# [OMNI] material_id="material:services._core.lifecycle.note_source.py"
"""overlay-note-store 浅路径 **只读** 读取器。

overlay-shell 已把笔记从 AppData 的 IndexedDB 落到浅路径 `C:/workspace/overlay-note-store/`:
- `index.json`  —— 笔记清单 (id / title / createDate / updatedDate / ydoc / md)
- `docs/<id>.md`   —— 人可读正文 (懒导出: 笔记被打开/编辑过才有)
- `docs/<id>.ydoc` —— Yjs 二进制快照 (完整但需解码, 本读取器不碰)

omni 侧只读 index.json + .md, **不复制、不另存**。读不到 .md 时回退提示走
`omni notes refresh`(overlay-shell 在跑时触发 backfill 导出)。

参考: overlay-shell/src-tauri/src/notesstore.rs (落盘逻辑) / overlay-note-store/index.json (格式)。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def overlay_note_store_dir() -> Path:
    """Return the canonical note-store root, keeping the old path as fallback."""
    env = os.environ.get("OVERLAY_NOTE_STORE_DIR") or os.environ.get("POOF_NOTES_DIR")
    if env:
        return Path(env)
    try:
        from omnicompany.core.config import omni_workspace_root

        workspace = omni_workspace_root().parent
        canonical = workspace / "overlay-note-store"
        if canonical.exists():
            return canonical
        return workspace / "overlay-note-store"
    except Exception:
        canonical = Path("C:/workspace/overlay-note-store")
        if canonical.exists():
            return canonical
        return Path("C:/workspace/overlay-note-store")


@dataclass
class OverlayNote:
    """一条 overlay note 的只读视图。"""

    id: str
    title: str
    create_date: int | None = None
    updated_date: int | None = None
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    md_rel: str | None = None
    ydoc_rel: str | None = None
    _root: Path | None = None

    @property
    def has_body(self) -> bool:
        if not (self._root and self.md_rel):
            return False
        return (self._root / self.md_rel).is_file()

    def body(self) -> str | None:
        """读 docs/<id>.md 正文; 没导出返回 None。"""
        if not (self._root and self.md_rel):
            return None
        p = self._root / self.md_rel
        if not p.is_file():
            return None
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return None

    def to_dict(self, *, with_body: bool = False) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "create_date": self.create_date,
            "updated_date": self.updated_date,
            "tags": list(self.tags),
            "links": list(self.links),
            "has_body": self.has_body,
            "anchor": f"note:poof-note://{self.id}",
        }
        if with_body:
            d["body"] = self.body()
        return d


class OverlayNoteSource:
    """读 overlay-note-store 浅路径的只读源。不写、不缓存到第二份存储。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or overlay_note_store_dir()

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    def available(self) -> bool:
        return self.index_path.is_file()

    def _load_index(self) -> dict[str, Any]:
        if not self.index_path.is_file():
            raise FileNotFoundError(
                f"读不到 overlay-note-store 浅路径索引: {self.index_path} "
                f"(overlay-shell 未落浅路径 / OVERLAY_NOTE_STORE_DIR 未配 / 旧 POOF_NOTES_DIR 未配)"
            )
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"overlay-note-store index.json 解析失败: {e}") from e

    def list_notes(self) -> list[OverlayNote]:
        idx = self._load_index()
        out: list[OverlayNote] = []
        for n in idx.get("notes", []) or []:
            if not isinstance(n, dict) or not n.get("id"):
                continue
            out.append(
                OverlayNote(
                    id=str(n["id"]),
                    title=str(n.get("title") or "(无标题)"),
                    create_date=n.get("createDate"),
                    updated_date=n.get("updatedDate"),
                    tags=list(n.get("tags") or []),
                    links=list(n.get("links") or []),
                    md_rel=n.get("md"),
                    ydoc_rel=n.get("ydoc"),
                    _root=self.root,
                )
            )
        return out

    def get_note(self, note_id: str) -> OverlayNote | None:
        for n in self.list_notes():
            if n.id == note_id:
                return n
        return None


def read_note_source(root: Path | None = None) -> OverlayNoteSource:
    """工厂: 拿一个 overlay-note-store 只读源。"""
    return OverlayNoteSource(root)


# Legacy API aliases kept for callers/tests that still import the old names.
PoofNote = OverlayNote
PoofNoteSource = OverlayNoteSource
poof_notes_dir = overlay_note_store_dir

__all__ = [
    "OverlayNote",
    "OverlayNoteSource",
    "overlay_note_store_dir",
    "PoofNote",
    "PoofNoteSource",
    "poof_notes_dir",
    "read_note_source",
]
