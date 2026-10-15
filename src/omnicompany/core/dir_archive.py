"""Directory semantic archive: what a path is, who owns it, and whether it may be deleted.

Standard: ``docs/standards/_global/directory_semantic_archive.md``.  Declarations stay where
they already live -- ``workspace.yaml`` (``entries``, ``machine_roots``), each project's
``PROJECT_INDEX.md`` ``directory_contract`` (roles, placements, ``records_root``,
``copies_root``, ``areas``, ``external_roots``) and batch manifests (``record.json`` /
``copy.json``; a worktree's ``copy.json`` sits in its Git admin directory).

This module reads those sources and answers questions about paths.  It never writes a
declaration and never follows a link: a path with a link on its ancestor chain is reported
as such instead of inheriting the meaning of the link target.  The answer for the whole
machine is computed on demand (the "projection" of the standard); nothing is cached on disk.
"""
from __future__ import annotations

import json
import ntpath
import os
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

FORMAL_ROLES = frozenset(
    {"src", "docs", "tests", "input", "data", "publish", "tool", "history", "archive",
     "reference", "external", "runtime", "project"}
)
TEMP_ROLES = frozenset({"tmp", "drafts", "records", "copies", "cache", "worktree"})
DECLARABLE_ROLES = (FORMAL_ROLES - {"project"}) | TEMP_ROLES
STATUSES = frozenset({"active", "retiring", "needs-owner"})
# Contract roles whose subtree may never hold temp areas; ``runtime`` is meant to be subdivided.
STRICT_FORMAL_CONTRACT_ROLES = ("src", "docs", "tests", "input", "archive")
# Drafts are excluded on purpose: a draft has to be promoted (or judged worthless) first.
DELETABLE_BY_ROLE = frozenset({"tmp", "cache"})
MANIFEST_NAMES = ("record.json", "copy.json")
STANDARD = r"C:/workspace/omnicompany\docs\standards\_global\directory_semantic_archive.md"
AGENTS_DEFAULT = r"C:/workspace/AGENTS.md「文件落点与目录层级」无声明时的默认位置"

_PLACEMENT_ROLE = {"worktrees": "worktree", "references": "reference", "publish": "publish"}
_WORKSPACE_CATEGORY_ROLE = {"归档": "archive", "临时": "tmp", "运行态": "data"}
_LAYER_RANK = {
    "machine": 0, "workspace": 1, "external": 2, "project-root": 3, "default": 4,
    "role": 5, "placement": 5, "records-root": 6, "copies-root": 6, "area": 7,
}
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_BATCH_ROOT_LAYERS = frozenset({"records-root", "copies-root", "area", "external"})


@dataclass(frozen=True)
class Declaration:
    path: str
    host: str
    layer: str
    role: str
    owner: str | None
    producer: str | None = None
    consumers: tuple[str, ...] = ()
    lifecycle: str | None = None
    status: str = "active"
    declared_in: str = ""
    note: str | None = None


@dataclass
class Archive:
    declarations: list[Declaration]
    errors: list[dict[str, Any]]
    workspace_root: str
    # project id -> directory_contract.canonical_root.  Other roots of a project (a read-only
    # P4 Client, a storage container) are boundaries, not trees the project lays out.
    canonical_roots: dict[str, str] = field(default_factory=dict)


# ── path helpers (lexical; never resolve through links) ─────────────────────────


def _key(path: str | Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(path))).rstrip("\\")


def _within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent + "\\")


def _join(base: str | Path, raw: str) -> str:
    raw = raw.strip()
    return ntpath.normpath(raw if ntpath.isabs(raw) else ntpath.join(str(base), raw))


def _is_abs_windows(raw: str) -> bool:
    drive, rest = ntpath.splitdrive(raw)
    return bool(drive) and rest.startswith(("\\", "/"))


def _consumers(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(v) for v in value if v is not None)
    return (str(value),)


# ── schema of the 2026-10-12 contract extensions (used by projects_registry) ────


