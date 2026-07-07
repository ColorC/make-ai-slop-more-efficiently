# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-06-23T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.rules.self_built_agent.scanner.py"
"""Guardian 规则 — 自造 agent 反模式 (OMNI-095, 2026-06-23 用户立).

立档背景 (2026-06-23):
  我 (Claude Code) 沉淀 team 时, 嫌统一 AgentNodeLoop(async + bus) 嵌进 worker 麻烦,
  自己手搓了个同步 ReAct 工具循环 (runtime/agent/tool_agent.py) —— 这是"重复造 agent",
  是最核心设施之一的二重实现。用户裁决:
  "你重建了一个 agent? 为什么不用统一实现? 如果统一实现不够好, 你就改进统一实现, 不要二重。"
  "不要搞什么第二套了, 我们用统一设施执行。"

正确范式 (统一 agent 权威 = packages/services/_core/agent):
  - 让 worker 调 LLM + 工具干活 → 走统一 AgentNodeLoop:
      · 子类化 AgentNodeLoop / ConfigurableAgent(配置驱动), 或
      · 普通 worker 借 `launch.run_json_agent(...)` 启动统一 AgentNodeLoop。
    工具走 SingleToolRouter / TOOL_REGISTRY(read_file/grep/glob/list_dir/write_file/bash...)。
  - 统一 agent 不够用 → **改进它**(加工具 / 加启动器 / 扩配置), 不 fork 第二套。
  - 外部自建(我来建 team/worker)更要严格照统一设施与模板, 不许另起炉灶。

扫描思路 (AST 启发, 非字符串包含 —— memory feedback_static_check_ast_not_string):
  非豁免 .py 文件里出现"手搓 agent 循环": 一个 for/while 循环体内直接调 LLM
  (call_json / LLMClient(...).call / new LLMClient), 且文件含工具回合 token
  (tool_use/tool_result/tool_calls/tool_name/tool_dispatch), 且**未**引用统一
  AgentNodeLoop/run_json_agent/ConfigurableAgent → 疑似自造 agent, needs_judgment 复核。
  情况多, 故 certainty=needs_judgment + disposition=warn(不硬卡, 交人/GuardianAgent 判)。
"""
from __future__ import annotations

import ast

from ._base import FileContext, GuardianRule, _is_external, _is_scratch, _not_graveyard

# 统一 agent 框架自身 + LLM/工具执行框架 —— 这些就是"标准实现", 一律豁免
_PATH_EXEMPTIONS: tuple[str, ...] = (
    "packages/services/_core/agent/",   # 统一 AgentNodeLoop 实现本身
    "runtime/agent/",                   # 旧 agent loop 框架(TeamRunner 版)
    "runtime/exec/",                    # 工具执行器框架
    "runtime/llm/",                     # LLM 客户端框架
    "runtime/routing/",                 # Router 框架
    "_archive",
    "_graveyard/",
    "vendors/",
    "/tests/",
    "/test_",
)
# 工具回合 token —— 手搓 agent 循环才会拼这些
_TOOL_TOKENS: tuple[str, ...] = ("tool_use", "tool_result", "tool_calls", "tool_name", "tool_dispatch")
# 引用了统一 agent —— 说明在用标准设施(可能在循环里调 run_json_agent 等), 豁免
_UNIFIED_TOKENS: tuple[str, ...] = ("AgentNodeLoop", "run_json_agent", "ConfigurableAgent")


def _is_path_exempt(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    return any(ex in p for ex in _PATH_EXEMPTIONS)


def _is_llm_call(node: ast.AST) -> bool:
    """这个 Call 是不是一次 LLM 调用(call_json / new LLMClient / x.call(messages=...))。"""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name) and f.id in ("call_json", "LLMClient"):
        return True
    if isinstance(f, ast.Attribute):
        if f.attr == "call_json":
            return True
        if f.attr == "call":
            kw = {k.arg for k in node.keywords}
            if "messages" in kw or "system" in kw:
                return True
    return False


def _loop_has_llm_call(loop_node: ast.AST) -> bool:
    for n in ast.walk(loop_node):
        if n is loop_node:
            continue
        if _is_llm_call(n):
            return True
    return False


def _file_has_handrolled_agent(tree: ast.AST) -> bool:
    """文件里是否有"循环体内直调 LLM"的手搓 agent 循环。"""
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.While, ast.AsyncFor)) and _loop_has_llm_call(node):
            return True
    return False


def _check_self_built_agent(ctx: FileContext) -> bool:
    if _is_external(ctx) or not _not_graveyard(ctx) or _is_scratch(ctx):
        return False
    if not ctx.path.endswith(".py") or _is_path_exempt(ctx):
        return False
    content = ctx.content or ""
    if not content or "noqa-OMNI-095" in content:
        return False
    # 快速过滤: 既要有 LLM 调用迹象, 又要有工具回合 token, 否则不必 parse
    if "call_json" not in content and "LLMClient" not in content and ".call(" not in content:
        return False
    if not any(t in content for t in _TOOL_TOKENS):
        return False
    # 已引用统一 agent → 在用标准设施, 豁免
    if any(t in content for t in _UNIFIED_TOKENS):
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    return _file_has_handrolled_agent(tree)


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-095",
        name="self-built-agent",
        severity="MEDIUM",
        description=(
            "文件手搓 agent / ReAct 循环(循环体内直调 LLM + 自拼 tool_use/tool_result 回合), 却未用"
            "统一 AgentNodeLoop —— 重复造 agent(最核心设施之一)。正确: 子类化 AgentNodeLoop/"
            "ConfigurableAgent, 或普通 worker 走 services._core.agent.launch.run_json_agent; 统一不够用就"
            "改进它别 fork。用户 2026-06-23 立: 不要重复造 agent, 仅限改进强化, 用统一设施执行。"
            "豁免: 统一 agent 框架自身(packages/services/_core/agent, runtime/agent|exec|llm|routing)。"
        ),
        check=_check_self_built_agent,
        disposition=["warn"],
        message_template=(
            "{path}: 疑似手搓 agent 循环(循环内直调 LLM + 自管 tool 回合)。"
            "改用统一 AgentNodeLoop(子类化 / ConfigurableAgent / launch.run_json_agent), "
            "统一设施不够用就改进它, 别造第二套。确属 agent 框架基础设施 → 加 # noqa-OMNI-095。"
        ),
        certainty="needs_judgment",
    ),
]

__all__ = ["RULES", "_check_self_built_agent", "_file_has_handrolled_agent"]
