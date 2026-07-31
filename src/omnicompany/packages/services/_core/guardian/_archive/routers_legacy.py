# [OMNI] origin=omnifactory domain=omnifactory/guardian ts=2026-04-05T17:04:51Z
# [OMNI] material_id="material:core.guardian.fs_scanner_arch_auditor_health_reporter.routers_legacy.py"
"""guardian.routers — 守护检查管线的 Router 实现

  FsScannerRouter      (HARD) 扫描文件系统污染，收集事实
  ArchAuditorRouter    (HARD) 审计 src/ 架构规范，收集事实
  HealthReporterRouter (AgentNodeLoop) 基于事实清单做探查评估、评分、建议
"""

from __future__ import annotations

import ast
import json
import logging
import os
from pathlib import Path
from typing import Any

from omnifactory.protocol.anchor import Verdict, VerdictKind
from omnifactory.runtime.agent.agent_loop_config import LoopConfig, CompactConfig, PermissionConfig
from omnifactory.runtime.agent.agent_loop_tools import ReadFileTool, GrepTool, GlobTool, ListDirTool, ThinkTool
from omnifactory.runtime.agent.agent_node_loop import AgentNodeLoop
from omnifactory.runtime.routing.router import Router

logger = logging.getLogger(__name__)

# ── 默认配置 ──

_DEFAULT_PROJECT_ROOT = Path("e:/WindowsWorkspace/omnifactory")

# 项目根目录下允许的合法条目
_ALLOWED_ROOT_ENTRIES = frozenset({
    "src", "scripts", "data", "config", "tests", "docs", "tmp", "venv", ".venv",
    ".git", ".pytest_cache", "__pycache__", "domains", "logs",
    "pyproject.toml", ".gitignore", ".env", ".env.local", "README.md",
})

# data/ 下允许的顶级子目录
_ALLOWED_DATA_DIRS = frozenset({
    "rewrite", "feishu_data", "demogame_data", "debug", "test",
    "autonomous", "sw", "workflow", "predicted_164",
    "_archive_agent_loop", "_archive", "guardian", "equiv",
})

# 盘根目录扫描路径（检测是否有散落文件）
_DRIVE_ROOTS_TO_CHECK = [
    Path("e:/"),
    Path("c:/"),
    Path("d:/"),
]

# 类型命名前缀（agent 常用作文件名）
_TYPE_NAME_PREFIXES = (
    "bash.stdout.", "bash.stderr.", "bash.int.", "fs.path.", "fs.content.",
    "python.code.", "think.plan.", "exec.output.", "data.json.",
)


# ════════════════════════════════════════════════════════════
# FsScannerRouter — 文件系统洁净度扫描
# ════════════════════════════════════════════════════════════

