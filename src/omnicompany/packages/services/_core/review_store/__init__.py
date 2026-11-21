# [OMNI] origin=kiro domain=services/_core/review_store ts=2026-08-20T10:40:00Z type=service status=active
"""审阅库统一读源（共享核心）。

单一真源 = 画布 `card.submission`（canvas-store 服务持有）。在此之前读源散落三处且互不相通：
dashboard 控制面自己实现一套转发、boss_sight 的简报读 reviewstage 材料库、material wiki
置顶又跨层 import dashboard —— 同一屏上侧栏徽标写「审阅 27」、点进审阅台只有 2 条。

本模块是**唯一**读口。它自持 canvas-store 客户端（URL 与 token 都是文件/env 配置，不属
dashboard 私有），因此 services 层可以直接用，不需要反向依赖 dashboard（arch_gate R1）。

新增读源一律加在这里；谁再在别处实现第二套画布扫描，`tests/services/review_store/` 的
闸门会红。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# 画布四态 ↔ 旧 reviewstage 三态。旧库口径仍被前端与简报消费，故双向映射是一等能力。
# seen（已看未裁决）在旧口径里等同 pending —— 旧库没有这一档。
CANVAS_STATE_TO_STATUS = {
    "unreviewed": "pending",
    "seen": "pending",
    "fully-passed": "accepted",
    "not-fully-passed": "rejected",
}
STATUS_TO_CANVAS_STATE = {
    "pending": "unreviewed",
    "accepted": "fully-passed",
    "rejected": "not-fully-passed",
    "blocked": "not-fully-passed",
}

_DEFAULT_PORT = "8212"
_DEFAULT_TOKEN_FILE = r"C:/workspace/omnicompany\data\canvas_store_token.txt"
_HTTP_OPENER_STATE = threading.local()


def store_url() -> str:
    explicit = os.environ.get("OMNI_CANVAS_STORE_URL")
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get("OMNI_CANVAS_STORE_PORT") or _DEFAULT_PORT
    return f"http://127.0.0.1:{port}"


def store_token() -> str:
    path = Path(os.environ.get("OMNI_CANVAS_STORE_TOKEN_FILE") or _DEFAULT_TOKEN_FILE)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _api(path: str, timeout: float = 15.0) -> tuple[int, dict]:
    request = urllib.request.Request(f"{store_url()}/api{path}", method="GET")
    token = store_token()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        # Building the default HTTPS handler enumerates Windows system
        # certificates, even for our loopback HTTP requests. Reuse one opener
        # per worker thread so session catalogue reads do not repeat that
        # GIL-heavy work for every canvas. Handlers are never shared by threads.
        opener = getattr(_HTTP_OPENER_STATE, "opener", None)
        if opener is None:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            _HTTP_OPENER_STATE.opener = opener
        with opener.open(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, (payload if isinstance(payload, dict) else {})
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read().decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            detail = {}
        return error.code, (detail if isinstance(detail, dict) else {})


def canvas_ids() -> list[str]:
    status, payload = _api("/canvases")
    if status != 200:
        raise RuntimeError(f"canvas-store 列表失败: HTTP {status} {payload.get('error', '')}")
    return [str(item.get("document_id")) for item in payload.get("canvases") or []
            if isinstance(item, dict) and item.get("document_id")]


def canvas_document(canvas_id: str) -> dict | None:
    status, payload = _api(f"/canvases/{urllib.parse.quote(canvas_id, safe='')}")
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"canvas-store 读失败: HTTP {status} {payload.get('error', '')}")
    document = payload.get("document")
    return document if isinstance(document, dict) else None


def session_from_canvas(canvas_id: str) -> str:
    """会话画布 key → pty 会话 id。审阅库画布（review-<project>）没有会话归属，返回空串。"""
    if not canvas_id.startswith("session-"):
        return ""
    import base64

    encoded = canvas_id[len("session-"):]
    try:
        encoded += "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(encoded).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""
    return raw[len("external:"):] if raw.startswith("external:") else raw


def iter_submission_cards(*, include_archived: bool = True) -> Iterator[dict]:
    """遍历所有画布里带 submission 的卡。

    每项: canvas / session_id / canvasTitle / card / submission。
    单张画布损坏只跳过并告警；读源整体不可达要抛出去（端点 500 可见）——
    「静默的空」和「真的没有」在读侧长得一模一样，4747e6219 那次就是栽在这上面。

    2026-08-20 性能: 一次全扫 = 79+ 张画布逐张 HTTP GET。前端一个页面十几个组件
    同时各发一次扫描会把 canvas-store 打到排队（实测 _stats 8-25s、别的请求饿死），
    所以快照走 3s 短 TTL 缓存 + 锁内单飞（并发调用共享同一次扫描），扫描内部按
    8 路并行拉画布。测试 monkeypatch 掉 canvas_ids/canvas_document 时自动绕过缓存。
    """
    yield from _snapshot(include_archived=include_archived)


_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"at": 0.0, "hits": None}
_CACHE_TTL_S = 3.0
_FETCH_WORKERS = 8


def _snapshot(*, include_archived: bool) -> list[dict]:
    # 测试把 canvas_ids/canvas_document 钉成内存 fixture 时必须绕过缓存,
    # 否则上一个用例的 3s 快照会污染下一个。
    if canvas_ids is not _ORIG_CANVAS_IDS or canvas_document is not _ORIG_CANVAS_DOCUMENT:
        return list(_scan_uncached(include_archived=include_archived))
    with _CACHE_LOCK:
        hits = _CACHE["hits"]
        if hits is None or time.monotonic() - _CACHE["at"] >= _CACHE_TTL_S:
            hits = list(_scan_uncached(include_archived=True))
            _CACHE.update(at=time.monotonic(), hits=hits)
        if include_archived:
            return hits
        return [h for h in hits if not (h.get("submission") or {}).get("archived")]


def invalidate_cache() -> None:
    """写路径（提审/裁决/评论）落完画布后调用, 让下一次读立刻重扫。"""
    with _CACHE_LOCK:
        _CACHE.update(at=0.0, hits=None)


def _scan_uncached(*, include_archived: bool) -> Iterator[dict]:
    ids = sorted(canvas_ids())
    documents: dict[str, dict | None] = {}
    if ids:
        with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as pool:
            for canvas_id, document in zip(ids, pool.map(_try_canvas_document, ids)):
                documents[canvas_id] = document
    for canvas_id in ids:
        document = documents.get(canvas_id)
        if not isinstance(document, dict):
            continue
        session_id = session_from_canvas(canvas_id)
        for card in document.get("cards") or []:
            if not isinstance(card, dict) or card.get("kind") == "group":
                continue
            submission = card.get("submission")
            if not isinstance(submission, dict) or not submission.get("state"):
                continue
            if not include_archived and submission.get("archived"):
                continue
            yield {
                "canvas": canvas_id,
                "session_id": session_id,
                "canvasTitle": document.get("title") or "",
                "card": card,
                "submission": submission,
            }


def _try_canvas_document(canvas_id: str) -> dict | None:
    try:
        return canvas_document(canvas_id)
    except Exception:  # noqa: BLE001
        logger.warning("submission scan: 画布 %s 读失败, 跳过", canvas_id, exc_info=True)
        return None


# 缓存绕过判据用的原始实现引用（模块装载时固定）。
_ORIG_CANVAS_IDS = canvas_ids
_ORIG_CANVAS_DOCUMENT = canvas_document


def submission_to_material(hit: dict) -> dict[str, Any]:
    """画布卡 → 旧 reviewstage Material 形状（读侧投影，供既有前端与简报零改动消费）。

    融合迁移把旧库全字段搬进了 submission，所以这里是无损还原而不是降级近似：
    `legacy` 里留着原 status/project/时间，一等字段里留着 tier/track/version/subject/
    links/extra/reviewContext/annotations/comments/history/pushed。
    """
    card = hit.get("card") or {}
    submission = hit.get("submission") or {}
    legacy = submission.get("legacy") if isinstance(submission.get("legacy"), dict) else {}
    projection = card.get("material") if isinstance(card.get("material"), dict) else {}
    state = str(submission.get("state") or "unreviewed")
    status = str(legacy.get("status") or CANVAS_STATE_TO_STATUS.get(state, "pending"))
    pushed = submission.get("pushed") if isinstance(submission.get("pushed"), dict) else {}
    source = card.get("source") if isinstance(card.get("source"), dict) else {}
    material: dict[str, Any] = {
        "id": str(card.get("id") or ""),
        "kind": str(submission.get("materialKind") or projection.get("kind") or "markdown"),
        "tier": str(submission.get("tier") or projection.get("tier") or "important"),
        "title": str(card.get("title") or card.get("id") or ""),
        "status": status,
        "canvas_state": state,
        "source_subagent_id": submission.get("sourceSubagentId"),
        "source_plan_id": submission.get("sourcePlanId") or projection.get("plan_id") or None,
        "file_relpath": legacy.get("fileRelpath") or None,
        "inline_content": None,
        "annotations": submission.get("annotations") or [],
        "comments": submission.get("comments") or [],
        "annotations_allowed": bool(submission.get("annotationsAllowed", True)),
        "created_at": str(submission.get("submittedAt") or legacy.get("createdAt") or ""),
        "updated_at": str(submission.get("updatedAt") or submission.get("verdictAt")
                          or legacy.get("updatedAt") or submission.get("submittedAt") or ""),
        "history": submission.get("history") or [],
        "pushed_to_user": bool(pushed.get("toUser")),
        "pushed_reason": pushed.get("reason") or None,
        "pushed_at": pushed.get("at") or None,
        "archived": bool(submission.get("archived")),
        "extra": submission.get("extra") if isinstance(submission.get("extra"), dict) else {},
        "review_context": submission.get("reviewContext"),
        "project": str(submission.get("project") or projection.get("project") or "unfiled"),
        "track": str(submission.get("track") or projection.get("track") or ""),
        "version": submission.get("version") if submission.get("version") is not None
        else projection.get("version"),
        "version_family": str(submission.get("versionFamily") or card.get("title") or ""),
        "subject_id": str(submission.get("subjectId") or ""),
        "subject_type": str(submission.get("subjectType") or ""),
        "revision": submission.get("revision"),
        "links": submission.get("links") if isinstance(submission.get("links"), dict) else {},
        # 画布侧独有：定位回原卡所需的坐标，前端「跳回画布」用。
        "canvas": hit.get("canvas") or "",
        "canvas_title": hit.get("canvasTitle") or "",
        "session_id": hit.get("session_id") or "",
        "adapter": str(card.get("adapter") or ""),
        "source": source or None,
        "verdict_at": submission.get("verdictAt") or None,
        "verdict_by": submission.get("verdictBy") or None,
        "reason": submission.get("reason") or None,
        "submitted_by": str(submission.get("submittedBy") or ""),
    }
    return material


def list_materials(*, include_archived: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
    """Material 形状列表（默认不含归档，与旧审阅台「归档归归档桶」同口径）。"""
    rows = [submission_to_material(hit)
            for hit in iter_submission_cards(include_archived=include_archived)]
    rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    return rows if limit is None else rows[:limit]


def stats() -> dict[str, Any]:
    """审阅统计。形状与旧 reviewstage `_stats` 保持一致，另加 archived 口径。"""
    by_status: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    mandatory_unaccepted = 0
    pushed_unread = 0
    archived_total = 0
    live_total = 0
    for hit in iter_submission_cards():
        submission = hit.get("submission") or {}
        if submission.get("archived"):
            archived_total += 1
            continue
        live_total += 1
        state = str(submission.get("state") or "")
        status = CANVAS_STATE_TO_STATUS.get(state, "pending")
        tier = str(submission.get("tier") or "important")
        by_status[status] = by_status.get(status, 0) + 1
        by_tier[tier] = by_tier.get(tier, 0) + 1
        if tier == "mandatory" and status == "pending":
            # 只数 pending: rejected 是用户已裁过的状态(驳回), 再计入「必验收待审」
            # 会让徽标被历史积压淹没(2026-07-19 实例: 19 条里 15 条是已驳回旧稿)。
            mandatory_unaccepted += 1
        pushed = submission.get("pushed")
        if isinstance(pushed, dict) and pushed.get("toUser") and status == "pending":
            pushed_unread += 1
    return {
        "total": live_total,
        "archived_total": archived_total,
        "by_status": by_status,
        "by_tier": by_tier,
        "mandatory_unaccepted": mandatory_unaccepted,
        "pushed_unread": pushed_unread,
    }


def get_material(material_id: str) -> dict[str, Any] | None:
    """按 id 取单条 Material 形状投影；不存在返回 None。

    迁移卡保留旧 mat_* id 作为卡 id，v25 之后的新卡是 review-* id，两种都按
    `card.id` 命中。需要旧库时代资产（封面/抽帧/正文文件）的调用方用投影里的
    `file_relpath` / `extra` 字段自行解析，文件仍在旧库数据目录（只读资产）。
    """
    for hit in iter_submission_cards():
        card = hit.get("card") or {}
        if str(card.get("id") or "") == material_id:
            return submission_to_material(hit)
    return None


def briefing() -> dict[str, Any]:
    """驾驶舱简报用的审阅块（形状兼容旧 `_reviewstage_briefing`）。"""
    try:
        summary = stats()
        recent = list_materials(include_archived=False, limit=8)
    except Exception as error:  # noqa: BLE001
        # 读源整体不可达必须可见，不能退化成 available=True 的空统计。
        return {
            "available": False,
            "error": f"{type(error).__name__}: {error}",
            "total": 0,
            "by_status": {},
            "by_tier": {},
            "mandatory_unaccepted": 0,
            "pushed_unread": 0,
            "recent": [],
        }
    return {
        "available": True,
        **summary,
        "recent": [{
            "id": row["id"],
            "title": row["title"],
            "kind": row["kind"],
            "tier": row["tier"],
            "status": row["status"],
            "source_plan_id": row["source_plan_id"],
            "source_subagent_id": row["source_subagent_id"],
            "pushed_to_user": row["pushed_to_user"],
            "updated_at": row["updated_at"],
            "canvas": row["canvas"],
            "open_ref": {"type": "review_material", "id": row["id"]},
        } for row in recent],
    }
