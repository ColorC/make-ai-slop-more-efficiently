from __future__ import annotations

"""Isolated work packets and loss-proof gates for Terra-authored reader stories.

The writer never edits a canonical partial bundle. ``prepare`` materializes a
read-only shadow packet, ``validate`` checks the candidate and its evidence
ledgers, and ``merge`` inserts only ``design_document.reader_story`` after the
protected payload is proven unchanged.
"""

import argparse
import copy
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKET_SCHEMA = "game-observatory.reader-story-work-packet.v1"
LEDGER_SCHEMA = "game-observatory.reader-story-atom-ledger.v1"
COVERAGE_SCHEMA = "game-observatory.reader-story-coverage-map.v1"
COVERAGE_ASSIGNMENT_SCHEMA = (
    "game-observatory.reader-story-coverage-assignment.v1"
)
IMAGE_MANIFEST_SCHEMA = "game-observatory.reader-story-image-manifest.v1"
IMAGE_REVIEW_SCHEMA = "game-observatory.reader-story-image-review-log.v1"
VALIDATION_SCHEMA = "game-observatory.reader-story-validation.v1"

REQUIRED_SECTION_IDS = (
    "interfaces-and-operations",
    "observed-operation",
    "experience-inference",
)
ALLOWED_ATOM_STATUSES = {
    "exact",
    "semantic",
    "linked",
    "moved_to_internal",
    "missing",
    "contradicted",
    "overclaimed",
}
PASSING_READER_STATUSES = {"exact", "semantic", "linked"}
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
}
IMAGE_KINDS = {
    "screenshot",
    "video_frame",
    "annotated_plate",
    "wireframe",
    "wireflow",
    "state_diagram",
    "interaction_diagram",
    "resource_diagram",
}
BLOCKED_READER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("contrastive-not-but", re.compile(r"(?:不是|并非)[^。；\n]{0,48}而是")),
    ("visit-sequence-shorthand", re.compile(r"(?:首|二|三)访")),
    (
        "internal-workflow-language",
        re.compile(
            r"AI\s*玩家|Agent|EvidenceRun|OperationMemory|publication_ready|"
            r"\bpartial\b|source_ref|已发布局部系统页|本次整理|取证流程|"
            r"安全返回",
            re.IGNORECASE,
        ),
    ),
    (
        "guide-language",
        re.compile(
            r"(?:本|这)(?:篇|页|份)?攻略|攻略(?:建议|推荐|写法)|"
            r"建议玩家|玩家应当|最好先|务必|记得|优先选择|优先升级"
        ),
    ),
)
NON_READER_LEAF_KEYS = {
    "bounds",
    "point",
    "target_bounds",
    "metadata",
    "locator",
    "url",
    "path",
    "sha256",
    "content_sha256",
    "captured_at",
    "captured_on",
    "operator",
    "platform",
    "source_type",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_reader_text(path: Path, text: str) -> None:
    """Write a PowerShell 5-readable UTF-8 view without changing machine JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8-sig")


def _reader_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def _json_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _without_reader_story(value: Mapping[str, Any]) -> dict[str, Any]:
    protected = copy.deepcopy(dict(value))
    design = protected.get("design_document")
    if isinstance(design, dict):
        design.pop("reader_story", None)
    return protected


def _all_object_ids(value: Any, *, path: str = "$") -> dict[str, str]:
    found: dict[str, str] = {}
    if isinstance(value, dict):
        identifier = value.get("id")
        if isinstance(identifier, str) and identifier.strip():
            found[identifier] = path
        for key, child in value.items():
            if path == "$.design_document" and key == "reader_story":
                continue
            found.update(_all_object_ids(child, path=f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.update(_all_object_ids(child, path=f"{path}[{index}]"))
    return found


def _artifact_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str) and (
                key == "artifact_id" or key.endswith("_artifact_id")
            ):
                if child.strip():
                    found.add(child)
            elif isinstance(child, list) and (
                key == "artifact_ids" or key.endswith("_artifact_ids")
            ):
                found.update(
                    item for item in child if isinstance(item, str) and item.strip()
                )
            found.update(_artifact_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_artifact_ids(child))
    return found


def _safe_token(value: str) -> str:
    token = re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("-")
    return token[:96] or "item"


def _short_text(value: Any, limit: int = 240) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _title_of(value: Any, fallback: str) -> str:
    if isinstance(value, dict):
        for key in ("title", "name", "label", "subject", "resource", "id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    if isinstance(value, str) and value.strip():
        return _short_text(value, 80)
    return fallback


def _marker_values(value: Any) -> list[str]:
    markers: list[str] = []
    if isinstance(value, dict):
        for key in (
            "title",
            "name",
            "label",
            "subject",
            "resource",
            "displayed_value",
            "observed_rule",
        ):
            candidate = value.get(key)
            if isinstance(candidate, (str, int, float)):
                text = str(candidate).strip()
                if 1 < len(text) <= 48 and text not in markers:
                    markers.append(text)
    elif isinstance(value, (str, int, float)):
        text = str(value).strip()
        if 1 < len(text) <= 48:
            markers.append(text)
    return markers[:8]


def _atom_id(kind: str, path: str, value: Any) -> str:
    identifier = value.get("id") if isinstance(value, dict) else None
    if isinstance(identifier, str) and identifier.strip():
        return identifier
    suffix = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return f"atom.{_safe_token(kind)}.{_safe_token(path)}.{suffix}"


def _append_atom(
    atoms: list[dict[str, Any]],
    *,
    kind: str,
    source_path: str,
    value: Any,
    obligation: str = "reader",
    priority: str = "P0",
) -> None:
    if value in (None, "", [], {}):
        return
    atom_id = _atom_id(kind, source_path, value)
    if any(item["atom_id"] == atom_id for item in atoms):
        atom_id = f"{atom_id}.{len(atoms)}"
    atoms.append(
        {
            "atom_id": atom_id,
            "kind": kind,
            "source_path": source_path,
            "title": _title_of(value, source_path.rsplit(".", 1)[-1]),
            "source_digest": _json_sha256(value),
            "source_excerpt": _short_text(value),
            "markers": _marker_values(value),
            "artifact_ids": sorted(_artifact_ids(value)),
            "obligation": obligation,
            "priority": priority,
        }
    )


def _append_scalar_leaves(
    atoms: list[dict[str, Any]],
    value: Any,
    *,
    kind: str,
    source_path: str,
    obligation: str = "reader",
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                key in {"reader_story", "source_refs", "template_version"}
                or key in NON_READER_LEAF_KEYS
                or key == "id"
                or key.endswith("_id")
                or key.endswith("_ids")
            ):
                continue
            _append_scalar_leaves(
                atoms,
                child,
                kind=kind,
                source_path=f"{source_path}.{key}",
                obligation=obligation,
            )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _append_scalar_leaves(
                atoms,
                child,
                kind=kind,
                source_path=f"{source_path}[{index}]",
                obligation=obligation,
            )
    elif isinstance(value, (str, int, float, bool)) and str(value).strip():
        _append_atom(
            atoms,
            kind=kind,
            source_path=source_path,
            value=value,
            obligation=obligation,
        )


def build_atom_ledger(
    bundle: Mapping[str, Any],
    *,
    legacy_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a reader-coverage ledger without prescribing article chapter count."""

    atoms: list[dict[str, Any]] = []
    for field in ("coverage_claim",):
        if field in bundle:
            _append_atom(
                atoms,
                kind="coverage-claim",
                source_path=f"$.{field}",
                value=bundle[field],
            )
    scope = bundle.get("scope")
    if isinstance(scope, dict) and scope.get("coverage"):
        _append_atom(
            atoms,
            kind="scope-boundary",
            source_path="$.scope.coverage",
            value=scope["coverage"],
        )

    design = bundle.get("design_document")
    if isinstance(design, dict):
        _append_scalar_leaves(
            atoms,
            design,
            kind="design-fact",
            source_path="$.design_document",
        )

    list_contracts: tuple[tuple[str, str, str], ...] = (
        ("screen_families", "interface-family", "reader"),
        ("screen_states", "interface-state", "reader"),
        ("ui_elements", "interface-element", "reader"),
        ("interactions", "observed-interaction", "reader"),
        ("state_transitions", "state-transition", "reader"),
        ("visible_mechanics", "visible-mechanic", "reader"),
        ("resource_displays", "resource-display", "reader"),
        ("play_records", "play-record", "reader"),
        ("demo_reproductions", "demo-reproduction", "reader"),
        ("community_feedback", "community-feedback", "reader"),
        ("evidence_gaps", "evidence-gap", "boundary"),
        ("source_refs", "source-reference", "internal"),
    )
    for key, kind, obligation in list_contracts:
        values = bundle.get(key, [])
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            path = f"$.{key}[{index}]"
            _append_atom(
                atoms,
                kind=kind,
                source_path=path,
                value=value,
                obligation=obligation,
                priority="P1" if obligation == "internal" else "P0",
            )
            if kind in {
                "interface-family",
                "interface-state",
                "observed-interaction",
                "resource-display",
                "evidence-gap",
            }:
                _append_scalar_leaves(
                    atoms,
                    value,
                    kind=f"{kind}-fact",
                    source_path=path,
                    obligation=obligation,
                )

    if legacy_profile:
        _append_scalar_leaves(
            atoms,
            legacy_profile,
            kind="legacy-reader-fact",
            source_path="$.legacy_reader_profile",
        )

    return {
        "schema": LEDGER_SCHEMA,
        "source_bundle_id": bundle.get("id"),
        "play_slug": (bundle.get("play") or {}).get("slug"),
        "policy": {
            "p0_coverage_required": 1.0,
            "all_reader_atoms_required": 0.98,
            "hard_fail_statuses": ["missing", "contradicted", "overclaimed"],
            "internal_atoms_may_use": ["moved_to_internal"],
        },
        "atoms": atoms,
        "counts": {
            "total": len(atoms),
            "p0": sum(item["priority"] == "P0" for item in atoms),
            "reader": sum(item["obligation"] == "reader" for item in atoms),
            "boundary": sum(item["obligation"] == "boundary" for item in atoms),
            "internal": sum(item["obligation"] == "internal" for item in atoms),
        },
    }


def _required_image_ids(bundle: Mapping[str, Any]) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}

    def mark(artifact_id: Any, reason: str) -> None:
        if isinstance(artifact_id, str) and artifact_id.strip():
            reasons.setdefault(artifact_id, []).append(reason)

    design = bundle.get("design_document")
    if isinstance(design, dict):
        for module in design.get("mechanism_modules", []):
            if isinstance(module, dict):
                mark(module.get("artifact_id"), "mechanism-module")
    for family in bundle.get("screen_families", []):
        if isinstance(family, dict):
            mark(family.get("representative_artifact_id"), "screen-family-representative")
    for state in bundle.get("screen_states", []):
        if isinstance(state, dict):
            artifact_ids = state.get("artifact_ids", [])
            if isinstance(artifact_ids, list) and artifact_ids:
                mark(artifact_ids[0], "screen-state")
    for interaction in bundle.get("interactions", []):
        if isinstance(interaction, dict):
            artifact_ids = interaction.get("artifact_ids", [])
            if isinstance(artifact_ids, list) and artifact_ids:
                mark(artifact_ids[-1], "observed-interaction-result")
    return reasons


