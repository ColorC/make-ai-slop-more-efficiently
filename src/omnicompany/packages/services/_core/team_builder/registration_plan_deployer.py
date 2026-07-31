# [OMNI] origin=codex domain=services/_core/team_builder ts=2026-07-25T00:00:00Z type=tool
# [OMNI] summary="Deploy one reviewed Team Builder registration plan without rerunning generation"
# [OMNI] why="The legacy deploy path reran LLM work and restored whole dirty files on rollback"
"""Safe deployment for an already reviewed Team Builder registration plan."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from omnicompany.core.config import omni_workspace_root
from omnicompany.packages.services._core.team_builder.package_location import (
    canonical_package_file_path,
    canonical_team_package_path,
    team_python_module,
)
from omnicompany.packages.services._core.team_builder.workers.registrar import (
    RegistrarWorker,
)
from omnicompany.protocol.anchor import VerdictKind


_REGISTRY_ANCHOR = "    # ── 自动注册 G2 里的 yaml team"
_MARKER_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _sha256_json(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _runtime_name(team_name: str) -> str:
    return team_name.replace("_", "-")


def _registry_markers(runtime_name: str) -> tuple[str, str]:
    if not _MARKER_NAME.fullmatch(runtime_name):
        raise ValueError(f"invalid generated runtime name: {runtime_name!r}")
    return (
        f'    # <team-builder-generated name="{runtime_name}">',
        f'    # </team-builder-generated name="{runtime_name}">',
    )


def build_registry_block(
    *,
    runtime_name: str,
    pipeline_entry_code: str,
) -> str:
    """Wrap one generated TeamEntry in an exact rollback/idempotency marker."""

    start, end = _registry_markers(runtime_name)
    code = pipeline_entry_code.rstrip()
    if not code or f'name="{runtime_name}"' not in code:
        raise ValueError(
            "pipeline_entry_code does not register the expected runtime name"
        )
    return f"{start}\n{code}\n{end}\n"


def insert_registry_block(
    source: str,
    *,
    runtime_name: str,
    block: str,
) -> tuple[str, bool]:
    """Insert one exact block before dynamic registry loaders.

    Returns ``(text, changed)``.  An identical existing block is idempotent;
    any other occurrence of the runtime name is rejected.
    """

    start, end = _registry_markers(runtime_name)
    if start in source or end in source:
        if block in source:
            return source, False
        raise ValueError(
            f"generated registry marker for {runtime_name!r} is incomplete or differs"
        )
    if f'name="{runtime_name}"' in source:
        raise ValueError(
            f"runtime name {runtime_name!r} is already registered elsewhere"
        )
    if _REGISTRY_ANCHOR not in source:
        raise ValueError("core registry insertion anchor is missing")
    return source.replace(_REGISTRY_ANCHOR, block + "\n" + _REGISTRY_ANCHOR, 1), True


def remove_registry_block(
    source: str,
    *,
    block: str,
) -> str:
    """Remove only the exact block written by this deployer."""

    needle = block + "\n"
    if needle in source:
        return source.replace(needle, "", 1)
    if block in source:
        return source.replace(block, "", 1)
    raise ValueError("cannot rollback: exact generated registry block is absent")


def validate_registration_plan(
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-run deterministic Registrar validation and reject stale/tampered plans."""

    candidate = deepcopy(dict(plan))
    if candidate.get("dry_run") is not True:
        raise ValueError("registration plan must still be an un-deployed dry-run")
    if candidate.get("human_review_required") is not True:
        raise ValueError("registration plan must retain the human-review gate")

    team_name = candidate.get("team_name")
    if not isinstance(team_name, str) or not team_name.strip():
        raise ValueError("registration plan team_name is required")
    target = canonical_team_package_path(
        candidate.get("target_package_path"),
        team_name=team_name,
    )
    files = candidate.get("files")
    if not isinstance(files, Mapping) or not files:
        raise ValueError("registration plan files mapping is required")
    canonical_files: dict[str, str] = {}
    for raw_rel_path, content in files.items():
        rel_path = canonical_package_file_path(raw_rel_path)
        if not isinstance(content, str):
            raise ValueError(f"registration plan file must contain text: {rel_path}")
        canonical_files[rel_path] = content

    verdict = RegistrarWorker().run(
        {
            "team_name": team_name,
            "target_package_path": target,
            "files": canonical_files,
        }
    )
    if verdict.kind != VerdictKind.PASS or not isinstance(verdict.output, dict):
        raise ValueError(f"Registrar rejected plan: {verdict.diagnosis}")
    expected = verdict.output
    if expected.get("files") != canonical_files:
        raise ValueError(
            "registration plan files differ from deterministic Registrar output"
        )
    if candidate.get("pipeline_entry_code") != expected.get("pipeline_entry_code"):
        raise ValueError(
            "registration plan pipeline entry is stale or does not match its files"
        )
    if candidate.get("files_to_write") != expected.get("files_to_write"):
        raise ValueError(
            "registration plan file manifest is stale or does not match its files"
        )
    if candidate.get("compile_check") != expected.get("compile_check"):
        raise ValueError("registration plan compile summary is stale")
    return expected


