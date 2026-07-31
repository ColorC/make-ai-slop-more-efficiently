"""Canonical AI-player text integrity, recovery, and public projection helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


UNRECOVERABLE_TEXT_PLACEHOLDER = "历史规范文本已损坏，原始记录保留且无法恢复"
HIDDEN_SOURCE_TEXT_PLACEHOLDER = "原始来源文本编码异常，已从规范投影隐藏"

_C1_PATTERN = re.compile(r"[\u0080-\u009f]")
SEMANTIC_FIELD_NAMES = frozenset({
    "blocked_reason",
    "description",
    "error",
    "expected_change",
    "failure",
    "forbidden_pattern",
    "guard",
    "instruction",
    "intent",
    "known_external_side_effects",
    "note",
    "objective",
    "observed_change",
    "quality_issues",
    "reactivation_condition",
    "reason",
    "recovery",
    "required_pattern",
    "result",
    "result_summary",
    "safe_stop_reason",
    "selection_reason",
    "subgoal_stack",
    "summary",
    "target_name",
    "title",
})
RAW_SOURCE_FIELD_NAMES = frozenset({
    "extracted_text",
    "ocr_text",
    "raw_ocr",
    "raw_text",
    "source_text",
    "transcript",
})
DEGRADED_ENCODING_HEALTH = frozenset({
    "corrupt",
    "degraded",
    "source_preserved",
    "unknown",
})


def canonical_text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_text_issue(value: str) -> Literal["literal_???", "U+FFFD", "C1"] | None:
    if "\ufffd" in value:
        return "U+FFFD"
    if _C1_PATTERN.search(value):
        return "C1"
    if "???" in value:
        return "literal_???"
    return None


class CanonicalTextIntegrityError(ValueError):
    """Raised before a damaged semantic string reaches canonical storage."""

    def __init__(self, code: str, path: str, message: str) -> None:
        super().__init__(f"{code} at {path}: {message}")
        self.code = code
        self.path = path


def _is_degraded_source(parent: dict[str, Any]) -> bool:
    health = parent.get("encoding_health")
    if isinstance(health, str):
        return health.casefold() in DEGRADED_ENCODING_HEALTH
    if isinstance(health, dict):
        status = health.get("status")
        return isinstance(status, str) and status.casefold() in DEGRADED_ENCODING_HEALTH
    return False


def validate_canonical_text_payload(
    value: Any,
    *,
    root_field: str = "$",
) -> None:
    """Reject damaged semantic prose while preserving marked raw-source evidence."""

    def visit(
        item: Any,
        *,
        path: str,
        semantic: bool,
        parent: dict[str, Any] | None,
        field_name: str | None,
    ) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                child_semantic = semantic or key in SEMANTIC_FIELD_NAMES
                visit(
                    child,
                    path=f"{path}.{key}",
                    semantic=child_semantic,
                    parent=item,
                    field_name=key,
                )
            return
        if isinstance(item, (list, tuple)):
            for index, child in enumerate(item):
                visit(
                    child,
                    path=f"{path}[{index}]",
                    semantic=semantic,
                    parent=parent,
                    field_name=field_name,
                )
            return
        if not isinstance(item, str):
            return
        issue = canonical_text_issue(item)
        if issue is None:
            return
        if field_name in RAW_SOURCE_FIELD_NAMES:
            if parent is not None and _is_degraded_source(parent):
                return
            raise CanonicalTextIntegrityError(
                "raw_source_encoding_health_required",
                path,
                "damaged source text must carry an explicit degraded encoding_health marker",
            )
        if semantic:
            raise CanonicalTextIntegrityError(
                "canonical_semantic_text_damaged",
                path,
                f"semantic text contains {issue}",
            )

    root_name = root_field.rsplit(".", 1)[-1]
    visit(
        value,
        path=root_field,
        semantic=root_name in SEMANTIC_FIELD_NAMES,
        parent=None,
        field_name=root_name,
    )


def sqlite_text_is_valid(value: Any, root_field: str) -> int:
    """SQLite scalar used by fail-closed INSERT/UPDATE triggers."""

    if value is None:
        return 1
    parsed = value
    if isinstance(value, str) and root_field.endswith("_json"):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return 0
    try:
        validate_canonical_text_payload(parsed, root_field=f"$.{root_field}")
    except CanonicalTextIntegrityError:
        return 0
    return 1


def recover_latin1_utf8(value: str) -> str | None:
    """Recover one mojibake string only when byte round-trip and readability agree."""

    if not _C1_PATTERN.search(value):
        return None
    try:
        original_bytes = value.encode("latin-1")
        recovered = original_bytes.decode("utf-8")
        round_tripped = recovered.encode("utf-8").decode("latin-1")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return None
    if round_tripped != value or canonical_text_issue(recovered) is not None:
        return None
    if not recovered or not any(character.isalnum() for character in recovered):
        return None
    printable = sum(character.isprintable() or character.isspace() for character in recovered)
    if printable / len(recovered) < 0.98:
        return None
    return recovered


class CanonicalTextCorrectionV1(BaseModel):
    """Append-only correction or explicit loss marker bound to immutable source bytes."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_id: Literal["game-observatory.ai-player.text-correction.v1"] = Field(
        default="game-observatory.ai-player.text-correction.v1",
        alias="schema",
    )
    id: str = Field(min_length=1)
    source_table: str = Field(
        pattern=r"^(?:ai_player_[a-z0-9_]+|evidence_(?:runs|steps))$"
    )
    record_key: dict[str, str | int]
    source_column: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    field_path: str = Field(pattern=r"^\$(?:\.[A-Za-z0-9_]+|\[\d+\])*$")
    original_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["recovered", "reconstructed", "unrecoverable"]
    projected_text: str = Field(min_length=1)
    diagnosis: str = Field(min_length=1)
    basis_reference_ids: list[str] = Field(default_factory=list)
    created_by: str = Field(min_length=1)
    created_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_projection(self) -> CanonicalTextCorrectionV1:
        if canonical_text_issue(self.projected_text) is not None:
            raise ValueError("projected_text must be clean canonical text")
        if self.status == "unrecoverable" and self.projected_text != UNRECOVERABLE_TEXT_PLACEHOLDER:
            raise ValueError("unrecoverable corrections must use the explicit loss placeholder")
        if self.status == "reconstructed" and not self.basis_reference_ids:
            raise ValueError("reconstructed corrections require evidence basis references")
        if self.status == "reconstructed" and self.projected_text in {
            UNRECOVERABLE_TEXT_PLACEHOLDER,
            HIDDEN_SOURCE_TEXT_PLACEHOLDER,
        }:
            raise ValueError("reconstructed corrections must provide concrete semantics")
        if any(not reference_id.strip() for reference_id in self.basis_reference_ids):
            raise ValueError("correction evidence basis references must be non-empty")
        return self