def _entry_shape(entry: Any, field: str, *, need_host: bool) -> tuple[str, str, str] | None:
    if not isinstance(entry, dict):
        return ("LAYOUT_EXT_ENTRY_TYPE", "每项必须是映射（path / role / lifecycle / status …）", field)
    path = entry.get("path")
    if not isinstance(path, str) or not path.strip():
        return ("LAYOUT_EXT_PATH_TYPE", "path 必须是非空路径字符串", f"{field}.path")
    role = entry.get("role")
    if role not in DECLARABLE_ROLES:
        return ("LAYOUT_EXT_ROLE", f"role 必须取闭集 {sorted(DECLARABLE_ROLES)}", f"{field}.role")
    lifecycle = entry.get("lifecycle")
    if not isinstance(lifecycle, str) or not lifecycle.strip():
        return ("LAYOUT_EXT_LIFECYCLE", "lifecycle 必须写明留多久、何时到期", f"{field}.lifecycle")
    status = entry.get("status", "active")
    if status not in STATUSES:
        return ("LAYOUT_EXT_STATUS", f"status 必须取 {sorted(STATUSES)}", f"{field}.status")
    for key in ("producer", "note", "evidence"):
        value = entry.get(key)
        if value is not None and not isinstance(value, str):
            return ("LAYOUT_EXT_FIELD_TYPE", f"{key} 必须是文字或省略", f"{field}.{key}")
    consumers = entry.get("consumers")
    if consumers is not None and not isinstance(consumers, (str, list)):
        return ("LAYOUT_EXT_FIELD_TYPE", "consumers 必须是文字、列表或省略", f"{field}.consumers")
    if need_host:
        host = entry.get("host")
        if not isinstance(host, str) or not host.strip():
            return ("LAYOUT_EXT_HOST", "host 必须写明机器（本机写 local）", f"{field}.host")
    return None


def validate_contract_extensions(
    contract: dict[str, Any],
    *,
    declared_roots: list[Path],
    resolved_roles: dict[str, Path],
    resolve: Callable[[str], Path],
    within: Callable[[Path, Path], bool],
) -> tuple[str, str, str] | None:
    """Return ``(code, message, field_path)`` for the first invalid extension key, else None.

    ``resolve`` and ``within`` are the contract validator's own path rules so the new keys
    are judged exactly like roles and placements.
    """

    def inside_roots(path: Path) -> bool:
        return any(within(path, root) for root in declared_roots)

    def overlaps(path: Path, role_path: Path) -> bool:
        return within(path, role_path) or within(role_path, path)

    for key in ("records_root", "copies_root"):
        raw = contract.get(key)
        if raw is None:
            continue
        field = f"directory_contract.{key}"
        if not isinstance(raw, str) or not raw.strip():
            return ("LAYOUT_EXT_PATH_TYPE", f"{key} 必须为路径字符串或 null", field)
        path = resolve(raw)
        if not inside_roots(path):
            return ("LAYOUT_EXT_OUT_OF_BOUNDS", f"{key} 必须落在已声明 roots 内；项目根以外的位置写进 external_roots", field)
        for name in STRICT_FORMAL_CONTRACT_ROLES:
            role_path = resolved_roles.get(name)
            if role_path is not None and overlaps(path, role_path):
                return ("LAYOUT_EXT_ROLE_CONFLICT", f"{key} 与正式角色 {name} 重合或互为祖先", field)

    areas = contract.get("areas")
    if areas is not None:
        if not isinstance(areas, list):
            return ("LAYOUT_AREAS_TYPE", "areas 必须是列表或 null", "directory_contract.areas")
        for index, area in enumerate(areas):
            field = f"directory_contract.areas[{index}]"
            error = _entry_shape(area, field, need_host=False)
            if error:
                return error
            path = resolve(area["path"])
            if not inside_roots(path):
                return ("LAYOUT_EXT_OUT_OF_BOUNDS", "areas 路径必须在已声明 roots 内；项目根以外的位置写进 external_roots", f"{field}.path")
            if area["role"] in TEMP_ROLES:
                for name in STRICT_FORMAL_CONTRACT_ROLES:
                    role_path = resolved_roles.get(name)
                    if role_path is not None and overlaps(path, role_path):
                        return ("LAYOUT_AREA_ROLE_CONFLICT", f"临时区域（{area['role']}）不能落在正式角色 {name} 里或包含它", f"{field}.path")
            else:
                for name in ("tmp", "drafts"):
                    role_path = resolved_roles.get(name)
                    if role_path is not None and within(path, role_path):
                        return ("LAYOUT_AREA_ROLE_CONFLICT", f"正式区域（{area['role']}）不能落在临时角色 {name} 里", f"{field}.path")

    externals = contract.get("external_roots")
    if externals is not None:
        if not isinstance(externals, list):
            return ("LAYOUT_EXTERNAL_ROOTS_TYPE", "external_roots 必须是列表或 null", "directory_contract.external_roots")
        for index, entry in enumerate(externals):
            field = f"directory_contract.external_roots[{index}]"
            error = _entry_shape(entry, field, need_host=True)
            if error:
                return error
            raw = os.path.expandvars(entry["path"]) if entry["host"] == "local" else entry["path"]
            if not _is_abs_windows(raw) and not raw.startswith("\\\\"):
                return ("LAYOUT_EXT_PATH_TYPE", "external_roots 的 path 必须是带盘符的绝对路径", f"{field}.path")
            if entry["host"] == "local" and inside_roots(Path(ntpath.normpath(raw))):
                return ("LAYOUT_EXT_INSIDE_ROOTS", "这个位置在项目根内，写进 areas", f"{field}.path")
    return None


