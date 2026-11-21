# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04T00:00:00Z type=infrastructure
"""EnterWorktreeRouter / ExitWorktreeRouter · git worktree 隔离 SingleTool.

参考: 参考项目/claude-code-analysis/src/tools/EnterWorktreeTool/prompt.ts + ExitWorktreeTool/prompt.ts

核心:
  - EnterWorktree: 创建 git worktree, 切换 session cwd
  - ExitWorktree: 退出 (keep / remove), 回原 cwd
  - 状态持久 .omni/worktree_session.json (跨工具调用)
  - 仅在用户明确说"worktree" 时使用 — prompt 强调
"""
from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from omnicompany.core.git_worktree import GitWorktreeSetupError, create_sparse_worktree
from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_SESSION_FILE = ".omni/worktree_session.json"


def _session_path(ctx: ToolContext) -> Path:
    base = Path(ctx.cwd) if ctx.cwd else Path.cwd()
    # 找 git root, fallback to cwd
    p = base
    while p != p.parent:
        if (p / ".git").exists():
            return p / _SESSION_FILE
        p = p.parent
    return base / _SESSION_FILE


def _read_session(ctx: ToolContext) -> dict:
    p = _session_path(ctx)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_session(ctx: ToolContext, data: dict) -> None:
    p = _session_path(ctx)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_session(ctx: ToolContext) -> None:
    p = _session_path(ctx)
    if p.exists():
        p.unlink()


def _git_run(
    args: list[str],
    cwd: Path,
    *,
    timeout: int = 60,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    kwargs = {
        "cwd": str(cwd),
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
    }
    if input_text is not None:
        kwargs["input"] = input_text
    return subprocess.run(
        ["git"] + args,
        **kwargs,
    )


class EnterWorktreeRouter(SingleToolRouter):
    """Create an isolated git worktree and mark this session as inside it."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ("meta_io.fs.create_file",)

    TOOL_NAME: ClassVar[str] = "EnterWorktree"
    DESCRIPTION: ClassVar[str] = (
        "Create an isolated git worktree and switch session cwd into it.\n"
        "\n"
        "Use ONLY when user explicitly says 'worktree'. NOT for general branch work.\n"
        "\n"
        "Behavior:\n"
        "- Creates worktree at .claude/worktrees/<name>/ from HEAD\n"
        "- New branch <name> based on current HEAD\n"
        "- Returns absolute path to new worktree (caller updates session cwd)\n"
        "- Refuses if already in a worktree session"
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Worktree + branch name (e.g. 'feat-x'). Must be filesystem-safe.",
            },
            "base": {
                "type": "string",
                "description": "Base ref (default HEAD)",
            },
        },
        "required": ["name"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        name = (args.get("name") or "").strip()
        if not name or any(c in name for c in r' /\:*?"<>|'):
            raise ToolExecutionError(f"name must be filesystem-safe (got {name!r})")
        base = (args.get("base") or "HEAD").strip()

        existing = _read_session(ctx)
        if existing.get("active"):
            raise ToolExecutionError(
                f"already in worktree session ({existing.get('worktree_path')}); "
                f"call ExitWorktree first"
            )

        repo_root = Path(ctx.cwd) if ctx.cwd else Path.cwd()
        # 找 git root
        p = repo_root
        while p != p.parent and not (p / ".git").exists():
            p = p.parent
        if not (p / ".git").exists():
            raise ToolExecutionError(f"not in a git repository (searched up from {repo_root})")
        repo_root = p

        worktree_path = repo_root / ".claude" / "worktrees" / name
        if worktree_path.exists():
            raise ToolExecutionError(f"worktree path already exists: {worktree_path}")

        try:
            create_sparse_worktree(
                repo_root,
                worktree_path,
                name,
                base,
                run_git=_git_run,
                timeout=60,
            )
        except GitWorktreeSetupError as exc:
            raise ToolExecutionError(
                str(exc)
            ) from exc

        _write_session(ctx, {
            "active": True,
            "worktree_path": str(worktree_path),
            "branch": name,
            "original_cwd": str(repo_root),
            "entered_at": datetime.now(timezone.utc).isoformat(),
        })
        return (
            f"Worktree created at {worktree_path} (branch: {name}). "
            f"Session cwd should switch there for subsequent work."
        )


class ExitWorktreeRouter(SingleToolRouter):
    """Exit the EnterWorktree session (keep or remove the worktree)."""

    CONSUMED_META_IO: ClassVar[tuple[str, ...]] = ()
    PRODUCED_META_IO: ClassVar[tuple[str, ...]] = ()

    TOOL_NAME: ClassVar[str] = "ExitWorktree"
    DESCRIPTION: ClassVar[str] = (
        "Exit the worktree session created by EnterWorktree.\n"
        "\n"
        "Actions:\n"
        "- `keep`: leave worktree dir + branch on disk (work to resume later)\n"
        "- `remove`: delete worktree dir + branch (clean exit)\n"
        "\n"
        "If called when no session active: no-op (returns informational message).\n"
        "Refuses to remove if there are uncommitted changes unless discard_changes=true."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["keep", "remove"],
                "description": "keep / remove",
            },
            "discard_changes": {
                "type": "boolean",
                "description": "Required true to remove a worktree with uncommitted changes",
            },
        },
        "required": ["action"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        action = args.get("action", "")
        if action not in ("keep", "remove"):
            raise ToolExecutionError("action must be 'keep' or 'remove'")
        discard = bool(args.get("discard_changes", False))

        session = _read_session(ctx)
        if not session.get("active"):
            return "No worktree session active. ExitWorktree is a no-op."

        worktree_path = Path(session["worktree_path"])
        original_cwd = session.get("original_cwd", "")
        branch = session.get("branch", "")

        if action == "keep":
            _clear_session(ctx)
            return f"Exited worktree session (kept on disk at {worktree_path}). Resume by cd to that path."

        # action == remove
        if not discard:
            # 检查是否有未 commit 改动
            status = _git_run(["status", "--porcelain"], cwd=worktree_path)
            if status.returncode == 0 and status.stdout.strip():
                changes = status.stdout.strip().split("\n")
                raise ToolExecutionError(
                    f"worktree has uncommitted changes ({len(changes)} files):\n"
                    + "\n".join(changes[:10])
                    + ("\n..." if len(changes) > 10 else "")
                    + "\nRe-call with discard_changes=true to remove anyway."
                )

        # git worktree remove (要在原 repo root 跑)
        repo_root = Path(original_cwd) if original_cwd else worktree_path.parent.parent.parent
        result = _git_run(
            ["worktree", "remove", "--force" if discard else "", str(worktree_path)],
            cwd=repo_root,
        )
        # filter empty arg
        cmd = ["worktree", "remove"]
        if discard:
            cmd.append("--force")
        cmd.append(str(worktree_path))
        result = _git_run(cmd, cwd=repo_root)
        if result.returncode != 0:
            raise ToolExecutionError(
                f"git worktree remove failed: {result.stderr.strip()[:500]}"
            )

        # 删 branch (best effort)
        if branch:
            del_branch = _git_run(["branch", "-D", branch], cwd=repo_root)
            branch_msg = (
                f"branch {branch} deleted"
                if del_branch.returncode == 0
                else f"branch {branch} delete failed (non-fatal): {del_branch.stderr.strip()[:200]}"
            )
        else:
            branch_msg = "no branch tracked"

        _clear_session(ctx)
        return f"Worktree {worktree_path} removed; {branch_msg}."
