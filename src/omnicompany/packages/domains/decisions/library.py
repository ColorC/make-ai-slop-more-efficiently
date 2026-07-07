# [OMNI] origin=ai-ide domain=decisions ts=2026-06-18T00:00:00Z type=library status=active
# [OMNI] summary="统一决策库:决策/猜想/评论的唯一落点。append-only jsonl + fold 取最新 + 按 id 增量合并 + 墓碑软删 + 落库校验。"
# [OMNI] why="主线=决策记录。所有源(对话/collab platform/策划文档/札记)和手记都汇进这一个库,成可搜索的决策树。照 research/library 范式,但去重键=显式 id(决策有稳定身份)而非题目归一。"
# [OMNI] tags=decisions,library,decision-record,dedup
"""统一决策库 —— 决策/猜想/评论的累积存储。

存储: records.jsonl(append-only,一行一条 decision.record,最新行权威)。
身份: 显式 id(DEC-/BLF-/CMT-YYYY-MM-DD-NNN)。同 id 再写 = 增量合并(累积段并集、标量最新胜)。
软删: status='deleted' 墓碑,id 不复用。
决策树: 不靠目录,靠记录里的 links(rests_on/supersedes/parent/related)链出拓扑。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from ._paths import RECORDS_PATH, ensure_dirs

# kind → id 前缀 / 默认生命周期起点(见 formats.py DECISION_RECORD.status 描述)
_KIND_PREFIX = {"decision": "DEC", "belief": "BLF", "comment": "CMT"}
_KIND_INIT_STATUS = {"decision": "proposed", "belief": "untested", "comment": "open"}

# links 的边:parent 是标量(单父),其余是列表。
# enforced_by 的值是执法载体标识(如 demogame.design_doc_lint.check7_self_reference),不是记录 id。
_LINK_LIST_RELS = ("rests_on", "supersedes", "related", "enforced_by")
_LINK_SCALAR_RELS = ("parent",)


class LibraryWriteError(Exception):
    """落库前自动校验不过(strict 模式)抛出。"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── 读 / 折叠 ───────────────────────────────────────────────────────────────

def _read_lines() -> list[dict]:
    if not RECORDS_PATH.is_file():
        return []
    out: list[dict] = []
    for line in RECORDS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def fold() -> dict[str, dict]:
    """折叠成当前态:每个 id 取最新写入的那行(含墓碑)。"""
    folded: dict[str, dict] = {}
    for rec in _read_lines():
        rid = rec.get("id")
        if rid:
            folded[rid] = rec
    return folded


def active_records() -> list[dict]:
    return [r for r in fold().values() if r.get("status") != "deleted"]


def get(record_id: str) -> dict | None:
    rec = fold().get(record_id)
    if rec and rec.get("status") != "deleted":
        return rec
    return None


# ── id 生成(DEC/BLF/CMT-YYYY-MM-DD-NNN,当日当 kind 顺延,不复用墓碑号)──────────

def new_id(kind: str, day: str | None = None) -> str:
    prefix = _KIND_PREFIX.get(kind)
    if not prefix:
        raise ValueError(f"未知 kind: {kind!r}(应为 decision|belief|comment)")
    day = day or _today()
    stem = f"{prefix}-{day}-"
    # 扫所有历史 id(含墓碑)避免复用号
    mx = 0
    for rec in _read_lines():
        rid = rec.get("id") or ""
        if rid.startswith(stem):
            tail = rid[len(stem):]
            if tail.isdigit():
                mx = max(mx, int(tail))
    return f"{stem}{mx + 1:03d}"


def new_record(kind: str, statement: str, **fields: Any) -> dict:
    """造一条待写记录(填默认:status 按 kind / scope=personal / created_at),不落库。"""
    if kind not in _KIND_PREFIX:
        raise ValueError(f"未知 kind: {kind!r}")
    rec: dict[str, Any] = {
        "kind": kind,
        "statement": (statement or "").strip(),
        "scope": fields.pop("scope", "personal"),
        "status": fields.pop("status", _KIND_INIT_STATUS[kind]),
    }
    rec.update({k: v for k, v in fields.items() if v is not None})
    return rec


# ── 合并(同 id 再写:累积段并集,标量最新胜)──────────────────────────────────

