# [OMNI] origin=omnifactory domain=omnifactory/repair ts=2026-04-10T00:00:00Z
# [OMNI] material_id="material:core.repair.repair_planner_format_patcher_format_repair_agent.routers_legacy.py"
"""repair.routers — Format 自动修复管线的 Router 实现

  RepairPlannerRouter      (LLM)  分析健康档案，输出字段修复 delta JSON
  FormatPatcherRouter      (HARD) 用 AST 定位 Format() 块，按 delta 做字符串 patch
  FormatRepairAgentLoop    (composite) 包装上述两步的迭代修复循环
"""

from __future__ import annotations

import ast
import json
import logging
import re
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.runtime.routing.router import Router

logger = logging.getLogger(__name__)

_DEFAULT_SOURCE_ROOT = Path("e:/WindowsWorkspace/omnifactory/src/omnifactory")


# ════════════════════════════════════════════════════════════════
# _run_diagnosis — Format 诊断工具函数（通过 PipelineRunner + SQLiteBus）
# ════════════════════════════════════════════════════════════════

def _run_diagnosis(format_id: str, source_root: str) -> dict:
    """对 format_id 跑完整诊断链，返回 health_record dict（含 extracted 字段供 Patcher 定位源文件）。

    通过 PipelineRunner + SQLiteBus 执行，所有节点 I/O 均记录到事件总线。
    health_record 的 extracted 字段由 HealthWriterRouter 直接输出（doctor/routers.py 已修正）。
    """
    from omnicompany.packages.services._diagnosis.doctor.run import _run_hard_diagnosis
    return _run_hard_diagnosis(format_id, source_root)


# ════════════════════════════════════════════════════════════════
# _patch_format_source — AST 精准 patch Format() 块
# ════════════════════════════════════════════════════════════════

def _to_repr(value: Any) -> str:
    """将 Python 值转为代码字面量字符串。"""
    if isinstance(value, str):
        # 对长字符串使用括号换行格式，必须转义所有 \ 和 "
        if "\n" in value or len(value) > 60:
            escaped = value.replace('\\', '\\\\').replace('"', '\\"')
            return f'(\n    "{escaped}"\n)'
        return repr(value)
    if isinstance(value, list):
        items = ", ".join(repr(x) for x in value)
        return f"[{items}]"
    if isinstance(value, dict):
        return repr(value)
    return repr(value)


def _char_offset(lines: list[str], line_idx: int, col_byte: int) -> int:
    """将 AST (line_idx, col_byte) 转换为 Python 字符串的字符偏移量。

    Python 3.8+ 的 AST col_offset / end_col_offset 是 UTF-8 字节偏移，
    而 Python 字符串切片按 Unicode 字符（code point）计。
    对含 CJK 字符的代码，两者不同，必须先把字节偏移转成字符偏移。
    """
    char_base = sum(len(l) for l in lines[:line_idx])
    line = lines[line_idx] if line_idx < len(lines) else ""
    # 把行编码为 UTF-8，截到字节偏移，再 decode 得到字符数
    char_col = len(line.encode("utf-8")[:col_byte].decode("utf-8", errors="replace"))
    return char_base + char_col


