# [OMNI] origin=claude-code domain=services/team_supervisor/team ts=2026-04-26T00:00:00Z type=team
# [OMNI] material_id="material:core.team_supervisor.team_topology.specification.py"
"""team_supervisor Team · 拓扑声明.

7 节点拓扑:
  TargetIngressWorker (entry · HARD)
    ├── ProductFormAnalyzerWorker (Q1 · AGENT)
    └── PurposeInterpreterWorker (Q2 · AGENT)
          ↓ (fan-in 2/2)
    HealthCriteriaDesignerWorker (Q3 · SOFT)
          ↓ (fan-in 3 上游)
    HypothesisGeneratorWorker (AGENT)
          ↓ (fan-in 4 上游)
    TestExecutorWorker (AGENT)
          ↓ (fan-in 全部)
    HealthReportAssemblerWorker (HARD · sink)
"""
from __future__ import annotations

from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    ValidatorKind,
    ValidatorSpec,
    VerdictKind,
)
from omnicompany.protocol.team import (
    NodeKind,
    NodeMaturity,
    TeamEdge,
    TeamNode,
    TeamSpec,
)


def _anchor(node_id, fmt_in, fmt_out, *, vkind, desc, routes, maturity=NodeMaturity.GROWING):
    return TeamNode(
        id=node_id,
        kind=NodeKind.ANCHOR,
        maturity=maturity,
        anchor=AnchorSpec(
            id=f"a_{node_id}",
            name=node_id,
            format_in=fmt_in,
            format_out=fmt_out,
            validator=ValidatorSpec(id=f"v_{node_id}", kind=vkind, description=desc),
            routes=routes,
        ),
    )


