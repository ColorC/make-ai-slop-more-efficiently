# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.guardian.workers.filesystem_scanner.implementation.py"
"""FsScannerWorker — Guardian health-check Team Worker (Router legacy 迁移版).

Worker 协议:
  FORMAT_IN  = guardian.check-request
  FORMAT_OUT = guardian.fs-report

职责: 扫描项目根目录及工作区, 检测文件系统污染。
  1. 项目根目录下的非法条目（不在白名单中的文件/目录）
  2. data/ 下的散落 db/临时文件
  3. 类型命名的临时文件（bash.stdout.* 等）
  4. 盘根目录（C:/E:/D:/ 等）是否有 omnicompany 产生的散落文件

注: 本 Worker 属于 guardian 健康检查管线 (fs_scanner → arch_auditor → health_reporter),
与主 patrol 管线 (GitDiffScan/RuleEngine) 是不同维度的检查。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.packages.services._core.omnicompany import Worker
from omnicompany.packages.services._core.guardian.rules.runtime_hygiene import (
    scan_data_root_layout_violations,
)

logger = logging.getLogger(__name__)


_DEFAULT_PROJECT_ROOT = Path("e:/WindowsWorkspace/omnicompany")

# 项目根目录下允许的合法条目
_ALLOWED_ROOT_ENTRIES = frozenset({
    "src", "scripts", "data", "config", "tests", "docs", "tmp", "venv", ".venv",
    ".git", ".pytest_cache", "__pycache__", "domains", "logs",
    "pyproject.toml", ".gitignore", ".env", ".env.local", "README.md",
})

# data/ 下允许的顶级子目录
# 2026-04-21: 原 hardcoded 白名单与 archmap.yaml 严重不一致 (13 entries vs archmap 的 3 entries),
# 导致 data/ 下 18+ 非法目录漏检. B3b 修复: 统一从 archmap.yaml 读取 allowed_subdirs.
# archmap 权威值: _archive*/ + absorption/ + domains/ (+ required_files events.db/ide_events.db/private_domain_nodes.db).
_ALLOWED_DATA_DIRS = frozenset({
    "_archive",       # 2026-04-21 B3 后 4 前缀统一归属
    "absorption",     # services/absorption 产物 (S3e.1 archmap 认可)
    "domains",        # 业务 domain artifact 通用层 (S3e.2+ archmap 认可)
    # 过渡期兼容 (2026-04-21 后这里其他值应清空, 让 B4 把不合规目录全部搬走):
    "_archive_agent_loop",  # 过渡名, B3 已搬; 留作 rollback 容错 TODO 删
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


class FsScannerWorker(Worker):
    """扫描项目根目录及工作区，检测文件系统污染。"""

    INPUT_KEYS = ["project_root"]
    DESCRIPTION = (
        "扫描文件系统污染：根目录非法条目、散落 db、类型命名临时文件、盘根目录污染"
    )
    FORMAT_IN = "guardian.check-request"
    FORMAT_OUT = "guardian.fs-report"

    def __init__(
        self,
        project_root: str | None = None,
        *,
        excluded_mount_roots: list[str] | None = None,
    ):
        self._root = Path(project_root) if project_root else _DEFAULT_PROJECT_ROOT
        # 已挂载外部业务仓根排除清单(错误样本㊄): 这些仓虽被扫描逻辑(尤其盘根扫描)
        # 触及, 但它们是登记在案的业务仓, 其正常业务文件不该被巡逻当散落文件误报。
        # None → 内部懒调用 list_mounted_repo_roots() 现取真实登记表(形状A的默认行为);
        # 给了列表 → 直接用(便于单元测试不碰真实 yaml)。
        self._excluded_mount_roots = excluded_mount_roots

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
        try:
            for item in scan_data_root_layout_violations(root):
                issues.append({
                    "category": "data_contamination",
                    "severity": "high" if item["kind"] == "dir" else "medium",
                    "path": item["path"],
                    "detail": f"data/ top-level {item['kind']} is outside archmap closed set: {item['name']}",
                    "suggestion": "Move under data/_runtime, data/services/<svc>, data/domains/<domain>, or revise archmap after review.",
                })
        except Exception as e:
            logger.warning("FsScanner: failed to scan data/ against archmap: %s", e)
        return

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

    def _resolve_excluded_mount_roots(self) -> list[Path]:
        """解析"已挂载外部仓根"排除清单(错误样本㊄)。

        - 构造时显式给了 excluded_mount_roots(形状A) → 直接用。
        - 否则懒导入 external_mounts.list_mounted_repo_roots() 现取现用(形状B)。
        - 读取失败/为空 → 退化为空集, 绝不抛异常(安全默认: 宁可多报一次也不能
          因为排除功能挂了而让整个巡检罢工)。
        """
        raw: list[str] = []
        if self._excluded_mount_roots is not None:
            raw = list(self._excluded_mount_roots)
        else:
            try:
                # 经模块对象取函数(而非 from-import 绑定), 兼容懒/静态两种导入写法,
                # 也让测试的 monkeypatch(打在 external_mounts 模块上)能生效。
                from omnicompany.packages.services._core.registry import (
                    external_mounts as _em,
                )

                raw = [str(p) for p in _em.list_mounted_repo_roots()]
            except Exception as e:  # noqa: BLE001
                logger.debug("FsScanner: 读取挂载排除清单失败, 退化为空集: %s", e)
                raw = []

        resolved: list[Path] = []
        for r in raw:
            try:
                resolved.append(Path(r).resolve())
            except Exception:  # noqa: BLE001
                continue
        return resolved

    @staticmethod
    def _is_under_any(target: Path, roots: list[Path]) -> bool:
        """target 本身或其任一父目录命中 roots 中任一路径即返回 True。"""
        try:
            target_resolved = target.resolve()
        except Exception:  # noqa: BLE001
            return False
        for root in roots:
            try:
                target_resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _check_drive_roots(self, issues: list[dict]) -> None:
        """检查盘根目录是否有 omnicompany/agent 产出的散落文件。"""
        suspect_patterns = (
            "omnicompany", "semantic_network", "evolution",
            "trace", "pain", "repair", "embedding",
        )
        excluded_roots = self._resolve_excluded_mount_roots()
        for drive in _DRIVE_ROOTS_TO_CHECK:
            if not drive.exists():
                continue
            try:
                for entry in drive.iterdir():
                    # 错误样本㊄: 已登记的外部挂载仓路径(及其内部)一律不误报为散落文件。
                    if self._is_under_any(entry, excluded_roots):
                        continue
                    name_lower = entry.name.lower()
                    # 只关注疑似 omnicompany 产出的
                    if any(p in name_lower for p in suspect_patterns):
                        issues.append({
                            "category": "drive_root_contamination",
                            "severity": "high",
                            "path": str(entry),
                            "detail": f"盘根目录出现疑似 omnicompany 产出: {entry.name}",
                            "suggestion": "清理或移动到 omnicompany/data/ 下",
                        })
                    # tmp 目录在 E 盘根
                    if drive == Path("e:/") and name_lower == "tmp":
                        issues.append({
                            "category": "drive_root_contamination",
                            "severity": "medium",
                            "path": str(entry),
                            "detail": "E 盘根目录的 tmp/ 应在 omnicompany/tmp/ 下",
                            "suggestion": "确认内容后移动或删除",
                        })
            except PermissionError:
                pass
