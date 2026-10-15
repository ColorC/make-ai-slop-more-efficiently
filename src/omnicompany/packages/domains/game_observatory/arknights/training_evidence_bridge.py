# [OMNI] origin=codex created_by=codex intent="bridge structured training receipts into fail-closed evidence certificates"
"""Structured, screenshot-free bridge for Arknights training evidence.

The bridge never observes the game and never upgrades an inference into a fact.
It copies only explicit receipts from the tournament state, semantic action
journal, run-world state and the recorder hook's exact completed-file list.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .training_evidence import (
    HUD_FIELDS,
    EvidenceJournal,
    atomic_json,
    compile_certificate,
    readiness_report,
)

BRIDGE_SCHEMA = "omnicompany.arknights.training-evidence-bridge.v1"
SETTLEMENT_SCHEMA = "omnicompany.arknights.semantic-settlement.v1"
RESIDENT_SETTLEMENT_SCHEMA = "omnicompany.arknights.resident-settlement-ocr.v1"
BATTLE_ACTION_FAULT_SCHEMA = "arknights-battle-action-fault.v1"
FACILITY_SEMANTIC_LEDGER_SCHEMA = "arknights-facility-semantic-ledger.v1"
FACILITY_SEMANTIC_RECEIPT_SCHEMA = "arknights-facility-semantic-receipt.v1"
FACILITY_SEMANTIC_REF_SCHEMA = "arknights-facility-semantic-receipt-ref.v1"
FACILITY_SIGNING_TRUST_SCHEMA = "arknights-facility-signing-trust-root.v1"
FACILITY_SIGNATURE_SCHEMA = "arknights-facility-semantic-ledger-signature.v1"
CONFIRMED_EFFECT_RECOVERY_SCHEMA = (
    "omnicompany.arknights.confirmed-daemon-effect-recovery.v1"
)
PRE_LIFECYCLE_UNBOUND_COMMANDS = {"goto-theme", "open-run"}


def training_run_identity(
    *, slot_id: str, theme_id: str, run_number: int
) -> dict[str, str]:
    slot, theme = str(slot_id).strip(), str(theme_id).strip()
    if (
        not slot
        or not theme
        or not isinstance(run_number, int)
        or isinstance(run_number, bool)
        or run_number < 1
    ):
        raise ValueError("training identity requires slot, theme, and positive run number")
    theme_key = hashlib.sha256(theme.encode("utf-8")).hexdigest()[:12]
    return {
        "run_id": f"training:{slot}:{theme_key}:run-{run_number}",
        "slot_id": slot,
        "theme_id": theme,
    }


def _same_identity(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        isinstance(left, Mapping)
        and set(left) == set(right)
        and all(str(left.get(key, "")).strip() == right[key] for key in right)
    )


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"structured evidence is not an object: {path}")
    return value


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ")
        except ValueError:
            return False
    return True


def _timestamp(value: Any) -> datetime | None:
    """Return one unambiguous UTC instant; naive timestamps are not evidence."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%Y%m%dT%H%M%S.%fZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _facility_trust_root(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("facility semantic signing trust root is not an object")
    normalized = {
        key: str(value.get(key) or "").strip()
        for key in (
            "schema",
            "signature_algorithm",
            "facility_instance_id",
            "key_id",
            "public_key_base64",
        )
    }
    try:
        public_bytes = base64.b64decode(
            normalized["public_key_base64"], validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("facility semantic public key is invalid") from exc
    if (
        normalized["schema"] != FACILITY_SIGNING_TRUST_SCHEMA
        or normalized["signature_algorithm"] != "ed25519"
        or re.fullmatch(
            r"facility-instance:[0-9a-f]{32}",
            normalized["facility_instance_id"],
        )
        is None
        or len(public_bytes) != 32
        or normalized["key_id"]
        != "sha256:" + hashlib.sha256(public_bytes).hexdigest()
    ):
        raise ValueError("facility semantic signing trust root is invalid")
    return normalized


class TrainingEvidenceBridge:
    """Persist a single run's source boundary and append proven facts once."""

    def __init__(
        self,
        *,
        tournament_root: Path | str,
        output_dir: Path | str,
        identity: Mapping[str, Any],
        facility_trust_root: Mapping[str, Any] | None = None,
    ) -> None:
        self.tournament_root = Path(tournament_root)
        self.output_dir = Path(output_dir)
        self.identity = {
            key: str(identity.get(key, "")).strip()
            for key in ("run_id", "slot_id", "theme_id")
        }
        if not all(self.identity.values()):
            raise ValueError("bridge identity is incomplete")
        self.facility_trust_root = _facility_trust_root(facility_trust_root)
        self.state_path = self.output_dir / "bridge-state.json"
        self.journal_path = self.output_dir / "evidence.jsonl"
        self.certificate_path = self.output_dir / "certificate.json"
        self.action_path = self.tournament_root / "live" / "contestant-actions.jsonl"
        self.action_fault_path = (
            self.tournament_root / "live" / "facility-battle-action-faults.jsonl"
        )
        self.semantic_receipt_path = (
            self.tournament_root / "live" / "facility-semantic-receipts.jsonl"
        )
        self.world_state_path = self.tournament_root / "live" / "run-world-state.json"
        self.journal = EvidenceJournal(self.journal_path, self.identity)

    def begin(
        self,
        active_run: Mapping[str, Any],
        *,
        action_offset: int,
        attaching: bool,
    ) -> None:
        self._validate_run(active_run)
        expected = [
            str(Path(value).resolve())
            for value in active_run.get("recording_roots", ())
        ]
        current = str(Path(str(active_run.get("recording", ""))).resolve())
        if not expected or current not in expected:
            raise ValueError("active run has no exact current recording root")
        semantic_raw = (
            self.semantic_receipt_path.read_bytes()
            if self.semantic_receipt_path.exists()
            else b""
        )
        if semantic_raw and not semantic_raw.endswith(b"\n"):
            raise ValueError("facility semantic receipt ledger has an incomplete row")
        if self.state_path.exists():
            state = _read_object(self.state_path)
            self._validate_state(state)
            semantic_offset = state["semantic_receipt_offset"]
            if (
                semantic_offset > len(semantic_raw)
                or (
                    semantic_offset
                    and semantic_raw[semantic_offset - 1 : semantic_offset] != b"\n"
                )
                or hashlib.sha256(semantic_raw[:semantic_offset]).hexdigest()
                != state["semantic_receipt_prefix_sha256"]
            ):
                raise ValueError("facility semantic receipt checkpoint changed")
            if self.facility_trust_root is None:
                if (
                    "facility-signing-runtime-pin-missing"
                    not in state["source_faults"]
                ):
                    state["source_faults"].append(
                        "facility-signing-runtime-pin-missing"
                    )
            else:
                # The persisted byte/hash checkpoint is the trust boundary
                # between daemon key eras. Only post-checkpoint rows are
                # verified against this fresh control-channel pin.
                state["facility_trust_root"] = self.facility_trust_root
            known = list(state["expected_recording_roots"])
            if expected[: len(known)] != known:
                raise ValueError("recording-root history changed across bridge resume")
            state["expected_recording_roots"] = expected
            if "action_fault_offset" not in state:
                state["action_fault_offset"] = (
                    self.action_fault_path.stat().st_size
                    if self.action_fault_path.exists()
                    else 0
                )
        else:
            if action_offset < 0:
                raise ValueError("action journal offset cannot be negative")
            state = {
                "schema": BRIDGE_SCHEMA,
                "identity": self.identity,
                "action_offset": action_offset,
                "action_fault_offset": (
                    self.action_fault_path.stat().st_size
                    if self.action_fault_path.exists()
                    else 0
                ),
                "semantic_receipt_offset": len(semantic_raw),
                "semantic_receipt_prefix_sha256": hashlib.sha256(
                    semantic_raw
                ).hexdigest(),
                "facility_trust_root": self.facility_trust_root,
                "expected_recording_roots": expected,
                "recordings": {},
                "source_faults": (
                    (["attach-without-prior-bridge-state"] if attaching else [])
                    + (
                        ["facility-signing-trust-root-missing"]
                        if self.facility_trust_root is None
                        else []
                    )
                ),
            }
        atomic_json(self.state_path, state)

    def record_recording_root(
        self,
        root: Path | str,
        completed_files: Sequence[Path | str],
        *,
        stopped_cleanly: bool,
    ) -> None:
        state = self._state()
        root_value = str(Path(root).resolve())
        if root_value not in state["expected_recording_roots"]:
            raise ValueError("recorder reported a root outside the active run")
        paths = [str(Path(value).resolve()) for value in completed_files]
        if len(paths) != len(set(paths)):
            raise ValueError("recorder reported duplicate completed files")
        receipt = {"completed_files": paths, "stopped_cleanly": stopped_cleanly is True}
        existing = state["recordings"].get(root_value)
        if existing is not None and existing != receipt:
            raise ValueError("conflicting recorder completion receipt")
        state["recordings"][root_value] = receipt
        atomic_json(self.state_path, state)

    def finalize(
        self,
        *,
        summary: Mapping[str, Any] | None,
        runner_exit_normal: bool | None,
        settlement_receipt: Mapping[str, Any] | None = None,
        action_projector: Callable[[Path], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        state = self._state()
        for index, reason in enumerate(state["source_faults"], 1):
            self.journal.append(
                "facility",
                {"event": "fault", "source": "evidence-bridge", "reason": reason},
                f"bridge:fault:{index}",
            )
        exploration_ids = self._append_action_receipts(state)
        self._append_action_faults(state)
        if len(exploration_ids) > 1:
            self.journal.append(
                "facility",
                {
                    "event": "fault",
                    "source": "contestant-action-journal",
                    "reason": "multiple exploration identities in one training run",
                },
                "actions:cross-exploration",
            )
        self._append_world_state(exploration_ids)
        if summary is not None:
            self._append_summary(summary)
        if settlement_receipt is not None:
            self._append_settlement(settlement_receipt, exploration_ids)
        self._append_recordings(state)
        if runner_exit_normal is not None:
            self.journal.append(
                "runner_exit",
                {"normal": runner_exit_normal is True},
                "runner:exit",
            )
        self.action_projection: dict[str, Any] = {}
        action_count = sum(
            row["kind"] == "action" for row in self.journal.rows()
        )
        if action_count:
            if action_projector is None:
                self.journal.append(
                    "evidence_fault",
                    {
                        "source": "canonical-action-quality-projector",
                        "reason": "canonical action-quality projector is unavailable",
                    },
                    "canonical-action-projection:missing",
                )
            else:
                try:
                    projection = dict(action_projector(self.journal_path))
                    observation_ids = list(projection.get("observation_ids") or [])
                    sample_ids = list(projection.get("sample_ids") or [])
                    if (
                        len(observation_ids) != action_count
                        or len(sample_ids) != action_count
                        or len(set(observation_ids)) != action_count
                        or len(set(sample_ids)) != action_count
                    ):
                        raise ValueError(
                            "canonical action projection is not one-to-one"
                        )
                    self.action_projection = {
                        "observation_ids": observation_ids,
                        "sample_ids": sample_ids,
                    }
                except Exception as exc:  # noqa: BLE001 - projection must fail closed
                    self.journal.append(
                        "evidence_fault",
                        {
                            "source": "canonical-action-quality-projector",
                            "reason": str(exc),
                        },
                        "canonical-action-projection:failed",
                    )
        return compile_certificate(
            self.journal_path, self.identity, output_path=self.certificate_path
        )

    def _validate_run(self, run: Mapping[str, Any]) -> None:
        run_number = run.get("run_number")
        expected_identity = (
            training_run_identity(
                slot_id=str(run.get("slot_id", "")),
                theme_id=str(run.get("theme", "")),
                run_number=run_number,
            )
            if isinstance(run_number, int) and not isinstance(run_number, bool)
            else {}
        )
        if (
            expected_identity != self.identity
            or not str(run.get("phase", "")).strip() == "training"
        ):
            raise ValueError("active run crosses the bridge slot, theme, or phase boundary")

    def _validate_state(self, state: Mapping[str, Any]) -> None:
        if state.get("schema") != BRIDGE_SCHEMA or not _same_identity(
            state.get("identity", {}), self.identity
        ):
            raise ValueError("bridge state schema or identity mismatch")
        if not isinstance(state.get("action_offset"), int) or state["action_offset"] < 0:
            raise ValueError("bridge action offset is invalid")
        fault_offset = state.get("action_fault_offset")
        if fault_offset is not None and (
            not isinstance(fault_offset, int) or fault_offset < 0
        ):
            raise ValueError("bridge action-fault offset is invalid")
        semantic_offset = state.get("semantic_receipt_offset")
        if semantic_offset is not None and (
            not isinstance(semantic_offset, int) or semantic_offset < 0
        ):
            raise ValueError("bridge semantic-receipt offset is invalid")
        semantic_prefix_sha256 = state.get("semantic_receipt_prefix_sha256")
        if (
            not isinstance(semantic_prefix_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", semantic_prefix_sha256)
        ):
            raise ValueError("bridge semantic-receipt checkpoint hash is invalid")
        if (
            not isinstance(state.get("expected_recording_roots"), list)
            or not isinstance(state.get("recordings"), dict)
            or not isinstance(state.get("source_faults"), list)
        ):
            raise ValueError("bridge recording state is invalid")

    def _state(self) -> dict[str, Any]:
        state = _read_object(self.state_path)
        self._validate_state(state)
        try:
            pinned = _facility_trust_root(state.get("facility_trust_root"))
        except ValueError:
            pinned = None
            if "facility-signing-state-pin-invalid" not in state["source_faults"]:
                state["source_faults"].append("facility-signing-state-pin-invalid")
        if self.facility_trust_root is None:
            if "facility-signing-runtime-pin-missing" not in state["source_faults"]:
                state["source_faults"].append(
                    "facility-signing-runtime-pin-missing"
                )
        elif pinned != self.facility_trust_root:
            if "facility-signing-state-pin-tampered" not in state["source_faults"]:
                state["source_faults"].append(
                    "facility-signing-state-pin-tampered"
                )
            # Verification always uses the control-channel in-memory pin.
            state["facility_trust_root"] = self.facility_trust_root
        return state

    def _action_rows(self, offset: int) -> list[dict[str, Any]]:
        if not self.action_path.exists():
            return []
        raw = self.action_path.read_bytes()
        if offset > len(raw):
            raise ValueError("contestant action journal was truncated after run start")
        tail = raw[offset:]
        if offset and tail and raw[offset - 1 : offset] != b"\n":
            raise ValueError("contestant action journal offset is not a row boundary")
        if tail and not tail.endswith(b"\n"):
            raise ValueError("contestant action journal ends with an incomplete row")
        rows: list[dict[str, Any]] = []
        row_start = offset
        for number, line in enumerate(tail.splitlines(keepends=True), 1):
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid contestant action row {number}") from exc
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("at"), str)
                or not row["at"].strip()
            ):
                raise ValueError(f"invalid contestant action row {number}")
            if row.get("schema") == CONFIRMED_EFFECT_RECOVERY_SCHEMA:
                if "_bridge_row_start" in row or "_bridge_prefix_sha256" in row:
                    raise ValueError(f"invalid contestant action row {number}")
                row["_bridge_row_start"] = row_start
                row["_bridge_prefix_sha256"] = hashlib.sha256(
                    raw[:row_start]
                ).hexdigest()
            row_start += len(line)
            rows.append(row)
        return rows

    def _append_action_faults(self, state: Mapping[str, Any]) -> None:
        offset = state.get("action_fault_offset")
        if offset is None or not self.action_fault_path.exists():
            return
        raw = self.action_fault_path.read_bytes()
        if offset > len(raw):
            raise ValueError("battle action fault journal was truncated after run start")
        tail = raw[offset:]
        if offset and tail and raw[offset - 1 : offset] != b"\n":
            raise ValueError("battle action fault offset is not a row boundary")
        if tail and not tail.endswith(b"\n"):
            raise ValueError("battle action fault journal ends with an incomplete row")
        for number, line in enumerate(tail.splitlines(), 1):
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid battle action fault row {number}") from exc
            if not isinstance(row, dict) or row.get("schema") != BATTLE_ACTION_FAULT_SCHEMA:
                raise ValueError(f"invalid battle action fault row {number}")
            fault_id = str(row.get("fault_id") or "")
            unsigned = dict(row)
            unsigned.pop("fault_id", None)
            canonical = json.dumps(
                unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            expected = "battle-action-fault:" + hashlib.sha256(canonical).hexdigest()
            if fault_id != expected or not _is_timestamp(row.get("at")):
                raise ValueError(f"unverified battle action fault row {number}")
            for key in ("native_quiesced", "pause_proved"):
                if not isinstance(row.get(key), bool):
                    raise ValueError(f"invalid battle action fault row {number}")
            selected = {
                "event": "fault",
                "source": "resident-native-battle-action",
                "fault_id": fault_id,
                "action": str(row.get("action") or "")[:64],
                "operator_id": str(row.get("operator_id") or "")[:128],
                "stage_name": str(row.get("stage_name") or "")[:128],
                "reason": str(row.get("violation") or "")[:256],
                "native_quiesced": row["native_quiesced"],
                "pause_proved": row["pause_proved"],
            }
            if not all(selected[key] for key in ("action", "operator_id", "reason")):
                raise ValueError(f"incomplete battle action fault row {number}")
            self.journal.append("facility", selected, fault_id)

    @staticmethod
    def _effect_predicate_matches(effect: Mapping[str, Any]) -> bool:
        expected = effect.get("expected")
        actual = effect.get("actual")
        if not isinstance(expected, Mapping) or not isinstance(actual, Mapping):
            return False
        predicate = str(expected.get("predicate") or "").strip()
        if actual.get("satisfied") is not True:
            return False
        if predicate == "semantic-surface-changed":
            before = str(actual.get("before_semantic_sha256") or "")
            after = str(actual.get("after_semantic_sha256") or "")
            return bool(before and after and before != after)
        if predicate == "start-new-opening-page":
            before = str(actual.get("before_semantic_sha256") or "")
            after = str(actual.get("after_semantic_sha256") or "")
            return bool(
                before
                and after
                and before != after
                and actual.get("requested_option_kind")
                == "start-new-exploration"
                and actual.get("opening_page") is True
                and actual.get("auth_status") != "expired"
            )
        if predicate == "any-semantic-marker":
            required = set(expected.get("required_markers") or ())
            observed = set(actual.get("observed_markers") or ())
            return bool(required and required.intersection(observed))
        if predicate == "active-run-surface":
            return actual.get("active_run_surface") is True
        if predicate == "route-map-visible":
            return actual.get("route_map_visible") is True
        return False

    @staticmethod
    def _receipt_matches_command(receipt_action: str, command: str) -> bool:
        accepted = {
            "screen-select": {"screen-choose"},
            "screen-select-label": {"screen-choose"},
            "screen-back": {"screen-choose", "back"},
        }.get(command, {command})
        return receipt_action in accepted

    def _semantic_receipts(
        self, state: Mapping[str, Any]
    ) -> dict[tuple[int, str, str, str], dict[str, Any]]:
        offset = state.get("semantic_receipt_offset")
        if offset is None or not self.semantic_receipt_path.exists():
            return {}
        trust_root = self.facility_trust_root
        if trust_root is None:
            raise ValueError("facility semantic signing trust root is absent")
        trusted_public_bytes = base64.b64decode(
            trust_root["public_key_base64"], validate=True
        )
        trusted_public_key = Ed25519PublicKey.from_public_bytes(trusted_public_bytes)
        raw = self.semantic_receipt_path.read_bytes()
        if (
            offset > len(raw)
            or hashlib.sha256(raw[:offset]).hexdigest()
            != state.get("semantic_receipt_prefix_sha256")
        ):
            raise ValueError("facility semantic receipt ledger was truncated")
        if offset and raw[offset - 1 : offset] != b"\n":
            raise ValueError("facility semantic receipt offset is not a row boundary")
        if raw and not raw.endswith(b"\n"):
            raise ValueError("facility semantic receipt ledger ends with an incomplete row")
        result: dict[tuple[int, str, str, str], dict[str, Any]] = {}
        previous = ""
        intents: set[str] = set()
        byte_offset = 0
        for number, line in enumerate(raw.splitlines(keepends=True), 1):
            row_start = byte_offset
            byte_offset += len(line)
            try:
                row = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"invalid facility semantic receipt row {number}"
                ) from exc
            unsigned = dict(row) if isinstance(row, dict) else {}
            row_hash = str(unsigned.pop("hash", ""))
            signature_base64 = str(unsigned.pop("signature_base64", ""))
            expected_hash = hashlib.sha256(
                _canonical_json(unsigned)
            ).hexdigest()
            receipt = row.get("receipt") if isinstance(row, dict) else None
            receipt_sha = str(row.get("receipt_sha256") or "")
            canonical_receipt_sha = (
                hashlib.sha256(
                    json.dumps(
                        receipt,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                if isinstance(receipt, dict)
                else ""
            )
            intent_id = (
                str(receipt.get("intent_id") or "").strip()
                if isinstance(receipt, dict)
                else ""
            )
            if (
                not isinstance(row, dict)
                or row.get("schema") != FACILITY_SEMANTIC_LEDGER_SCHEMA
                or row.get("seq") != number
                or row.get("prev_hash") != previous
                or row_hash != expected_hash
                or receipt_sha != canonical_receipt_sha
                or not _is_timestamp(row.get("recorded_at"))
                or not isinstance(receipt, dict)
                or receipt.get("schema") != FACILITY_SEMANTIC_RECEIPT_SCHEMA
                or not intent_id
                or intent_id in intents
            ):
                raise ValueError(
                    f"unverified facility semantic receipt row {number}"
                )
            intents.add(intent_id)
            previous = row_hash
            if row_start < offset:
                continue
            try:
                signature = base64.b64decode(signature_base64, validate=True)
                if (
                    row.get("signature_algorithm") != "ed25519"
                    or row.get("facility_instance_id")
                    != trust_root["facility_instance_id"]
                    or row.get("signing_key_id") != trust_root["key_id"]
                    or row.get("signing_public_key_base64")
                    != trust_root["public_key_base64"]
                    or len(signature) != 64
                ):
                    raise ValueError("facility signing identity differs from pin")
                trusted_public_key.verify(
                    signature,
                    _canonical_json(
                        {
                            "schema": FACILITY_SIGNATURE_SCHEMA,
                            "ledger_row": unsigned,
                            "ledger_hash": row_hash,
                        }
                    ),
                )
            except (binascii.Error, InvalidSignature, TypeError, ValueError) as exc:
                raise ValueError(
                    f"unverified facility semantic receipt signature row {number}"
                ) from exc
            key = (
                number,
                str(receipt.get("receipt_id") or ""),
                receipt_sha,
                row_hash,
            )
            result[key] = {**receipt, "recorded_at": row["recorded_at"]}
        return result

    def _append_action_receipts(self, state: Mapping[str, Any]) -> set[str]:
        try:
            rows = self._action_rows(state["action_offset"])
        except ValueError as exc:
            self.journal.append(
                "facility",
                {
                    "event": "fault",
                    "source": "contestant-action-journal",
                    "reason": str(exc),
                },
                "actions:invalid",
            )
            return set()
        try:
            canonical_receipts = self._semantic_receipts(state)
        except ValueError as exc:
            self.journal.append(
                "facility",
                {
                    "event": "fault",
                    "source": "facility-semantic-receipt-ledger",
                    "reason": str(exc),
                },
                "facility-semantic-receipts:invalid",
            )
            canonical_receipts = {}
        used_receipts: set[str] = set()
        exploration_ids: set[str] = set()
        lifecycle_exploration_id = ""
        lifecycle_at: datetime | None = None
        lifecycle_ambiguous = False
        if self.world_state_path.exists():
            try:
                world = _read_object(self.world_state_path)
            except (OSError, ValueError, json.JSONDecodeError):
                world = {}
                lifecycle_ambiguous = True
            if (
                world.get("schema") == "arknights-run-world-state.v2"
                and world.get("identity_status") in {"active", "finished"}
                and str(world.get("exploration_id") or "").strip()
            ):
                lifecycle_exploration_id = str(world["exploration_id"]).strip()
                lifecycle = world.get("lifecycle_evidence")
                if isinstance(lifecycle, Mapping):
                    lifecycle_at = _timestamp(lifecycle.get("captured_at"))
                if lifecycle_at is None:
                    lifecycle_ambiguous = True
        for index, row in enumerate(rows, 1):
            timestamp = f"{row['at']}:action:{index}"
            semantic = str(row.get("command") or row.get("action") or "").strip()
            journal_action_at = _timestamp(row.get("at"))
            post_lifecycle_mutation = bool(
                lifecycle_exploration_id
                and semantic not in PRE_LIFECYCLE_UNBOUND_COMMANDS
                and (
                    lifecycle_ambiguous
                    or lifecycle_at is None
                    or journal_action_at is None
                    or journal_action_at >= lifecycle_at
                )
            )
            reference = row.get("canonical_receipt_ref")
            canonical: dict[str, Any] | None = None
            trust_fault = ""
            recovered_effect = row.get("schema") == CONFIRMED_EFFECT_RECOVERY_SCHEMA
            if isinstance(reference, Mapping):
                key = (
                    reference.get("seq"),
                    str(reference.get("receipt_id") or ""),
                    str(reference.get("receipt_sha256") or ""),
                    str(reference.get("ledger_hash") or ""),
                )
                if reference.get("schema") != FACILITY_SEMANTIC_REF_SCHEMA:
                    trust_fault = "canonical receipt reference schema is invalid"
                else:
                    canonical = canonical_receipts.get(key)  # type: ignore[arg-type]
                    if canonical is None:
                        trust_fault = "canonical receipt reference is not in daemon ledger"
                    elif str(canonical.get("receipt_id") or "") in used_receipts:
                        trust_fault = "canonical receipt was referenced more than once"
                    elif not self._receipt_matches_command(
                        str(canonical.get("action") or ""), semantic
                    ):
                        trust_fault = "canonical receipt action differs from journal command"
                    else:
                        used_receipts.add(str(canonical["receipt_id"]))
            elif semantic not in PRE_LIFECYCLE_UNBOUND_COMMANDS:
                trust_fault = "daemon canonical receipt reference is absent"
            if recovered_effect and not trust_fault:
                recovery = row.get("recovery")
                allowed_row_keys = {
                    "schema",
                    "at",
                    "command",
                    "ok",
                    "verified_success",
                    "input_sent",
                    "journal_prefix_bytes",
                    "journal_prefix_sha256",
                    "recovery",
                    "canonical_receipt_ref",
                    "_bridge_row_start",
                    "_bridge_prefix_sha256",
                }
                if (
                    set(row) != allowed_row_keys
                    or row.get("ok") is not True
                    or row.get("verified_success") is not False
                    or row.get("input_sent") is not False
                    or row.get("journal_prefix_bytes") != row.get("_bridge_row_start")
                    or row.get("journal_prefix_sha256")
                    != row.get("_bridge_prefix_sha256")
                    or not isinstance(recovery, Mapping)
                    or set(recovery)
                    != {
                        "mode",
                        "run_identity",
                        "intent_id",
                        "action",
                        "exploration_id",
                        "evidence_receipt",
                        "receipt_id",
                    }
                    or recovery.get("mode") != "confirmed-effect-no-input"
                    or not _same_identity(recovery.get("run_identity", {}), self.identity)
                    or canonical is None
                    or recovery.get("intent_id") != canonical.get("intent_id")
                    or recovery.get("action") != canonical.get("action")
                    or recovery.get("exploration_id")
                    != canonical.get("exploration_id")
                    or recovery.get("evidence_receipt")
                    != canonical.get("evidence_receipt")
                    or recovery.get("receipt_id") != canonical.get("receipt_id")
                ):
                    trust_fault = "confirmed-effect recovery binding is invalid"
            source = canonical or {}
            auth_fault = bool(
                canonical is not None
                and not trust_fault
                and canonical.get("fault_kind") == "authentication-expired"
                and canonical.get("input_sent") is True
                and canonical.get("verified_success") is False
            )
            if canonical is not None and not auth_fault:
                self.journal.append(
                    "facility",
                    {
                        "event": "healthy",
                        "source": "facility-semantic-receipt-ledger",
                        "mode": "resident",
                        "receipt_id": str(canonical["receipt_id"]),
                    },
                    f"{timestamp}:facility-ledger",
                )
            action_at = _timestamp(source.get("recorded_at"))
            exploration_id = str(source.get("exploration_id") or "").strip()
            if exploration_id:
                exploration_ids.add(exploration_id)
            if auth_fault:
                effect = source.get("effect_proof")
                binding = source.get("evidence_binding")
                actual = effect.get("actual") if isinstance(effect, Mapping) else None
                valid_auth_fault = bool(
                    isinstance(binding, Mapping)
                    and isinstance(effect, Mapping)
                    and isinstance(actual, Mapping)
                    and effect.get("kind") == "facility-authentication-fault"
                    and actual.get("auth_status") == "expired"
                    and actual.get("satisfied") is False
                    and str(source.get("intent_id") or "")
                    and str(source.get("intent_id") or "")
                    == str(binding.get("intent_id") or "")
                    == str(effect.get("intent_id") or "")
                    and exploration_id
                    == str(binding.get("exploration_id") or "")
                    == str(effect.get("exploration_id") or "")
                    and all(
                        re.fullmatch(r"[0-9a-f]{64}", str(effect.get(key) or ""))
                        is not None
                        for key in ("before_frame_sha256", "after_frame_sha256")
                    )
                    and effect.get("before_frame_sha256")
                    != effect.get("after_frame_sha256")
                )
                self.journal.append(
                    "facility",
                    {
                        "event": "fault",
                        "source": "facility-semantic-receipt-ledger",
                        "reason": (
                            "authentication-expired"
                            if valid_auth_fault
                            else "invalid authentication fault receipt"
                        ),
                        "receipt_id": str(source.get("receipt_id") or ""),
                        "intent_id": str(source.get("intent_id") or ""),
                        "exploration_id": exploration_id,
                    },
                    f"{timestamp}:facility-authentication-fault",
                )
                continue
            safety = source.get("safety_channel")
            no_input_replay = (
                canonical is not None and canonical.get("input_sent") is False
            )
            after_lifecycle = False
            lifecycle_fault = ""
            if trust_fault:
                payload = {
                    "source": "contestant-action-journal",
                    "reason": trust_fault,
                    "semantic_action": semantic or "unknown-mutating-action",
                }
                if post_lifecycle_mutation:
                    # Once the structured world lifecycle is active, every
                    # mutating observation must resolve to the daemon-owned
                    # ledger. Missing/illegal references mean the facility
                    # evidence boundary was breached and void the attempt.
                    self.journal.append(
                        "facility",
                        {"event": "fault", **payload},
                        f"{timestamp}:canonical-reference-fault",
                    )
                else:
                    self.journal.append(
                        "evidence_fault",
                        payload,
                        f"{timestamp}:canonical-reference-fault",
                    )
            elif (
                lifecycle_exploration_id
                and semantic not in PRE_LIFECYCLE_UNBOUND_COMMANDS
                and not no_input_replay
            ):
                if lifecycle_ambiguous or action_at is None:
                    lifecycle_fault = "ambiguous action/exploration lifecycle time"
                elif lifecycle_at is not None and action_at >= lifecycle_at:
                    after_lifecycle = True
                    if exploration_id != lifecycle_exploration_id:
                        lifecycle_fault = (
                            "missing or cross-exploration mutating action binding"
                        )
                    elif not isinstance(safety, Mapping) or (
                        safety.get("available") is not True
                        or safety.get("fresh") is not True
                    ):
                        lifecycle_fault = (
                            "missing or false mutating action safety channel"
                        )
            if lifecycle_fault:
                self.journal.append(
                    "facility",
                    {
                        "event": "fault",
                        "source": "contestant-action-journal",
                        "reason": lifecycle_fault,
                        "semantic_action": semantic or "unknown-mutating-action",
                    },
                    f"{timestamp}:lifecycle-fault",
                )
            if trust_fault:
                continue
            canonical_fault = ""
            effect = source.get("effect_proof")
            binding = source.get("evidence_binding")
            intent_id = str(source.get("intent_id") or "").strip()
            evidence_receipt = str(source.get("evidence_receipt") or "").strip()
            duration = source.get("duration_ms")
            monotonic_keys = (
                "sample_requested_monotonic_ns",
                "sample_received_monotonic_ns",
                "receipt_created_monotonic_ns",
            )
            monotonic = (
                [safety.get(key) for key in monotonic_keys]
                if isinstance(safety, Mapping)
                else []
            )
            safety_at = (
                _timestamp(safety.get("captured_at"))
                if isinstance(safety, Mapping)
                else None
            )
            if after_lifecycle and not lifecycle_fault and not no_input_replay:
                if source.get("verified_success") is not True:
                    canonical_fault = "explicit verified_success is absent"
                elif not intent_id or not evidence_receipt:
                    canonical_fault = "intent or lifecycle evidence receipt is absent"
                elif not isinstance(binding, Mapping) or not isinstance(effect, Mapping):
                    canonical_fault = "evidence binding or effect proof is absent"
                elif any(
                    str(container.get("exploration_id") or "").strip()
                    != lifecycle_exploration_id
                    for container in (binding, effect, safety)
                ):
                    canonical_fault = "effect proof crosses exploration identity"
                elif any(
                    str(container.get("intent_id") or "").strip() != intent_id
                    for container in (binding, effect, safety)
                ):
                    canonical_fault = "effect proof crosses action intent"
                elif any(
                    str(container.get("evidence_receipt") or "").strip()
                    != evidence_receipt
                    for container in (binding, effect, safety)
                ):
                    canonical_fault = "effect proof crosses lifecycle evidence receipt"
                elif (
                    safety.get("input_sent") is not True
                    or not str(safety.get("source") or "").strip()
                    or safety_at is None
                ):
                    canonical_fault = "safety source or captured_at is absent"
                elif len(monotonic) != 3 or any(
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                    for value in monotonic
                ):
                    canonical_fault = "safety monotonic timing evidence is absent"
                elif monotonic != sorted(monotonic):
                    canonical_fault = "safety monotonic timing evidence is out of order"
                elif action_at is None or safety_at > action_at or (
                    action_at - safety_at
                ).total_seconds() > 120:
                    canonical_fault = "safety capture is not fresh for the action receipt"
                elif not isinstance(duration, (int, float)) or isinstance(
                    duration, bool
                ) or float(duration) < 0:
                    canonical_fault = "facility duration is absent"
                elif not str(effect.get("kind") or "").strip():
                    canonical_fault = "structured effect proof kind is absent"
                elif not isinstance(effect.get("expected"), Mapping) or not isinstance(
                    effect.get("actual"), Mapping
                ):
                    canonical_fault = "structured expected/actual effect proof is absent"
                elif "kind" in effect["actual"]:
                    canonical_fault = "effect actual improperly copies producer kind"
                elif not self._effect_predicate_matches(effect):
                    canonical_fault = "semantic postcondition predicate is not proved"
                elif any(
                    not re.fullmatch(r"[0-9a-f]{64}", str(effect.get(key) or ""))
                    for key in ("before_frame_sha256", "after_frame_sha256")
                ) or effect.get("before_frame_sha256") == effect.get(
                    "after_frame_sha256"
                ):
                    canonical_fault = "effect proof lacks a measured frame transition"
            if canonical_fault:
                self.journal.append(
                    "evidence_fault",
                    {
                        "source": "facility-semantic-receipt-ledger",
                        "reason": canonical_fault,
                        "semantic_action": semantic or "unknown-mutating-action",
                    },
                    f"{timestamp}:canonical-fault",
                )
            if recovered_effect and not trust_fault and not lifecycle_fault and not canonical_fault:
                self.journal.append(
                    "facility",
                    {
                        "event": "healthy",
                        "source": "facility-semantic-receipt-ledger",
                        "mode": "confirmed-effect-recovered-no-input",
                        "receipt_id": str(source.get("receipt_id") or ""),
                        "intent_id": intent_id,
                        "exploration_id": exploration_id,
                        "evidence_receipt": evidence_receipt,
                        "original_input_sent": source.get("input_sent") is True,
                        "recovery_input_sent": False,
                        "canonical_receipt_ref": dict(reference),
                    },
                    f"{timestamp}:confirmed-effect-recovered",
                )
            if (
                after_lifecycle
                and not no_input_replay
                and not recovered_effect
                and not lifecycle_fault
                and not canonical_fault
                and exploration_id
                and isinstance(safety, Mapping)
                and isinstance(safety.get("available"), bool)
                and isinstance(safety.get("fresh"), bool)
                and semantic
            ):
                self.journal.append(
                    "action",
                    {
                        "semantic_action": semantic,
                        "verified_success": source.get("verified_success") is True,
                        "exploration_id": exploration_id,
                        "intent_id": intent_id,
                        "evidence_receipt": evidence_receipt,
                        "evidence_binding": dict(binding),
                        "effect_proof": dict(effect),
                        "facility_duration_ms": float(duration),
                        "safety_channel": dict(safety),
                        "operator": str(
                            row.get("operator") or row.get("operator_id") or ""
                        ).strip()
                        or None,
                    },
                    timestamp,
                )

            battle = source.get("battle_after")
            if (
                lifecycle_fault
                or canonical_fault
                or no_input_replay
                or recovered_effect
                or not exploration_id
                or not isinstance(battle, Mapping)
                or battle.get("loaded") is not True
            ):
                continue
            fields = {key: battle[key] for key in HUD_FIELDS if key in battle}
            if not isinstance(fields.get("remaining_life"), int) or not isinstance(
                fields.get("leaked_enemies"), int
            ):
                continue
            self.journal.append("hud", {"fields": fields}, f"{timestamp}:hud")
            boundary = source.get("battle_boundary")
            if (
                isinstance(boundary, Mapping)
                and boundary.get("phase") in {"start", "end"}
                and str(boundary.get("battle_id", "")).strip()
            ):
                self.journal.append(
                    "battle_boundary",
                    {
                        "phase": boundary["phase"],
                        "battle_id": str(boundary["battle_id"]).strip(),
                        "fields": {
                            "remaining_life": fields["remaining_life"],
                            "leaked_enemies": fields["leaked_enemies"],
                        },
                    },
                    f"{timestamp}:boundary",
                )
        if lifecycle_exploration_id and lifecycle_at is not None:
            for receipt in canonical_receipts.values():
                receipt_id = str(receipt.get("receipt_id") or "")
                recorded_at = _timestamp(receipt.get("recorded_at"))
                if (
                    receipt.get("input_sent") is True
                    and recorded_at is not None
                    and recorded_at >= lifecycle_at
                    and receipt_id not in used_receipts
                ):
                    self.journal.append(
                        "facility",
                        {
                            "event": "fault",
                            "source": "facility-semantic-receipt-ledger",
                            "reason": "daemon input receipt is absent from contestant references",
                            "receipt_id": receipt_id,
                        },
                        f"facility-semantic-unreferenced:{receipt_id}",
                    )
        return exploration_ids

    def _append_world_state(self, exploration_ids: set[str]) -> None:
        if not self.world_state_path.exists():
            return
        try:
            world = _read_object(self.world_state_path)
        except (OSError, ValueError, json.JSONDecodeError):
            return
        if world.get("schema") != "arknights-run-world-state.v2" or world.get(
            "identity_status"
        ) not in {"active", "finished"}:
            return
        if (
            len(exploration_ids) != 1
            or str(world.get("exploration_id") or "").strip()
            not in exploration_ids
        ):
            return
        members = world.get("current_squad")
        if (
            world.get("current_squad_complete") is True
            and isinstance(members, list)
            and members
        ):
            safe_members = []
            for member in members:
                member_evidence = (
                    member.get("evidence", {})
                    if isinstance(member, Mapping)
                    else {}
                )
                if (
                    not isinstance(member, Mapping)
                    or not str(member.get("operator_id", "")).strip()
                    or not str(member.get("name", "")).strip()
                    or not isinstance(member_evidence, Mapping)
                    or not all(
                        str(member_evidence.get(key, "")).strip()
                        for key in ("kind", "source", "receipt_id")
                    )
                ):
                    return
                safe_evidence = {
                    key: member_evidence[key]
                    for key in (
                        "kind",
                        "source",
                        "receipt_id",
                        "captured_at",
                        "frame_sha256",
                    )
                    if key in member_evidence
                }
                safe_members.append(
                    {
                        "operator_id": str(member["operator_id"]),
                        "name": str(member["name"]),
                        "evidence": safe_evidence,
                    }
                )
            self.journal.append(
                "squad",
                {"members": safe_members},
                f"world:{world.get('version', 0)}:squad",
            )
        floor = world.get("floor")
        if isinstance(floor, Mapping) and set(floor) == {"floor", "evidence"}:
            value = floor.get("floor")
            evidence = floor.get("evidence")
        else:
            value, evidence = None, None
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
            and isinstance(evidence, Mapping)
            and all(
                str(evidence.get(key, "")).strip()
                for key in ("kind", "source", "receipt_id")
            )
        ):
            self.journal.append(
                "floor",
                {
                    "floor": value,
                    "source": "run-world-state",
                    "exploration_id": world["exploration_id"],
                    "evidence": {
                        key: evidence[key]
                        for key in (
                            "kind",
                            "source",
                            "receipt_id",
                            "captured_at",
                            "frame_sha256",
                        )
                        if key in evidence
                    },
                },
                f"world:{world.get('version', 0)}:floor",
            )

    def _append_summary(self, summary: Mapping[str, Any]) -> None:
        self._validate_run(summary)
        restarts = summary.get("technical_restarts", 0)
        if not isinstance(restarts, int) or isinstance(restarts, bool) or restarts < 0:
            raise ValueError("summary technical restart count is invalid")
        for index in range(restarts):
            self.journal.append(
                "facility",
                {
                    "event": "restart",
                    "source": "tournament-state",
                    "restart_number": index + 1,
                },
                f"tournament:technical-restart:{index + 1}",
            )
        outcome = {"completed": "clear", "failed": "loss"}.get(summary.get("outcome"))
        if outcome is not None:
            self.journal.append(
                "ledger",
                {
                    "outcome": outcome,
                    "identity": self.identity,
                    "source": "tournament-ledger",
                },
                "tournament:ledger",
            )

    def _append_settlement(
        self, receipt: Mapping[str, Any], exploration_ids: set[str]
    ) -> bool:
        ocr = receipt.get("ocr")
        observed_at = receipt.get("observed_at")
        source_sha256 = receipt.get("source_sha256")
        exact_keys = {
            "schema",
            "identity",
            "exploration_id",
            "observed_at",
            "outcome",
            "source_sha256",
            "ocr",
        }
        if (
            set(receipt) != exact_keys
            or len(exploration_ids) != 1
            or receipt.get("schema") != SETTLEMENT_SCHEMA
            or not _same_identity(receipt.get("identity", {}), self.identity)
            or str(receipt.get("exploration_id") or "").strip()
            != next(iter(exploration_ids), "")
            or receipt.get("outcome") not in {"clear", "loss"}
            or not _is_timestamp(observed_at)
            or not isinstance(source_sha256, str)
            or len(source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in source_sha256)
            or not isinstance(ocr, Mapping)
            or set(ocr)
            != {
                "verified",
                "text",
                "outcome",
                "source_sha256",
                "source",
                "confidence",
            }
            or ocr.get("verified") is not True
            or not str(ocr.get("text", "")).strip()
            or ocr.get("outcome") != receipt.get("outcome")
            or ocr.get("source_sha256") != source_sha256
            or ocr.get("source") != "resident-rapidocr-frame"
            or not isinstance(ocr.get("confidence"), (int, float))
            or isinstance(ocr.get("confidence"), bool)
            or float(ocr["confidence"]) < 0.9
        ):
            return False
        self.journal.append(
            "settlement",
            {
                "outcome": receipt["outcome"],
                "identity": self.identity,
                "exploration_id": receipt["exploration_id"],
                "observed_at": observed_at,
                "source_sha256": source_sha256,
                "ocr": {
                    "verified": True,
                    "text": str(ocr["text"]).strip(),
                    "outcome": receipt["outcome"],
                    "source_sha256": source_sha256,
                    "source": "resident-rapidocr-frame",
                    "confidence": float(ocr["confidence"]),
                },
            },
            observed_at,
        )
        return True

    def _append_recordings(self, state: Mapping[str, Any]) -> None:
        roots = state["expected_recording_roots"]
        receipts = state["recordings"]
        if any(
            root not in receipts
            or receipts[root].get("stopped_cleanly") is not True
            or not receipts[root].get("completed_files")
            for root in roots
        ):
            return
        files: list[str] = []
        for root in roots:
            root_path = Path(root)
            for value in receipts[root]["completed_files"]:
                path = Path(value)
                if not self._recording_belongs_to_root(path, root_path):
                    return
                files.append(value)
        if len(files) != len(set(files)) or not files:
            return
        paths = [Path(value) for value in files]
        if any(not path.is_file() for path in paths):
            return
        count = len(paths)
        for index, path in enumerate(paths, 1):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.journal.append(
                "recording",
                {
                    "path": str(path.resolve()),
                    "sha256": digest,
                    "segment": index,
                    "segment_count": count,
                    "complete": True,
                    "identity": self.identity,
                },
                f"recording:{index}:{digest}",
            )

    @staticmethod
    def _recording_belongs_to_root(path: Path, root: Path) -> bool:
        path, root = path.resolve(), root.resolve()
        if path.parent != root.parent or path.suffix.casefold() != ".mp4":
            return False
        return path == root or path.name.startswith(f"{root.stem}.part-")


def update_slot_readiness(slot_dir: Path | str) -> dict[str, Any]:
    """Compile readiness from the exact intact certificate files in a slot."""
    root = Path(slot_dir)
    paths = sorted(root.glob("*/certificate.json"))
    certificates = [_read_object(path) for path in paths]
    report = readiness_report(certificates, paths)
    from .training_supervisor import (
        TrainingSupervisionLedger,
        training_supervision_readiness,
    )

    supervision = training_supervision_readiness(
        TrainingSupervisionLedger(root / "supervision.jsonl"),
        paths,
    )
    report["supervision"] = supervision
    report["ready"] = bool(report.get("ready") and supervision["ready"])
    report["starts_match"] = False
    atomic_json(root / "readiness.json", report)
    return report
