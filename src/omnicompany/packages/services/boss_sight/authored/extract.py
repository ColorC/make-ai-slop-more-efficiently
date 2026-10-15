# [OMNI] origin=ai-ide domain=dashboard/boss_sight ts=2026-07-04T00:00:00Z type=infra status=active
# [OMNI] summary="把标记为 llm_input 的札记, 用性价比模型提炼成结构化决策, 直接落统一决策库(records.jsonl)。增量 checkpoint。"
# [OMNI] why="用户要'作为 llm 输入的决策由性价比 team 定期/手动提取成结构化输入'喂总控。M2(统一设计工作室计划 D3): 两本账必留一本, 旧 authored_decisions.json 平行真源已收编进统一库并归档。"
# [OMNI] tags=authored,decisions,extraction,governance
"""决策提取: uses 含 llm_input 的札记 → 统一决策库记录(kind=decision, status=proposed, authority=derived)。

M2 改造(2026-07-04, UNIFIED-DESIGN-STUDIO §5 M2 / §6 D3):
  - 产物落点 = 统一决策库 data/domains/decisions/library/records.jsonl(唯一真源);
    旧 data/boss_sight/authored_decisions.json 已归档(data/boss_sight/_archive/),不再读写。
  - 幂等键 = alias "authored-note-<note_id>";增量只提取库里没有的札记。
  - 消费(cockpit ctx_summary.decisions)改读统一库投影(load_decisions 保持返回形状)。
  - 运行戳写 data/governance/decisions/extract_last.json(governance catalog 的"上次跑"指针)。
复用统一 LLM 面 runtime.llm.structured.call_json + 默认便宜模型, 顺序 + 逐条落库, 续跑安全。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision_gist": {"type": "string", "description": "这条札记表达的决策/指示, 一句话提炼"},
        "scope": {"type": "string", "description": "作用范围: 如某 project/plan/全局/某材料"},
        "constraint": {"type": "string", "description": "硬约束或要求(如有), 否则空串"},
        "applies_to": {"type": "string", "description": "应作用于谁(project/plan id 或对象描述)"},
    },
    "required": ["decision_gist", "scope"],
}

_SYSTEM = (
    "你是把用户随手写的札记提炼成'给执行 agent 的结构化决策输入'的助手。"
    "只提炼用户的意图/指示/约束, 不臆造。输出严格 JSON。"
)

_NOTE_TAG = "authored-note"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alias(note_id: str) -> str:
    return f"authored-note-{note_id}"


def _stamp_path() -> Path:
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root() / "data" / "governance" / "decisions" / "extract_last.json"


def _existing_note_ids() -> dict[str, str]:
    """库里已炼化的札记: note_id → record_id(按 alias 前缀识别)。"""
    from omnicompany.packages.domains.decisions import library

    out: dict[str, str] = {}
    for rec in library.active_records():
        for a in rec.get("aliases") or []:
            if a.startswith("authored-note-"):
                out[a[len("authored-note-"):]] = rec["id"]
    return out


def _record_payload(note, d: dict[str, Any]) -> dict[str, Any]:
    """札记炼化结果 → 统一库记录。硬约束并进 statement, 不另开自由字段。"""
    gist = (d.get("decision_gist") or "").strip()
    constraint = (d.get("constraint") or "").strip()
    statement = gist + (f"(硬约束: {constraint})" if constraint else "")
    applies = (d.get("applies_to") or "").strip() or (d.get("scope") or "").strip()
    return {
        "kind": "decision",
        "statement": statement[:200],
        "scope": "personal",
        "status": "proposed",              # 札记反推,未经用户拍板 → proposed
        "authority": "derived",
        "project": note.project_id if note.project_id != "unfiled" else "",
        "applies_to": applies,
        "aliases": [_alias(note.id)],
        "tags": [_NOTE_TAG, "札记炼化"],
        "anchor": {"kind": "note", "ref": f"authored-note:{note.id}",
                   "excerpt": (note.content or "")[:200]},
        "origin": {"channel": "note", "session_ref": note.id, "author": "user",
                   "observed_at": note.created_at or _now_iso()},
        "created_by": "authored.extract",
    }


def extract_decisions(*, model: str = "qwen3.6-plus", reextract: bool = False) -> dict[str, Any]:
    """提取 uses 含 llm_input 的札记进统一决策库。增量(库里已有的跳过, reextract=True 重提合并)。"""
    from omnicompany.packages.domains.decisions import catalog, library

    from .store import get_authored_store

    store = get_authored_store()
    notes = [n for n in store.list() if "llm_input" in (n.uses or [])]
    done = _existing_note_ids()
    todo = notes if reextract else [n for n in notes if n.id not in done]

    errors = 0
    extracted = 0
    for n in todo:
        target_ctx = json.dumps(n.target, ensure_ascii=False)
        user = f"札记正文:\n{n.content}\n\n关联对象(target): {target_ctx}\n所属项目: {n.project_id}"
        try:
            from omnicompany.runtime.llm.structured import call_json
            d = call_json(system=_SYSTEM, user=user, schema=DECISION_SCHEMA,
                          model=model, caller="authored.extract", max_tokens=1200)
            payload = _record_payload(n, d)
            if n.id in done:
                payload["id"] = done[n.id]      # 重提 → 合并进原记录
            library.upsert(payload)             # 逐条落库, 续跑安全
            extracted += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            import logging
            logging.getLogger(__name__).warning(
                "[authored.extract] 札记 %s 炼化失败: %s", n.id, str(e)[:200])

    if extracted:
        catalog.rebuild_index()

    result = {"total_llm_input": len(notes), "newly_extracted": extracted,
              "errors": errors, "decisions_total": len(_existing_note_ids())}
    try:
        p = _stamp_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"ts": _now_iso(), **result}, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    except OSError:
        pass
    return result


def load_decisions(max_items: int | None = None) -> list[dict[str, Any]]:
    """供总控 ctx 消费: 从统一库投影札记炼化决策(保持旧返回形状, 消费方零改动)。"""
    from omnicompany.packages.domains.decisions import library

    out: list[dict[str, Any]] = []
    for rec in library.active_records():
        if _NOTE_TAG not in (rec.get("tags") or []):
            continue
        note_id = ""
        for a in rec.get("aliases") or []:
            if a.startswith("authored-note-"):
                note_id = a[len("authored-note-"):]
                break
        out.append({
            "note_id": note_id,
            "decision_gist": rec.get("statement", ""),
            "scope": rec.get("applies_to", ""),
            "constraint": "",                      # 已并进 statement
            "applies_to": rec.get("applies_to", ""),
            "project": rec.get("project", ""),
            "extracted_at": rec.get("updated_at", ""),
        })
    out.sort(key=lambda d: d.get("extracted_at") or "", reverse=True)
    return out[:max_items] if max_items else out


if __name__ == "__main__":
    print(extract_decisions())
