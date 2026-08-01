# [OMNI] origin=claude-code created_by=omni-recover intent=store-content-addressed-evidence-index
"""Content-addressed normalized index for provider adapter output."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable

from .evidence import EvidenceRecord
from .providers import ProviderRegistry


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    os.replace(temporary, path)


def collect_evidence(
    registry: ProviderRegistry,
    sources: Iterable[tuple[str, Path]],
    *,
    session_filters: dict[str, set[str]] | None = None,
    path_prefixes: Iterable[Path] = (),
) -> list[EvidenceRecord]:
    records: dict[str, EvidenceRecord] = {}
    normalized_prefixes = [str(path.resolve(strict=False)).replace("\\", "/").casefold() for path in path_prefixes]
    for provider, source in sources:
        adapter = registry.adapters.get(provider)
        if adapter is None:
            raise ValueError(f"provider has no parser adapter: {provider}")
        for record in adapter.iter_evidence(source.resolve(strict=False)):
            allowed_sessions = (session_filters or {}).get(provider)
            if allowed_sessions is not None and record.session_id not in allowed_sessions:
                continue
            if normalized_prefixes:
                normalized_path = (record.path or "").replace("\\", "/").casefold()
                if not normalized_path or not any(
                    normalized_path == prefix or normalized_path.startswith(prefix.rstrip("/") + "/")
                    for prefix in normalized_prefixes
                ):
                    continue
            previous = records.get(record.evidence_id)
            if previous is not None and previous != record:
                raise ValueError(f"evidence id collision: {record.evidence_id}")
            records[record.evidence_id] = record
    return sorted(records.values(), key=lambda row: (
        row.timestamp, row.provider, row.session_id, row.sequence, row.evidence_id
    ))


def write_index(records: Iterable[EvidenceRecord], output_dir: Path) -> dict[str, object]:
    """Write metadata JSONL plus deduplicated exact-byte blobs."""

    output_dir.mkdir(parents=True, exist_ok=False)
    lines: list[bytes] = []
    providers: set[str] = set()
    blob_count = 0
    blob_bytes = 0
    seen_blobs: set[str] = set()
    for record in records:
        providers.add(record.provider)
        row = record.as_json()
        encoded = row.pop("content_b64", None)
        if encoded is not None:
            body = base64.b64decode(encoded, validate=True)
            digest = record.content_sha256
            if digest is None or _sha256(body) != digest:
                raise ValueError(f"record byte hash mismatch: {record.evidence_id}")
            blob = output_dir / "objects" / "sha256" / digest[:2] / digest
            if not blob.exists():
                _atomic_write(blob, body)
            if digest not in seen_blobs:
                seen_blobs.add(digest)
                blob_count += 1
                blob_bytes += len(body)
            row["content_blob"] = str(blob.relative_to(output_dir)).replace("\\", "/")
        lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")
    index_bytes = b"".join(lines)
    index_path = output_dir / "evidence.jsonl"
    _atomic_write(index_path, index_bytes)
    manifest = {
        "schema_version": 1,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "record_count": len(lines),
        "providers": sorted(providers),
        "blob_count": blob_count,
        "blob_bytes": blob_bytes,
        "index_sha256": _sha256(index_bytes),
        "contains_raw_workspace_bytes": blob_count > 0,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _atomic_write(output_dir / "manifest.json", manifest_bytes)
    return manifest


def load_index(index_dir: Path) -> list[EvidenceRecord]:
    manifest = json.loads((index_dir / "manifest.json").read_text(encoding="utf-8"))
    index_bytes = (index_dir / "evidence.jsonl").read_bytes()
    if _sha256(index_bytes) != manifest["index_sha256"]:
        raise ValueError("normalized evidence index hash mismatch")
    records: list[EvidenceRecord] = []
    for line in index_bytes.splitlines():
        if not line:
            continue
        row = json.loads(line)
        blob_name = row.pop("content_blob", None)
        if blob_name is not None:
            blob = (index_dir / blob_name).resolve(strict=False)
            try:
                blob.relative_to(index_dir.resolve(strict=False))
            except ValueError as exc:
                raise ValueError(f"invalid evidence blob pointer: {blob_name}") from exc
            if not blob.is_file():
                raise ValueError(f"invalid evidence blob pointer: {blob_name}")
            body = blob.read_bytes()
            if _sha256(body) != row.get("content_sha256") or len(body) != row.get("content_length"):
                raise ValueError(f"evidence blob verification failed: {blob_name}")
            row["content_b64"] = base64.b64encode(body).decode("ascii")
        records.append(EvidenceRecord.from_json(row))
    if len(records) != manifest["record_count"]:
        raise ValueError("normalized evidence record count mismatch")
    return records
