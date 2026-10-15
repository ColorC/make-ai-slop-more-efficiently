# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T08:20:00+08:00 type=static-content-compiler status=active
"""Restricted, deterministic compiler for tournament simulator content.

The compiler accepts JSON data conforming to :class:`ContentBundle`; it has no
plugin, expression, import, template, or script execution surface.  Visibility,
provenance, licence, and fidelity metadata are checked before an audience
package can be emitted.  Any ambiguity is rejected rather than guessed.
"""

from __future__ import annotations

import json
from omnicompany.core.pycompat import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .models import (
    ContentBundle,
    MechanismFidelity,
    VisibilityClass,
    canonical_sha256,
)

SOURCE_SCHEMA = "omnicompany.arknights.tournament-sim.static-source.v1"
PACKAGE_SCHEMA = "omnicompany.arknights.tournament-sim.static-package.v1"
MAX_SOURCE_BYTES = 8 * 1024 * 1024


class ContentCompileError(ValueError):
    """A source document cannot be safely compiled."""


class StaticAudience(StrEnum):
    FIRSTSEEN = "firstseen"
    DEEP = "deep"
    JUDGE = "judge"


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ProvenanceRecord(_StrictContract):
    source_id: str = Field(min_length=1)
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_ref: str = Field(min_length=1)


class LicenseRecord(_StrictContract):
    license_id: str = Field(min_length=1)
    permitted_audiences: tuple[StaticAudience, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def audiences_are_unique(self) -> "LicenseRecord":
        if len(set(self.permitted_audiences)) != len(self.permitted_audiences):
            raise ValueError("permitted_audiences must be unique")
        return self


class StaticSourceDocument(_StrictContract):
    schema_id: Literal[SOURCE_SCHEMA] = SOURCE_SCHEMA
    asset_id: str = Field(min_length=1)
    visibility: VisibilityClass
    provenance: ProvenanceRecord
    license: LicenseRecord
    fidelity: tuple[MechanismFidelity, ...] = Field(min_length=1)
    content: ContentBundle

    @model_validator(mode="after")
    def metadata_matches_payload(self) -> "StaticSourceDocument":
        if self.content.visibility is not self.visibility:
            raise ValueError("source visibility does not match content visibility")
        if self.content.source != self.provenance.source_id:
            raise ValueError("content source does not match provenance source_id")
        if self.content.content_hash != self.provenance.payload_sha256:
            raise ValueError("content hash does not match provenance payload_sha256")
        declared = {item.mechanism: item for item in self.fidelity}
        embedded = {item.mechanism: item for item in self.content.mechanism_fidelity}
        if len(declared) != len(self.fidelity):
            raise ValueError("source fidelity mechanisms must be unique")
        if declared != embedded:
            raise ValueError("source fidelity does not exactly match embedded fidelity")
        return self


class CompiledStaticAsset(_StrictContract):
    asset_id: str
    visibility: VisibilityClass
    provenance: ProvenanceRecord
    license: LicenseRecord
    fidelity: tuple[MechanismFidelity, ...]
    content: ContentBundle


class CompiledStaticPackage(_StrictContract):
    schema_id: Literal[PACKAGE_SCHEMA] = PACKAGE_SCHEMA
    audience: StaticAudience
    assets: tuple[CompiledStaticAsset, ...]
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def computed_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"package_hash"}))

    def verify_hash(self) -> bool:
        return self.package_hash == self.computed_hash()


_VISIBILITY_BY_AUDIENCE: dict[StaticAudience, frozenset[VisibilityClass]] = {
    StaticAudience.FIRSTSEEN: frozenset({VisibilityClass.BASE_PUBLIC}),
    StaticAudience.DEEP: frozenset({VisibilityClass.BASE_PUBLIC, VisibilityClass.IS_RESTRICTED}),
    StaticAudience.JUDGE: frozenset(VisibilityClass),
}

_EXECUTABLE_KEYS = frozenset(
    {
        "callable",
        "code",
        "command",
        "eval",
        "exec",
        "expression",
        "import",
        "module",
        "plugin",
        "script",
        "shell",
    }
)


