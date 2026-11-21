# [OMNI] origin=ai-ide domain=services/_diagnosis/doctor/tools ts=2026-05-07T04:15:00Z type=router status=skeleton agent=ai-ide
# [OMNI] summary="GitLogTool 结构化 git log 工具 — 修 4hr 拷问真问题 1+5: 元诊断 agent 走 10 问 #7 需 git 历史, 但不开通用 bash (违反 agent_tools.md). 立结构化工具"
# [OMNI] why="MetaDiagnosticAgent 之前 prompt 第 7 问要 git log 但工具集没 bash, prompt-tools 不一致. agent_tools.md 原则 1 禁通用 bash. 折中: 立结构化 git_log 工具 (单一职责 / schema 严格)"
# [OMNI] tags=tool,doctor,git-log,structured,no-shell-injection
# [OMNI] material_id="material:diagnosis.doctor.tools.git_log_tool.skeleton.py"
"""GitLogTool · 结构化 git log 工具 (V0 骨架).

修 stage10_4hr_interrogation 真问题 1+5:
- 真问题 1: SPEC.tools 没 bash, 不能跑 git log (违反 agent_first.md §8.5)
- 真问题 5: agent_first.md 要 bash 占主 vs agent_tools.md 禁通用 bash 矛盾
- 折中: 立结构化 git_log 工具

设计 (跟 agent_tools.md 原则 1 一致):
- 不接受任意 git 命令字符串
- 严格 schema: since (date) / until (optional date) / max_count (default 50) / paths (optional path filter)
- 内部固定调 git log --pretty=format:%h|%ai|%an|%s --shortstat
- 解析后返结构化 commit list, agent 看结构不看 stdout

不开通用 bash → LLM 不会写 grep -rn 巨型目录 / rm -rf /. 跟 agent_tools.md 立场一致.

供 MetaDiagnosticAgent 走 10 问 #7 (过去运行/修复经历) 用. 后续也可给 work_pattern_scanner 复用.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)


def _project_root() -> Path:
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        if (p / "src" / "omnicompany").is_dir() and (p / "docs").is_dir():
            return p
    return here.parents[6] if len(here.parents) > 6 else here.parent


_PROJECT_ROOT = _project_root()


@dataclass
class GitCommitSummary:
    short_hash: str
    date: str
    author: str
    subject: str
    files_changed: int = 0
    lines_added: int = 0
    lines_deleted: int = 0


def _parse_git_log_output(stdout: str) -> list[GitCommitSummary]:
    """解析 'git log --pretty=format:%h|%ai|%an|%s --shortstat' 输出."""
    commits: list[GitCommitSummary] = []
    lines = stdout.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if "|" in line and len(line.split("|", 3)) == 4:
            h, date, author, subject = line.split("|", 3)
            cs = GitCommitSummary(
                short_hash=h.strip(),
                date=date.strip(),
                author=author.strip(),
                subject=subject.strip(),
            )
            # 看下一行是否 shortstat
            if i + 1 < len(lines) and ("files changed" in lines[i + 1] or "file changed" in lines[i + 1]):
                sl = lines[i + 1]
                fm = re.search(r"(\d+) files? changed", sl)
                am = re.search(r"(\d+) insertions?", sl)
                dm = re.search(r"(\d+) deletions?", sl)
                if fm:
                    cs.files_changed = int(fm.group(1))
                if am:
                    cs.lines_added = int(am.group(1))
                if dm:
                    cs.lines_deleted = int(dm.group(1))
                i += 2
            else:
                i += 1
            commits.append(cs)
        else:
            i += 1
    return commits


class GitLogTool(SingleToolRouter):
    """结构化 git log 工具 — 严格 schema 不接通用 shell."""

    TOOL_NAME: ClassVar[str] = "git_log"
    DESCRIPTION: ClassVar[str] = (
        "Read recent git commits in a structured way (no shell access). "
        "Use this for diagnosing team work pattern + fix history (元诊断 10 问 #7). "
        "禁直接 shell — 工具 schema 严格只接受 since/until/max_count/paths 字段, 内部固定调 git log + 解析输出. "
        "Returns list of GitCommitSummary {short_hash, date, author, subject, files_changed, lines_added, lines_deleted}."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "since": {
                "type": "string",
                "description": "Git log --since (date string e.g. '2026-04-30' or '2 weeks ago')",
            },
            "until": {
                "type": "string",
                "description": "Optional. Git log --until (date string)",
            },
            "max_count": {
                "type": "integer",
                "description": "Max commits to return (default 50, max 500)",
                "default": 50,
                "maximum": 500,
            },
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional. Filter commits to those touching these paths (relative to project root)",
                "default": [],
            },
        },
        "required": ["since"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = True
    IS_READONLY: ClassVar[bool] = True

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        since = (args.get("since") or "").strip()
        if not since:
            raise ToolExecutionError("since 字段必填 (例 '2026-04-30' 或 '2 weeks ago')")
        until = (args.get("until") or "").strip() or None
        max_count = int(args.get("max_count") or 50)
        if max_count > 500:
            max_count = 500
        paths = args.get("paths") or []

        # 严格构造命令 (固定模板, 用户 input 只填 schema 字段)
        cmd = [
            "git", "log",
            f"--since={since}",
            f"-{max_count}",
            "--pretty=format:%h|%ai|%an|%s",
            "--shortstat",
        ]
        if until:
            cmd.append(f"--until={until}")
        if paths:
            # 路径分隔符
            cmd.append("--")
            cmd.extend([str(p) for p in paths])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                cwd=str(_PROJECT_ROOT), timeout=15,
            )
        except subprocess.TimeoutExpired:
            raise ToolExecutionError("git log 超时 (15s). 请缩小 since/max_count 范围")
        except Exception as e:
            raise ToolExecutionError(f"git log 调用失败: {e}")

        if result.returncode != 0:
            raise ToolExecutionError(f"git log 返非 0: {result.stderr.strip()[:200]}")

        commits = _parse_git_log_output(result.stdout)
        commit_dicts = [asdict(c) for c in commits]

        # 写 ctx scratch 让 agent 后续读
        scratch = getattr(ctx, "scratch", None)
        if scratch is not None and isinstance(scratch, dict):
            scratch["last_git_log_result"] = commit_dicts

        # 返简短摘要给 agent (不返完整 commit list 防 token 暴炸)
        summary_lines = [f"git log (since={since}{', until='+until if until else ''}): {len(commits)} commits"]
        for c in commits[:10]:
            summary_lines.append(f"  {c.short_hash} {c.date[:10]} {c.subject[:80]}")
        if len(commits) > 10:
            summary_lines.append(f"  ... ({len(commits) - 10} more, 完整 list 在 ctx.scratch.last_git_log_result)")
        return "\n".join(summary_lines)