def _resolve_artifact_path(
    artifact_id: str,
    *,
    facility: Any,
    artifact: Any,
) -> Path | None:
    candidates: list[Path] = []
    if artifact is not None:
        declared = Path(str(artifact.path))
        candidates.append(
            declared if declared.is_absolute() else facility.store.root / declared
        )
    candidates.append(facility.store.artifact_root / artifact_id)
    for suffix in IMAGE_SUFFIXES | {".json", ".jsonl", ".txt", ".log", ".mp4", ".webm"}:
        candidates.append(facility.store.artifact_root / f"{artifact_id}{suffix}")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def build_artifact_manifest(
    bundle: Mapping[str, Any],
    *,
    facility: Any,
    workspace: Path,
) -> dict[str, Any]:
    """Copy every referenced artifact into the shadow packet.

    Copies are deliberate: a hardlink would let a workspace writer mutate the
    canonical artifact through the candidate directory.
    """

    artifact_ids = sorted(_artifact_ids(bundle))
    store_artifacts = facility.store.get_artifacts(artifact_ids)
    required_reasons = _required_image_ids(bundle)
    assets = workspace / "input" / "artifacts"
    assets.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    copied_by_sha: dict[str, str] = {}

    for artifact_id in artifact_ids:
        artifact = store_artifacts.get(artifact_id)
        path = _resolve_artifact_path(
            artifact_id,
            facility=facility,
            artifact=artifact,
        )
        suffix = path.suffix.lower() if path else ""
        kind = str(getattr(artifact, "kind", "") or "")
        media_type = str(getattr(artifact, "media_type", "") or "")
        is_image = (
            kind in IMAGE_KINDS
            or suffix in IMAGE_SUFFIXES
            or media_type.startswith("image/")
        )
        packet_path: str | None = None
        actual_sha: str | None = None
        size: int | None = None
        if path:
            actual_sha = _file_sha256(path)
            size = path.stat().st_size
            if actual_sha in copied_by_sha:
                packet_path = copied_by_sha[actual_sha]
            else:
                target_name = f"{actual_sha[:20]}-{_safe_token(path.name)}"
                target = assets / target_name
                shutil.copy2(path, target)
                packet_path = target.relative_to(workspace).as_posix()
                copied_by_sha[actual_sha] = packet_path
        declared_sha = str(getattr(artifact, "sha256", "") or "")
        entries.append(
            {
                "artifact_id": artifact_id,
                "kind": kind or None,
                "media_type": media_type
                or (mimetypes.guess_type(path.name)[0] if path else None),
                "is_image": is_image,
                "available": path is not None,
                "packet_path": packet_path,
                "sha256": actual_sha or declared_sha or None,
                "declared_sha256": declared_sha or None,
                "bytes": size,
                "required_review": bool(is_image and artifact_id in required_reasons),
                "review_reasons": required_reasons.get(artifact_id, []),
                "source_metadata": (
                    getattr(artifact, "metadata", {}) if artifact is not None else {}
                ),
            }
        )

    return {
        "schema": IMAGE_MANIFEST_SCHEMA,
        "source_bundle_id": bundle.get("id"),
        "entries": entries,
        "counts": {
            "referenced": len(entries),
            "available": sum(item["available"] for item in entries),
            "images": sum(item["is_image"] and item["available"] for item in entries),
            "required_review": len(
                {
                    item["sha256"]
                    for item in entries
                    if item["required_review"] and item["sha256"]
                }
            ),
        },
    }


def _load_legacy_profile(play_slug: str) -> Mapping[str, Any] | None:
    try:
        from .reader_projection import READER_PLAY_PROFILES
    except (ImportError, ValueError):
        return None
    profile = READER_PLAY_PROFILES.get(play_slug)
    return copy.deepcopy(profile) if isinstance(profile, dict) else None


def _materialize_bundle(source: Path) -> tuple[dict[str, Any], Any]:
    try:
        from .api import _load_partial_fact_bundle
        from .runtime import GameObservatory
    except (ImportError, ValueError) as exc:
        raise RuntimeError(
            "prepare must run with the omnicompany src tree on PYTHONPATH"
        ) from exc
    facility = GameObservatory()
    return _load_partial_fact_bundle(source.resolve(), facility), facility


def _extract_story(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("reader_story"), dict):
        value = value["reader_story"]
    if (
        isinstance(value, dict)
        and isinstance(value.get("design_document"), dict)
        and isinstance(value["design_document"].get("reader_story"), dict)
    ):
        value = value["design_document"]["reader_story"]
    if not isinstance(value, dict):
        raise ValueError("reader story must be a JSON object")
    return value


_AGGREGATE_LIST_KEYS = (
    "screen_families",
    "screen_states",
    "ui_elements",
    "interactions",
    "state_transitions",
    "visible_mechanics",
    "resource_displays",
    "play_records",
    "demo_reproductions",
    "community_feedback",
    "evidence_gaps",
    "source_refs",
)