class FsScannerRouter(Router):
    """扫描项目根目录及工作区，检测文件系统污染。

    检查项：
    1. 项目根目录下的非法条目（不在白名单中的文件/目录）
    2. data/ 下的散落 db/临时文件
    3. 类型命名的临时文件（bash.stdout.* 等）
    4. 盘根目录（C:/E:/D:/ 等）是否有 omnifactory 产生的散落文件
    """

    INPUT_KEYS = ["project_root"]
    DESCRIPTION = "扫描文件系统污染：根目录非法条目、散落 db、类型命名临时文件、盘根目录污染"
    FORMAT_IN = "guardian.check-request"
    FORMAT_OUT = "guardian.fs-report"

    def __init__(self, project_root: str | None = None):
        self._root = Path(project_root) if project_root else _DEFAULT_PROJECT_ROOT

    def run(self, input_data: Any) -> Verdict:
        if isinstance(input_data, dict):
            root = Path(input_data.get("project_root", str(self._root)))
        else:
            root = self._root

        issues: list[dict[str, str]] = []

        # 1. 根目录非法条目
        self._check_root_entries(root, issues)
        # 2. data/ 散落文件
        self._check_data_dir(root, issues)
        # 3. 类型命名临时文件
        self._check_type_named_files(root, issues)
        # 4. 盘根目录污染
        self._check_drive_roots(issues)

        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "project_root": str(root),
                "fs_issues": issues,
                "fs_issue_count": len(issues),
            },
        )

    def _check_root_entries(self, root: Path, issues: list[dict]) -> None:
        try:
            for entry in sorted(root.iterdir()):
                name = entry.name
                if name.startswith(".") and name not in (".git", ".gitignore", ".env", ".env.local"):
                    continue
                if name in _ALLOWED_ROOT_ENTRIES:
                    continue
                kind = "file" if entry.is_file() else "dir"
                severity = "high" if entry.is_file() and entry.suffix in (".db", ".json", ".txt") else "medium"
                issues.append({
                    "category": "root_contamination",
                    "severity": severity,
                    "path": str(entry.relative_to(root)),
                    "detail": f"项目根目录出现非法{kind}: {name}",
                    "suggestion": "移动到 data/ 或 tmp/ 下，或添加到 .gitignore",
                })
        except Exception as e:
            logger.warning("FsScanner: 无法扫描根目录 %s: %s", root, e)

    def _check_data_dir(self, root: Path, issues: list[dict]) -> None:
        data_dir = root / "data"
        if not data_dir.exists():
            return
        try:
            for entry in sorted(data_dir.iterdir()):
                name = entry.name
                if name.startswith("_") or name.startswith("."):
                    continue
                if entry.is_dir() and name in _ALLOWED_DATA_DIRS:
                    continue
                if entry.is_file():
                    # data/ 根目录不应该有散文件
                    issues.append({
                        "category": "data_contamination",
                        "severity": "medium",
                        "path": f"data/{name}",
                        "detail": f"data/ 下散落文件: {name}",
                        "suggestion": "移到 data/ 子目录下或归档到 _archive/",
                    })
                elif entry.is_dir() and name not in _ALLOWED_DATA_DIRS:
                    issues.append({
                        "category": "data_contamination",
                        "severity": "low",
                        "path": f"data/{name}",
                        "detail": f"data/ 下非标准子目录: {name}",
                        "suggestion": "注册到 _ALLOWED_DATA_DIRS 或归档",
                    })
        except Exception as e:
            logger.warning("FsScanner: 无法扫描 data/ 目录: %s", e)

    def _check_type_named_files(self, root: Path, issues: list[dict]) -> None:
        found: list[str] = []
        try:
            count = 0
            for dirpath, dirnames, filenames in os.walk(str(root)):
                dirnames[:] = [
                    d for d in dirnames
                    if d not in (".git", "venv", ".venv", "__pycache__", ".pytest_cache", "node_modules")
                ]
                for fname in filenames:
                    if any(fname.startswith(p) for p in _TYPE_NAME_PREFIXES):
                        rel = os.path.relpath(os.path.join(dirpath, fname), str(root))
                        found.append(rel.replace("\\", "/"))
                count += len(filenames)
                if count > 2000:
                    break
        except Exception:
            pass

        if found:
            issues.append({
                "category": "type_named_files",
                "severity": "medium",
                "path": "; ".join(found[:5]),
                "detail": f"发现 {len(found)} 个类型命名临时文件",
                "suggestion": "这些是 agent 运行残留，应该清理",
            })

    def _check_drive_roots(self, issues: list[dict]) -> None:
        """检查盘根目录是否有 omnifactory/agent 产出的散落文件。"""
        suspect_patterns = (
            "omnifactory", "semantic_network", "evolution",
            "trace", "pain", "repair", "embedding",
        )
        for drive in _DRIVE_ROOTS_TO_CHECK:
            if not drive.exists():
                continue
            try:
                for entry in drive.iterdir():
                    name_lower = entry.name.lower()
                    # 只关注疑似 omnifactory 产出的
                    if any(p in name_lower for p in suspect_patterns):
                        issues.append({
                            "category": "drive_root_contamination",
                            "severity": "high",
                            "path": str(entry),
                            "detail": f"盘根目录出现疑似 omnifactory 产出: {entry.name}",
                            "suggestion": "清理或移动到 omnifactory/data/ 下",
                        })
                    # tmp 目录在 E 盘根
                    if drive == Path("e:/") and name_lower == "tmp":
                        issues.append({
                            "category": "drive_root_contamination",
                            "severity": "medium",
                            "path": str(entry),
                            "detail": "E 盘根目录的 tmp/ 应在 omnifactory/tmp/ 下",
                            "suggestion": "确认内容后移动或删除",
                        })
            except PermissionError:
                pass


# ════════════════════════════════════════════════════════════
# ArchAuditorRouter — 架构规范审计
# ════════════════════════════════════════════════════════════

class ArchAuditorRouter(Router):
    """审计 src/ 下的架构规范。

    检查项：
    1. DEPRECATED 标记的模块是否仍被 import
    2. Router 实现是否声明了必要元数据（DESCRIPTION / FORMAT_IN / FORMAT_OUT）
    3. __init__.py 是否为空（应有 docstring）
    4. 不规范的模块位置（如 routers 直接放在 runtime/ 下）
    """

    INPUT_KEYS = None  # 接收 fs_scanner 的全部输出
    DESCRIPTION = "审计 src/ 架构规范：DEPRECATED、Router 元数据、模块位置"
    FORMAT_IN = "guardian.fs-report"
    FORMAT_OUT = "guardian.arch-report"

    def run(self, input_data: Any) -> Verdict:
        if not isinstance(input_data, dict):
            input_data = {}

        project_root = Path(input_data.get("project_root", str(_DEFAULT_PROJECT_ROOT)))
        src_root = project_root / "src" / "omnifactory"
        fs_issues = input_data.get("fs_issues", [])

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


# ════════════════════════════════════════════════════════════
# HealthReporterRouter — 汇总 + 评分 + 事件发出
# ════════════════════════════════════════════════════════════

