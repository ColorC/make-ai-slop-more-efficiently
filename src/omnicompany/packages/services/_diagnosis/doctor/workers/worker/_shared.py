# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=helper
# [OMNI] material_id="material:diagnosis.doctor.worker.worker.shared_ast_tools.py"
"""Worker 诊断子域共享 AST 工具 + 模式常量 (Stage 3 Clean Migration 2026-04-22)."""
from __future__ import annotations

import ast
import logging
from pathlib import Path

logger = logging.getLogger("omnicompany.doctor.router")


# 默认 source root (omnicompany 项目的 src/omnicompany/)
DEFAULT_SOURCE_ROOT = Path("e:/WindowsWorkspace/omnicompany/src/omnicompany")


# 已知模型名称模式 (R-11 检测硬编码模型名)
KNOWN_MODEL_PATTERNS = (
    "gpt-4", "gpt-3.5", "gpt4", "gpt3",
    "claude-3", "claude-2", "claude-1",
    "qwen", "deepseek", "gemini", "mistral", "llama",
    "text-davinci", "o1-preview", "o3-",
)

# LLM 调用的方法名模式 (检测 LLMClient 使用)
LLM_CALL_METHODS = ("client.call", "llm.call", "self.client.call", "self.llm.call", "LLMClient(")

# 直接 LLM import (R-04 检测)
DIRECT_LLM_IMPORTS = (
    "import openai", "import anthropic",
    "from openai import", "from anthropic import",
)

# 文件写操作 (R-06 检测)
FILE_WRITE_PATTERNS = (
    "open(", ".write_text(", ".write_bytes(",
    "shutil.copy(", "shutil.move(",
)

# LLM 协议泄漏模式 (R-12 检测)
PROTOCOL_LEAK_PATTERNS = (
    'block.type == "tool_use"', "block.type == 'tool_use'",
    "response.choices[", ".choices[0].message",
    ".message.tool_calls",
)


# ════════════════════════════════════════════════════════════════
# AST 工具函数
# ════════════════════════════════════════════════════════════════

def classify_self_assignment(var_name: str, context: str) -> str:
    """将 self.xxx = ... 分类为 INFO / SUSPICIOUS / LIKELY_VIOLATION."""
    info_patterns = (
        "_logger", "_log", "logger", "log",
        "last_token_count", "last_tokens", "_model", "_client",
        "_source_root", "_default", "_config",
    )
    violation_patterns = (
        "cache", "history", "counter", "count",
        "last_result", "last_output", "session",
        "state", "pending", "buffer",
    )
    lower = var_name.lower()
    for p in info_patterns:
        if p in lower:
            return "INFO"
    for p in violation_patterns:
        if p in lower:
            return "LIKELY_VIOLATION"
    return "SUSPICIOUS"


def get_call_repr(node: ast.expr) -> str:
    """将 Call.func AST 节点转换为可读字符串, 如 'self.client.call'."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{get_call_repr(node.value)}.{node.attr}"
    return ""


def get_line_context(source_lines: list[str], lineno: int, radius: int) -> str:
    """提取 lineno 行前后 radius 行的上下文 (1-indexed)."""
    if not source_lines or lineno <= 0:
        return ""
    start = max(0, lineno - 1 - radius)
    end = min(len(source_lines), lineno + radius)
    return "\n".join(source_lines[start:end])


def extract_vk_from_expr(node: ast.expr) -> list[str]:
    """从表达式中递归提取所有 VerdictKind.XXX 的 XXX 值 (支持三元/if-else)."""
    kinds = []
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in ("VerdictKind",):
            kinds.append(node.attr)
    elif isinstance(node, ast.IfExp):
        kinds.extend(extract_vk_from_expr(node.body))
        kinds.extend(extract_vk_from_expr(node.orelse))
    elif isinstance(node, ast.BoolOp):
        for v in node.values:
            kinds.extend(extract_vk_from_expr(v))
    return kinds


def extract_verdict_pattern(call_node: ast.Call) -> dict:
    """从 Verdict(...) 调用提取 kind/confidence/diagnosis/granted_tags."""
    result = {"kind": None, "confidence": None, "diagnosis": None, "granted_tags": []}
    for kw in call_node.keywords:
        if kw.arg == "kind":
            if isinstance(kw.value, ast.Attribute):
                result["kind"] = kw.value.attr
            elif isinstance(kw.value, ast.Name):
                result["kind"] = kw.value.id
        elif kw.arg == "confidence":
            try:
                result["confidence"] = ast.literal_eval(kw.value)
            except Exception:
                pass
        elif kw.arg == "diagnosis":
            try:
                result["diagnosis"] = ast.literal_eval(kw.value)
            except Exception:
                result["diagnosis"] = "(f-string or expr)"
        elif kw.arg == "granted_tags":
            try:
                result["granted_tags"] = ast.literal_eval(kw.value)
            except Exception:
                result["granted_tags"] = []
    return result


def classify_except_handling(handler: ast.ExceptHandler) -> str:
    """分类 except 块的处理方式."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
            repr_ = get_call_repr(node.value.func)
            if repr_ == "Verdict":
                for kw in node.value.keywords:
                    if kw.arg == "kind":
                        if isinstance(kw.value, ast.Attribute) and kw.value.attr == "PASS":
                            return "return_pass"
                        if isinstance(kw.value, ast.Attribute) and kw.value.attr == "FAIL":
                            return "return_fail"
        if isinstance(node, ast.Raise):
            return "raise"
    has_log = any(
        isinstance(n, ast.Call) and "log" in get_call_repr(n.func).lower()
        for n in ast.walk(handler)
        if isinstance(n, ast.Call)
    )
    if has_log:
        return "log_only"
    if not list(handler.body):
        return "ignore"
    return "log_only"