@dataclass(frozen=True, slots=True)
class TextProjectionResult:
    payload: Any
    applied_correction_ids: tuple[str, ...]
    unrecoverable_count: int
    unregistered_damage_count: int
    hidden_source_count: int
    unrecoverable_correction_ids: tuple[str, ...] = ()
    unregistered_damage_keys: tuple[str, ...] = ()
    hidden_source_keys: tuple[str, ...] = ()

    def health(self) -> dict[str, Any]:
        return {
            "status": (
                "degraded"
                if self.unrecoverable_count or self.unregistered_damage_count
                else "healthy"
            ),
            "applied_correction_ids": list(self.applied_correction_ids),
            "unrecoverable_count": self.unrecoverable_count,
            "unregistered_damage_count": self.unregistered_damage_count,
            "hidden_source_count": self.hidden_source_count,
        }


def value_at_json_path(payload: Any, field_path: str) -> Any:
    """Resolve the constrained JSONPath form persisted by the correction ledger."""

    if field_path == "$":
        return payload
    current = payload
    for name, index in re.findall(r"\.([A-Za-z0-9_]+)|\[(\d+)\]", field_path[1:]):
        if name:
            if not isinstance(current, dict) or name not in current:
                raise KeyError(field_path)
            current = current[name]
        else:
            if not isinstance(current, list):
                raise KeyError(field_path)
            current = current[int(index)]
    return current


