# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=config
# [OMNI] material_id="material:core.repair.shared_utils.ast_diff_engine.py"
"""repair.workers._shared — Router 修复子管线的共享工具 (AST 分析 + diff 应用)。

这些是 Router 修复 9 Worker 之间共享的纯函数工具库 (不是 Worker), 放在 _shared.py
以便每个 Worker 文件保持聚焦。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path


_DEFAULT_SOURCE_ROOT = Path(__file__).parents[5]  # omnicompany/src/omnicompany
_REPAIR_PENDING_DIR = Path(__file__).parents[6] / "data" / "doctor" / "repair" / "pending"
_APPLIED_DIR = _REPAIR_PENDING_DIR.parent / "applied"
_BACKUP_DIR = _REPAIR_PENDING_DIR.parent / "backups"
_MODEL = "qwen3.6-plus"


def extract_class_docstring(class_source: str) -> str:
    """从类源码中提取类 docstring（第一个字符串字面量）。"""
    try:
        tree = ast.parse(class_source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    return node.body[0].value.value.strip()
    except Exception:
        pass
    return ""


def analyze_direct_accesses(run_source: str) -> list[dict]:
    """AST 分析 run() 中对 input_data 的直接下标访问 (input_data["key"])。

    返回每个访问点的信息:
      - key: 被访问的键名
      - line: 行号 (相对于 run_source)
      - context: 该行代码 (用于判断语义)
      - usage_type: "assign_to_dict_call" / "iterate" / "index" / "plain"
      - crash_if_missing: True (KeyError) / True (None 会 crash)
      - crash_if_empty: True (空列表 / 空 dict 会导致后续操作失败)
    """
    results: list[dict] = []
    try:
        tree = ast.parse(run_source)
        lines = run_source.splitlines()
    except Exception:
        return results

    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        val = node.value
        if not (isinstance(val, ast.Name) and val.id == "input_data"):
            continue
        slc = node.slice
        if not isinstance(slc, ast.Constant):
            continue
        key = slc.value
        line_no = node.lineno
        line_text = lines[line_no - 1].strip() if line_no <= len(lines) else ""

        usage_type = "plain"
        crash_if_empty = False
        if "dict(" in line_text and f'input_data["{key}"]' in line_text:
            usage_type = "assign_to_dict_call"
            crash_if_empty = False
        elif line_text.lstrip().startswith("for ") and f'input_data["{key}"]' in line_text:
            usage_type = "iterate"
            crash_if_empty = False
        elif f'input_data["{key}"][' in line_text:
            usage_type = "index"
            crash_if_empty = True
        else:
            usage_type = "plain"
            crash_if_empty = False

        results.append({
            "key": key,
            "line": line_no,
            "context": line_text,
            "usage_type": usage_type,
            "crash_if_missing": True,
            "crash_if_empty": crash_if_empty,
        })

    return results


def extract_pipeline_node_desc(pipeline_brief: dict | None, source_root: Path) -> str:
    """从 pipeline 文件中提取本 Router 节点的 ValidatorSpec.description。

    用于 DESCRIPTION 补全: 管线的 validator 描述往往比类本身 DESCRIPTION 更精确。
    """
    if not pipeline_brief:
        return ""
    node_id = pipeline_brief.get("node_id")
    pipeline_id = pipeline_brief.get("pipeline_id", "")
    if not node_id:
        return ""

    pipeline_files: list[Path] = []
    for pat in ("pipeline.py", "*_pipeline.py", "pipeline_*.py"):
        for p in source_root.rglob(pat):
            if "__pycache__" not in str(p) and "_graveyard" not in str(p):
                pipeline_files.append(p)

    for pf in pipeline_files:
        try:
            content = pf.read_text(encoding="utf-8", errors="ignore")
            if node_id not in content and pipeline_id not in content:
                continue
            tree = ast.parse(content)
        except Exception:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name not in ("AnchorSpec", "TransformerSpec", "ValidatorSpec"):
                continue

            this_id = None
            for kw in node.keywords:
                if kw.arg == "id" and isinstance(kw.value, ast.Constant):
                    this_id = kw.value.value

            if this_id != node_id and func_name != "ValidatorSpec":
                continue

            for kw in node.keywords:
                if kw.arg == "description":
                    try:
                        val = ast.literal_eval(kw.value)
                        if isinstance(val, str) and val.strip():
                            return val.strip()
                    except Exception:
                        pass

    return ""


def extract_diff(response: str) -> str | None:
    """从 LLM 响应中提取 ```diff...``` 代码块。"""
    m = re.search(r"```diff\s*\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r"(--- a/.*?(?=```|\Z))", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    return None


def parse_diff_hunks(diff: str) -> list[dict]:
    """将 unified diff 解析为 hunks 列表。

    每个 hunk: {"removed": [str], "added": [str], "context": [str]}
    其中 removed 是去掉前导 `-` 的行, added 是去掉前导 `+` 的行,
    context 是去掉前导 ` ` 的上下文行 (仅 removed 为空时用于锚定)。
    """
    hunks: list[dict] = []
    current: dict | None = None

    for line in diff.splitlines():
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            if current and (current["removed"] or current["added"]):
                hunks.append(current)
            current = {"removed": [], "added": [], "context": []}
            continue
        if current is None:
            current = {"removed": [], "added": [], "context": []}

        if line.startswith("-"):
            current["removed"].append(line[1:])
        elif line.startswith("+"):
            current["added"].append(line[1:])
        else:
            ctx_line = line[1:] if line.startswith(" ") else line
            current["context"].append(ctx_line)
            if current["removed"] or current["added"]:
                hunks.append(current)
                current = {"removed": [], "added": [], "context": [ctx_line]}

    if current and (current["removed"] or current["added"]):
        hunks.append(current)

    return hunks


def apply_diff_to_source(source: str, diff: str) -> tuple[str, list[str]]:
    """将 unified diff 应用到源文本。

    返回 (new_source, errors)。errors 非空表示有 hunk 应用失败。
    策略:
      1. 对每个 hunk, 先尝试直接替换 removed_lines 为 added_lines
      2. 若失败, 尝试带上下文锚定后再替换
      3. 若仍失败, 跳过并记录错误 (不阻塞其他 hunk)
    """
    errors: list[str] = []
    result = source

    hunks = parse_diff_hunks(diff)
    for hunk in hunks:
        removed = hunk["removed"]
        added = hunk["added"]

        if not removed and not added:
            continue

        old_str = "\n".join(removed)
        new_str = "\n".join(added)

        if old_str and old_str in result:
            result = result.replace(old_str, new_str, 1)
        elif old_str:
            src_lines = result.splitlines(keepends=True)
            rem_lines = [l.rstrip() for l in removed]
            matched_start = -1
            for i in range(len(src_lines) - len(rem_lines) + 1):
                if all(src_lines[i + j].rstrip("\n\r") == rem_lines[j]
                       for j in range(len(rem_lines))):
                    matched_start = i
                    break
            if matched_start >= 0:
                end = matched_start + len(rem_lines)
                eol = "\n"
                new_lines = [l + eol for l in new_str.splitlines()]
                result_lines = src_lines[:matched_start] + new_lines + src_lines[end:]
                result = "".join(result_lines)
            else:
                errors.append(f"无法匹配删除块（前20字）: {old_str[:40]!r}")
        elif added and not removed:
            ctx = hunk.get("context", [])
            if ctx:
                anchor = ctx[-1].rstrip()
                if anchor in result:
                    result = result.replace(anchor, anchor + "\n" + "\n".join(added), 1)
                else:
                    errors.append(f"插入锚点未找到: {anchor[:40]!r}")

    return result, errors
