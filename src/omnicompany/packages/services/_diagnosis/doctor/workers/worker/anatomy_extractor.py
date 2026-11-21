# [OMNI] origin=claude-code domain=omnicompany/doctor ts=2026-04-22T00:00:00Z type=worker
# [OMNI] material_id="material:diagnosis.doctor.worker.worker.ast_structure_extractor.py"
"""WorkerAnatomyExtractor — AST 提取 Router 结构 (HARD, Stage 3 Clean Migration 2026-04-22).

Worker 协议:
  FORMAT_IN  = diag.worker.request
  FORMAT_OUT = diag.worker.extracted

诊断目标: AST 解析 source_file, 提取目标 worker_class 的:
  - 类变量: DESCRIPTION / FORMAT_IN / FORMAT_OUT / INPUT_KEYS / PASSTHROUGH
  - __init__ 参数 / run() 源码 + 行数 + async 标志
  - 7 类 AST 衍生信号:
      * router_kind: LLM vs RULE
      * llm_calls: 类内所有方法的 LLM 调用点
      * self_assignments: run() 内的 self.xxx = ... (按变量名粗分类)
      * input_keys_accessed: run() 里访问的 input_data 键
      * output_keys_produced: Verdict(output={...}) 顶层键
      * verdict_patterns: 每个 return Verdict(...) 的 kind/confidence/diagnosis
      * exception_patterns: run() 内 except 块的处理方式

PASS: 文件存在且 AST 可解析 (found=true 或 false 均 PASS)
FAIL: 文件/目录不存在, 或 AST 解析遇到语法错误
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.protocol.anchor import Verdict, VerdictKind

from ._shared import DEFAULT_SOURCE_ROOT, extract_router_ast, logger


class WorkerAnatomyExtractor(Worker):
    """打开 source_file (或目录), AST 解析, 提取目标 worker_class 的全部结构信息和 7 类衍生信号."""

    DESCRIPTION = "AST 提取目标 Router 类的结构 (DESCRIPTION/FORMAT_IN/OUT/run源码/行数) 和 7 类衍生信号 (llm_calls/self_assignments/verdict_patterns 等)"
    FORMAT_IN = "diag.worker.request"
    FORMAT_OUT = "diag.worker.extracted"
    INPUT_KEYS = ["worker_class", "source_file", "source_root"]

    def run(self, input_data: Any) -> Verdict:
        worker_class: str = input_data["worker_class"]
        source_file = Path(input_data["source_file"])
        source_root = Path(input_data.get("source_root", DEFAULT_SOURCE_ROOT))

        if not source_file.exists():
            return Verdict(
                kind=VerdictKind.FAIL,
                confidence=1.0,
                output=self._empty_output(worker_class, source_file, source_root),
                diagnosis=f"RouterExtractor FAIL: {source_file} 不存在",
            )

        py_files: list[Path] = []
        if source_file.is_dir():
            py_files = [f for f in source_file.rglob("*.py") if "__pycache__" not in str(f)]
        else:
            py_files = [source_file]

        extracted_data: dict | None = None
        found = False

        for py_file in py_files:
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                logger.warning("RouterExtractor: cannot read %s: %s", py_file, e)
                continue

            if worker_class not in content:
                continue

            try:
                tree = ast.parse(content, filename=str(py_file))
            except SyntaxError as e:
                return Verdict(
                    kind=VerdictKind.FAIL,
                    confidence=1.0,
                    output=self._empty_output(worker_class, source_file, source_root),
                    diagnosis=f"RouterExtractor FAIL: {py_file} AST 解析失败: {e}",
                )

            source_lines = content.splitlines()

            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                if node.name != worker_class:
                    continue

                data = extract_router_ast(node, source_lines)
                found = True
                extracted_data = data
                break

            if found:
                break

        base = {
            "worker_class": worker_class,
            "source_file": str(source_file),
            "source_root": str(source_root),
            "found": found,
        }

        if found and extracted_data:
            base.update(extracted_data)
        else:
            base.update(self._empty_fields())

        return Verdict(
            kind=VerdictKind.PASS,
            confidence=1.0,
            output=base,
            diagnosis=f"RouterExtractor: {worker_class} found={found}",
        )

    def _empty_output(self, worker_class: str, source_file: Path, source_root: Path) -> dict:
        out = {
            "worker_class": worker_class,
            "source_file": str(source_file),
            "source_root": str(source_root),
            "found": False,
        }
        out.update(self._empty_fields())
        return out

    @staticmethod
    def _empty_fields() -> dict:
        return {
            "description": None,
            "format_in": None,
            "format_out": None,
            "input_keys": None,
            "output_keys": None,
            "passthrough": None,
            "init_params": [],
            "run_source": "",
            "run_line_count": 0,
            "ast_signals": {
                "router_kind": "RULE",
                "llm_calls": [],
                "self_assignments": [],
                "input_keys_accessed": [],
                "output_keys_produced": [],
                "verdict_patterns": [],
                "exception_patterns": [],
            },
        }