# ── loading declarations ───────────────────────────────────────────────────────


def _default_workspace_root() -> Path:
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root().parent


def _registry_projects() -> list[tuple[str, Path]]:
    from omnicompany.core import projects_registry

    rows = []
    for item in projects_registry.list_projects():
        index = item.get("index_path")
        if item.get("id") and index:
            rows.append((str(item["id"]), Path(index)))
    return rows


def _load_yaml(path: Path) -> Any:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _declaration_from_entry(entry: dict[str, Any], *, layer: str, owner: str | None, declared_in: str, base: str) -> Declaration:
    host = str(entry.get("host") or "local")
    raw = entry["path"]
    if host == "local":
        raw = os.path.expandvars(raw)
    return Declaration(
        path=_join(base, raw),
        host=host,
        layer=layer,
        role=entry["role"],
        owner=entry.get("owner", owner),
        producer=entry.get("producer"),
        consumers=_consumers(entry.get("consumers")),
        lifecycle=entry.get("lifecycle"),
        status=entry.get("status", "active"),
        declared_in=declared_in,
        note=entry.get("note") or entry.get("evidence"),
    )


def _load_workspace(path: Path, workspace_root: Path, errors: list[dict[str, Any]]) -> tuple[list[Declaration], list[dict[str, Any]]]:
    if not path.is_file():
        errors.append({"source": str(path), "code": "WORKSPACE_YAML_MISSING", "message": "workspace.yaml 不存在"})
        return [], []
    try:
        data = _load_yaml(path) or {}
    except Exception as exc:  # noqa: BLE001 - report any parse failure as data
        errors.append({"source": str(path), "code": "WORKSPACE_YAML_INVALID", "message": str(exc)})
        return [], []
    decls: list[Declaration] = []
    if not isinstance(data, dict):
        errors.append({"source": str(path), "code": "WORKSPACE_YAML_INVALID", "message": "workspace.yaml 顶层不是映射"})
        return [], []
    raw_entries = [e for e in data.get("entries") or [] if isinstance(e, dict) and isinstance(e.get("name"), str)]
    machine_roots = data.get("machine_roots")
    if machine_roots is not None and not isinstance(machine_roots, list):
        errors.append({"source": str(path), "code": "MACHINE_ROOTS_TYPE", "message": "machine_roots 必须是列表", "path": "machine_roots"})
        machine_roots = None
    for index, entry in enumerate(machine_roots or []):
        field = f"machine_roots[{index}]"
        error = _entry_shape(entry, field, need_host=True)
        if error:
            errors.append({"source": str(path), "code": error[0], "message": error[1], "path": error[2]})
            continue
        decls.append(_declaration_from_entry(entry, layer="machine", owner=entry.get("owner"), declared_in=f"{path} :: {field}", base=str(workspace_root)))
    return decls, raw_entries


def _project_root_paths(fm: dict[str, Any], index_dir: Path) -> list[str]:
    roots = []
    for item in fm.get("roots") or []:
        raw = item.get("path") if isinstance(item, dict) else item
        if isinstance(raw, str) and raw.strip():
            roots.append(_join(index_dir, raw))
    return roots


