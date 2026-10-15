# [OMNI] origin=codex domain=services/_core/material_profiles ts=2026-08-19T00:00:00+08:00 type=router status=active
# [OMNI] summary="Material 分类 profile 指针注册表：按项目显式加载，不聚合或复制各域完整树"
# [OMNI] material_id="material:core.material_profiles.registry.py"
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from .schema import (
    MaterialClassificationProfile,
    MaterialClassificationProfileError,
    FacetTreeReference,
    MaterialProfileRegistration,
)


def _resolve_loader(loader_ref: str, omnicompany_root: Path) -> Path:
    prefix, separator, relative = loader_ref.partition(":")
    if not separator or prefix not in {"workspace", "omnicompany", "mount"}:
        raise MaterialClassificationProfileError(
            "loader_ref 只允许 workspace:、omnicompany: 或 mount: 引用"
        )
    if prefix == "mount":
        mount_name, separator, mount_relative = relative.replace("\\", "/").partition("/")
        if not separator or not mount_name or not mount_relative:
            raise MaterialClassificationProfileError(
                "mount loader_ref 必须是 mount:<mount_name>/<relative-path>"
            )
        from omnicompany.packages.services._core.registry.external_mounts import (
            discover_external_mounts,
        )

        matches = [
            item
            for item in discover_external_mounts(
                registry_path=omnicompany_root / "config" / "external_mounts.yaml"
            )
            if item.get("mount_name") == mount_name and item.get("ok")
        ]
        if len(matches) != 1:
            raise MaterialClassificationProfileError(
                f"loader_ref 指向未登记或不可用的外部挂载：{mount_name}"
            )
        base = Path(matches[0]["repo_path"]).resolve()
        resolved = (base / mount_relative).resolve()
        if not resolved.is_relative_to(base):
            raise MaterialClassificationProfileError(f"loader_ref 越出允许根：{loader_ref}")
        return resolved
    base = omnicompany_root.parent if prefix == "workspace" else omnicompany_root
    resolved = (base / relative).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise MaterialClassificationProfileError(f"loader_ref 越出允许根：{loader_ref}")
    return resolved


def _resolve_tree_ref(reference: FacetTreeReference, omnicompany_root: Path) -> Path:
    """Resolve a profile tree_ref without allowing it to escape the workspace."""
    if reference.kind != "workspace-file":
        raise MaterialClassificationProfileError(
            f"tree_ref.kind 只允许 workspace-file：{reference.kind}"
        )
    ref = reference.ref.replace("\\", "/")
    candidate_ref = Path(ref)
    if candidate_ref.is_absolute() or candidate_ref.drive:
        raise MaterialClassificationProfileError(f"tree_ref 不允许绝对路径：{reference.ref}")
    base = omnicompany_root.parent.resolve()
    resolved = (base / ref).resolve()
    if not resolved.is_relative_to(base):
        raise MaterialClassificationProfileError(f"tree_ref 越出允许根：{reference.ref}")
    if not resolved.is_file():
        raise MaterialClassificationProfileError(f"tree_ref 文件不存在或不是文件：{reference.ref}")
    return resolved


