# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-26T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.rules.manual_llm_output_parse.scanner.py"
"""Guardian 规则 — LLM 输出手解反模式 (OMNI-080, 2026-04-26).

立档背景 (2026-04-26 用户在 Stage E P5.1 抓到):
  我 (Claude Code, L2) 在写 4 个 stage_e 设计点提取/验收 worker 时, 全部用了
  `===EVIDENCE===` 硬编码 marker + `json.loads(text)` 手解 LLM finish tool 的 result
  字符串. 这违反 memory `feedback_no_manual_parse_use_structured_output` (2026-04-25
  L1 立的跨项目铁律).

  正确范式: 自定义 SubmitXxxRouter 子类 SingleToolRouter, 给 INPUT_SCHEMA 定义结构化字段,
  LLM 调用时给 args 字典, _execute(args, ctx) 直接消费 dict — 不做任何 text parse.

参考:
  - 反例: stage_e_design_point_extractor / _test_coverage_mapper /
          _test_gap_filler / _semantic_acceptance_gate 都有 _parse_evidence
  - 正例: packages/domains/demogame/ux/routers/prefab_rule_extraction_loop.py
          的 SubmitReportRouter (TOOL_NAME='submit_report' + INPUT_SCHEMA)

扫描思路 (AST):
  文件含 字符串字面量 `===EVIDENCE===` 或 `===END===` 或 markdown fence regex
    + 同文件含 `json.loads` 调用
    → 高度可疑 (80%+ 违反).

  certainty=high (确定的反模式), disposition=warn — 因为有少量合理用例
  (e.g. 解析外部数据源 JSON, 不是 LLM 输出), LLM 复核区分.
"""
from __future__ import annotations

import ast
import re

from ._base import FileContext, GuardianRule, _is_external, _not_graveyard


# ══════════════════════════════════════════════════════════════
# 通用豁免
# ══════════════════════════════════════════════════════════════

_PATH_EXEMPTIONS: tuple[str, ...] = (
    # 架构永久: services/agent 是 SingleToolRouter 等基础设施定义所在,
    # 它内部有解析 LLM tool 调用的合法处理 (ToolDispatch 等)
    "src/omnicompany/packages/services/agent/routers/extract_result.py",
    # 归档/外部
    "_archive/", "_graveyard/", "vendors/",
)


def _is_path_exempt(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    return any(p.startswith(ex) or ex in p for ex in _PATH_EXEMPTIONS)


def _common_skip(ctx: FileContext) -> bool:
    if _is_external(ctx) or not _not_graveyard(ctx):
        return True
    if not ctx.path.endswith(".py"):
        return True
    if _is_path_exempt(ctx):
        return True
    if not ctx.content:
        return True
    return False


# ══════════════════════════════════════════════════════════════
# OMNI-080 · LLM 输出 text parse 反模式
# ══════════════════════════════════════════════════════════════

# 反模式 marker 字符串字面量 (LLM agent worker 常用)
_EVIDENCE_MARKERS: tuple[str, ...] = (
    "===EVIDENCE===",
    "===END===",
    "```json",   # markdown fence (跟 json.loads 共现 → 反模式高度可疑)
)


def _file_has_marker_literal(tree: ast.AST, content: str) -> str | None:
    """ast.walk 找 ast.Constant (字符串) 是否含 evidence marker.
    返回首个命中的 marker 或 None.

    用 ast.walk 找节点, 不是 `"X" in code` (memory feedback_static_check_ast_not_string).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for m in _EVIDENCE_MARKERS:
                if m in node.value:
                    return m
    return None


def _file_has_json_loads_call(tree: ast.AST) -> bool:
    """ast.walk 找 ast.Call 调 json.loads."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # json.loads(...)
        if isinstance(func, ast.Attribute) and func.attr == "loads":
            if isinstance(func.value, ast.Name) and func.value.id == "json":
                return True
        # 形 `from json import loads` 后裸调 loads(...) — 同类反模式
        if isinstance(func, ast.Name) and func.id == "loads":
            return True
    return False


def _file_has_re_search_fence(content: str) -> bool:
    """文件含正则匹 markdown fence 模式 (re.search/findall + ```json)."""
    return bool(re.search(r"re\.(search|findall|match).*?```json", content, re.DOTALL))


def _check_manual_evidence_parse(ctx: FileContext) -> bool:
    """OMNI-080 粗筛: 同文件含 evidence marker 字面量 + json.loads 调用.

    粗筛保守 — 命中即报候选, LLM 复核区分:
    - 真反模式 (LLM tool result 文本手解) → confirmed
    - 合法用例 (解析外部 JSON 数据源 / 配置文件 等) → dismissed
    """
    if _common_skip(ctx):
        return False
    content = ctx.content or ""
    if "noqa-OMNI-080" in content:
        return False
    # 快速过滤: 必须出现至少一个 marker 字串 + json
    if "EVIDENCE" not in content and "```json" not in content and "json.loads" not in content:
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False

    has_marker = _file_has_marker_literal(tree, content)
    has_json_loads = _file_has_json_loads_call(tree)
    has_re_fence = _file_has_re_search_fence(content)

    # 反模式组合:
    # (a) 硬编码 EVIDENCE marker 字面量 + json.loads → 强信号
    # (b) re.search(```json) + json.loads → 强信号
    if (has_marker and has_json_loads) or (has_re_fence and has_json_loads):
        return True
    return False


# ══════════════════════════════════════════════════════════════
# RULES
# ══════════════════════════════════════════════════════════════

RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-080",
        name="manual-llm-output-parse",
        severity="MEDIUM",
        description=(
            "文件含 LLM evidence marker 字符串字面量 (===EVIDENCE=== / ===END=== / ```json) "
            "+ json.loads 调用 — 疑似手解 LLM finish tool 的 result 文本. "
            "正确做法: 自定义 SubmitXxxRouter 子类 SingleToolRouter, INPUT_SCHEMA 定义结构化字段, "
            "LLM 给 args dict, _execute 直接消费 — 不做 text parse. "
            "memory: feedback_no_manual_parse_use_structured_output (2026-04-25 跨项目铁律). "
            "正例: packages/domains/demogame/ux/routers/prefab_rule_extraction_loop.py SubmitReportRouter."
        ),
        check=_check_manual_evidence_parse,
        disposition=["warn"],
        message_template=(
            "{path}: 同文件含 evidence marker 字面量 + json.loads. "
            "若是 LLM tool result 手解 → 改 SubmitXxxRouter + INPUT_SCHEMA 结构化 args. "
            "若解析外部 JSON 数据源 → 加 # noqa-OMNI-080 注释或路径加豁免."
        ),
        certainty="high",
    ),
]
