# [OMNI] origin=claude-code domain=services/_governance ts=2026-07-03T00:00:00Z type=router
# [OMNI] material_id="material:governance.scheduler.work_meter.py"
"""工作量计量读数器(锚: overnight-run.md 第六节工作项3"工作里程表")。

监督触发器从自然时间改为工作量: 判断本身零模型调用、毫秒级、全部本地读数。
四个计量名:
  commits         — 主仓提交数(git rev-list --count HEAD, 隐藏窗口子进程)
  ledger_events   — 留痕账本行数(data/ledger/events.jsonl 行数)
  llm_calls       — 内部模型调用行数(data/llm/meter.jsonl 行数)
  sessions        — 会话登记文件数(会话目录下 *.json 文件数)

任一计量源缺失(不是 git 仓/文件不存在/目录不存在)一律按 0 计, 绝不抛异常
(错误样本㊄: 计数源文件缺失→按零计不崩)。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from omnicompany.core.config import omni_workspace_root

METRIC_NAMES: frozenset[str] = frozenset({"commits", "ledger_events", "llm_calls", "sessions"})

# Windows 隐藏子进程窗口(禁止前台跳控制台窗口铁律)。非 Windows 取 0。
_BG_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _count_commits(workspace_root: Path) -> int:
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_BG_FLAGS,
        )
        if proc.returncode != 0:
            return 0
        return int((proc.stdout or "0").strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _count_lines(path: Path) -> int:
    try:
        if not path.is_file():
            return 0
        n = 0
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for _ in f:
                n += 1
        return n
    except OSError:
        return 0


def _count_session_files(sessions_dir: Path) -> int:
    try:
        if not sessions_dir.is_dir():
            return 0
        return sum(1 for _ in sessions_dir.glob("*.json"))
    except OSError:
        return 0


def read_metric(
    name: str,
    *,
    workspace_root: Path | None = None,
    sessions_dir: Path | None = None,
) -> int:
    """读单个计量名当前值。未知计量名/源缺失一律返回 0, 绝不抛异常。"""
    try:
        root = Path(workspace_root) if workspace_root is not None else omni_workspace_root()
        if name == "commits":
            return _count_commits(root)
        if name == "ledger_events":
            return _count_lines(root / "data" / "ledger" / "events.jsonl")
        if name == "llm_calls":
            return _count_lines(root / "data" / "llm" / "meter.jsonl")
        if name == "sessions":
            sdir = Path(sessions_dir) if sessions_dir is not None else (root / ".omni" / "sessions")
            return _count_session_files(sdir)
        return 0
    except Exception:  # noqa: BLE001  # 计量读数绝不能成为故障点
        return 0


def read_all_metrics(
    *,
    workspace_root: Path | None = None,
    sessions_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """读全部计量名, 每项标 value + available(源是否存在)。"""
    root = Path(workspace_root) if workspace_root is not None else omni_workspace_root()
    out: dict[str, dict[str, Any]] = {}
    for name in METRIC_NAMES:
        value = read_metric(name, workspace_root=root, sessions_dir=sessions_dir)
        if name == "commits":
            available = (root / ".git").exists()
        elif name == "ledger_events":
            available = (root / "data" / "ledger" / "events.jsonl").is_file()
        elif name == "llm_calls":
            available = (root / "data" / "llm" / "meter.jsonl").is_file()
        elif name == "sessions":
            sdir = Path(sessions_dir) if sessions_dir is not None else (root / ".omni" / "sessions")
            available = sdir.is_dir()
        else:
            available = False
        out[name] = {"value": value, "available": available}
    return out