def _reject_executable_keys(value: object, *, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = "".join(char for char in str(key).casefold() if char.isalnum())
            if normalized in _EXECUTABLE_KEYS:
                raise ContentCompileError(f"executable field is forbidden at {path}.{key}")
            _reject_executable_keys(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_executable_keys(child, path=f"{path}[{index}]")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContentCompileError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def parse_static_source(raw: bytes) -> StaticSourceDocument:
    """Parse one bounded UTF-8 JSON document without executing source content."""

    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ContentCompileError(f"source size must be within 1..{MAX_SOURCE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ContentCompileError(f"non-finite JSON number is forbidden: {value}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContentCompileError("source must be strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ContentCompileError("source document must be a JSON object")
    _reject_executable_keys(payload)
    try:
        # JSON-mode strict validation accepts JSON enum/tuple encodings while
        # still rejecting coercions such as a numeric string for an integer.
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return StaticSourceDocument.model_validate_json(canonical, strict=True)
    except ValidationError as exc:
        raise ContentCompileError("source document failed the static schema") from exc


def build_static_source(
    *,
    asset_id: str,
    content: ContentBundle,
    evidence_ref: str,
    license_id: str,
    permitted_audiences: tuple[StaticAudience, ...],
) -> StaticSourceDocument:
    """Build a metadata-closed source document from an already validated bundle."""

    return StaticSourceDocument(
        asset_id=asset_id,
        visibility=content.visibility,
        provenance=ProvenanceRecord(
            source_id=content.source,
            payload_sha256=content.content_hash,
            evidence_ref=evidence_ref,
        ),
        license=LicenseRecord(
            license_id=license_id,
            permitted_audiences=permitted_audiences,
        ),
        fidelity=content.mechanism_fidelity,
        content=content,
    )


def compile_static_package(
    sources: tuple[StaticSourceDocument, ...],
    *,
    audience: StaticAudience,
) -> CompiledStaticPackage:
    """Compile an audience package using the visibility lattice.

    Empty output is legal (notably for an unseeded first-seen package), but the
    input set itself must be non-empty and internally collision-free.
    """

    if not sources:
        raise ContentCompileError("at least one static source is required")
    asset_ids = [source.asset_id.casefold() for source in sources]
    if len(asset_ids) != len(set(asset_ids)):
        raise ContentCompileError("asset_id values collide case-insensitively")
    bundle_ids = [source.content.bundle_id.casefold() for source in sources]
    if len(bundle_ids) != len(set(bundle_ids)):
        raise ContentCompileError("bundle_id values collide case-insensitively")

    allowed_visibility = _VISIBILITY_BY_AUDIENCE[audience]
    selected: list[CompiledStaticAsset] = []
    for source in sources:
        # Revalidate copies so model_construct() cannot smuggle unverified input.
        try:
            checked = StaticSourceDocument.model_validate(
                source.model_dump(mode="python"), strict=True
            )
        except ValidationError as exc:
            raise ContentCompileError("source document failed revalidation") from exc
        if checked.visibility not in allowed_visibility:
            continue
        if audience not in checked.license.permitted_audiences:
            raise ContentCompileError(
                f"license {checked.license.license_id!r} does not permit {audience.value}"
            )
        selected.append(
            CompiledStaticAsset(
                asset_id=checked.asset_id,
                visibility=checked.visibility,
                provenance=checked.provenance,
                license=checked.license,
                fidelity=checked.fidelity,
                content=checked.content,
            )
        )
    assets = tuple(sorted(selected, key=lambda item: item.asset_id.casefold()))
    body = {
        "schema_id": PACKAGE_SCHEMA,
        "audience": audience.value,
        "assets": [asset.model_dump(mode="json") for asset in assets],
    }
    return CompiledStaticPackage(
        audience=audience,
        assets=assets,
        package_hash=canonical_sha256(body),
    )


def compile_all_audiences(
    sources: tuple[StaticSourceDocument, ...],
) -> dict[StaticAudience, CompiledStaticPackage]:
    """Build all three deterministic distributions from one reviewed source set."""

    return {
        audience: compile_static_package(sources, audience=audience) for audience in StaticAudience
    }


__all__ = [
    "CompiledStaticAsset",
    "CompiledStaticPackage",
    "ContentCompileError",
    "LicenseRecord",
    "MAX_SOURCE_BYTES",
    "PACKAGE_SCHEMA",
    "ProvenanceRecord",
    "SOURCE_SCHEMA",
    "StaticAudience",
    "StaticSourceDocument",
    "build_static_source",
    "compile_all_audiences",
    "compile_static_package",
    "parse_static_source",
]