def _load_project(project_id: str, index: Path, errors: list[dict[str, Any]],
                  canonical_roots: dict[str, str] | None = None) -> list[Declaration]:
    from omnicompany.core import projects_registry

    parsed = projects_registry.parse_index_file(index)
    fm = parsed.get("data") if isinstance(parsed.get("data"), dict) else None
    if not parsed.get("ok"):
        errors.append({
            "source": str(index), "project": project_id, "code": parsed.get("error_code"),
            "message": parsed.get("error"), "path": parsed.get("error_path"),
        })
    if fm is None:
        return []
    roots = _project_root_paths(fm, index.parent)
    decls = [
        Declaration(path=root, host="local", layer="project-root", role="project", owner=project_id,
                    declared_in=f"{index} :: roots")
        for root in roots
    ]
    contract = fm.get("directory_contract") if parsed.get("ok") else None
    if not isinstance(contract, dict):
        for root in roots:
            decls.extend(_defaults(project_id, root, set()))
        return decls
    canonical = _join(index.parent, str(contract.get("canonical_root") or (roots[0] if roots else index.parent)))
    if canonical_roots is not None:
        canonical_roots[project_id] = canonical
    # With a contract, roots other than the canonical one are boundaries the project reads
    # (e.g. the shared read-only D:/P4/main/Client), not trees it owns.
    decls = [
        d if _key(d.path) == _key(canonical) else Declaration(
            path=d.path, host=d.host, layer=d.layer, role="reference", owner=d.owner,
            declared_in=d.declared_in, note="项目的额外根（只读边界，不归本项目布局）",
        )
        for d in decls
    ]
    lifecycle = contract.get("lifecycle") or {}
    declared_roles: set[str] = set()
    for key, raw in (contract.get("roles") or {}).items():
        if raw is None:
            continue
        declared_roles.add(key)
        decls.append(Declaration(
            path=_join(canonical, raw), host="local", layer="role", role=key, owner=project_id,
            lifecycle=lifecycle.get(key), declared_in=f"{index} :: directory_contract.roles.{key}",
        ))
    for key, raw in (contract.get("placements") or {}).items():
        if raw is None:
            continue
        decls.append(Declaration(
            path=_join(canonical, raw), host="local", layer="placement", role=_PLACEMENT_ROLE.get(key, key),
            owner=project_id, lifecycle=lifecycle.get(key),
            declared_in=f"{index} :: directory_contract.placements.{key}",
        ))
    for key, role, layer in (("records_root", "records", "records-root"), ("copies_root", "copies", "copies-root")):
        raw = contract.get(key)
        if raw:
            decls.append(Declaration(
                path=_join(canonical, raw), host="local", layer=layer, role=role, owner=project_id,
                lifecycle="批次按 record.json / copy.json 清单到期", declared_in=f"{index} :: directory_contract.{key}",
            ))
    for i, area in enumerate(contract.get("areas") or []):
        decls.append(_declaration_from_entry(area, layer="area", owner=project_id,
                                             declared_in=f"{index} :: directory_contract.areas[{i}]", base=canonical))
    for i, entry in enumerate(contract.get("external_roots") or []):
        decls.append(_declaration_from_entry(entry, layer="external", owner=project_id,
                                             declared_in=f"{index} :: directory_contract.external_roots[{i}]", base=canonical))
    for root in roots:
        decls.extend(_defaults(project_id, root, declared_roles))
    return decls


def _defaults(project_id: str, root: str, declared_roles: set[str]) -> list[Declaration]:
    """AGENTS.md defaults: ``.omni/tmp`` and ``.omni/sandbox/drafts`` when the contract names none."""
    out = []
    if "tmp" not in declared_roles:
        out.append(Declaration(path=_join(root, ".omni/tmp"), host="local", layer="default", role="tmp",
                               owner=project_id, lifecycle="任务结束核验后清理", declared_in=AGENTS_DEFAULT))
    if "drafts" not in declared_roles:
        out.append(Declaration(path=_join(root, ".omni/sandbox/drafts"), host="local", layer="default", role="drafts",
                               owner=project_id, lifecycle="审核后提升到正式位置或清理", declared_in=AGENTS_DEFAULT))
    return out


def load_archive(
    *,
    workspace_yaml: Path | None = None,
    projects: Iterable[tuple[str, Path]] | None = None,
    workspace_root: Path | None = None,
) -> Archive:
    """Collect every declaration.  Arguments exist for tests; defaults read the real sources."""
    root = Path(workspace_root) if workspace_root is not None else _default_workspace_root()
    yaml_path = Path(workspace_yaml) if workspace_yaml is not None else root / "workspace.yaml"
    errors: list[dict[str, Any]] = []
    decls, ws_entries = _load_workspace(yaml_path, root, errors)
    project_rows = list(projects) if projects is not None else _registry_projects()
    project_decls: list[Declaration] = []
    canonical_roots: dict[str, str] = {}
    for project_id, index in project_rows:
        project_decls.extend(_load_project(project_id, Path(index), errors, canonical_roots))
    root_owner = {_key(d.path): d.owner for d in project_decls if d.layer == "project-root"}
    for entry in ws_entries:
        raw_path = entry.get("path") or entry["name"]
        path = _join(root, str(raw_path))
        category = str(entry.get("category") or "")
        status = entry.get("status")
        field = f"entries[name={entry['name']}]"
        # Archive fields on a workspace entry (directory_semantic_archive.md 第二节): dir_role,
        # project, producer, consumers, lifecycle, archive_status, evidence.  All optional.
        role = entry.get("dir_role") or _WORKSPACE_CATEGORY_ROLE.get(category, "project")
        if role not in DECLARABLE_ROLES | {"project"}:
            errors.append({"source": str(yaml_path), "code": "LAYOUT_EXT_ROLE",
                           "message": f"dir_role 必须取闭集 {sorted(DECLARABLE_ROLES)}", "path": f"{field}.dir_role"})
            role = "project"
        archive_status = entry.get("archive_status", "active")
        if archive_status not in STATUSES:
            errors.append({"source": str(yaml_path), "code": "LAYOUT_EXT_STATUS",
                           "message": f"archive_status 必须取 {sorted(STATUSES)}", "path": f"{field}.archive_status"})
            archive_status = "needs-owner"
        lifecycle = entry.get("lifecycle")
        if lifecycle is not None and (not isinstance(lifecycle, str) or not lifecycle.strip()):
            errors.append({"source": str(yaml_path), "code": "LAYOUT_EXT_LIFECYCLE",
                           "message": "lifecycle 必须是非空文字或省略", "path": f"{field}.lifecycle"})
            lifecycle = None
        owner = entry.get("project") or root_owner.get(_key(path))
        if owner is None and role == "project":
            owner = f"workspace:{entry['name']}"
        decls.append(Declaration(
            path=path, host="local", layer="workspace",
            role=role,
            owner=owner,
            producer=entry.get("producer"),
            consumers=_consumers(entry.get("consumers")),
            lifecycle=lifecycle, status=archive_status,
            declared_in=f"{yaml_path} :: {field}",
            note="；".join(str(x) for x in (category, entry.get("role"), status, entry.get("evidence")) if x),
        ))
    decls.extend(project_decls)
    return Archive(declarations=decls, errors=errors, workspace_root=str(root), canonical_roots=canonical_roots)