def _resolved_target_root(
    *,
    repo_root: Path,
    target_package_path: str,
) -> Path:
    root = (repo_root / target_package_path).resolve()
    packages_root = (repo_root / "src/omnicompany/packages").resolve()
    try:
        root.relative_to(packages_root)
    except ValueError as exc:
        raise ValueError("target package resolves outside packages root") from exc
    return root


def _assert_target_available(target_root: Path) -> None:
    if not target_root.exists():
        return
    extant = [path for path in target_root.rglob("*") if path.is_file()]
    if extant:
        raise ValueError(
            f"target package already contains files; refusing overwrite: {target_root}"
        )


def _write_package(
    *,
    target_root: Path,
    files: Mapping[str, str],
) -> list[Path]:
    from omnicompany.runtime.buses import DiskBus

    _assert_target_available(target_root)
    written: list[Path] = []
    bus = DiskBus()
    for rel_path, content in files.items():
        path = target_root / canonical_package_file_path(rel_path)
        bus.write(path, content, atomic=True)
        written.append(path)
    return written


def _remove_created_package_files(
    *,
    target_root: Path,
    written: list[Path],
) -> None:
    """Rollback only exact files created in this invocation."""

    resolved_root = target_root.resolve()
    for path in reversed(written):
        resolved = path.resolve()
        resolved.relative_to(resolved_root)
        if resolved.is_file():
            resolved.unlink()
    directories = sorted(
        {
            parent
            for path in written
            for parent in path.parents
            if parent == target_root or target_root in parent.parents
        },
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for directory in directories:
        if directory.exists():
            try:
                directory.rmdir()
            except OSError:
                pass


def _refresh_filesystem_import_caches() -> None:
    """Refresh local package finders without the broken global metadata hook."""

    from importlib.machinery import FileFinder

    for finder in tuple(sys.path_importer_cache.values()):
        if isinstance(finder, FileFinder):
            finder.invalidate_caches()


def _smoke_package(
    *,
    module: str,
    expected_team_id: str | None = None,
) -> dict[str, Any]:
    _refresh_filesystem_import_caches()
    for loaded_name in list(sys.modules):
        if loaded_name == module or loaded_name.startswith(f"{module}."):
            del sys.modules[loaded_name]

    package = importlib.import_module(module)
    team = package.build_team()
    bindings = package.build_bindings()
    node_ids = {node.id for node in team.nodes}
    binding_ids = set(bindings)
    if node_ids != binding_ids:
        raise ValueError(
            f"Team nodes and bindings differ: nodes={node_ids}, bindings={binding_ids}"
        )
    if expected_team_id and team.id != expected_team_id:
        raise ValueError(
            f"built TeamSpec.id {team.id!r} differs from expected {expected_team_id!r}"
        )
    if any(not hasattr(worker, "run") for worker in bindings.values()):
        raise ValueError("one or more bindings do not implement run()")
    return {
        "team_id": team.id,
        "nodes": sorted(node_ids),
        "bindings": sorted(binding_ids),
        "positions": [position.id for position in team.positions],
    }


def _verify_runtime_registry(
    *,
    runtime_name: str,
    expected_team_id: str,
) -> None:
    from omnicompany.core import registry
    from omnicompany.core import pipelines

    _refresh_filesystem_import_caches()
    importlib.reload(pipelines)
    pipelines.register_all()
    entry = registry.get(runtime_name)
    if entry is None:
        raise ValueError(f"runtime registry cannot see {runtime_name!r}")
    if entry.build_team().id != expected_team_id:
        raise ValueError("runtime registry entry builds a different TeamSpec")


def _project_for_binding(project_id: str) -> dict[str, Any]:
    from omnicompany.core.projects_registry import list_projects

    current = next(
        (item for item in list_projects() if item.get("id") == project_id),
        None,
    )
    if current is None:
        raise ValueError(f"Project is not registered: {project_id!r}")
    return current


def _bind_project(*, project_id: str, team_id: str) -> dict[str, Any]:
    from omnicompany.core.projects_registry import set_project

    current = _project_for_binding(project_id)
    team_ids = [
        item
        for item in current.get("team_ids") or []
        if isinstance(item, str) and item.strip()
    ]
    if team_id not in team_ids:
        team_ids.append(team_id)
    return set_project(
        project_id,
        by="controller",
        team_ids=team_ids,
        primary_team_id=team_id,
    )


def deploy_registration_plan(
    plan: Mapping[str, Any],
    *,
    approved: bool,
    project_id: str | None = None,
    check_only: bool = False,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Deploy, register, and optionally bind one reviewed Team without running it."""

    if not approved:
        raise ValueError("explicit reviewed-plan approval is required")
    root = (repo_root or omni_workspace_root()).resolve()
    validated = validate_registration_plan(plan)
    team_name = validated["team_name"]
    target = validated["target_package_path"]
    files = validated["files"]
    runtime_name = _runtime_name(team_name)
    module = team_python_module(target, team_name=team_name)
    target_root = _resolved_target_root(
        repo_root=root,
        target_package_path=target,
    )
    _assert_target_available(target_root)
    if project_id:
        _project_for_binding(project_id)
    pipelines_path = root / "src/omnicompany/core/pipelines.py"
    pipelines_text = pipelines_path.read_text(encoding="utf-8")
    block = build_registry_block(
        runtime_name=runtime_name,
        pipeline_entry_code=validated["pipeline_entry_code"],
    )
    new_pipelines_text, registry_change = insert_registry_block(
        pipelines_text,
        runtime_name=runtime_name,
        block=block,
    )

    preflight = {
        "team_name": team_name,
        "runtime_name": runtime_name,
        "target_package_path": target,
        "python_module": module,
        "files": sorted(files),
        "registry_change_required": registry_change,
        "project_id": project_id,
        "plan_sha256": _sha256_json(plan),
        "check_only": check_only,
    }
    if check_only:
        return {"status": "ready", **preflight}

    from omnicompany.runtime.buses import DiskBus

    written: list[Path] = []
    registry_written = False
    try:
        written = _write_package(target_root=target_root, files=files)
        smoke = _smoke_package(module=module)
        if registry_change:
            DiskBus().write(pipelines_path, new_pipelines_text, atomic=True)
            registry_written = True
        _verify_runtime_registry(
            runtime_name=runtime_name,
            expected_team_id=smoke["team_id"],
        )
    except Exception:
        if registry_written:
            current = pipelines_path.read_text(encoding="utf-8")
            rolled_back = remove_registry_block(current, block=block)
            DiskBus().write(pipelines_path, rolled_back, atomic=True)
        if written:
            _remove_created_package_files(
                target_root=target_root,
                written=written,
            )
        raise

    project = (
        _bind_project(project_id=project_id, team_id=smoke["team_id"])
        if project_id
        else None
    )
    return {
        "status": "deployed_not_executed",
        **preflight,
        "check_only": False,
        "smoke": smoke,
        "runtime_visible": True,
        "project_binding": (
            {
                "project_id": project["id"],
                "team_ids": project.get("team_ids") or [],
                "primary_team_id": project.get("primary_team_id"),
            }
            if project
            else None
        ),
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "build_registry_block",
    "deploy_registration_plan",
    "insert_registry_block",
    "remove_registry_block",
    "validate_registration_plan",
]
