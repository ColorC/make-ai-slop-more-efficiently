# [OMNI] origin=ai-ide domain=dashboard/boss_sight ts=2026-06-14T00:00:00Z type=infra status=active
# [OMNI] summary="统一札记 /api/boss-sight/notes CRUD —— 收敛评论/草稿写入口的唯一 API。"
# [OMNI] why="去重 4 个分散写入口; 旧 reviewstage comment 端点薄转发到这里。"
# [OMNI] tags=authored,notes,api
"""统一札记 API。挂载于 /api/boss-sight/notes(ccdaemon, 经 boss_sight 代理透传)。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .store import FEEDBACK_STATUSES, get_authored_store

notes_router = APIRouter(prefix="/api/boss-sight/notes", tags=["authored-notes"])


def _with_path(note: Any) -> dict[str, Any]:
    d = note.to_dict()
    d["json_path"] = get_authored_store().json_path(note.id)
    return d


class CreateNoteBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=200000)
    author: str = "user"
    target: dict[str, Any] = Field(default_factory=dict)
    uses: list[str] = Field(default_factory=lambda: ["comment"])
    feedback_status: str = "saved"
    project_id: str | None = None
    captures: list[str] = Field(default_factory=list)
    # 同文档表面(如 vilo demo)用 html2canvas 截自己后, 发 data:image/png;base64,... 一起存,
    # 解码落盘成截图、相对路径并进 captures(集中管理面渲染缩略图)。dashboard 抓不到跨文档 iframe,
    # 截图职责在被截应用侧, 故任意 note 都可携带一张自截图。
    image_data_url: str | None = Field(default=None, max_length=8_000_000)
    extra: dict[str, Any] = Field(default_factory=dict)


class UpdateNoteBody(BaseModel):
    content: str | None = None
    uses: list[str] | None = None
    feedback_status: str | None = None
    title: str | None = None          # 重命名: 自定义显示名(空串=清掉, 回退正文首行)
    by: str = "user"


class ExportDraftBody(BaseModel):
    dest_dir: str | None = None     # 默认取 target.new_object.dest_dir
    filename: str | None = None     # 默认从 new_object.title / target.title 起名
    overwrite: bool = False


@notes_router.post("")
async def create_note(body: CreateNoteBody) -> dict[str, Any]:
    store = get_authored_store()
    captures = list(body.captures or [])
    if body.image_data_url:  # 自截图: 复用 captures 的解码落盘助手, 相对路径并进 captures
        try:
            import time as _t
            from ..captures.routes import _save_image_data_url
            rel = _save_image_data_url(body.image_data_url, _t.strftime("%Y%m%dT%H%M%S"))
            if rel:
                captures.insert(0, rel)
        except Exception:  # noqa: BLE001
            pass
    try:
        n = store.create(
            content=body.content, author=body.author, target=body.target,
            uses=body.uses, feedback_status=body.feedback_status,
            project_id=body.project_id, captures=captures, extra=body.extra,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _with_path(n)


@notes_router.get("")
async def list_notes(
    target: str = Query(default=""),     # target.kind 过滤
    target_id: str = Query(default=""),
    project: str = Query(default=""),
    uses: str = Query(default=""),
    q: str = Query(default=""),
    include_archived: bool = Query(default=False),
) -> dict[str, Any]:
    store = get_authored_store()
    items = store.list(
        target_kind=target or None, target_id=target_id or None,
        project=project or None, uses=uses or None, q=q or None,
        include_archived=include_archived,
    )
    return {"count": len(items), "items": [_with_path(n) for n in items]}


@notes_router.get("/by-target/{kind}/{tid:path}")
async def notes_by_target(kind: str, tid: str) -> dict[str, Any]:
    """重进某对象时回显它的札记。"""
    store = get_authored_store()
    items = store.list(target_kind=kind, target_id=tid)
    return {"count": len(items), "items": [_with_path(n) for n in items]}


@notes_router.get("/_decisions")
async def list_decisions() -> dict[str, Any]:
    from .extract import load_decisions
    items = load_decisions()
    return {"count": len(items), "items": items}


@notes_router.get("/_meta/feedback-statuses")
async def feedback_statuses() -> dict[str, Any]:
    return {"statuses": sorted(FEEDBACK_STATUSES)}


@notes_router.get("/{note_id}")
async def get_note(note_id: str) -> dict[str, Any]:
    n = get_authored_store().get(note_id)
    if n is None or n.archived:
        raise HTTPException(404, f"note not found: {note_id}")
    return _with_path(n)


@notes_router.put("/{note_id}")
async def update_note(note_id: str, body: UpdateNoteBody) -> dict[str, Any]:
    store = get_authored_store()
    try:
        n = store.update(note_id, content=body.content, uses=body.uses,
                         feedback_status=body.feedback_status, title=body.title, by=body.by)
    except KeyError as e:
        raise HTTPException(404, f"note not found: {e}") from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return _with_path(n)


@notes_router.delete("/{note_id}")
async def delete_note(note_id: str) -> dict[str, Any]:
    ok = get_authored_store().delete(note_id)
    if not ok:
        raise HTTPException(404, f"note not found: {note_id}")
    return {"archived": True, "id": note_id}


@notes_router.post("/{note_id}/export-draft")
async def export_draft(note_id: str, body: ExportDraftBody) -> dict[str, Any]:
    """把草稿正文落成项目目录里的成品文件:撰写态真源留中心 store, 成品落项目仓库目录。

    dest_dir 取 body 或 target.new_object.dest_dir; 必须是已存在的真实目录(拒绝写任意新路径)。"""
    import re
    from datetime import datetime, timezone
    from pathlib import Path

    store = get_authored_store()
    n = store.get(note_id)
    if n is None or n.archived:
        raise HTTPException(404, f"note not found: {note_id}")
    nt = n.target or {}
    new_obj = nt.get("new_object") if isinstance(nt.get("new_object"), dict) else {}
    dest_dir = (body.dest_dir or new_obj.get("dest_dir") or nt.get("dest_dir") or "").strip()
    if not dest_dir:
        raise HTTPException(400, "缺 dest_dir(传 body.dest_dir 或在 target.new_object.dest_dir 里设)")
    d = Path(dest_dir)
    if not d.is_absolute():
        from omnicompany.core.config import omni_workspace_root
        d = omni_workspace_root().parent / dest_dir
    d = d.resolve()
    if not d.is_dir():
        raise HTTPException(400, f"dest_dir 不是已存在的目录(拒绝写任意新路径): {d}")
    title = body.filename or new_obj.get("title") or nt.get("title") or n.id
    slug = re.sub(r"[^0-9A-Za-z_.一-鿿-]+", "-", str(title)).strip("-") or n.id
    if not slug.lower().endswith((".md", ".txt", ".json")):
        slug += ".md"
    path = (d / slug).resolve()
    if path.parent != d:  # slug 里的 ../ 之类越界
        raise HTTPException(400, "非法文件名")
    if path.exists() and not body.overwrite:
        raise HTTPException(409, f"文件已存在(传 overwrite=true 覆盖): {path}")
    try:
        path.write_text(n.content, encoding="utf-8")
    except OSError as e:
        raise HTTPException(500, f"写文件失败: {e}") from e
    try:  # 标记草稿已导出成成品(真源仍在中心 store)
        n.extra = {**(n.extra or {}), "exported_to": str(path),
                   "exported_at": datetime.now(timezone.utc).isoformat()}
        store._persist(n)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "exported_path": str(path), "note_id": note_id}


@notes_router.post("/_extract-decisions")
async def extract_decisions_now(reextract: bool = False) -> dict[str, Any]:
    """手动触发: 把 uses 含 llm_input 的札记提炼成结构化决策。定期由 governance cron 调同函数。"""
    from .extract import extract_decisions
    return extract_decisions(reextract=reextract)


__all__ = ["notes_router"]