def _patch_format_source(source_text: str, format_id: str, delta: dict) -> tuple[str, list[str]]:
    """
    在 source_text 中找到 Format(id=format_id, ...) 块，
    对 delta 中的每个字段做精准替换（AST 字符级定位）。

    返回 (patched_source, list_of_applied_fields)。
    未应用的字段（找不到 Format 块）返回原文 + 空列表。
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return source_text, []

    lines = source_text.splitlines(keepends=True)

    # 找到 Format(id=format_id) 调用节点
    format_call: ast.Call | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_format = (isinstance(func, ast.Name) and func.id == "Format") or (
            isinstance(func, ast.Attribute) and func.attr == "Format"
        )
        if not is_format:
            continue
        for kw in node.keywords:
            if kw.arg == "id":
                try:
                    if ast.literal_eval(kw.value) == format_id:
                        format_call = node
                        break
                except Exception:
                    pass
        if format_call:
            break

    if not format_call:
        return source_text, []

    applied: list[str] = []
    # 逐个字段处理；每次 patch 后重新解析（保持 AST 一致性）
    current = source_text
    for field, new_value in delta.items():
        current, ok = _apply_single_field(current, format_id, field, new_value)
        if ok:
            applied.append(field)

    return current, applied


def _apply_single_field(source_text: str, format_id: str, field: str, new_value: Any) -> tuple[str, bool]:
    """
    对单个字段做 patch。
    - 字段存在：用 AST 精准定位值的字符范围，替换为 new_repr
    - 字段不存在：在 Format() 最后一个 kwarg 后插入新行
    """
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return source_text, False

    lines = source_text.splitlines(keepends=True)

    # 找 Format(id=format_id)
    format_call: ast.Call | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_format = (isinstance(func, ast.Name) and func.id == "Format") or (
            isinstance(func, ast.Attribute) and func.attr == "Format"
        )
        if not is_format:
            continue
        for kw in node.keywords:
            if kw.arg == "id":
                try:
                    if ast.literal_eval(kw.value) == format_id:
                        format_call = node
                        break
                except Exception:
                    pass
        if format_call:
            break

    if not format_call:
        return source_text, False

    new_repr = _to_repr(new_value)

    # 查找该 field 是否已存在于 kwargs
    for kw in format_call.keywords:
        if kw.arg != field:
            continue
        # 精准替换值的字符范围（包括 kw.value 的完整 span）
        val = kw.value
        start = _char_offset(lines, val.lineno - 1, val.col_offset)
        end = _char_offset(lines, val.end_lineno - 1, val.end_col_offset)
        patched = source_text[:start] + new_repr + source_text[end:]
        return patched, True

    # 字段不存在 — 在最后一个 kwarg 值结束位置后插入
    if not format_call.keywords:
        return source_text, False

    last_kw = format_call.keywords[-1]
    last_val = last_kw.value
    end = _char_offset(lines, last_val.end_lineno - 1, last_val.end_col_offset)

    # 检测缩进：取最后一个 kwarg 所在行的前导空格
    last_kw_line = lines[last_kw.value.lineno - 1] if lines else ""
    indent = re.match(r"(\s*)", last_kw_line).group(1)
    # 对齐到 kwarg 名（kwarg 本身的 col_offset）
    kw_indent = " " * last_kw.col_offset

    insert = f",\n{kw_indent}{field}={new_repr}"
    patched = source_text[:end] + insert + source_text[end:]
    return patched, True


# ════════════════════════════════════════════════════════════════
# RepairPlannerRouter — LLM 规划修复 delta
# ════════════════════════════════════════════════════════════════


class RepairPlannerRouter(Router):
    """调用 LLM 分析 health_record 中的失败检查，输出字段修复 delta JSON。

    输入：repair.fmt.attempt（含 health_record + format source）
    输出：delta dict，key=字段名，value=修复后的值
    """

    DESCRIPTION = "LLM 分析 Format 健康失败项，输出字段修复 delta JSON"
    FORMAT_IN = "repair.fmt.attempt"
    FORMAT_OUT = "repair.fmt.attempt"

    _MODEL = "qwen3.6-plus"
    _SYSTEM = """\
你是 OmniCompany 的 Format 修复专家，请严格对照 standards/material.md 规范修复 Format 定义。

=== 核心标准（必须满足） ===

F-01 五要素 — description 必须包含全部五项：
  ① 关键字段的业务含义（不只是字段名/类型，要说明用途）
  ② 枚举值的业务约束（如有 enum/状态机）
  ③ 上游来源：由哪个节点/管线生产
  ④ 下游用途：被哪个具体节点（名称）消费
  ⑤ 最小合法示例（或说明数据形态）