# ── explaining a path ──────────────────────────────────────────────────────────


def _ancestor_links(path: str) -> list[str]:
    links = []
    current = Path(path)
    for part in [*reversed(current.parents), current]:
        try:
            info = os.lstat(part)
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            links.append(str(part))
    return links


def _read_manifest(file: Path) -> dict[str, Any]:
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"file": str(file), "dir": str(file.parent), "manifest": None, "error": str(exc)}
    if not isinstance(data, dict):
        return {"file": str(file), "dir": str(file.parent), "manifest": None, "error": "清单不是 JSON 对象"}
    return {"file": str(file), "dir": str(file.parent), "manifest": data, "error": None}


def _dirs_up(path: str, stop: str) -> list[Path]:
    """``path`` (or its parent when it is a file) up to but excluding ``stop``."""
    start = Path(path)
    if start.is_file():
        start = start.parent
    out = []
    stop_key = _key(stop)
    current = start
    while _within(_key(current), stop_key) and _key(current) != stop_key:
        out.append(current)
        if current.parent == current:
            break
        current = current.parent
    return out


def _find_manifest(path: str, stop: str) -> dict[str, Any] | None:
    for directory in _dirs_up(path, stop):
        for name in MANIFEST_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return _read_manifest(candidate)
    return None


def _find_worktree(path: str, stop: str) -> dict[str, Any] | None:
    for directory in [*_dirs_up(path, stop), Path(stop)]:
        marker = directory / ".git"
        if not marker.is_file():
            continue
        try:
            text = marker.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not text.startswith("gitdir:"):
            continue
        admin = Path(text[len("gitdir:"):].strip())
        if not admin.is_absolute():
            admin = directory / admin
        manifest = admin / "copy.json"
        info: dict[str, Any] = {"root": str(directory), "admin_dir": str(admin), "copy_json": None}
        if manifest.is_file():
            info["copy_json"] = _read_manifest(manifest)
        return info
    return None


def _conflicts(chain: list[Declaration]) -> list[str]:
    found = []
    for outer in chain:
        for inner in chain:
            if inner is outer or not _within(_key(inner.path), _key(outer.path)) or _key(inner.path) == _key(outer.path):
                continue
            if outer.role in STRICT_FORMAL_CONTRACT_ROLES and inner.role in TEMP_ROLES:
                found.append(f"临时角色 {inner.role}（{inner.declared_in}）落在正式角色 {outer.role}（{outer.declared_in}）里")
            if outer.role in {"tmp", "drafts"} and inner.role in FORMAL_ROLES and inner.role != "project":
                found.append(f"正式角色 {inner.role}（{inner.declared_in}）落在临时角色 {outer.role}（{outer.declared_in}）里")
    return found


