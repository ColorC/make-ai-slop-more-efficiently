# [OMNI] origin=codex created_by=codex intent="compile fail-closed tournament training evidence without starting matches"
"""Offline, append-only Arknights training evidence control plane.

This module accepts only caller-supplied facts and deliberately never imports a
facility, device, or runner implementation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

from omnicompany.packages.domains.game_observatory.media_validation import artifact_file_issues
from omnicompany.packages.domains.game_observatory.models import ArtifactRef

JOURNAL_SCHEMA = "omnicompany.arknights.training-evidence-journal.v1"
CERTIFICATE_SCHEMA = "omnicompany.arknights.training-death-certificate.v1"
DOCTRINE_SCHEMA = "omnicompany.arknights.training-doctrine.v1"
IDENTITY_FIELDS = ("run_id", "slot_id", "theme_id")
DOCTRINE_FIELDS = frozenset({
    "schema",
    "version",
    "slot_id",
    "rule",
    "source_certificate_hash",
    "source_identity",
    "prev_hash",
    "hash",
})
KINDS = frozenset({"hud", "battle_boundary", "squad", "action", "facility", "evidence_fault", "floor", "settlement", "ledger", "recording", "runner_exit"})
HUD_FIELDS = frozenset({"loaded", "battle_state", "battle_result", "speed_level", "real_play_time", "paused", "life_ceiling", "remaining_life", "current_cost", "minimum_cost", "maximum_cost", "cost_progress", "cost_period_seconds", "cost_remaining_seconds", "cost_locked", "negative_recovery_multiplier", "spawned_enemies", "spawned_waves", "killed_enemies", "valid_killed_enemies", "leaked_enemies", "total_enemies"})


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value) if not isinstance(value, bytes) else value).hexdigest()


def _identity(value: Mapping[str, Any]) -> dict[str, str]:
    identity = value.get("identity", value)
    if not isinstance(identity, Mapping):
        raise ValueError("identity must be an object")
    result = {key: str(identity.get(key, "")).strip() for key in IDENTITY_FIELDS}
    if not all(result.values()):
        raise ValueError("run_id, slot_id, and theme_id are mandatory")
    return result


def _same_identity(value: Mapping[str, Any], identity: Mapping[str, Any], label: str) -> None:
    if _identity(value) != _identity(identity):
        raise ValueError(f"{label} identity must exactly match the journal run, slot, and theme")


def _validate_payload(kind: str, payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    if kind == "hud":
        fields = payload.get("fields", payload)
        if not isinstance(fields, Mapping) or not fields or not set(fields).issubset(HUD_FIELDS):
            raise ValueError("HUD must contain only exact resident-memory summary fields (no addresses)")
        if "remaining_life" not in fields or "leaked_enemies" not in fields:
            raise ValueError("HUD requires remaining_life and leaked_enemies")
    elif kind == "battle_boundary":
        fields = payload.get("fields")
        if payload.get("phase") not in {"start", "end"} or not isinstance(payload.get("battle_id"), str) or not payload["battle_id"].strip():
            raise ValueError("battle boundary requires a non-empty battle_id and start or end phase")
        if not isinstance(fields, Mapping) or set(fields) != {"remaining_life", "leaked_enemies"}:
            raise ValueError("battle boundary requires exactly evidenced remaining_life and leaked_enemies HUD fields")
        if not all(isinstance(fields[key], int) and fields[key] >= 0 for key in fields):
            raise ValueError("battle boundary HUD values must be non-negative integers")
    elif kind == "squad" and not payload.get("members"):
        raise ValueError("squad update requires evidenced members")
    elif kind == "action":
        if payload.get("verified_success") is not True or not str(payload.get("semantic_action", "")).strip():
            raise ValueError("action receipt requires semantic_action and verified_success boolean")
        identity = tuple(
            str(payload.get(key) or "").strip()
            for key in ("exploration_id", "intent_id", "evidence_receipt")
        )
        if not all(identity):
            raise ValueError("action receipt requires exploration, intent, and evidence receipt")
        for name in ("evidence_binding", "effect_proof", "safety_channel"):
            value = payload.get(name)
            if not isinstance(value, Mapping) or tuple(
                str(value.get(key) or "").strip()
                for key in ("exploration_id", "intent_id", "evidence_receipt")
            ) != identity:
                raise ValueError(f"action receipt {name} crosses its canonical binding")
        effect = payload["effect_proof"]
        if (
            not str(effect.get("kind") or "").strip()
            or not isinstance(effect.get("expected"), Mapping)
            or not isinstance(effect.get("actual"), Mapping)
        ):
            raise ValueError("action receipt requires structured expected/actual effect proof")
        duration = payload.get("facility_duration_ms")
        if (
            not isinstance(duration, (int, float))
            or isinstance(duration, bool)
            or float(duration) < 0
        ):
            raise ValueError("action receipt requires measured facility duration")
        safety = payload.get("safety_channel", {})
        if (
            not isinstance(safety, Mapping)
            or safety.get("available") is not True
            or safety.get("fresh") is not True
            or safety.get("input_sent") is not True
            or not str(safety.get("source") or "").strip()
            or not str(safety.get("captured_at") or "").strip()
        ):
            raise ValueError("input action requires explicit safety-channel availability and freshness")
    elif kind == "evidence_fault":
        if not str(payload.get("source", "")).strip() or not str(
            payload.get("reason", "")
        ).strip():
            raise ValueError("evidence fault requires source and reason")
    elif kind == "facility" and str(payload.get("event", "")) not in {"start", "stop", "fault", "restart", "healthy"}:
        raise ValueError("facility event is invalid")
    elif kind == "floor" and (not isinstance(payload.get("floor"), int) or payload["floor"] < 0):
        raise ValueError("floor requires a non-negative integer")
    elif kind in {"settlement", "ledger"}:
        if payload.get("outcome") not in {"clear", "loss"}:
            raise ValueError(f"{kind} outcome requires clear or loss")
        _identity(payload)
    elif kind == "recording":
        required = {"path", "sha256", "segment", "segment_count", "complete", "identity"}
        if not required.issubset(payload) or not isinstance(payload.get("segment"), int) or payload["segment"] < 1 or not isinstance(payload.get("segment_count"), int) or payload["segment_count"] < 1:
            raise ValueError("recording requires positive segment, segment_count, identity, path, hash, and complete")
        if payload["segment"] > payload["segment_count"] or not isinstance(payload["complete"], bool) or not re.fullmatch(r"[0-9a-f]{64}", str(payload["sha256"])):
            raise ValueError("recording segment/count/complete/SHA-256 is invalid")
        _identity(payload)
    elif kind == "runner_exit" and payload.get("normal") not in {True, False}:
        raise ValueError("runner_exit requires normal boolean")


def _row_hash(row: Mapping[str, Any]) -> str:
    return sha256({key: value for key, value in row.items() if key != "hash"})


class EvidenceJournal:
    """A durable line-oriented chain; exact completed replay is idempotent."""
    def __init__(self, path: Path | str, identity: Mapping[str, Any]):
        self.path = Path(path)
        self.identity = _identity(identity)

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        result: list[dict[str, Any]] = []
        previous = ""
        for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("schema") != JOURNAL_SCHEMA or _identity(row) != self.identity:
                    raise ValueError("schema or identity mismatch")
                if row.get("seq") != line_number or row.get("prev_hash") != previous or row.get("hash") != _row_hash(row):
                    raise ValueError("sequence or hash chain mismatch")
                if row.get("kind") not in KINDS or not isinstance(row.get("timestamp"), str):
                    raise ValueError("invalid journal row")
                _validate_payload(row["kind"], row["payload"])
                if row["kind"] in {"settlement", "ledger", "recording"}:
                    _same_identity(row["payload"], self.identity, row["kind"])
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid evidence journal row {line_number}: {exc}") from exc
            result.append(row)
            previous = row["hash"]
        return result

    def append(self, kind: str, payload: Mapping[str, Any], timestamp: str) -> dict[str, Any]:
        if kind not in KINDS or not isinstance(timestamp, str) or not timestamp.strip():
            raise ValueError("kind and timestamp are mandatory")
        _validate_payload(kind, payload)
        if kind in {"settlement", "ledger", "recording"}:
            _same_identity(payload, self.identity, kind)
        rows = self.rows()
        same_key = [row for row in rows if row["kind"] == kind and row["timestamp"] == timestamp]
        if same_key:
            if same_key[0]["payload"] == dict(payload):
                return same_key[0]
            raise ValueError("conflicting replay for existing evidence row")
        row = {"schema": JOURNAL_SCHEMA, "seq": len(rows) + 1, "timestamp": timestamp, "identity": self.identity, "kind": kind, "payload": dict(payload), "prev_hash": rows[-1]["hash"] if rows else ""}
        row["hash"] = _row_hash(row)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return row


def validate_recording_segment(payload: Mapping[str, Any], identity: Mapping[str, Any]) -> dict[str, Any]:
    """Validate hash and actual playback/decode with ``media_validation``."""
    try:
        _same_identity(payload, identity, "recording")
        _validate_payload("recording", payload)
    except ValueError as exc:
        return {"valid": False, "reason": str(exc)}
    path = Path(str(payload["path"]))
    if not path.is_file() or payload["complete"] is not True:
        return {"valid": False, "reason": "recording missing or marked incomplete"}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != payload["sha256"]:
        return {"valid": False, "reason": "recording SHA-256 mismatch"}
    try:
        artifact = ArtifactRef(id=f"training.{identity['run_id']}.{payload['segment']}", kind="video", path=str(path), sha256=digest, media_type="video/mp4")
        issues = artifact_file_issues(artifact)
    except Exception as exc:
        return {"valid": False, "reason": f"media validator unavailable: {type(exc).__name__}: {exc}"}
    return {"valid": not issues, "issues": issues, "path": str(path.resolve()), "sha256": digest}


def _first_battle(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    boundaries = [row for row in rows if row["kind"] == "battle_boundary"]
    starts = [row for row in boundaries if row["payload"]["phase"] == "start"]
    if not starts:
        return None
    first = starts[0]["payload"]["battle_id"]
    matching = [row for row in boundaries if row["payload"]["battle_id"] == first]
    if [row["payload"]["phase"] for row in matching] != ["start", "end"]:
        return None
    return {"battle_id": first, "start": matching[0]["payload"]["fields"], "end": matching[1]["payload"]["fields"], "zero_leak": matching[1]["payload"]["fields"]["leaked_enemies"] == 0}


def compile_certificate(journal_path: Path | str, identity: Mapping[str, Any], output_path: Path | str | None = None) -> dict[str, Any]:
    journal = EvidenceJournal(journal_path, identity)
    rows, ident = journal.rows(), journal.identity
    by_kind = {kind: [row for row in rows if row["kind"] == kind] for kind in KINDS}
    hud = by_kind["hud"][-12:]
    faults = [row for row in by_kind["facility"] if row["payload"]["event"] in {"stop", "fault", "restart"}]
    unsafe = [row for row in by_kind["action"] if not (row["payload"]["safety_channel"]["available"] and row["payload"]["safety_channel"]["fresh"])]
    exits = by_kind["runner_exit"]
    abnormal = bool(exits and exits[-1]["payload"]["normal"] is not True)
    final_squad = by_kind["squad"][-1]["payload"].get("members") if by_kind["squad"] else None
    actions = [row for row in by_kind["action"] if row["payload"]["verified_success"]]
    floor = by_kind["floor"][-1]["payload"]["floor"] if by_kind["floor"] else None
    settlement = by_kind["settlement"][-1]["payload"] if by_kind["settlement"] else None
    ledger = by_kind["ledger"][-1]["payload"] if by_kind["ledger"] else None
    recordings = [dict(row["payload"], validation=validate_recording_segment(row["payload"], ident)) for row in by_kind["recording"]]
    counts = {item["segment_count"] for item in recordings}
    ordered = sorted(recordings, key=lambda item: item["segment"])
    observed_indices = [item["segment"] for item in recordings]
    expected_indices = list(range(1, next(iter(counts)) + 1)) if len(counts) == 1 else []
    recording_complete = bool(ordered and observed_indices == expected_indices and [item["segment"] for item in ordered] == expected_indices and len(ordered) == len(expected_indices) and all(item["complete"] and item["validation"]["valid"] for item in ordered))
    first_battle = _first_battle(rows)
    mandatory_missing = (
        bool(by_kind["evidence_fault"])
        or not final_squad
        or len(hud) != 12
        or not actions
        or first_battle is None
        or floor is None
        or settlement is None
        or ledger is None
        or not recording_complete
        or not exits
    )
    technical = bool(faults or unsafe or abnormal)
    triple = bool(settlement and ledger and settlement["outcome"] == ledger["outcome"] == "clear" and _identity(settlement) == _identity(ledger) == ident and recording_complete)
    classification = "technical_void" if technical else "unresolved" if mandatory_missing else "clear" if triple else "natural_loss" if settlement and ledger and settlement["outcome"] == ledger["outcome"] == "loss" else "unresolved"
    cert = {"schema": CERTIFICATE_SCHEMA, "identity": ident, "journal_path": str(Path(journal_path).resolve()), "journal_hash": rows[-1]["hash"] if rows else "", "final_squad": final_squad, "final_hud_summaries": [row["payload"].get("fields", row["payload"]) for row in hud], "last_verified_success_action": actions[-1]["payload"] if actions else None, "leak_life_timeline": [{"seq": row["seq"], "timestamp": row["timestamp"], **row["payload"].get("fields", row["payload"])} for row in hud], "first_battle": first_battle, "final_floor": floor, "ending_ocr": settlement, "ledger_outcome": ledger, "recording_evidence": recordings, "recording_complete": recording_complete, "facility_fault_evidence": faults, "evidence_faults": [row["payload"] for row in by_kind["evidence_fault"]], "classification": classification, "triple_proved_clear": classification == "clear", "missing_mandatory_evidence": mandatory_missing}
    cert["hash"] = sha256(cert)
    if output_path is not None:
        atomic_json(output_path, cert)
    return cert


def atomic_json(path: Path | str, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False, newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        name = handle.name
    os.replace(name, target)


def _validate_doctrine_rule(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("doctrine rule must be text")
    rule = value.strip()
    lowered = rule.casefold()
    if (
        len(rule) < 16
        or "\n" in rule
        or "\r" in rule
        or not re.search(
            r"\b(if|when|after|before)\b.*\b(then|must|do|choose|avoid)\b",
            lowered,
        )
    ):
        raise ValueError("doctrine rule must be executable conditional prose")
    forbidden = (
        r"\b(?:event|encounter|node)\b.*\b(?:answer|option)\b.*"
        r"\b(?:result|outcome|yield(?:s|ed)?)\b",
        r"(?:事件|遭遇|节点).*选项.*(?:结果|获得|导致)",
        r"\b(?:x|y|row|col|pixel)\s*(?::|=|is)?\s*\d+",
        r"\b(?:tap|swipe)\s*\(?\s*\d+\s*[,，]\s*\d+",
        r"\b\d{2,4}\s*[,/]\s*\d{2,4}\b",
    )
    if any(re.search(pattern, lowered) for pattern in forbidden):
        raise ValueError("doctrine cannot contain hidden-event mappings or coordinate-level actions")
    return rule


def _doctrine_rows(path: Path, slot_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    previous = ""
    for version, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if path.exists() else []:
        row = json.loads(line)
        if not isinstance(row, dict) or set(row) != DOCTRINE_FIELDS:
            raise ValueError("doctrine row shape is invalid")
        row_version = row.get("version")
        if (
            row.get("schema") != DOCTRINE_SCHEMA
            or not isinstance(row_version, int)
            or isinstance(row_version, bool)
            or row_version != version
            or row.get("slot_id") != slot_id
            or row.get("prev_hash") != previous
            or row.get("hash") != _row_hash(row)
        ):
            raise ValueError("doctrine version, slot, or hash chain is invalid")
        source_hash = row.get("source_certificate_hash")
        if not isinstance(source_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", source_hash
        ):
            raise ValueError("doctrine source certificate hash is invalid")
        if _identity(row.get("source_identity", {}))["slot_id"] != slot_id:
            raise ValueError("doctrine source identity crosses the slot boundary")
        row["rule"] = _validate_doctrine_rule(row.get("rule"))
        rows.append(row)
        previous = row["hash"]
    return rows


def load_doctrine_rules(path: Path | str, *, slot_id: str) -> tuple[str, ...]:
    """Return only safe rule text after validating the complete slot-local chain."""
    normalized_slot = str(slot_id).strip()
    if not normalized_slot:
        raise ValueError("doctrine slot_id is mandatory")
    return tuple(row["rule"] for row in _doctrine_rows(Path(path), normalized_slot))


def distill_doctrine(path: Path | str, *, slot_id: str, rule: str, certificate: Mapping[str, Any]) -> dict[str, Any]:
    slot_id, rule = str(slot_id).strip(), _validate_doctrine_rule(rule)
    cert_identity = _identity(certificate.get("identity", {}))
    if not slot_id or cert_identity["slot_id"] != slot_id or certificate.get("hash") != sha256({key: value for key, value in certificate.items() if key != "hash"}):
        raise ValueError("doctrine write requires an intact source certificate in its source slot")
    target = Path(path)
    existing = _doctrine_rows(target, slot_id)
    record = {"schema": DOCTRINE_SCHEMA, "version": len(existing) + 1, "slot_id": slot_id, "rule": rule, "source_certificate_hash": certificate["hash"], "source_identity": cert_identity, "prev_hash": existing[-1]["hash"] if existing else ""}
    record["hash"] = _row_hash(record)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def _validated_certificates(certificates: Iterable[Mapping[str, Any]], paths: Iterable[Path | str]) -> list[Mapping[str, Any]]:
    certs, bound_paths = list(certificates), list(paths)
    for cert in certs:
        if cert.get("schema") != CERTIFICATE_SCHEMA or cert.get("hash") != sha256({key: value for key, value in cert.items() if key != "hash"}):
            raise ValueError("certificate hash is invalid")
        _identity(cert.get("identity", {}))
    if bound_paths and len(bound_paths) != len(certs):
        raise ValueError("every supplied certificate must have exactly one certificate path")
    for cert, path_value in zip(certs, bound_paths):
        raw = Path(path_value).read_bytes()
        try:
            disk = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("certificate path does not contain JSON") from exc
        if disk != cert or disk.get("hash") != cert["hash"] or _identity(disk) != _identity(cert):
            raise ValueError("certificate path bytes or identity do not match supplied certificate")
    return certs


def metrics_report(certificates: Iterable[Mapping[str, Any]], certificate_paths: Iterable[Path | str] = ()) -> dict[str, Any]:
    paths = list(certificate_paths)
    certs = _validated_certificates(certificates, paths)
    qualified = [
        cert for cert in certs if cert["classification"] in {"natural_loss", "clear"}
    ]
    nontechnical = [
        cert for cert in certs if cert["classification"] != "technical_void"
    ]
    floors = [cert["final_floor"] for cert in qualified if isinstance(cert.get("final_floor"), int)]
    # This rate is descriptive even for an unresolved run; unresolved runs
    # still never contribute to the qualification streak or readiness gate.
    first = [
        cert
        for cert in nontechnical
        if isinstance(cert.get("first_battle"), Mapping)
    ]
    zero = sum(cert["first_battle"].get("zero_leak") is True for cert in first)
    streak = 0
    for cert in reversed(certs):
        if cert["classification"] not in {"natural_loss", "clear"}:
            break
        streak += 1
    return {"schema": "omnicompany.arknights.training-metrics.v1", "run_count": len(certs), "technical_void_runs": [cert["identity"]["run_id"] for cert in certs if cert["classification"] == "technical_void"], "consecutive_nontechnical_runs": streak, "median_reached_floor": statistics.median(floors) if floors else None, "first_battle_zero_leak_rate": zero / len(first) if first else None, "clear_count": sum(cert["triple_proved_clear"] is True for cert in certs), "certificate_paths_hashes": [{"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()} for path in paths]}


def readiness_report(certificates: Iterable[Mapping[str, Any]], certificate_paths: Iterable[Path | str] = ()) -> dict[str, Any]:
    metrics = metrics_report(certificates, certificate_paths)
    ready = metrics["consecutive_nontechnical_runs"] >= 3 and (metrics["median_reached_floor"] or 0) >= 3 and metrics["clear_count"] >= 1
    return {"schema": "omnicompany.arknights.training-readiness.v1", "ready": ready, "starts_match": False, "metrics": metrics}
