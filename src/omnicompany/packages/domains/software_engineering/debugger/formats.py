# [OMNI] origin=human domain=software_engineering/debugger ts=2026-04-08T03:23:41Z
# [OMNI] material_id="material:domains.software_engineering.debugger.semantic_formats.registry.py"
"""debugger.formats — 调试工作流的语义类型体系

数据流不是线性的，是循环的：

  error-report → error-analysis ──→ hypothesis
                                      ↕ (循环)
                              probe-result ← probe-plan
                                      ↓
                                  fix-patch → test-feedback
                                                  ↓
                                          (PASS → verified-fix)
                                          (FAIL → 回到 hypothesis 更新)

贯穿全程的累积上下文: debug-context（假设历史+证据+修改记录）
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "debug"

FORMATS = [
    # ── 错误输入 ──
    Format(
        id=f"{DOMAIN}.error-report",
        name="ErrorReport",
        description="编译器或测试框架的原始错误输出，包含错误消息、文件路径、行号、错误代码",
        parent="tool-observation",
    ),

    # ── 分析产出 ──
    Format(
        id=f"{DOMAIN}.error-analysis",
        name="ErrorAnalysis",
        description="对错误的直接原因分析：什么类型的错误、错误发生在哪个表达式、涉及哪些类型/变量",
        parent=f"{DOMAIN}.error-report",
        tags=["analyzed"],
    ),
    Format(
        id=f"{DOMAIN}.trace-evidence",
        name="TraceEvidence",
        description="追踪过程中读到的代码片段：变量定义、类型声明、import 来源等，用于支撑或否定假设",
        parent="tool-observation",
        tags=["evidence"],
    ),

    # ── 假设体系（核心循环载体）──
    Format(
        id=f"{DOMAIN}.hypothesis",
        name="Hypothesis",
        description="对错误根因的具体假设：错误可能发生在哪个文件哪个位置、为什么会出这个错、预测修复方式",
        parent="debug.hypothesis",
        tags=["hypothesis"],
    ),
    Format(
        id=f"{DOMAIN}.probe-plan",
        name="ProbePlan",
        description="为验证假设设计的试探方案：加什么日志、写什么最小测试、读哪个文件的哪一段",
        parent=f"{DOMAIN}.hypothesis",
        tags=["hypothesis", "probe-designed"],
        required_tags=["hypothesis"],
    ),
    Format(
        id=f"{DOMAIN}.probe-result",
        name="ProbeResult",
        description="试探执行的结果：日志输出、测试通过/失败、读到的代码内容，以及对假设的判定（证实/证否/不确定）",
        parent="tool-observation",
        tags=["evidence", "probe-executed"],
    ),

    # ── 修复侧 ──
    Format(
        id=f"{DOMAIN}.fix-patch",
        name="FixPatch",
        description="具体的修复变更：文件路径、原内容、新内容、修复理由（关联到哪个被证实的假设）",
        parent="code",
        tags=["fix-proposed"],
        required_tags=["hypothesis"],
    ),
    Format(
        id=f"{DOMAIN}.test-feedback",
        name="TestFeedback",
        description="修复后的复测结果：编译器/测试的完整输出，标注是完全通过、部分通过还是新错误",
        parent="tool-observation",
        tags=["tested"],
    ),
    Format(
        id=f"{DOMAIN}.regression-analysis",
        name="RegressionAnalysis",
        description="复测失败时的归因分析：判断是假设错误（需回退+重新假设）、实践错误（假设对但改法不对）、还是不同的新问题",
        parent=f"{DOMAIN}.test-feedback",
        tags=["tested", "regression-analyzed"],
        required_tags=["tested"],
    ),
    Format(
        id=f"{DOMAIN}.verified-fix",
        name="VerifiedFix",
        description="复测完全通过的修复：补丁内容 + 证实假设 + 测试通过证据，可以安全提交",
        parent=f"{DOMAIN}.fix-patch",
        tags=["fix-proposed", "verified"],
        required_tags=["fix-proposed", "tested"],
    ),

    # ── 累积上下文（贯穿全程，所有回路节点的通用输入）──
    Format(
        id=f"{DOMAIN}.debug-context",
        name="DebugContext",
        description="调试全程的累积状态：错误历史、所有假设及其证据和判定结果、尝试过的修改及其复测结果、已排除的方向",
        parent="agent-state",
        tags=["stateful", "accumulating"],
    ),
    Format(
        id=f"{DOMAIN}.enriched-context",
        name="EnrichedContext",
        description="附带了新证据/分析/回归结论的调试上下文，是 debug-context 的子类型，用于回路输入",
        parent=f"{DOMAIN}.debug-context",
        tags=["stateful", "accumulating", "enriched"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
