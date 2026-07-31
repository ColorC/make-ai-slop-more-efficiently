# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-06-21T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.rules.custom_llm_call.scanner.py"
"""Guardian 规则 — 自定义 LLM 调用反模式 (OMNI-094, 2026-06-21 用户立).

立档背景 (2026-06-21):
  写 poof 总控派发路由器时, 我 (Claude Code) 直接 `subprocess.run(["claude","-p",...])` 调本机
  claude code 做归类 —— 这是"自定义 LLM 调用", 绕过了 Omnicompany 的标准 worker 抽象。
  用户裁决: "请使用标准实现, 不要在 Omnicompany 里面出现自定义 LLM 调用, 自动解析对话内容的
  程序也必须是 team 或者 worker。这一点也要加入 guardian 守则。"

正确范式 (LLM-CALL-UNIFICATION 权威):
  - 所有业务 LLM 调用先按 Atlas `llm-workflow` Skill 选型, 并位于注册 Team/Worker/Agent 中。
  - 本机 claude code / codex 的一次性任务 → `run_external_agent_request(provider='claude-code'|'codex')`
    (packages/services/_core/agent/external_workers) —— 受审计、带 cwd + 权限模式的标准 worker。
  - 纯结构化 LLM 调用 → `runtime.llm.structured.call_json`(唯一权威)。
  - 文件型任务 → 统一 Agent 在同一会话内写文件、跑 lint、按错误修订并复验。
  绝不裸 `subprocess`/`os.system`/`os.popen` 直起 claude/codex CLI 当 LLM 使。
  绝不在 docs/scripts/.omni/sandbox/仓根临时脚本直接调用 LLMClient/call_json/batch/agent。

扫描思路 (AST, 不是字符串包含 —— memory feedback_static_check_ast_not_string):
  文件含 subprocess.{run,Popen,call,check_output,check_call,getoutput,getstatusoutput}
    或 os.{system,popen} 调用, 且其命令(list 首元素 / 字符串首词)是 'claude' 或 'codex' CLI
    → 自定义 LLM 调用。唯一豁免: external_workers/ 适配器本身(它就是那条标准路径的实现)。
  非标准管线路径直接 import/call LLMClient、call_json、batch、AgentNodeLoop、
    ConfigurableAgent、run_external_agent_request → 临时 LLM 包装器。
"""
from __future__ import annotations

import ast

from ._base import FileContext, GuardianRule, _is_external, _not_graveyard

_PATH_EXEMPTIONS: tuple[str, ...] = (
    # 唯一合法的"裸起外部 agent CLI"处: 受审计的 worker 适配器本身
    "packages/services/_core/agent/external_workers/",
    "_archive/",
    "_graveyard/",
    "vendors/",
    "/tests/",
    "/test_",
)

_CLI_NAMES = ("claude", "codex")
_SUBPROCESS_FUNCS = {"run", "Popen", "call", "check_output", "check_call", "getoutput", "getstatusoutput"}
_LLM_FACILITY_MODULES: tuple[str, ...] = (
    "omnicompany.runtime.llm",
    "omnicompany.packages.services._core.agent",
)
_LLM_FACILITY_NAMES: frozenset[str] = frozenset({
    "LLMClient",
    "call_json",
    "run_parallel_items",
    "run_external_agent_request",
    "ExternalAgentRunRequest",
    "AgentNodeLoop",
    "ConfigurableAgent",
    "AgentSpec",
})
_STANDARD_PIPELINE_PATHS: tuple[str, ...] = (
    "src/omnicompany/packages/",
    "src/omnicompany/runtime/",
    "src/omnicompany/cli/commands/worker.py",
)


