# [OMNI] origin=codex domain=dashboard/boss_sight ts=2026-07-10T00:00:00Z type=service
# [OMNI] summary="后台治理报告进入审阅台的合并去重入口"
# [OMNI] why="逐条人工待办会刷屏; 相同 finding 集只保留一份带明确理由的审阅材料"
# [OMNI] tags=reviewstage,governance,dedupe,report
# [OMNI] material_id="material:dashboard.boss_sight.reviewstage.report_submission.py"
"""One deduplicated entry point for background governance review reports."""

from __future__ import annotations

import hashlib
import os
from typing import Any

from .store import MaterialStore


def submit_markdown_report(
    store: MaterialStore,
    *,
    title: str,
    content: str,
    source_plan_id: str,
    reason: str,
    dedupe_key: str,
    stable_payload: str,
    project: str = "omnicompany",
    track: str = "语义治理",
    tier: str = "important",
    version_family: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def _with_current_source(result: dict[str, Any]) -> dict[str, Any]:
        material_id = result.get("material_id")
        if material_id and (os.environ.get("CODEX_THREAD_ID") or os.environ.get("OMNI_CC_TRACE_ID")):
            from .readback import link_material_to_current_conversation
            result["source_link"] = link_material_to_current_conversation(material_id, source_plan_id)
        return result

    family = (version_family or title).strip()
    fingerprint = hashlib.sha256(stable_payload.encode("utf-8")).hexdigest()
    existing = store.list(include_archived=True, project=project, track=track)
    for material in existing:
        meta = material.extra or {}
        if meta.get("report_dedupe_key") == dedupe_key and meta.get("report_fingerprint") == fingerprint:
            return _with_current_source({"submitted": False, "reason": "unchanged", "material_id": material.id})
    versions = [int(material.version or 0) for material in existing if material.version_family == family]
    metadata = dict(extra or {})
    metadata.update({"report_dedupe_key": dedupe_key, "report_fingerprint": fingerprint})
    material = store.create(
        kind="markdown", tier=tier, title=title,
        source_plan_id=source_plan_id, inline_content=content,
        extra=metadata, project=project, track=track,
        version=max(versions, default=0) + 1, version_family=family,
    )
    result: dict[str, Any] = {
        "submitted": True,
        "material_id": material.id,
        "reason": reason,
        "pushed": False,
    }
    try:
        store.mark_pushed(material.id, reason=reason)
    except PermissionError as exc:
        # Report creation remains durable, but a background producer cannot turn
        # a submit-host-only check into a user-visible push.
        result["push_blocked"] = str(exc)
    else:
        result["pushed"] = True
    return _with_current_source(result)
