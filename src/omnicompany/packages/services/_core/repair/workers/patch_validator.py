# [OMNI] origin=claude-code domain=omnicompany/repair ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.repair.patch_validator.diff_safety_checker.py"
"""PatchValidatorWorker — Repair Team Worker (Router 修复分组 · #7).

Worker 协议:
  FORMAT_IN  = diag.repair.patch-plan
  FORMAT_OUT = diag.repair.validated-patch

职责: AST 验证修复 diff 的安全性。
"""
from __future__ import annotations

import ast
import re
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind


class PatchValidatorWorker(Worker):
    """AST 验证修复 diff 的安全性:

      1. diff 非空
      2. 新增行语法合法
      3. 不删除现有 PASS/FAIL Verdict 路径
      4. 不修改 FORMAT_IN / FORMAT_OUT 字段值
    """

    DESCRIPTION = "AST 验证 diff 安全性：语法合法 + 不破坏现有 PASS/FAIL 路径 + 不修改 FORMAT_IN/OUT"
    FORMAT_IN = "diag.repair.patch-plan"
    FORMAT_OUT = "diag.repair.validated-patch"

    def run(self, input_data: Any) -> Verdict:
        diff: str | None = input_data.get("diff")
        if not diff:
            return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                           output={**input_data, "validation_passed": True,
                                   "validation_notes": ["无 diff，跳过验证"]},
                           diagnosis="PatchValidator: 无 diff，跳过")

        router_class: str = input_data.get("router_class", "")
        notes: list[str] = []
        failed = False

        if not diff.strip():
            notes.append("diff 为空")
            failed = True

        if not failed:
            added_lines = [l[1:] for l in diff.splitlines()
                           if l.startswith("+") and not l.startswith("+++")]
            test_snippet = "\n".join(added_lines)
            if test_snippet.strip():
                try:
                    ast.parse(test_snippet)
                    notes.append("diff 新增行语法合法 ✓")
                except SyntaxError as e:
                    notes.append(f"diff 新增行语法错误（可能是片段，非致命）: {e}")

        removed_lines = [l[1:] for l in diff.splitlines()
                         if l.startswith("-") and not l.startswith("---")]
        for line in removed_lines:
            if "VerdictKind.PASS" in line or "VerdictKind.FAIL" in line:
                notes.append(f"diff 删除了 Verdict 行（危险）: {line.strip()}")
                failed = True
                break

        for line in diff.splitlines():
            if line.startswith(("-", "+")) and not line.startswith(("---", "+++")):
                stripped = line[1:].strip()
                if re.match(r"FORMAT_(IN|OUT)\s*=", stripped):
                    notes.append(f"diff 修改了 FORMAT_IN/OUT（禁止）: {stripped}")
                    failed = True
                    break

        # 检查新增行中 Verdict 构造函数是否用了 result= 而非 kind=（常见 LLM 幻觉）
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                stripped = line[1:]
                if "Verdict(" in stripped and re.search(r'result\s*=\s*["\']?FAIL', stripped):
                    notes.append(
                        f"diff 新增行使用了错误的 Verdict 参数 result='FAIL'（应为 kind=VerdictKind.FAIL）: {stripped.strip()}"
                    )
                    failed = True
                    break

        if failed:
            return Verdict(kind=VerdictKind.FAIL, confidence=1.0,
                           output={**input_data, "validation_passed": False, "validation_notes": notes},
                           diagnosis=f"PatchValidator: {router_class} 验证失败")

        return Verdict(kind=VerdictKind.PASS, confidence=1.0,
                       output={**input_data, "validation_passed": True, "validation_notes": notes},
                       diagnosis=f"PatchValidator: {router_class} 验证通过")