def _is_path_exempt(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    return any(ex in p for ex in _PATH_EXEMPTIONS)


def _head_is_cli(value: str) -> bool:
    """命令头(可执行名)是否是 claude/codex(剥路径与 .cmd/.exe 后缀)。"""
    head = value.strip().split()[0:1]
    if not head:
        return False
    name = head[0].replace("\\", "/").split("/")[-1].lower()
    name = name.removesuffix(".cmd").removesuffix(".exe").removesuffix(".bat")
    return name in _CLI_NAMES


def _arg_is_llm_cli(arg: ast.AST) -> bool:
    # list/tuple 形: ["claude","-p",...] / ["codex","exec",...] —— 看首元素
    if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
        first = arg.elts[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return _head_is_cli(first.value)
        return False
    # 字符串命令形: "claude -p ..." / "codex exec ..."
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return _head_is_cli(arg.value)
    return False


def _file_has_raw_llm_cli(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        is_sub = False
        if isinstance(func, ast.Attribute):
            if (func.attr in _SUBPROCESS_FUNCS and isinstance(func.value, ast.Name)
                    and func.value.id == "subprocess"):
                is_sub = True
            elif (func.attr in ("system", "popen") and isinstance(func.value, ast.Name)
                  and func.value.id == "os"):
                is_sub = True
        elif isinstance(func, ast.Name) and func.id in _SUBPROCESS_FUNCS:
            is_sub = True  # `from subprocess import run/Popen/...`
        if is_sub and _arg_is_llm_cli(node.args[0]):
            return True
    return False


def _is_standard_pipeline_path(ctx: FileContext) -> bool:
    p = ctx.path.replace("\\", "/")
    return any(p.startswith(prefix) for prefix in _STANDARD_PIPELINE_PATHS)


def _file_uses_llm_facility(tree: ast.AST) -> bool:
    """是否直接依赖统一 LLM/Agent 设施。

    设施本身没有问题；问题是把它们放进非 Team/Worker/Agent 的临时脚本。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(_LLM_FACILITY_MODULES):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name.startswith(_LLM_FACILITY_MODULES) for alias in node.names):
                return True
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in _LLM_FACILITY_NAMES:
                return True
            if isinstance(func, ast.Attribute) and func.attr in _LLM_FACILITY_NAMES:
                return True
    return False


def _check_custom_llm_call(ctx: FileContext) -> bool:
    if _is_external(ctx) or not _not_graveyard(ctx):
        return False
    if not ctx.path.endswith(".py") or _is_path_exempt(ctx):
        return False
    content = ctx.content or ""
    if not content or "noqa-OMNI-094" in content:
        return False
    has_cli_hint = (
        ("subprocess" in content or "os.system" in content or "os.popen" in content)
        and ("claude" in content or "codex" in content)
    )
    has_facility_hint = any(token in content for token in _LLM_FACILITY_NAMES)
    if not has_cli_hint and not has_facility_hint:
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    if has_cli_hint and _file_has_raw_llm_cli(tree):
        return True
    return not _is_standard_pipeline_path(ctx) and _file_uses_llm_facility(tree)


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-094",
        name="custom-llm-call",
        severity="HIGH",
        description=(
            "禁止自定义 LLM 调用: 裸 subprocess/os 直起 claude/codex，或在非标准管线路径直接调用 "
            "LLMClient/call_json/batch/Agent。正确: 先按 Atlas llm-workflow Skill 选型，业务实现放入注册 "
            "Team/Worker/Agent；一次性文件工作走受审计的 omni worker run。文件校验由同一 Agent 会话 "
            "写入、lint、修订、复验。唯一豁免: 统一 runtime/agent 设施、标准 package 和测试/归档。"
        ),
        check=_check_custom_llm_call,
        disposition=["warn"],
        message_template=(
            "{path}: 非标准 LLM/Agent 调用。先用 Atlas llm-workflow Skill 选型；"
            "把复用逻辑放进注册 Team/Worker/Agent，文件型一次性任务走 omni worker run 并在同会话 lint 修复。"
            "确属统一设施实现 → 加 # noqa-OMNI-094 并说明理由。"
        ),
        certainty="absolute",
    ),
]

__all__ = [
    "RULES",
    "_check_custom_llm_call",
    "_file_has_raw_llm_cli",
    "_file_uses_llm_facility",
]
