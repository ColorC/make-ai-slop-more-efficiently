# [OMNI] origin=kiro ts=2026-08-19 type=infra
"""controlplane/video_review_comments.py — 网页影片审阅评论（锚点 = 某一秒 + 某一元素）。

为什么单独一套而不复用 kb_annotations：那边的 anchor 是 `{hash, snippet}`（段落文本哈希），
锚的是**静态文本**。网页影片的 DOM 随时间重渲染，同一个选择器在不同秒指向完全不同的内容，
**只有「秒 + 元素」配对才能把评论钉回它当时说的那一帧**。所以 anchor 必须带 time_sec。

anchor 字段:
  time_sec     必填，评论指向的播放时刻（秒，float）。回放先 setTime 再按 selector 找元素。
  selector     必填，元素 CSS 选择器（审阅壳里生成，规则与 frontend/src/lib/iframePicker.ts
               的 buildSelectorPath 同源：优先 id → 稳定 data-* → class/nth-child 链）。
  frame_path   iframe 链（[] = 审阅壳直属 iframe 内；多层嵌套时逐层 selector）。
               递归圈选靠它，不靠"猜哪一层"。
  tag / text_excerpt / rect / beat_id / viewport  显示与软恢复用，选择器失配时还能告诉人
               "当时圈的是什么"——不做自动重锚，宁可显示"元素已变"也不假装找到了。

存储: data/video_review_comments/<episode>.json（episode 白名单同 bilibili_draft：^[a-z0-9-]+$）。
评论按**期**归档而不是按某次送审：同一支影片会反复送审，评论是对影片本体说的话，
挂在送审卡上会随卡作废而丢。

鉴权: 与 dashboard 其它 /api 端点同一条网关鉴权路径，本模块不自带鉴权（本机审阅设施）。
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

video_review_comments_router = APIRouter(tags=["video-review-comments"])

_EP_RE = re.compile(r"^[a-z0-9-]+$")
_MAX_COMMENT_CHARS = 4000
_MAX_SELECTOR_CHARS = 600
_MAX_FRAME_DEPTH = 6


def _data_root() -> Path:
    root = Path(__file__).resolve().parents[3] / "data" / "video_review_comments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _file_for(episode: str) -> Path:
    if not _EP_RE.fullmatch(episode or ""):
        raise HTTPException(status_code=400, detail=f"invalid episode: {episode!r}")
    return _data_root() / f"{episode}.json"


def _load(episode: str) -> list[dict[str, Any]]:
    path = _file_for(episode)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # 坏文件不当成"没有评论"静默吞掉——那正是审阅队列恒空那类事故的形态。
        raise HTTPException(status_code=500, detail=f"comment store is unreadable: {path.name}") from None
    return data if isinstance(data, list) else []


def _save(episode: str, items: list[dict[str, Any]]) -> None:
    path = _file_for(episode)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


class Rect(BaseModel):
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0


class Viewport(BaseModel):
    width: int = 0
    height: int = 0


class Anchor(BaseModel):
    time_sec: float = Field(ge=0)
    selector: str
    frame_path: list[str] = Field(default_factory=list)
    tag: str = ""
    text_excerpt: str = ""
    beat_id: str = ""
    rect: Rect | None = None
    viewport: Viewport | None = None


class CommentCreate(BaseModel):
    anchor: Anchor
    comment: str
    author: str | None = None


def _validated_anchor(anchor: Anchor) -> dict[str, Any]:
    selector = anchor.selector.strip()
    if not selector:
        raise HTTPException(status_code=400, detail="anchor.selector is required")
    if len(selector) > _MAX_SELECTOR_CHARS:
        raise HTTPException(status_code=400, detail="anchor.selector too long")
    if len(anchor.frame_path) > _MAX_FRAME_DEPTH:
        raise HTTPException(status_code=400, detail="anchor.frame_path too deep")
    return {
        "time_sec": round(float(anchor.time_sec), 3),
        "selector": selector,
        "frame_path": [str(f)[:_MAX_SELECTOR_CHARS] for f in anchor.frame_path],
        "tag": anchor.tag[:40],
        "text_excerpt": anchor.text_excerpt[:240],
        "beat_id": anchor.beat_id[:40],
        "rect": anchor.rect.model_dump() if anchor.rect else None,
        "viewport": anchor.viewport.model_dump() if anchor.viewport else None,
    }


@video_review_comments_router.get("/video-review/{episode}/comments")
def list_comments(episode: str) -> dict[str, Any]:
    """按锚定时刻排序返回——评论列表就是一条时间轴，不是收件箱。"""
    items = _load(episode)
    items.sort(key=lambda c: (float((c.get("anchor") or {}).get("time_sec") or 0), str(c.get("created_at") or "")))
    open_count = sum(1 for c in items if not c.get("resolved"))
    return {"episode": episode, "items": items, "total": len(items), "open": open_count}


@video_review_comments_router.post("/video-review/{episode}/comments")
def create_comment(episode: str, body: CommentCreate) -> dict[str, Any]:
    text = body.comment.strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty comment")
    if len(text) > _MAX_COMMENT_CHARS:
        raise HTTPException(status_code=400, detail="comment too long")
    items = _load(episode)
    entry = {
        "id": "vrc-" + uuid.uuid4().hex[:12],
        "anchor": _validated_anchor(body.anchor),
        "comment": text,
        "author": (body.author or "user")[:80],
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "resolved": False,
    }
    items.append(entry)
    _save(episode, items)
    return entry


@video_review_comments_router.patch("/video-review/{episode}/comments/{comment_id}")
def patch_comment(episode: str, comment_id: str, body: dict[str, Any]) -> dict[str, Any]:
    items = _load(episode)
    found = next((c for c in items if c.get("id") == comment_id), None)
    if found is None:
        raise HTTPException(status_code=404, detail="comment not found")
    if "comment" in body:
        text = str(body["comment"]).strip()
        if not text:
            raise HTTPException(status_code=400, detail="empty comment")
        if len(text) > _MAX_COMMENT_CHARS:
            raise HTTPException(status_code=400, detail="comment too long")
        found["comment"] = text
    if "resolved" in body:
        found["resolved"] = bool(body["resolved"])
    _save(episode, items)
    return found


@video_review_comments_router.delete("/video-review/{episode}/comments/{comment_id}")
def delete_comment(episode: str, comment_id: str) -> dict[str, Any]:
    items = _load(episode)
    remaining = [c for c in items if c.get("id") != comment_id]
    if len(remaining) == len(items):
        raise HTTPException(status_code=404, detail="comment not found")
    _save(episode, remaining)
    return {"ok": True, "remaining": len(remaining)}
