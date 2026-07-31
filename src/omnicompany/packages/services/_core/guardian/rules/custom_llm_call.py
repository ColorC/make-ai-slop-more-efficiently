# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-06-21T00:00:00Z type=config
# [OMNI] material_id="material:core.guardian.rules.custom_llm_call.scanner.py"
"""Guardian 规则 — 自定义 LLM 调用反模式 (OMNI-094, 2026-06-21 用户立).

立档背景 (2026-06-21):
  写 poof 总控派发路由器时, 我 (Claude Code) 直接 `subprocess.run(["claude","-p",...])` 调本机
  claude code 做归类 —— 这是"自定义 LLM 调用", 绕过了 Omnicompany 的标准 worker 抽象。
  用户裁决: "请使用标准实现, 不要在 Omnicompany 里面出现自定义 LLM 调用, 自动解析对话内容的
  程序也必须是 team 或者 worker。这一点也要加入 guardian 守则。"

正确范式 (LLM-CALL-UNIFICATION 权威):
  - 本机 claude code / codex 的一次性任务 → `run_external_agent_request(provider='claude-code'|'codex')`
    (packages/services/_core/agent/external_workers) —— 受审计、带 cwd + 权限模式的标准 worker。
  - 纯结构化 LLM 调用 → `runtime.llm.structured.call_json`(唯一权威)。
  绝不裸 `subprocess`/`os.system`/`os.popen` 直起 claude/codex CLI 当 LLM 使。

扫描思路 (AST, 不是字符串包含 —— memory feedback_static_check_ast_not_string):
  文件含 subprocess.{run,Popen,call,check_output,check_call,getoutput,getstatusoutput}
    或 os.{system,popen} 调用, 且其命令(list 首元素 / 字符串首词)是 'claude' 或 'codex' CLI
    → 自定义 LLM 调用。唯一豁免: external_workers/ 适配器本身(它就是那条标准路径的实现)。
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


def _check_custom_llm_call(ctx: FileContext) -> bool:
    if _is_external(ctx) or not _not_graveyard(ctx):
        return False
    if not ctx.path.endswith(".py") or _is_path_exempt(ctx):
        return False
    content = ctx.content or ""
    if not content or "noqa-OMNI-094" in content:
        return False
    # 快速过滤: 同时要有 进程调用迹象 + claude/codex 字样, 否则不必 parse
    if "subprocess" not in content and "os.system" not in content and "os.popen" not in content:
        return False
    if "claude" not in content and "codex" not in content:
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    return _file_has_raw_llm_cli(tree)


RULES: list[GuardianRule] = [
    GuardianRule(
        id="OMNI-094",
        name="custom-llm-call",
        severity="HIGH",
        description=(
            "文件裸 subprocess / os.system / os.popen 直起 claude/codex CLI 当 LLM 用 —— 自定义 LLM 调用, "
            "绕过标准 worker。正确: 本机 claude code/codex 一次性任务走 "
            "run_external_agent_request(provider='claude-code'|'codex'); 纯结构化 LLM 走 "
            "runtime.llm.call_json。用户 2026-06-21 立: 不许自定义 LLM 调用, 自动解析对话必须 team/worker。"
            "唯一豁免: packages/services/_core/agent/external_workers/ 适配器本身。"
        ),
        check=_check_custom_llm_call,
        disposition=["warn"],
        message_template=(
            "{path}: 裸 subprocess/os 直调 claude/codex CLI = 自定义 LLM 调用。"
            "改用 run_external_agent_request(provider='claude-code'/'codex') 或 runtime.llm.call_json。"
            "确属 worker 基础设施 → 加 # noqa-OMNI-094。"
        ),
        certainty="high",
    ),
]

__all__ = ["RULES", "_check_custom_llm_call", "_file_has_raw_llm_cli"]