def _aggregate_target_bundles(
    primary: Mapping[str, Any],
    related: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a validation-only system view without mutating canonical bundles."""

    aggregate = copy.deepcopy(dict(primary))
    for key in _AGGREGATE_LIST_KEYS:
        combined: list[Any] = []
        seen: set[tuple[str, str]] = set()
        for bundle in (primary, *related):
            values = bundle.get(key, [])
            if not isinstance(values, list):
                continue
            for value in values:
                identifier = (
                    str(value.get("id") or "")
                    if isinstance(value, dict)
                    else ""
                )
                identity = (identifier, _json_sha256(value))
                if identity in seen:
                    continue
                seen.add(identity)
                combined.append(copy.deepcopy(value))
        if combined:
            aggregate[key] = combined
    aggregate["reader_story_related_bundles"] = [
        {
            "id": bundle.get("id"),
            "play": copy.deepcopy(bundle.get("play")),
            "scope": copy.deepcopy(bundle.get("scope")),
            "design_document": copy.deepcopy(bundle.get("design_document")),
        }
        for bundle in related
    ]
    return aggregate


def _aggregate_source_id(
    primary: Mapping[str, Any],
    related: Sequence[Mapping[str, Any]],
) -> str:
    bundle_ids = [
        str(bundle.get("id") or "")
        for bundle in (primary, *related)
        if str(bundle.get("id") or "")
    ]
    if len(bundle_ids) == 1:
        return bundle_ids[0]
    return "reader-story-aggregate." + _json_sha256(bundle_ids)[:20]


def _combine_atom_ledgers(
    ledgers: Sequence[Mapping[str, Any]],
    *,
    source_bundle_id: str,
    play_slug: str,
) -> dict[str, Any]:
    atoms: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for ledger in ledgers:
        origin = str(ledger.get("source_bundle_id") or "")
        for raw_atom in ledger.get("atoms", []):
            if not isinstance(raw_atom, dict):
                continue
            atom = copy.deepcopy(raw_atom)
            atom["origin_bundle_id"] = origin
            atom_id = str(atom.get("atom_id") or "")
            existing = by_id.get(atom_id)
            if existing:
                if existing.get("source_digest") == atom.get("source_digest"):
                    continue
                atom_id = (
                    f"{atom_id}@@"
                    f"{_json_sha256([origin, atom_id, atom.get('source_digest')])[:12]}"
                )
                atom["atom_id"] = atom_id
            by_id[atom_id] = atom
            atoms.append(atom)
    return {
        "schema": LEDGER_SCHEMA,
        "source_bundle_id": source_bundle_id,
        "play_slug": play_slug,
        "policy": {
            "p0_coverage_required": 1.0,
            "all_reader_atoms_required": 0.98,
            "hard_fail_statuses": ["missing", "contradicted", "overclaimed"],
            "internal_atoms_may_use": ["moved_to_internal"],
        },
        "atoms": atoms,
        "counts": {
            "total": len(atoms),
            "p0": sum(item["priority"] == "P0" for item in atoms),
            "reader": sum(item["obligation"] == "reader" for item in atoms),
            "boundary": sum(
                item["obligation"] == "boundary" for item in atoms
            ),
            "internal": sum(
                item["obligation"] == "internal" for item in atoms
            ),
        },
    }


def _combine_artifact_manifests(
    manifests: Sequence[Mapping[str, Any]],
    *,
    source_bundle_id: str,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for manifest in manifests:
        origin = str(manifest.get("source_bundle_id") or "")
        for raw_entry in manifest.get("entries", []):
            if not isinstance(raw_entry, dict):
                continue
            entry = copy.deepcopy(raw_entry)
            artifact_id = str(entry.get("artifact_id") or "")
            existing = by_id.get(artifact_id)
            if existing:
                existing_sha = str(existing.get("sha256") or "")
                new_sha = str(entry.get("sha256") or "")
                if existing_sha and new_sha and existing_sha != new_sha:
                    raise ValueError(
                        "related bundles disagree on artifact content: "
                        f"{artifact_id}"
                    )
                if not existing.get("available") and entry.get("available"):
                    preserved_origins = existing.get("origin_bundle_ids", [])
                    existing.clear()
                    existing.update(entry)
                    existing["origin_bundle_ids"] = preserved_origins
                existing["required_review"] = bool(
                    existing.get("required_review")
                    or entry.get("required_review")
                )
                existing["review_reasons"] = sorted(
                    {
                        *existing.get("review_reasons", []),
                        *entry.get("review_reasons", []),
                    }
                )
                origins = existing.setdefault("origin_bundle_ids", [])
                if origin and origin not in origins:
                    origins.append(origin)
                continue
            entry["origin_bundle_ids"] = [origin] if origin else []
            by_id[artifact_id] = entry
            entries.append(entry)
    return {
        "schema": IMAGE_MANIFEST_SCHEMA,
        "source_bundle_id": source_bundle_id,
        "entries": entries,
        "counts": {
            "referenced": len(entries),
            "available": sum(item["available"] for item in entries),
            "images": sum(
                item["is_image"] and item["available"] for item in entries
            ),
            "required_review": len(
                {
                    item["sha256"]
                    for item in entries
                    if item["required_review"] and item["sha256"]
                }
            ),
        },
    }


def prepare_workspace(
    *,
    source: Path,
    workspace: Path,
    prompt: Path,
    standard: Path,
    examples: Sequence[Path] = (),
    related_sources: Sequence[Path] = (),
) -> dict[str, Any]:
    source = source.resolve()
    workspace = workspace.resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise FileExistsError(f"refusing to overwrite non-empty workspace: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    raw = _read_json(source)
    materialized, facility = _materialize_bundle(source)
    related_materialized: list[dict[str, Any]] = []
    resolved_related_sources: list[Path] = []
    for related_source in related_sources:
        resolved = related_source.resolve()
        related_bundle, _ = _materialize_bundle(resolved)
        related_materialized.append(related_bundle)
        resolved_related_sources.append(resolved)
    play_slug = str((materialized.get("play") or {}).get("slug") or source.stem)
    projection_profile = _load_legacy_profile(play_slug)
    source_design = raw.get("design_document")
    existing_story = (
        copy.deepcopy(source_design.get("reader_story"))
        if isinstance(source_design, dict)
        and isinstance(source_design.get("reader_story"), dict)
        else None
    )
    legacy_sources: dict[str, Any] = {}
    if existing_story:
        legacy_sources["current_reader_story"] = existing_story
    if projection_profile:
        legacy_sources["projection_profile"] = projection_profile
    legacy_profile: Mapping[str, Any] | None = legacy_sources or None

    primary_target = _without_reader_story(materialized)
    related_targets = [
        _without_reader_story(bundle) for bundle in related_materialized
    ]
    target = _aggregate_target_bundles(primary_target, related_targets)
    raw_shadow = _without_reader_story(raw)
    _write_json(workspace / "input" / "target.materialized.json", target)
    _write_json(workspace / "input" / "target.raw-overlay.json", raw_shadow)
    if legacy_profile:
        _write_json(
            workspace / "input" / "existing-reader-profile.json",
            legacy_profile,
        )

    aggregate_source_id = _aggregate_source_id(
        primary_target,
        related_targets,
    )
    ledgers = [
        build_atom_ledger(primary_target, legacy_profile=legacy_profile),
        *[
            build_atom_ledger(related_target)
            for related_target in related_targets
        ],
    ]
    ledger = _combine_atom_ledgers(
        ledgers,
        source_bundle_id=aggregate_source_id,
        play_slug=play_slug,
    )
    _write_json(workspace / "input" / "atom-ledger.json", ledger)
    artifact_manifests = [
        build_artifact_manifest(
            primary_target,
            facility=facility,
            workspace=workspace,
        )
    ]
    for related_target in related_targets:
        artifact_manifests.append(
            build_artifact_manifest(
                related_target,
                facility=facility,
                workspace=workspace,
            )
        )
    artifact_manifest = _combine_artifact_manifests(
        artifact_manifests,
        source_bundle_id=aggregate_source_id,
    )
    _write_json(workspace / "input" / "artifact-manifest.json", artifact_manifest)

    example_index: list[dict[str, Any]] = []
    reader_root = workspace / "input" / "reader"
    reader_root.mkdir(parents=True, exist_ok=True)
    reader_files: list[str] = []
    for index, example in enumerate(examples, start=1):
        example_value = _read_json(example.resolve())
        story = _extract_story(example_value)
        example_slug = str(
            (example_value.get("play") or {}).get("slug")
            if isinstance(example_value, dict)
            else ""
        ) or example.stem
        relative = Path("input") / "gold-examples" / (
            f"{index:02d}-{_safe_token(example_slug)}.reader-story.json"
        )
        _write_json(workspace / relative, story)
        reader_example = reader_root / f"60-gold-example-{index:02d}.txt"
        _write_reader_text(reader_example, _reader_json(story))
        reader_files.append(reader_example.name)
        example_index.append(
            {
                "play_slug": example_slug,
                "path": relative.as_posix(),
                "concepts": len(story.get("concepts", [])),
                "sections": len(story.get("sections", [])),
            }
        )

    shutil.copy2(standard.resolve(), workspace / "input" / "reader-standard.md")
    _write_reader_text(
        reader_root / "10-reader-standard.txt",
        standard.read_text(encoding="utf-8"),
    )
    reader_files.insert(0, "10-reader-standard.txt")

    target_views = (
        (
            "20-target-identity-and-design.txt",
            {
                key: target.get(key)
                for key in (
                    "schema",
                    "id",
                    "status",
                    "publication_ready",
                    "content_kind",
                    "coverage_claim",
                    "game",
                    "play",
                    "scope",
                    "design_document",
                    "content_partition",
                )
                if key in target
            },
        ),
        (
            "21-target-interfaces.txt",
            {
                key: target.get(key, [])
                for key in ("screen_families", "screen_states", "ui_elements")
            },
        ),
        (
            "22-target-behavior.txt",
            {
                key: target.get(key, [])
                for key in (
                    "interactions",
                    "state_transitions",
                    "visible_mechanics",
                    "resource_displays",
                )
            },
        ),
        (
            "23-target-records-and-boundaries.txt",
            {
                key: target.get(key, [])
                for key in (
                    "play_records",
                    "demo_reproductions",
                    "community_feedback",
                    "evidence_gaps",
                    "source_refs",
                )
            },
        ),
    )
    for filename, view in target_views:
        _write_reader_text(reader_root / filename, _reader_json(view))
        reader_files.append(filename)
    if legacy_profile:
        filename = "30-existing-reader-profile.txt"
        _write_reader_text(reader_root / filename, _reader_json(legacy_profile))
        reader_files.append(filename)
    for index, (related_target, related_source) in enumerate(
        zip(related_targets, resolved_related_sources, strict=True),
        start=1,
    ):
        related_slug = str(
            (related_target.get("play") or {}).get("slug")
            or related_source.stem
        )
        machine_path = (
            workspace
            / "input"
            / "related"
            / f"{index:02d}-{_safe_token(related_slug)}.materialized.json"
        )
        _write_json(machine_path, related_target)
        filename = f"25-related-system-{index:02d}-{_safe_token(related_slug)}.txt"
        _write_reader_text(
            reader_root / filename,
            _reader_json(related_target),
        )
        reader_files.append(filename)
    filename = "40-artifact-manifest.txt"
    reader_artifact_manifest = {
        "schema": artifact_manifest["schema"],
        "source_bundle_id": artifact_manifest["source_bundle_id"],
        "counts": artifact_manifest["counts"],
        "entries": [
            {
                key: entry.get(key)
                for key in (
                    "artifact_id",
                    "kind",
                    "is_image",
                    "available",
                    "packet_path",
                    "sha256",
                    "required_review",
                    "review_reasons",
                )
            }
            for entry in artifact_manifest["entries"]
        ],
    }
    _write_reader_text(
        reader_root / filename,
        _reader_json(reader_artifact_manifest),
    )
    reader_files.append(filename)
    atom_chunk_size = 100
    ledger_atoms = ledger["atoms"]
    for offset in range(0, len(ledger_atoms), atom_chunk_size):
        chunk_index = offset // atom_chunk_size + 1
        filename = f"50-atom-ledger-{chunk_index:03d}.txt"
        chunk = {
            "schema": ledger["schema"],
            "source_bundle_id": ledger["source_bundle_id"],
            "play_slug": ledger["play_slug"],
            "range": {
                "start": offset,
                "end_exclusive": min(offset + atom_chunk_size, len(ledger_atoms)),
                "total": len(ledger_atoms),
            },
            "atoms": [
                {
                    "index": atom_index,
                    "kind": atom.get("kind"),
                    "source_path": atom.get("source_path"),
                    "fact": _short_text(
                        atom.get("source_excerpt") or atom.get("title"),
                        160,
                    ),
                    "obligation": atom.get("obligation"),
                }
                for atom_index, atom in enumerate(
                    ledger_atoms[
                        offset : offset + atom_chunk_size
                    ],
                    start=offset,
                )
            ],
        }
        _write_reader_text(reader_root / filename, _reader_json(chunk))
        reader_files.append(filename)
    _write_reader_text(
        reader_root / "00-READ-ME.txt",
        (
            "这些文件是机器输入的 UTF-8 BOM 只读镜像，供 Windows PowerShell 5 "
            "正确显示中文。按文件名前缀顺序完整读取。不得使用 Get-Content 读取 "
            "input 根目录中的无 BOM JSON；校验器仍使用那些机器 JSON。\n\n"
            + "\n".join(f"- {name}" for name in sorted(reader_files))
            + "\n"
        ),
    )

    template = prompt.read_text(encoding="utf-8")
    run_spec = (
        template.replace("{{TARGET_SLUG}}", play_slug)
        .replace(
            "{{VALIDATE_COMMAND}}",
            f'"{sys.executable}" validator.py validate --workspace .',
        )
        .replace("{{WORKSPACE}}", workspace.as_posix())
    )
    (workspace / "prompt.md").write_text(run_spec, encoding="utf-8")
    shutil.copy2(Path(__file__).resolve(), workspace / "validator.py")
    (workspace / "output").mkdir(exist_ok=True)

    protected_raw = _without_reader_story(raw)
    protected_materialized = _without_reader_story(materialized)
    baseline = {
        "schema": PACKET_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(source),
        "source_file_sha256": _file_sha256(source),
        "source_had_reader_story": bool(
            isinstance(raw.get("design_document"), dict)
            and isinstance(raw["design_document"].get("reader_story"), dict)
        ),
        "protected_raw_sha256": _json_sha256(protected_raw),
        "protected_materialized_sha256": _json_sha256(protected_materialized),
        "protected_aggregate_sha256": _json_sha256(target),
        "protected_object_ids": _all_object_ids(protected_materialized),
        "protected_artifact_ids": sorted(_artifact_ids(protected_materialized)),
        "source_bundle_id": materialized.get("id"),
        "aggregate_source_bundle_id": aggregate_source_id,
        "play_slug": play_slug,
        "related_sources": [
            {
                "path": str(path),
                "file_sha256": _file_sha256(path),
                "bundle_id": bundle.get("id"),
                "play_slug": (bundle.get("play") or {}).get("slug"),
            }
            for path, bundle in zip(
                resolved_related_sources,
                related_materialized,
                strict=True,
            )
        ],
        "examples": example_index,
        "candidate_files": {
            "reader_story": "output/reader_story.json",
            "coverage_map": "output/coverage-map.json",
            "image_review_log": "output/image-review-log.json",
        },
    }
    _write_json(workspace / "input" / "baseline-manifest.json", baseline)
    return {
        "ok": True,
        "workspace": str(workspace),
        "play_slug": play_slug,
        "atoms": ledger["counts"],
        "artifacts": artifact_manifest["counts"],
        "examples": example_index,
        "related_sources": [
            {
                "path": str(path),
                "bundle_id": bundle.get("id"),
                "play_slug": (bundle.get("play") or {}).get("slug"),
            }
            for path, bundle in zip(
                resolved_related_sources,
                related_materialized,
                strict=True,
            )
        ],
    }


def _story_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_story_text(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(_story_text(item) for item in value.values())
    return str(value) if value is not None else ""


def _reader_prose_text(value: Any, *, parent_key: str = "") -> str:
    """Return reader-visible prose while excluding ids and evidence metadata."""

    if parent_key in {
        "id",
        "artifact_id",
        "artifact_ids",
        "play_slug",
        "source_ref",
        "source_refs",
        "evidence_step_id",
        "evidence_step_ids",
    }:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            _reader_prose_text(item, parent_key=parent_key) for item in value
        )
    if isinstance(value, dict):
        return "\n".join(
            _reader_prose_text(item, parent_key=str(key))
            for key, item in value.items()
        )
    return str(value) if value is not None else ""


def _fact_first_prose_text(story: Mapping[str, Any]) -> str:
    """Return prose that must explain the game before player behavior or inference."""

    sections = [
        item
        for item in story.get("sections", [])
        if isinstance(item, Mapping)
        and item.get("id") not in {"observed-operation", "experience-inference"}
    ]
    return _reader_prose_text(
        {
            "title": story.get("title"),
            "summary": story.get("summary"),
            "lead": story.get("lead"),
            "sections": sections,
        }
    )


def _story_artifact_ids(story: Mapping[str, Any]) -> set[str]:
    return _artifact_ids(story)


def public_reader_link_targets(
    *,
    source_path: Path,
    target: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Return same-game canonical pages that are safe Wiki link targets."""

    drafts = source_path.resolve().parent
    candidates = sorted(drafts.glob("*.partial.v1.json"))
    if len(candidates) < 2:
        return []
    try:
        from .reader_content_taxonomy import reader_content_position
    except ImportError:
        return []

    current_game = (
        target.get("game") if isinstance(target.get("game"), Mapping) else {}
    )
    current_scope = (
        target.get("scope") if isinstance(target.get("scope"), Mapping) else {}
    )
    current_game_id = str(
        current_game.get("id") or current_scope.get("game_id") or ""
    )
    targets: list[dict[str, str]] = []
    for path in candidates:
        try:
            raw = _read_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        game = raw.get("game") if isinstance(raw.get("game"), dict) else {}
        scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
        play = raw.get("play") if isinstance(raw.get("play"), dict) else {}
        slug = str(play.get("slug") or "")
        game_id = str(game.get("id") or scope.get("game_id") or "")
        if not slug or game_id != current_game_id:
            continue
        position = reader_content_position(game_id, slug)
        if (
            raw.get("publication_ready") is not True
            or position.get("reader_visibility", "public") != "public"
        ):
            continue
        targets.append(
            {
                "play_slug": slug,
                "title": str(play.get("title") or slug),
                "level": str(position.get("level") or ""),
            }
        )
    return sorted(targets, key=lambda item: item["play_slug"])


def _reader_story_link_issues(
    story: Mapping[str, Any],
    *,
    source_path: Path,
    target: Mapping[str, Any],
) -> list[str]:
    """Validate reader Wiki targets when the packet came from canonical drafts."""

    requested: list[tuple[str, str]] = []
    for concept in story.get("concepts", []):
        if isinstance(concept, Mapping) and concept.get("play_slug"):
            requested.append((str(concept["play_slug"]), ""))
    for section in story.get("sections", []):
        if not isinstance(section, Mapping):
            continue
        for related in section.get("related", []):
            if isinstance(related, Mapping) and related.get("play_slug"):
                requested.append(
                    (
                        str(related["play_slug"]),
                        str(related.get("section") or ""),
                    )
                )
    if not requested:
        return []

    public_targets = {
        item["play_slug"]
        for item in public_reader_link_targets(
            source_path=source_path,
            target=target,
        )
    }

    valid_sections = {
        "design",
        "interfaces",
        "screen-tags",
        "records",
        "feedback",
        "tags",
        "demo",
    }
    issues: list[str] = []
    for slug, section in requested:
        if slug not in public_targets:
            issues.append(f"reader Wiki link targets a non-public page: {slug}")
        if section and section not in valid_sections:
            issues.append(
                f"reader Wiki link uses an invalid section {section!r}: {slug}"
            )
    return issues


def _valid_after_path(path: str, story: Mapping[str, Any]) -> bool:
    if path in {"reader_story", "summary", "lead", "title"}:
        return True
    concept_ids = {
        item.get("id")
        for item in story.get("concepts", [])
        if isinstance(item, dict)
    }
    section_ids = {
        item.get("id")
        for item in story.get("sections", [])
        if isinstance(item, dict)
    }
    if path.startswith("concepts."):
        return path.split(".", 2)[1] in concept_ids
    if path.startswith("sections."):
        return path.split(".", 2)[1] in section_ids
    return False


def _coverage_results(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict) and isinstance(value.get("atom_results"), list):
        return [item for item in value["atom_results"] if isinstance(item, dict)]
    return []


def _table_sequence_numbers(story: Mapping[str, Any]) -> set[str]:
    """Exclude presentation row numbers from mechanism-number recall."""

    numbers: set[str] = set()
    for section in story.get("sections", []):
        if not isinstance(section, dict):
            continue
        table = section.get("table")
        if not isinstance(table, dict):
            continue
        columns = table.get("columns", [])
        if (
            not isinstance(columns, list)
            or not columns
            or str(columns[0]).strip() not in {"序号", "编号", "步骤"}
        ):
            continue
        for row in table.get("rows", []):
            if (
                isinstance(row, list)
                and row
                and re.fullmatch(r"\d+", str(row[0]).strip())
            ):
                numbers.add(str(row[0]).strip())
    return numbers


def _reference_metrics(story: Mapping[str, Any]) -> dict[str, Any]:
    sections = [
        item for item in story.get("sections", []) if isinstance(item, dict)
    ]
    interface = next(
        (item for item in sections if item.get("id") == "interfaces-and-operations"),
        {},
    )
    operation = next(
        (item for item in sections if item.get("id") == "observed-operation"),
        {},
    )
    operation_flow = operation.get("flow", [])
    operation_table = operation.get("table", {})
    operation_rows = (
        operation_table.get("rows", [])
        if isinstance(operation_table, Mapping)
        else []
    )
    operation_entries = max(
        len(operation_flow) if isinstance(operation_flow, list) else 0,
        len(operation_rows) if isinstance(operation_rows, list) else 0,
    )
    prose_text = _reader_prose_text(story)
    numbers = set(re.findall(r"\d+(?:\.\d+)?%?", prose_text))
    numbers -= _table_sequence_numbers(story)
    return {
        "concepts": len(story.get("concepts", [])),
        "sections": len(sections),
        "interface_items": len(interface.get("items", [])),
        "operation_flow": (
            len(operation_flow) if isinstance(operation_flow, list) else 0
        ),
        "operation_entries": operation_entries,
        "story_artifacts": len(_story_artifact_ids(story)),
        "text_chars": len(re.sub(r"\s+", "", prose_text)),
        "concept_names": sorted(
            {
                str(item.get("name")).strip()
                for item in story.get("concepts", [])
                if isinstance(item, dict) and str(item.get("name") or "").strip()
            }
        ),
        "numbers": sorted(numbers),
    }


def _required_numeric_fact_tokens(
    ledger: Mapping[str, Any],
) -> set[str]:
    """Select reader numbers that must survive prose reorganization exactly."""

    required: set[str] = set()
    for atom in ledger.get("atoms", []):
        if not isinstance(atom, Mapping):
            continue
        if atom.get("obligation") != "reader":
            continue
        kind = str(atom.get("kind") or "")
        source_path = str(atom.get("source_path") or "")
        if kind == "resource-display-fact":
            if ".displayed_value" not in source_path and not source_path.endswith(
                ".label"
            ):
                continue
        elif kind != "legacy-reader-fact":
            continue
        required.update(
            re.findall(
                r"\d+(?:\.\d+)?%?",
                str(atom.get("source_excerpt") or ""),
            )
        )
    return required


def validate_workspace(
    workspace: Path,
    *,
    candidate: Path | None = None,
    coverage_map: Path | None = None,
    image_review_log: Path | None = None,
    reference_story: Path | None = None,
    report: Path | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    candidate = (candidate or workspace / "output" / "reader_story.json").resolve()
    coverage_map = (
        coverage_map or workspace / "output" / "coverage-map.json"
    ).resolve()
    image_review_log = (
        image_review_log or workspace / "output" / "image-review-log.json"
    ).resolve()
    report = (report or workspace / "output" / "validation-report.json").resolve()
    errors: list[str] = []
    warnings: list[str] = []

    required_files = {
        "candidate": candidate,
        "coverage_map": coverage_map,
        "image_review_log": image_review_log,
        "atom_ledger": workspace / "input" / "atom-ledger.json",
        "artifact_manifest": workspace / "input" / "artifact-manifest.json",
        "baseline": workspace / "input" / "baseline-manifest.json",
        "target": workspace / "input" / "target.materialized.json",
    }
    missing = [name for name, path in required_files.items() if not path.is_file()]
    if missing:
        result = {
            "schema": VALIDATION_SCHEMA,
            "passed": False,
            "errors": [f"missing required file: {name}" for name in missing],
            "warnings": [],
            "metrics": {},
        }
        _write_json(report, result)
        return result

    try:
        story = _extract_story(_read_json(candidate))
        coverage = _read_json(coverage_map)
        image_log = _read_json(image_review_log)
        ledger = _read_json(required_files["atom_ledger"])
        artifact_manifest = _read_json(required_files["artifact_manifest"])
        baseline = _read_json(required_files["baseline"])
        target = _read_json(required_files["target"])
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        result = {
            "schema": VALIDATION_SCHEMA,
            "passed": False,
            "errors": [f"candidate packet cannot be read: {exc}"],
            "warnings": [],
            "metrics": {},
        }
        _write_json(report, result)
        return result

    for key in ("eyebrow", "title", "summary"):
        if not isinstance(story.get(key), str) or len(story[key].strip()) < 2:
            errors.append(f"reader_story.{key} must be a non-empty reader-facing string")
    if not isinstance(story.get("lead"), list) or not story["lead"]:
        errors.append("reader_story.lead must contain at least one paragraph")

    concepts = story.get("concepts", [])
    if not isinstance(concepts, list) or len(concepts) < 2:
        errors.append("reader_story.concepts must contain at least two concepts")
        concepts = []
    concept_ids = [
        item.get("id") for item in concepts if isinstance(item, dict)
    ]
    if any(not isinstance(item, str) or not item.strip() for item in concept_ids):
        errors.append("every concept requires a stable id")
    if len(concept_ids) != len(set(concept_ids)):
        errors.append("concept ids must be unique")
    for index, concept in enumerate(concepts):
        if not isinstance(concept, dict):
            errors.append(f"concepts[{index}] must be an object")
            continue
        for key in ("name", "description"):
            if not isinstance(concept.get(key), str) or not concept[key].strip():
                errors.append(f"concepts[{index}].{key} is required")

    sections = story.get("sections", [])
    if not isinstance(sections, list) or len(sections) < 4:
        errors.append("reader_story.sections must contain mechanism and reader-tab sections")
        sections = []
    section_ids = [
        item.get("id") for item in sections if isinstance(item, dict)
    ]
    if len(section_ids) != len(set(section_ids)):
        errors.append("section ids must be unique")
    for required in REQUIRED_SECTION_IDS:
        if required not in section_ids:
            errors.append(f"required section is missing: {required}")
    if all(required in section_ids for required in REQUIRED_SECTION_IDS):
        interface_index = section_ids.index("interfaces-and-operations")
        operation_index = section_ids.index("observed-operation")
        inference_index = section_ids.index("experience-inference")
        if not interface_index < operation_index < inference_index:
            errors.append(
                "interfaces, observed operations and experience inference must remain separate and ordered"
            )
        if inference_index != len(section_ids) - 1:
            errors.append("experience-inference must be the final section")
        inference = sections[inference_index]
        if inference.get("inference") is not True:
            errors.append("experience-inference must declare inference=true")
        if interface_index == 0:
            errors.append("mechanism facts must appear before interfaces and inference")

    section_by_id = {
        item.get("id"): item for item in sections if isinstance(item, dict)
    }
    interface = section_by_id.get("interfaces-and-operations", {})
    interface_items = interface.get("items", [])
    target_states = target.get("screen_states", [])
    minimum_interface_items = min(max(len(target_states), 1), 5)
    if not isinstance(interface_items, list) or (
        _artifact_ids(target) and len(interface_items) < minimum_interface_items
    ):
        errors.append(
            "interfaces-and-operations needs distinct illustrated interface/state items "
            f"(expected at least {minimum_interface_items})"
        )
        interface_items = []
    for index, item in enumerate(interface_items):
        if not isinstance(item, dict):
            errors.append(f"interface item {index} must be an object")
            continue
        for key in ("title", "body", "caption"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                errors.append(f"interface item {index} requires {key}")
        if _artifact_ids(target) and not str(item.get("artifact_id") or "").strip():
            errors.append(f"interface item {index} requires an artifact_id")

    observed = section_by_id.get("observed-operation", {})
    observed_flow = observed.get("flow", [])
    observed_table = observed.get("table", {})
    observed_rows = (
        observed_table.get("rows", [])
        if isinstance(observed_table, Mapping)
        else []
    )
    observed_entry_count = max(
        len(observed_flow) if isinstance(observed_flow, list) else 0,
        len(observed_rows) if isinstance(observed_rows, list) else 0,
    )
    target_interactions = target.get("interactions", [])
    minimum_flow = min(max(len(target_interactions), 1), 5)
    if target_interactions and observed_entry_count < minimum_flow:
        errors.append(
            "observed-operation must record the played operations "
            f"(expected at least {minimum_flow} flow or table entries)"
        )

    reader_text = _reader_prose_text(story)
    for code, pattern in BLOCKED_READER_PATTERNS:
        match = pattern.search(reader_text)
        if match:
            errors.append(f"blocked reader prose ({code}): {match.group(0)!r}")
    fact_first_text = _fact_first_prose_text(story)
    if "玩家" in fact_first_text:
        errors.append(
            "fact-first prose must explain concepts, mechanisms and interfaces "
            "without using 玩家 as the subject; move player behavior to "
            "observed-operation or experience-inference"
        )

    for section_index, section in enumerate(sections):
        if not isinstance(section, Mapping):
            continue
        related_items = section.get("related", [])
        if not isinstance(related_items, list):
            errors.append(f"sections[{section_index}].related must be a list")
            continue
        for related_index, related in enumerate(related_items):
            if not isinstance(related, Mapping):
                errors.append(
                    f"sections[{section_index}].related[{related_index}] "
                    "must be an object"
                )
                continue
            for key in ("play_slug", "title", "description"):
                if not isinstance(related.get(key), str) or not str(
                    related.get(key)
                ).strip():
                    errors.append(
                        f"sections[{section_index}].related[{related_index}] "
                        f"requires {key}"
                    )

    source_path = Path(str(baseline.get("source_path") or ""))
    if source_path.is_file():
        errors.extend(
            _reader_story_link_issues(
                story,
                source_path=source_path,
                target=target,
            )
        )

    manifest_entries = [
        item
        for item in artifact_manifest.get("entries", [])
        if isinstance(item, dict)
    ]
    manifest_by_id = {
        item.get("artifact_id"): item
        for item in manifest_entries
        if isinstance(item.get("artifact_id"), str)
    }
    used_artifacts = _story_artifact_ids(story)
    for artifact_id in sorted(used_artifacts):
        entry = manifest_by_id.get(artifact_id)
        if not entry:
            errors.append(f"story uses an artifact outside the target packet: {artifact_id}")
        elif not entry.get("available"):
            errors.append(f"story uses an unavailable artifact: {artifact_id}")
        elif not entry.get("is_image"):
            errors.append(f"story illustration is not an image: {artifact_id}")

    if coverage.get("schema") != COVERAGE_SCHEMA:
        errors.append(f"coverage map schema must be {COVERAGE_SCHEMA}")
    if coverage.get("source_bundle_id") != ledger.get("source_bundle_id"):
        errors.append("coverage map source_bundle_id does not match the atom ledger")
    coverage_results = _coverage_results(coverage)
    result_by_atom: dict[str, dict[str, Any]] = {}
    for item in coverage_results:
        atom_id = item.get("atom_id")
        if not isinstance(atom_id, str) or not atom_id.strip():
            errors.append("coverage result requires atom_id")
            continue
        if atom_id in result_by_atom:
            errors.append(f"duplicate coverage atom: {atom_id}")
        result_by_atom[atom_id] = item

    atoms = [item for item in ledger.get("atoms", []) if isinstance(item, dict)]
    atom_by_id = {item.get("atom_id"): item for item in atoms}
    unexpected = sorted(set(result_by_atom) - set(atom_by_id))
    if unexpected:
        errors.append(f"coverage map has unknown atoms: {unexpected[:5]}")
    passing_p0 = 0
    passing_reader = 0
    reader_total = 0
    for atom_id, atom in atom_by_id.items():
        result = result_by_atom.get(atom_id)
        obligation = atom.get("obligation")
        if obligation in {"reader", "boundary"}:
            reader_total += 1
        if not result:
            if obligation != "internal":
                errors.append(f"coverage atom is missing: {atom_id}")
            continue
        status = result.get("status")
        if status not in ALLOWED_ATOM_STATUSES:
            errors.append(f"coverage atom {atom_id} has invalid status: {status}")
            continue
        if status in {"missing", "contradicted", "overclaimed"}:
            errors.append(f"coverage atom {atom_id} is a hard failure: {status}")
        if obligation in {"reader", "boundary"} and status not in PASSING_READER_STATUSES:
            errors.append(
                f"reader atom {atom_id} cannot use coverage status {status!r}"
            )
        if obligation == "internal" and status not in (
            PASSING_READER_STATUSES | {"moved_to_internal"}
        ):
            errors.append(
                f"internal atom {atom_id} cannot use coverage status {status!r}"
            )
        after_paths = result.get("after_paths", [])
        if status in PASSING_READER_STATUSES:
            if not isinstance(after_paths, list) or not after_paths:
                errors.append(f"covered atom {atom_id} requires after_paths")
            elif any(
                not isinstance(path, str) or not _valid_after_path(path, story)
                for path in after_paths
            ):
                errors.append(f"covered atom {atom_id} has invalid after_paths")
        if result.get("source_digest") != atom.get("source_digest"):
            errors.append(f"coverage atom {atom_id} has a stale source_digest")
        if atom.get("priority") == "P0" and status in PASSING_READER_STATUSES:
            passing_p0 += 1
        if obligation in {"reader", "boundary"} and status in PASSING_READER_STATUSES:
            passing_reader += 1

    p0_total = sum(item.get("priority") == "P0" for item in atoms)
    if p0_total and passing_p0 != p0_total:
        errors.append(f"P0 atom coverage must be 100% ({passing_p0}/{p0_total})")
    reader_ratio = passing_reader / reader_total if reader_total else 1.0
    if reader_ratio < 0.98:
        errors.append(
            f"all reader atom coverage must be at least 98% ({reader_ratio:.1%})"
        )
    if coverage.get("unsupported_claims"):
        errors.append("coverage map reports unsupported claims")
    if coverage.get("hard_failures"):
        errors.append("coverage map reports hard failures")

    if image_log.get("schema") != IMAGE_REVIEW_SCHEMA:
        errors.append(f"image review schema must be {IMAGE_REVIEW_SCHEMA}")
    reviews = [
        item for item in image_log.get("reviews", []) if isinstance(item, dict)
    ]
    reviewed_shas: set[str] = set()
    reviewed_ids: set[str] = set()
    for item in reviews:
        artifact_id = item.get("artifact_id")
        sha = item.get("sha256")
        entry = manifest_by_id.get(artifact_id)
        if not entry:
            errors.append(f"image review references unknown artifact: {artifact_id}")
            continue
        if sha != entry.get("sha256"):
            errors.append(f"image review hash mismatch: {artifact_id}")
            continue
        observation = " ".join(
            str(item.get(key) or "")
            for key in ("observed_title", "observed_state", "observation")
        ).strip()
        controls = item.get("observed_controls", [])
        if len(observation) < 6 and not controls:
            errors.append(f"image review lacks visual observations: {artifact_id}")
        reviewed_ids.add(str(artifact_id))
        if isinstance(sha, str) and sha:
            reviewed_shas.add(sha)

    required_shas = {
        item.get("sha256")
        for item in manifest_entries
        if item.get("required_review") and item.get("available") and item.get("sha256")
    }
    missing_required_images = sorted(required_shas - reviewed_shas)
    if missing_required_images:
        errors.append(
            f"required image groups were not visually reviewed: {len(missing_required_images)}"
        )
    for artifact_id in used_artifacts:
        entry = manifest_by_id.get(artifact_id)
        if entry and entry.get("sha256") not in reviewed_shas:
            errors.append(f"story image was not visually reviewed: {artifact_id}")

    metrics: dict[str, Any] = {
        **_reference_metrics(story),
        "p0_atoms": p0_total,
        "p0_atoms_covered": passing_p0,
        "reader_atom_coverage": round(reader_ratio, 4),
        "required_image_groups": len(required_shas),
        "reviewed_image_groups": len(reviewed_shas),
        "used_artifacts": len(used_artifacts),
    }
    required_numeric_facts = _required_numeric_fact_tokens(ledger)
    candidate_numeric_facts = set(metrics["numbers"])
    missing_numeric_facts = sorted(
        required_numeric_facts - candidate_numeric_facts,
        key=lambda item: (len(item), item),
    )
    numeric_recall = (
        len(required_numeric_facts & candidate_numeric_facts)
        / len(required_numeric_facts)
        if required_numeric_facts
        else 1.0
    )
    metrics["required_numeric_facts"] = sorted(required_numeric_facts)
    metrics["required_numeric_recall"] = round(numeric_recall, 4)
    metrics["missing_numeric_facts"] = missing_numeric_facts
    if missing_numeric_facts:
        errors.append(
            "reader-visible resource and legacy numbers are missing: "
            + ", ".join(missing_numeric_facts[:24])
        )

    if reference_story:
        reference = _extract_story(_read_json(reference_story.resolve()))
        reference_metrics = _reference_metrics(reference)
        metrics["reference"] = reference_metrics
        for key in (
            "concepts",
            "interface_items",
            "operation_entries",
            "story_artifacts",
        ):
            if metrics[key] < reference_metrics[key]:
                errors.append(
                    f"benchmark regression: {key} {metrics[key]} < {reference_metrics[key]}"
                )
        if metrics["sections"] < reference_metrics["sections"]:
            warnings.append(
                "candidate uses fewer sections than the reference; this is allowed "
                "when mechanisms remain complete and coherently grouped"
            )
        if metrics["text_chars"] < int(reference_metrics["text_chars"] * 0.85):
            errors.append("benchmark regression: reader story is materially thinner")
        candidate_names = {
            str(item.get("name")).strip()
            for item in concepts
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        }
        reference_names = set(reference_metrics["concept_names"])
        name_ratio = (
            len(candidate_names & reference_names) / len(reference_names)
            if reference_names
            else 1.0
        )
        metrics["reference_concept_recall"] = round(name_ratio, 4)
        if name_ratio < 0.75:
            warnings.append(
                "literal concept-name recall is low "
                f"({name_ratio:.1%}); review semantic aliases in the judge pass"
            )
        candidate_numbers = set(metrics["numbers"])
        reference_numbers = set(reference_metrics["numbers"])
        number_ratio = (
            len(candidate_numbers & reference_numbers) / len(reference_numbers)
            if reference_numbers
            else 1.0
        )
        metrics["reference_number_recall"] = round(number_ratio, 4)
        if number_ratio < 0.8:
            errors.append(
                f"benchmark regression: numeric fact recall is only {number_ratio:.1%}"
            )

    result = {
        "schema": VALIDATION_SCHEMA,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }
    _write_json(report, result)
    return result


def _decode_structured_json(
    structured: Mapping[str, Any],
    field: str,
    *,
    repairs: list[str] | None = None,
) -> dict[str, Any]:
    payload = structured.get(field)
    if isinstance(payload, dict):
        return copy.deepcopy(payload)
    if not isinstance(payload, str) or not payload.strip():
        raise ValueError(f"Terra structured output is missing {field}")
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        prefix = payload[: exc.pos]
        trailer = payload[exc.pos:]
        if exc.msg == "Extra data" and trailer == "}":
            try:
                value = json.loads(prefix)
            except json.JSONDecodeError as prefix_exc:
                raise ValueError(
                    f"Terra {field} is not valid JSON: {exc}"
                ) from prefix_exc
            if repairs is not None:
                repairs.append(
                    f"{field}: removed one redundant closing brace after "
                    "a complete JSON object"
                )
        else:
            raise ValueError(f"Terra {field} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Terra {field} must decode to an object")
    return value


def _expand_coverage_assignment(
    assignment: Mapping[str, Any],
    ledger: Mapping[str, Any],
    story: Mapping[str, Any],
) -> dict[str, Any]:
    """Expand a compact Terra assignment into the loss-gate coverage map.

    Terra only selects destinations for atom ids. The trusted parent restores
    source digests and emits one result per ledger atom, avoiding a large and
    error-prone repetition of immutable source data in model output.
    """

    if assignment.get("schema") != COVERAGE_ASSIGNMENT_SCHEMA:
        raise ValueError(
            f"coverage assignment schema must be {COVERAGE_ASSIGNMENT_SCHEMA}"
        )
    source_bundle_id = ledger.get("source_bundle_id")
    if assignment.get("source_bundle_id") != source_bundle_id:
        raise ValueError(
            "coverage assignment source_bundle_id does not match the atom ledger"
        )
    atoms = [
        item for item in ledger.get("atoms", []) if isinstance(item, dict)
    ]
    atom_by_id = {
        item.get("atom_id"): item
        for item in atoms
        if isinstance(item.get("atom_id"), str)
    }
    selected: dict[str, dict[str, Any]] = {}

    groups = assignment.get("assignments", [])
    if not isinstance(groups, list):
        raise ValueError("coverage assignment assignments must be a list")
    for index, group in enumerate(groups):
        if not isinstance(group, dict):
            raise ValueError(f"coverage assignment group {index} must be an object")
        status = group.get("status", "semantic")
        if status not in PASSING_READER_STATUSES:
            raise ValueError(
                f"coverage assignment group {index} has invalid status: {status}"
            )
        after_paths = group.get("after_paths")
        if after_paths is None:
            after_path = group.get("after_path")
            after_paths = [after_path] if isinstance(after_path, str) else []
        if isinstance(after_paths, list):
            after_paths = [
                "reader_story" if path == "reader_story_json" else path
                for path in after_paths
            ]
        if (
            not isinstance(after_paths, list)
            or not after_paths
            or any(
                not isinstance(path, str) or not _valid_after_path(path, story)
                for path in after_paths
            )
        ):
            raise ValueError(
                f"coverage assignment group {index} has invalid after_paths"
            )
        has_ids = "atom_ids" in group
        has_indexes = "atom_indexes" in group
        if has_ids == has_indexes:
            raise ValueError(
                f"coverage assignment group {index} requires exactly one of "
                "atom_ids or atom_indexes"
            )
        raw_selectors = (
            group.get("atom_ids") if has_ids else group.get("atom_indexes")
        )
        if not isinstance(raw_selectors, list) or not raw_selectors:
            raise ValueError(
                f"coverage assignment group {index} requires atom selectors"
            )
        atom_ids: list[str] = []
        if has_ids:
            atom_ids = list(raw_selectors)
        else:
            for atom_index in raw_selectors:
                if (
                    isinstance(atom_index, bool)
                    or not isinstance(atom_index, int)
                    or atom_index < 0
                    or atom_index >= len(atoms)
                ):
                    raise ValueError(
                        "coverage assignment has invalid atom index: "
                        f"{atom_index}"
                    )
                atom_ids.append(str(atoms[atom_index]["atom_id"]))
        for atom_id in atom_ids:
            if not isinstance(atom_id, str) or atom_id not in atom_by_id:
                raise ValueError(f"coverage assignment has unknown atom: {atom_id}")
            if atom_id in selected:
                raise ValueError(f"coverage assignment duplicates atom: {atom_id}")
            atom = atom_by_id[atom_id]
            if atom.get("obligation") == "internal":
                raise ValueError(
                    f"internal atom must use internal_atom_ids: {atom_id}"
                )
            result: dict[str, Any] = {
                "status": status,
                "after_paths": list(after_paths),
            }
            if isinstance(group.get("confidence"), (int, float)):
                result["confidence"] = group["confidence"]
            if isinstance(group.get("reason"), str) and group["reason"].strip():
                result["reason"] = group["reason"].strip()
            selected[atom_id] = result

    has_internal_ids = "internal_atom_ids" in assignment
    has_internal_indexes = "internal_atom_indexes" in assignment
    if has_internal_ids and has_internal_indexes:
        raise ValueError(
            "coverage assignment cannot mix internal_atom_ids and "
            "internal_atom_indexes"
        )
    raw_internal = (
        assignment.get("internal_atom_ids", [])
        if has_internal_ids
        else assignment.get("internal_atom_indexes", [])
    )
    if not isinstance(raw_internal, list):
        raise ValueError("coverage assignment internal selectors must be a list")
    internal_atom_ids: list[str] = []
    if has_internal_ids:
        internal_atom_ids = list(raw_internal)
    else:
        for atom_index in raw_internal:
            if (
                isinstance(atom_index, bool)
                or not isinstance(atom_index, int)
                or atom_index < 0
                or atom_index >= len(atoms)
            ):
                raise ValueError(
                    f"coverage assignment has invalid internal atom index: {atom_index}"
                )
            internal_atom_ids.append(str(atoms[atom_index]["atom_id"]))
    for atom_id in internal_atom_ids:
        if not isinstance(atom_id, str) or atom_id not in atom_by_id:
            raise ValueError(f"coverage assignment has unknown internal atom: {atom_id}")
        if atom_id in selected:
            raise ValueError(f"coverage assignment duplicates atom: {atom_id}")
        if atom_by_id[atom_id].get("obligation") != "internal":
            raise ValueError(
                f"reader or boundary atom cannot be marked internal: {atom_id}"
            )
        selected[atom_id] = {
            "status": "moved_to_internal",
            "after_paths": [],
        }

    missing = [atom_id for atom_id in atom_by_id if atom_id not in selected]
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(
            f"coverage assignment is missing {len(missing)} atoms: {preview}"
        )

    results: list[dict[str, Any]] = []
    for atom in atoms:
        atom_id = atom["atom_id"]
        results.append(
            {
                "atom_id": atom_id,
                "source_digest": atom.get("source_digest"),
                **selected[atom_id],
            }
        )
    return {
        "schema": COVERAGE_SCHEMA,
        "source_bundle_id": source_bundle_id,
        "atom_results": results,
        "unsupported_claims": assignment.get("unsupported_claims", []),
        "hard_failures": assignment.get("hard_failures", []),
    }


def _recoverable_transient_worker_result(result: Mapping[str, Any]) -> bool:
    """Accept a complete exit-zero result mislabeled by reconnect notices only."""

    if result.get("status") != "failed" or result.get("exit_code") != 0:
        return False
    if str(result.get("error") or "").strip():
        return False
    if not isinstance(result.get("structured_output"), dict):
        return False
    error_events = [
        item
        for item in result.get("events", [])
        if isinstance(item, dict)
        and item.get("type") in {"error", "turn.failed"}
    ]
    if not error_events or any(
        item.get("type") == "turn.failed"
        or not re.match(
            r"^\s*Reconnecting\.\.\.\s+\d+/\d+\s+\(",
            str(item.get("message") or ""),
            flags=re.IGNORECASE,
        )
        for item in error_events
    ):
        return False
    return True


def _merge_image_review_logs(
    prior_logs: Sequence[Mapping[str, Any]],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(current))
    reviews_by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    key_by_artifact_id: dict[str, str] = {}
    for log in (*prior_logs, current):
        for review in log.get("reviews", []):
            if not isinstance(review, dict):
                continue
            sha = str(review.get("sha256") or "")
            artifact_id = str(review.get("artifact_id") or "")
            key = f"sha:{sha}" if sha else f"artifact:{artifact_id}"
            prior_key = key_by_artifact_id.get(artifact_id)
            if artifact_id and prior_key and prior_key != key:
                reviews_by_key.pop(prior_key, None)
                order = [item for item in order if item != prior_key]
            if key not in reviews_by_key:
                order.append(key)
            reviews_by_key[key] = copy.deepcopy(review)
            if artifact_id:
                key_by_artifact_id[artifact_id] = key
    merged["reviews"] = [reviews_by_key[key] for key in order]
    return merged


def _merge_coverage_assignment_patches(
    base: Mapping[str, Any],
    patches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(base))
    assignments = merged.setdefault("assignments", [])
    if not isinstance(assignments, list):
        raise ValueError("base coverage assignments must be a list")
    internal_key = (
        "internal_atom_indexes"
        if "internal_atom_indexes" in merged
        else "internal_atom_ids"
    )
    internal = merged.setdefault(internal_key, [])
    if not isinstance(internal, list):
        raise ValueError("base internal atom selectors must be a list")
    for patch in patches:
        patch_source = patch.get("source_bundle_id")
        if (
            patch_source
            and patch_source != merged.get("source_bundle_id")
        ):
            raise ValueError(
                "coverage patch source_bundle_id does not match the base"
            )
        patch_assignments = patch.get("assignments", [])
        if not isinstance(patch_assignments, list):
            raise ValueError("coverage patch assignments must be a list")
        assignments.extend(copy.deepcopy(patch_assignments))
        patch_internal = patch.get(internal_key, [])
        if not isinstance(patch_internal, list):
            raise ValueError("coverage patch internal selectors must be a list")
        internal.extend(copy.deepcopy(patch_internal))
        for key in ("unsupported_claims", "hard_failures"):
            values = patch.get(key, [])
            if values:
                destination = merged.setdefault(key, [])
                if not isinstance(destination, list) or not isinstance(values, list):
                    raise ValueError(f"coverage patch {key} must be a list")
                destination.extend(copy.deepcopy(values))
    return merged


def _apply_reader_story_patches(
    story: Mapping[str, Any],
    patches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    merged = copy.deepcopy(dict(story))
    for patch in patches:
        replacement_story = patch.get("replace_story")
        if replacement_story is not None:
            if not isinstance(replacement_story, dict):
                raise ValueError(
                    "reader story patch replace_story must be an object"
                )
            merged = copy.deepcopy(replacement_story)
        sections = merged.get("sections", [])
        if not isinstance(sections, list):
            raise ValueError("base reader story sections must be a list")
        replacements = patch.get("replace_sections", [])
        if not isinstance(replacements, list):
            raise ValueError("reader story patch replace_sections must be a list")
        for replacement in replacements:
            if not isinstance(replacement, dict):
                raise ValueError("reader story replacement section must be an object")
            section_id = replacement.get("id")
            matching = [
                index
                for index, section in enumerate(sections)
                if isinstance(section, dict) and section.get("id") == section_id
            ]
            if len(matching) != 1:
                raise ValueError(
                    "reader story patch must replace exactly one existing section: "
                    f"{section_id}"
                )
            sections[matching[0]] = copy.deepcopy(replacement)
    return merged


def ingest_worker_result(
    workspace: Path,
    result_path: Path,
    *,
    prior_results: Sequence[Path] = (),
    image_patch_results: Sequence[Path] = (),
    coverage_patch_results: Sequence[Path] = (),
    story_patch_results: Sequence[Path] = (),
) -> dict[str, Any]:
    """Materialize a read-only Terra worker's structured response in the packet."""

    workspace = workspace.resolve()
    result_path = result_path.resolve()
    result = _read_json(result_path)
    recovered_transient = _recoverable_transient_worker_result(result)
    if result.get("status") != "succeeded" and not recovered_transient:
        raise ValueError(
            f"Terra worker did not succeed: {result.get('status')} {result.get('error') or ''}"
        )
    structured = result.get("structured_output")
    if not isinstance(structured, dict):
        raise ValueError("Terra worker returned no structured_output")
    serialization_repairs: list[str] = []
    story = _extract_story(
        _decode_structured_json(
            structured,
            "reader_story_json",
            repairs=serialization_repairs,
        )
    )
    resolved_story_patches: list[str] = []
    story_patches: list[dict[str, Any]] = []
    for story_patch_path in story_patch_results:
        resolved_story_patch = story_patch_path.resolve()
        story_patch_result = _read_json(resolved_story_patch)
        story_patch_structured = story_patch_result.get("structured_output")
        if not isinstance(story_patch_structured, dict):
            raise ValueError(
                "reader story patch has no structured_output: "
                f"{resolved_story_patch}"
            )
        story_patches.append(
            _decode_structured_json(
                story_patch_structured,
                "reader_story_patch",
                repairs=serialization_repairs,
            )
        )
        resolved_story_patches.append(str(resolved_story_patch))
    if story_patches:
        story = _apply_reader_story_patches(story, story_patches)
    image_log = _decode_structured_json(
        structured,
        "image_review_log_json",
        repairs=serialization_repairs,
    )
    prior_image_logs: list[dict[str, Any]] = []
    resolved_prior_results: list[str] = []
    for prior_result_path in prior_results:
        resolved_prior = prior_result_path.resolve()
        prior_result = _read_json(resolved_prior)
        prior_structured = prior_result.get("structured_output")
        if not isinstance(prior_structured, dict):
            raise ValueError(
                f"prior Terra result has no structured_output: {resolved_prior}"
            )
        prior_image_logs.append(
            _decode_structured_json(
                prior_structured,
                "image_review_log_json",
                repairs=serialization_repairs,
            )
        )
        resolved_prior_results.append(str(resolved_prior))
    if prior_image_logs:
        image_log = _merge_image_review_logs(prior_image_logs, image_log)
    resolved_image_patches: list[str] = []
    for image_patch_path in image_patch_results:
        resolved_image_patch = image_patch_path.resolve()
        image_patch_result = _read_json(resolved_image_patch)
        image_patch_structured = image_patch_result.get("structured_output")
        if not isinstance(image_patch_structured, dict):
            raise ValueError(
                "image review patch has no structured_output: "
                f"{resolved_image_patch}"
            )
        image_patch_log = _decode_structured_json(
            image_patch_structured,
            "image_review_log_json",
            repairs=serialization_repairs,
        )
        image_log = _merge_image_review_logs([image_log], image_patch_log)
        resolved_image_patches.append(str(resolved_image_patch))
    if structured.get("coverage_assignment_json"):
        assignment = _decode_structured_json(
            structured,
            "coverage_assignment_json",
            repairs=serialization_repairs,
        )
        resolved_coverage_patches: list[str] = []
        for patch_path in coverage_patch_results:
            resolved_patch = patch_path.resolve()
            patch_result = _read_json(resolved_patch)
            patch_structured = patch_result.get("structured_output")
            if not isinstance(patch_structured, dict):
                raise ValueError(
                    f"coverage patch has no structured_output: {resolved_patch}"
                )
            replacement_present = (
                "coverage_assignment_replacement_json" in patch_structured
            )
            patch_present = "coverage_assignment_patch" in patch_structured
            if replacement_present and patch_present:
                raise ValueError(
                    "coverage repair cannot contain both replacement and patch"
                )
            if replacement_present:
                replacement = _decode_structured_json(
                    patch_structured,
                    "coverage_assignment_replacement_json",
                    repairs=serialization_repairs,
                )
                if (
                    replacement.get("source_bundle_id")
                    != assignment.get("source_bundle_id")
                ):
                    raise ValueError(
                        "coverage replacement source_bundle_id does not match "
                        "the base"
                    )
                assignment = replacement
            else:
                field = (
                    "coverage_assignment_patch"
                    if patch_present
                    else "coverage_assignment_json"
                )
                patch = _decode_structured_json(
                    patch_structured,
                    field,
                    repairs=serialization_repairs,
                )
                assignment = _merge_coverage_assignment_patches(
                    assignment,
                    [patch],
                )
            resolved_coverage_patches.append(str(resolved_patch))
        ledger = _read_json(workspace / "input" / "atom-ledger.json")
        coverage = _expand_coverage_assignment(assignment, ledger, story)
    else:
        resolved_coverage_patches = []
        coverage = _decode_structured_json(
            structured,
            "coverage_map_json",
            repairs=serialization_repairs,
        )

    outputs = (
        (workspace / "output" / "reader_story.json", story),
        (workspace / "output" / "coverage-map.json", coverage),
        (workspace / "output" / "image-review-log.json", image_log),
    )
    written: list[str] = []
    for target, value in outputs:
        _write_json(target, value)
        written.append(str(target))
    receipt = {
        "schema": "game-observatory.reader-story-terra-receipt.v1",
        "source_result": str(result_path),
        "prior_results": resolved_prior_results,
        "image_patch_results": resolved_image_patches,
        "coverage_patch_results": resolved_coverage_patches,
        "story_patch_results": resolved_story_patches,
        "run_id": result.get("run_id"),
        "provider": result.get("provider"),
        "status": result.get("status"),
        "normalization": result.get("normalization"),
        "recovered_transient_reconnect": recovered_transient,
        "serialization_repairs": serialization_repairs,
        "duration_ms": result.get("duration_ms"),
        "completion": structured.get("completion"),
        "written": written,
    }
    _write_json(workspace / "output" / "terra-run-receipt.json", receipt)
    return {"ok": True, **receipt}


def merge_candidate(
    workspace: Path,
    *,
    source: Path | None = None,
    archive_dir: Path | None = None,
    allow_replace: bool = False,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    baseline = _read_json(workspace / "input" / "baseline-manifest.json")
    source = (source or Path(str(baseline["source_path"]))).resolve()
    validation = validate_workspace(workspace)
    if not validation["passed"]:
        raise ValueError("candidate validation failed; canonical merge is forbidden")
    current = _read_json(source)
    current_protected = _without_reader_story(current)
    if _json_sha256(current_protected) != baseline["protected_raw_sha256"]:
        raise ValueError("source changed after packet preparation; rebuild the packet")
    design = current.get("design_document")
    if not isinstance(design, dict):
        raise ValueError("source design_document is missing")
    if isinstance(design.get("reader_story"), dict) and not allow_replace:
        raise ValueError("source already has reader_story; use --allow-replace explicitly")

    story = _extract_story(_read_json(workspace / "output" / "reader_story.json"))
    merged = copy.deepcopy(current)
    merged["design_document"]["reader_story"] = story
    if _json_sha256(_without_reader_story(merged)) != baseline["protected_raw_sha256"]:
        raise AssertionError("merge attempted to change protected source content")
    if _all_object_ids(_without_reader_story(merged)) != _all_object_ids(current_protected):
        raise AssertionError("merge attempted to change protected object ids")

    archive_dir = (
        archive_dir
        or source.parent.parent
        / "reader-story-baselines"
        / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ).resolve()
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / source.name
    if archive_path.exists():
        raise FileExistsError(f"archive already exists: {archive_path}")
    shutil.copy2(source, archive_path)

    temporary = source.with_suffix(source.suffix + ".reader-story.tmp")
    _write_json(temporary, merged)
    if _json_sha256(_without_reader_story(_read_json(temporary))) != baseline[
        "protected_raw_sha256"
    ]:
        temporary.unlink(missing_ok=True)
        raise AssertionError("serialized merge changed protected source content")
    os.replace(temporary, source)
    return {
        "ok": True,
        "source": str(source),
        "archive": str(archive_path),
        "reader_story_sha256": _json_sha256(story),
        "protected_raw_sha256": baseline["protected_raw_sha256"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--workspace", type=Path, required=True)
    prepare.add_argument("--prompt", type=Path, required=True)
    prepare.add_argument("--standard", type=Path, required=True)
    prepare.add_argument("--example", type=Path, action="append", default=[])
    prepare.add_argument(
        "--related-source",
        type=Path,
        action="append",
        default=[],
    )

    validate = subparsers.add_parser("validate")
    validate.add_argument("--workspace", type=Path, default=Path("."))
    validate.add_argument("--candidate", type=Path)
    validate.add_argument("--coverage-map", type=Path)
    validate.add_argument("--image-review-log", type=Path)
    validate.add_argument("--reference-story", type=Path)
    validate.add_argument("--report", type=Path)

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--workspace", type=Path, required=True)
    ingest.add_argument("--result", type=Path, required=True)
    ingest.add_argument(
        "--prior-result",
        type=Path,
        action="append",
        default=[],
    )
    ingest.add_argument(
        "--coverage-patch-result",
        type=Path,
        action="append",
        default=[],
    )
    ingest.add_argument(
        "--image-patch-result",
        type=Path,
        action="append",
        default=[],
    )
    ingest.add_argument(
        "--story-patch-result",
        type=Path,
        action="append",
        default=[],
    )

    merge = subparsers.add_parser("merge")
    merge.add_argument("--workspace", type=Path, required=True)
    merge.add_argument("--source", type=Path)
    merge.add_argument("--archive-dir", type=Path)
    merge.add_argument("--allow-replace", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_workspace(
                source=args.source,
                workspace=args.workspace,
                prompt=args.prompt,
                standard=args.standard,
                examples=args.example,
                related_sources=args.related_source,
            )
        elif args.command == "validate":
            result = validate_workspace(
                args.workspace,
                candidate=args.candidate,
                coverage_map=args.coverage_map,
                image_review_log=args.image_review_log,
                reference_story=args.reference_story,
                report=args.report,
            )
        elif args.command == "ingest":
            result = ingest_worker_result(
                args.workspace,
                args.result,
                prior_results=args.prior_result,
                image_patch_results=args.image_patch_result,
                coverage_patch_results=args.coverage_patch_result,
                story_patch_results=args.story_patch_result,
            )
        else:
            result = merge_candidate(
                args.workspace,
                source=args.source,
                archive_dir=args.archive_dir,
                allow_replace=args.allow_replace,
            )
    except Exception as exc:  # CLI boundary: preserve a compact machine-readable failure.
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "validate" and not result["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