def _union_strs(old: list[str] | None, new: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in (old or []) + (new or []):
        s = (x or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _union_by_key(old: list[dict] | None, new: list[dict] | None, key: str) -> list[dict]:
    """按某字段去重合并;后来者覆盖同键项,空键项全保留。"""
    merged: list[dict] = []
    idx: dict[str, int] = {}
    for it in (old or []) + (new or []):
        if not isinstance(it, dict):
            continue
        k = str(it.get(key, "")).strip()
        if k and k in idx:
            merged[idx[k]] = it
        else:
            if k:
                idx[k] = len(merged)
            merged.append(it)
    return merged


def _merge_links(old: dict | None, new: dict | None) -> dict:
    old = old or {}
    new = new or {}
    out: dict[str, Any] = {}
    for rel in _LINK_LIST_RELS:
        u = _union_strs(old.get(rel), new.get(rel))
        if u:
            out[rel] = u
    parent = new.get("parent") or old.get("parent")
    if parent:
        out["parent"] = parent
    return out


def _merge(existing: dict, incoming: dict) -> dict:
    """同 id 再写的合并:累积段并集、链合并、challenge_log 追加,其余标量取 incoming 非空值。"""
    m = dict(existing)
    accumulate_str = ("tags", "aliases")
    for k in accumulate_str:
        u = _union_strs(existing.get(k), incoming.get(k))
        if u:
            m[k] = u
    m["decision_space"] = _union_by_key(existing.get("decision_space"), incoming.get("decision_space"), "option")
    m["evidence"] = _union_by_key(existing.get("evidence"), incoming.get("evidence"), "ref")
    m["challenge_log"] = _union_by_key(existing.get("challenge_log"), incoming.get("challenge_log"), "ts")
    m["links"] = _merge_links(existing.get("links"), incoming.get("links"))
    # 标量:incoming 非空则覆盖
    handled = set(accumulate_str) | {"decision_space", "evidence", "challenge_log", "links",
                                     "id", "kind", "created_at", "created_by"}
    for k, v in incoming.items():
        if k in handled:
            continue
        if v not in (None, "", [], {}):
            m[k] = v
    # 清掉合并出来的空容器
    for k in ("decision_space", "evidence", "challenge_log"):
        if not m.get(k):
            m.pop(k, None)
    if not m.get("links"):
        m.pop("links", None)
    m["created_at"] = existing.get("created_at") or incoming.get("created_at") or now_iso()
    m["updated_at"] = now_iso()
    return m


# ── 落库校验 ─────────────────────────────────────────────────────────────────

def validate_record(record: dict) -> list[str]:
    """schema 校验(decision.record)+ 决策域专属 lint(显化决策须列被否决项)。"""
    from omnicompany.runtime.llm.structured import validate_json_schema

    from .formats import DECISION_RECORD

    issues = [f"{i.path}: {i.message}"
              for i in validate_json_schema(record, DECISION_RECORD.json_schema)]
    kind = record.get("kind")
    if kind == "decision":
        # 只有"已拍板"的决策才要求列被否决项;status=proposed 是开放选择空间/备选库,
        # 多个候选未选很正常(那是"还需要定什么"的清单,不是缺陷,更不是矛盾)。
        status = (record.get("status") or "").lower()
        made = status in ("adopted", "superseded", "revoked")
        space = record.get("decision_space") or []
        has_rejected = any(o.get("chosen") is False for o in space if isinstance(o, dict))
        if made and not has_rejected:
            issues.append("decision: 已拍板却没列被否决项 —— 不算显化决策(见 DESIGN)")
    if kind == "belief" and not record.get("risk_if_wrong"):
        issues.append("belief: 缺 risk_if_wrong(猜想错了多大代价,关系到要不要验)")
    # enforced_by 边纪律(双权威 A6):库是索引,规则正文的人读权威在域文档。
    links = record.get("links") or {}
    if links.get("enforced_by"):
        anchor = record.get("anchor") or {}
        if not (anchor.get("ref") or "").strip():
            issues.append("enforced_by: 带执法边的记录必须有 anchor.ref 指回规则文档(库=索引,文档=人读权威)")
        if len(record.get("statement") or "") > 200:
            issues.append("enforced_by: statement 超 200 字——索引存要点与原话引文,规则正文留在域文档")
    return issues


# ── 写 ───────────────────────────────────────────────────────────────────────

def upsert(record: dict) -> tuple[dict, bool]:
    """写入决策库。无 id 则生成;同 id 已存在→增量合并。返 (record, is_update)。

    OMNI_DECISIONS_STRICT_WRITE=1 时校验不过直接抛 LibraryWriteError;默认仍落库但打 validation 标记。
    """
    ensure_dirs()
    kind = record.get("kind")
    if kind not in _KIND_PREFIX:
        raise ValueError(f"未知/缺 kind: {kind!r}")
    record = dict(record)
    record.setdefault("statement", "")

    rid = record.get("id")
    folded = fold()
    if not rid:
        rid = new_id(kind)
        record["id"] = rid
    existing = folded.get(rid)
    is_update = bool(existing and existing.get("status") != "deleted")

    if is_update:
        record = _merge(existing, record)
    else:
        record.setdefault("created_at", now_iso())
        record["updated_at"] = record["created_at"]
        record.setdefault("status", _KIND_INIT_STATUS.get(kind, "proposed"))

    issues = validate_record(record)
    record["validation"] = {"ok": not issues, "issues": issues, "checked_at": now_iso()}
    if issues and os.environ.get("OMNI_DECISIONS_STRICT_WRITE") == "1":
        raise LibraryWriteError(f"record {rid} 校验不过: {issues}")

    with RECORDS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record, is_update


def soft_delete(record_id: str) -> bool:
    """墓碑软删(写一行 status=deleted)。id 不复用。"""
    rec = get(record_id)
    if not rec:
        return False
    ensure_dirs()
    rec = dict(rec)
    rec["status"] = "deleted"
    rec["updated_at"] = now_iso()
    with RECORDS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def add_link(src_id: str, rel: str, dst_id: str) -> dict:
    """给决策树加一条边:src --rel--> dst。rel ∈ rests_on/supersedes/parent/related/enforced_by。

    enforced_by 的 dst 是执法载体标识(函数/token 路径),不校验为记录 id。
    """
    if rel not in _LINK_LIST_RELS + _LINK_SCALAR_RELS:
        raise ValueError(f"未知关系 {rel!r}(应为 {_LINK_LIST_RELS + _LINK_SCALAR_RELS})")
    src = get(src_id)
    if not src:
        raise ValueError(f"源记录不存在: {src_id}")
    links = dict(src.get("links") or {})
    if rel in _LINK_SCALAR_RELS:
        links[rel] = dst_id
    else:
        links[rel] = _union_strs(links.get(rel), [dst_id])
    rec, _ = upsert({**src, "links": links})
    return rec


def set_status(record_id: str, status: str) -> dict:
    rec = get(record_id)
    if not rec:
        raise ValueError(f"记录不存在: {record_id}")
    out, _ = upsert({**rec, "status": status})
    return out
