# [OMNI] origin=claude-code domain=services/semantic_auditor ts=2026-04-18T00:00:00Z
# [OMNI] material_id="material:diagnosis.semantic_auditor.team_specification.python"
"""semantic_auditor.pipeline — 语义审计管线拓扑

  artifact_selector ──→ standard_matcher ──→ excerpt_retriever
                                                   ↓
                                              llm_auditor (async HARD + AuditAgent)
                                                   ↓
                                              finding_writer

五节点串行。B1（前三）确定性；B2（后两）LLM + 落盘。

遵循 CLAUDE.md Agent Node Loop 纯 Router 化铁律：
  - 每个节点都是 Router，不混入控制流
  - LLMAuditRouter 内部通过 AuditAgent（AgentNodeLoop 子类）完成单审；
    所有 LLM / tool / compact / prompt 事件由 AgentNodeLoop 自动 publish bus
  - Pipeline 当前不原生支持 fan-out，LLMAuditRouter 作为薄循环外壳
"""
from __future__ import annotations

from omnifactory.protocol.team import (
    TeamSpec, TeamNode, TeamEdge,
    NodeKind, NodeMaturity,
)
from omnifactory.protocol.anchor import (
    AnchorSpec, TransformerSpec, TransformMethod,
    ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)

DOMAIN = "semantic_auditor"


def build_team() -> TeamSpec:
    nodes = [
        TeamNode(
            id="artifact_selector",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-artifact-selector",
                name="ArtifactSelector",
                from_format=f"{DOMAIN}.artifact-request",
                to_format=f"{DOMAIN}.artifact-set",
                method=TransformMethod.RULE,
                description=(
                    "把输入（paths / git-diff / full-scan）转成 Artifact 清单，"
                    "每个 Artifact 按 standards-index.yaml.kind_inference 打 kind 标签"
                ),
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        TeamNode(
            id="standard_matcher",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-standard-matcher",
                name="StandardMatcher",
                from_format=f"{DOMAIN}.artifact-set",
                to_format=f"{DOMAIN}.audit-target-set",
                method=TransformMethod.RULE,
                description=(
                    "读 standards-index.yaml，为每个 Artifact 按 kind + path_match "
                    "匹配适用 standard id 列表"
                ),
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        TeamNode(
            id="excerpt_retriever",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-excerpt-retriever",
                name="ExcerptRetriever",
                from_format=f"{DOMAIN}.audit-target-set",
                to_format=f"{DOMAIN}.audit-excerpt-set",
                method=TransformMethod.RULE,
                description=(
                    "按 excerpt_strategy 取 standard 摘录（full / section 切块），"
                    "产出 (target, standard_id, excerpt_text) 三元组清单"
                ),
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── LLM 审计（async HARD 外壳，内部 AuditAgent 单审每条 excerpt） ──
        TeamNode(
            id="llm_auditor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-llm-auditor",
                name="LLMAuditor",
                format_in=f"{DOMAIN}.audit-excerpt-set",
                format_out=f"{DOMAIN}.finding-set",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-llm-auditor-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "LLM 审计：对每条 excerpt 启动一次 AuditAgent (AgentNodeLoop)，"
                        "读 artifact + 对照标准 → 产出 Finding 列表"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.FAIL: Route(action=RouteAction.EMIT),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── Finding 落盘（HARD，验证字段 + append REGISTRY + ARCH-CHANGES） ──
        TeamNode(
            id="finding_writer",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-finding-writer",
                name="FindingWriter",
                from_format=f"{DOMAIN}.finding-set",
                to_format=f"{DOMAIN}.finding-written",
                method=TransformMethod.RULE,
                description=(
                    "验证 Finding 字段（必填 + confidence 区间 + standard_id 合法），"
                    "append 到 docs/tech_debt/REGISTRY.md §语义合规待审 + "
                    "docs/ARCH-CHANGES.jsonl event=finding-generated"
                ),
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    edges = [
        TeamEdge(source="artifact_selector", target="standard_matcher"),
        TeamEdge(source="standard_matcher", target="excerpt_retriever"),
        TeamEdge(source="excerpt_retriever", target="llm_auditor"),
        TeamEdge(source="llm_auditor", target="finding_writer"),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-baseline",
        name="Semantic Auditor Baseline Pipeline",
        description=(
            "语义审计管线：收集 artifact → 匹配适用标准 → 取标准摘录 → "
            "LLM 单审 → Finding 落盘 REGISTRY"
        ),
        nodes=nodes,
        edges=edges,
        entry="artifact_selector",
        tags=["semantic_auditor", "audit", "compliance"],
    )
