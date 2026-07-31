# [OMNI] origin=claude-code domain=services/absorption/pipeline.py ts=2026-04-08T12:00:00Z
# [OMNI] material_id="material:learning.absorption.pipeline_topology_definitions.py"
"""absorption.pipeline — Repo Absorption 管线拓扑 (Stage 3d: 7 节点)。

Stage 3d 升级将原 4 节点扩展为 7 节点线性 DAG, 加入:
  - OmniCompany 自身能力快照 (L2)
  - AgentNodeLoop 迭代式 LandmarkPicker (L3)
  - 覆盖度审计 (L5)
  - Markdown 报告生成 (L6)

数据流:

  user_request
       │ target_intake (ANCHOR + HARD, RULE)
       ▼
  intake
       │ repo_facade_fetcher (ANCHOR + HARD)  ← L1 升级: 递归 tree + 全 README + 贡献者 + releases
       ▼
  facade_card
       │ omnicompany_snapshot_fetcher (ANCHOR + HARD)  ← L2 新增: 扫本仓能力
       ▼
  omnicompany_snapshot
       │ landmark_picker (ANCHOR + SOFT, AgentNodeLoop 50 turns)  ← L3 升级
       ▼
  landmark_list (含 landscape_sketches + capability_gaps + 探索轨迹)
       │ coverage_auditor (ANCHOR + HARD)  ← L5 新增
       ▼
  coverage_audit
       │ triage_gate (ANCHOR + HARD)
       ▼
  triaged_landmarks
       │ report_writer (TRANSFORMER + RULE)  ← L6 新增
       ▼
  report → EMIT
"""

from __future__ import annotations

