"""Read-only legacy text audit and explicit correction-overlay registration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..models import utc_now
from .store import AIPlayerStore
from .text_integrity import (
    CanonicalTextCorrectionV1,
    UNRECOVERABLE_TEXT_PLACEHOLDER,
    canonical_text_issue,
    canonical_text_sha256,
    recover_latin1_utf8,
)


@dataclass(frozen=True, slots=True)
class CanonicalTextFinding:
    source_table: str
    record_key: dict[str, str | int]
    source_column: str
    field_path: str
    original_text: str
    issue: str


def _walk_text(value: Any, path: str = "$") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, str):
        if canonical_text_issue(value) is not None:
            found.append((path, value))
    elif isinstance(value, dict):
        for key, child in value.items():
            found.extend(_walk_text(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_text(child, f"{path}[{index}]"))
    return found


def audit_canonical_text(player: AIPlayerStore) -> list[CanonicalTextFinding]:
    """Enumerate damaged strings without mutating or normalizing source records."""

    findings: list[CanonicalTextFinding] = []
    with player._connection() as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'ai_player_%' ORDER BY name"
        ).fetchall()
        for table_row in tables:
            table = str(table_row["name"])
            if table in {"ai_player_schema_version", "ai_player_text_corrections"}:
                continue
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            primary_keys = [
                str(row["name"])
                for row in sorted(
                    (row for row in columns if int(row["pk"]) > 0),
                    key=lambda row: int(row["pk"]),
                )
            ]
            text_columns = [
                str(row["name"])
                for row in columns
                if str(row["type"] or "").upper() == "TEXT"
            ]
            for row in connection.execute(f'SELECT * FROM "{table}"'):
                record_key = {key: row[key] for key in primary_keys}
                for column in text_columns:
                    raw_value = row[column]
                    if not isinstance(raw_value, str):
                        continue
                    value: Any = raw_value
                    if column.endswith("_json"):
                        try:
                            value = json.loads(raw_value)
                        except (TypeError, ValueError):
                            value = raw_value
                    for field_path, damaged_text in _walk_text(value):
                        issue = canonical_text_issue(damaged_text)
                        if issue is None:
                            continue
                        findings.append(
                            CanonicalTextFinding(
                                source_table=table,
                                record_key=record_key,
                                source_column=column,
                                field_path=field_path,
                                original_text=damaged_text,
                                issue=issue,
                            )
                        )
    return findings


def canonical_source_table_snapshot(player: AIPlayerStore) -> dict[str, dict[str, Any]]:
    """Fingerprint every immutable/source table, excluding schema and overlays."""

    snapshot: dict[str, dict[str, Any]] = {}
    with player._connection() as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name LIKE 'ai_player_%' ORDER BY name"
        ).fetchall()
        for table_row in tables:
            table = str(table_row["name"])
            if table in {"ai_player_schema_version", "ai_player_text_corrections"}:
                continue
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            column_names = [str(row["name"]) for row in columns]
            primary_keys = [
                str(row["name"])
                for row in sorted(
                    (row for row in columns if int(row["pk"]) > 0),
                    key=lambda row: int(row["pk"]),
                )
            ]
            order_by = ", ".join(f'"{column}"' for column in primary_keys or column_names)
            digest = hashlib.sha256()
            row_count = 0
            for row in connection.execute(
                f'SELECT * FROM "{table}" ORDER BY {order_by}'
            ):
                canonical_row = json.dumps(
                    {column: row[column] for column in column_names},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                digest.update(len(canonical_row).to_bytes(8, "big"))
                digest.update(canonical_row)
                row_count += 1
            snapshot[table] = {
                "row_count": row_count,
                "sha256": digest.hexdigest(),
            }
    return snapshot


def correction_for_finding(
    finding: CanonicalTextFinding,
    *,
    created_at: str,
    created_by: str,
) -> CanonicalTextCorrectionV1:
    recovered = recover_latin1_utf8(finding.original_text)
    if recovered is not None:
        status = "recovered"
        projected_text = recovered
        diagnosis = (
            "UTF-8 bytes were decoded as Latin-1; exact byte round-trip and "
            "readability checks passed"
        )
    else:
        status = "unrecoverable"
        projected_text = UNRECOVERABLE_TEXT_PLACEHOLDER
        diagnosis = (
            "source bytes no longer preserve the original text; immutable raw record retained"
        )
    source_identity = json.dumps(
        {
            "source_table": finding.source_table,
            "record_key": finding.record_key,
            "source_column": finding.source_column,
            "field_path": finding.field_path,
            "original_sha256": canonical_text_sha256(finding.original_text),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    correction_id = "text-correction." + hashlib.sha256(
        source_identity.encode("utf-8")
    ).hexdigest()[:32]
    return CanonicalTextCorrectionV1(
        id=correction_id,
        source_table=finding.source_table,
        record_key=finding.record_key,
        source_column=finding.source_column,
        field_path=finding.field_path,
        original_sha256=canonical_text_sha256(finding.original_text),
        status=status,
        projected_text=projected_text,
        diagnosis=diagnosis,
        created_by=created_by,
        created_at=created_at,
    )


def register_canonical_text_corrections(
    player: AIPlayerStore,
    *,
    created_by: str = "canonical-text-integrity-audit.v1",
    created_at: str | None = None,
) -> list[CanonicalTextCorrectionV1]:
    """Append deterministic overlays for every currently damaged legacy field."""

    timestamp = created_at or utc_now()
    corrections = [
        correction_for_finding(
            finding,
            created_at=timestamp,
            created_by=created_by,
        )
        for finding in audit_canonical_text(player)
    ]
    return [player.append_text_correction(correction) for correction in corrections]
