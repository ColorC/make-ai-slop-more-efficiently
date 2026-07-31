# [OMNI] origin=claude-code domain=decisions ts=2026-07-04T00:00:00Z type=pipeline status=active
# [OMNI] summary="反向固化器 v0:统一决策库的已拍板高权威裁决 → LLM 聚类成规则候选 → markdown 报告(L3,禁自动写规则文档,须人裁)。"
# [OMNI] why="统一设计工作室计划第四期(docs/plans/[2026-07-04]UNIFIED-DESIGN-STUDIO/plan.md §10 第四期)。"
#   "只做候选生成,不碰规则文档/执法器——吸取 ExpeL 插入污染教训(裁决未经复核直接改规则会插毒)"
#   "与 Constitutional AI 教训(纯规则罗列撞泛化天花板,必须保留'为什么'/rationale)。"
# [OMNI] tags=decisions,consolidate,rule-candidate,reverse-solidifier
"""反向固化器 v0 —— 决策 → 规则候选(L3,禁自动落规则文档,须人裁)。

流程:
  1. 从统一决策库(library.active_records())按质量门槛筛候选裁决
     (status=adopted,authority ∈ {user_explicit, high},未被 supersede,statement 非空)。
     不足 3 条 → 如实报"料不够不硬造",不调 LLM,退出。
  2. 用 runtime.llm.structured.call_json 把候选裁决聚类成规则候选:
     规则文本(祈使句)+ 为什么(必须引用裁决 rationale/anchor.excerpt 原话)+
     支撑裁决 id 列表 + 建议落点(自由文本)+ 建议 enforced_by(可空,不许编造)。
  3. 代码过滤:无支撑 id 的候选一律丢弃(不靠提示词自觉)。
  4. 落 markdown 报告,报告头声明 L3 级、禁自动写入、须人裁。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

from . import library

# 质量门槛:只收已拍板 + 高权威的裁决(吸取 ExpeL 教训:未审的低权威推断裁决不许成为规则的原料)。
_ELIGIBLE_STATUS = {"adopted"}
_ELIGIBLE_AUTHORITY = {"user_explicit", "high"}
_MIN_CANDIDATES = 3

DEFAULT_MODEL = "qwen3.6-plus"

_REPORT_DIR = omni_workspace_root() / "data" / "domains" / "decisions" / "consolidation"

_L3_HEADER = (
    "**规则候选 = L3 级,禁自动写进规则文档,须人裁**"
    "(分诊规范 docs/standards/review/发现分诊三级规范.md:"
    "L3=疑似实质错误/自动处置会给幻觉盖章,禁止任何自动修复/自动登记,拦下挂人裁决)。"
)

_CLUSTER_SYSTEM = (
    "你是把一批已拍板的高权威裁决(user_explicit/high authority, status=adopted)聚类归纳成"
    "『规则候选』的助手,供人工审阅是否要写进正式规则文档。"
    "每条规则候选必须满足:\n"
    "1) rule: 祈使句,一句话可执行的规则文本。\n"
    "2) why: 为什么定这条规则——必须引用支撑裁决的 rationale 或原话(anchor.excerpt),"
    "不许只复述 rule 本身,不许空泛正确却不可验证(纯规则罗列会撞泛化天花板,'为什么'不可省)。\n"
    "3) supporting_ids: 支撑此规则候选的裁决 id 列表(必须是输入里给的 id,不许编造)。\n"
    "4) suggested_location: 这条规则该落到哪个域规则文档的哪一节(自由文本描述,不确定就写你的猜测依据)。\n"
    "5) suggested_enforced_by: 如果输入的裁决 links.enforced_by 已经指出了执法器,原样带出;"
    "没有就输出空字符串——绝不允许凭空编造一个不存在的函数名/路径。\n"
    "只基于给定的裁决聚类归纳,不臆造未出现的裁决内容。"
    "同一主题的多条裁决尽量聚成一条规则候选(避免重复噪声),但不要强行合并语义不同的裁决。"
)

_CLUSTER_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule": {"type": "string"},
                    "why": {"type": "string"},
                    "supporting_ids": {"type": "array", "items": {"type": "string"}},
                    "suggested_location": {"type": "string"},
                    "suggested_enforced_by": {"type": "string"},
                },
                "required": ["rule", "why", "supporting_ids"],
            },
        }
    },
    "required": ["candidates"],
}


@dataclass
class RuleCandidate:
    rule: str
    why: str
    supporting_ids: list[str] = field(default_factory=list)
    suggested_location: str = ""
    suggested_enforced_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "why": self.why,
            "supporting_ids": list(self.supporting_ids),
            "suggested_location": self.suggested_location,
            "suggested_enforced_by": self.suggested_enforced_by,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── 1. 质量门槛筛选 ──────────────────────────────────────────────────────────

def eligible_decisions(*, project: str | None = None, tag: str | None = None) -> list[dict]:
    """从统一库筛候选裁决:status=adopted + authority ∈ {user_explicit, high} +
    未被 supersede(即没有别的记录把它列进 links.supersedes)+ statement 非空。
    """
    records = library.active_records()

    superseded_ids: set[str] = set()
    for r in records:
        for sid in (r.get("links") or {}).get("supersedes") or []:
            superseded_ids.add(sid)

    out: list[dict] = []
    for r in records:
        if r.get("kind") != "decision":
            continue
        if (r.get("status") or "") not in _ELIGIBLE_STATUS:
            continue
        if (r.get("authority") or "") not in _ELIGIBLE_AUTHORITY:
            continue
        if not (r.get("statement") or "").strip():
            continue
        if r.get("id") in superseded_ids:
            continue
        if project and (r.get("project") or "") != project:
            continue
        if tag and tag not in (r.get("tags") or []):
            continue
        out.append(r)
    return out


# ── 2. LLM 聚类成规则候选 ────────────────────────────────────────────────────

def _decision_brief(r: dict) -> dict[str, Any]:
    anchor = r.get("anchor") or {}
    return {
        "id": r.get("id"),
        "statement": r.get("statement", ""),
        "rationale": r.get("rationale", ""),
        "anchor_excerpt": anchor.get("excerpt", ""),
        "decision_space": r.get("decision_space") or [],
        "project": r.get("project", ""),
        "tags": r.get("tags") or [],
        "links_enforced_by": (r.get("links") or {}).get("enforced_by") or [],
    }


def cluster_candidates(decisions: list[dict], *, model: str | None = None) -> list[RuleCandidate]:
    """调 LLM 把候选裁决聚类成规则候选;无支撑 id 的候选直接丢弃(代码过滤,不靠提示词自觉)。"""
    from omnicompany.runtime.llm.structured import call_json

    valid_ids = {r.get("id") for r in decisions if r.get("id")}
    briefs = [_decision_brief(r) for r in decisions]

    result = call_json(
        system=_CLUSTER_SYSTEM,
        user=json.dumps({"裁决列表": briefs}, ensure_ascii=False),
        schema=_CLUSTER_SCHEMA,
        model=model or DEFAULT_MODEL,
        caller="decisions.consolidate",
        max_tokens=6000,
    )

    out: list[RuleCandidate] = []
    for c in (result or {}).get("candidates") or []:
        if not isinstance(c, dict):
            continue
        supporting = [sid for sid in (c.get("supporting_ids") or []) if sid in valid_ids]
        if not supporting:
            # 无支撑 id(或支撑 id 不在本次输入范围内)→ 丢弃,不许凭空规则候选。
            continue
        rule = (c.get("rule") or "").strip()
        why = (c.get("why") or "").strip()
        if not rule or not why:
            continue
        out.append(RuleCandidate(
            rule=rule,
            why=why,
            supporting_ids=supporting,
            suggested_location=(c.get("suggested_location") or "").strip(),
            suggested_enforced_by=(c.get("suggested_enforced_by") or "").strip(),
        ))
    return out


# ── 3. 报告落盘 ──────────────────────────────────────────────────────────────

def _report_path(project: str, *, day: str | None = None) -> Path:
    safe_project = (project or "unfiled").strip() or "unfiled"
    return _REPORT_DIR / f"{safe_project}-{day or _today()}.md"


def _render_report(*, project: str, tag: str | None, candidates: list[RuleCandidate],
                    decisions_by_id: dict[str, dict]) -> str:
    lines = [
        f"# 反向固化器 v0 · 规则候选 · {project}",
        "",
        f"> {_L3_HEADER}",
        "",
        f"- 生成时间: {_now_iso()}",
        f"- 项目: {project}" + (f" · 标签过滤: {tag}" if tag else ""),
        f"- 候选裁决数: {len(decisions_by_id)}",
        f"- 规则候选数: {len(candidates)}",
        "",
        "---",
        "",
    ]
    if not candidates:
        lines.append("(本轮无可溯源的规则候选——所有 LLM 输出候选均无支撑裁决 id,已全部丢弃。)")
        lines.append("")
        return "\n".join(lines)

    for i, c in enumerate(candidates, 1):
        lines.append(f"## 候选 {i}: {c.rule}")
        lines.append("")
        lines.append(f"**为什么**: {c.why}")
        lines.append("")
        lines.append(f"**建议落点**: {c.suggested_location or '(未给出,需人补)'}")
        lines.append("")
        lines.append(f"**建议 enforced_by**: {c.suggested_enforced_by or '(空——未指认现成执法器,不得编造)'}")
        lines.append("")
        lines.append("**支撑裁决**:")
        for sid in c.supporting_ids:
            rec = decisions_by_id.get(sid)
            if rec:
                lines.append(f"- `{sid}` {rec.get('statement', '')[:80]}")
            else:
                lines.append(f"- `{sid}`")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def run(*, project: str, tag: str | None = None, model: str | None = None,
        submit: bool = False) -> dict[str, Any]:
    """反向固化器 v0 主入口:筛裁决 → 聚类 → 落报告。返回结果摘要(供 CLI 打印)。

    submit=True 时同时把每条规则候选排进候选流水线(审阅台 decision-candidate 队列,
    信号入口②固化聚类——决策本体 §六;仍禁自动写规则文档,人裁后 candidate-apply 写回)。
    """
    decisions = eligible_decisions(project=project, tag=tag)

    if len(decisions) < _MIN_CANDIDATES:
        return {
            "ok": True,
            "skipped": True,
            "reason": f"料不够不硬造:合格候选裁决仅 {len(decisions)} 条(需 ≥ {_MIN_CANDIDATES})",
            "eligible_count": len(decisions),
            "report_path": None,
            "candidate_count": 0,
        }

    candidates = cluster_candidates(decisions, model=model)
    decisions_by_id = {r["id"]: r for r in decisions if r.get("id")}

    report_md = _render_report(project=project, tag=tag, candidates=candidates,
                               decisions_by_id=decisions_by_id)
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = _report_path(project)
    path.write_text(report_md, encoding="utf-8")

    submitted: list[dict[str, Any]] = []
    if submit and candidates:
        from . import candidates as cand_mod

        for c in candidates:
            body = (
                f"## 固化聚类规则候选\n\n**规则(祈使句)**: {c.rule}\n\n"
                f"**为什么**: {c.why}\n\n"
                f"**支撑裁决**: {', '.join(f'`{sid}`' for sid in c.supporting_ids)}\n\n"
                f"**建议落点**: {c.suggested_location or '(未给出)'}\n\n"
                f"accept 后 candidate-apply 会把 proposed 写为新陈述(rests_on 支撑裁决)。\n"
            )
            res = cand_mod.submit_candidate(
                title=f"[固化聚类] {c.rule[:60]}",
                body_md=body,
                source="consolidate",
                action="new",
                proposed={
                    "kind": "decision",
                    "statement": c.rule,
                    "rationale": c.why,
                    "status": "adopted",
                    "project": project,
                    "authority": "high",
                    "links": {"rests_on": c.supporting_ids},
                    "decision_space": [
                        {"option": c.rule, "chosen": True, "why": c.why},
                        {"option": "维持无规则(逐案裁决)", "chosen": False,
                         "why": "同类裁决已反复出现,逐案成本高于固化"},
                    ],
                },
                project=project,
                by="decisions.consolidate",
            )
            submitted.append(res)

    return {
        "ok": True,
        "skipped": False,
        "reason": "",
        "eligible_count": len(decisions),
        "report_path": str(path),
        "candidate_count": len(candidates),
        "submitted": submitted,
    }
