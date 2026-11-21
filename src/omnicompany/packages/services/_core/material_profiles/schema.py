# [OMNI] origin=codex domain=services/_core/material_profiles ts=2026-08-19T00:00:00+08:00 type=router status=active
# [OMNI] summary="域自持 Material 主分类树、次要标签树与不泄漏整树的分类引用契约"
# [OMNI] material_id="material:core.material_profiles.schema.py"
"""Contracts for owner-scoped Material classification profiles.

The platform only indexes profile pointers.  A complete tree is loaded only
when a caller explicitly asks for its owning project/profile.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence


class MaterialClassificationProfileError(ValueError):
    """A profile, path, or classification reference violates its contract."""


def _token(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or any(char.isspace() for char in text) or "/" in text:
        raise MaterialClassificationProfileError(f"{field_name} 必须是非空且不含空白或 / 的稳定 token")
    return text


def _text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MaterialClassificationProfileError(f"{field_name} 不能为空")
    return text


def _reference_token(value: Any, field_name: str) -> str:
    """Validate an identifier from a referenced taxonomy without redefining it."""
    text = str(value or "").strip()
    if not text or any(char.isspace() for char in text):
        raise MaterialClassificationProfileError(f"{field_name} 必须是非空且不含空白的引用 token")
    return text


@dataclass(frozen=True, slots=True)
class TaxonomyNode:
    id: str
    label: str
    selectable: bool = False
    definition: str | None = None
    is_not: str | None = None
    examples: tuple[str, ...] = ()
    counterexamples: tuple[str, ...] = ()
    children: tuple["TaxonomyNode", ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _token(self.id, "taxonomy.id"))
        object.__setattr__(self, "label", _text(self.label, "taxonomy.label"))
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "examples", tuple(str(item).strip() for item in self.examples if str(item).strip()))
        object.__setattr__(self, "counterexamples", tuple(str(item).strip() for item in self.counterexamples if str(item).strip()))
        if self.selectable and self.children:
            raise MaterialClassificationProfileError(f"可选择节点 {self.id} 不能同时拥有 children")
        if self.selectable and (not self.definition or not self.is_not or not self.examples or not self.counterexamples):
            raise MaterialClassificationProfileError(
                f"可选择节点 {self.id} 必须完整定义 definition/is_not/examples/counterexamples"
            )
        child_ids = [child.id for child in self.children]
        if len(child_ids) != len(set(child_ids)):
            raise MaterialClassificationProfileError(f"{self.id} 下存在重复子节点")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "TaxonomyNode":
        children = raw.get("children") or ()
        if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
            raise MaterialClassificationProfileError("taxonomy.children 必须是数组")
        return cls(
            id=raw.get("id", ""),
            label=raw.get("label", ""),
            selectable=bool(raw.get("selectable", False)),
            definition=str(raw["definition"]).strip() if raw.get("definition") else None,
            is_not=str(raw["is_not"]).strip() if raw.get("is_not") else None,
            examples=tuple(raw.get("examples") or ()),
            counterexamples=tuple(raw.get("counterexamples") or ()),
            children=tuple(cls.from_mapping(item) for item in children),
        )

    def walk(self, prefix: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], "TaxonomyNode"]]:
        path = (*prefix, self.id)
        yield path, self
        for child in self.children:
            yield from child.walk(path)

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "label": self.label, "selectable": self.selectable}
        if self.definition:
            result.update({
                "definition": self.definition,
                "is_not": self.is_not,
                "examples": list(self.examples),
                "counterexamples": list(self.counterexamples),
            })
        if self.children:
            result["children"] = [child.to_mapping() for child in self.children]
        return result


@dataclass(frozen=True, slots=True)
class FacetTreeReference:
    """A validated index into an owner-provided taxonomy source.

    ``paths`` is only an in-memory lookup index.  It is deliberately omitted
    from serialization so the referenced taxonomy remains the sole source of
    its labels and membership.
    """

    kind: str
    ref: str
    paths: Mapping[tuple[str, ...], str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _token(self.kind, "tree_ref.kind"))
        object.__setattr__(self, "ref", _text(self.ref, "tree_ref.ref"))
        normalized: dict[tuple[str, ...], str] = {}
        for path, source_id in self.paths.items():
            normalized_path = tuple(_reference_token(item, "tree_ref.path") for item in path)
            if not normalized_path:
                raise MaterialClassificationProfileError("tree_ref 不得含空路径")
            if normalized_path in normalized:
                raise MaterialClassificationProfileError(
                    f"tree_ref 存在歧义路径：{'/'.join(normalized_path)}"
                )
            normalized[normalized_path] = _reference_token(source_id, "tree_ref.id")
        object.__setattr__(self, "paths", normalized)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FacetTreeReference":
        if not isinstance(raw, Mapping):
            raise MaterialClassificationProfileError("tree_ref 必须是对象")
        return cls(kind=raw.get("kind", ""), ref=raw.get("ref", ""))

    def with_paths(self, paths: Mapping[tuple[str, ...], str]) -> "FacetTreeReference":
        return type(self)(kind=self.kind, ref=self.ref, paths=paths)

    def to_mapping(self) -> dict[str, str]:
        return {"kind": self.kind, "ref": self.ref}

    def normalize_path(self, value: Sequence[str], field_name: str) -> tuple[str, ...]:
        parts = tuple(_reference_token(item, field_name) for item in value)
        if not parts:
            raise MaterialClassificationProfileError(f"{field_name} 不能为空")
        if any(item in {".", ".."} for item in parts):
            raise MaterialClassificationProfileError(f"{field_name} 不得含 . 或 ..")
        # CLI paths arrive split on '/', while callers may provide the
        # canonical external id as one value.  Both forms resolve to the same
        # source path and the canonical result is always split.
        canonical = tuple(part for item in parts for part in item.split("/"))
        if any(not item for item in canonical):
            raise MaterialClassificationProfileError(f"{field_name} 不能含空路径段")
        if canonical not in self.paths:
            raise MaterialClassificationProfileError(
                f"{field_name} 路径不可选择：{'/'.join(canonical)}"
            )
        return canonical


def _tree_paths(roots: tuple[TaxonomyNode, ...], label: str) -> dict[tuple[str, ...], TaxonomyNode]:
    paths = {path: node for root in roots for path, node in root.walk()}
    ids = [node.id for node in paths.values()]
    if len(ids) != len(set(ids)):
        raise MaterialClassificationProfileError(f"{label} 全树节点 id 必须唯一")
    return paths


@dataclass(frozen=True, slots=True)
class FacetTree:
    id: str
    label: str
    cardinality: str
    roots: tuple[TaxonomyNode, ...]
    applies_to: tuple[str, ...] = ()
    required: bool = False
    tree_ref: FacetTreeReference | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _token(self.id, "facet.id"))
        object.__setattr__(self, "label", _text(self.label, "facet.label"))
        if self.cardinality not in {"one", "many"}:
            raise MaterialClassificationProfileError(f"facet {self.id} cardinality 只能是 one/many")
        object.__setattr__(self, "roots", tuple(self.roots))
        object.__setattr__(self, "applies_to", tuple(_token(item, f"facet {self.id}.applies_to") for item in self.applies_to))
        if self.roots and self.tree_ref is not None:
            raise MaterialClassificationProfileError(f"facet {self.id} 不能同时含 tree 和 tree_ref")
        if not self.roots and self.tree_ref is None:
            raise MaterialClassificationProfileError(f"facet {self.id} 必须含 tree 或 tree_ref")
        if self.roots:
            _tree_paths(self.roots, f"facet {self.id}")
        elif not self.tree_ref.paths:
            raise MaterialClassificationProfileError(f"facet {self.id} 的 tree_ref 未加载可选择项")

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        reference_loader: Callable[[FacetTreeReference], FacetTreeReference] | None = None,
    ) -> "FacetTree":
        raw_tree = raw.get("tree")
        raw_ref = raw.get("tree_ref")
        if raw_tree is not None and raw_ref is not None:
            raise MaterialClassificationProfileError("facet 不能同时含 tree 和 tree_ref")
        tree_ref = None
        if raw_ref is not None:
            tree_ref = FacetTreeReference.from_mapping(raw_ref)
            if reference_loader is None:
                raise MaterialClassificationProfileError("tree_ref 必须通过 profile loader 解析")
            tree_ref = reference_loader(tree_ref)
        roots = raw_tree or ()
        if tree_ref is not None:
            roots = ()
        return cls(
            id=raw.get("id", ""),
            label=raw.get("label", ""),
            cardinality=str(raw.get("cardinality", "one")),
            required=bool(raw.get("required", False)),
            applies_to=tuple(raw.get("applies_to") or ()),
            roots=tuple(TaxonomyNode.from_mapping(item) for item in roots),
            tree_ref=tree_ref,
        )

    def to_mapping(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "label": self.label,
            "cardinality": self.cardinality,
            "required": self.required,
            "applies_to": list(self.applies_to),
        }
        if self.tree_ref is not None:
            result["tree_ref"] = self.tree_ref.to_mapping()
        else:
            result["tree"] = [root.to_mapping() for root in self.roots]
        return result


@dataclass(frozen=True, slots=True)
class MaterialClassificationRef:
    profile_id: str
    profile_revision: str
    primary_path: tuple[str, ...]
    facets: Mapping[str, tuple[tuple[str, ...], ...]] = field(default_factory=dict)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": "omni.material-classification-ref/v1",
            "profile_id": self.profile_id,
            "profile_revision": self.profile_revision,
            "primary_path": list(self.primary_path),
            "facets": {key: [list(path) for path in paths] for key, paths in self.facets.items()},
        }


@dataclass(frozen=True, slots=True)
class MaterialClassificationProfile:
    profile_id: str
    project_id: str
    revision: str
    primary_tree: tuple[TaxonomyNode, ...]
    facet_trees: tuple[FacetTree, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _token(self.profile_id, "profile_id"))
        object.__setattr__(self, "project_id", _token(self.project_id, "project_id"))
        object.__setattr__(self, "revision", _token(self.revision, "revision"))
        object.__setattr__(self, "primary_tree", tuple(self.primary_tree))
        object.__setattr__(self, "facet_trees", tuple(self.facet_trees))
        if not self.primary_tree:
            raise MaterialClassificationProfileError("primary_tree 不能为空")
        primary = _tree_paths(self.primary_tree, "primary_tree")
        if not any(node.selectable for node in primary.values()):
            raise MaterialClassificationProfileError("primary_tree 至少需要一个可选择叶子")
        facet_ids = [facet.id for facet in self.facet_trees]
        if len(facet_ids) != len(set(facet_ids)):
            raise MaterialClassificationProfileError("facet_trees 存在重复 id")
        leaf_ids = {node.id for node in primary.values() if node.selectable}
        for facet in self.facet_trees:
            unknown = set(facet.applies_to) - leaf_ids
            if unknown:
                raise MaterialClassificationProfileError(f"facet {facet.id} applies_to 含未知主类型：{sorted(unknown)}")

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        reference_loader: Callable[[FacetTreeReference], FacetTreeReference] | None = None,
    ) -> "MaterialClassificationProfile":
        if raw.get("schema") != "omni.material-classification-profile/v1":
            raise MaterialClassificationProfileError("profile schema 必须是 omni.material-classification-profile/v1")
        return cls(
            profile_id=raw.get("profile_id", ""),
            project_id=raw.get("project_id", ""),
            revision=raw.get("revision", ""),
            primary_tree=tuple(TaxonomyNode.from_mapping(item) for item in raw.get("primary_tree") or ()),
            facet_trees=tuple(
                FacetTree.from_mapping(item, reference_loader=reference_loader)
                for item in raw.get("facet_trees") or ()
            ),
        )

    def classify(
        self,
        primary_path: Sequence[str],
        facets: Mapping[str, Sequence[Sequence[str]]] | None = None,
    ) -> MaterialClassificationRef:
        primary = tuple(_token(item, "primary_path") for item in primary_path)
        primary_node = _tree_paths(self.primary_tree, "primary_tree").get(primary)
        if primary_node is None or not primary_node.selectable:
            raise MaterialClassificationProfileError(f"primary_path 不是可选择叶子：{'/'.join(primary)}")
        primary_leaf = primary_node.id
        requested = facets or {}
        facet_by_id = {facet.id: facet for facet in self.facet_trees}
        unknown = set(requested) - set(facet_by_id)
        if unknown:
            raise MaterialClassificationProfileError(f"未知 facet：{sorted(unknown)}")
        normalized: dict[str, tuple[tuple[str, ...], ...]] = {}
        for facet_id, paths in requested.items():
            facet = facet_by_id[facet_id]
            if facet.applies_to and primary_leaf not in facet.applies_to:
                raise MaterialClassificationProfileError(f"facet {facet_id} 不适用于 {primary_leaf}")
            values: tuple[tuple[str, ...], ...]
            if facet.tree_ref is not None:
                values = tuple(
                    facet.tree_ref.normalize_path(path, f"facet {facet_id}") for path in paths
                )
            else:
                values = tuple(tuple(_token(item, f"facet {facet_id}") for item in path) for path in paths)
            if facet.cardinality == "one" and len(values) != 1:
                raise MaterialClassificationProfileError(f"facet {facet_id} 只能选择一个值")
            if facet.tree_ref is None:
                available = _tree_paths(facet.roots, f"facet {facet_id}")
                for value in values:
                    node = available.get(value)
                    if node is None or not node.selectable:
                        raise MaterialClassificationProfileError(f"facet {facet_id} 路径不可选择：{'/'.join(value)}")
            normalized[facet_id] = values
        for facet in self.facet_trees:
            if facet.required and (not facet.applies_to or primary_leaf in facet.applies_to) and facet.id not in normalized:
                raise MaterialClassificationProfileError(f"缺少必填 facet：{facet.id}")
        return MaterialClassificationRef(self.profile_id, self.revision, primary, normalized)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": "omni.material-classification-profile/v1",
            "profile_id": self.profile_id,
            "project_id": self.project_id,
            "revision": self.revision,
            "primary_tree": [root.to_mapping() for root in self.primary_tree],
            "facet_trees": [facet.to_mapping() for facet in self.facet_trees],
        }


@dataclass(frozen=True, slots=True)
class MaterialProfileRegistration:
    profile_id: str
    project_id: str
    revision: str
    loader_ref: str
    # 对外发布的身份串（写进每份 Material 的 classification_context_ref.profile_ref）。
    # 默认等于 loader_ref；文件搬家后可只改 loader_ref、保留 profile_ref，接入方按身份串校验的既有材料不受影响。
    profile_ref: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MaterialProfileRegistration":
        raw_profile_ref = raw.get("profile_ref")
        return cls(
            profile_id=_token(raw.get("profile_id"), "registration.profile_id"),
            project_id=_token(raw.get("project_id"), "registration.project_id"),
            revision=_token(raw.get("revision"), "registration.revision"),
            loader_ref=_text(raw.get("loader_ref"), "registration.loader_ref"),
            profile_ref=_text(raw_profile_ref, "registration.profile_ref") if raw_profile_ref is not None else None,
        )

    @property
    def published_ref(self) -> str:
        return self.profile_ref or self.loader_ref

    def to_mapping(self) -> dict[str, str]:
        return {
            "profile_id": self.profile_id,
            "project_id": self.project_id,
            "revision": self.revision,
            "loader_ref": self.loader_ref,
            **({"profile_ref": self.profile_ref} if self.profile_ref else {}),
        }
