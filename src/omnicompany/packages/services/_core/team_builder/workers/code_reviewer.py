# [OMNI] origin=claude-code domain=services/team_builder/workers ts=2026-04-24T00:00:00Z type=worker
# [OMNI] material_id="material:core.team_builder.code_reviewer.external_audit.py"
"""CodeReviewer (V3.2 P6 · 2026-04-24) — 他评 Worker.

feedback_forced_self_review_split_to_external 的实装:
  CodeGenerator 的自产自检不可靠 (csv_to_md 实测 LLM 在 MaterialDesigner 产 schema 要 `header`,
  在 WorkerCodeOrchestrator 产 Worker 代码输出 `headers` · 自己审不到).
  独立他评 Worker · 跨环节一致性审.

职责 (HARD · 纯规则 · 快):
1. 每 worker 的 Verdict(output={...}) 字段 ⊆ 对应 Material.json_schema.required
2. Worker class 名一致性: worker_id → 期望的 ClassName 必须在代码中出现
3. Worker file 数量与 worker_design_detailed 数量一致 (不多不少)
4. (未来扩: import 虚构 / ServiceBus 反模式 · 后者已由 aggregator 内置 lint)

FAIL 时抛**可诊断**信息 (observed_keys / required / missing), 让 JUMP 回上游改.

FORMAT_IN (composite):
  code_package + worker_design_detailed + material_design_detailed
FORMAT_OUT:
  code_review_report = {issues: [...], verdict: "pass" | "fail"}

本 worker **不 patch 产物** · 只报告. 后续可接 routing policy 决定 JUMP 到哪里重产.
"""
from __future__ import annotations

import ast
from typing import Any, ClassVar

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from .code_gen_hard import _class_name_for, _module_name_for


def _dict_literal_keys(node: ast.AST) -> set[str]:
    """从 ast.Dict / ast.Call(dict(...)) 中抽 str key. 不递归."""
    keys: set[str] = set()
    if isinstance(node, ast.Dict):
        for k in node.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.add(k.value)
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        for kw in node.keywords:
            if kw.arg:
                keys.add(kw.arg)
    return keys


def _build_var_assignments(code: str) -> dict[str, set[str]]:
    """扫全文, 找所有 `<var> = {...}` / `<var> = {**other, ...}` 的 dict 赋值 · 收集 keys.

    复合形: `x = {"a": 1}; x["b"] = 2; x["c"] = 3` · 要把 b/c 也算进 x 的 keys.
    支持 `x.update({...})` 扩展.
    """
    var_keys: dict[str, set[str]] = {}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return var_keys

    for node in ast.walk(tree):
        # x = {...} or x = dict(...)
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name):
                ks = _dict_literal_keys(node.value)
                if ks:
                    var_keys.setdefault(tgt.id, set()).update(ks)
        # x["k"] = v
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                sl = tgt.slice
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    var_keys.setdefault(tgt.value.id, set()).add(sl.value)
        # x.update({...})
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            if isinstance(node.func.value, ast.Name) and node.args:
                ks = _dict_literal_keys(node.args[0])
                if ks:
                    var_keys.setdefault(node.func.value.id, set()).update(ks)
    return var_keys


