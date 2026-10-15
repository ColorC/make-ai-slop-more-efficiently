"""Read-only validation for formal-domain migration and retirement manifests."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

import yaml


_ALLOWED_STATUSES = {"pending", "compatibility", "retired"}
_REQUIRED_GATE_IDS = {
    "single-writer",
    "current-readback",
    "rebuild",
    "rollback",
    "history-preserved",
    "stable-link",
}


@dataclass(frozen=True)
class MigrationIssue:
    """One actionable manifest defect."""

    code: str
    message: str
    item_id: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {"code": self.code, "message": self.message}
        if self.item_id is not None:
            result["item_id"] = self.item_id
        return result


@dataclass(frozen=True)
class MigrationValidationReport:
    """Validation result plus a compact, read-only migration inventory."""

    valid: bool
    items: tuple[dict[str, Any], ...]
    issues: tuple[MigrationIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        status_counts: dict[str, int] = {}
        for item in self.items:
            status = str(item.get("status", "invalid"))
            status_counts[status] = status_counts.get(status, 0) + 1
        return {
            "valid": self.valid,
            "summary": {
                "item_count": len(self.items),
                "issue_count": len(self.issues),
                "status_counts": status_counts,
                "gate_ready_count": sum(bool(item.get("gate_ready")) for item in self.items),
            },
            "items": list(self.items),
            "issues": [issue.as_dict() for issue in self.issues],
        }


class MigrationManifestError(ValueError):
    """Raised when a migration manifest cannot be loaded."""


def load_migration_manifest(path: str | Path) -> dict[str, Any]:
    """Load JSON or YAML without mutating migration state."""

    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8-sig")
        payload = json.loads(text) if source.suffix.lower() == ".json" else yaml.safe_load(text)
    except FileNotFoundError as exc:
        raise MigrationManifestError(f"migration manifest not found: {source}") from exc
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise MigrationManifestError(f"cannot read migration manifest {source}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MigrationManifestError("migration manifest root must be an object")
    return payload


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _evidence_list(value: object) -> bool:
    return isinstance(value, list) and all(_non_empty_string(item) for item in value)


def validate_migration_manifest(payload: Mapping[str, Any]) -> MigrationValidationReport:
    """Validate a manifest; pending migration gates are valid, unsafe claims are not."""

    issues: list[MigrationIssue] = []
    summaries: list[dict[str, Any]] = []
    if payload.get("schema_version") != 1:
        issues.append(MigrationIssue("schema-version", "schema_version must be 1"))
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        issues.append(MigrationIssue("items", "items must be a non-empty array"))
        return MigrationValidationReport(False, (), tuple(issues))

    seen_ids: set[str] = set()
    authority_writers: dict[str, str] = {}
    for index, raw_item in enumerate(raw_items):
        fallback_id = f"items[{index}]"
        if not isinstance(raw_item, Mapping):
            issues.append(MigrationIssue("item-shape", "item must be an object", fallback_id))
            continue
        item_id = raw_item.get("id") if _non_empty_string(raw_item.get("id")) else fallback_id
        item_id = str(item_id)
        if item_id in seen_ids:
            issues.append(MigrationIssue("duplicate-item", f"duplicate migration id: {item_id}", item_id))
        seen_ids.add(item_id)

        for field in ("id", "title", "scope"):
            if not _non_empty_string(raw_item.get(field)):
                issues.append(MigrationIssue("required-field", f"{field} must be a non-empty string", item_id))
        status = raw_item.get("status")
        if status not in _ALLOWED_STATUSES:
            issues.append(
                MigrationIssue(
                    "status",
                    "status must be pending, compatibility or retired",
                    item_id,
                )
            )

        writers = raw_item.get("writers")
        writer_id: str | None = None
        writer_enabled = False
        if not isinstance(writers, list) or len(writers) != 1:
            issues.append(
                MigrationIssue(
                    "single-writer",
                    "writers must declare exactly one writer",
                    item_id,
                )
            )
        else:
            writer = writers[0]
            if not isinstance(writer, Mapping):
                issues.append(MigrationIssue("writer-shape", "writer must be an object", item_id))
            else:
                writer_id = writer.get("id") if _non_empty_string(writer.get("id")) else None
                if writer_id is None or not _non_empty_string(writer.get("entry")):
                    issues.append(
                        MigrationIssue(
                            "writer-fields",
                            "writer requires non-empty id and entry",
                            item_id,
                        )
                    )
                if not isinstance(writer.get("enabled"), bool):
                    issues.append(
                        MigrationIssue("writer-enabled", "writer.enabled must be boolean", item_id)
                    )
                else:
                    writer_enabled = writer["enabled"]

        legacy_entry = raw_item.get("legacy_entry")
        if not isinstance(legacy_entry, Mapping) or not _non_empty_string(legacy_entry.get("ref")):
            issues.append(
                MigrationIssue("legacy-entry", "legacy_entry.ref must be declared", item_id)
            )

        authority = raw_item.get("new_authority")
        authority_key: str | None = None
        if not isinstance(authority, Mapping):
            issues.append(MigrationIssue("new-authority", "new_authority must be an object", item_id))
        else:
            authority_key = (
                str(authority.get("authority_key"))
                if _non_empty_string(authority.get("authority_key"))
                else None
            )
            if authority_key is None or not _non_empty_string(authority.get("ref")):
                issues.append(
                    MigrationIssue(
                        "new-authority-fields",
                        "new_authority requires authority_key and ref",
                        item_id,
                    )
                )
            elif writer_id:
                prior = authority_writers.get(authority_key)
                if prior is not None and prior != writer_id:
                    issues.append(
                        MigrationIssue(
                            "authority-writer-conflict",
                            f"authority {authority_key} has multiple writers: {prior}, {writer_id}",
                            item_id,
                        )
                    )
                authority_writers[authority_key] = writer_id

        checks: dict[str, bool] = {}
        check_specs = {
            "current_readback": ("entry", "verified"),
            "rebuild": ("entry", "verified"),
            "rollback": ("entry", "rehearsed"),
            "history_retention": ("strategy", "preserved"),
            "stable_link": ("ref", "verified"),
        }
        for field, (entry_field, flag_field) in check_specs.items():
            value = raw_item.get(field)
            if not isinstance(value, Mapping):
                issues.append(MigrationIssue(field.replace("_", "-"), f"{field} must be an object", item_id))
                checks[field] = False
                continue
            if not _non_empty_string(value.get(entry_field)):
                issues.append(
                    MigrationIssue(
                        field.replace("_", "-"),
                        f"{field}.{entry_field} must be declared",
                        item_id,
                    )
                )
            flag = value.get(flag_field)
            if not isinstance(flag, bool):
                issues.append(
                    MigrationIssue(
                        field.replace("_", "-"),
                        f"{field}.{flag_field} must be boolean",
                        item_id,
                    )
                )
                checks[field] = False
            else:
                checks[field] = flag
                if flag and not _evidence_list(value.get("evidence")):
                    issues.append(
                        MigrationIssue(
                            "missing-evidence",
                            f"verified {field} requires evidence",
                            item_id,
                        )
                    )

        retirement_gate = raw_item.get("retirement_gate")
        gate_satisfied = False
        blockers: list[str] = []
        if not isinstance(retirement_gate, Mapping):
            issues.append(
                MigrationIssue("retirement-gate", "retirement_gate must be an object", item_id)
            )
        else:
            requirements = retirement_gate.get("requirements")
            raw_blockers = retirement_gate.get("blockers")
            if not isinstance(raw_blockers, list) or not all(
                _non_empty_string(value) for value in raw_blockers
            ):
                issues.append(
                    MigrationIssue(
                        "retirement-blockers",
                        "retirement_gate.blockers must be an array of non-empty strings",
                        item_id,
                    )
                )
            else:
                blockers = [str(value) for value in raw_blockers]
            requirement_values: list[bool] = []
            requirement_ids: set[str] = set()
            if not isinstance(requirements, list) or not requirements:
                issues.append(
                    MigrationIssue(
                        "retirement-requirements",
                        "retirement_gate.requirements must be non-empty",
                        item_id,
                    )
                )
            else:
                for requirement in requirements:
                    if not isinstance(requirement, Mapping):
                        issues.append(
                            MigrationIssue(
                                "retirement-requirement-shape",
                                "retirement requirement must be an object",
                                item_id,
                            )
                        )
                        requirement_values.append(False)
                        continue
                    requirement_id = requirement.get("id")
                    if not _non_empty_string(requirement_id):
                        issues.append(
                            MigrationIssue(
                                "retirement-requirement-id",
                                "retirement requirement id must be non-empty",
                                item_id,
                            )
                        )
                    else:
                        requirement_id = str(requirement_id)
                        if requirement_id in requirement_ids:
                            issues.append(
                                MigrationIssue(
                                    "duplicate-requirement",
                                    f"duplicate retirement requirement: {requirement_id}",
                                    item_id,
                                )
                            )
                        requirement_ids.add(requirement_id)
                    satisfied = requirement.get("satisfied")
                    if not isinstance(satisfied, bool):
                        issues.append(
                            MigrationIssue(
                                "retirement-requirement-value",
                                "retirement requirement satisfied must be boolean",
                                item_id,
                            )
                        )
                        requirement_values.append(False)
                    else:
                        requirement_values.append(satisfied)
                        if satisfied and not _evidence_list(requirement.get("evidence")):
                            issues.append(
                                MigrationIssue(
                                    "missing-evidence",
                                    f"satisfied requirement {requirement_id} requires evidence",
                                    item_id,
                                )
                            )
                missing_ids = sorted(_REQUIRED_GATE_IDS - requirement_ids)
                if missing_ids:
                    issues.append(
                        MigrationIssue(
                            "missing-gate-requirement",
                            "retirement gate misses: " + ", ".join(missing_ids),
                            item_id,
                        )
                    )
            gate_satisfied = (
                not blockers
                and writer_enabled
                and not (_REQUIRED_GATE_IDS - requirement_ids)
                and bool(requirement_values)
                and all(requirement_values)
                and all(checks.values())
            )

        if status == "retired" and not gate_satisfied:
            issues.append(
                MigrationIssue(
                    "unsafe-retirement",
                    "status retired is forbidden until every retirement gate is satisfied",
                    item_id,
                )
            )

        summaries.append(
            {
                "id": item_id,
                "title": raw_item.get("title"),
                "scope": raw_item.get("scope"),
                "status": status,
                "gate_ready": gate_satisfied,
                "writer": writer_id,
                "writer_enabled": writer_enabled,
                "new_authority": authority_key,
                "stable_link": (
                    raw_item.get("stable_link", {}).get("ref")
                    if isinstance(raw_item.get("stable_link"), Mapping)
                    else None
                ),
                "blockers": blockers,
            }
        )

    return MigrationValidationReport(not issues, tuple(summaries), tuple(issues))


def check_migration_manifest(path: str | Path) -> MigrationValidationReport:
    """Load and validate one manifest."""

    return validate_migration_manifest(load_migration_manifest(path))


__all__ = [
    "MigrationIssue",
    "MigrationManifestError",
    "MigrationValidationReport",
    "check_migration_manifest",
    "load_migration_manifest",
    "validate_migration_manifest",
]
