# [OMNI] origin=human domain=software_engineering/lang_rewrite ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.lang_rewrite.team_topology.dag.py"
"""lang_rewrite.pipeline — 跨语言改写管线拓扑（完整体 DAG）

分层验证架构（基于 VERT/Syzygy/OpenRewrite 行业经验）:

  source_analyzer → dependency_mapper ──→ demand_extractor ──→ ┐
                                      ──→ supply_scanner   ──→ ┤→ idiom_translator (LLM)
                                                                ┘         │
                                                                    PASS  │  FAIL→RETRY
                                                                          ↓
                                           ┌─────── type_checker ←── agent_fixer ←─┐ (feedback)
                                           │       (L1 tsc HARD)  (LLM 修复)       │
                                           ↓ PASS                                   └── FAIL
                                       style_checker (L2 biome lint HARD)
                                           │ FAIL → style_fixer → style_checker (feedback)
                                           ↓ PASS
                                    interface_extractor (确定性 AST)
                                           │
                                    ┌──────┴──────┐
                                    ↓             ↓
                           signature_comparator   behavioral_tester
                           (L3a 签名比对 HARD)     (L3b import 测试 HARD)
                                    └──────┬──────┘
                                           ↓ fan-in join
                                    equivalence_judge (L4 LLM 最终裁判 SOFT)
                                           │
                                     PASS→EMIT
                                     FAIL→feedback_demote→idiom_translator (feedback)

层次说明:
  L1 type_checker:       tsc --strict, 确定性 HARD, 0 错误才能继续
  L2 style_checker:      biome lint, 确定性 HARD, 惯用风格验证
  L3a signature_comparator: AST 接口名比对, 确定性, 缺失接口必须重译
  L3b behavioral_tester: import 验证脚本, 确定性, tsc 编译固定模板
  L4 equivalence_judge:  LLM 语义裁判, 只在前三层全通过后启动
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

DOMAIN = "rewrite"


def build_team() -> TeamSpec:
    nodes = [
        # ── 分析阶段（确定性）──
        TeamNode(
            id="source_analyzer",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-source-analyze",
                name="SourceAnalyzer",
                from_format=f"{DOMAIN}.source-module",
                to_format=f"{DOMAIN}.source-module",
                method=TransformMethod.RULE,
                description="解析 Python 源文件：提取 AST、公开接口、内部 import、外部依赖",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
        TeamNode(
            id="dependency_mapper",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-dep-map",
                name="DependencyMapper",
                from_format=f"{DOMAIN}.source-module",
                to_format=f"{DOMAIN}.dependency-graph",
                method=TransformMethod.RULE,
                description="构建模块依赖图，拓扑排序确定移植顺序，映射外部依赖到目标语言等价物",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 需求侧扫描（确定性：下游如何调用当前模块）──
        TeamNode(
            id="demand_extractor",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-demand-extract",
                name="DemandExtractor",
                from_format=f"{DOMAIN}.dependency-graph",
                to_format=f"{DOMAIN}.demand-set",
                method=TransformMethod.RULE,
                description="扫描下游模块对当前模块的调用需求，提取签名期望（需求侧）",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 供给侧扫描（确定性：当前模块依赖的签名）──
        TeamNode(
            id="supply_scanner",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-supply-scan",
                name="SupplyScanner",
                from_format=f"{DOMAIN}.dependency-graph",
                to_format=f"{DOMAIN}.supply-map",
                method=TransformMethod.RULE,
                description="扫描当前模块依赖的真实签名：TS 已有则提取声明，缺失则提取 Python 签名",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── 翻译阶段（LLM，fan-in 自 demand_extractor + supply_scanner）──
        TeamNode(
            id="idiom_translator",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-idiom-translate",
                name="IdiomTranslator",
                format_in=f"{DOMAIN}.translation-context",
                format_out=f"{DOMAIN}.generated-code",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-translate-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 将 Python 模块翻译为目标语言惯用写法，保持接口签名和语义等价",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="type_checker"),
                    VerdictKind.FAIL: Route(action=RouteAction.RETRY, max_retries=3),
                    VerdictKind.PARTIAL: Route(action=RouteAction.RETRY, max_retries=3,
                                               feedback="部分翻译通过，请修正失败部分"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── L1: 编译检查（确定性 HARD）──
        TeamNode(
            id="type_checker",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-type-check",
                name="TypeChecker",
                format_in=f"{DOMAIN}.generated-code",
                format_out=f"{DOMAIN}.checked-code",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-typecheck-v",
                    kind=ValidatorKind.HARD,
                    description="L1: tsc --strict 零错误通过，确定性",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="style_checker"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="agent_fixer"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── L1 修复兜底 ──
        TeamNode(
            id="agent_fixer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-agent-fix",
                name="AgentFixer",
                format_in=f"{DOMAIN}.generated-code",
                format_out=f"{DOMAIN}.generated-code",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-agentfix-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 自主修复编译错误",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.JUMP, target="type_checker"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT,
                                            feedback="无法修复编译错误，需人工介入"),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── L2: 风格检查（确定性 HARD）──
        TeamNode(
            id="style_checker",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-style-check",
                name="StyleChecker",
                format_in=f"{DOMAIN}.checked-code",
                format_out=f"{DOMAIN}.style-checked",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-style-v",
                    kind=ValidatorKind.HARD,
                    description="L2: biome lint 惯用风格检查（any 滥用/未用变量/不安全操作）",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="interface_extractor"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="style_fixer"),
                },
            ),
            maturity=NodeMaturity.GROWING,
        ),

        # ── L2 风格修复 ──
        TeamNode(
            id="style_fixer",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-style-fix",
                name="StyleFixer",
                format_in=f"{DOMAIN}.style-checked",
                format_out=f"{DOMAIN}.checked-code",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-stylefix-v",
                    kind=ValidatorKind.SOFT,
                    description="LLM 修复惯用风格问题（消除 any/补类型注解）",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.JUMP, target="style_checker"),
                    VerdictKind.FAIL: Route(action=RouteAction.HALT,
                                            feedback="风格修复失败，需人工介入"),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── L3 前置：接口规格提取（确定性 AST）──
        TeamNode(
            id="interface_extractor",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-iface-extract",
                name="InterfaceExtractor",
                from_format=f"{DOMAIN}.style-checked",
                to_format=f"{DOMAIN}.interface-spec",
                method=TransformMethod.RULE,
                description="确定性 AST 提取 Python/__all__ 和 TS/export 的公开接口规格",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── L3a: 签名比对（确定性 HARD，fan-out 之一）──
        TeamNode(
            id="signature_comparator",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-sig-compare",
                name="SignatureComparator",
                format_in=f"{DOMAIN}.interface-spec",
                format_out=f"{DOMAIN}.signature-compared",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-sigcmp-v",
                    kind=ValidatorKind.HARD,
                    description="L3a: 对比 Python/TS 接口名集合，缺失接口必须重译",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="equivalence_judge"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="feedback_demote",
                                            feedback="接口签名缺失，需重新翻译"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── L3b: 行为测试（确定性执行，fan-out 之二）──
        TeamNode(
            id="behavioral_tester",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-behav-test",
                name="BehavioralTester",
                format_in=f"{DOMAIN}.interface-spec",
                format_out=f"{DOMAIN}.behavioral-tested",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-behavtest-v",
                    kind=ValidatorKind.HARD,
                    description="L3b: 固定模板 import 测试，验证接口可导入 + 类型正确",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.NEXT, target="equivalence_judge"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="feedback_demote",
                                            feedback="接口 import 测试失败，需重新翻译"),
                },
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),

        # ── L4: LLM 最终语义裁判（fan-in 自 sig + behav）──
        TeamNode(
            id="equivalence_judge",
            kind=NodeKind.ANCHOR,
            anchor=AnchorSpec(
                id=f"{DOMAIN}-equiv-judge",
                name="EquivalenceJudge",
                format_in=f"{DOMAIN}.behavioral-tested",
                format_out=f"{DOMAIN}.verified-code",
                validator=ValidatorSpec(
                    id=f"{DOMAIN}-equivjudge-v",
                    kind=ValidatorKind.SOFT,
                    description="L4: LLM 最终语义裁判（设计意图/六元约束/惯用性），前置层全通过后才启动",
                ),
                routes={
                    VerdictKind.PASS: Route(action=RouteAction.EMIT),
                    VerdictKind.PARTIAL: Route(action=RouteAction.NEXT, target="feedback_demote",
                                               feedback="部分语义不等价，需重新翻译"),
                    VerdictKind.FAIL: Route(action=RouteAction.NEXT, target="feedback_demote",
                                            feedback="语义等价性判定失败，需重新翻译"),
                },
            ),
            maturity=NodeMaturity.HYPOTHETICAL,
        ),

        # ── 回退降级 Transformer ──
        TeamNode(
            id="feedback_demote",
            kind=NodeKind.TRANSFORMER,
            transformer=TransformerSpec(
                id=f"{DOMAIN}-feedback-demote",
                name="FeedbackDemote",
                from_format=f"{DOMAIN}.verified-code",
                to_format=f"{DOMAIN}.translation-context",
                method=TransformMethod.RULE,
                description="验证失败时，携带反馈降级为 translation-context 重新翻译",
            ),
            maturity=NodeMaturity.CRYSTALLIZED,
        ),
    ]

    edges = [
        # ── 分析链 ──
        TeamEdge(source="source_analyzer", target="dependency_mapper"),
        # ── Fan-out: 两路并发上下文扫描 ──
        TeamEdge(source="dependency_mapper", target="demand_extractor",
                     label="依赖图→需求侧扫描"),
        TeamEdge(source="dependency_mapper", target="supply_scanner",
                     label="依赖图→供给侧扫描"),
        # ── Fan-in: 汇入翻译 ──
        TeamEdge(source="demand_extractor", target="idiom_translator",
                     label="需求侧→翻译上下文"),
        TeamEdge(source="supply_scanner", target="idiom_translator",
                     label="供给侧→翻译上下文"),
        # ── L1: 编译链 ──
        TeamEdge(source="idiom_translator", target="type_checker", condition="pass"),
        TeamEdge(source="type_checker", target="style_checker", condition="pass"),
        TeamEdge(source="type_checker", target="agent_fixer", condition="fail"),
        TeamEdge(source="agent_fixer", target="type_checker",
                     condition="pass", feedback=True, label="编译修复后重检"),
        # ── L2: 风格链 ──
        TeamEdge(source="style_checker", target="interface_extractor", condition="pass"),
        TeamEdge(source="style_checker", target="style_fixer", condition="fail"),
        TeamEdge(source="style_fixer", target="style_checker",
                     condition="pass", feedback=True, label="风格修复后重检"),
        # ── L3: Fan-out 并发 sig + behav ──
        TeamEdge(source="interface_extractor", target="signature_comparator",
                     label="接口规格→签名比对"),
        TeamEdge(source="interface_extractor", target="behavioral_tester",
                     label="接口规格→行为测试"),
        # ── L4: Fan-in 汇入最终裁判 ──
        TeamEdge(source="signature_comparator", target="equivalence_judge",
                     condition="pass", label="签名通过→裁判"),
        TeamEdge(source="behavioral_tester", target="equivalence_judge",
                     condition="pass", label="行为通过→裁判"),
        # ── L3/L4 失败 → 降级重译 ──
        TeamEdge(source="signature_comparator", target="feedback_demote",
                     condition="fail", label="签名缺失→降级"),
        TeamEdge(source="behavioral_tester", target="feedback_demote",
                     condition="fail", label="行为失败→降级"),
        TeamEdge(source="equivalence_judge", target="feedback_demote",
                     condition="fail", label="语义判定失败→降级"),
        TeamEdge(source="equivalence_judge", target="feedback_demote",
                     condition="partial", label="部分等价→降级"),
        # ── 反馈边 ──
        TeamEdge(source="feedback_demote", target="idiom_translator",
                     feedback=True, label="携带反馈重新翻译"),
    ]

    return TeamSpec(
        id=f"{DOMAIN}-pipeline",
        name="Language Rewrite Pipeline",
        description="将 Python 引擎层模块改写为 TypeScript / Rust，"
                    "通过编译检查 + 语义等价性验证闭环保证正确性",
        nodes=nodes,
        edges=edges,
        entry="source_analyzer",
        tags=["rewrite", "cross-language", "engine"],
    )
