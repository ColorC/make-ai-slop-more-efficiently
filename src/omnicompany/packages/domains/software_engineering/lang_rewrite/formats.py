# [OMNI] origin=human domain=software_engineering/lang_rewrite ts=2026-04-08T03:23:42Z
# [OMNI] material_id="material:domains.software_engineering.lang_rewrite.rewrite_formats.definitions.py"
"""lang_rewrite.formats — 跨语言改写管线的语义类型体系

数据流（三路汇入翻译）:
  source-module → dependency-graph ──→ demand-set ──→ ┐
                                  ──→ supply-map  ──→ ├→ translation-context → generated-code
                                                       ┘        ↓
                                                          checked-code → verified-code
"""

from omnicompany.protocol.format import Format, FormatRegistry

DOMAIN = "rewrite"

FORMATS = [
    # ── 分析阶段 ──
    Format(
        id=f"{DOMAIN}.source-module",
        name="SourceModule",
        description="待改写的 Python 源文件及其元数据：路径、AST 摘要、公开接口列表、内部依赖",
        parent="code",
    ),
    Format(
        id=f"{DOMAIN}.dependency-graph",
        name="DependencyGraph",
        description="引擎层模块间的内部依赖图：拓扑排序后的移植顺序和每个模块的外部依赖映射",
        parent=f"{DOMAIN}.source-module",
        tags=["structured", "dependency-resolved"],
    ),

    # ── 上下文扫描阶段（并发扫描，fan-out 自 dependency-graph）──
    Format(
        id=f"{DOMAIN}.demand-set",
        name="DemandSet",
        description="需求侧：下游模块对当前模块的调用方式（签名期望）",
        parent=f"{DOMAIN}.dependency-graph",
        tags=["structured", "dependency-resolved"],
    ),
    Format(
        id=f"{DOMAIN}.supply-map",
        name="SupplyMap",
        description="供给侧：当前模块依赖的真实签名（TS 已有则提取声明，缺失则提取 Python 签名）",
        parent=f"{DOMAIN}.dependency-graph",
        tags=["structured", "dependency-resolved"],
    ),

    # ── 翻译阶段（fan-in 自 demand-set + supply-map）──
    Format(
        id=f"{DOMAIN}.translation-context",
        name="TranslationContext",
        description="翻译上下文：源代码 + 供给侧签名上下文 + 需求侧调用需求，供 LLM 翻译使用",
        parent=f"{DOMAIN}.dependency-graph",
        tags=["structured", "dependency-resolved", "translation-ready"],
        required_tags=["dependency-resolved"],
    ),
    Format(
        id=f"{DOMAIN}.generated-code",
        name="GeneratedCode",
        description="LLM 产出的目标语言代码：源文件内容 + 目标文件内容 + 接口对照表",
        parent="code",
        tags=["translated"],
    ),

    # ── L1 验证 ──
    Format(
        id=f"{DOMAIN}.checked-code",
        name="CheckedCode",
        description="L1: tsc --strict 零错误通过的代码",
        parent=f"{DOMAIN}.generated-code",
        tags=["translated", "type-checked"],
        required_tags=["translated"],
    ),

    # ── L2 验证 ──
    Format(
        id=f"{DOMAIN}.style-checked",
        name="StyleChecked",
        description="L2: biome lint 惯用风格检查通过的代码（无 any 滥用/未用变量等）",
        parent=f"{DOMAIN}.checked-code",
        tags=["translated", "type-checked", "style-checked"],
        required_tags=["type-checked"],
    ),

    # ── L3 前置：接口规格 ──
    Format(
        id=f"{DOMAIN}.interface-spec",
        name="InterfaceSpec",
        description="双语接口规格：Python __all__ 和 TS export 的公开接口列表（AST 提取）",
        parent=f"{DOMAIN}.style-checked",
        tags=["translated", "type-checked", "style-checked"],
    ),

    # ── L3a: 签名比对结果 ──
    Format(
        id=f"{DOMAIN}.signature-compared",
        name="SignatureCompared",
        description="L3a: Python/TS 接口名比对结果（matched/missing/extra）",
        parent=f"{DOMAIN}.interface-spec",
        tags=["translated", "type-checked", "style-checked", "signature-verified"],
    ),

    # ── L3b: 行为测试结果 ──
    Format(
        id=f"{DOMAIN}.behavioral-tested",
        name="BehavioralTested",
        description="L3b: 固定模板 import 测试通过，接口可导入且类型正确",
        parent=f"{DOMAIN}.interface-spec",
        tags=["translated", "type-checked", "style-checked", "behavioral-tested"],
    ),

    # ── L4: 最终验证 ──
    Format(
        id=f"{DOMAIN}.verified-code",
        name="VerifiedCode",
        description="L4: LLM 语义裁判通过，设计意图等价 + 六元约束 + 惯用性全满足",
        parent=f"{DOMAIN}.checked-code",
        tags=["translated", "type-checked", "semantically-verified"],
        required_tags=["translated", "type-checked"],
    ),
]


def register_formats(registry: FormatRegistry) -> None:
    """将本域 Format 注册到全局 FormatRegistry。"""
    for fmt in FORMATS:
        if not registry.is_registered(fmt.id):
            registry.register(fmt)
