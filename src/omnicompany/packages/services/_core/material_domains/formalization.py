"""Append-only, atomic storage for formalization Material Graphs."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..material_core import assert_material_graph


class FormalizationConflictError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FormalizationStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _records(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.directory.exists():
            return []
        records = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                records.append((path, json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError) as exc:
                raise ValueError(f"corrupt formalization record: {path}") from exc
        return records

    @staticmethod
    def _record_material(graph: dict[str, Any]) -> dict[str, Any]:
        candidates = [item for item in graph["materials"] if isinstance(item.get("formalization"), dict)]
        if len(candidates) != 1:
            raise ValueError("formalization graph must contain exactly one formalization material")
        return candidates[0]

    def record(self, graph: object) -> dict[str, Any]:
        graph = assert_material_graph(graph)
        material = self._record_material(graph)
        formalization = material["formalization"]
        key = formalization["idempotency_key"]
        fingerprint = hashlib.sha256(_canonical(graph)).hexdigest()
        for path, prior in self._records():
            prior_material = self._record_material(prior)
            if prior_material["formalization"]["idempotency_key"] == key:
                if hashlib.sha256(_canonical(prior)).hexdigest() == fingerprint:
                    return {"created": False, "path": str(path), "graph": prior}
                raise FormalizationConflictError(f"idempotency key has different content: {key}")
        encoded = base64.urlsafe_b64encode(material["id"].encode("utf-8")).decode("ascii").rstrip("=")
        path = self.directory / f"{encoded}.json"
        if path.exists():
            raise FormalizationConflictError(f"formalization record already exists: {material['id']}")
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return {"created": True, "path": str(path), "graph": graph}

    def find_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        for path, graph in self._records():
            material = self._record_material(graph)
            if material["formalization"]["idempotency_key"] == key:
                return {"path": str(path), "graph": graph}
        return None

    def list(self) -> list[dict[str, Any]]:
        records = [graph for _, graph in self._records()]
        return sorted(records, key=lambda graph: self._record_material(graph)["formalization"]["occurred_at"])
