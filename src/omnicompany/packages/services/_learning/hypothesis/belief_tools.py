# [OMNI] origin=claude-code domain=services/hypothesis ts=2026-07-10T00:00:00Z type=router status=active
# [OMNI] summary="假设探索的决策库工具组(v5 收编版):Reflector 直接读写统一决策库 belief,不再编辑 khyp markdown。list/record/challenge/resolve/link 五件套+确定性快照。"
# [OMNI] why="决策本体合并清单#1(docs/plans/[2026-07-10]DECISION-ONTOLOGY/plan.md):hypothesis 管线改读写决策库;猜想=事实性陈述的未验证态,与日常决策同库同设施;主题文档降为生成投影。"
# [OMNI] tags=hypothesis,decisions,belief,tools,decision-ontology
"""假设探索 × 统一决策库 —— Reflector 的工具组。

一条探索假设 = 决策库一条 kind=belief 记录:
  statement=猜想陈述 · risk_if_wrong=错了多大代价 · evidence_query=怎么验证 ·
  status/verification_status=生命周期(untested→challenged→supported|partial|falsified) ·
  challenge_log/resolution=挑战与裁定 · links.rests_on=前提链。
探索域归属: tags=["hypothesis-explore", "domain:<domain>"] + track={plan, hypothesis-<domain>}。
"""

from __future__ import annotations

import json
import logging
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent import (
    SingleToolRouter,
    ToolExecutionError,
)
from omnicompany.runtime.agent.agent_loop_tools import ToolContext

log = logging.getLogger(__name__)

EXPLORE_TAG = "hypothesis-explore"


def domain_tag(domain: str) -> str:
    return f"domain:{(domain or '').strip()}"


def _lib():
    from omnicompany.packages.domains.decisions import library
    return library


def load_domain_beliefs(domain: str) -> list[dict]:
    """确定性读取某探索域的全部 active belief(按 created_at 升序)。"""
    tag = domain_tag(domain)
    recs = [r for r in _lib().active_records()
            if r.get("kind") == "belief"
            and EXPLORE_TAG in (r.get("tags") or [])
            and tag in (r.get("tags") or [])]
    return sorted(recs, key=lambda r: r.get("created_at") or "")


def beliefs_snapshot(domain: str) -> dict:
    """给 agent 看的紧凑快照(id/陈述/状态/风险/验证入口/证据与挑战计数)。"""
    entries = []
    for r in load_domain_beliefs(domain):
        entries.append({
            "id": r.get("id"),
            "statement": r.get("statement", ""),
            "status": r.get("status", ""),
            "risk_if_wrong": r.get("risk_if_wrong", ""),
            "evidence_query": r.get("evidence_query", ""),
            "n_evidence": len(r.get("evidence") or []),
            "n_challenges": len(r.get("challenge_log") or []),
            "rests_on": (r.get("links") or {}).get("rests_on") or [],
        })
    return {"domain": domain, "total": len(entries), "beliefs": entries}


