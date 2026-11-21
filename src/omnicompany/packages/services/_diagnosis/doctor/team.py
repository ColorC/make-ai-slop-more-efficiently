# [OMNI] origin=omnicompany domain=omnicompany/doctor ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:diagnosis.doctor.team_topology.builder.py"
"""doctor.team — Material 诊断 Team 拓扑

拓扑图（Phase 2，全顺序）:

  format_extractor → signature_diff ─(PASS)→ five_element → tag_coverage
                                    │                                  ↓
                                    │(FAIL)    parent_chain → example_presence
                                    │                              ↓
                                    │               semantic_quality → desc_eval
                                    │                                    │
                                    └────────────→ health_writer ←───────┘

说明:
  - format_extractor:  HARD，从源码提取 Format 定义和用途（doctor.material.request → extracted）
  - signature_diff:    HARD Anchor，校验是否存在正式定义（extracted → acc）
      FAIL → 直接产出最小健康档案（EMIT）
      PASS → 进入完整检查链
  - five_element:      HARD，五要素完整性（acc → acc）
  - tag_coverage:      HARD，命名规范（acc → acc）
  - parent_chain:      HARD，parent 字段合法性（acc → acc）
  - example_presence:  HARD，examples/json_schema 存在性（acc → acc）
  - semantic_quality:  HARD，无 LLM 语义质量（schema 字段提及率/example覆盖/追溯性）（acc → acc）
  - desc_eval:         LLM，定义质量评估（内容/用途/长度/schema 一致性/可操作性五维）（acc → acc）
  - health_writer:     HARD，汇总生成健康档案（acc → health-record）
"""

from omnicompany.protocol.team import (
    TeamSpec, TeamNode, TeamEdge,
    NodeKind, NodeMaturity,
)
from omnicompany.protocol.anchor import (
    AnchorSpec, TransformerSpec, TransformMethod,
    ValidatorSpec, ValidatorKind,
    Route, RouteAction, VerdictKind,
)
# 注：health_writer 只有一条入边（来自 desc_eval），in_degree=1。
# signature_diff FAIL 路径使用 EMIT 直接产出最小健康档案，不进入 health_writer。

DOMAIN = "doctor"


