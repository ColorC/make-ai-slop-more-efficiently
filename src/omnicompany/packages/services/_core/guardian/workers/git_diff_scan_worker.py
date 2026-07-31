# [OMNI] origin=claude-code domain=omnicompany/guardian ts=2026-04-20T00:00:00Z type=router
# [OMNI] material_id="material:core.guardian.workers.git_diff_scanner.implementation.py"
"""GitDiffScanWorker — Guardian Team Worker #1 (self-contained).

Worker 协议:
  FORMAT_IN  = guardian.scan_request
  FORMAT_OUT = guardian.file_context_set

职责: 订阅扫描请求 → 读 git diff/full scan → 产出 FileContext 集合。

历史: 原 `patrol_runner.py` 的 git scan 函数已归档到 `_archive/patrol_runner_legacy.py`,
逻辑内联至本 Worker (2026-04-20 Team 1 迁移)。
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from omnicompany.protocol.anchor import Verdict, VerdictKind
from omnicompany.packages.services._core.omnicompany import Worker

from ..rules import FileContext, parse_omnimark

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# Git scan 内部函数（内联自原 patrol_runner.py）
# ══════════════════════════════════════════════════════════════════════


def _git_committed_changes(root: Path, n_commits: int = 1) -> list[tuple[str, str]]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-status", f"HEAD~{n_commits}", "HEAD"],
            cwd=str(root), text=True, stderr=subprocess.DEVNULL,
        )
        results = []
        for line in out.splitlines():
            # rename/copy 行是 "R095\told\tnew" 三段 — 取末段(新路径), 与
            # _git_staged_changes 一致. 旧 split("\t", 1) 会把 "old\tnew"
            # 整串当路径, 产出不存在的假路径罚单 (2026-07-15 修).
            parts = line.split("\t")
            if len(parts) >= 2:
                results.append((parts[0][:1], parts[-1].strip()))
        return results
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _git_uncommitted_changes(root: Path) -> list[tuple[str, str]]:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(root), text=True, stderr=subprocess.DEVNULL,
        )
        results = []
        for line in out.splitlines():
            if len(line) < 4:
                continue
            status = line[:2].strip()
            path = line[3:].strip()
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            change_type = status[0] if status else "M"
            if change_type == "?":
                change_type = "A"
            results.append((change_type, path))
        return results
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _git_staged_changes(root: Path) -> list[tuple[str, str]]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--cached", "--name-status"],
            cwd=str(root), text=True, stderr=subprocess.DEVNULL,
        )
        results = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0][0]
            path = parts[-1]
            results.append((status, path))
        return results
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []


def _load_file_ctx(root: Path, change_type: str, rel_path: str) -> Optional[FileContext]:
    rel_path_norm = rel_path.replace("\\", "/")
    abs_path = root / rel_path_norm
    content: Optional[str] = None
    if change_type != "D" and abs_path.exists() and abs_path.is_file():
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    omnimark = parse_omnimark(content) if content else None
    return FileContext(
        path=rel_path_norm, abs_path=str(abs_path),
        change_type=change_type, content=content, omnimark=omnimark,
    )


def _full_src_scan(root: Path) -> list[FileContext]:
    files: list[FileContext] = []
    for search_dir in [root / "src", root / "scripts"]:
        if not search_dir.exists():
            continue
        for p in search_dir.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            try:
                content = p.read_text(encoding="utf-8", errors="replace") if p.suffix == ".py" else None
            except Exception:
                content = None
            omnimark = parse_omnimark(content) if content else None
            files.append(FileContext(
                path=rel, abs_path=str(p), change_type="M",
                content=content, omnimark=omnimark,
            ))
    # docs/ 扫 (2026-05-08 V0-V26 巡检后扩): 之前 full_scan 只看 src+scripts,
    # OMNI-035 系列 docs 规范全在 full_scan 失效 — 现扩 .md/.yaml/.yml/.json 文档文件,
    # 跳 .py/.pyc/__pycache__/_archive (OMNI-035g/i 已硬扫小子集即可).
    docs_dir = root / "docs"
    if docs_dir.exists():
        _DOC_EXTS = {".md", ".yaml", ".yml", ".json", ".jsonl", ".rst", ".txt"}
        for p in docs_dir.rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts:
                continue
            # 跳 _archive 内深处 — 不扫归档区
            parts = p.parts
            if "_archive" in parts and parts.index("_archive") >= len(docs_dir.parts):
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            ext = p.suffix.lower()
            try:
                # OmniMark 头解析需 .md 内容
                content = p.read_text(encoding="utf-8", errors="replace") if ext in (".md", ".yaml", ".yml") else None
            except Exception:
                content = None
            omnimark = parse_omnimark(content) if content and ext == ".md" else None
            files.append(FileContext(
                path=rel, abs_path=str(p), change_type="M",
                content=content, omnimark=omnimark,
            ))
    data_dir = root / "data"
    # 2026-05-08 立: data/ 下 跳 _workspaces/_archive/_graveyard 子树 +
    # 任何含 node_modules/.git 的子目录 (team_builder 动态 worktree 不扫)
    _DATA_SKIP_DIRS = {"_workspaces", "_archive", "_graveyard", "node_modules", ".git", "__pycache__"}
    def _data_path_excluded(parts: tuple) -> bool:
        return any(d in parts for d in _DATA_SKIP_DIRS) or any(
            d.startswith("repo_abs_") for d in parts
        )
    if data_dir.exists():
        for p in data_dir.rglob("*.db"):
            if _data_path_excluded(p.parts):
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            files.append(FileContext(
                path=rel, abs_path=str(p), change_type="M",
                content=None, omnimark=None,
            ))
        # 2026-05-08 立 OMNI-055 (data/ 不放可执行代码) 配套: 扫 data/ 下代码后缀
        _DATA_CODE_EXTS = (".py", ".sh", ".ps1", ".bat", ".js", ".ts")
        for p in data_dir.rglob("*"):
            if not p.is_file() or _data_path_excluded(p.parts):
                continue
            ext = p.suffix.lower()
            if ext not in _DATA_CODE_EXTS:
                continue
            rel = str(p.relative_to(root)).replace("\\", "/")
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                content = None
            files.append(FileContext(
                path=rel, abs_path=str(p), change_type="M",
                content=content, omnimark=None,
            ))
        for p in data_dir.iterdir():
            if p.is_dir():
                rel = f"data/{p.name}/"
                files.append(FileContext(
                    path=rel, abs_path=str(p), change_type="M",
                    content=None, omnimark=None,
                ))
    _SKIP_ROOT_FILES = frozenset({
        "pyproject.toml", "poetry.lock", "uv.lock", "requirements.txt",
        ".gitignore", ".gitattributes", "README.md", "LICENSE", "CHANGELOG.md",
        ".python-version", "pytest.ini", "mypy.ini", ".pre-commit-config.yaml",
    })
    try:
        for p in root.iterdir():
            if p.is_file():
                if p.name in _SKIP_ROOT_FILES or p.name.startswith("."):
                    continue
                files.append(FileContext(
                    path=p.name, abs_path=str(p), change_type="M",
                    content=None, omnimark=None,
                ))
            elif p.is_dir():
                if p.name.startswith("."):
                    continue
                files.append(FileContext(
                    path=p.name + "/", abs_path=str(p), change_type="M",
                    content=None, omnimark=None,
                ))
    except OSError:
        pass
    return files


# ══════════════════════════════════════════════════════════════════════
# Worker
# ══════════════════════════════════════════════════════════════════════


class GitDiffScanWorker(Worker):
    """扫描 git 变更 / 全量源码 → 产出 guardian.file_context_set。"""

    DESCRIPTION = (
        "Guardian Team Worker #1: 订阅 guardian.scan_request 根据 scan_mode "
        "(diff / full / staged) 扫描文件, 加载 FileContext (路径/内容/OmniMark), "
        "产出 guardian.file_context_set 供 RuleEngineWorker 消费。"
    )
    FORMAT_IN = "guardian.scan_request"
    FORMAT_OUT = "guardian.file_context_set"

    def run(self, input_data: dict[str, Any]) -> Verdict:
        req = input_data.get("guardian.scan_request") or input_data
        scan_mode = req.get("scan_mode", "diff")
        project_root = Path(req.get("project_root", "."))
        n_commits = req.get("n_commits", 1)
        committed = req.get("committed", True)
        uncommitted = req.get("uncommitted", True)

        scan_ts = datetime.now(timezone.utc).isoformat()

        if scan_mode == "full":
            files = _full_src_scan(project_root)
        elif scan_mode == "staged":
            changed = _git_staged_changes(project_root)
            files = [ctx for ct, p in changed if (ctx := _load_file_ctx(project_root, ct, p)) is not None]
        else:
            changed: list[tuple[str, str]] = []
            if committed:
                changed += _git_committed_changes(project_root, n_commits)
            if uncommitted:
                changed += _git_uncommitted_changes(project_root)
            files = [ctx for ct, p in changed if (ctx := _load_file_ctx(project_root, ct, p)) is not None]

        files_dicts = [
            {"path": f.path, "abs_path": f.abs_path, "change_type": f.change_type,
             "content": f.content, "omnimark": f.omnimark}
            for f in files
        ]

        # Protocol 约定: verdict.output 是 FORMAT_OUT 对应 Format 的 payload 本体 (平铺)
        return Verdict(
            kind=VerdictKind.PASS,
            output={
                "scan_ts": scan_ts,
                "scan_mode": scan_mode,
                "files": files_dicts,
            },
        )