class HealthReporterRouter(AgentNodeLoop):
    """LLM Agent 评估项目健康度。

    继承 AgentNodeLoop：可以自主探查文件、grep 搜索、列目录，
    基于扫描事实 + 实际文件内容做上下文判断，而非硬规则算分。
    """

    TOOLS = [ReadFileTool, GrepTool, GlobTool, ListDirTool, ThinkTool]

    SYSTEM_PROMPT = """\
你是一个项目架构健康度评估专家。你将收到自动扫描器产出的事实清单。

你可以使用工具探查具体文件内容来辅助判断，例如：
- 用 read_file 查看 DEPRECATED 模块是否真的没人在用
- 用 grep 搜索某个模块是否被 import
- 用 list_dir 查看某个目录下的实际内容

评判原则：
- 散落的 .db/.json 文件在项目根目录 = 真正的架构污染
- DEPRECATED 模块如果仍被 import = 需要迁移，否则直接清理
- 空的 __init__.py 在 Python 中完全正常，不扣分
- 盘根目录（C:/ E:/）出现项目文件 = 严重，工具在不该写的地方写了
- Router 缺少 DESCRIPTION = 轻微

完成评估后，使用 finish 工具输出结果。finish 的 result 字段必须是严格 JSON：
{
    "health_score": 0到100的整数,
    "verdict": "healthy" | "needs_attention" | "unhealthy",
    "summary": "一句话总结",
    "issue_assessments": [
        {"category": "类别", "severity": "critical|warning|info", "count": 数量, "assessment": "判断"}
    ],
    "top_actions": ["最重要的 1-3 条改进建议"],
    "report": "完整的中文可读报告"
}"""

    LOOP_CONFIG = LoopConfig(
        max_turns=15,
        compact=CompactConfig(auto_compact_enabled=False),  # 轮数少不需要压缩
        permission=PermissionConfig(mode="readonly"),  # 健康检查只读
    )

    DESCRIPTION = "AgentNodeLoop: LLM 评估项目健康度（可探查文件）"
    FORMAT_IN = "guardian.arch-report"
    FORMAT_OUT = "guardian.health-report"

    def __init__(self, model: str | None = None):
        super().__init__(model=model, config=self.LOOP_CONFIG)

    def build_initial_messages(self, input_data: dict) -> list[dict]:
        fs_issues = input_data.get("fs_issues", [])
        arch_issues = input_data.get("arch_issues", [])
        all_issues = fs_issues + arch_issues

        if not all_issues:
            return [{"role": "user", "content": "无问题。请直接用 finish 输出 health_score=100。"}]

        # 按 category 聚合
        by_category: dict[str, list[dict]] = {}
        for issue in all_issues:
            cat = issue.get("category", "unknown")
            by_category.setdefault(cat, []).append(issue)

        lines = [f"自动扫描发现 {len(all_issues)} 个问题：\n"]
        for cat, items in sorted(by_category.items()):
            lines.append(f"## {cat} ({len(items)} 个)")
            for item in items[:8]:
                lines.append(f"- {item.get('detail', '')}  路径: {item.get('path', '')}")
            if len(items) > 8:
                lines.append(f"- ... 另外 {len(items) - 8} 个同类问题")
            lines.append("")

        lines.append(
            "请评估这些问题的真实严重性。你可以用工具探查具体文件来辅助判断。"
            "完成后用 finish 输出 JSON 结果。"
        )

        return [{"role": "user", "content": "\n".join(lines)}]

    def extract_result(self, final_text: str, messages: list[dict]) -> Verdict:
        # 从原始 input 恢复 issues（用于传递给下游）
        # build_initial_messages 的 input_data 不在这里了，从 messages 推断
        fs_issues: list[dict] = []
        arch_issues: list[dict] = []

        try:
            text = final_text.strip()
            if "```" in text:
                for part in text.split("```"):
                    if part.startswith("json"):
                        text = part[4:].strip()
                        break
                    elif "{" in part:
                        text = part.strip()
                        break

            parsed = json.loads(text)
        except Exception as e:
            logger.error("[health_reporter] 结果解析失败: %s", e)
            return Verdict(
                kind=VerdictKind.FAIL,
                output={
                    "health_score": 0,
                    "verdict": "parse_error",
                    "report": f"LLM 输出解析失败: {e}\n原始输出: {final_text[:500]}",
                    "fs_issues": fs_issues,
                    "arch_issues": arch_issues,
                },
            )

        health_score = parsed.get("health_score", 50)
        verdict = parsed.get("verdict", "needs_attention")
        report = parsed.get("report", parsed.get("summary", ""))

        passed = verdict == "healthy" or health_score >= 60
        kind = VerdictKind.PASS if passed else VerdictKind.FAIL

        return Verdict(
            kind=kind,
            output={
                "health_score": health_score,
                "verdict": verdict,
                "total_issues": sum(a.get("count", 0) for a in parsed.get("issue_assessments", [])),
                "issue_assessments": parsed.get("issue_assessments", []),
                "top_actions": parsed.get("top_actions", []),
                "fs_issues": fs_issues,
                "arch_issues": arch_issues,
                "report": report,
            },
            diagnosis=report if not passed else None,
        )
