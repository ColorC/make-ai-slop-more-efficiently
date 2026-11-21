"""Small, sparse-first helpers for creating isolated Git worktrees."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
import stat
from typing import Callable


# One creation authority: source selections, never a full checkout fallback.
DEFAULT_WORKTREE_SOURCE_PATHS = (
    "src", "docs", "tests", "scripts", "config", "packages", "services",
    "templates", "contracts", "tools", "assets", "webworks", "workshop", "gmkit", "shared",
    ".github", "AGENTS.md", "CLAUDE.md", "README.md", "LICENSE", "CHANGELOG.md",
    "CONTRIBUTING.md", "SECURITY.md", "pyproject.toml", "requirements.txt",
    "requirements-game-observatory-optional.txt", "uv.lock", "poetry.lock",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "tsconfig.json", ".gitmodules", ".gitignore", ".gitattributes", ".editorconfig", "mkdocs.yml",
    "Makefile", "Dockerfile",
)
WORKTREE_RUNTIME_PREFIXES = (
    ".git", "var/runtime", "var/vendor", "data", "runtime", "input", "inputs", "logs",
    "recordings", "captures", ".omni", ".worktrees", "node_modules", ".venv",
    "venv", "build", "dist", "target", ".cache", ".next", ".pnpm-store",
)
WORKTREE_NESTED_DEPENDENCIES = ("node_modules", ".venv", ".cache", ".next")


def worktree_sparse_patterns(source_paths=None) -> tuple[str, ...]:
    if source_paths is not None and (not isinstance(source_paths,(tuple,list)) or isinstance(source_paths,str)):
        raise GitWorktreeSetupError("Source selections must be a list of relative paths")
    paths = DEFAULT_WORKTREE_SOURCE_PATHS if source_paths is None else tuple(source_paths)
    if not paths or len(paths)>64:
        raise GitWorktreeSetupError("Declare 1-64 source paths; full checkout is not supported")
    includes = []
    for raw in paths:
        if not isinstance(raw, str) or len(raw)>512 or any(c in raw for c in "\n\r\x00*?"):
            raise GitWorktreeSetupError("Sparse selections must be bounded literal relative paths")
        normalized = raw.replace("\\", "/").rstrip("/")
        path = PurePosixPath(normalized)
        if path.is_absolute() or not normalized or normalized=="." or ":" in normalized or ".." in path.parts:
            raise GitWorktreeSetupError("Sparse selection escapes the repository")
        key = str(path).casefold()
        if any(key==p or key.startswith(p+"/") or p.startswith(key+"/") for p in WORKTREE_RUNTIME_PREFIXES):
            raise GitWorktreeSetupError(f"Runtime/input/cache selection is blocked: {normalized}")
        if any(part.casefold() in WORKTREE_NESTED_DEPENDENCIES for part in path.parts):
            raise GitWorktreeSetupError(f"Dependency/cache selection is blocked: {normalized}")
        escaped = str(path).replace("[", "\\[").replace("]", "\\]")
        includes.append("/"+escaped+"/")
        includes.append("/"+escaped)
    excludes = ["!/{}/".format(p) for p in WORKTREE_RUNTIME_PREFIXES]
    excludes += ["!/**/{}/".format(p) for p in WORKTREE_NESTED_DEPENDENCIES]
    return tuple(dict.fromkeys(includes+excludes))


def preflight_new_worktree_path(worktree_path: Path) -> None:
    target = worktree_path.absolute()
    for part in [*reversed(target.parents), target]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0)&0x400:
            raise GitWorktreeSetupError(f"Worktree ancestor is a link/reparse point: {part}")
        if part==target:
            raise GitWorktreeSetupError(f"New worktree target already exists: {target}")



class GitWorktreeSetupError(RuntimeError):
    """A worktree setup command failed before the caller may report success."""


GitRunner = Callable[..., subprocess.CompletedProcess]


def _raise_git_failure(
    step: str,
    args: list[str],
    result: subprocess.CompletedProcess,
    worktree_path: Path,
) -> None:
    command = " ".join(["git", *args])
    stdout = (result.stdout or "").strip() or "<empty>"
    stderr = (result.stderr or "").strip() or "<empty>"
    raise GitWorktreeSetupError(
        f"git {step} failed (exit={result.returncode}) for worktree path "
        f"{worktree_path.resolve()}; command={command!r}; stdout={stdout}; stderr={stderr}"
    )


def _run_git(
    run_git: GitRunner,
    args: list[str],
    *,
    cwd: Path,
    timeout: int,
    worktree_path: Path,
    step: str,
    input_text: str | None = None,
) -> subprocess.CompletedProcess:
    try:
        result = run_git(
            args,
            cwd=cwd,
            timeout=timeout,
            input_text=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or "<empty>"
        stderr = exc.stderr or "<empty>"
        raise GitWorktreeSetupError(
            f"git {step} timed out after {timeout}s for worktree path {worktree_path.resolve()}; "
            f"command={' '.join(['git', *args])!r}; stdout={stdout}; stderr={stderr}"
        ) from exc
    except OSError as exc:
        raise GitWorktreeSetupError(
            f"git {step} could not run for worktree path {worktree_path.resolve()}; "
            f"command={' '.join(['git', *args])!r}; error={exc}"
        ) from exc
    if result.returncode != 0:
        _raise_git_failure(step, args, result, worktree_path)
    return result


def create_sparse_worktree(
    repo_root: Path,
    worktree_path: Path,
    branch: str,
    base_ref: str,
    *,
    run_git: GitRunner,
    timeout: int,
    source_paths: tuple[str, ...] | None = None,
    manifest: dict | None = None,
) -> Path | None:
    """Create a fresh source-only worktree; retained runtime stays at canonical paths.

    Returns the ``copy.json`` written into the worktree's Git admin directory (``manifest``
    adds or overrides fields such as purpose and consumer), or None when it could not be written.
    """
    sparse_patterns = worktree_sparse_patterns(source_paths)
    preflight_new_worktree_path(worktree_path)
    if branch.startswith("-") or base_ref.startswith("-"):
        raise GitWorktreeSetupError("Branch/base ref cannot be command options")
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    add_args = [
        "worktree",
        "add",
        "--no-checkout",
        "-b",
        branch,
        str(worktree_path),
        base_ref,
    ]
    _run_git(
        run_git,
        add_args,
        cwd=repo_root,
        timeout=timeout,
        worktree_path=worktree_path,
        step="worktree add",
    )

    sparse_args = ["sparse-checkout", "set", "--no-cone", "--stdin"]
    sparse_input = "\n".join(sparse_patterns) + "\n"
    _run_git(
        run_git,
        sparse_args,
        cwd=worktree_path,
        timeout=timeout,
        worktree_path=worktree_path,
        step="sparse-checkout set",
        input_text=sparse_input,
    )

    checkout_args = ["checkout", branch]
    _run_git(
        run_git,
        checkout_args,
        cwd=worktree_path,
        timeout=timeout,
        worktree_path=worktree_path,
        step="sparse checkout",
    )
    return write_worktree_manifest(
        repo_root, worktree_path, branch, base_ref, run_git=run_git, timeout=timeout, manifest=manifest,
    )


def write_worktree_manifest(
    repo_root: Path,
    worktree_path: Path,
    branch: str,
    base_ref: str,
    *,
    run_git: GitRunner,
    timeout: int,
    manifest: dict | None = None,
) -> Path | None:
    """Write the worktree's ``copy.json`` into its Git admin directory (directory_semantic_archive.md).

    The admin directory (``<common-dir>/worktrees/<name>``) is removed by ``git worktree remove``
    together with the registration, and a file there never shows up as untracked in the
    worktree.  A failure here never undoes or fails the worktree that was just created.
    """
    import json
    from datetime import datetime, timezone

    try:
        result = run_git(["rev-parse", "--absolute-git-dir"], cwd=worktree_path, timeout=timeout, input_text=None)
        admin = Path((result.stdout or "").strip())
        if result.returncode != 0 or not admin.is_absolute() or not admin.is_dir():
            return None
        if admin.parent.name != "worktrees" or worktree_path.resolve() in (admin, *admin.parents):
            return None
        data = {
            "schema": "omni.copy/1",
            "kind": "worktree",
            "source": str(repo_root),
            "source_version": base_ref,
            "branch": branch,
            "path": str(worktree_path),
            "purpose": None,
            "consumer": None,
            "expires_when": "功能完成或发布确认后，用不带 --force 的 git worktree remove 回收（AGENTS.md「worktree 完成即回收」）",
            "max_count": 1,
            "reclaimer": "当前执行 Agent（AGENTS.md：清理责任不按创建者划分）",
            "producer": "omnicompany.core.git_worktree.create_sparse_worktree",
            "created": datetime.now(timezone.utc).isoformat(),
        }
        data.update(manifest or {})
        target = admin / "copy.json"
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target
    except Exception:  # noqa: BLE001 - the manifest is bookkeeping; the worktree stays usable
        return None

DEFAULT_WORKTREE_SPARSE_PATTERNS = worktree_sparse_patterns()