def project_current_text(
    payload: Any,
    corrections: list[CanonicalTextCorrectionV1] | None = None,
) -> TextProjectionResult:
    """Fail-safe a context-free payload without ever applying an ambiguous overlay."""

    del corrections
    applied: set[str] = set()
    unregistered_keys: set[str] = set()
    hidden_source_keys: set[str] = set()

    def visit(
        item: Any,
        *,
        path: str,
        field_name: str | None = None,
    ) -> Any:
        if isinstance(item, dict):
            return {
                key: visit(value, path=f"{path}.{key}", field_name=key)
                for key, value in item.items()
            }
        if isinstance(item, list):
            return [
                visit(value, path=f"{path}[{index}]", field_name=field_name)
                for index, value in enumerate(item)
            ]
        if isinstance(item, tuple):
            return [
                visit(value, path=f"{path}[{index}]", field_name=field_name)
                for index, value in enumerate(item)
            ]
        if not isinstance(item, str) or canonical_text_issue(item) is None:
            return item
        if field_name in RAW_SOURCE_FIELD_NAMES:
            hidden_source_keys.add(f"{path}:{canonical_text_sha256(item)}")
            return HIDDEN_SOURCE_TEXT_PLACEHOLDER
        unregistered_keys.add(f"{path}:{canonical_text_sha256(item)}")
        return UNRECOVERABLE_TEXT_PLACEHOLDER

    projected = visit(payload, path="$")
    return TextProjectionResult(
        payload=projected,
        applied_correction_ids=tuple(sorted(applied)),
        unrecoverable_count=0,
        unregistered_damage_count=len(unregistered_keys),
        hidden_source_count=len(hidden_source_keys),
        unregistered_damage_keys=tuple(sorted(unregistered_keys)),
        hidden_source_keys=tuple(sorted(hidden_source_keys)),
    )


def canonical_record_key_json(record_key: dict[str, str | int]) -> str:
    return json.dumps(
        record_key,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def project_canonical_record_text(
    payload: Any,
    corrections: list[CanonicalTextCorrectionV1],
    *,
    source_table: str,
    record_key: dict[str, str | int],
    source_column: str,
) -> TextProjectionResult:
    """Apply only overlays matching the exact record, column, JSONPath, and raw hash."""

    canonical_key = canonical_record_key_json(record_key)
    latest_by_binding: dict[tuple[str, str], CanonicalTextCorrectionV1] = {}
    for correction in sorted(corrections, key=lambda item: (item.created_at, item.id)):
        if correction.source_table != source_table:
            continue
        if canonical_record_key_json(correction.record_key) != canonical_key:
            continue
        if correction.source_column != source_column:
            continue
        latest_by_binding[(correction.field_path, correction.original_sha256)] = correction
    applied: set[str] = set()
    unrecoverable_correction_ids: set[str] = set()
    unregistered_keys: set[str] = set()
    hidden_source_keys: set[str] = set()

    def visit(item: Any, *, path: str, field_name: str | None = None) -> Any:
        if isinstance(item, dict):
            return {
                key: visit(value, path=f"{path}.{key}", field_name=key)
                for key, value in item.items()
            }
        if isinstance(item, list):
            return [
                visit(value, path=f"{path}[{index}]", field_name=field_name)
                for index, value in enumerate(item)
            ]
        if not isinstance(item, str) or canonical_text_issue(item) is None:
            return item
        if field_name in RAW_SOURCE_FIELD_NAMES:
            hidden_source_keys.add(
                f"{source_table}:{canonical_key}:{source_column}:{path}:"
                f"{canonical_text_sha256(item)}"
            )
            return HIDDEN_SOURCE_TEXT_PLACEHOLDER
        correction = latest_by_binding.get((path, canonical_text_sha256(item)))
        if correction is None:
            unregistered_keys.add(
                f"{source_table}:{canonical_key}:{source_column}:{path}:"
                f"{canonical_text_sha256(item)}"
            )
            return UNRECOVERABLE_TEXT_PLACEHOLDER
        applied.add(correction.id)
        if correction.status == "unrecoverable":
            unrecoverable_correction_ids.add(correction.id)
        return correction.projected_text

    projected = visit(payload, path="$")
    return TextProjectionResult(
        payload=projected,
        applied_correction_ids=tuple(sorted(applied)),
        unrecoverable_count=len(unrecoverable_correction_ids),
        unregistered_damage_count=len(unregistered_keys),
        hidden_source_count=len(hidden_source_keys),
        unrecoverable_correction_ids=tuple(sorted(unrecoverable_correction_ids)),
        unregistered_damage_keys=tuple(sorted(unregistered_keys)),
        hidden_source_keys=tuple(sorted(hidden_source_keys)),
    )