def is_router_class(class_node: ast.ClassDef, source_text: str) -> bool:
    """判断是否是 Router 子类 (Router/LLMRouter/AgentNodeLoop)."""
    router_bases = {"Router", "LLMRouter", "AgentNodeLoop"}
    for base in class_node.bases:
        name = get_call_repr(base)
        if name in router_bases:
            return True
    return (
        "DESCRIPTION" in source_text
        and "FORMAT_IN" in source_text
        and "FORMAT_OUT" in source_text
        and "def run" in source_text
    )


def count_run_lines(func_node: ast.FunctionDef) -> int:
    """估算 run() 行数 (无 source_lines 时)."""
    if hasattr(func_node, "end_lineno"):
        return func_node.end_lineno - func_node.lineno + 1
    return 0


def extract_router_ast(
    class_node: ast.ClassDef,
    source_lines: list[str],
) -> dict:
    """从 ClassDef AST 节点提取 Router 类的完整信号 (7 类 AST 衍生信号)."""
    # ── 类变量字面量 ──
    class_vars: dict = {}
    class_var_kinds: dict[str, str] = {}

    for stmt in class_node.body:
        if isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                if not (isinstance(t, ast.Name) and t.id in (
                    "DESCRIPTION", "FORMAT_IN", "FORMAT_OUT",
                    "INPUT_KEYS", "OUTPUT_KEYS", "PASSTHROUGH",
                )):
                    continue
                if t.id in ("FORMAT_IN", "FORMAT_OUT"):
                    if isinstance(stmt.value, ast.JoinedStr):
                        class_vars[t.id] = None
                        class_var_kinds[t.id] = "fstring"
                    elif isinstance(stmt.value, ast.List):
                        try:
                            class_vars[t.id] = ast.literal_eval(stmt.value)
                        except Exception:
                            class_vars[t.id] = None
                        class_var_kinds[t.id] = "list"
                    else:
                        try:
                            class_vars[t.id] = ast.literal_eval(stmt.value)
                            class_var_kinds[t.id] = "literal"
                        except Exception:
                            class_vars[t.id] = None
                            class_var_kinds[t.id] = "dynamic"
                else:
                    try:
                        class_vars[t.id] = ast.literal_eval(stmt.value)
                    except Exception:
                        class_vars[t.id] = None

    # ── __init__ 参数 ──
    init_params: list[str] = []
    run_source: str = ""
    run_line_count: int = 0
    run_start_line: int = 0
    run_is_async: bool = False

    _FN_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

    for stmt in class_node.body:
        if not isinstance(stmt, _FN_TYPES):
            continue
        if stmt.name == "__init__":
            init_params = [a.arg for a in stmt.args.args if a.arg != "self"]
        elif stmt.name == "run":
            run_is_async = isinstance(stmt, ast.AsyncFunctionDef)
            run_start_line = stmt.lineno
            if source_lines:
                end_line = stmt.end_lineno if hasattr(stmt, "end_lineno") else stmt.lineno
                run_source = "\n".join(source_lines[stmt.lineno - 1: end_line])
                run_line_count = end_line - stmt.lineno + 1
            else:
                run_line_count = count_run_lines(stmt)

    # ── 7 类 AST 衍生信号 ──
    llm_calls: list[dict] = []
    self_assignments: list[dict] = []
    input_keys_accessed: list[str] = []
    output_keys_produced: list[str] = []
    verdict_patterns: list[dict] = []
    exception_patterns: list[dict] = []

    for stmt in class_node.body:
        if not isinstance(stmt, _FN_TYPES):
            continue
        is_run = stmt.name == "run"

        for node in ast.walk(stmt):
            # llm_calls: 扫描类内所有方法
            if isinstance(node, ast.Call):
                func_repr = get_call_repr(node.func)
                if any(pat in func_repr for pat in ("client.call", "llm.call", "LLMClient")):
                    ctx = get_line_context(source_lines, getattr(node, "lineno", 0), 2)
                    llm_calls.append({
                        "line": getattr(node, "lineno", 0),
                        "context": ctx,
                        "method": stmt.name,
                    })

            if not is_run:
                continue

            # self_assignments
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                        var_name = t.attr
                        ctx = get_line_context(source_lines, getattr(node, "lineno", 0), 1)
                        classification = classify_self_assignment(var_name, ctx)
                        self_assignments.append({
                            "var": var_name,
                            "line": getattr(node, "lineno", 0),
                            "classification": classification,
                            "context": ctx,
                        })

            # input_keys_accessed
            if isinstance(node, ast.Subscript):
                if isinstance(node.value, ast.Name) and node.value.id == "input_data":
                    try:
                        key = ast.literal_eval(node.slice)
                        if isinstance(key, str) and key not in input_keys_accessed:
                            input_keys_accessed.append(key)
                    except Exception:
                        pass
            if isinstance(node, ast.Call):
                func_repr = get_call_repr(node.func)
                if func_repr in ("input_data.get",) and node.args:
                    try:
                        key = ast.literal_eval(node.args[0])
                        if isinstance(key, str) and key not in input_keys_accessed:
                            input_keys_accessed.append(key)
                    except Exception:
                        pass

            # output_keys_produced
            if isinstance(node, ast.Call):
                func_repr = get_call_repr(node.func)
                if func_repr == "Verdict":
                    for kw in node.keywords:
                        if kw.arg == "output" and isinstance(kw.value, ast.Dict):
                            for k in kw.value.keys:
                                try:
                                    key = ast.literal_eval(k)
                                    if isinstance(key, str) and key not in output_keys_produced:
                                        output_keys_produced.append(key)
                                except Exception:
                                    pass

            # verdict_patterns
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
                func_repr = get_call_repr(node.value.func)
                if func_repr == "Verdict":
                    vp = extract_verdict_pattern(node.value)
                    verdict_patterns.append(vp)

            # verdict_kind_variable_assigns
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        inferred = extract_vk_from_expr(node.value)
                        for kind_val in inferred:
                            verdict_patterns.append({
                                "kind": kind_val,
                                "confidence": None,
                                "diagnosis": None,
                                "granted_tags": [],
                                "_inferred_from_variable": True,
                            })

        if is_run:
            for node in ast.walk(stmt):
                if isinstance(node, ast.ExceptHandler):
                    exc_type = "Exception"
                    if node.type is not None:
                        if isinstance(node.type, ast.Name):
                            exc_type = node.type.id
                        elif isinstance(node.type, ast.Attribute):
                            exc_type = f"{ast.dump(node.type)}"
                    handling = classify_except_handling(node)
                    ctx = get_line_context(source_lines, getattr(node, "lineno", 0), 2)
                    exception_patterns.append({
                        "exception_type": exc_type,
                        "handling": handling,
                        "context": ctx,
                    })

    router_kind = "LLM" if llm_calls else "RULE"

    return {
        "description": class_vars.get("DESCRIPTION"),
        "format_in": class_vars.get("FORMAT_IN"),
        "format_out": class_vars.get("FORMAT_OUT"),
        "format_in_kind": class_var_kinds.get("FORMAT_IN", "literal"),
        "format_out_kind": class_var_kinds.get("FORMAT_OUT", "literal"),
        "input_keys": class_vars.get("INPUT_KEYS"),
        "output_keys": class_vars.get("OUTPUT_KEYS"),
        "passthrough": class_vars.get("PASSTHROUGH"),
        "init_params": init_params,
        "run_is_async": run_is_async,
        "run_source": run_source,
        "run_line_count": run_line_count,
        "ast_signals": {
            "router_kind": router_kind,
            "llm_calls": llm_calls,
            "self_assignments": self_assignments,
            "input_keys_accessed": input_keys_accessed,
            "output_keys_produced": output_keys_produced,
            "verdict_patterns": verdict_patterns,
            "exception_patterns": exception_patterns,
        },
    }