def _extract_output_keys_from_verdict_calls(code: str) -> set[str]:
    """静态分析 · 找 `Verdict(output=...)` 里出现的所有 dict keys.

    V2 · 2026-04-25: 支持**变量引用** · 反例来自 csv_to_md #11 MarkdownWriter:
    ```python
    sink = {"content": gfm, "row_count": n}
    return Verdict(output=sink)   # <-- V1 抓不到 · 只抓到别处字面 dict 的 keys
    ```

    V2 处理:
    1. dict literal: `Verdict(output={"a": ...})` → {a}
    2. dict(...) call: `Verdict(output=dict(a=1))` → {a}
    3. **变量引用**: `Verdict(output=sink)` → 回溯找 sink 的 assignments · 收集 keys
    4. `{**other, "a": 1}` merge: 可选递归 (当前取自身声明的 keys)
    """
    keys: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return keys
    var_keys = _build_var_assignments(code)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = None
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
        if func_name != "Verdict":
            continue
        for kw in node.keywords:
            if kw.arg != "output":
                continue
            val = kw.value
            # Level 1/2: dict literal or dict(...) call
            direct = _dict_literal_keys(val)
            if direct:
                keys.update(direct)
            # Level 3: variable reference → lookup assignments
            elif isinstance(val, ast.Name) and val.id in var_keys:
                keys.update(var_keys[val.id])
            # Level 4: {**other, ...} — 先取自身字面 keys (递归跨 var 可后续扩)
            elif isinstance(val, ast.Dict):
                # already handled by _dict_literal_keys above, no-op
                pass
    return keys


def _find_class_names(code: str) -> set[str]:
    """AST 抽所有 class 名."""
    names: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
    return names


def _find_input_get_keys(code: str) -> set[str]:
    """AST 抽 Worker code 里所有 `xxx.get("key")` / `xxx["key"]` 调用的字符串 key.

    主要关注 input_data 和 payload 的 get() 调用. 用于 D1 检查:
    Worker 读的 key 是否在 FORMAT_IN Material 的 json_schema.properties 里.

    Keep false-positive 宽松 (任何 `.get(字符串)` 都收) · 降低漏报.
    """
    keys: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return keys

    for node in ast.walk(tree):
        # Pattern 1: x.get("key") or x.get("key", default)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            if node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    keys.add(first.value)
        # Pattern 2: x["key"]
        elif isinstance(node, ast.Subscript):
            sl = node.slice
            if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                keys.add(sl.value)
    return keys


def _worker_details_list(input_data: dict) -> list[dict]:
    wd = input_data.get("_from_worker_designer") or {}
    if isinstance(wd, dict):
        details = wd.get("details")
        if isinstance(details, list):
            return [d for d in details if isinstance(d, dict)]
    if isinstance(wd, list):
        return [d for d in wd if isinstance(d, dict)]
    return []


def _material_details_map(input_data: dict) -> dict[str, dict]:
    md = input_data.get("_from_material_designer") or {}
    details = []
    if isinstance(md, dict):
        details = md.get("details") or []
    if isinstance(md, list):
        details = md
    out: dict[str, dict] = {}
    for m in details:
        if isinstance(m, dict) and m.get("material_id"):
            out[m["material_id"]] = m
    return out


def _code_package(input_data: dict) -> dict:
    cp = input_data.get("_from_code_aggregator") or input_data.get("_from_code_generator") or {}
    if not isinstance(cp, dict):
        return {}
    return cp