def _load_tree_ref(reference: FacetTreeReference, omnicompany_root: Path) -> FacetTreeReference:
    path = _resolve_tree_ref(reference, omnicompany_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MaterialClassificationProfileError(f"tree_ref 不是有效 JSON：{path}") from exc
    if not isinstance(raw, Mapping) or not isinstance(raw.get("domains"), list):
        raise MaterialClassificationProfileError("tree_ref JSON 必须含 domains 数组")

    paths: dict[tuple[str, ...], str] = {}
    for domain_index, domain in enumerate(raw["domains"]):
        if not isinstance(domain, Mapping) or not isinstance(domain.get("buckets"), list):
            raise MaterialClassificationProfileError(
                f"tree_ref domains[{domain_index}] 必须含 buckets 数组"
            )
        for bucket_index, bucket in enumerate(domain["buckets"]):
            prefix = f"tree_ref domains[{domain_index}].buckets[{bucket_index}]"
            if not isinstance(bucket, Mapping):
                raise MaterialClassificationProfileError(f"{prefix} 必须是对象")
            bucket_id = bucket.get("id")
            if not isinstance(bucket_id, str) or not bucket_id.strip():
                raise MaterialClassificationProfileError(f"{prefix}.id 必须是非空字符串")
            bucket_id = bucket_id.strip()
            parts = tuple(bucket_id.split("/"))
            if any(not part or part in {".", ".."} or any(char.isspace() for char in part) for part in parts):
                raise MaterialClassificationProfileError(f"{prefix}.id 不是合法引用路径：{bucket_id}")
            if not isinstance(bucket.get("title"), str) or not bucket["title"].strip():
                raise MaterialClassificationProfileError(f"{prefix}.title 必须是非空字符串")
            if not isinstance(bucket.get("summary"), str) or not bucket["summary"].strip():
                raise MaterialClassificationProfileError(f"{prefix}.summary 必须是非空字符串")
            groups = bucket.get("groups")
            if not isinstance(groups, list):
                raise MaterialClassificationProfileError(f"{prefix}.groups 必须是数组")
            for group_index, group in enumerate(groups):
                group_prefix = f"{prefix}.groups[{group_index}]"
                if not isinstance(group, Mapping) or not isinstance(group.get("members"), list):
                    raise MaterialClassificationProfileError(f"{group_prefix} 必须含 members 数组")
                if any(not isinstance(member, str) or not member.strip() for member in group["members"]):
                    raise MaterialClassificationProfileError(f"{group_prefix}.members 必须都是非空字符串")
            if parts in paths:
                raise MaterialClassificationProfileError(
                    f"tree_ref 存在歧义 bucket id：{bucket_id}"
                )
            paths[parts] = bucket_id
    if not paths:
        raise MaterialClassificationProfileError("tree_ref 至少需要一个可选择 bucket")
    return reference.with_paths(paths)


class MaterialProfileRegistry:
    """Pointer-only registry. Full trees are returned only for one explicit owner."""

    def __init__(self, manifest_path: str | Path, *, omnicompany_root: str | Path) -> None:
        self.manifest_path = Path(manifest_path).resolve()
        self.omnicompany_root = Path(omnicompany_root).resolve()
        raw = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping) or raw.get("schema") != "omni.material-classification-profile-registry/v1":
            raise MaterialClassificationProfileError(
                "profile registry schema 必须是 omni.material-classification-profile-registry/v1"
            )
        items = raw.get("profiles") or ()
        self._registrations = tuple(MaterialProfileRegistration.from_mapping(item) for item in items)
        ids = [item.profile_id for item in self._registrations]
        projects = [item.project_id for item in self._registrations]
        if len(ids) != len(set(ids)) or len(projects) != len(set(projects)):
            raise MaterialClassificationProfileError("profile registry 的 profile_id/project_id 必须唯一")

    def list(self) -> tuple[MaterialProfileRegistration, ...]:
        return self._registrations

    def registration(self, owner: str) -> MaterialProfileRegistration:
        found = next(
            (item for item in self._registrations if item.project_id == owner or item.profile_id == owner),
            None,
        )
        if found is None:
            raise MaterialClassificationProfileError(f"未登记 Material classification profile：{owner}")
        return found

    def load(self, owner: str) -> MaterialClassificationProfile:
        registration = self.registration(owner)
        path = _resolve_loader(registration.loader_ref, self.omnicompany_root)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise MaterialClassificationProfileError(f"profile 必须是对象：{path}")
        profile = MaterialClassificationProfile.from_mapping(
            raw,
            reference_loader=lambda reference: _load_tree_ref(reference, self.omnicompany_root),
        )
        if (
            profile.profile_id != registration.profile_id
            or profile.project_id != registration.project_id
            or profile.revision != registration.revision
        ):
            raise MaterialClassificationProfileError("profile 本体与指针登记的 id/project/revision 不一致")
        return profile

    def context_ref(self, owner: str, *, read_via: str = "omni material profile show") -> dict[str, str]:
        """Return a copy-free receipt for a successful official profile read."""
        registration = self.registration(owner)
        # Load before issuing the receipt so an absent, malformed or stale
        # owner profile can never produce a context pointer.
        self.load(owner)
        if read_via not in {"omni material profile show", "omni material profile classify"}:
            raise MaterialClassificationProfileError(
                "read_via 只能是 omni material profile show 或 omni material profile classify"
            )
        return {
            "schema": "omni.material-classification-context-ref/v1",
            "profile_id": registration.profile_id,
            "profile_revision": registration.revision,
            "project_id": registration.project_id,
            "profile_ref": registration.published_ref,
            "read_via": read_via,
        }

    def agent_context(self, owner: str) -> dict[str, Any]:
        registration = self.registration(owner)
        profile = self.load(owner)
        return {
            "schema": "omni.material-classification-agent-context/v1",
            "authority": {
                "profile_id": registration.profile_id,
                "profile_revision": registration.revision,
                "profile_ref": registration.published_ref,
                "scope": registration.project_id,
                "disclosure": "explicit-owner-only",
            },
            "classification_context_ref": self.context_ref(owner),
            "profile": profile.to_mapping(),
            "decision_order": [
                "先沿 primary_tree 选择且只选择一个 selectable 叶子",
                "再按 facet_trees 选择适用的次要标签；不得自造自由文本分类",
                "文件、网页、图片、表格属于 representation，不是主分类或 facet",
                "提交前用 omni material profile classify 生成并校验 classification_ref",
            ],
        }
