from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - hook path must stay fail-open.
    yaml = None  # type: ignore[assignment]


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
ROOT_FIELDS = ("allowed_write_roots", "external_write_roots", "write_roots")
PATH_FIELDS = ("allowed_write_paths", "external_write_paths", "write_paths")
# BOSS SIGHT 块 3 R8: plan.md frontmatter 可声明 hard_block_on_denial: true 让 guard
# 命中变 "硬阻断" (subagent 停 + emit subagent.blocked 唤起总控). 不声明默认软, 跟旧行为一致.
HARD_BLOCK_FIELDS = ("hard_block_on_denial", "guard_hard_block")
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_editor"})
SHELL_TOOLS = frozenset({"Bash", "Shell"})
SHELL_WRITE_RE = re.compile(
    r"(\s>\s|\s>>\s|\|\s*tee\b|\bSet-Content\b|\bAdd-Content\b|\bOut-File\b|"
    r"\bNew-Item\b|\bRemove-Item\b|\bMove-Item\b|\bCopy-Item\b|\bmkdir\b|\btouch\b|"
    r"\brm\b|\bdel\b|\bpython\b.*\bopen\s*\([^)]*[\"'][wax][\"'])",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class PlannedWriteScope:
    workspace_root: Path
    roots: tuple[Path, ...]
    paths: tuple[Path, ...]
    sources: tuple[str, ...]
    plan_id: str | None = None
    plan_md: Path | None = None
    project_md: Path | None = None
    # BOSS SIGHT 块 3 R8: 硬阻断标志. plan / project frontmatter 任一声明
    # hard_block_on_denial: true 即 hard mode. 默认 False = 软 (旧行为).
    hard_block_on_denial: bool = False


@dataclass(frozen=True)
class ToolPathCandidate:
    key: str
    raw: str
    resolved: Path | None = None
    error: str | None = None


def repo_root() -> Path:
    from omnicompany.core.config import omni_workspace_root
    return omni_workspace_root()


def plans_root() -> Path:
    return repo_root() / "docs" / "plans"


def parse_frontmatter(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}
    if yaml is None:
        return {}
    try:
        data = yaml.safe_load(match.group(1)) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def project_md_for_plan(plan_id: str | None) -> Path | None:
    if not plan_id:
        return None
    parts = plan_id.split("/")
    if len(parts) < 3:
        return None
    candidate = plans_root() / "/".join(parts[:-1]) / "project.md"
    return candidate if candidate.is_file() else None


def plan_md_for_plan(plan_id: str | None) -> Path | None:
    if not plan_id:
        return None
    candidate = plans_root() / plan_id / "plan.md"
    return candidate if candidate.is_file() else None


def _iter_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        raw = value.get("path") or value.get("root") or value.get("dir")
        return [str(raw)] if raw else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            out.extend(_iter_values(item))
        return out
    return []


def _is_absish(raw: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or raw.startswith(("/", "\\"))


def _resolve_declared_path(raw: str, *, base_dir: Path) -> Path | None:
    raw = os.path.expandvars(raw.strip())
    if not raw:
        return None
    try:
        p = Path(raw)
        if p.is_absolute() or _is_absish(raw):
            return p.resolve()
        return (base_dir / p).resolve()
    except OSError:
        return None


def _collect_declared_paths(
    meta: dict[str, Any],
    *,
    base_dir: Path,
    fields: tuple[str, ...],
) -> list[Path]:
    out: list[Path] = []
    for field in fields:
        for raw in _iter_values(meta.get(field)):
            resolved = _resolve_declared_path(raw, base_dir=base_dir)
            if resolved is not None:
                out.append(resolved)
    return out


def _meta_hard_block(meta: dict[str, Any]) -> bool:
    """检查 plan/project frontmatter 任一 HARD_BLOCK_FIELDS = true."""
    for f in HARD_BLOCK_FIELDS:
        v = meta.get(f)
        if isinstance(v, bool) and v:
            return True
        if isinstance(v, str) and v.strip().lower() in {"true", "yes", "1", "hard"}:
            return True
    return False


def planned_write_scope(*, cwd: str, active_plan: str | None) -> PlannedWriteScope:
    workspace_root = Path(cwd or os.getcwd()).resolve()
    roots: list[Path] = [workspace_root]
    paths: list[Path] = []
    sources: list[str] = ["workspace"]
    hard_block = False

    project_md = project_md_for_plan(active_plan)
    if project_md:
        meta = parse_frontmatter(project_md)
        roots.extend(_collect_declared_paths(meta, base_dir=project_md.parent, fields=ROOT_FIELDS))
        paths.extend(_collect_declared_paths(meta, base_dir=project_md.parent, fields=PATH_FIELDS))
        sources.append(str(project_md.relative_to(repo_root())))
        if _meta_hard_block(meta):
            hard_block = True

    plan_md = plan_md_for_plan(active_plan)
    if plan_md:
        meta = parse_frontmatter(plan_md)
        roots.extend(_collect_declared_paths(meta, base_dir=plan_md.parent, fields=ROOT_FIELDS))
        paths.extend(_collect_declared_paths(meta, base_dir=plan_md.parent, fields=PATH_FIELDS))
        sources.append(str(plan_md.relative_to(repo_root())))
        if _meta_hard_block(meta):
            hard_block = True

    return PlannedWriteScope(
        workspace_root=workspace_root,
        roots=tuple(dict.fromkeys(roots)),
        paths=tuple(dict.fromkeys(paths)),
        sources=tuple(sources),
        plan_id=active_plan,
        plan_md=plan_md,
        project_md=project_md,
        hard_block_on_denial=hard_block,
    )


def is_inside_or_equal(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


# 常见自动有序目录 (2026-09-12 用户裁定放宽): 由规范或 harness 按"任务/会话"自动分层,
# 写进去不会变成游离垃圾, 无需计划声明即可放行。临时文件的首选落点仍是项目内
# `.omni/tmp/<task-or-session>/` 并在 progress.md 留痕; harness 会话临时区只是放行,
# 写入时由 hook 提醒改用项目内落点。系统临时目录下的散乱路径不在此列。
PROJECT_TMP = "project_tmp"
CLAUDE_SCRATCHPAD = "claude_scratchpad"
CLAUDE_MEMORY = "claude_memory"

_PROJECT_TMP_RE = re.compile(r"/\.omni/(?:tmp|sandbox/drafts)/[^/]+/")
_CLAUDE_SCRATCHPAD_RE = re.compile(r"^claude/[^/]+/[^/]+/scratchpad/")
_CLAUDE_MEMORY_RE = re.compile(r"^\.claude[^/]*/projects/[^/]+/memory/")

TMP_PLACEMENT_HINT = (
    "临时文件首选 <项目主目录>/.omni/tmp/<task-or-session>/ (任何项目下都直接放行), "
    "并在当前计划 progress.md 留痕; 会话临时区随会话失联, 不留痕。"
)


def _relative_under(path_posix: str, root: Path) -> str | None:
    try:
        root_posix = root.resolve().as_posix().lower().rstrip("/") + "/"
    except OSError:
        return None
    return path_posix[len(root_posix):] if path_posix.startswith(root_posix) else None


def auto_ordered_write_kind(path: Path) -> str | None:
    """Return which auto-ordered directory ``path`` falls in, or None."""
    p = path.as_posix().lower()
    if _PROJECT_TMP_RE.search(p):
        return PROJECT_TMP
    rel = _relative_under(p, Path(tempfile.gettempdir()))
    if rel is not None and _CLAUDE_SCRATCHPAD_RE.match(rel):
        return CLAUDE_SCRATCHPAD
    rel = _relative_under(p, Path.home())
    if rel is not None and _CLAUDE_MEMORY_RE.match(rel):
        return CLAUDE_MEMORY
    return None


def is_allowed(path: Path, scope: PlannedWriteScope) -> bool:
    if any(path == allowed for allowed in scope.paths):
        return True
    if auto_ordered_write_kind(path) is not None:
        return True
    return any(is_inside_or_equal(path, root) for root in scope.roots)


def tool_path_candidates(tool_name: str, tool_input: dict[str, Any]) -> list[ToolPathCandidate]:
    if not isinstance(tool_input, dict):
        return []
    candidates: list[ToolPathCandidate] = []
    if tool_name in WRITE_TOOLS:
        for key in ("file_path", "path", "notebook_path"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(ToolPathCandidate(key=key, raw=value.strip()))
    elif tool_name in SHELL_TOOLS:
        command = str(tool_input.get("command") or "")
        if not SHELL_WRITE_RE.search(command):
            return []
        value = tool_input.get("cwd")
        if isinstance(value, str) and value.strip():
            candidates.append(ToolPathCandidate(key="cwd", raw=value.strip()))
    return candidates


def resolve_candidate(candidate: ToolPathCandidate, *, cwd: str) -> ToolPathCandidate:
    root = Path(cwd or os.getcwd()).resolve()
    try:
        p = Path(candidate.raw)
        resolved = p.resolve() if p.is_absolute() or _is_absish(candidate.raw) else (root / p).resolve()
    except OSError as exc:
        return ToolPathCandidate(candidate.key, candidate.raw, error=str(exc))
    return ToolPathCandidate(candidate.key, candidate.raw, resolved=resolved)


def denial_message(tool_name: str, candidate: ToolPathCandidate, scope: PlannedWriteScope) -> str | None:
    if candidate.error:
        return f"OmniChat blocked {tool_name}: cannot resolve `{candidate.key}` path {candidate.raw!r}: {candidate.error}"
    if candidate.resolved is None or is_allowed(candidate.resolved, scope):
        return None
    plan_ref = f"`docs/plans/{scope.plan_id}/plan.md`" if scope.plan_id else "an active plan"
    project_ref = f" and `{scope.project_md.relative_to(repo_root())}`" if scope.project_md else ""
    declared = "\n".join(f"- {p}" for p in [*scope.roots, *scope.paths])
    return (
        f"OmniChat blocked {tool_name}: `{candidate.key}` resolves outside the planned write scope.\n"
        f"Requested: {candidate.resolved}\n"
        f"Workspace: {scope.workspace_root}\n"
        f"Active plan: {scope.plan_id or '(none)'}\n"
        f"Declared scope:\n{declared}\n\n"
        f"临时/草稿文件不用申请外部目录: {TMP_PLACEMENT_HINT}\n\n"
        "To write there, first read the relevant standards/specs, then update "
        f"{plan_ref}{project_ref} frontmatter with one of:\n"
        "allowed_write_roots:\n"
        "  - \"D:/path/to/owned/output-dir\"\n"
        "allowed_write_paths:\n"
        "  - \"D:/path/to/exact-file.ext\"\n"
        "Document what each external directory is for in the plan before retrying."
    )


def planned_write_denial(
    *,
    cwd: str,
    active_plan: str | None,
    tool_name: str,
    tool_input: dict[str, Any],
) -> str | None:
    """旧 API: 仅返回 denial message. 保留以免破坏现有调用."""
    result = planned_write_denial_with_scope(
        cwd=cwd, active_plan=active_plan, tool_name=tool_name, tool_input=tool_input,
    )
    return result[0] if result else None


def planned_write_denial_with_scope(
    *,
    cwd: str,
    active_plan: str | None,
    tool_name: str,
    tool_input: dict[str, Any],
) -> tuple[str, PlannedWriteScope] | None:
    """块 3 R8 新 API: 返回 (denial_message, scope). 调用方可以拿 scope.hard_block_on_denial
    决定是软 (record + 继续) 还是硬 (emit subagent.blocked + 拦)."""
    scope = planned_write_scope(cwd=cwd, active_plan=active_plan)
    for candidate in tool_path_candidates(tool_name, tool_input):
        resolved = resolve_candidate(candidate, cwd=cwd)
        msg = denial_message(tool_name, resolved, scope)
        if msg:
            return msg, scope
    return None