def build_team() -> TeamSpec:
    """构建 team_supervisor Team."""
    nodes = []

    nodes.append(_anchor(
        "TargetIngressWorker",
        "team_supervisor.target_spec",
        "team_supervisor.target_metadata",
        vkind=ValidatorKind.HARD,
        desc=(
            "HARD · 装入 target team 元数据. 校 target_team_id 在 PipelineRegistry 注册, "
            "解析 build_team() 抽 FORMAT_OUT/FORMAT_IN material id, 列出 workers/ 文件, "
            "验证 DESIGN.md/team.py 路径真实存在. 不调 LLM."
        ),
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
        },
    ))

    nodes.append(_anchor(
        "ProductFormAnalyzerWorker",
        "team_supervisor.target_metadata",
        "team_supervisor.product_form_brief",
        vkind=ValidatorKind.SOFT,
        desc=(
            "Q1 产物形式答案 · AGENT. 通过 ReadFile/Glob/Grep 探索 target FORMAT_OUT schema + "
            "末节点 worker 代码 + 历史 trace 产物, 用自然语言句子产 product_form_brief. "
            "禁分类标签 / 打分; 全字段句子. 末步必调 submit_product_form 工具."
        ),
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
        },
    ))

    nodes.append(_anchor(
        "PurposeInterpreterWorker",
        "team_supervisor.target_metadata",
        "team_supervisor.design_purpose_brief",
        vkind=ValidatorKind.SOFT,
        desc=(
            "Q2 设计目的答案 · AGENT. 通过 ReadFile/Glob/Grep 探索 DESIGN.md + team.py docstring + "
            "worker docstring + dispatch 调用方代码, 用自然语言句子产 design_purpose_brief. "
            "禁分类标签; 全字段句子. evidence_sources 至少 1 条引用. 末步必调 submit_design_purpose 工具."
        ),
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
        },
    ))

    nodes.append(_anchor(
        "HealthCriteriaDesignerWorker",
        ["team_supervisor.product_form_brief", "team_supervisor.design_purpose_brief"],
        "team_supervisor.health_criteria",
        vkind=ValidatorKind.SOFT,
        desc=(
            "Q3 健康判据 · SOFT. 综合 Q1+Q2 brief, 用 LLMClient 一次性产 health_criteria "
            "(key_observations/red_flags/oracle_strategies). 全字段句子; oracle 用语义描述 + "
            "implementation_hint 不用 metric=value. 末步必调 submit_health_criteria 工具."
        ),
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
        },
    ))

    nodes.append(_anchor(
        "HypothesisGeneratorWorker",
        [
            "team_supervisor.product_form_brief",
            "team_supervisor.design_purpose_brief",
            "team_supervisor.health_criteria",
        ],
        "team_supervisor.hypothesis_set",
        vkind=ValidatorKind.SOFT,
        desc=(
            "假设产生 · AGENT. 综合三问 brief + 实读 target 代码后, 产 ≥10 条 (条件→预期) 假设. "
            "每条带 condition/expectation/oracle_code_hint/rationale 全自然语言句子. id 唯一 H-NNN 格式. "
            "末步必调 submit_hypothesis_set 工具."
        ),
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
        },
    ))

    nodes.append(_anchor(
        "TestExecutorWorker",
        [
            "team_supervisor.hypothesis_set",
            "team_supervisor.target_metadata",
        ],
        "team_supervisor.test_results",
        vkind=ValidatorKind.SOFT,
        desc=(
            "测试执行 · 外置 AgentRuntime + Omni 控制面复合节点. 缺 sample_input 时 Cordis "
            "仅做只读推断; Omni 按 ingress 已校验的固定 target_team_id 真跑 TeamRunner; "
            "Cordis 再只读评估全部假设. AgentRuntime 不获得 Team dispatch 权限."
        ),
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.PARTIAL: Route(action=RouteAction.NEXT),
            # The composite may already have executed a side-effectful target.
            # Retrying the whole node would dispatch it twice; resume/retry must
            # therefore be an explicit Omni control-plane decision.
            VerdictKind.FAIL: Route(action=RouteAction.HALT),
        },
    ))

    nodes.append(_anchor(
        "HealthReportAssemblerWorker",
        [
            "team_supervisor.test_results",
            "team_supervisor.hypothesis_set",
            "team_supervisor.health_criteria",
            "team_supervisor.product_form_brief",
            "team_supervisor.design_purpose_brief",
            "team_supervisor.target_metadata",
        ],
        "team_supervisor.health_report",
        vkind=ValidatorKind.HARD,
        desc=(
            "装配 · HARD · 不调 LLM. 透传三问 brief, 算 verdict (passed/total ≥0.8 PASS · ≥0.5 PARTIAL · 否则 FAIL), "
            "拼 diagnosis 段落 (含具体失败假设引用). 装 ledger_increment 用于下次累积."
        ),
        routes={
            VerdictKind.PASS: Route(action=RouteAction.NEXT),
            VerdictKind.PARTIAL: Route(action=RouteAction.NEXT),
            VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=1),
        },
    ))

    edges = []

    # TargetIngressWorker → Q1 + Q2 (并行)
    edges.append(TeamEdge(
        source="TargetIngressWorker",
        target="ProductFormAnalyzerWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="TargetIngressWorker",
        target="PurposeInterpreterWorker",
        condition=VerdictKind.PASS,
    ))

    # Q1 + Q2 → Q3 (fan-in)
    edges.append(TeamEdge(
        source="ProductFormAnalyzerWorker",
        target="HealthCriteriaDesignerWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="PurposeInterpreterWorker",
        target="HealthCriteriaDesignerWorker",
        condition=VerdictKind.PASS,
    ))

    # Q1 + Q2 + Q3 → Hypothesis (fan-in)
    edges.append(TeamEdge(
        source="ProductFormAnalyzerWorker",
        target="HypothesisGeneratorWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="PurposeInterpreterWorker",
        target="HypothesisGeneratorWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="HealthCriteriaDesignerWorker",
        target="HypothesisGeneratorWorker",
        condition=VerdictKind.PASS,
    ))

    # Hypothesis + target_metadata + target_spec → Test (fan-in · target_spec 通过 ingress 透传)
    edges.append(TeamEdge(
        source="HypothesisGeneratorWorker",
        target="TestExecutorWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="TargetIngressWorker",
        target="TestExecutorWorker",
        condition=VerdictKind.PASS,
    ))

    # 全部上游 → Report (fan-in)
    edges.append(TeamEdge(
        source="TestExecutorWorker",
        target="HealthReportAssemblerWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="TestExecutorWorker",
        target="HealthReportAssemblerWorker",
        condition=VerdictKind.PARTIAL,
    ))
    edges.append(TeamEdge(
        source="HypothesisGeneratorWorker",
        target="HealthReportAssemblerWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="HealthCriteriaDesignerWorker",
        target="HealthReportAssemblerWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="ProductFormAnalyzerWorker",
        target="HealthReportAssemblerWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="PurposeInterpreterWorker",
        target="HealthReportAssemblerWorker",
        condition=VerdictKind.PASS,
    ))
    edges.append(TeamEdge(
        source="TargetIngressWorker",
        target="HealthReportAssemblerWorker",
        condition=VerdictKind.PASS,
    ))

    return TeamSpec(
        id="team_supervisor",
        name="team_supervisor",
        description="通用 team 健康监督 · 三问 + 假设进化 + 信号模式",
        entry="TargetIngressWorker",
        nodes=nodes,
        edges=edges,
        tags=["team_supervisor", "supervision", "health"],
    )