class CodeReviewer(Worker):
    """V3.2 P6 · HARD 他评 · 跨 Material schema ⇔ Worker code 的一致性审.

    实测捕获的反模式 (2026-04-24 csv_to_md · LLM 不一致):
    - Material schema 要 `header` (单数) · Worker 代码输出 `headers` (复数)
    - Material.json_schema.required 里字段 Worker 代码 never emits
    - Worker class 名不匹配 worker_id
    """

    DESCRIPTION: ClassVar[str] = (
        "V3.2 P6 · HARD 他评 Worker · 跨 Material schema required + Worker code output dict 一致性 + "
        "class name 对齐 + file 存在性. FAIL 带可诊断 issues list, 让上游 JUMP 回重产 (不 patch 产物)."
    )
    FORMAT_IN: ClassVar = [
        "team_builder.material.code_package",
        "team_builder.material.worker_design_detailed",
        "team_builder.material.material_design_detailed",
    ]
    FORMAT_IN_MODE: ClassVar[str] = "and"
    FORMAT_OUT: ClassVar[str] = "team_builder.material.code_review_report"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            return Verdict(kind=VerdictKind.FAIL, output={}, diagnosis="input_data must be dict")

        worker_details = _worker_details_list(input_data)
        material_map = _material_details_map(input_data)
        code_package = _code_package(input_data)
        files = code_package.get("files") or {}
        if not isinstance(files, dict):
            files = {}

        issues: list[dict] = []

        # Check 1: 每 worker 的 code 文件存在 + output keys ⊆ required
        for w in worker_details:
            worker_id = w.get("worker_id") or ""
            if not worker_id:
                continue
            if str(w.get("impl_type") or "").upper() == "EXISTING":
                # WorkerDesigner has already imported and type-checked this live
                # Worker class. It intentionally has no generated local file.
                continue
            module = _module_name_for(worker_id)
            rel_path = f"workers/{module}.py"
            expected_class = _class_name_for(worker_id)
            code = files.get(rel_path, "")

            if not code:
                issues.append({
                    "severity": "critical",
                    "worker_id": worker_id,
                    "category": "missing_file",
                    "issue": f"Worker 代码文件不存在: {rel_path}",
                    "fix_hint": f"WorkerCodeOrchestrator 重产 · 确保文件名为 {rel_path}",
                })
                continue

            # Class name check
            classes = _find_class_names(code)
            if expected_class not in classes:
                issues.append({
                    "severity": "critical",
                    "worker_id": worker_id,
                    "category": "class_name_mismatch",
                    "issue": f"Worker 代码未定义 class {expected_class} · 实际 classes: {sorted(classes)}",
                    "fix_hint": f"重产 · 类名必须是 {expected_class} (_class_name_for(worker_id) 约定)",
                    "expected_class": expected_class,
                    "observed_classes": sorted(classes),
                })

            # Output keys ⊆ Material.required check
            format_out = w.get("format_out")
            if isinstance(format_out, str) and format_out in material_map:
                material = material_map[format_out]
                required = (material.get("json_schema") or {}).get("required") or []
                if required:
                    output_keys = _extract_output_keys_from_verdict_calls(code)
                    # 忽略 run_time internal keys
                    observed = {k for k in output_keys if not k.startswith("_")}
                    missing = [r for r in required if r not in observed]
                    if missing and observed:  # observed 非空才报 (避免空 dict 误报)
                        issues.append({
                            "severity": "critical",
                            "worker_id": worker_id,
                            "category": "output_schema_mismatch",
                            "issue": (
                                f"Worker 代码 Verdict.output 缺 Material {format_out} required 字段: {missing} · "
                                f"实际产 {sorted(observed)}"
                            ),
                            "fix_hint": (
                                f"MaterialDesigner 或 WorkerCodeOrchestrator 之一错 · "
                                f"LLM 在 schema 用 {required} 但 worker code 用 {sorted(observed)} · "
                                f"建议 JUMP 回 MaterialDesigner + WorkerCodeOrchestrator 统一命名"
                            ),
                            "required": required,
                            "observed": sorted(observed),
                            "material_id": format_out,
                        })

            # D1 (V3.2 · 2026-04-24): Worker 读的 key 必须 ⊆ FORMAT_IN Material schema properties
            # 反例: Material csv_source required=['file_path'], Worker 用 payload.get('path') → runtime FAIL
            format_in = w.get("format_in")
            format_in_list = format_in if isinstance(format_in, list) else [format_in] if isinstance(format_in, str) else []
            allowed_input_keys: set[str] = set()
            for fi in format_in_list:
                if isinstance(fi, str) and fi in material_map:
                    props = (material_map[fi].get("json_schema") or {}).get("properties") or {}
                    if isinstance(props, dict):
                        allowed_input_keys.update(props.keys())
            if allowed_input_keys:
                input_keys_used = _find_input_get_keys(code)
                # 只关心看起来像"从 input 读业务字段"的 key · 白名单常见非业务 key
                NOISE = {
                    "kind", "output", "diagnosis", "details", "content", "type", "properties",
                    "required", "input_data", "FORMAT_IN", "FORMAT_OUT", "verdict", "files",
                    "value", "path_sep", "sep", "strip", "mode", "encoding",
                    # 常见 Python builtin lookups
                    "value", "key", "name", "items", "keys", "values",
                }
                # 只看疑似业务字段 key (3-40 字符 · 非 noise · 非下划线开头)
                candidate_used = {
                    k for k in input_keys_used
                    if 2 <= len(k) <= 40 and not k.startswith("_") and k not in NOISE
                }
                # 不在 FORMAT_IN schema 里的 key = 可能的 bug
                suspicious = sorted(candidate_used - allowed_input_keys)
                # 进一步过滤: 只报在 Material.required 里没有的 · 但代码在读的
                # 收紧: 只检查 required 缺失 (避免警报)
                all_required_in: set[str] = set()
                for fi in format_in_list:
                    if isinstance(fi, str) and fi in material_map:
                        all_required_in.update((material_map[fi].get("json_schema") or {}).get("required") or [])
                # Worker 必须读 required · 并且不应读不存在的
                required_not_read = [r for r in all_required_in if r not in input_keys_used]
                # 仅报 "读了外部 key · 但它不是任何 FORMAT_IN property": 强信号
                bad_extract = [k for k in suspicious if k in candidate_used and any(
                    fi in material_map for fi in format_in_list
                )]
                # 降噪: 若 Worker 用常规的顶层 key 读 (如 'csv_to_md.csv_source'), 不报
                format_in_ids = {fi for fi in format_in_list if isinstance(fi, str)}
                bad_extract = [k for k in bad_extract if k not in format_in_ids]

                if required_not_read and allowed_input_keys:
                    issues.append({
                        "severity": "critical",
                        "worker_id": worker_id,
                        "category": "input_key_not_read",
                        "issue": (
                            f"Worker 代码未读 FORMAT_IN Material {format_in_list} required 字段: {required_not_read} · "
                            f"实际读的 key (含 noise): {sorted(input_keys_used)[:10]}..."
                        ),
                        "fix_hint": (
                            f"Worker code 必须读 required 字段 {required_not_read} · "
                            f"或 MaterialDesigner 改 required 为 Worker 实际读的字段"
                        ),
                        "required_not_read": required_not_read,
                        "format_in": format_in_list,
                    })

        # Check 2: Worker file 数量与 worker_design_detailed 一致
        expected_files = {
            f"workers/{_module_name_for(w.get('worker_id',''))}.py"
            for w in worker_details
            if w.get("worker_id")
            and str(w.get("impl_type") or "").upper() != "EXISTING"
        }
        actual_worker_files = {f for f in files if f.startswith("workers/") and f.endswith(".py") and f != "workers/__init__.py"}
        extra = actual_worker_files - expected_files
        missing_files = expected_files - actual_worker_files
        if extra:
            issues.append({
                "severity": "warning",
                "category": "extra_files",
                "issue": f"产了 worker_design_detailed 里没有的 .py: {sorted(extra)}",
                "fix_hint": "清理或让 MaterialDesigner 补充对应 worker_design",
            })
        if missing_files:
            issues.append({
                "severity": "critical",
                "category": "missing_files",
                "issue": f"worker_design_detailed 要求但 bundle 缺: {sorted(missing_files)}",
                "fix_hint": "WorkerCodeOrchestrator 重产",
            })

        # 总判定: critical → FAIL, warning → PASS (带 warnings)
        critical = [i for i in issues if i.get("severity") == "critical"]
        warnings = [i for i in issues if i.get("severity") == "warning"]
        kind = VerdictKind.FAIL if critical else VerdictKind.PASS

        return Verdict(
            kind=kind,
            output={
                "issues": issues,
                "verdict": kind.value,
                "critical_count": len(critical),
                "warning_count": len(warnings),
            },
            diagnosis=(
                f"code_review · {len(critical)} critical · {len(warnings)} warnings · "
                f"{len(worker_details)} workers checked"
                + (f" · 最 critical: {critical[0].get('issue', '')[:120]}" if critical else "")
            ),
        )