def _lexical_defaults(path: str, candidates: list[Declaration], archive: Archive) -> list[Declaration]:
    """AGENTS.md defaults for any ``<dir>/.omni/tmp`` and ``<dir>/.omni/sandbox/drafts`` on the path.

    Mirrors ``verified_cleanup.tmp_zone``: the default temp area does not depend on the
    containing project being registered.  An explicit declaration of the same path wins.
    """
    parts = ntpath.normpath(path).split("\\")
    lowered = [p.lower() for p in parts]
    declared = {_key(d.path) for d in candidates}
    out = []
    for i in range(1, len(parts) - 1):
        if lowered[i] != ".omni":
            continue
        base = "\\".join(parts[:i])
        for tail, role, lifecycle in ((("tmp",), "tmp", "任务结束核验后清理"),
                                      (("sandbox", "drafts"), "drafts", "审核后提升到正式位置或清理")):
            if tuple(lowered[i + 1 : i + 1 + len(tail)]) != tail:
                continue
            area = "\\".join(parts[: i + 1 + len(tail)])
            if _key(area) in declared:
                continue
            out.append(Declaration(path=area, host="local", layer="default", role=role,
                                   owner=_owner_of(base, archive), lifecycle=lifecycle, declared_in=AGENTS_DEFAULT))
    return out


def _owner_of(directory: str, archive: Archive) -> str | None:
    """Project owning ``directory``: an exact project root, else the workspace entry, else the deepest root."""
    key = _key(directory)
    exact = [d for d in archive.declarations if d.layer == "project-root" and _key(d.path) == key]
    if exact:
        return exact[0].owner
    entries = [d for d in archive.declarations if d.layer == "workspace" and _key(d.path) == key]
    if entries:
        return entries[0].owner
    roots = [d for d in archive.declarations if d.layer == "project-root" and _within(key, _key(d.path))]
    return max(roots, key=lambda d: len(d.path)).owner if roots else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def explain(target: str | Path, *, host: str = "local", archive: Archive | None = None,
            now: datetime | None = None) -> dict[str, Any]:
    """Answer owner / role / producer / lifecycle / delete basis for one absolute path."""
    archive = archive if archive is not None else load_archive()
    # The target is taken literally: a directory may really be named ``%SystemDrive%``.
    raw = str(target)
    if not (_is_abs_windows(raw) or raw.startswith("\\\\")):
        answer: dict[str, Any] = {"target": raw, "host": host, "error": "目标必须是带盘符的绝对路径"}
        answer["delete"] = {"allowed": False, "basis": None, "problems": [answer["error"]]}
        return answer
    path = ntpath.normpath(raw)
    key = _key(path)
    candidates = [d for d in archive.declarations if d.host == host and _within(key, _key(d.path))]
    if host == "local":
        candidates.extend(_lexical_defaults(path, candidates, archive))
    chain = sorted(candidates, key=lambda d: (len(_key(d.path)), _LAYER_RANK.get(d.layer, 0)))
    best = chain[-1] if chain else None
    links = _ancestor_links(path) if host == "local" else []
    batch = worktree = None
    role = best.role if best else None
    if host == "local" and best is not None and not links:
        if best.role in TEMP_ROLES - {"worktree"} and key != _key(best.path):
            batch = _find_manifest(path, best.path)
        worktree = _find_worktree(path, best.path)
        if worktree is not None:
            role = "worktree"
    answer = {
        "target": path,
        "host": host,
        "undeclared": best is None,
        "role": role,
        "formal": role is None or role in FORMAL_ROLES,
        "owner": best.owner if best else None,
        "producer": best.producer if best else None,
        "consumers": list(best.consumers) if best else [],
        "lifecycle": best.lifecycle if best else None,
        "status": best.status if best else None,
        "declared_in": best.declared_in if best else None,
        "note": best.note if best else None,
        "declaration": asdict(best) if best else None,
        "chain": [{"path": d.path, "layer": d.layer, "role": d.role, "owner": d.owner, "declared_in": d.declared_in} for d in chain],
        "batch": batch,
        "worktree": worktree,
        "links": links,
        "conflicts": _conflicts(chain),
    }
    if answer["conflicts"]:
        answer["formal"] = True
    answer["delete"] = delete_basis(answer, now=now)
    return answer


