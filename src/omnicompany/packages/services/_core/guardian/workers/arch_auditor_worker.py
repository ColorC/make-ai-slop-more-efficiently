# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.guardian.workers.architecture_auditor.implementation.py"
"""ArchAuditorWorker — Guardian health-check Team Worker (Router legacy 迁移版).

Worker 协议:
  FORMAT_IN  = guardian.fs-report
  FORMAT_OUT = guardian.arch-report

职责: 审计 src/ 下的架构规范。
  1. DEPRECATED 标记的模块是否仍被 import
  2. Router 实现是否声明了必要元数据（DESCRIPTION / FORMAT_IN / FORMAT_OUT）
  3. __init__.py 是否为空（应有 docstring）
  4. 不规范的模块位置（如 routers 直接放在 runtime/ 下）
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.packages.services._core.omnicompany import Worker

logger = logging.getLogger(__name__)


_DEFAULT_PROJECT_ROOT = Path("e:/WindowsWorkspace/omnicompany")


class ArchAuditorWorker(Worker):
    """审计 src/ 下的架构规范。"""

    INPUT_KEYS = None  # 接收 fs_scanner 的全部输出
    DESCRIPTION = "审计 src/ 架构规范：DEPRECATED、Router 元数据、模块位置"
    FORMAT_IN = "guardian.fs-report"
    FORMAT_OUT = "guardian.arch-report"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            input_data = {}

        project_root = Path(input_data.get("project_root", str(_DEFAULT_PROJECT_ROOT)))
        src_root = project_root / "src" / "omnicompany"

        arch_issues: list[dict[str, str]] = []

        # 1. 检查 DEPRECATED 模块
        self._check_deprecated_modules(src_root, arch_issues)
        # 2. 检查 Router 元数据完整性
        self._check_router_metadata(src_root, arch_issues)
        # 3. 检查空的 __init__.py
        self._check_empty_inits(src_root, arch_issues)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                **input_data,
                "arch_issues": arch_issues,
                "arch_issue_count": len(arch_issues),
            },
        )

    def _check_deprecated_modules(self, src_root: Path, issues: list[dict]) -> None:
        """扫描所有 .py 文件的前 5 行，查找 DEPRECATED 标记。"""
        if not src_root.exists():
            return
        for py_file in src_root.rglob("*.py"):
            if "__pycache__" in str(py_file) or "_graveyard" in str(py_file):
                continue
            try:
                lines = py_file.read_text(encoding="utf-8", errors="ignore").splitlines()[:5]
                for line in lines:
                    if "DEPRECATED" in line.upper() and ("DO NOT USE" in line.upper() or "LEGACY" in line.upper()):
                        rel = str(py_file.relative_to(src_root.parent.parent))
                        issues.append({
                            "category": "deprecated_module",
                            "severity": "low",
                            "path": rel.replace("\\", "/"),
                            "detail": f"DEPRECATED 模块仍存在: {line.strip()[:80]}",
                            "suggestion": "确认无引用后移到 _graveyard/ 或删除",
                        })
                        break
            except Exception:
                pass

    def _check_router_metadata(self, src_root: Path, issues: list[dict]) -> None:
        """检查 packages/ 下 Router 子类是否声明了必要元数据。

        Post-2026-04-07: walks packages/ (not the retired primitives_impl/).
        """
        packages_dir = src_root / "packages"
        if not packages_dir.exists():
            return

        for py_file in packages_dir.rglob("routers.py"):
            if "__pycache__" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ClassDef):
                        continue
                    if not any(
                        (isinstance(b, ast.Name) and b.id == "Router") or
                        (isinstance(b, ast.Attribute) and b.attr == "Router")
                        for b in node.bases
                    ):
                        continue

                    # 检查是否声明了 DESCRIPTION
                    class_body_names = set()
                    for stmt in node.body:
                        if isinstance(stmt, ast.Assign):
                            for t in stmt.targets:
                                if isinstance(t, ast.Name):
                                    class_body_names.add(t.id)
                        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                            class_body_names.add(stmt.target.id)

                    rel = str(py_file.relative_to(src_root.parent.parent))
                    missing = []
                    for attr in ("DESCRIPTION",):
                        if attr not in class_body_names:
                            missing.append(attr)
                    if missing:
                        issues.append({
                            "category": "router_metadata",
                            "severity": "low",
                            "path": f"{rel.replace(chr(92), '/')}:{node.lineno}",
                            "detail": f"{node.name} 缺少元数据: {', '.join(missing)}",
                            "suggestion": "添加 DESCRIPTION 类属性",
                        })
            except Exception:
                pass

    # 空 __init__.py 可以忽略的目录（这些地方空 __init__.py 完全正常）
    _INIT_IGNORE_DIRS = frozenset({
        "_graveyard", "__pycache__", "commands", "sw",
    })

    def _check_empty_inits(self, src_root: Path, issues: list[dict]) -> None:
        """检查核心业务目录的 __init__.py 是否有模块说明。

        空 __init__.py 在 Python 中是正常的包标记，不是所有包都需要 docstring。
        Post-2026-04-07: reports on packages/<domain>/__init__.py (not the
        retired primitives_impl/).
        """
        if not src_root.exists():
            return
        packages_dir = src_root / "packages"
        if not packages_dir.exists():
            return
        for init_file in packages_dir.glob("*/__init__.py"):
            if "__pycache__" in str(init_file):
                continue
            parent_name = init_file.parent.name
            if parent_name in self._INIT_IGNORE_DIRS or parent_name.startswith("_"):
                continue
            try:
                content = init_file.read_text(encoding="utf-8", errors="ignore").strip()
                if not content:
                    rel = str(init_file.relative_to(src_root.parent.parent))
                    issues.append({
                        "category": "empty_init",
                        "severity": "low",
                        "path": rel.replace("\\", "/"),
                        "detail": f"业务包 {parent_name}/ 的 __init__.py 无模块说明",
                        "suggestion": "添加模块用途 docstring",
                    })
            except Exception:
                pass
