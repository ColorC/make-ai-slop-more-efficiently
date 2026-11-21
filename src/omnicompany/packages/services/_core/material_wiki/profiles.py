# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=router status=active
# [OMNI] summary="从项目 Wiki frontmatter 或平台 profile 指针清单加载材料分类；平台清单不复制项目 taxonomy"
# [OMNI] material_id="material:core.material_wiki.profiles.py"
"""Material Wiki project profile loaders."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import yaml

from .schema import (
    MaterialWikiContractError,
    MaterialWikiProfile,
    ProfileRegistration,
    ProfileSource,
    TaxonomySourceKind,
)


def resolve_canonical_root(root_ref: str, *, omnicompany_root: str | Path) -> Path:
    """Resolve a declared canonical root without guessing from project names."""
    prefix, separator, relative = root_ref.partition(":")
    if not separator or prefix not in {"workspace", "omnicompany"}:
        raise MaterialWikiContractError(
            f"canonical.root_ref 必须是 workspace:<path> 或 omnicompany:<path>，实际为 {root_ref!r}"
        )
    omni_root = Path(omnicompany_root).resolve()
    base = omni_root.parent if prefix == "workspace" else omni_root
    resolved = (base / relative).resolve()
    if prefix == "workspace" and not resolved.is_relative_to(omni_root.parent):
        raise MaterialWikiContractError(f"canonical.root_ref 越出 workspace：{root_ref}")
    if prefix == "omnicompany" and not resolved.is_relative_to(omni_root):
        raise MaterialWikiContractError(f"canonical.root_ref 越出 omnicompany：{root_ref}")
    return resolved


def resolve_loader_path(loader_ref: str, *, omnicompany_root: str | Path) -> Path:
    """Resolve a profile loader pointer while preserving its conceptual source ref."""
    path_ref = loader_ref.split("#", 1)[0]
    return resolve_canonical_root(path_ref, omnicompany_root=omnicompany_root)


def _frontmatter(path: Path) -> Mapping[str, Any]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise MaterialWikiContractError(f"{path} 缺少 YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise MaterialWikiContractError(f"{path} 的 YAML frontmatter 未闭合") from exc
    raw = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(raw, Mapping):
        raise MaterialWikiContractError(f"{path} 的 YAML frontmatter 必须是对象")
    return raw


def load_profile_from_wiki(path: str | Path) -> MaterialWikiProfile:
    source_path = Path(path).resolve()
    frontmatter = _frontmatter(source_path)
    raw = frontmatter.get("material_profile")
    if not isinstance(raw, Mapping):
        raise MaterialWikiContractError(f"{source_path} 缺少 material_profile")
    return MaterialWikiProfile.from_mapping(
        raw,
        source=ProfileSource(
            kind=TaxonomySourceKind.WIKI_FRONTMATTER,
            ref=source_path.as_posix(),
        ),
    )


def load_profile_manifest(path: str | Path) -> tuple[ProfileRegistration, ...]:
    manifest_path = Path(path)
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, Mapping) or int(raw.get("schema_version", 0)) != 1:
        raise MaterialWikiContractError("profile manifest schema_version 必须为 1")
    profiles_raw = raw.get("profiles") or ()
    if not isinstance(profiles_raw, list):
        raise MaterialWikiContractError("profile manifest 的 profiles 必须是数组")
    profiles = tuple(ProfileRegistration.from_mapping(item) for item in profiles_raw)
    ids = [profile.profile_id for profile in profiles]
    projects = [profile.project_id for profile in profiles]
    if len(ids) != len(set(ids)):
        raise MaterialWikiContractError("profile manifest 存在重复 profile_id")
    if len(projects) != len(set(projects)):
        raise MaterialWikiContractError("profile manifest 存在重复 project_id")
    return profiles