def build_team() -> TeamSpec:
    nodes = [
        # ── 1. Format 源码提取 ──
        TeamNode(
            id="format_extractor",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-material-extract",
                name="FormatExtractor",
                from_format=f"{DOMAIN}.material.request",
                to_format=f"{DOMAIN}.material.extracted",
                method=TransformMethod.RULE,
                description="扫描 source_root 下所有 .py 文件，提取 Format ID 的"
                            "定义行（formats.py 中的常量赋值 + 行内注释）和所有引用位置",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 2. 存在性校验（Anchor，可短路）──
        TeamNode(
            id="signature_diff",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-sig-diff",
                name="SignatureDiff",
                format_in=f"{DOMAIN}.material.extracted",
                format_out=f"{DOMAIN}.material.extracted",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-sig-v",
                    kind=ValidatorKind.HARD,
                    description="校验 Format ID 是否在 formats.py 中有正式常量定义；"
                                "无定义则以 FAIL 短路到 HealthWriter",
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        # target 留空 → Runner._resolve_next_all 按 PASS 边分发到所有 6 个检查器
                        feedback="Format 有正式定义，进入完整检查链（fan-out）",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.EMIT,
                        feedback="Format 无正式定义，直接输出最小健康档案（EMIT）",
                    ),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 3. 五要素完整性检查 ──
        TeamNode(
            id="five_element_check",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-five-elem",
                name="FiveElementCheck",
                from_format=f"{DOMAIN}.material.extracted",
                to_format=f"{DOMAIN}.material.check.five-element",
                method=TransformMethod.RULE,
                description="检查 Format 五要素：ID 含域前缀 / 有常量名 / "
                            "有行内描述 / 定义于 formats.py / 至少一处引用",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 4. 命名规范检查 ──
        TeamNode(
            id="tag_coverage",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-tag-cov",
                name="TagCoverage",
                from_format=f"{DOMAIN}.material.extracted",
                to_format=f"{DOMAIN}.material.check.tag-coverage",
                method=TransformMethod.RULE,
                description="检查 Format ID 命名规范：全小写 / 含语义类型标记 / 无重复域前缀",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 5. 管线连通性检查 ──
        TeamNode(
            id="parent_chain",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-parent-chain",
                name="ParentChain",
                from_format=f"{DOMAIN}.material.extracted",
                to_format=f"{DOMAIN}.material.check.parent-chain",
                method=TransformMethod.RULE,
                description="检查 Format 在管线中的连通性：是否有 FORMAT_IN 和 FORMAT_OUT 使用者",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 6. composite Format 组合完整性检查 ──
        TeamNode(
            id="composite_format_check",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-composite-check",
                name="CompositeFormatCheck",
                from_format=f"{DOMAIN}.material.extracted",
                to_format=f"{DOMAIN}.material.check.composite",
                method=TransformMethod.RULE,
                description="检查 composite Format 的 components 合法性和描述完整性；非 composite 跳过",
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 7. 复杂格式示例存在性检查 ──
        TeamNode(
            id="example_presence",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-example-presence",
                name="ExamplePresence",
                from_format=f"{DOMAIN}.material.extracted",
                to_format=f"{DOMAIN}.material.check.example-presence",
                method=TransformMethod.RULE,
                description="检查 Format.examples 列表非空且含有意义的示例 dict（[PLANNED] 格式豁免）",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 8. 全语境语义审计（LLM）──
        TeamNode(
            id="desc_eval",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-contextual-audit",
                name="FormatContextualAudit",
                from_format=f"{DOMAIN}.material.extracted",
                to_format=f"{DOMAIN}.material.check.llm-audit",
                method=TransformMethod.LLM,
                description="LLM 全语境审计：format + 上下游 Router 源码 + 完整标准 → "
                            "定性报告（F-01/F-06/F-08/FA 反模式/上下游匹配）+ git 存档",
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 9. 健康档案汇总（fan-in 汇聚点）──
        TeamNode(
            id="health_writer",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-health-write",
                name="HealthWriter",
                from_format=f"{DOMAIN}.material.checks",
                to_format=f"{DOMAIN}.material.health-record",
                method=TransformMethod.RULE,
                description="汇总所有检查结果，计算健康评分（0~1）和等级（A/B/C/D/F），"
                            "输出最终健康档案",
            ),
            maturity=NodeMaturity.MATURE,  # 上游含 GROWING（composite_format_check / desc_eval），短板原则
        ),
    ]

    edges = [
        TeamEdge(source="format_extractor", target="signature_diff"),
        # ── fan-out：signature_diff PASS → 6 个独立检查器（并行语义）──
        # FAIL → EMIT（无下游边，直接产出最小健康档案）
        TeamEdge(source="signature_diff", target="five_element_check",
                     condition=VerdictKind.PASS, label="有正式定义 → 五要素检查"),
        TeamEdge(source="signature_diff", target="tag_coverage",
                     condition=VerdictKind.PASS, label="有正式定义 → 命名规范检查"),
        TeamEdge(source="signature_diff", target="parent_chain",
                     condition=VerdictKind.PASS, label="有正式定义 → 连通性检查"),
        TeamEdge(source="signature_diff", target="composite_format_check",
                     condition=VerdictKind.PASS, label="有正式定义 → composite 检查"),
        TeamEdge(source="signature_diff", target="example_presence",
                     condition=VerdictKind.PASS, label="有正式定义 → 示例质量检查"),
        TeamEdge(source="signature_diff", target="desc_eval",
                     condition=VerdictKind.PASS, label="有正式定义 → LLM 语义审计"),
        # ── fan-in：6 个检查器 → health_writer 汇聚 ──
        TeamEdge(source="five_element_check",    target="health_writer"),
        TeamEdge(source="tag_coverage",          target="health_writer"),
        TeamEdge(source="parent_chain",          target="health_writer"),
        TeamEdge(source="composite_format_check",target="health_writer"),
        TeamEdge(source="example_presence",      target="health_writer"),
        TeamEdge(source="desc_eval",             target="health_writer"),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-material-diagnosis",
        name="Format Health Diagnosis Pipeline",
        description=(
            "Format 健康诊断管线：对指定 Format ID 做全面检查\n"
            "（存在性 → 五要素 → 命名规范 → 连通性 → 描述质量 → 健康档案）"
        ),
        nodes=nodes,
        edges=edges,
        entry="format_extractor",
        tags=["doctor", "format", "diagnosis", "health"],
    )


def build_team_topology_pipeline() -> TeamSpec:
    """构建 Pipeline 拓扑诊断管线。

    拓扑（fan-out / fan-in 模式，与 Format 诊断管线对称）:

      pipeline_spec_loader ─(PASS)→ pipeline_structural_check  ──→
                           │        pipeline_format_contract   ──→
                           │        pipeline_maturity_check    ──→ pipeline_topo_health_writer
                           │        pipeline_soft_hard_check   ──→
                           │        pipeline_narrative_check   ──→
                           │(FAIL)→ EMIT 最小健康档案

    说明:
      - pipeline_spec_loader:       HARD Anchor，从 pipeline.py 加载 TeamSpec；失败 → EMIT
      - pipeline_structural_check:  RULE，no_entry/isolated/dead_end/cycle/duplicate_edge
      - pipeline_format_contract:   RULE，format_break/composite_missing/granted_tag_chain
      - pipeline_maturity_check:    RULE，maturity_consistency（短板原则）
      - pipeline_soft_hard_check:   RULE，soft_hard_pairing（P-07）
      - pipeline_narrative_check:   LLM，叙事连贯性/语义跳跃/意图对齐（L4）
      - pipeline_topo_health_writer:RULE，fan-in 汇聚 → 健康档案

    自指性：本管线本身应通过自己定义的所有检查（无 blocking/degrading Finding）。
    """
    nodes = [
        # ── 1. 加载 TeamSpec 对象（Anchor，可短路）──
        TeamNode(
            id="pipeline_spec_loader",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-team-loader",
                name="TeamSpecLoader",
                format_in="diag.team.request",
                format_out="diag.team.extracted",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-team-loader-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "从 pipeline.py 调用所有无参数 build_*() 函数，加载 TeamSpec 对象；"
                        "文件不存在/无有效 build_*()/加载异常则 FAIL → EMIT 最小健康档案"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        # target 留空 → Runner._resolve_next_all 按 PASS 边分发到所有 5 个检查器
                        feedback="Pipeline 文件加载成功，进入 5 路并行拓扑检查（fan-out）",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.EMIT,
                        feedback="Pipeline 文件加载失败，EMIT 最小健康档案",
                    ),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 2a. 结构合法性检查（fan-out 第 1 路）──
        TeamNode(
            id="pipeline_structural_check",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-team-structural",
                name="PipelineStructuralCheck",
                from_format="diag.team.extracted",
                to_format="diag.team.check.structural",
                method=TransformMethod.RULE,
                description=(
                    "Pipeline 结构合法性检查："
                    "no_entry（入口节点存在性）/ isolated（孤立节点）/ "
                    "dead_end（悬空终端）/ cycle（非 feedback 边成环）/ duplicate_edge（重复边）"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 2b. Format 契约检查（fan-out 第 2 路）──
        TeamNode(
            id="pipeline_format_contract",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-team-material-contract",
                name="PipelineFormatContractCheck",
                from_format="diag.team.extracted",
                to_format="diag.team.check.format-contract",
                method=TransformMethod.RULE,
                description=(
                    "Pipeline Format 契约检查："
                    "format_break（相邻边 Format 断裂）/ "
                    "composite_missing（composite Format 上游覆盖缺失）/ "
                    "granted_tag_chain（required_tags 被上游 format_out.tags 静态覆盖）"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 2c. 成熟度一致性检查（fan-out 第 3 路）──
        TeamNode(
            id="pipeline_maturity_check",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-team-maturity",
                name="PipelineMaturityCheck",
                from_format="diag.team.extracted",
                to_format="diag.team.check.maturity",
                method=TransformMethod.RULE,
                description=(
                    "Pipeline 成熟度一致性检查（短板原则）："
                    "CRYSTALLIZED 节点不应直接依赖 GROWING/HYPOTHETICAL 上游，"
                    "否则 CRYSTALLIZED 声明具有误导性"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 2d. 软硬配对检查（fan-out 第 4 路）──
        TeamNode(
            id="pipeline_soft_hard_check",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-team-soft-hard",
                name="PipelineSoftHardCheck",
                from_format="diag.team.extracted",
                to_format="diag.team.check.soft-hard",
                method=TransformMethod.RULE,
                description=(
                    "P-07 软硬配对检查：LLM 节点（method=LLM）的直接下游应有 "
                    "RULE 或 ANCHOR 节点作为验证器，否则 LLM 输出无确定性保障"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 2e. 整管线叙事审计（fan-out 第 5 路，L4 LLM）──
        TeamNode(
            id="pipeline_narrative_check",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-team-narrative",
                name="PipelineNarrativeCheck",
                from_format="diag.team.extracted",
                to_format="diag.team.check.narrative",
                method=TransformMethod.LLM,
                description=(
                    "L4 整管线叙事审计（LLM）：给定完整 Format 链 + 所有节点 DESCRIPTION + "
                    "purpose/design_rationale（来自 manifest），评估叙事连贯性、语义跳跃、意图对齐。"
                    "LLM 失败时降级为 SKIP（passed=None），不阻断管线。"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 3. 健康档案汇总（fan-in 汇聚点）──
        TeamNode(
            id="pipeline_topo_health_writer",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-team-topo-health",
                name="PipelineTopoHealthWriter",
                from_format="diag.team.checks",
                to_format="diag.team.health-record",
                method=TransformMethod.RULE,
                description=(
                    "汇总 5 个并行检查器的所有 Finding，按 level 排序，"
                    "计算健康等级（PASS/INFO/WARN/FAIL），输出结构化健康档案"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]

    edges = [
        # PASS → 5 路并行检查（fan-out；FAIL → EMIT，无下游边）
        TeamEdge(source="pipeline_spec_loader", target="pipeline_structural_check",
                     condition=VerdictKind.PASS, label="加载成功 → 结构合法性检查"),
        TeamEdge(source="pipeline_spec_loader", target="pipeline_format_contract",
                     condition=VerdictKind.PASS, label="加载成功 → Format 契约检查"),
        TeamEdge(source="pipeline_spec_loader", target="pipeline_maturity_check",
                     condition=VerdictKind.PASS, label="加载成功 → 成熟度一致性检查"),
        TeamEdge(source="pipeline_spec_loader", target="pipeline_soft_hard_check",
                     condition=VerdictKind.PASS, label="加载成功 → 软硬配对检查"),
        TeamEdge(source="pipeline_spec_loader", target="pipeline_narrative_check",
                     condition=VerdictKind.PASS, label="加载成功 → 叙事审计（L4）"),
        # fan-in → health_writer（5 → 1）
        TeamEdge(source="pipeline_structural_check", target="pipeline_topo_health_writer"),
        TeamEdge(source="pipeline_format_contract",  target="pipeline_topo_health_writer"),
        TeamEdge(source="pipeline_maturity_check",   target="pipeline_topo_health_writer"),
        TeamEdge(source="pipeline_soft_hard_check",  target="pipeline_topo_health_writer"),
        TeamEdge(source="pipeline_narrative_check",  target="pipeline_topo_health_writer"),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-team-topology",
        name="Pipeline Topology Diagnosis Pipeline",
        description=(
            "Pipeline 拓扑诊断管线：加载 pipeline.py → 5 路并行拓扑检查 → 健康档案\n"
            "（结构合法性 + Format 契约 + 成熟度一致性 + 软硬配对 + L4 叙事审计）"
        ),
        purpose="对 pipeline.py 文件执行分类拓扑检查，产出每类问题独立可审计的 Finding 列表和结构化健康档案",
        nodes=nodes,
        edges=edges,
        entry="pipeline_spec_loader",
        tags=["doctor", "pipeline", "topology", "diagnosis"],
    )


def build_router_pipeline() -> TeamSpec:
    """构建 Router 健康诊断管线。

    拓扑：
      rtr_extractor → rtr_signature ─(PASS)→ rtr_context_collector → rtr_det_checker
                                   │                    ↓                    ↓
                                   │         rtr_contextual_audit → rtr_health_writer
                                   │(FAIL)
                                   └────────────────────→ rtr_health_writer (EMIT 最小档案)
    """
    nodes = [
        # ── 1. AST 提取 Router 类结构 ──
        TeamNode(
            id="rtr_extractor",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-worker-extract",
                name="RouterExtractor",
                from_format="diag.worker.request",
                to_format="diag.worker.extracted",
                method=TransformMethod.RULE,
                description=(
                    "AST 解析 source_file，提取目标 router_class 的"
                    "类变量（DESCRIPTION/FORMAT_IN/OUT）、run() 源码和行数、"
                    "以及 7 类衍生信号（llm_calls/self_assignments/verdict_patterns 等）"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 2. 存在性校验（Anchor，可短路）──
        TeamNode(
            id="rtr_signature",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-worker-sig",
                name="RouterSignature",
                format_in="diag.worker.extracted",
                format_out="diag.worker.sig-checked",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-worker-sig-v",
                    kind=ValidatorKind.HARD,
                    description=(
                        "校验 Router 类是否存在且有 DESCRIPTION/FORMAT_IN/FORMAT_OUT；"
                        "任一缺失则 EMIT 最小健康档案（短路跳过后续节点）"
                    ),
                ),
                routes={
                    VerdictKind.PASS: Route(
                        action=RouteAction.NEXT,
                        target="rtr_context_collector",
                        feedback="Router 基础元数据完整，进入完整诊断链",
                    ),
                    VerdictKind.FAIL: Route(
                        action=RouteAction.EMIT,
                        feedback="Router 基础元数据缺失，EMIT 最小健康档案",
                    ),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 3. 跨 source_root 收集上下文 ──
        TeamNode(
            id="rtr_context_collector",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-worker-ctx",
                name="RouterContextCollector",
                from_format="diag.worker.sig-checked",
                to_format="diag.worker.context",
                method=TransformMethod.RULE,
                description=(
                    "根据 FORMAT_IN/OUT 在整个 source_root 搜索："
                    "Format 对象定义（来自任何 formats.py）、"
                    "上游 Router（FORMAT_OUT == 本 Router 的 FORMAT_IN）、"
                    "下游 Router（FORMAT_IN == 本 Router 的 FORMAT_OUT）、"
                    "Pipeline 引用（哪条 pipeline.py 用到了本 Router）"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 4. 确定性检查（11 项）──
        TeamNode(
            id="rtr_det_checker",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-worker-det",
                name="RouterDetChecker",
                from_format="diag.worker.context",
                to_format="diag.worker.det-checks",
                method=TransformMethod.RULE,
                description=(
                    "对 Router run() 源码执行 11 项确定性检查："
                    "R-01(DESCRIPTION长度) / R-04(统一LLMClient) / R-05(PASS+FAIL双覆盖) / "
                    "R-06(不直接写文件) / R-10(run()≤80行) / R-11(无硬编模型) / "
                    "R-12(无协议泄漏) / R-13(RULE confidence=1.0) / R-17(异常不假通过) / "
                    "R-07-signal(self赋值分类信号，passed=null供LLM解读)"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 5. LLM 全语境语义审计 ──
        TeamNode(
            id="rtr_contextual_audit",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-worker-audit",
                name="RouterContextualAudit",
                from_format="diag.worker.det-checks",
                to_format="diag.worker.audit",
                method=TransformMethod.LLM,
                description=(
                    "LLM 全语境审计：Router 源码 + FORMAT 定义 + 邻居 DESCRIPTION + "
                    "Pipeline 简述 + 确定性失败摘要 + AST 信号 + router.md 标准节选 → "
                    "层 A/B/C/D 评级 + 改进建议 + git 存档。"
                    "RULE Router 用 Schema B（精简），LLM Router 用 Schema A（完整）"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── 6. 汇总，生成健康档案 ──
        TeamNode(
            id="rtr_health_writer",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-worker-health",
                name="RouterHealthWriter",
                from_format="diag.worker.audit",
                to_format="diag.worker.health-record",
                method=TransformMethod.RULE,
                description=(
                    "汇总 acc.checks，加权评分（CRITICAL=4/HIGH=3/MEDIUM=2/LOW=1/INFO=0），"
                    "优先采用 LLM 审计的 overall_grade，否则按分数映射（≥0.90→A/≥0.75→B/≥0.55→C/else→D），"
                    "输出 diag.worker.health-record"
                ),
            ),
            maturity=NodeMaturity.GROWING,
        ),
    ]

    edges = [
        TeamEdge(source="rtr_extractor", target="rtr_signature"),
        TeamEdge(
            source="rtr_signature",
            target="rtr_context_collector",
            condition=VerdictKind.PASS,
            label="基础元数据完整 → 完整诊断链",
        ),
        # FAIL → EMIT（rtr_signature FAIL 时直接 EMIT 最小档案，不走 health_writer）
        TeamEdge(source="rtr_context_collector", target="rtr_det_checker"),
        TeamEdge(source="rtr_det_checker", target="rtr_contextual_audit"),
        TeamEdge(source="rtr_contextual_audit", target="rtr_health_writer"),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-router-diagnosis",
        name="Router Health Diagnosis Pipeline",
        description=(
            "Router 健康诊断管线：对指定 Router 类做全面检查\n"
            "（AST 提取 → 存在性 → 上下文收集 → 确定性检查 → LLM 语义审计 → 健康档案）"
        ),
        nodes=nodes,
        edges=edges,
        entry="rtr_extractor",
        tags=["doctor", "router", "diagnosis", "health"],
    )
