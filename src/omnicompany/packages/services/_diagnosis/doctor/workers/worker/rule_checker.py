# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.worker.deterministic_rule_checker.py"
"""WorkerRuleChecker — 12 项 Rule 级检查 (HARD, Stage 3 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.worker.context
  FORMAT_OUT = diag.worker.det-checks

诊断目标: 对 Router run() 源码执行 12 项确定性检查 + R-07 AST 信号格式化.

规则清单 (对照 docs/standards/worker.md):
  - R-01  DESCRIPTION ≥ 50 字符
  - R-04  统一 LLMClient, 无直接 openai/anthropic import
  - R-04-async  run() 不应为 async (LAP 同步协议)
  - R-02-list   FORMAT_IN/OUT 不应为列表 (用 composite Format 替代)
  - R-05  Verdict 覆盖 PASS 和 FAIL
  - R-06  不直接写文件 (需走 guarded_write)
  - R-10  run() ≤ 80 行
  - R-11  无硬编模型名
  - R-12  无 LLM 协议泄漏 (block.type/choices[0])
  - R-13  RULE Router confidence = 1.0
  - R-input-unused  RULE Router run() 应从 input_data 读取键值
  - R-17  异常不假通过 (except → FAIL/raise, 不能 → PASS)
  - R-18  FORMAT_IN json_schema required 字段应在 run() 中被访问 + 双向覆盖
  - R-07 信号: self 赋值分类 (passed=null, 供 LLM 解读)

永远返回 PASS; 问题以 check 形式追加到 acc.checks, LLM 审计下游进一步解读.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import (
    DIRECT_LLM_IMPORTS,
    FILE_WRITE_PATTERNS,
    KNOWN_MODEL_PATTERNS,
    PROTOCOL_LEAK_PATTERNS,
)


class WorkerRuleChecker(Worker):
    """对 Router run() 源码执行 12 项 Rule 级检查 + R-07 AST 信号格式化."""

    DESCRIPTION = "对 Router run() 源码执行 12 项确定性检查 (R-01/04/04-async/02-list/05/06/10/11/12/13/17/18 + R-07 信号). R-18 FieldCoverage: run() 访问字段 vs FORMAT_IN json_schema properties 双向覆盖检查"
    FORMAT_IN = "diag.worker.context"
    FORMAT_OUT = "diag.worker.det-checks"
    INPUT_KEYS = ["worker_class", "extracted"]

    def run(self, input_data: Any) -> Verdict:
        worker_class: str = input_data["worker_class"]
        extracted: dict = input_data.get("extracted", {})
        run_source: str = extracted.get("run_source", "") or ""
        run_line_count: int = extracted.get("run_line_count", 0)
        description: str = extracted.get("description") or ""
        ast_signals: dict = extracted.get("ast_signals", {})
        router_kind: str = ast_signals.get("router_kind", "RULE")
        llm_calls: list = ast_signals.get("llm_calls", [])
        self_assignments: list = ast_signals.get("self_assignments", [])
        verdict_patterns: list = ast_signals.get("verdict_patterns", [])
        exception_patterns: list = ast_signals.get("exception_patterns", [])

        source_file = Path(input_data.get("source_file", ""))
        module_imports = self._read_module_imports(source_file)

        new_checks: list[dict] = []

        # ── R-01: DESCRIPTION 长度 ──
        desc_len = len(description)
        new_checks.append({
            "check": "R-01",
            "standard": "DESCRIPTION ≥ 50 字符",
            "severity": "HIGH",
            "passed": desc_len >= 50,
            "observation": f"DESCRIPTION {desc_len} chars, 阈值 50 {'✓' if desc_len >= 50 else '✗'}",
            "detail": {"measured": desc_len, "threshold": 50},
        })

        # ── R-04: 统一 LLMClient ──
        r04_violations = [imp for imp in module_imports if any(pat in imp for pat in DIRECT_LLM_IMPORTS)]
        new_checks.append({
            "check": "R-04",
            "standard": "统一 LLMClient, 无直接 openai/anthropic import",
            "severity": "CRITICAL",
            "passed": len(r04_violations) == 0,
            "observation": (
                "无直接 LLM import ✓" if not r04_violations
                else f"发现直接 LLM import: {', '.join(r04_violations)}"
            ),
            "detail": {"violations": r04_violations} if r04_violations else None,
        })

        # ── R-04-async: run() 不应为 async ──
        run_is_async = extracted.get("run_is_async", False)
        new_checks.append({
            "check": "R-04-async",
            "standard": "run() 不应为 async (LAP 同步协议, TeamRunner 用 to_thread 包装)",
            "severity": "MEDIUM",
            "passed": not run_is_async,
            "observation": (
                "run() 定义为 async def, 违反同步协议 ✗" if run_is_async else
                "run() 是同步方法 ✓"
            ),
            "detail": None,
        })

        # ── R-02-list: FORMAT_IN/OUT 不应为列表 ──
        format_in_kind = extracted.get("format_in_kind", "literal")
        format_out_kind = extracted.get("format_out_kind", "literal")
        list_fields = []
        if format_in_kind == "list":
            list_fields.append("FORMAT_IN")
        if format_out_kind == "list":
            list_fields.append("FORMAT_OUT")
        if list_fields:
            _raw_in = extracted.get("format_in")
            _raw_out = extracted.get("format_out")
            _obs_parts = []
            if "FORMAT_IN" in list_fields:
                _obs_parts.append(f"FORMAT_IN={_raw_in}")
            if "FORMAT_OUT" in list_fields:
                _obs_parts.append(f"FORMAT_OUT={_raw_out}")
            new_checks.append({
                "check": "R-02-list",
                "standard": "FORMAT_IN/OUT 应为单一 Format ID 字符串, 不应是列表",
                "severity": "HIGH",
                "passed": False,
                "observation": (
                    f"{' / '.join(list_fields)} 为列表 ({'; '.join(_obs_parts)}). "
                    "正确做法: ① AnchorSpec(format_in=[...]) 在 pipeline 中声明 fan-in; "
                    "② 定义 composite Format (Format.components=[...]), Router 类 FORMAT_IN 保持单字符串指向该复合 Format."
                ),
                "detail": {"list_fields": list_fields, "format_in": _raw_in, "format_out": _raw_out},
            })

        # ── R-05: PASS + FAIL 双覆盖 ──
        kinds = {vp.get("kind") for vp in verdict_patterns if vp.get("kind")}
        has_pass = "PASS" in kinds
        has_fail = "FAIL" in kinds
        r05_passed = has_pass and has_fail
        r05_obs = ["PASS ✓" if has_pass else "PASS 缺失 ✗",
                   "FAIL ✓" if has_fail else "FAIL 缺失 ✗"]
        new_checks.append({
            "check": "R-05",
            "standard": "Verdict 覆盖 PASS 和 FAIL",
            "severity": "HIGH",
            "passed": r05_passed,
            "observation": "; ".join(r05_obs),
            "detail": {"kinds_found": list(kinds)},
        })

        # ── R-06: 不直接写文件 ──
        r06_violations: list[str] = []
        for line_no, line in enumerate(run_source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat in FILE_WRITE_PATTERNS:
                if pat in stripped:
                    if "guarded_write" in stripped:
                        continue
                    if pat == "open(" and ("'r'" in stripped or '"r"' in stripped or "'rb'" in stripped):
                        continue
                    r06_violations.append(f"L{line_no}: {stripped[:80]}")
        new_checks.append({
            "check": "R-06",
            "standard": "不直接写文件 (需走 guarded_write)",
            "severity": "HIGH",
            "passed": len(r06_violations) == 0,
            "observation": (
                "无直接文件写操作 ✓" if not r06_violations
                else f"发现直接写操作: {'; '.join(r06_violations[:3])}"
            ),
            "detail": {"violations": r06_violations} if r06_violations else None,
        })

        # ── R-10: run() ≤ 80 行 ──
        new_checks.append({
            "check": "R-10",
            "standard": "run() ≤ 80 行",
            "severity": "MEDIUM",
            "passed": run_line_count <= 80,
            "observation": (
                f"run() 共 {run_line_count} 行, 阈值 80 {'✓' if run_line_count <= 80 else '✗'}"
                + (f" (超出 {run_line_count - 80} 行)" if run_line_count > 80 else "")
            ),
            "detail": {"measured": run_line_count, "threshold": 80},
        })

        # ── R-11: 无硬编模型名 ──
        r11_violations: list[str] = []
        for line_no, line in enumerate(run_source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat in KNOWN_MODEL_PATTERNS:
                if pat in stripped and ('"' in stripped or "'" in stripped):
                    r11_violations.append(f"L{line_no}: 含 '{pat}'")
                    break
        new_checks.append({
            "check": "R-11",
            "standard": "无硬编模型名",
            "severity": "MEDIUM",
            "passed": len(r11_violations) == 0,
            "observation": (
                "无硬编模型名 ✓" if not r11_violations
                else f"发现硬编模型名: {'; '.join(r11_violations[:3])}"
            ),
            "detail": {"violations": r11_violations} if r11_violations else None,
        })

        # ── R-12: 无 LLM 协议泄漏 ──
        r12_violations: list[str] = []
        for line_no, line in enumerate(run_source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat in PROTOCOL_LEAK_PATTERNS:
                if pat in stripped:
                    r12_violations.append(f"L{line_no}: {stripped[:80]}")
        new_checks.append({
            "check": "R-12",
            "standard": "无 LLM 协议泄漏 (block.type/choices[0] 等)",
            "severity": "MEDIUM",
            "passed": len(r12_violations) == 0,
            "observation": (
                "无协议泄漏 ✓" if not r12_violations
                else f"发现协议泄漏: {'; '.join(r12_violations[:3])}"
            ),
            "detail": {"violations": r12_violations} if r12_violations else None,
        })

        # ── R-13: RULE Router confidence = 1.0 ──
        if router_kind == "RULE":
            bad_conf = [
                vp for vp in verdict_patterns
                if vp.get("confidence") is not None and vp.get("confidence") != 1.0
            ]
            new_checks.append({
                "check": "R-13",
                "standard": "确定性 Router confidence = 1.0",
                "severity": "MEDIUM",
                "passed": len(bad_conf) == 0,
                "observation": (
                    "所有 Verdict.confidence = 1.0 ✓" if not bad_conf
                    else f"发现非 1.0 置信度: {[vp.get('confidence') for vp in bad_conf]}"
                ),
                "detail": {"bad_confidence": bad_conf} if bad_conf else None,
            })

        # ── R-input-unused ──
        _input_keys = ast_signals.get("input_keys_accessed", [])
        _output_keys = ast_signals.get("output_keys_produced", [])
        _is_llm_router = bool(llm_calls)
        _format_in_val = extracted.get("format_in")
        _format_out_val = extracted.get("format_out")
        _is_passthrough = (
            _format_in_val and _format_out_val and _format_in_val == _format_out_val
        )
        _is_whole_passthrough = (not _output_keys)
        if (
            not _is_llm_router
            and not _is_passthrough
            and not _is_whole_passthrough
            and not _input_keys
            and run_line_count > 5
            and format_in_kind == "literal"
        ):
            new_checks.append({
                "check": "R-input-unused",
                "standard": "RULE Router 的 run() 应从 input_data 读取键值 (d_rule_output_precise)",
                "severity": "HIGH",
                "passed": False,
                "observation": (
                    f"run() 未访问 input_data 的任何键 (input_keys_accessed=[]), "
                    f"但 FORMAT_IN({_format_in_val}) ≠ FORMAT_OUT({_format_out_val}). "
                    "可能是 stub 实现 (硬编码输出) 或使用了整体传递模式. 需 LLM 审计确认."
                ),
                "detail": {
                    "input_keys_accessed": _input_keys,
                    "output_keys_produced": _output_keys,
                    "run_line_count": run_line_count,
                },
            })

        # ── R-17: 异常不假通过 ──
        r17_violations = [
            ep for ep in exception_patterns if ep.get("handling") == "return_pass"
        ]
        new_checks.append({
            "check": "R-17",
            "standard": "异常不假通过 (except → FAIL/raise, 不能 → PASS)",
            "severity": "HIGH",
            "passed": len(r17_violations) == 0,
            "observation": (
                "无 except→PASS 模式 ✓" if not r17_violations
                else f"发现 {len(r17_violations)} 处 except→PASS: {[ep.get('exception_type') for ep in r17_violations]}"
            ),
            "detail": {"violations": r17_violations} if r17_violations else None,
        })

        # ── R-18: FieldCoverage ──
        _format_in_def: dict = input_data.get("format_in_def") or {}
        _fmt_schema: dict = _format_in_def.get("json_schema") or {}
        _fmt_props: dict = _fmt_schema.get("properties") or {}
        _fmt_required: list = _fmt_schema.get("required") or []
        _accessed: list[str] = ast_signals.get("input_keys_accessed", [])

        if (
            _fmt_props
            and not _is_llm_router
            and format_in_kind == "literal"
            and run_line_count > 3
        ):
            never_accessed = [k for k in _fmt_required if k not in _accessed]
            undeclared_accesses = [k for k in _accessed if k not in _fmt_props]
            _PASSTHROUGH_KEYS = {
                "material_id", "source_root", "checks", "extracted", "sig_diff_ok",
                "reports", "worker_class", "source_file", "format_in_def", "format_out_def",
            }
            undeclared_accesses = [k for k in undeclared_accesses if k not in _PASSTHROUGH_KEYS]

            if never_accessed or undeclared_accesses:
                parts = []
                if never_accessed:
                    parts.append(f"required 字段声明但未访问: {never_accessed}")
                if undeclared_accesses:
                    parts.append(f"访问了 schema 未声明字段: {undeclared_accesses}")
                new_checks.append({
                    "check": "R-18",
                    "standard": "FORMAT_IN json_schema 的 required 字段应在 run() 中被访问; run() 访问的字段应在 schema 中声明",
                    "severity": "MEDIUM",
                    "passed": False,
                    "observation": "; ".join(parts),
                    "detail": {
                        "never_accessed_required": never_accessed,
                        "undeclared_accesses": undeclared_accesses,
                        "schema_properties": list(_fmt_props.keys()),
                        "accessed_keys": _accessed,
                    },
                })
            else:
                new_checks.append({
                    "check": "R-18",
                    "standard": "FORMAT_IN json_schema 的 required 字段应在 run() 中被访问",
                    "severity": "MEDIUM",
                    "passed": True,
                    "observation": (
                        f"required 字段全部被访问 ({len(_fmt_required)} 项)"
                        + (" , 无未声明访问 ✓" if not undeclared_accesses else "")
                    ),
                    "detail": None,
                })

        # ── R-07 信号: self 赋值分类 ──
        for sa in self_assignments:
            if sa.get("classification") in ("SUSPICIOUS", "LIKELY_VIOLATION"):
                sev = "MEDIUM" if sa.get("classification") == "SUSPICIOUS" else "HIGH"
                new_checks.append({
                    "check": "R-07-signal",
                    "standard": "跨调用状态 (信号, 非判定)",
                    "severity": sev,
                    "passed": None,
                    "observation": (
                        f"run() 第 {sa.get('line', '?')} 行 self.{sa.get('var')} = ... "
                        f"(分类: {sa.get('classification')}), 交由语义审计判断严重性"
                    ),
                    "detail": sa,
                })

        output = dict(input_data)
        output["checks"] = list(input_data.get("checks", [])) + new_checks

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=output,
            diagnosis=(
                f"RouterDetChecker: {worker_class} "
                f"passed={sum(1 for c in new_checks if c.get('passed') is True)}"
                f"/{sum(1 for c in new_checks if c.get('passed') is not None)} checks"
            ),
        )

    def _read_module_imports(self, source_file: Path) -> list[str]:
        """读取源文件的模块级 import 语句."""
        if not source_file.exists():
            return []
        try:
            content = source_file.read_text(encoding="utf-8", errors="ignore")
            imports = []
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("import ") or stripped.startswith("from "):
                    imports.append(stripped)
            return imports
        except Exception:
            return []