F-02 — description 长度 >= 100 字符 [MUST]

F-07 — examples 中的示例必须通过 json_schema 验证（如有 schema）[SHOULD]

=== 修复原则 ===
- 只输出需要修改的字段，不输出无需修改的字段
- 字段名必须是 Format() 构造器的合法 kwarg：description / tags / examples / json_schema / parent（禁止修改 id）
- tags 必须包含域标签（如 "bw"、"guardian"、"doctor" 等，从 format_id 前缀推断）
- description 修复重点：
    * 补全下游节点名（F-01-④）——格式："下游用途：[节点名] 使用此数据做 XXX"
    * 补全字段业务语义（F-01-①）——说明每个关键字段的用途，而非仅重复字段名
    * 长度 >= 100 字符（F-02）
- examples 修复：提供覆盖 json_schema required 字段的最小合法示例
- 如果是终端输出节点（"终端输出"/"无后续"），下游用途写"终端输出，供用户/CI 读取"
- 输出严格 JSON，无 markdown，无注释：{"field1": value1, "field2": value2}
- 如果所有标准已满足，输出空对象：{}
"""

    def __init__(self, model: str | None = None):
        self._model = model or self._MODEL

    def run(self, input_data: Any) -> Verdict:
        health_record: dict = input_data.get("health_record", {})
        source_excerpt: str = input_data.get("source_excerpt", "")
        format_id: str = input_data.get("format_id", "")

        delta = self._plan(format_id, health_record, source_excerpt)

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output={**input_data, "delta": delta},
            diagnosis=f"RepairPlanner: delta_fields={list(delta.keys())}",
        )

    def _plan(self, format_id: str, health_record: dict, source_excerpt: str) -> dict:
        failing_checks = [
            c for c in health_record.get("checks", [])
            if not c.get("passed")
        ]
        if not failing_checks:
            return {}

        try:
            from omnicompany.runtime.llm.llm import LLMClient

            client = LLMClient(model=self._model)
            user_msg = (
                f"Format ID: {format_id}\n\n"
                f"当前源码（Format 定义段）：\n```python\n{source_excerpt}\n```\n\n"
                f"健康档案摘要（grade={health_record.get('health_grade')}）：\n"
                f"失败检查：\n"
                + "\n".join(
                    f"  - {c['check']}: {c.get('detail', '')} "
                    + (f"sub_checks={[s for s in c.get('sub_checks', []) if not s.get('passed')]}"
                       if c.get("sub_checks") else "")
                    for c in failing_checks
                )
                + "\n\n请输出修复 delta JSON。"
            )
            resp = client.call(
                messages=[{"role": "user", "content": user_msg}],
                system=self._SYSTEM,
            )
            raw = resp.content[0].text.strip()
            # 去除可能的 markdown 代码块包装
            if raw.startswith("```"):
                raw = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("`").strip()
            delta = json.loads(raw)
            # 安全检查：不允许修改 id
            delta.pop("id", None)
            return delta
        except Exception as e:
            logger.warning("RepairPlanner LLM call failed: %s", e)
            return {}


# ════════════════════════════════════════════════════════════════
# FormatPatcherRouter — 将 delta 写入 Format 源码文件
# ════════════════════════════════════════════════════════════════


class FormatPatcherRouter(Router):
    """将 LLM 给出的 delta 精准写入 Format() 源码定义，使用 guarded_write。

    输入：repair.fmt.attempt（含 delta + health_record.extracted.defined_in）
    输出：同 + patch_ok / patch_applied_fields / patch_error
    """

    DESCRIPTION = "将 delta JSON 字段精准写入 Format() 源码定义"
    FORMAT_IN = "repair.fmt.attempt"
    FORMAT_OUT = "repair.fmt.attempt"

    def run(self, input_data: Any) -> Verdict:
        delta: dict = input_data.get("delta", {})
        health_record: dict = input_data.get("health_record", {})
        format_id: str = input_data.get("format_id", "")
        source_root: str = input_data.get("source_root", "")

        extracted = health_record.get("extracted", {})
        defined_in: str = extracted.get("defined_in", "")

        if not delta:
            return Verdict(
                kind=VerdictKind.PASS,
                confidence=1.0,
                output={**input_data, "patch_ok": True, "patch_applied_fields": [], "patch_note": "delta 为空，跳过 patch"},
                diagnosis="FormatPatcher: delta empty, skip",
            )

        if not defined_in:
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={**input_data, "patch_ok": False, "patch_error": "无法确定 defined_in 路径"},
                diagnosis="FormatPatcher: no defined_in",
            )

        # defined_in 是相对于 source_root.parent 的路径（FormatExtractor 的约定）
        source_root_path = Path(source_root) if source_root else _DEFAULT_SOURCE_ROOT
        target_path = source_root_path.parent / defined_in
        if not target_path.exists():
            target_path = source_root_path / defined_in
        if not target_path.exists():
            target_path = Path(defined_in)
        if not target_path.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={**input_data, "patch_ok": False, "patch_error": f"文件不存在: {target_path}"},
                diagnosis=f"FormatPatcher: file not found {target_path}",
            )

        try:
            original = target_path.read_text(encoding="utf-8")
            patched, applied = _patch_format_source(original, format_id, delta)

            if not applied:
                return Verdict(
                    kind=VerdictKind.PASS,
                    confidence=1.0,
                    output={**input_data, "patch_ok": True, "patch_applied_fields": [], "patch_note": "未找到可 patch 字段"},
                    diagnosis="FormatPatcher: no fields applied",
                )

            from omnicompany.core.guarded_write import write_file
            write_file(
                target_path,
                patched,
                origin="omnifactory",
                domain="repair",
                node="format-patcher",
                purpose=f"LLM 修复 {format_id} Format 字段: {applied}",
            )

            return Verdict(
                kind=VerdictKind.PASS,
                confidence=1.0,
                output={**input_data, "patch_ok": True, "patch_applied_fields": applied},
                diagnosis=f"FormatPatcher: applied {applied}",
            )
        except Exception as e:
            logger.error("FormatPatcher failed: %s", e)
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output={**input_data, "patch_ok": False, "patch_error": str(e)},
                diagnosis=f"FormatPatcher error: {e}",
            )


# ════════════════════════════════════════════════════════════════
# FormatRepairAgentLoop — 修复迭代循环
# ════════════════════════════════════════════════════════════════


class FormatRepairAgentLoop(Router):
    """Format 修复 AgentLoop：诊断 → LLM 规划 → Patch → 重新诊断，循环至 A 级或达到上限。

    每轮迭代：
      1. 运行完整诊断（doctor 管线的 HARD 节点，不含 LLM desc_eval）
      2. 若 grade == 'A'，结束
      3. 提取 Format 源码段，调用 RepairPlannerRouter（LLM）生成 delta
      4. 调用 FormatPatcherRouter 将 delta 写入源文件
      5. 重复

    输出：repair.fmt.report
    """

    DESCRIPTION = "Format 修复 AgentLoop：诊断 → LLM 规划 → Patch，循环至 A 级"
    FORMAT_IN = "repair.fmt.request"
    FORMAT_OUT = "repair.fmt.report"

    def __init__(self, model: str | None = None):
        self._planner = RepairPlannerRouter(model=model)
        self._patcher = FormatPatcherRouter()

    def run(self, input_data: Any) -> Verdict:
        format_id: str = input_data["format_id"]
        source_root: str = input_data.get("source_root", str(_DEFAULT_SOURCE_ROOT))
        max_iter: int = int(input_data.get("max_iterations", 3))

        iterations: list[dict] = []
        initial_grade: str | None = None
        current_grade: str | None = None

        for i in range(1, max_iter + 1):
            # ── 1. 诊断 ──
            health_record = _run_diagnosis(format_id, source_root)
            grade = health_record.get("health_grade", "F")
            score = health_record.get("health_score", 0.0)

            if initial_grade is None:
                initial_grade = grade
            current_grade = grade

            iter_entry: dict = {
                "iter": i,
                "grade_before": grade,
                "score_before": score,
            }

            if grade == "A":
                iter_entry["note"] = "诊断通过，无需修复"
                iterations.append(iter_entry)
                break

            # ── 2. 提取 Format 源码段 ──
            source_excerpt = self._extract_source_excerpt(format_id, source_root, health_record)

            # ── 3. LLM 规划 delta ──
            attempt = {
                "format_id": format_id,
                "source_root": source_root,
                "health_record": health_record,
                "source_excerpt": source_excerpt,
                "iter": i,
            }
            plan_result = self._planner.run(attempt)
            plan_out = plan_result.output if hasattr(plan_result, "output") else plan_result
            delta: dict = plan_out.get("delta", {})
            iter_entry["delta"] = delta

            if not delta:
                iter_entry["note"] = "LLM 未给出修复建议，停止循环"
                iterations.append(iter_entry)
                break

            # ── 4. Patch 源文件 ──
            patch_result = self._patcher.run({**plan_out})
            patch_out = patch_result.output if hasattr(patch_result, "output") else patch_result
            patch_ok: bool = patch_out.get("patch_ok", False)
            iter_entry["patch_ok"] = patch_ok
            iter_entry["patch_applied_fields"] = patch_out.get("patch_applied_fields", [])
            if not patch_ok:
                iter_entry["patch_error"] = patch_out.get("patch_error", "unknown")

            # ── 5. 重新诊断，记录本轮 grade_after ──
            if patch_ok:
                health_after = _run_diagnosis(format_id, source_root)
                grade_after = health_after.get("health_grade", "F")
                current_grade = grade_after
            else:
                grade_after = grade
            iter_entry["grade_after"] = grade_after

            iterations.append(iter_entry)

            if grade_after == "A":
                break
            if not patch_ok:
                break  # patch 失败，不继续

        success = current_grade == "A"
        report = {
            "format_id": format_id,
            "source_root": source_root,
            "initial_grade": initial_grade or "?",
            "final_grade": current_grade or "?",
            "success": success,
            "iterations": iterations,
        }

        return Verdict(
            kind=VerdictKind.PASS if success else VerdictKind.FAIL,
            confidence=1.0,
            output=report,
            diagnosis=(
                f"RepairLoop: {format_id} {initial_grade}→{current_grade} "
                f"({'OK' if success else 'FAIL'}) in {len(iterations)} iter(s)"
            ),
        )

    def _extract_source_excerpt(self, format_id: str, source_root: str, health_record: dict) -> str:
        """提取 Format() 定义块的源码文本（最多 80 行）。"""
        extracted = health_record.get("extracted", {})
        defined_in: str = extracted.get("defined_in", "")
        if not defined_in:
            return ""

        source_root_path = Path(source_root) if source_root else _DEFAULT_SOURCE_ROOT
        target_path = source_root_path.parent / defined_in
        if not target_path.exists():
            target_path = source_root_path / defined_in
        if not target_path.exists():
            target_path = Path(defined_in)
        if not target_path.exists():
            return ""

        try:
            src = target_path.read_text(encoding="utf-8")
            tree = ast.parse(src)
            lines = src.splitlines(keepends=True)

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_format = (isinstance(func, ast.Name) and func.id == "Format") or (
                    isinstance(func, ast.Attribute) and func.attr == "Format"
                )
                if not is_format:
                    continue
                for kw in node.keywords:
                    if kw.arg == "id":
                        try:
                            if ast.literal_eval(kw.value) == format_id:
                                start = node.lineno - 1
                                end = min(node.end_lineno, start + 80)
                                return "".join(lines[start:end])
                        except Exception:
                            pass
        except Exception:
            pass
        return ""