class ListBeliefsRouter(SingleToolRouter):
    """list_beliefs — 列出本探索域当前全部 belief。"""

    TOOL_NAME: ClassVar[str] = "list_beliefs"
    DESCRIPTION: ClassVar[str] = (
        "列出本探索域在统一决策库里的全部 belief(含 id/陈述/状态/风险/证据计数)。"
        "动手改库前先看当前态,避免重复立同义猜想。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {"domain": {"type": "string", "description": "探索域名(缺省=本 session 域)"}},
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        domain = (args.get("domain") or getattr(ctx, "explore_domain", "") or "").strip()
        if not domain:
            raise ToolExecutionError("domain 缺失且无 session 默认域")
        return json.dumps(beliefs_snapshot(domain), ensure_ascii=False, indent=2)


class RecordBeliefRouter(SingleToolRouter):
    """record_belief — 立一条新猜想(kind=belief)进统一决策库。"""

    TOOL_NAME: ClassVar[str] = "record_belief"
    DESCRIPTION: ClassVar[str] = (
        "把一条可证伪的猜想立进统一决策库(kind=belief,初始 status=untested)。"
        "必填 statement(一句话陈述)与 risk_if_wrong(low|medium|high);"
        "evidence_query 写清怎么验证;rests_on 传前提 belief id 列表(推导必须带出处)。"
        "返回新记录 id(BLF-...)。同义猜想别重复立,先 list_beliefs 查。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "statement": {"type": "string", "description": "猜想陈述(一句话,可证伪)"},
            "risk_if_wrong": {"type": "string", "enum": ["low", "medium", "high"]},
            "evidence_query": {"type": "string", "description": "怎么验证这个猜想"},
            "rationale": {"type": "string", "description": "为什么这么猜(观察依据)"},
            "rests_on": {"type": "array", "items": {"type": "string"},
                         "description": "前提 belief id 列表(库内真 id)"},
        },
        "required": ["statement", "risk_if_wrong"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        lib = _lib()
        statement = (args.get("statement") or "").strip()
        risk = (args.get("risk_if_wrong") or "").strip()
        if not statement:
            raise ToolExecutionError("statement 必填")
        if risk not in ("low", "medium", "high"):
            raise ToolExecutionError("risk_if_wrong 须为 low|medium|high")
        domain = (getattr(ctx, "explore_domain", "") or "").strip()
        if not domain:
            raise ToolExecutionError("session 未注入 explore_domain,无法归域")
        record: dict[str, Any] = {
            "kind": "belief",
            "statement": statement,
            "risk_if_wrong": risk,
            "nature": "factual",
            "tags": [EXPLORE_TAG, domain_tag(domain)],
            "track": {"kind": "plan", "id": f"hypothesis-{domain}"},
            "origin": {"channel": "claude", "author": "hypothesis.reflector",
                       "session_ref": getattr(ctx, "hyp_session_id", "") or ctx.trace_id},
            "created_by": "hypothesis.reflector",
        }
        for k in ("evidence_query", "rationale"):
            v = (args.get(k) or "").strip()
            if v:
                record[k] = v
        rests_on = [r for r in (args.get("rests_on") or []) if (r or "").strip()]
        if rests_on:
            missing = [r for r in rests_on if not lib.get(r)]
            if missing:
                raise ToolExecutionError(f"rests_on 里有库内不存在的 id: {missing}(推导必须指回真前提)")
            record["links"] = {"rests_on": rests_on}
        rec, _ = lib.upsert(record)
        return json.dumps({"id": rec["id"], "status": rec.get("status")}, ensure_ascii=False)


class ChallengeBeliefRouter(SingleToolRouter):
    """challenge_belief — 对某条 belief 记一笔挑战(反证优先)。"""

    TOOL_NAME: ClassVar[str] = "challenge_belief"
    DESCRIPTION: ClassVar[str] = (
        "对某条 belief 发起挑战:记 challenge_log 一笔并把状态置 challenged。"
        "看到与猜想相悖的观察就调它,不要静默忽略反例(反证优先通则)。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "belief id(BLF-...)"},
            "reason": {"type": "string", "description": "挑战理由(观察到什么反例)"},
            "source": {"type": "string", "description": "证据出处(命令/文件/输出摘要)"},
        },
        "required": ["id", "reason"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        try:
            rec = _lib().challenge(args.get("id", ""), args.get("reason", ""),
                                   source=args.get("source", ""),
                                   challenger="hypothesis.reflector")
        except ValueError as e:
            raise ToolExecutionError(str(e)) from None
        return json.dumps({"id": rec["id"], "status": rec.get("status"),
                           "n_challenges": len(rec.get("challenge_log") or [])}, ensure_ascii=False)


class ResolveBeliefRouter(SingleToolRouter):
    """resolve_belief — 裁定 belief 下场(supported/partial/falsified)。"""

    TOOL_NAME: ClassVar[str] = "resolve_belief"
    DESCRIPTION: ClassVar[str] = (
        "裁定一条 belief 的下场:supported(观察反复支持)/partial(部分成立)/falsified(被证伪)。"
        "必须带 evidence(依据什么观察)与 method(怎么验的)。"
        "falsified 时返回值会点名 rests_on 它的下游记录——逐条复核并对仍受影响的调 challenge_belief(回传必做通则)。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "outcome": {"type": "string", "enum": ["supported", "partial", "falsified"]},
            "evidence": {"type": "string", "description": "依据什么观察"},
            "method": {"type": "string", "description": "验证方法"},
        },
        "required": ["id", "outcome", "evidence"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        lib = _lib()
        try:
            rec = lib.resolve(args.get("id", ""), args.get("outcome", ""),
                              evidence=args.get("evidence", ""),
                              method=args.get("method", ""),
                              by="hypothesis.reflector")
        except ValueError as e:
            raise ToolExecutionError(str(e)) from None
        out: dict[str, Any] = {"id": rec["id"], "status": rec.get("status")}
        if args.get("outcome") == "falsified":
            out["downstream_rests_on_this"] = [
                {"id": d.get("id"), "statement": (d.get("statement") or "")[:80]}
                for d in lib.impacted_by(rec["id"])
            ]
        return json.dumps(out, ensure_ascii=False, indent=2)


class LinkBeliefRouter(SingleToolRouter):
    """link_belief — 给两条记录连边(rests_on/related/supersedes)。"""

    TOOL_NAME: ClassVar[str] = "link_belief"
    DESCRIPTION: ClassVar[str] = (
        "连边:src --rel--> dst。rel 用 rests_on(src 立足于 dst)/related(相关)/"
        "supersedes(src 取代 dst)。边动词正典=六词表,这三种 rel 分别映射 推导/联想/延伸。"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "src": {"type": "string"},
            "rel": {"type": "string", "enum": ["rests_on", "related", "supersedes"]},
            "dst": {"type": "string"},
        },
        "required": ["src", "rel", "dst"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        lib = _lib()
        dst = args.get("dst", "")
        if not lib.get(dst):
            raise ToolExecutionError(f"dst 记录不存在: {dst}")
        try:
            rec = lib.add_link(args.get("src", ""), args.get("rel", ""), dst)
        except ValueError as e:
            raise ToolExecutionError(str(e)) from None
        return json.dumps({"id": rec["id"], "links": rec.get("links")}, ensure_ascii=False)


BELIEF_TOOL_ROUTERS: list[type[SingleToolRouter]] = [
    ListBeliefsRouter,
    RecordBeliefRouter,
    ChallengeBeliefRouter,
    ResolveBeliefRouter,
    LinkBeliefRouter,
]