from omnicompany.protocol.anchor import (
    AnchorSpec,
    Route,
    RouteAction,
    TransformMethod,
    TransformerSpec,
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

DOMAIN = "absorption"


def build_survey_pipeline() -> TeamSpec:
    """Stage 3d 7 节点线性 DAG。"""
    nodes = [
        # ── Node 01: target_intake ──
        TeamNode(
            id="target_intake",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-target-intake",
                name="TargetIntake",
                format_in=f"{DOMAIN}.user_request",
                format_out=f"{DOMAIN}.intake",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-intake-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "规整 user_request 为 intake: 解析 owner/name 短名和 URL、"
                        "校验 profile、分配全局唯一 absorption_id。"
                        "HARD: 无效输入立即 HALT, 不漂到下游。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        target="repo_facade_fetcher",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT,
                        feedback="user_request 不合法，请检查 repos 列表和 profile 枚举",
                    ),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── Node 02: repo_facade_fetcher (L1 升级) ──
        TeamNode(
            id="repo_facade_fetcher",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-repo-facade-fetch",
                name="RepoFacadeFetcher",
                format_in=f"{DOMAIN}.intake",
                format_out=f"{DOMAIN}.facade_card",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-facade-fetch-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "gh CLI 抓 GitHub 全量门面: 递归 tree + 全 README + "
                        "贡献者 + 近期 release + 语言/commit 频率。"
                        "HARD: 任一 repo 拉取失败即 HALT。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        target="omnicompany_snapshot_fetcher",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT,
                        feedback="GitHub API 拉取失败, 检查网络/token/repo 是否存在",
                    ),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── Node 03: omnicompany_snapshot_fetcher (L2 新增) ──
        TeamNode(
            id="omnicompany_snapshot_fetcher",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-omni-snapshot",
                name="OmnicompanySnapshotFetcher",
                format_in=f"{DOMAIN}.facade_card",
                format_out=f"{DOMAIN}.omnicompany_snapshot",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-omni-snapshot-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "扫 packages/core/runtime 生成 OmniCompany 当前能力快照 "
                        "(packages / registered_pipelines / routers / builtin_tools / core_modules)。"
                        "纯 FS 扫描, 无 LLM 无网络。供下游 LandmarkPicker 做对照判定, "
                        "避免凭想象判断 gap。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        target="landmark_picker",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT,
                        feedback="OmniCompany 快照生成失败",
                    ),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── Node 04: landmark_picker (L3 升级: AgentNodeLoop) ──
        TeamNode(
            id="landmark_picker",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-landmark-pick",
                name="LandmarkPicker",
                format_in=f"{DOMAIN}.omnicompany_snapshot",
                format_out=f"{DOMAIN}.landmark_list",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-landmark-pick-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "AgentNodeLoop (max 50 turns, readonly): 迭代 gh_tree_list/"
                        "gh_file_read 探索外部仓库真实源码 + omni_capabilities 查本仓对照。"
                        "产出 evidence-backed landmarks (每个 landmark 必须引用实际读过的 file_path + "
                        "snippet) + landscape_sketches + capability_gaps (每个 gap 必须引用 "
                        "OmniCompany 快照中的具体条目或 'no match found')。所有提交项带 "
                        "confidence(high/medium/low) + confidence_reason。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        target="coverage_auditor",
                    ),
                    VerdictKind.PARTIAL: Route(
                        action=RouteAction.NEXT,
                        target="coverage_auditor",
                        feedback="picker 结束但提交为空, 下游仍可审计覆盖度",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.RETRY,
                        max_retries=1,
                        feedback="picker 初始化失败, 重试一次",
                    ),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── Node 05: coverage_auditor (L5 新增) ──
        TeamNode(
            id="coverage_auditor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-coverage-audit",
                name="CoverageAuditor",
                format_in=f"{DOMAIN}.landmark_list",
                format_out=f"{DOMAIN}.coverage_audit",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-coverage-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "审计 LandmarkPicker 的探索覆盖度: 总 tree_recursive vs 实际读过的文件。"
                        "产出每个 repo 的 coverage_by_repo (total_files / files_read / "
                        "read_percent / top_dirs 表 / unscanned_top_dirs) + 全局 "
                        "overall_coverage_percent。供 ReportWriter 渲染 '诚实局限' 段。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        target="triage_gate",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT,
                        feedback="覆盖度审计失败",
                    ),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── Node 06: triage_gate ──
        TeamNode(
            id="triage_gate",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-triage-gate",
                name="TriageGate",
                format_in=f"{DOMAIN}.coverage_audit",
                format_out=f"{DOMAIN}.triaged_landmarks",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-triage-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "硬规则: 至少 1 个 tier-1 地标才 PASS, 否则 FAIL。"
                        "同时把全部 landmarks + sketches + gaps + coverage_audit + "
                        "picker 探索轨迹一并落盘到 data/absorption/landmark_pool/<absorption_id>.json "
                        "留档。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        target="report_writer",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT,
                        feedback="无 tier-1 地标, 建议跳过吸纳 (pool 已留档, 可复查)",
                    ),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── Node 07: report_writer (L6 新增) ──
        TeamNode(
            id="report_writer",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-report-write",
                name="ReportWriter",
                from_format=f"{DOMAIN}.triaged_landmarks",
                to_format=f"{DOMAIN}.report",
                method=TransformMethod.RULE,
                description=(
                    "从全 state 生成 human-readable markdown 报告到 "
                    "data/absorption/reports/<absorption_id>.md。含 TL;DR / 每 repo "
                    "Landscape / tier-1 详情 + 证据 snippet / tier-2/3 表 / gap 分析 / "
                    "覆盖审计表 / 全局诚实局限。"
                ),
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),
    ]

    edges = [
        TeamEdge(source="target_intake", target="repo_facade_fetcher",
                     condition=VerdictKind.PASS, label="规整后的 intake"),
        TeamEdge(source="repo_facade_fetcher", target="omnicompany_snapshot_fetcher",
                     condition=VerdictKind.PASS, label="facade card 抓取完成"),
        TeamEdge(source="omnicompany_snapshot_fetcher", target="landmark_picker",
                     condition=VerdictKind.PASS, label="本仓对照集就位"),
        TeamEdge(source="landmark_picker", target="coverage_auditor",
                     condition=VerdictKind.PASS, label="地标+sketch+gap 产出"),
        TeamEdge(source="coverage_auditor", target="triage_gate",
                     condition=VerdictKind.PASS, label="覆盖审计完成"),
        TeamEdge(source="triage_gate", target="report_writer",
                     condition=VerdictKind.PASS, label="tier-1 放行 + pool 落盘"),
    ]

    return TeamSpec(
        id=f"{DOMAIN}.survey",
        name="Repo Absorption · Stage 1 Survey & Triage (Stage 3d 7-node)",
        description=(
            "Repo Absorption 管线的 Phase A (Survey) 7 节点线性 DAG。"
            "Stage 3d 升级加入 OmniCompany 对照 / AgentNodeLoop 迭代读码 / "
            "覆盖审计 / markdown 报告, 产出 evidence-backed 的可复查绝对级情报。"
            "产物: data/absorption/landmark_pool/<id>.json + "
            "data/absorption/reports/<id>.md。"
        ),
        nodes=nodes,
        edges=edges,
        entry="target_intake",
        tags=["domain.absorption", "phase.a_survey", "stage.1", "stage_3d_upgraded"],
    )


PIPELINES = {
    "absorption.survey": build_survey_pipeline,
}


# ═══════════════════════════════════════════════════════════
# V2 — 问题驱动的定向深读管线骨架 (2026-04-13)
# 文档: docs/plans/[2026-04-13]REPO-ABSORPTION-V2/plan.md
# Phase 1: 线性骨架（无 SCATTER，所有节点 GROWING），可跑 happy path
# Phase 3: 加入 QuestionFanout SCATTER + DirectedReader 并行
# ═══════════════════════════════════════════════════════════

V2_DOMAIN = "absorption"


def build_v2_pipeline() -> TeamSpec:
    """V2 问题驱动深读管线，Phase 1 线性骨架（8 节点）。

    Phase 1 为简化线性流（不含 SCATTER 并行，DirectedReader 串行处理整体问题列表）。
    所有节点 GROWING，实现为 stub，仅用于跑通 happy path 和拓扑验证。
    Phase 3 升级时将 question_fanout 改为 SCATTER 节点 + DirectedReader 子管线。
    """
    nodes = [
        # ── Node 01: recon_scout (AgentNodeLoop, Phase 2 实现) ──
        TeamNode(
            id="recon_scout",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{V2_DOMAIN}-v2-recon-scout",
                name="ReconScoutV2",
                format_in=f"{V2_DOMAIN}.request",
                format_out=f"{V2_DOMAIN}.recon.map",
                validator=ValidatorSpec(
                    id=f"{V2_DOMAIN}-v2-recon-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "AgentNodeLoop: 固定读取顺序（README→目录树→入口→架构文档→核心抽象），"
                        "≤30 个文件，产出粗粒度能力图谱。Phase 1 为 STUB（直接 PASS）。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="intersection_planner"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="侦察失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 02: intersection_planner (LLM, Phase 2 实现) ──
        TeamNode(
            id="intersection_planner",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{V2_DOMAIN}-v2-intersection",
                name="IntersectionPlannerV2",
                format_in=f"{V2_DOMAIN}.recon.map",
                format_out=f"{V2_DOMAIN}.question-list",
                validator=ValidatorSpec(
                    id=f"{V2_DOMAIN}-v2-intersection-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "LLM: 对比自画像缺口（G1-G7）vs 侦察图谱，输出优先化问题清单。"
                        "每条问题有 gap_id / 优先级 / 预期位置 / 跳过理由。最多 20 个问题。"
                        "Phase 1 为 STUB（生成 1 个示例问题）。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT, target="human_approval_gate",
                    ),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="问题清单生成失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 03: human_approval_gate (RULE passthrough, 人工门) ──
        TeamNode(
            id="human_approval_gate",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{V2_DOMAIN}-v2-human-gate",
                name="HumanApprovalGateV2",
                format_in=f"{V2_DOMAIN}.question-list",
                format_out=f"{V2_DOMAIN}.question-list.approved",
                validator=ValidatorSpec(
                    id=f"{V2_DOMAIN}-v2-human-gate-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "人工门：将 question-list 写出到 data/absorption/<repo>/pending_questions.md，"
                        "等待人工编辑后读回（或在 auto 模式下直接 passthrough）。"
                        "Phase 1 为 auto passthrough。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT, target="directed_reader",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT, feedback="人工审核被拒绝",
                    ),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 04: directed_reader (Phase 1 串行 stub, Phase 3 换 SCATTER) ──
        TeamNode(
            id="directed_reader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{V2_DOMAIN}-v2-directed-reader",
                name="DirectedReaderV2",
                format_in=f"{V2_DOMAIN}.question-list.approved",
                format_out=f"{V2_DOMAIN}.question.answer",
                validator=ValidatorSpec(
                    id=f"{V2_DOMAIN}-v2-reader-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "Phase 1: 串行处理所有问题（相当于 QuestionFanout + DirectedReader 的合并 stub）。"
                        "对每个问题走 PENDING→SEARCHING→READING→ANSWERING 状态机，"
                        "内置终止判据（≤15文件/8000token/问题已回答）。"
                        "Phase 3 将拆分为 QuestionFanout(SCATTER) + DirectedReader(sub_pipeline)。"
                        "Phase 1 为 STUB（对每个问题生成 not_found 答案）。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT, target="coverage_auditor",
                    ),
                    VerdictKind.PARTIAL: Route(
                        action=RouteAction.NEXT, target="coverage_auditor",
                        feedback="部分问题未回答，继续审计",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT, feedback="深读完全失败",
                    ),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 05: coverage_auditor (RULE, Phase 3 实现) ──
        TeamNode(
            id="coverage_auditor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{V2_DOMAIN}-v2-coverage-audit",
                name="CoverageAuditorV2",
                format_in=f"{V2_DOMAIN}.question.answer",
                format_out=f"{V2_DOMAIN}.audit",
                validator=ValidatorSpec(
                    id=f"{V2_DOMAIN}-v2-coverage-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "RULE: 检查 recon.map 中的关键模块是否都被问题覆盖到，"
                        "汇总 answered/partial/not_found/skipped 分布，计算 coverage_score。"
                        "Phase 1 为 STUB（coverage_score=0, 直接 PASS）。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT, target="synthesis",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT, feedback="覆盖审计失败",
                    ),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 06: synthesis (LLM, Phase 4 实现) ──
        TeamNode(
            id="synthesis",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{V2_DOMAIN}-v2-synthesis",
                name="SynthesisV2",
                format_in=f"{V2_DOMAIN}.audit",
                format_out=f"{V2_DOMAIN}.synthesis",
                validator=ValidatorSpec(
                    id=f"{V2_DOMAIN}-v2-synthesis-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "LLM: 聚合所有 question.answer + audit，产出 4 份产物："
                        "架构图 / 亮点清单 / 各部分思路 / OmniCompany 对照。"
                        "Phase 1 为 STUB（空亮点清单）。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT, target="report_writer_v2",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.HALT, feedback="综合分析失败",
                    ),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 07: report_writer_v2 (RULE, Phase 4 实现) ──
        TeamNode(
            id="report_writer_v2",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{V2_DOMAIN}-v2-report-write",
                name="ReportWriterV2",
                from_format=f"{V2_DOMAIN}.synthesis",
                to_format=f"{V2_DOMAIN}.report.v2",
                method=TransformMethod.RULE,
                description=(
                    "写入 data/absorption/<repo>/<date>/report.md + "
                    "更新 data/absorption/<repo>/.omni/manifest.yaml。"
                    "Phase 1 为 STUB（写空 report.md）。"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]

    edges = [
        TeamEdge(source="recon_scout", target="intersection_planner",
                     condition=VerdictKind.PASS, label="侦察图谱就位"),
        TeamEdge(source="intersection_planner", target="human_approval_gate",
                     condition=VerdictKind.PASS, label="问题清单生成"),
        TeamEdge(source="human_approval_gate", target="directed_reader",
                     condition=VerdictKind.PASS, label="问题清单审核通过"),
        TeamEdge(source="directed_reader", target="coverage_auditor",
                     condition=VerdictKind.PASS, label="所有问题答案就位"),
        TeamEdge(source="directed_reader", target="coverage_auditor",
                     condition=VerdictKind.PARTIAL, label="部分问题答案"),
        TeamEdge(source="coverage_auditor", target="synthesis",
                     condition=VerdictKind.PASS, label="覆盖审计完成"),
        TeamEdge(source="synthesis", target="report_writer_v2",
                     condition=VerdictKind.PASS, label="综合分析完成"),
    ]

    return TeamSpec(
        id=f"{V2_DOMAIN}.v2",
        name="Repo Absorption V2 · 问题驱动定向深读管线（Phase 1 骨架）",
        description=(
            "V2 管线：问题驱动的定向深读。以自画像缺口 G1-G7 为问题来源，"
            "侦察段产出能力图谱，定向深读段带着具体问题递归读代码，终止条件是'问题被回答'。"
            "Phase 1 为线性骨架（无 SCATTER），所有节点 GROWING stub。"
            "Phase 3 将 directed_reader 替换为 QuestionFanout(SCATTER) + DirectedReader(sub_pipeline)。"
            "产物: data/absorption/<repo>/<date>/report.md。"
        ),
        nodes=nodes,
        edges=edges,
        entry="recon_scout",
        tags=["domain.absorption", "phase.v2", "stage.1_skeleton"],
    )


def build_v3_pipeline() -> TeamSpec:
    """V3 模块驱动吸纳管线（Phase A 骨架）。

    四层 Format 架构：repomap → important-modules → module.code → learning
    设计文档：docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md

    Phase A（当前）：RepoMapper 实化，其余节点为 STUB。
    Phase B：ModulePickerRouter（LLM 语义选模块）。
    Phase C：ModuleReaderRouter + LearningExtractorRouter + ReportWriterV3Router。

    2026-04-18 升级：repo_mapper 后加 wiki 三路 fan-in（capability / gap / reception），
    module_explorer FORMAT_IN 升为 composite `absorption.module_exploration.context`。
    """
    nodes = [
        TeamNode(
            id="repo_mapper",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-module-repo-mapper",
                name="RepoMapperV3",
                format_in="absorption.request",
                format_out="absorption.repomap",
                validator=ValidatorSpec(
                    id="absorption-module-repo-mapper-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "纯计算：扫描全 repo，按行数×关键词分数排序，"
                        "产出 coarse_view（全量粗粒度）+ detail_views（按需细粒度）。"
                        "解决 V2 Scout 漏读正交基础设施的根因问题。"
                    ),
                ),
                # 4 路 fan-out：PASS 不指定 target，由 edges 驱动所有下游
                # (module_explorer + 3 个 query_builder)
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="RepoMapper 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        # ── wiki 知识链 · capability ───────────────────────────────────
        TeamNode(
            id="capability_query_builder",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-v3-capability-query-builder",
                name="CapabilityInventoryQueryBuilderV3",
                format_in="absorption.repomap",
                format_out="omni.self.capability_inventory_query",
                validator=ValidatorSpec(
                    id="absorption-v3-capability-query-builder-v",
                    kind=ValidatorKind.HARD,
                    description="RULE：从 repo_name 派生 capability 查询（默认全取 active+design）。",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="capability_loader"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="capability_query_builder 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="capability_loader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-v3-capability-loader",
                name="CapabilityInventoryLoaderV3",
                format_in="omni.self.capability_inventory_query",
                format_out="omni.self.capability_inventory",
                validator=ValidatorSpec(
                    id="absorption-v3-capability-loader-v",
                    kind=ValidatorKind.HARD,
                    description="扫 src/omnicompany/**/DESIGN.md，产出 capability_inventory。",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="module_explorer"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="capability_loader 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        # ── wiki 知识链 · gap ──────────────────────────────────────────
        TeamNode(
            id="gap_query_builder",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-v3-gap-query-builder",
                name="GapRegistryQueryBuilderV3",
                format_in="absorption.repomap",
                format_out="omni.self.gap_registry_query",
                validator=ValidatorSpec(
                    id="absorption-v3-gap-query-builder-v",
                    kind=ValidatorKind.HARD,
                    description="RULE：从 repo_name 派生 gap 查询（默认全取 P0+P1+P2）。",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="gap_loader"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="gap_query_builder 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="gap_loader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-v3-gap-loader",
                name="GapRegistryLoaderV3",
                format_in="omni.self.gap_registry_query",
                format_out="omni.self.gap_registry",
                validator=ValidatorSpec(
                    id="absorption-v3-gap-loader-v",
                    kind=ValidatorKind.HARD,
                    description="扫 docs/gaps/G*.md，产出 gap_registry。",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="module_explorer"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="gap_loader 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        # ── wiki 知识链 · reception ────────────────────────────────────
        TeamNode(
            id="reception_query_builder",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-v3-reception-query-builder",
                name="ReceptionIntentsQueryBuilderV3",
                format_in="absorption.repomap",
                format_out="omni.self.reception_intent_query",
                validator=ValidatorSpec(
                    id="absorption-v3-reception-query-builder-v",
                    kind=ValidatorKind.HARD,
                    description="RULE：从 repo_name 派生 reception 查询（默认全取基础设施模块）。",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="reception_loader"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="reception_query_builder 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="reception_loader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-v3-reception-loader",
                name="ReceptionIntentsLoaderV3",
                format_in="omni.self.reception_intent_query",
                format_out="omni.self.reception_intents",
                validator=ValidatorSpec(
                    id="absorption-v3-reception-loader-v",
                    kind=ValidatorKind.HARD,
                    description="扫基础设施模块 DESIGN.md 第 8 节，产出 reception_intents。",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="module_explorer"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="reception_loader 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="module_explorer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-module-module-explorer",
                name="ModuleExplorerV3",
                format_in="absorption.module_exploration.context",
                format_out="absorption.module.code",
                validator=ValidatorSpec(
                    id="absorption-module-module-explorer-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "AgentNodeLoop：composite FORMAT_IN 4 路 fan-in"
                        "（repomap + capability_inventory + gap_registry + reception_intents）。"
                        "四元判断：已有可改进 / 已知缺口 / 愿接收新主题 / 架构冲突 (+unforeseen 兜底)。"
                        "local_grep 主动发现 + local_read 确认 + submit_module 提交。"
                        "≤25 文件预算，无模块数量约束。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="learning_extractor"),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="learning_extractor"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="ModuleExplorer 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="learning_extractor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-module-learning-extractor",
                name="LearningExtractorV3",
                format_in="absorption.module.code",
                format_out="absorption.learning",
                validator=ValidatorSpec(
                    id="absorption-module-learning-extractor-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "LLM 判断：看模块代码，提取可操作发现，"
                        "绑定 G1-G7，含 portability 评级。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="report_writer"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="LearningExtractor 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 4: report_writer_v3（LLM 综合报告）──
        TeamNode(
            id="report_writer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-module-report-writer",
                name="ReportWriterV3",
                format_in="absorption.learning",
                format_out="absorption.report.v3",
                validator=ValidatorSpec(
                    id="absorption-module-report-writer-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "LLM 单次调用：把 learning findings 渲染成综合 Markdown 报告，"
                        "写盘到 data/absorption/<repo>/report.md，"
                        "返回 report_md + structured 结构化摘要 + iteration。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="human_feedback_gate"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="ReportWriterV3 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 5: human_feedback_gate（RULE：读 feedback.md）──
        TeamNode(
            id="human_feedback_gate",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-module-human-feedback-gate",
                name="HumanFeedbackGateV3",
                format_in="absorption.report.v3",
                format_out="absorption.feedback",
                validator=ValidatorSpec(
                    id="absorption-module-human-feedback-gate-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "RULE：检查 data/absorption/<repo>/feedback.md 是否存在。"
                        "若存在：读取原文，解析方向，重命名为 feedback_<iteration>.md.done。"
                        "若不存在：auto-pass（directions=[]）。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="feedback_router"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="HumanFeedbackGate 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 6: feedback_router（RULE：EMIT 或 JUMP 到补充探索路径）──
        TeamNode(
            id="feedback_router",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-module-feedback-router",
                name="FeedbackRouterV3",
                format_in="absorption.feedback",
                format_out="absorption.supplement_request",
                validator=ValidatorSpec(
                    id="absorption-module-feedback-router-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "RULE：判断是否需要补充学习。"
                        "directions=[] → PASS → EMIT（最终报告锁定）。"
                        "directions 非空 → PARTIAL → JUMP 至 supplement_explorer（独立补充路径）。"
                        "JUMP 时携带 supplement_guidance + previous_findings + context，iteration+1。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.PARTIAL: Route(
                        action=RouteAction.JUMP,
                        target="supplement_explorer",
                        feedback="补充学习：JUMP 至 supplement_explorer",
                    ),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="FeedbackRouter 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ══ 补充探索路径（与主路径分离）══════════════════════════════════════

        # ── Node 7: supplement_explorer（复用 ModuleExplorer 逻辑，补充探索语境）──
        TeamNode(
            id="supplement_explorer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-module-supplement-explorer",
                name="SupplementExplorerV3",
                format_in="absorption.supplement_request",
                format_out="absorption.module.code",
                validator=ValidatorSpec(
                    id="absorption-module-supplement-explorer-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "补充探索（复用 ModuleExplorerRouter）：基于 supplement_guidance 定向读取，"
                        "用户消息中含已有发现列表和上一轮已读文件，引导 LLM 专注新方向。"
                        "与主路径 module_explorer 节点隔离，不污染初次探索的路由。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="supplement_extractor"),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="supplement_extractor"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="SupplementExplorer 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 8: supplement_extractor（复用 LearningExtractor 逻辑）──
        TeamNode(
            id="supplement_extractor",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-module-supplement-extractor",
                name="SupplementExtractorV3",
                format_in="absorption.module.code",
                format_out="absorption.learning",
                validator=ValidatorSpec(
                    id="absorption-module-supplement-extractor-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "补充提炼（复用 LearningExtractorRouter）：从补充探索模块中提炼新发现，"
                        "与主路径 learning_extractor 节点隔离。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="report_updater"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="SupplementExtractor 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── Node 9: report_updater（LLM 增量融合，与 report_writer_v3 严格分离）──
        TeamNode(
            id="report_updater",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-module-report-updater",
                name="ReportUpdaterV3",
                format_in="absorption.learning",
                format_out="absorption.report.v3",
                validator=ValidatorSpec(
                    id="absorption-module-report-updater-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "LLM 增量融合：将补充发现融入已有报告，覆盖写 report.md。"
                        "与 report_writer_v3（起草）严格分离，只做增量追加与融合，不重写。"
                        "输出合并后的全量 findings 供下游 human_feedback_gate 判断。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="human_feedback_gate"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="ReportUpdaterV3 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]

    edges = [
        # 主路径：repo_mapper 4 路 fan-out → module_explorer 4 路 fan-in
        TeamEdge(source="repo_mapper", target="module_explorer",
                     condition=VerdictKind.PASS, label="外部仓库 repomap (fan-in 1/4)"),
        TeamEdge(source="repo_mapper", target="capability_query_builder",
                     condition=VerdictKind.PASS, label="capability 查询启动"),
        TeamEdge(source="repo_mapper", target="gap_query_builder",
                     condition=VerdictKind.PASS, label="gap 查询启动"),
        TeamEdge(source="repo_mapper", target="reception_query_builder",
                     condition=VerdictKind.PASS, label="reception 查询启动"),
        # wiki 三链 → module_explorer 的剩余 3 路 fan-in
        TeamEdge(source="capability_query_builder", target="capability_loader",
                     condition=VerdictKind.PASS, label="capability 查询就绪"),
        TeamEdge(source="capability_loader", target="module_explorer",
                     condition=VerdictKind.PASS, label="capability_inventory (fan-in 2/4)"),
        TeamEdge(source="gap_query_builder", target="gap_loader",
                     condition=VerdictKind.PASS, label="gap 查询就绪"),
        TeamEdge(source="gap_loader", target="module_explorer",
                     condition=VerdictKind.PASS, label="gap_registry (fan-in 3/4)"),
        TeamEdge(source="reception_query_builder", target="reception_loader",
                     condition=VerdictKind.PASS, label="reception 查询就绪"),
        TeamEdge(source="reception_loader", target="module_explorer",
                     condition=VerdictKind.PASS, label="reception_intents (fan-in 4/4)"),
        # module_explorer 之后的主线
        TeamEdge(source="module_explorer", target="learning_extractor",
                     condition=VerdictKind.PASS, label="模块已读取并提交"),
        TeamEdge(source="module_explorer", target="learning_extractor",
                     condition=VerdictKind.PARTIAL, label="部分模块已提交"),
        TeamEdge(source="learning_extractor", target="report_writer",
                     condition=VerdictKind.PASS, label="学习发现提炼完成"),
        TeamEdge(source="report_writer", target="human_feedback_gate",
                     condition=VerdictKind.PASS, label="初稿报告写出"),
        TeamEdge(source="human_feedback_gate", target="feedback_router",
                     condition=VerdictKind.PASS, label="反馈读取完成"),
        # 补充路径（独立循环）
        TeamEdge(source="feedback_router", target="supplement_explorer",
                     condition=VerdictKind.PARTIAL, label="JUMP 至补充探索"),
        TeamEdge(source="supplement_explorer", target="supplement_extractor",
                     condition=VerdictKind.PASS, label="补充模块已读取"),
        TeamEdge(source="supplement_explorer", target="supplement_extractor",
                     condition=VerdictKind.PARTIAL, label="补充模块部分读取"),
        TeamEdge(source="supplement_extractor", target="report_updater",
                     condition=VerdictKind.PASS, label="补充发现提炼完成"),
        TeamEdge(source="report_updater", target="human_feedback_gate",
                     condition=VerdictKind.PASS, label="报告增量更新完成"),
    ]

    return TeamSpec(
        id="absorption.v3",
        name="Repo Absorption V3 · 模块驱动管线（主路径 + 补充反馈循环）",
        description=(
            "V3 管线：主路径 repo_mapper→module_explorer→learning_extractor→report_writer_v3→feedback_gate。"
            "补充路径（有 feedback 时）：feedback_router JUMP → supplement_explorer → supplement_extractor → report_updater → feedback_gate。"
            "report_writer_v3（起草）与 report_updater（增量融合）严格分离，各司其职。"
            "设计文档：docs/plans/[2026-04-13]REPO-ABSORPTION-V3/DESIGN.md。"
        ),
        nodes=nodes,
        edges=edges,
        entry="repo_mapper",
        tags=["domain.absorption", "phase.v3"],
    )


def build_v3_stage3_pipeline() -> TeamSpec:
    """V3 Stage 3 工作流修改管线（Phase 1 + 2026-04-18 知识 fan-in）。

    拓扑（7 节点 + 7 边）:

        entry_bootstrap (identity)
            ├─→ spec_parser  (fan-in 1/3)
            ├─→ capability_query_builder → capability_loader → spec_parser (fan-in 2/3)
            └─→ gap_query_builder        → gap_loader        → spec_parser (fan-in 3/3)

        spec_parser (FORMAT_IN=absorption.proposal.context composite)
            └─→ human_approval_gate_s3
                    └─→ EMIT

    SpecParser 消费 composite Format，3 路 fan-in 通过 components 的 format_id 作 key 区分。
    本设计是 P-13 / F-15 规范的活样本（声明即消费）。

    设计文档：docs/plans/[2026-04-14]STAGE3-WORKFLOW-MODIFIER/plan.md + 2026-04-18 知识 fan-in 改造
    """
    nodes = [
        # ── 入口分发器（identity fan-out 3 路）─────────────────────────
        TeamNode(
            id="entry_bootstrap",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="absorption-workflow-modifier-entry-bootstrap",
                name="Stage3EntryBootstrap",
                method=TransformMethod.RULE,
                from_format="absorption.report.v3",
                to_format="absorption.report.v3",
                description=(
                    "identity 传递 absorption.report.v3，fan-out 3 路："
                    "spec_parser / capability_query_builder / gap_query_builder。"
                ),
            ),
            maturity=NodeMaturity.MATURE,
        ),
        # ── Knowledge 加载链（query builder → loader）────────────────────
        TeamNode(
            id="capability_query_builder",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="absorption-workflow-modifier-capability-query",
                name="CapabilityInventoryQueryBuilder",
                method=TransformMethod.RULE,
                from_format="absorption.report.v3",
                to_format="omni.self.capability_inventory_query",
                description="从 report.v3.repo_name 派生默认 capability_inventory_query。",
            ),
            maturity=NodeMaturity.MATURE,
        ),
        TeamNode(
            id="capability_loader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-workflow-modifier-capability-loader",
                name="CapabilityInventoryLoader",
                format_in="omni.self.capability_inventory_query",
                format_out="omni.self.capability_inventory",
                validator=ValidatorSpec(
                    id="absorption-workflow-modifier-capability-loader-v",
                    kind=ValidatorKind.HARD,
                    description="扫 src/omnicompany/**/DESIGN.md 产出能力清单快照。",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="spec_parser"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="capability_loader 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="gap_query_builder",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id="absorption-workflow-modifier-gap-query",
                name="GapRegistryQueryBuilder",
                method=TransformMethod.RULE,
                from_format="absorption.report.v3",
                to_format="omni.self.gap_registry_query",
                description="从 report.v3.repo_name 派生默认 gap_registry_query。",
            ),
            maturity=NodeMaturity.MATURE,
        ),
        TeamNode(
            id="gap_loader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-workflow-modifier-gap-loader",
                name="GapRegistryLoader",
                format_in="omni.self.gap_registry_query",
                format_out="omni.self.gap_registry",
                validator=ValidatorSpec(
                    id="absorption-workflow-modifier-gap-loader-v",
                    kind=ValidatorKind.HARD,
                    description="扫 docs/gaps/G*.md 产出缺口档案快照。",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="spec_parser"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="gap_loader 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        # ── SpecParser（3 路 fan-in composite）─────────────────────────
        TeamNode(
            id="spec_parser",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-workflow-modifier-spec-parser",
                name="SpecParserS3",
                format_in="absorption.proposal.context",
                format_out="absorption.proposal.list",
                validator=ValidatorSpec(
                    id="absorption-workflow-modifier-spec-parser-v",
                    kind=ValidatorKind.SOFT,
                    description=(
                        "RULE+LLM：3 路 fan-in（report.v3 + capability_inventory + gap_registry）"
                        "→ 提案列表。wiki 上下文结构化注入，不再靠硬编码字符串。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="proposal_feedback_gate"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="SpecParser 无法提取提案"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        # ── Feedback 回路（2026-04-18 新增，复用 Stage 2 模式）──────────
        TeamNode(
            id="proposal_feedback_gate",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-workflow-modifier-proposal-feedback-gate",
                name="ProposalFeedbackGate",
                format_in="absorption.proposal.list",
                format_out="absorption.proposal.feedback",
                validator=ValidatorSpec(
                    id="absorption-workflow-modifier-proposal-feedback-gate-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "RULE：读 data/domains/absorption/<repo>/proposal_feedback.md，"
                        "完整解析（铁律 A 不截断），重命名 .done；"
                        "无文件则 auto-pass（directions=[]）。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="proposal_feedback_router"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="ProposalFeedbackGate 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="proposal_feedback_router",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-workflow-modifier-proposal-feedback-router",
                name="ProposalFeedbackRouter",
                format_in="absorption.proposal.feedback",
                format_out="absorption.proposal.list",
                validator=ValidatorSpec(
                    id="absorption-workflow-modifier-proposal-feedback-router-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "RULE：has_feedback=False → PASS 给 approval_gate；"
                        "has_feedback=True → PARTIAL + JUMP 回 spec_parser（带 supplement_guidance）。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="human_approval_gate_s3"),
                    VerdictKind.PARTIAL: Route(
                        action=RouteAction.JUMP,
                        target="spec_parser",
                        feedback="提案补充综合：JUMP 回 spec_parser",
                    ),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="ProposalFeedbackRouter 失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
        TeamNode(
            id="human_approval_gate_s3",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id="absorption-workflow-modifier-human-approval",
                name="HumanApprovalGateS3",
                format_in="absorption.proposal.list",
                format_out="absorption.proposal.approved",
                validator=ValidatorSpec(
                    id="absorption-workflow-modifier-human-approval-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "RULE：检查 approved_proposals.txt。"
                        "risk=low 自动通过；risk≥medium 等待人工写入文件。"
                        "PASS=有审批；PARTIAL=有 pending；FAIL=inputs 为空。"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.PARTIAL: Route(
                        action=RouteAction.EMIT,
                        feedback="部分提案待人工审批，已批准部分可继续",
                    ),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT, feedback="审批门失败"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]

    edges = [
        # entry_bootstrap fan-out 3 路
        TeamEdge(source="entry_bootstrap", target="spec_parser",
                     condition=VerdictKind.PASS, label="report.v3 直达（fan-in 1/3）"),
        TeamEdge(source="entry_bootstrap", target="capability_query_builder",
                     condition=VerdictKind.PASS, label="启动 capability loader 链"),
        TeamEdge(source="entry_bootstrap", target="gap_query_builder",
                     condition=VerdictKind.PASS, label="启动 gap loader 链"),
        # Loader 链
        TeamEdge(source="capability_query_builder", target="capability_loader",
                     condition=VerdictKind.PASS, label="query 就绪"),
        TeamEdge(source="capability_loader", target="spec_parser",
                     condition=VerdictKind.PASS, label="capability_inventory（fan-in 2/3）"),
        TeamEdge(source="gap_query_builder", target="gap_loader",
                     condition=VerdictKind.PASS, label="query 就绪"),
        TeamEdge(source="gap_loader", target="spec_parser",
                     condition=VerdictKind.PASS, label="gap_registry（fan-in 3/3）"),
        # 主链继续 → feedback 门 → feedback 路由 → approval
        TeamEdge(source="spec_parser", target="proposal_feedback_gate",
                     condition=VerdictKind.PASS, label="提案解析完成，检查 feedback"),
        TeamEdge(source="proposal_feedback_gate", target="proposal_feedback_router",
                     condition=VerdictKind.PASS, label="feedback 已解析"),
        TeamEdge(source="proposal_feedback_router", target="human_approval_gate_s3",
                     condition=VerdictKind.PASS, label="无 feedback → 审批"),
        # JUMP 边（PARTIAL 时 spec_parser 接收 supplement_request 重新综合）
        TeamEdge(source="proposal_feedback_router", target="spec_parser",
                     condition=VerdictKind.PARTIAL, label="有 feedback → JUMP 回 spec_parser",
                     feedback=True),
    ]

    return TeamSpec(
        id="absorption.v3-stage3",
        name="Repo Absorption V3 Stage 3 · 工作流修改管线（3 路 fan-in）",
        description=(
            "Stage 3：将 absorption.report.v3 的改进提案解析为结构化任务。"
            "SpecParser 消费 composite Format (absorption.proposal.context)，"
            "3 路 fan-in（report + capability_inventory + gap_registry），wiki 结构化上下文注入。"
            "经人工审批后由 WorkflowGenerator（Phase 2）生成工作流变更。"
            "本版是 P-13 / F-15 规范的活样本。"
            "设计文档：docs/plans/[2026-04-14]STAGE3-WORKFLOW-MODIFIER/plan.md"
        ),
        nodes=nodes,
        edges=edges,
        entry="entry_bootstrap",
        tags=["domain.absorption", "phase.v3.s3", "fan-in.knowledge"],
        parallel_groups=[
            ["capability_query_builder", "gap_query_builder"],
            ["capability_loader", "gap_loader"],
        ],
    )


PIPELINES = {
    "absorption.survey": build_survey_pipeline,
    "absorption.v2": build_v2_pipeline,
    "absorption.v3": build_v3_pipeline,
    "absorption.v3-stage3": build_v3_stage3_pipeline,
}
