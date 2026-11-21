"""Fresh task-only launch roots for externally managed agent workers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


PURE_RUNTIME_ROOT = Path.home() / ".cloudcli" / "pure-agent-runtime"


def prepare_pure_runtime(*, task: str | None, provider: str) -> Path:
    """Create an isolated cwd containing only the explicit task instruction.

    The directory is deliberately outside the workspace and is never populated
    from it. Provider login homes are inherited separately through the normal
    account environment, so this does not create a second login or copy secrets.
    """
    runtime = PURE_RUNTIME_ROOT / uuid4().hex
    runtime.mkdir(parents=True, exist_ok=False)
    task_text = (task or "等待用户原始需求；不要读取本机已有内容。").strip()
    instruction = (
        "# Pure agent task context\n\n"
        "当前任务与本机已有内容完全无关。只根据用户原始需求执行。\n"
        "不要读取父工作区、历史会话、memory、hooks、skills、插件或其它额外上下文。\n"
        "当前运行时仅提供账号登录态；账号目录不属于任务材料。\n\n"
        f"provider: {provider}\n"
        f"created_at: {datetime.now(timezone.utc).isoformat()}\n\n"
        "用户原始任务：\n"
        f"{task_text}\n"
    )
    (runtime / "AGENTS.md").write_text(instruction, encoding="utf-8")
    return runtime


def cleanup_pure_runtime(cwd: str | Path | None) -> bool:
    """Remove one finished pure-session root, never an arbitrary directory."""
    if not cwd:
        return False
    root = PURE_RUNTIME_ROOT.resolve()
    candidate = Path(cwd).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    if candidate == root or not candidate.is_dir():
        return False
    try:
        (candidate / "AGENTS.md").unlink(missing_ok=True)
        candidate.rmdir()
    except OSError:
        return False
    return True


__all__ = ["PURE_RUNTIME_ROOT", "cleanup_pure_runtime", "prepare_pure_runtime"]
