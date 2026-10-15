"""Rebuildable current-authority projection with explicit conflict states."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class AuthorityConflictError(ValueError):
    pass


def _key(scope: dict[str, Any]) -> str:
    try:
        return json.dumps([scope[name] for name in ("object", "purpose", "scope")], ensure_ascii=False, separators=(",", ":"))
    except KeyError as exc:
        raise ValueError("authority scope needs object, purpose and scope") from exc


def _same_version(left: object, right: object) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    left_revision, right_revision = left.get("revision_ref"), right.get("revision_ref")
    return (
        left.get("material_id") == right.get("material_id")
        and isinstance(left_revision, dict)
        and isinstance(right_revision, dict)
        and all(left_revision.get(name) == right_revision.get(name) for name in ("provider", "object", "revision"))
    )


class AuthorityIndex:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "entries": {}, "history": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=True, indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(temp, self.path)

    @staticmethod
    def _value(record: dict[str, Any]) -> dict[str, Any]:
        formal = record["formalization"]
        record_ref = {"material_id": record["id"], "revision_ref": record["revision_ref"]}
        return {
            "material_ref": formal.get("input_material", record_ref),
            "record_ref": record_ref,
            "artifact_refs": formal["artifacts"],
            "occurred_at": formal.get("occurred_at"),
        }

    def would_conflict(self, formal: dict[str, Any], record: dict[str, Any]) -> bool:
        entries = self._load()["entries"]
        action = formal["authority_effect"]["action"]
        if action == "none":
            return False
        record_ref = {"material_id": record["id"], "revision_ref": record["revision_ref"]}
        input_material = formal.get("input_material", record_ref)
        for scope in formal["authority_effect"]["scopes"]:
            current = entries.get(_key(scope), {}).get("current", [])
            if action == "establish" and current:
                return True
            if action in {"replace", "withdraw"} and len(current) != 1:
                return True
            if action in {"replace", "withdraw"}:
                current_material = current[0].get("material_ref", current[0].get("record_ref"))
                replaces = formal["authority_effect"].get("replaces") or []
                if action == "replace" and (
                    len(replaces) != 1 or not _same_version(replaces[0], current_material)
                ):
                    return True
                if action == "withdraw" and not _same_version(input_material, current_material):
                    return True
                if action == "withdraw" and replaces and (
                    len(replaces) != 1 or not _same_version(replaces[0], current_material)
                ):
                    return True
                base_revision = formal.get("base_revision_ref")
                if action == "replace" and base_revision is not None and base_revision != current_material.get("revision_ref"):
                    return True
        return False

    def apply(self, record: dict[str, Any]) -> dict[str, Any]:
        formal, data, value = record["formalization"], self._load(), self._value(record)
        if formal.get("outcome") != "succeeded": return {"status": "rejected"}
        effect = formal["authority_effect"]; action = effect["action"]
        if action == "none": return {"status": "unchanged"}
        if self.would_conflict(formal, record):
            raise AuthorityConflictError("authority action does not exactly match the current Material version")
        for scope in effect["scopes"]:
            key, entry = _key(scope), data["entries"].setdefault(_key(scope), {"scope": scope, "current": []})
            current = entry["current"]
            if action == "establish":
                entry["current"] = [value]
            elif action == "replace":
                entry["current"] = [value]
            elif action == "withdraw":
                entry["current"] = []
            data["history"].append({"scope": scope, "action": action, "value": value})
        self._save(data); return {"status": "applied", "entries": data["entries"]}

    def get(self, scope: dict[str, Any], *, include_conflict: bool = False) -> dict[str, Any]:
        entry = self._load()["entries"].get(_key(scope))
        if not entry or not entry["current"]: return {"status": "absent"}
        if len(entry["current"]) != 1:
            return {"status": "conflict", "evidence": entry["current"]}
        return {"status": "current", "value": entry["current"][0]}

    def history(self, scope: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        records = self._load()["history"]
        return records if scope is None else [item for item in records if item["scope"] == scope]

    def rebuild(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """Atomically rebuild.  Invalid non-establish actions leave the old projection untouched."""
        temporary = self.path.with_suffix(self.path.suffix + ".rebuild")
        temporary.unlink(missing_ok=True)
        candidate = AuthorityIndex(temporary)
        try:
            for record in records:
                try:
                    candidate.apply(record)
                except AuthorityConflictError:
                    formal = record["formalization"]
                    if formal["authority_effect"]["action"] != "establish":
                        raise
                    # Two successful establishes are evidence of a conflict, not a reason to choose newest.
                    data = candidate._load(); value = candidate._value(record)
                    for scope in formal["authority_effect"]["scopes"]:
                        entry = data["entries"].setdefault(_key(scope), {"scope": scope, "current": []})
                        entry["current"].append(value)
                        data["history"].append({"scope": scope, "action": "establish-conflict", "value": value})
                    candidate._save(data)
            candidate_data = candidate._load()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temporary, self.path)
            return candidate_data
        finally:
            temporary.unlink(missing_ok=True)