def delete_basis(answer: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Whether the archive authorizes deleting ``answer['target']``, and why or why not."""
    now = now or datetime.now(timezone.utc)
    problems: list[str] = []
    basis: str | None = None
    if answer.get("error"):
        return {"allowed": False, "basis": None, "problems": [answer["error"]]}
    if answer["links"]:
        problems.append("目标或祖先是链接/重解析点：" + "；".join(answer["links"]) + "；链接对象单独核验，不继承目标的语义")
    if answer["undeclared"]:
        problems.append(f"未登记：哪一层都没有声明，按正式内容保护；先登记归属（{STANDARD} 第四、六节）")
        return {"allowed": False, "basis": None, "problems": problems}
    declared_in = answer["declared_in"]
    role = answer["role"]
    if answer["conflicts"]:
        problems.append("登记互相矛盾，按正式处理：" + "；".join(answer["conflicts"]))
    if answer["status"] == "needs-owner":
        problems.append(f"登记为 needs-owner（{declared_in}），归属还没确认")
    if role == "worktree":
        problems.append("这是 worktree，用不带 --force 的 git worktree remove 收尾")
    elif role in FORMAL_ROLES:
        problems.append(f"正式角色 {role}（{declared_in}），不按档案删除")
    elif _key(answer["target"]) == _key(answer["declaration"]["path"]):
        if answer["status"] == "retiring" and role in DELETABLE_BY_ROLE and answer["lifecycle"]:
            basis = f"停用的 {role} 区域整体收尾（{declared_in}）；寿命：{answer['lifecycle']}"
        else:
            problems.append(f"这是 {role} 角色的根目录本身（{declared_in}），只删其中的批次；"
                            "要整体收掉，先把登记改成 archive_status/status: retiring 并写明寿命")
    elif role in ("records", "copies"):
        batch = answer["batch"]
        if not batch:
            problems.append(f"{role} 角色里的批次没有清单（no-manifest）：按 records_and_copies_lifecycle.md 2.4 报给产生者，不删")
        elif batch.get("error"):
            problems.append(f"清单读不出：{batch['file']}：{batch['error']}")
        else:
            manifest = batch["manifest"]
            expires = _parse_time(manifest.get("expires"))
            if expires is None:
                extra = ""
                if manifest.get("expires_when"):
                    extra = f"；到期条件是文字说明（{manifest.get('expires_when')}），由回收者 {manifest.get('reclaimer') or '未写'} 按条件执行"
                problems.append(f"清单没有可解析的 expires（{batch['file']}）{extra}")
            elif expires > now:
                problems.append(f"清单未到期（{batch['file']}，expires={manifest.get('expires')}）")
            elif manifest.get("human_evidence") and not manifest.get("evidence_refs"):
                problems.append(f"清单标为给人看的证据，但没有 evidence_refs，证据还没上传（{batch['file']}）")
            else:
                basis = f"批次清单已到期（{batch['file']}，expires={manifest.get('expires')}）"
    elif role in DELETABLE_BY_ROLE:
        batch = answer["batch"]
        manifest = (batch or {}).get("manifest") or {}
        expires = _parse_time(manifest.get("expires"))
        if not answer["lifecycle"]:
            problems.append(f"{role} 角色的登记没写寿命（{declared_in}），没人对删除条件负责，不按角色删")
        elif manifest.get("human_evidence") and (expires is None or expires > now):
            problems.append(f"批次清单标为给人看的证据且未到期（{batch['file']}），按清单保留")
        else:
            basis = f"临时角色 {role}（{declared_in}）；寿命：{answer['lifecycle']}"
    elif role == "drafts":
        problems.append(f"草稿区（{declared_in}）要先把有用的内容提升到正式位置，不按角色整棵删")
    else:
        problems.append(f"角色 {role} 没有按档案删除的规则")
    allowed = not problems and basis is not None
    return {"allowed": allowed, "basis": basis if allowed else None, "problems": problems}


# ── whole-archive views ────────────────────────────────────────────────────────


def batch_candidates(archive: Archive, *, now: datetime | None = None,
                     roots: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """Every ``<root>/<business>/<category>/<batch>`` under local records/copies roots, explained."""
    wanted = {_key(r) for r in roots} if roots else None
    seen: set[str] = set()
    out = []
    for decl in archive.declarations:
        # Only roots declared to follow the <business>/<category>/<batch> layout; a legacy
        # workspace or machine entry merely labelled records/copies is not enumerated as batches.
        if decl.host != "local" or decl.role not in ("records", "copies") or decl.layer not in _BATCH_ROOT_LAYERS:
            continue
        root_key = _key(decl.path)
        if root_key in seen or (wanted is not None and root_key not in wanted):
            continue
        seen.add(root_key)
        root = Path(decl.path)
        if not root.is_dir() or _ancestor_links(str(root)):
            continue
        for business in _subdirs(root):
            for category in _subdirs(business):
                for batch in _subdirs(category):
                    out.append(explain(batch, archive=archive, now=now))
    return out


def _subdirs(path: Path) -> list[Path]:
    try:
        return sorted(Path(e.path) for e in os.scandir(path) if e.is_dir(follow_symlinks=False))
    except OSError:
        return []


_ON_DEMAND_ROLES = frozenset({"tmp", "drafts", "cache", "worktree"})


def _tracked_top_dirs(root: Path) -> set[str] | None:
    """Names of the first-level directories Git tracks under ``root`` (HEAD tree); None when not a work tree."""
    import subprocess

    try:
        r = subprocess.run(["git", "-C", str(root), "ls-tree", "-d", "--name-only", "-z", "HEAD"],
                           capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return {n.decode("utf-8", "replace") for n in r.stdout.split(b"\0") if n}


def drift(archive: Archive, *, now: datetime | None = None,
          tracked_dirs: Callable[[Path], set[str] | None] = _tracked_top_dirs) -> dict[str, Any]:
    """One-level reconciliation of reality against the archive; never recurses or follows links.

    A project root's first-level directory that Git tracks is recorded by the repository itself and is
    formal content (verified_cleanup refuses tracked files), so it is counted under ``tracked_unlabelled``
    rather than reported as undeclared.  Untracked first-level directories named like tool output
    (``worktree_closeout.REGENERABLE_NAMES``, ``*.egg-info``) are counted under ``regenerable_unlabelled``.
    Both counts only quiet the report: ``explain`` and deletion basis are unchanged.
    """
    from omnicompany.core.worktree_closeout import REGENERABLE_NAMES

    local = [d for d in archive.declarations if d.host == "local"]
    findings: list[dict[str, Any]] = []
    tracked_unlabelled: dict[str, int] = {}
    regenerable_unlabelled: dict[str, int] = {}

    def covered(path: str, decls: list[Declaration]) -> bool:
        key = _key(path)
        return any(_within(key, _key(d.path)) or _within(_key(d.path), key) for d in decls)

    workspace = Path(archive.workspace_root)
    for child in _subdirs(workspace):
        if not covered(str(child), local):
            if child.name.lower() in REGENERABLE_NAMES:
                regenerable_unlabelled["workspace"] = regenerable_unlabelled.get("workspace", 0) + 1
                continue
            findings.append({"kind": "undeclared", "scope": "workspace", "path": str(child)})

    contract_projects = {d.owner for d in local if d.layer in ("role", "placement", "records-root", "copies-root", "area")}
    inner = [d for d in local if d.layer not in ("project-root", "workspace", "machine")]
    for decl in local:
        if decl.layer != "project-root" or decl.owner not in contract_projects:
            continue
        canonical = archive.canonical_roots.get(decl.owner or "")
        if canonical is None or _key(canonical) != _key(decl.path):
            continue  # extra roots (a read-only P4 Client, a storage container) are not laid out by this project
        own = [d for d in inner if d.owner == decl.owner]
        tracked: set[str] | None = None
        for child in _subdirs(Path(decl.path)):
            if child.name == ".git":
                continue
            if not covered(str(child), own):
                if tracked is None:
                    tracked = tracked_dirs(Path(decl.path)) or set()
                if child.name in tracked:
                    tracked_unlabelled[decl.owner or ""] = tracked_unlabelled.get(decl.owner or "", 0) + 1
                    continue
                if child.name.lower() in REGENERABLE_NAMES or child.name.lower().endswith(".egg-info"):
                    regenerable_unlabelled[decl.owner or ""] = regenerable_unlabelled.get(decl.owner or "", 0) + 1
                    continue
                findings.append({"kind": "undeclared", "scope": f"project:{decl.owner}", "path": str(child)})
    not_created: list[str] = []
    for decl in local:
        if decl.layer in ("role", "placement", "records-root", "copies-root", "area", "external", "machine"):
            if not os.path.lexists(decl.path):
                # A contract's role/placement is where content goes once produced, and a temporary root is
                # created per task; their absence is a location not yet used, not a stale registration.
                if decl.layer in ("role", "placement") or decl.role in _ON_DEMAND_ROLES:
                    not_created.append(decl.path)
                    continue
                findings.append({"kind": "declared-missing", "path": decl.path, "declared_in": decl.declared_in})
    for answer in batch_candidates(archive, now=now):
        if answer["delete"]["allowed"]:
            findings.append({"kind": "expired-unreaped", "path": answer["target"], "basis": answer["delete"]["basis"]})
        elif answer["batch"] is None:
            findings.append({"kind": "no-manifest", "path": answer["target"]})
    no_contract = sorted({d.owner for d in local if d.layer == "project-root"} - contract_projects - {None})
    return {
        "findings": findings,
        "projects_without_contract": no_contract,
        "tracked_unlabelled": tracked_unlabelled,
        "regenerable_unlabelled": regenerable_unlabelled,
        "declared_not_created": not_created,
        "errors": archive.errors,
        "scope": "workspace root first level; project roots first level (contract projects only; Git-tracked "
                 "and tool-output-named first-level dirs counted, not listed); declared paths existence; "
                 "records/copies batches at business/category/batch depth",
    }
