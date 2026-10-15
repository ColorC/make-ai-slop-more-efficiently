# [OMNI] origin=codex domain=services/_core/material_wiki ts=2026-08-02T00:00:00+08:00 type=format status=active
# [OMNI] summary="材料 Wiki P1 通用合同：项目 profile、分类树、版本状态、来源关系与只指向本体的 entry 元数据"
# [OMNI] why="平台需要统一可追溯合同，但项目分类必须留在项目自己的 Wiki/domain-tree，不能把 Vilo 四层十一类复制成全局常量"
# [OMNI] tags=material-wiki,schema,profile,version,provenance
# [OMNI] material_id="material:core.material_wiki.schema.py"
"""Material Wiki 的最小通用合同。

本模块不保存正文，也不定义任何项目的分类名称。项目正文由 ``canonical_ref``
指向项目本体；分类由 ``MaterialWikiProfile`` 从项目权威源加载。ReviewStage、
registry、semantic_fs 和报告只能消费这些字段，不在这里复制正文。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any, Iterable, Mapping, Sequence


_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_MATERIAL_ID_RE = re.compile(r"^[^\W_][\w.:-]*$", re.UNICODE)
_PLACEMENT_SEGMENT_RE = re.compile(r"^[^\W_][\w-]*$", re.UNICODE)


class MaterialWikiContractError(ValueError):
    """材料 Wiki 合同或项目 profile 不成立。"""


def _token(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or not _TOKEN_RE.fullmatch(text):
        raise MaterialWikiContractError(
            f"{field_name} 必须是稳定 token（字母/数字/._:-），实际为 {value!r}"
        )
    return text


def _material_identifier(value: Any, field_name: str = "material_id") -> str:
    """Material IDs are human-facing and may be Chinese; other contract tokens stay ASCII."""
    text = str(value or "").strip()
    if not text or not _MATERIAL_ID_RE.fullmatch(text):
        raise MaterialWikiContractError(
            f"{field_name} 只能包含中英文、数字、点、下划线、冒号或短横线，且不能以下划线开头"
        )
    if text in {".", ".."}:
        raise MaterialWikiContractError(f"{field_name} 不是安全的稳定标识")
    return text


def _placement_segment(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not _PLACEMENT_SEGMENT_RE.fullmatch(text):
        raise MaterialWikiContractError(
            f"placement.path 每一级只能包含中英文、数字、下划线或短横线，实际为 {value!r}"
        )
    return text


def _text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MaterialWikiContractError(f"{field_name} 不能为空")
    return text


class TaxonomySourceKind(str, Enum):
    WIKI_FRONTMATTER = "wiki_frontmatter"
    DOMAIN_TREE = "domain_tree"
    TRACK = "track"


@dataclass(frozen=True, slots=True)
class ProfileSource:
    kind: TaxonomySourceKind
    ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", TaxonomySourceKind(self.kind))
        object.__setattr__(self, "ref", _text(self.ref, "profile.source.ref"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProfileSource":
        return cls(kind=TaxonomySourceKind(raw.get("kind")), ref=raw.get("ref", ""))


@dataclass(frozen=True, slots=True)
class MaterialStatusDefinition:
    id: str
    label: str
    current: bool
    approved: bool = False
    terminal: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _token(self.id, "status.id"))
        object.__setattr__(self, "label", _text(self.label, "status.label"))
        if self.approved and not self.current:
            raise MaterialWikiContractError("approved 状态必须同时是 current")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MaterialStatusDefinition":
        return cls(
            id=raw.get("id", ""),
            label=raw.get("label", ""),
            current=bool(raw.get("current", False)),
            approved=bool(raw.get("approved", False)),
            terminal=bool(raw.get("terminal", False)),
        )


@dataclass(frozen=True, slots=True)
class MaterialTaxonomyNode:
    id: str
    label: str
    children: tuple["MaterialTaxonomyNode", ...] = ()
    allow_dynamic_children: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _token(self.id, "taxonomy.id"))
        object.__setattr__(self, "label", _text(self.label, "taxonomy.label"))
        object.__setattr__(self, "children", tuple(self.children))
        child_ids = [child.id for child in self.children]
        if len(child_ids) != len(set(child_ids)):
            raise MaterialWikiContractError(f"{self.id} 下存在重复子类型 id")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MaterialTaxonomyNode":
        children_raw = raw.get("children") or ()
        if not isinstance(children_raw, Sequence) or isinstance(children_raw, (str, bytes)):
            raise MaterialWikiContractError("taxonomy.children 必须是数组")
        return cls(
            id=raw.get("id", ""),
            label=raw.get("label", ""),
            children=tuple(cls.from_mapping(item) for item in children_raw),
            allow_dynamic_children=bool(raw.get("allow_dynamic_children", False)),
        )

    def walk(self, *, depth: int = 1) -> Iterable[tuple[int, "MaterialTaxonomyNode"]]:
        yield depth, self
        for child in self.children:
            yield from child.walk(depth=depth + 1)


@dataclass(frozen=True, slots=True)
class ProfileRegistration:
    """平台 profile 名录只保存指针，不复制项目 taxonomy。"""

    profile_id: str
    project_id: str
    source: ProfileSource
    version_policy: str
    loader_ref: str | None = None
    overall_plan_ref: str | None = None
    canonical: "CanonicalBackendRegistration | None" = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _token(self.profile_id, "profile_id"))
        object.__setattr__(self, "project_id", _token(self.project_id, "project_id"))
        object.__setattr__(self, "version_policy", _token(self.version_policy, "version_policy"))
        if self.loader_ref is not None:
            object.__setattr__(self, "loader_ref", _text(self.loader_ref, "loader_ref"))
        if self.overall_plan_ref is not None:
            object.__setattr__(
                self,
                "overall_plan_ref",
                _text(self.overall_plan_ref, "overall_plan_ref"),
            )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProfileRegistration":
        return cls(
            profile_id=raw.get("profile_id", ""),
            project_id=raw.get("project_id", ""),
            source=ProfileSource.from_mapping(raw.get("source") or {}),
            version_policy=raw.get("version_policy", ""),
            loader_ref=raw.get("loader_ref"),
            overall_plan_ref=raw.get("overall_plan_ref"),
            canonical=(
                CanonicalBackendRegistration.from_mapping(raw["canonical"])
                if raw.get("canonical")
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class CanonicalBackendRegistration:
    kind: str
    root_ref: str
    canonical_prefix: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _token(self.kind, "canonical.kind"))
        object.__setattr__(self, "root_ref", _text(self.root_ref, "canonical.root_ref"))
        object.__setattr__(
            self,
            "canonical_prefix",
            _text(self.canonical_prefix, "canonical.canonical_prefix"),
        )
        if self.kind != "markdown_wiki":
            raise MaterialWikiContractError(f"暂不支持 canonical.kind={self.kind}")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "CanonicalBackendRegistration":
        return cls(
            kind=raw.get("kind", ""),
            root_ref=raw.get("root_ref", ""),
            canonical_prefix=raw.get("canonical_prefix", ""),
        )


@dataclass(frozen=True, slots=True)
class MaterialWikiProfile:
    profile_id: str
    project_id: str
    schema_version: int
    source: ProfileSource
    taxonomy: tuple[MaterialTaxonomyNode, ...]
    statuses: tuple[MaterialStatusDefinition, ...]
    default_status: str
    locked_depth: int = 0
    dynamic_from_depth: int | None = None
    max_depth: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _token(self.profile_id, "profile_id"))
        object.__setattr__(self, "project_id", _token(self.project_id, "project_id"))
        if self.schema_version < 1:
            raise MaterialWikiContractError("schema_version 必须 >= 1")
        object.__setattr__(self, "taxonomy", tuple(self.taxonomy))
        object.__setattr__(self, "statuses", tuple(self.statuses))
        if not self.taxonomy:
            raise MaterialWikiContractError("已解析 profile 必须含 taxonomy")
        if not self.statuses:
            raise MaterialWikiContractError("profile 必须含 statuses")
        ids = [node.id for root in self.taxonomy for _, node in root.walk()]
        if len(ids) != len(set(ids)):
            raise MaterialWikiContractError("taxonomy 全树存在重复 id")
        status_ids = [status.id for status in self.statuses]
        if len(status_ids) != len(set(status_ids)):
            raise MaterialWikiContractError("profile 存在重复 status id")
        object.__setattr__(self, "default_status", _token(self.default_status, "default_status"))
        if self.default_status not in status_ids:
            raise MaterialWikiContractError("default_status 不在 statuses 中")
        if self.locked_depth < 0:
            raise MaterialWikiContractError("locked_depth 不能为负数")
        if self.dynamic_from_depth is not None and self.dynamic_from_depth <= self.locked_depth:
            raise MaterialWikiContractError("dynamic_from_depth 必须大于 locked_depth")
        if self.max_depth is not None and self.max_depth < self.locked_depth:
            raise MaterialWikiContractError("max_depth 不能小于 locked_depth")

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
        *,
        source: ProfileSource,
    ) -> "MaterialWikiProfile":
        taxonomy_raw = raw.get("taxonomy") or ()
        statuses_raw = raw.get("statuses") or ()
        return cls(
            profile_id=raw.get("profile_id", ""),
            project_id=raw.get("project_id", ""),
            schema_version=int(raw.get("schema_version", 0)),
            source=source,
            taxonomy=tuple(MaterialTaxonomyNode.from_mapping(item) for item in taxonomy_raw),
            statuses=tuple(MaterialStatusDefinition.from_mapping(item) for item in statuses_raw),
            default_status=raw.get("default_status", ""),
            locked_depth=int(raw.get("locked_depth", 0)),
            dynamic_from_depth=(
                int(raw["dynamic_from_depth"])
                if raw.get("dynamic_from_depth") is not None
                else None
            ),
            max_depth=int(raw["max_depth"]) if raw.get("max_depth") is not None else None,
        )

    @property
    def status_ids(self) -> frozenset[str]:
        return frozenset(status.id for status in self.statuses)

    def validate_path(self, path: Sequence[str]) -> tuple[str, ...]:
        normalized = tuple(_placement_segment(item) for item in path)
        if not normalized:
            raise MaterialWikiContractError("placement.path 不能为空")
        if self.max_depth is not None and len(normalized) > self.max_depth:
            raise MaterialWikiContractError(
                f"placement.path 深度 {len(normalized)} 超过 profile 上限 {self.max_depth}"
            )

        candidates = self.taxonomy
        parent: MaterialTaxonomyNode | None = None
        dynamic_branch = False
        for depth, segment in enumerate(normalized, start=1):
            matched = next((node for node in candidates if node.id == segment), None)
            if matched is not None:
                parent = matched
                candidates = matched.children
                continue
            dynamic_allowed = (
                dynamic_branch
                or (
                    self.dynamic_from_depth is not None
                    and depth >= self.dynamic_from_depth
                    and parent is not None
                    and parent.allow_dynamic_children
                )
            )
            if not dynamic_allowed:
                raise MaterialWikiContractError(
                    f"{self.profile_id} 不允许分类路径 {'/'.join(normalized)}（第 {depth} 级 {segment} 未注册）"
                )
            dynamic_branch = True
            parent = None
            candidates = ()
        if len(normalized) < self.locked_depth:
            raise MaterialWikiContractError(
                f"placement.path 至少需要 {self.locked_depth} 级，实际 {len(normalized)} 级"
            )
        return normalized


@dataclass(frozen=True, slots=True)
class MaterialSourceRef:
    kind: str
    ref: str
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _token(self.kind, "source.kind"))
        object.__setattr__(self, "ref", _text(self.ref, "source.ref"))
        if self.note is not None:
            object.__setattr__(self, "note", _text(self.note, "source.note"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MaterialSourceRef":
        return cls(kind=raw.get("kind", ""), ref=raw.get("ref", ""), note=raw.get("note"))


@dataclass(frozen=True, slots=True)
class MaterialVersionRef:
    material_id: str
    version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "material_id", _material_identifier(self.material_id))
        object.__setattr__(self, "version", _text(self.version, "version"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MaterialVersionRef":
        return cls(material_id=raw.get("material_id", ""), version=raw.get("version", ""))


@dataclass(frozen=True, slots=True)
class MaterialRelations:
    parent_version: MaterialVersionRef | None = None
    supersedes: tuple[MaterialVersionRef, ...] = ()
    derived_from: tuple[MaterialVersionRef, ...] = ()
    related: tuple[MaterialVersionRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "supersedes", tuple(self.supersedes))
        object.__setattr__(self, "derived_from", tuple(self.derived_from))
        object.__setattr__(self, "related", tuple(self.related))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "MaterialRelations":
        raw = raw or {}
        parent_raw = raw.get("parent_version")
        return cls(
            parent_version=(MaterialVersionRef.from_mapping(parent_raw) if parent_raw else None),
            supersedes=tuple(MaterialVersionRef.from_mapping(item) for item in raw.get("supersedes") or ()),
            derived_from=tuple(MaterialVersionRef.from_mapping(item) for item in raw.get("derived_from") or ()),
            related=tuple(MaterialVersionRef.from_mapping(item) for item in raw.get("related") or ()),
        )


@dataclass(frozen=True, slots=True)
class MaterialWikiEntry:
    material_id: str
    project_id: str
    profile_id: str
    title: str
    canonical_ref: str
    placement_path: tuple[str, ...]
    version: str
    status: str
    sources: tuple[MaterialSourceRef, ...]
    relations: MaterialRelations = field(default_factory=MaterialRelations)
    author_session: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "material_id", _material_identifier(self.material_id))
        object.__setattr__(self, "project_id", _token(self.project_id, "project_id"))
        object.__setattr__(self, "profile_id", _token(self.profile_id, "profile_id"))
        object.__setattr__(self, "title", _text(self.title, "title"))
        object.__setattr__(self, "canonical_ref", _text(self.canonical_ref, "canonical_ref"))
        object.__setattr__(
            self,
            "placement_path",
            tuple(_placement_segment(item) for item in self.placement_path),
        )
        if not self.placement_path:
            raise MaterialWikiContractError("placement_path 不能为空")
        object.__setattr__(self, "version", _text(self.version, "version"))
        object.__setattr__(self, "status", _token(self.status, "status"))
        object.__setattr__(self, "sources", tuple(self.sources))
        if not self.sources:
            raise MaterialWikiContractError("sources 不能为空：可复用材料必须能追溯来源")
        if self.author_session is not None:
            object.__setattr__(self, "author_session", _text(self.author_session, "author_session"))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MaterialWikiEntry":
        return cls(
            material_id=raw.get("material_id", ""),
            project_id=raw.get("project_id", ""),
            profile_id=raw.get("profile_id", ""),
            title=raw.get("title", ""),
            canonical_ref=raw.get("canonical_ref", ""),
            placement_path=tuple(raw.get("placement_path") or ()),
            version=raw.get("version", ""),
            status=raw.get("status", ""),
            sources=tuple(MaterialSourceRef.from_mapping(item) for item in raw.get("sources") or ()),
            relations=MaterialRelations.from_mapping(raw.get("relations")),
            author_session=raw.get("author_session"),
        )

    def validate_against(self, profile: MaterialWikiProfile) -> None:
        if self.project_id != profile.project_id:
            raise MaterialWikiContractError(
                f"entry.project_id={self.project_id} 与 profile.project_id={profile.project_id} 不一致"
            )
        if self.profile_id != profile.profile_id:
            raise MaterialWikiContractError(
                f"entry.profile_id={self.profile_id} 与 profile.profile_id={profile.profile_id} 不一致"
            )
        profile.validate_path(self.placement_path)
        if self.status not in profile.status_ids:
            raise MaterialWikiContractError(
                f"status={self.status} 不在 {profile.profile_id} 的合法状态中"
            )
