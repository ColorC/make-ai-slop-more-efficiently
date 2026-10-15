# [OMNI] origin=ai-ide domain=dashboard/boss_sight ts=2026-06-14T00:00:00Z type=infra status=active
# [OMNI] summary="一次性迁移: reviewstage Material.comments[] → 中心 authored Note。幂等(按 src_comment_id 去重)。"
# [OMNI] why="统一评论入口后, 旧的内嵌评论要搬进中心 store, 才能在集中管理面看到。"
# [OMNI] tags=authored,migration
"""把 reviewstage 各 material 内嵌 comments[] 迁进中心 authored store。

幂等: 每条迁出的 Note 在 extra.src_comment_id 记原 comment id; 重跑时已存在的跳过。
原 comments[] 不动(冻结为历史)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _reviewstage_root() -> Path:
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data" / "boss_sight" / "reviewstage"


def migrate_reviewstage_comments() -> dict[str, Any]:
    from .store import get_authored_store

    store = get_authored_store()
    # 已迁移的 src_comment_id 集合(幂等)
    existing_src = set()
    for n in store.list(include_archived=True):
        sid = (n.extra or {}).get("src_comment_id")
        if sid:
            existing_src.add(sid)

    root = _reviewstage_root()
    migrated = 0
    skipped = 0
    materials = 0
    if root.exists():
        for p in sorted(root.glob("*.json")):
            try:
                m = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            mid = m.get("id") or p.stem
            comments = m.get("comments") or []
            if comments:
                materials += 1
            for c in comments:
                cid = c.get("id")
                if not cid or cid in existing_src:
                    skipped += 1
                    continue
                content = (c.get("content") or "").strip()
                if not content:
                    skipped += 1
                    continue
                ctarget = c.get("target") or {}
                # 原评论 target 的 kind/id 是 vilo 子对象(art/card/wiki_paragraph), 不能盖掉 material;
                # 放进 sub_kind/sub_id, 并带 material 的 source_plan_id 让 project 能算出。
                sub = {k: v for k, v in ctarget.items() if k not in ("kind", "id")}
                target = {
                    "kind": "material", "id": mid, "material_id": mid,
                    "plan_id": m.get("source_plan_id"),
                    "sub_kind": ctarget.get("kind"), "sub_id": ctarget.get("id"), **sub,
                }
                n = store.create(
                    content=content,
                    author=c.get("author", "user"),
                    target=target,
                    uses=["comment"],
                    feedback_status=c.get("feedback_status") or "saved",
                    extra={"src_comment_id": cid, "migrated_from": "reviewstage"},
                )
                # 保留原始时间戳/历史
                n.created_at = c.get("created_at", n.created_at)
                n.feedback_history = list(c.get("feedback_history") or n.feedback_history)
                store._persist(n)
                existing_src.add(cid)
                migrated += 1
    return {"materials_with_comments": materials, "migrated": migrated, "skipped": skipped}


if __name__ == "__main__":
    print(migrate_reviewstage_comments())
