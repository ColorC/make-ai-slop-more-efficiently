"""Material Wiki adapters for formal-domain governance.

The Wiki remains the canonical body store.  These read-only adapters merely let
the generic formal-domain layer inspect an already recorded Wiki artifact.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..material_domains.governance import ArtifactInspection, GovernanceFinding
from .adapters.markdown import (
    MaterialEntryNotFoundError,
    MarkdownWikiAdapter,
    WikiDocument,
    parse_document,
)
from .runtime import MaterialWikiRuntime


_ARTIFACT_KINDS = frozenset({"material-wiki-document", "material-wiki-view"})
_REVISION_PROVIDER = "material-wiki"


class MaterialWikiFormalDomainError(ValueError):
    """A formal-domain reference cannot unambiguously resolve to a Wiki entry."""


def _revision_ref(artifact: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = artifact.get("revision_ref")
    return value if isinstance(value, Mapping) else None


class _MaterialWikiResolver:
    def __init__(self, runtime: MaterialWikiRuntime) -> None:
        self.runtime = runtime
        self._paths: dict[str, list[tuple[MarkdownWikiAdapter, Path]]] | None = None

    def _build_index(self) -> None:
        paths: dict[str, list[tuple[MarkdownWikiAdapter, Path]]] = {}
        for registration in self.runtime.registrations():
            adapter = self.runtime.adapter(registration.project_id)
            for path, document in adapter.iter_documents():
                paths.setdefault(document.material_id, []).append((adapter, path))
        self._paths = paths

    def resolve(self, material_id: str) -> tuple[MarkdownWikiAdapter, WikiDocument] | None:
        if self._paths is None or material_id not in self._paths:
            self._build_index()
        candidates = self._paths.get(material_id, []) if self._paths is not None else []
        matches: list[tuple[MarkdownWikiAdapter, WikiDocument]] = []
        for adapter, path in candidates:
            try:
                document = parse_document(path.read_text(encoding="utf-8"), path)
            except (OSError, ValueError):
                continue
            if document.material_id == material_id:
                matches.append((adapter, document))
        if len(matches) > 1:
            raise MaterialWikiFormalDomainError(
                f"material id {material_id!r} is registered in multiple Material Wiki profiles"
            )
        return matches[0] if matches else None


def _resolver_for(runtime: MaterialWikiRuntime) -> _MaterialWikiResolver:
    resolver = getattr(runtime, "_formal_domain_resolver", None)
    if not isinstance(resolver, _MaterialWikiResolver):
        resolver = _MaterialWikiResolver(runtime)
        setattr(runtime, "_formal_domain_resolver", resolver)
    return resolver


class MaterialWikiArtifactInspector:
    """Inspect formal artifacts against the native Wiki version, never a digest."""

    def __init__(self, runtime: MaterialWikiRuntime) -> None:
        self.runtime = runtime
        self._resolver = _resolver_for(runtime)

    def inspect(self, artifact: dict[str, Any]) -> ArtifactInspection:
        revision_ref = _revision_ref(artifact)
        if (
            artifact.get("kind") not in _ARTIFACT_KINDS
            or revision_ref is None
            or revision_ref.get("provider") != _REVISION_PROVIDER
        ):
            return ArtifactInspection(False)
        material_id = revision_ref.get("object")
        expected_version = revision_ref.get("revision")
        if not isinstance(material_id, str) or not material_id or not isinstance(expected_version, str) or not expected_version:
            return ArtifactInspection(False)
        resolved = self._resolver.resolve(material_id)
        if resolved is None:
            return ArtifactInspection(False)
        adapter, document = resolved
        canonical_ref = adapter.canonical_ref(document.material_id, document.placement_path)
        if artifact.get("ref") != canonical_ref:
            return ArtifactInspection(False)
        current_version = document.current_version
        return ArtifactInspection(
            exists=True,
            revision=current_version,
            structure_ok=True,
            indexable=bool(current_version and expected_version == current_version),
        )


class RequiredMarkdownConceptRule:
    """Require configured concept groups in the exact formal Wiki artifact version.

    Each configured group is an OR-set of terms; every group must have at least
    one matching term.  The mapping is intentionally product-neutral.
    """

    rule_id = "material-wiki.required-markdown-concepts"

    def __init__(
        self,
        runtime: MaterialWikiRuntime,
        required_concepts: Mapping[str, Sequence[Sequence[str]]] | None = None,
        *,
        rule_id: str | None = None,
    ) -> None:
        self._resolver = _resolver_for(runtime)
        self.required_concepts = {
            str(material_id): tuple(tuple(str(term) for term in group if str(term)) for group in groups)
            for material_id, groups in (required_concepts or {}).items()
        }
        if rule_id:
            self.rule_id = rule_id

    @staticmethod
    def _target(record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "material_id": record.get("id", "formalization"),
            "revision_ref": record.get("revision_ref", {}),
        }

    @staticmethod
    def _evidence(formal: Mapping[str, Any]) -> list[dict[str, Any]]:
        sources = formal.get("sources", [])
        return list(sources) if isinstance(sources, list) else []

    def _finding(self, record: Mapping[str, Any], formal: Mapping[str, Any], *, passed: bool, message: str) -> GovernanceFinding:
        return GovernanceFinding(
            self.rule_id,
            "passed" if passed else "failed",
            self._target(record),
            message,
            self._evidence(formal),
            not passed,
        )

    def evaluate(self, domain: dict[str, Any], record: dict[str, Any]) -> GovernanceFinding:
        formal = record.get("formalization", record)
        if not isinstance(formal, Mapping):
            return self._finding(record, {}, passed=False, message="formalization is required")
        input_material = formal.get("input_material")
        material_id = input_material.get("material_id") if isinstance(input_material, Mapping) else None
        if not isinstance(material_id, str) or not material_id:
            return self._finding(record, formal, passed=False, message="input material is required")
        groups = self.required_concepts.get(material_id)
        if groups is None:
            fd = domain.get("formal_domain", domain)
            extensions = fd.get("extensions", {}) if isinstance(fd, Mapping) else {}
            configured = (
                extensions.get("required_markdown_concepts", {})
                if isinstance(extensions, Mapping)
                else {}
            )
            raw_groups = configured.get(material_id) if isinstance(configured, Mapping) else None
            if isinstance(raw_groups, Sequence) and not isinstance(raw_groups, (str, bytes)):
                groups = tuple(
                    tuple(str(term) for term in group if str(term))
                    for group in raw_groups
                    if isinstance(group, Sequence) and not isinstance(group, (str, bytes))
                )
        if groups is None:
            return self._finding(record, formal, passed=True, message="no Markdown concepts are configured for this input material")
        artifacts = formal.get("artifacts")
        if not isinstance(artifacts, list):
            return self._finding(record, formal, passed=False, message="formal Material Wiki artifact is required")
        artifact = next(
            (
                item for item in artifacts
                if isinstance(item, Mapping)
                and item.get("kind") in _ARTIFACT_KINDS
                and (_revision_ref(item) or {}).get("provider") == _REVISION_PROVIDER
            ),
            None,
        )
        if artifact is None:
            return self._finding(record, formal, passed=False, message="formal Material Wiki artifact is required")
        revision_ref = _revision_ref(artifact)
        assert revision_ref is not None
        wiki_id, version = revision_ref.get("object"), revision_ref.get("revision")
        if not isinstance(wiki_id, str) or not wiki_id or not isinstance(version, str) or not version:
            return self._finding(record, formal, passed=False, message="formal Material Wiki artifact requires an exact version")
        try:
            resolved = self._resolver.resolve(wiki_id)
            if resolved is None:
                return self._finding(record, formal, passed=False, message="formal Material Wiki artifact is missing")
            _adapter, document = resolved
            body = document.version(version).body
        except MaterialEntryNotFoundError:
            return self._finding(record, formal, passed=False, message="formal Material Wiki artifact version is missing")
        missing = [group for group in groups if not group or not any(term in body for term in group)]
        if missing:
            return self._finding(record, formal, passed=False, message="required Markdown concept groups are missing")
        return self._finding(record, formal, passed=True, message="required Markdown concept groups are present")


class MaterialWikiIdentityInputRule:
    """Optionally require the input version to be the exact Wiki artifact version."""

    rule_id = "material-wiki.identity-input"

    @staticmethod
    def _finding(record: Mapping[str, Any], *, passed: bool, message: str) -> GovernanceFinding:
        formal = record.get("formalization", record)
        sources = formal.get("sources", []) if isinstance(formal, Mapping) else []
        return GovernanceFinding(
            MaterialWikiIdentityInputRule.rule_id,
            "passed" if passed else "failed",
            {
                "material_id": record.get("id", "formalization"),
                "revision_ref": record.get("revision_ref", {}),
            },
            message,
            list(sources) if isinstance(sources, list) else [],
            not passed,
        )

    def evaluate(self, domain: dict[str, Any], record: dict[str, Any]) -> GovernanceFinding:
        fd = domain.get("formal_domain", domain)
        extensions = fd.get("extensions", {}) if isinstance(fd, Mapping) else {}
        required = (
            extensions.get("require_material_wiki_identity_input") is True
            if isinstance(extensions, Mapping)
            else False
        )
        if not required:
            return self._finding(record, passed=True, message="identity input is not required by this domain")
        formal = record.get("formalization", record)
        if not isinstance(formal, Mapping):
            return self._finding(record, passed=False, message="formalization is required")
        input_material = formal.get("input_material")
        artifacts = formal.get("artifacts")
        if not isinstance(input_material, Mapping) or not isinstance(artifacts, list):
            return self._finding(record, passed=False, message="input material and Wiki artifact are required")
        wiki_artifacts = [
            item
            for item in artifacts
            if isinstance(item, Mapping)
            and item.get("kind") in _ARTIFACT_KINDS
            and (_revision_ref(item) or {}).get("provider") == _REVISION_PROVIDER
        ]
        input_revision = input_material.get("revision_ref")
        passed = len(wiki_artifacts) == 1 and isinstance(input_revision, Mapping)
        if passed:
            artifact_revision = _revision_ref(wiki_artifacts[0])
            passed = (
                artifact_revision is not None
                and input_material.get("material_id") == artifact_revision.get("object")
                and input_revision.get("provider") == _REVISION_PROVIDER
                and input_revision.get("object") == artifact_revision.get("object")
                and input_revision.get("revision") == artifact_revision.get("revision")
            )
        return self._finding(
            record,
            passed=bool(passed),
            message=(
                "input material exactly matches the formal Wiki artifact"
                if passed
                else "input material must exactly match the single formal Wiki artifact"
            ),
        )


__all__ = [
    "MaterialWikiArtifactInspector",
    "MaterialWikiFormalDomainError",
    "MaterialWikiIdentityInputRule",
    "RequiredMarkdownConceptRule",
]
