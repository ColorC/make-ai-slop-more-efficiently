# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-28T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.rules.prompt_anti_pattern_catalog.rule.py"
"""Guardian 规则 — AI 指令(prompt)反模式 (OMNI-090/091/092, 2026-04-28).

立档背景 (2026-04-25 用户在 docauthor 重构尾声提出):
  prompt 是给 LLM 的指令, 应该写"它要满足什么 (原则 / 约束 / 目标)",
  不该写"它该怎么做 (具体分类 / 具体案例 / 具体引用)". 三条反模式都是
  prompt 越界从'满足'走到了'怎么做'.

  详见: docs/plans/[2026-04-25]PROMPT-ANTIPATTERN-DETECTION/plan.md

跟 OMNI-080 (manual-llm-output-parse) 的区别:
  OMNI-080  → LLM **输出**端反模式 (模型产文本后手解)
  OMNI-090+ → LLM **输入**端反模式 (人写指令时坏写法)

为何不在规则引擎层粗筛触发:
  三条反模式分类靠语义判断, 硬规则 (字符串/AST) 命中即误报率极高.
  规则引擎层 check 一律返回 False (不进 patrol 警告输出),
  真正扫描走 workers/prompt_antipattern_scanner.py · 由它读 RULES 的
  description 作为分类指南喂给 LLM 复核, 一个文件一次 LLM 调用判三类.

  GuardianAuditStore 仍按 rule_id 分类落盘, 防重跑机制照常生效.
"""
from __future__ import annotations

from ._base import FileContext, GuardianRule


def _never_hit(ctx: FileContext) -> bool:
    """OMNI-090/091/092 共享占位粗筛 — 永远返回 False.

    规则引擎层不触发, 真正扫描由 PromptAntiPatternScanWorker 主导. 详见模块 docstring.
    """
    return False


# ══════════════════════════════════════════════════════════════
# RULES (概念注册 · 提供 description 供 reviewer prompt 引用)
# ══════════════════════════════════════════════════════════════

RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-090",
        name="prompt-context-pollution",
        severity="MEDIUM",
        description=(
            "AI 指令(prompt)含未自解释的外部 agent 上下文 / 引用易腐计划文档 / "
            "留下思考或修改痕迹. 三种典型: "
            "(a) 引用未在 prompt 内解释的概念 (如 '按上次说的方式做') · "
            "(b) 引用易腐 plan / feedback 文档 (如 '见 plan §三.2') — 文档变 prompt 跟着腐 · "
            "(c) 留思考或修改痕迹 (如 '// 之前是 A 但发现不行所以改 B'). "
            "原则: prompt 必须自洽, 任何概念在 prompt 内解释或不写, 不引用外部文档. "
            "memory: feedback_prompt_should_specify_what_not_how (2026-04-25 跨项目铁律)."
        ),
        check=_never_hit,
        disposition=["warn"],
        message_template=(
            "{path}: prompt 含意义不明的外部上下文 / 易腐计划引用 / 思考修改痕迹. "
            "改: 把概念在 prompt 内自解释, 不引外部文档, 不留修改痕迹."
        ),
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-091",
        name="prompt-clumsy-enumeration",
        severity="MEDIUM",
        description=(
            "AI 指令(prompt)对本无限的产出空间 (代码 / 设计 / 自然语言生成) "
            "做片面分类指导, 或对可枚举对象列出有重复/遗漏的清单. 两种典型: "
            "(a) 真无穷空间用'这种情况这样处理 / 那种情况那样处理'分类 — LLM 按例子套, 例子外即坏 · "
            "(b) 可枚举的事列出枚举但有重复 (两分支语义重叠) 或遗漏 (常见情况漏列). "
            "原则: prompt 写**原则**让 LLM 自判, 不替它分类. 真要给例子就给反例和边界. "
            "memory: feedback_prompt_should_specify_what_not_how (2026-04-25 跨项目铁律)."
        ),
        check=_never_hit,
        disposition=["warn"],
        message_template=(
            "{path}: prompt 对无限空间用片面分类示例, 或可枚举但有重复/遗漏. "
            "改: 写原则让 LLM 自判, 不要替它穷举."
        ),
        certainty="needs_judgment",
    ),
    GuardianRule(
        id="OMNI-092",
        name="prompt-outdated-specifics",
        severity="MEDIUM",
        description=(
            "AI 指令(prompt)用具体案例锁 LLM 解空间, 而案例过时 / 初级 / 仅一种解. 三种典型: "
            "(a) 案例本身过时 (用了已废弃 API / 已重构 service) · "
            "(b) 案例只是初级方案而问题有更优解 — LLM 看了照搬不思考 · "
            "(c) 一种问题有 N 种解, prompt 只举一种, LLM 偏向那一种被狭隘化. "
            "原则: prompt 给原则 + 约束 + 目标, 让 LLM 自决具体方案. "
            "memory: feedback_prompt_should_specify_what_not_how (2026-04-25 跨项目铁律)."
        ),
        check=_never_hit,
        disposition=["warn"],
        message_template=(
            "{path}: prompt 用具体案例锁 LLM 解空间. "
            "改: 给原则 + 约束 + 目标, 让 LLM 自决怎么做."
        ),
        certainty="needs_judgment",
    ),
]


__all__ = [
    "RULES",
    "_never_hit",
]
