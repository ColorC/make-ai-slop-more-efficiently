# [OMNI] origin=claude-code domain=services/domain_scout ts=2026-04-24T00:00:00Z
# [OMNI] material_id="material:diagnosis.domain_scout.team_spec_declaration.py"
"""domain_scout TeamSpec (bus-driven, MaterialDispatcher 激活).

edges 声明为 TeamSpec 文档, MaterialDispatcher 不消费 edges (纯 FORMAT_IN/OUT 订阅驱动).
"""
from omnifactory.protocol.anchor import (
    AnchorSpec, Route, RouteAction, ValidatorKind, ValidatorSpec, VerdictKind,
)
from omnifactory.protocol.team import NodeKind, NodeMaturity, TeamEdge, TeamNode, TeamSpec

DOMAIN = "domain_scout"


def _anchor(node_id: str, fmt_in: str, fmt_out: str, *, vkind: ValidatorKind, desc: str) -> TeamNode:
    return TeamNode(
        id=node_id,
        kind=NodeKind.ANCHOR,
        maturity=NodeMaturity.HYPOTHETICAL,
        anchor=AnchorSpec(
            id=f"a_{node_id}",
            name=node_id,
            format_in=fmt_in,
            format_out=fmt_out,
            validator=ValidatorSpec(id=f"v_{node_id}", kind=vkind, description=desc),
            routes={
                VerdictKind.PASS: Route(action=RouteAction.NEXT),
                VerdictKind.FAIL: Route(action=RouteAction.HALT),
            },
        ),
    )


def build_team() -> TeamSpec:
    """domain_scout team · 6 节点线性链 · D4 VerifiabilityCheck HARD 门."""
    nodes = [
        _anchor("source_fetcher", f"{DOMAIN}.scout_request", f"{DOMAIN}.fetch_batch",
                vkind=ValidatorKind.HARD, desc="抓取原文, 不做相关性判定 (D5)"),
        _anchor("dedup_filter", f"{DOMAIN}.fetch_batch", f"{DOMAIN}.dedup_candidates",
                vkind=ValidatorKind.HARD, desc="url+source_hash 指纹去重 (D2 唯一规则例外)"),
        _anchor("evidence_extractor", f"{DOMAIN}.dedup_candidates", f"{DOMAIN}.evidence_bundle",
                vkind=ValidatorKind.SOFT, desc="LLM 抽引用片段, 不截断原文 (L1 铁律 A)"),
        _anchor("llm_summarizer", f"{DOMAIN}.evidence_bundle", f"{DOMAIN}.raw_findings",
                vkind=ValidatorKind.SOFT, desc="LLM 写 finding 草稿"),
        _anchor("verifiability_check", f"{DOMAIN}.raw_findings", f"{DOMAIN}.verified_findings",
                vkind=ValidatorKind.HARD, desc="D4 硬门: source_url/quoted/source_hash 三项缺一 FAIL"),
        _anchor("digest_writer", f"{DOMAIN}.verified_findings", f"{DOMAIN}.digest",
                vkind=ValidatorKind.HARD, desc="写 digest.md + append index.jsonl (sink)"),
    ]
    edges = [
        TeamEdge(source="source_fetcher", target="dedup_filter", condition=VerdictKind.PASS),
        TeamEdge(source="dedup_filter", target="evidence_extractor", condition=VerdictKind.PASS),
        TeamEdge(source="evidence_extractor", target="llm_summarizer", condition=VerdictKind.PASS),
        TeamEdge(source="llm_summarizer", target="verifiability_check", condition=VerdictKind.PASS),
        TeamEdge(source="verifiability_check", target="digest_writer", condition=VerdictKind.PASS),
    ]
    return TeamSpec(
        id=f"{DOMAIN}-scout",
        name=f"{DOMAIN} scout pipeline",
        description="周期性外部调研: 抓取 → 去重 → 抽证 → 摘要 → 可验证性过滤 → 写 digest",
        entry="source_fetcher",
        nodes=nodes,
        edges=edges,
        tags=[DOMAIN, "scout", "external_research"],
    )
