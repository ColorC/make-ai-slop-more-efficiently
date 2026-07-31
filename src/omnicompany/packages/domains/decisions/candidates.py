# [OMNI] origin=claude-code domain=decisions ts=2026-07-10T00:00:00Z type=module status=active
# [OMNI] summary="候选流水线(原语四):一切改陈述库的提议统一登记为内部 decision-candidate 治理件。四信号入口(偏离聚集/固化聚类/到期/人工与AI记忆)+确认后写回(新版本+取代链,有验证件先过门)。"
# [OMNI] why="plan §六:三回路合一条流水线;人机同门只差发起者;报告偏离与提议修订两个动作;唯一写入通道=候选流水线(手册治理规则1)。"
# [OMNI] tags=decisions,candidates,pipeline,review,decision-ontology
"""候选流水线(决策本体原语四)。

流:信号 → submit_candidate(内部治理件 kind=decision-candidate, pending)
   → 人裁(以对话答复为主；必要时可对材料 accept/reject)
   → apply_accepted(写回陈述库:new/revise/retire;有验证件的记录需 verified_note 才放行)
   → 材料盖 applied 标记(extra.candidate.applied_at/applied_record_id)。

纪律:
  - 本模块是陈述库「修订」的唯一程序入口;偏离「报告」在 ledger(两个显式动作,不许一步到位)。
  - 候选去重:同 (source, action, target_ids[或无target时标题]) 已有 pending 候选不重复进队。
  - 写回永不删除:revise=新版本+supersedes 链;retire=状态置换(词表内),记录保留。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import library

CANDIDATE_KIND = "decision-candidate"
_SOURCES = ("deviation", "consolidate", "expiry", "memory", "human")
_ACTIONS = ("new", "revise", "retire")

# 到期信号:按 kind 配预期寿命(天)——超龄且无消费/无链的 adopted/untested 记录进复核队列。
DEFAULT_LIFETIMES_DAYS = {"decision": 90, "belief": 45, "comment": 60}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_store():
    """审阅台 MaterialStore(与 omni review submit 同一构造路径)。"""
    try:
        from omnicompany.dashboard.boss_sight.reviewstage.routes import get_store
        return get_store()
    except Exception:
        from omnicompany.core.config import omni_workspace_root
        from omnicompany.dashboard.boss_sight.reviewstage import MaterialStore
        from omnicompany.dashboard.boss_sight.reviewstage.material_types import (
            default_review_format_registry,
        )
        return MaterialStore(
            root=omni_workspace_root() / "data" / "boss_sight" / "reviewstage",
            format_registry=default_review_format_registry(),
        )


def _list_candidates(store=None, *, include_archived: bool = False) -> list:
    """列全部候选治理件(store.list 无 kind 过滤,这里自筛)。"""
    store = store or _get_store()
    return [m for m in store.list(
                include_archived=include_archived,
                include_internal=True,
            )
            if (m.kind.value if hasattr(m.kind, "value") else m.kind) == CANDIDATE_KIND]


def _open_candidates(store=None) -> list:
    return [m for m in _list_candidates(store)
            if (m.status.value if hasattr(m.status, "value") else m.status) == "pending"]


def _dupe_key(source: str, action: str, target_ids: list[str], title: str = "") -> str:
    """去重键。有 target 时按 (source,action,targets)——同一目标同类提议不重复排队;
    无 target 的 new 提案彼此无关,纳入归一化标题防误伤(空 target 曾把两条不相关提案互相去重)。"""
    tgt = ",".join(sorted(target_ids or []))
    if not tgt:
        tgt = f"title:{(title or '').strip().lower()[:80]}"
    return f"{source}|{action}|{tgt}"


def submit_candidate(
    *,
    title: str,
    body_md: str,
    source: str,
    action: str,
    target_ids: list[str] | None = None,
    proposed: dict[str, Any] | None = None,
    project: str = "omnicompany",
    by: str = "",
    tier: str = "important",
    source_plan_id: str = "",
    dedupe: bool = True,
) -> dict[str, Any]:
    """登记一条内部候选治理件。返回 {material_id | skipped_duplicate}。"""
    if source not in _SOURCES:
        raise ValueError(f"source 须为 {_SOURCES}, 收到 {source!r}")
    if action not in _ACTIONS:
        raise ValueError(f"action 须为 {_ACTIONS}, 收到 {action!r}")
    target_ids = [t for t in (target_ids or []) if (t or "").strip()]
    if action in ("revise", "retire") and not target_ids:
        raise ValueError(f"action={action} 必须给 target_ids")
    for t in target_ids:
        if not library.get(t):
            raise ValueError(f"target 记录不存在: {t}")

    store = _get_store()
    if dedupe:
        key = _dupe_key(source, action, target_ids, title)
        for m in _open_candidates(store):
            c = (m.extra or {}).get("candidate") or {}
            if _dupe_key(c.get("source", ""), c.get("action", ""),
                         c.get("target_ids") or [], m.title or "") == key:
                return {"skipped_duplicate": m.id}

    m = store.create(
        kind=CANDIDATE_KIND,
        tier=tier,
        title=title[:200],
        inline_content=body_md,
        source_plan_id=(source_plan_id or "").strip() or None,
        project=project,
        track="决策候选",
        version=1,
        version_family=f"candidate:{_dupe_key(source, action, target_ids, title)}"[:120],
        extra={
            "reviewstage_visibility": "internal",
            "candidate": {
                "source": source,
                "action": action,
                "target_ids": target_ids,
                "proposed": proposed or {},
                "by": by or "",
                "submitted_at": _now_iso(),
            },
        },
    )
    return {"material_id": m.id}


# ── 信号入口①: 偏离聚集 ─────────────────────────────────────────────────────

def scan_deviations(min_count: int = 2, limit: int = 500) -> list[dict[str, Any]]:
    """偏离聚集处=规则该修处:同一陈述被 ≥min_count 笔偏离引用 → 一条修订候选。"""
    from omnicompany.packages.services._core.ledger import list_deviations

    by_ref: dict[str, list[dict]] = {}
    for ev in list_deviations(limit):
        d = ev.get("deviation") or {}
        for ref in d.get("refs") or []:
            by_ref.setdefault(ref, []).append(ev)

    out: list[dict[str, Any]] = []
    for ref, evs in sorted(by_ref.items(), key=lambda kv: -len(kv[1])):
        if len(evs) < min_count:
            continue
        rec = library.get(ref)
        if not rec:
            continue
        notes = "\n".join(f"- [{(e.get('deviation') or {}).get('kind')}] "
                          f"{(e.get('deviation') or {}).get('note', '')}"
                          f"({e.get('time', '')}, {e.get('agent', '') or '匿名'})"
                          for e in evs)
        body = (
            f"## 偏离聚集修订候选\n\n"
            f"**目标陈述**: `{ref}` {rec.get('statement', '')}\n\n"
            f"**聚集偏离({len(evs)} 笔)**:\n{notes}\n\n"
            f"**提议**: 复核该陈述是否仍成立;偏离一致指向的方向即修订方向。"
            f"编辑本候选评论区给出修订文本,accept 后由 candidate-apply 写回。\n"
        )
        res = submit_candidate(
            title=f"[偏离聚集] {ref} 被 {len(evs)} 笔偏离引用",
            body_md=body,
            source="deviation",
            action="revise",
            target_ids=[ref],
            project=rec.get("project") or "omnicompany",
            by="candidates.scan_deviations",
        )
        out.append({"ref": ref, "deviations": len(evs), **res})
    return out


# ── 信号入口③: 到期与信号 ────────────────────────────────────────────────────

def scan_expiry(
    lifetimes_days: dict[str, int] | None = None,
    *,
    max_candidates: int = 10,
) -> list[dict[str, Any]]:
    """按类型配预期寿命,超龄进复核队列。

    进队条件(全部满足):
      - status ∈ {adopted(decision), untested(belief)}(其余状态已有下场);
      - 距 updated_at 超过该 kind 预期寿命;
      - 非蒸馏态(手册条目另有手册治理);
      - 非枢纽:没有任何 active 记录 links 指向它(枢纽陈述不可删,先修下游)。
    单边化:一次扫描最多 max_candidates 条,按超龄程度降序。
    """
    lifetimes = {**DEFAULT_LIFETIMES_DAYS, **(lifetimes_days or {})}
    now = datetime.now(timezone.utc)
    recs = library.active_records()

    inbound: set[str] = set()
    for r in recs:
        links = r.get("links") or {}
        for rel in ("rests_on", "supersedes", "related"):
            inbound.update(links.get(rel) or [])
        if links.get("parent"):
            inbound.add(links["parent"])

    def _age_days(r: dict) -> float:
        ts = r.get("updated_at") or r.get("created_at") or ""
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (now - dt).total_seconds() / 86400
        except ValueError:
            return 0.0

    eligible: list[tuple[float, dict]] = []
    for r in recs:
        kind = r.get("kind") or ""
        status = r.get("status") or ""
        if (kind, status) not in (("decision", "adopted"), ("belief", "untested")):
            continue
        if r.get("distilled"):
            continue
        if r.get("id") in inbound:
            continue
        life = lifetimes.get(kind, 90)
        age = _age_days(r)
        if age > life:
            eligible.append((age - life, r))

    eligible.sort(key=lambda t: -t[0])
    out: list[dict[str, Any]] = []
    for overdue, rec in eligible[:max_candidates]:
        rid = rec["id"]
        body = (
            f"## 到期复核候选\n\n"
            f"**目标陈述**: `{rid}` {rec.get('statement', '')}\n\n"
            f"- kind={rec.get('kind')} status={rec.get('status')} "
            f"超预期寿命 {overdue:.0f} 天,且无任何链入引用(非枢纽)。\n\n"
            f"**提议**: 仍有效→accept 后 apply 不动内容只续期(重写一版);"
            f"已过时→改本候选 action(评论说明)走退役。\n"
        )
        res = submit_candidate(
            title=f"[到期复核] {rid} 超龄 {overdue:.0f} 天无引用",
            body_md=body,
            source="expiry",
            action="retire",
            target_ids=[rid],
            project=rec.get("project") or "omnicompany",
            by="candidates.scan_expiry",
        )
        out.append({"ref": rid, "overdue_days": round(overdue), **res})
    return out


# ── 写回: accept 后的唯一修订通道 ────────────────────────────────────────────

def apply_accepted(*, verified_note: str = "", by: str = "") -> list[dict[str, Any]]:
    """把审阅台已 accept 且未 applied 的候选写回陈述库。

    - action=new:    upsert proposed 为新记录;
    - action=revise: upsert proposed 为新记录 + links.supersedes=target_ids;旧记录状态置 superseded;
    - action=retire: decision→revoked / comment→resolved / belief→按 proposed.outcome 走 resolve
                     (未给 outcome 时置 challenged 待验,绝不静默证伪);
    - 验证件门:target 记录带 verification 时,必须给 verified_note(说明跑过什么)才放行。
    """
    store = _get_store()
    applied: list[dict[str, Any]] = []
    for m in _list_candidates(store):
        status = m.status.value if hasattr(m.status, "value") else m.status
        c = (m.extra or {}).get("candidate") or {}
        if status != "accepted" or c.get("applied_at"):
            continue
        action = c.get("action")
        targets = [t for t in (c.get("target_ids") or []) if library.get(t)]
        proposed = c.get("proposed") or {}

        gated = [t for t in targets if (library.get(t) or {}).get("verification")]
        if gated and not verified_note.strip():
            applied.append({"material_id": m.id, "skipped": True,
                            "reason": f"目标带验证件 {gated},须 --verified '<跑过什么>' 才写回"})
            continue

        note = {"candidate_material": m.id, "by": by or c.get("by") or "candidate-apply"}
        if verified_note.strip():
            note["verified"] = verified_note.strip()

        new_id = ""
        if action in ("new", "revise") and proposed.get("statement"):
            payload = dict(proposed)
            payload.setdefault("kind", "decision")
            payload.setdefault("status", "adopted" if payload["kind"] == "decision" else None)
            payload = {k: v for k, v in payload.items() if v is not None}
            payload.setdefault("origin", {"channel": "manual", "author": by or c.get("by") or ""})
            payload.setdefault("rationale", f"候选流水线写回(材料 {m.id})")
            if action == "revise" and targets:
                links = dict(payload.get("links") or {})
                links["supersedes"] = sorted(set((links.get("supersedes") or []) + targets))
                payload["links"] = links
            rec, _ = library.upsert(payload)
            new_id = rec["id"]
            if action == "revise":
                for t in targets:
                    old = library.get(t) or {}
                    if old.get("kind") == "decision":
                        library.set_status(t, "superseded")
        elif action == "retire":
            for t in targets:
                old = library.get(t) or {}
                k = old.get("kind")
                if k == "decision":
                    library.set_status(t, "revoked")
                elif k == "comment":
                    library.set_status(t, "resolved")
                elif k == "belief":
                    outcome = (proposed.get("outcome") or "").strip()
                    if outcome in ("supported", "partial", "falsified"):
                        library.resolve(t, outcome,
                                        evidence=proposed.get("evidence", "候选流水线裁决"),
                                        method="candidate-apply", by=by)
                    else:
                        library.challenge(t, f"到期复核未续期(材料 {m.id})",
                                          challenger=by or "candidate-apply")
        else:
            applied.append({"material_id": m.id, "skipped": True,
                            "reason": f"action={action} 但 proposed.statement 为空,无从写回"})
            continue

        store.patch_extra(m.id, {"candidate": {**c, "applied_at": _now_iso(),
                                                "applied_record_id": new_id,
                                                "applied_note": note}},
                          by=by or "candidate-apply")
        applied.append({"material_id": m.id, "action": action,
                        "applied_record_id": new_id, "targets": targets})
    return applied


def queue_status() -> dict[str, Any]:
    """候选队列现状(按状态/来源计数)。"""
    store = _get_store()
    mats = _list_candidates(store)
    by_status: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for m in mats:
        s = m.status.value if hasattr(m.status, "value") else m.status
        by_status[s] = by_status.get(s, 0) + 1
        src = ((m.extra or {}).get("candidate") or {}).get("source") or "?"
        by_source[src] = by_source.get(src, 0) + 1
    return {"total": len(mats), "by_status": by_status, "by_source": by_source}
