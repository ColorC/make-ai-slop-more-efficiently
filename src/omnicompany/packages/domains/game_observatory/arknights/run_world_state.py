# [OMNI] origin=codex created_by=codex intent="maintain one versioned evidence-bound Arknights exploration world state"
"""Versioned, contamination-resistant state for one physical IS exploration.

The tournament ledger run number is intentionally absent.  A physical
exploration may span many harness runs, and the same ledger number may be
reused after a restart.  Identity is created or attached only by an explicit
semantic lifecycle receipt.
"""

from __future__ import annotations

import json
import os
import copy
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from omnicompany.core.pycompat import StrEnum
from pathlib import Path
from typing import Any, Iterable


class ExplorationIdentityStatus(StrEnum):
    UNKNOWN = "identity_unknown"
    ACTIVE = "active"
    FINISHED = "finished"


class OperatorEvidenceKind(StrEnum):
    SQUAD_CONFIRMED = "squad_confirmed"
    RECRUITMENT_CONFIRMED = "recruitment_confirmed"
    CANDIDATE_SCREEN = "candidate_screen"
    HISTORY_ONLY = "history_only"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: str
    source: str
    receipt_id: str
    captured_at: str | None = None
    frame_sha256: str | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.source.strip() or not self.receipt_id.strip():
            raise ValueError("evidence kind, source, and receipt_id must be non-empty")


@dataclass(frozen=True, slots=True)
class OperatorFact:
    operator_id: str
    name: str
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not self.operator_id.strip() or not self.name.strip():
            raise ValueError("operator_id and name must be non-empty")


@dataclass(frozen=True, slots=True)
class CollectibleFact:
    item_id: str
    name: str
    rarity: int | None
    usage: str | None
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not self.item_id.strip() or not self.name.strip():
            raise ValueError("collectible item_id and name must be non-empty")


@dataclass(frozen=True, slots=True)
class ResourceFact:
    key: str
    value: int | float
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not self.key.strip() or isinstance(self.value, bool):
            raise ValueError("resource key and numeric value are required")


@dataclass(frozen=True, slots=True)
class FloorFact:
    floor: int
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        if (
            not isinstance(self.floor, int)
            or isinstance(self.floor, bool)
            or self.floor < 0
        ):
            raise ValueError("floor requires an explicit non-negative integer")


@dataclass(frozen=True, slots=True)
class LoadedStageFact:
    name: str
    mode: str
    level_ids: tuple[str, ...]
    stage_ids: tuple[str, ...]
    width: int | None
    height: int | None
    evidence: EvidenceRef

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.level_ids:
            raise ValueError("loaded stage requires name and at least one level id")
        if any(not value.strip() for value in self.level_ids):
            raise ValueError("loaded stage level ids must be non-empty")

    @property
    def identity(self) -> tuple[Any, ...]:
        return (
            self.name,
            self.mode,
            self.level_ids,
            self.stage_ids,
            self.width,
            self.height,
        )


@dataclass(slots=True)
class RunWorldState:
    schema: str = "arknights-run-world-state.v2"
    exploration_id: str | None = None
    identity_status: ExplorationIdentityStatus = ExplorationIdentityStatus.UNKNOWN
    version: int = 0
    current_squad: dict[str, OperatorFact] = field(default_factory=dict)
    current_squad_complete: bool = False
    available_candidates: dict[str, OperatorFact] = field(default_factory=dict)
    available_candidates_complete: bool = False
    seen_history: dict[str, OperatorFact] = field(default_factory=dict)
    acquired_collectibles: dict[str, CollectibleFact] = field(default_factory=dict)
    resources: dict[str, ResourceFact] = field(default_factory=dict)
    floor: FloorFact | None = None
    loaded_stage: LoadedStageFact | None = None
    stage_history: list[LoadedStageFact] = field(default_factory=list)
    lifecycle_evidence: EvidenceRef | None = None
    updated_at_ns: int = 0

    def validate(self) -> None:
        if self.identity_status is ExplorationIdentityStatus.ACTIVE and not self.exploration_id:
            raise ValueError("active exploration requires exploration_id")
        if self.identity_status is ExplorationIdentityStatus.UNKNOWN:
            if self.exploration_id is not None:
                raise ValueError("unknown identity cannot retain exploration_id")
            if (
                self.current_squad
                or self.available_candidates
                or self.acquired_collectibles
                or self.resources
                or self.floor is not None
                or self.loaded_stage is not None
            ):
                raise ValueError("unknown identity cannot expose current-run facts")
        overlap = set(self.current_squad) & set(self.available_candidates)
        if overlap:
            raise ValueError(f"squad and candidates overlap: {sorted(overlap)}")

    def to_json(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "exploration_id": self.exploration_id,
            "identity_status": self.identity_status.value,
            "version": self.version,
            "current_squad": [
                asdict(self.current_squad[key]) for key in sorted(self.current_squad)
            ],
            "current_squad_complete": self.current_squad_complete,
            "available_candidates": [
                asdict(self.available_candidates[key])
                for key in sorted(self.available_candidates)
            ],
            "available_candidates_complete": self.available_candidates_complete,
            "seen_history": [
                asdict(self.seen_history[key]) for key in sorted(self.seen_history)
            ],
            "acquired_collectibles": [
                asdict(self.acquired_collectibles[key])
                for key in sorted(self.acquired_collectibles)
            ],
            "resources": [
                asdict(self.resources[key]) for key in sorted(self.resources)
            ],
            "floor": None if self.floor is None else asdict(self.floor),
            "loaded_stage": (
                None if self.loaded_stage is None else asdict(self.loaded_stage)
            ),
            "stage_history": [asdict(stage) for stage in self.stage_history],
            "lifecycle_evidence": (
                None if self.lifecycle_evidence is None else asdict(self.lifecycle_evidence)
            ),
            "updated_at_ns": self.updated_at_ns,
        }

    @classmethod
    def from_json(cls, value: dict[str, Any]) -> "RunWorldState":
        def facts(name: str) -> dict[str, OperatorFact]:
            result: dict[str, OperatorFact] = {}
            for row in value.get(name) or []:
                evidence = EvidenceRef(**row["evidence"])
                fact = OperatorFact(
                    operator_id=str(row["operator_id"]),
                    name=str(row["name"]),
                    evidence=evidence,
                )
                if fact.operator_id in result:
                    raise ValueError(f"duplicate {name} operator: {fact.operator_id}")
                result[fact.operator_id] = fact
            return result

        def collectibles() -> dict[str, CollectibleFact]:
            result: dict[str, CollectibleFact] = {}
            for row in value.get("acquired_collectibles") or []:
                evidence = EvidenceRef(**row["evidence"])
                fact = CollectibleFact(
                    item_id=str(row["item_id"]),
                    name=str(row["name"]),
                    rarity=(None if row.get("rarity") is None else int(row["rarity"])),
                    usage=row.get("usage"),
                    evidence=evidence,
                )
                if fact.item_id in result:
                    raise ValueError(f"duplicate collectible: {fact.item_id}")
                result[fact.item_id] = fact
            return result

        def resources() -> dict[str, ResourceFact]:
            result: dict[str, ResourceFact] = {}
            for row in value.get("resources") or []:
                fact = ResourceFact(
                    key=str(row["key"]),
                    value=row["value"],
                    evidence=EvidenceRef(**row["evidence"]),
                )
                if fact.key in result:
                    raise ValueError(f"duplicate resource: {fact.key}")
                result[fact.key] = fact
            return result

        def floor(row: dict[str, Any] | None) -> FloorFact | None:
            if row is None:
                return None
            if not isinstance(row, dict) or set(row) != {"floor", "evidence"}:
                raise ValueError("floor fact has an unsupported shape")
            return FloorFact(
                floor=row["floor"],
                evidence=EvidenceRef(**row["evidence"]),
            )

        def stage(row: dict[str, Any] | None) -> LoadedStageFact | None:
            if row is None:
                return None
            return LoadedStageFact(
                name=str(row["name"]),
                mode=str(row.get("mode") or "unknown"),
                level_ids=tuple(str(item) for item in row.get("level_ids") or []),
                stage_ids=tuple(str(item) for item in row.get("stage_ids") or []),
                width=None if row.get("width") is None else int(row["width"]),
                height=None if row.get("height") is None else int(row["height"]),
                evidence=EvidenceRef(**row["evidence"]),
            )

        lifecycle = value.get("lifecycle_evidence")
        state = cls(
            schema=str(value.get("schema") or ""),
            exploration_id=value.get("exploration_id"),
            identity_status=ExplorationIdentityStatus(
                str(value.get("identity_status") or ExplorationIdentityStatus.UNKNOWN.value)
            ),
            version=int(value.get("version") or 0),
            current_squad=facts("current_squad"),
            current_squad_complete=bool(value.get("current_squad_complete", False)),
            available_candidates=facts("available_candidates"),
            available_candidates_complete=bool(
                value.get("available_candidates_complete", False)
            ),
            seen_history=facts("seen_history"),
            acquired_collectibles=collectibles(),
            resources=resources(),
            floor=floor(value.get("floor")),
            loaded_stage=stage(value.get("loaded_stage")),
            stage_history=[
                item
                for item in (stage(row) for row in value.get("stage_history") or [])
                if item is not None
            ],
            lifecycle_evidence=(None if lifecycle is None else EvidenceRef(**lifecycle)),
            updated_at_ns=int(value.get("updated_at_ns") or 0),
        )
        if state.schema == "arknights-run-world-state.v1":
            state.schema = "arknights-run-world-state.v2"
        if state.schema != "arknights-run-world-state.v2":
            raise ValueError(f"unsupported run world state schema: {state.schema}")
        state.validate()
        return state


class RunWorldStateStore:
    """Atomic lifecycle and operator-set updates for one current exploration."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cached: RunWorldState | None = None

    def read(self) -> RunWorldState:
        with self._lock:
            if self._cached is not None:
                return copy.deepcopy(self._cached)
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._cached = RunWorldState()
            else:
                self._cached = RunWorldState.from_json(value)
            return copy.deepcopy(self._cached)

    def _write(self, state: RunWorldState) -> RunWorldState:
        state.version += 1
        state.updated_at_ns = time.time_ns()
        state.validate()
        temporary = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(state.to_json(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        self._cached = copy.deepcopy(state)
        return copy.deepcopy(state)

    def begin_new(self, evidence: EvidenceRef) -> RunWorldState:
        """Create a new physical exploration after semantic entry is verified."""

        with self._lock:
            state = RunWorldState(
                exploration_id=str(uuid.uuid4()),
                identity_status=ExplorationIdentityStatus.ACTIVE,
                lifecycle_evidence=evidence,
            )
            return self._write(state)

    def attach_existing(self, exploration_id: str, evidence: EvidenceRef) -> RunWorldState:
        """Explicit recovery path; never infer identity from a ledger number."""

        uuid.UUID(exploration_id)
        with self._lock:
            state = self.read()
            if state.exploration_id not in {None, exploration_id}:
                raise ValueError("cannot attach a different physical exploration")
            state.exploration_id = exploration_id
            state.identity_status = ExplorationIdentityStatus.ACTIVE
            state.lifecycle_evidence = evidence
            return self._write(state)

    def lose_identity(self, evidence: EvidenceRef) -> RunWorldState:
        """Fail closed while retaining history for diagnostics only."""

        with self._lock:
            previous = self.read()
            history = dict(previous.seen_history)
            history.update(previous.current_squad)
            history.update(previous.available_candidates)
            state = RunWorldState(
                exploration_id=None,
                identity_status=ExplorationIdentityStatus.UNKNOWN,
                seen_history=history,
                stage_history=list(previous.stage_history),
                lifecycle_evidence=evidence,
            )
            return self._write(state)

    def finish(self, evidence: EvidenceRef) -> RunWorldState:
        with self._lock:
            state = self.read()
            if state.identity_status is not ExplorationIdentityStatus.ACTIVE:
                raise ValueError("only an active exploration can finish")
            state.identity_status = ExplorationIdentityStatus.FINISHED
            state.lifecycle_evidence = evidence
            return self._write(state)

    def set_candidates(
        self, operators: Iterable[OperatorFact], *, evidence_kind: OperatorEvidenceKind
    ) -> RunWorldState:
        if evidence_kind is not OperatorEvidenceKind.CANDIDATE_SCREEN:
            raise ValueError("candidate set requires candidate-screen evidence")
        candidates = self._unique(operators)
        with self._lock:
            state = self._active()
            if set(candidates) & set(state.current_squad):
                raise ValueError("current squad member cannot remain a candidate")
            state.available_candidates = candidates
            state.available_candidates_complete = True
            state.seen_history.update(candidates)
            return self._write(state)

    def confirm_squad(
        self, operators: Iterable[OperatorFact], *, evidence_kind: OperatorEvidenceKind
    ) -> RunWorldState:
        if evidence_kind is not OperatorEvidenceKind.SQUAD_CONFIRMED:
            raise ValueError("squad replacement requires confirmed-squad evidence")
        squad = self._unique(operators)
        with self._lock:
            state = self._active()
            state.current_squad = squad
            state.current_squad_complete = True
            state.available_candidates = {
                key: value
                for key, value in state.available_candidates.items()
                if key not in squad
            }
            state.seen_history.update(squad)
            return self._write(state)

    def confirm_recruitment(
        self, operator: OperatorFact, *, evidence_kind: OperatorEvidenceKind
    ) -> RunWorldState:
        if evidence_kind is not OperatorEvidenceKind.RECRUITMENT_CONFIRMED:
            raise ValueError("recruitment requires confirmed action receipt")
        with self._lock:
            state = self._active()
            state.current_squad[operator.operator_id] = operator
            state.available_candidates.pop(operator.operator_id, None)
            state.seen_history[operator.operator_id] = operator
            return self._write(state)

    def observe_history(self, operators: Iterable[OperatorFact]) -> RunWorldState:
        with self._lock:
            state = self.read()
            state.seen_history.update(self._unique(operators))
            return self._write(state)

    def apply_semantic_observation(
        self,
        *,
        operators: Iterable[OperatorFact] = (),
        candidates: Iterable[OperatorFact] | None = None,
        candidates_complete: bool = False,
        collectibles: Iterable[CollectibleFact] = (),
        loaded_stage: LoadedStageFact | None = None,
    ) -> RunWorldState:
        """Atomically apply only evidence-safe semantic projections.

        Merely seeing an operator name can update history, never the squad.
        Candidates require the caller to have proved a recruitment-card screen.
        Current-run stage and collectible facts fail closed without an active
        physical exploration identity. Unchanged observations do not hit disk.
        """

        observed = self._unique(operators)
        candidate_rows = None if candidates is None else self._unique(candidates)
        collectible_rows = {row.item_id: row for row in collectibles}
        with self._lock:
            state = self.read()
            changed = False
            for key, row in observed.items():
                if key not in state.seen_history:
                    state.seen_history[key] = row
                    changed = True
            if state.identity_status is ExplorationIdentityStatus.ACTIVE:
                if candidate_rows is not None:
                    # A live candidate-screen projection can include stale card
                    # labels from the preceding reveal/selection transition.
                    # Confirmed squad membership is stronger evidence, so merge
                    # monotonically by dropping those rows instead of rejecting
                    # the entire atomic observation (which would also discard
                    # valid stage/collectible facts).
                    candidate_rows = {
                        key: value
                        for key, value in candidate_rows.items()
                        if key not in state.current_squad
                    }
                    if (
                        set(candidate_rows) != set(state.available_candidates)
                        or state.available_candidates_complete != candidates_complete
                    ):
                        state.available_candidates = candidate_rows
                        state.available_candidates_complete = candidates_complete
                        state.seen_history.update(candidate_rows)
                        changed = True
                for key, row in collectible_rows.items():
                    if key not in state.acquired_collectibles:
                        state.acquired_collectibles[key] = row
                        changed = True
                if loaded_stage is not None and (
                    state.loaded_stage is None
                    or state.loaded_stage.identity != loaded_stage.identity
                ):
                    state.loaded_stage = loaded_stage
                    if not state.stage_history or (
                        state.stage_history[-1].identity != loaded_stage.identity
                    ):
                        state.stage_history.append(loaded_stage)
                    changed = True
            return self._write(state) if changed else state

    def set_resources(self, resources: Iterable[ResourceFact]) -> RunWorldState:
        rows = {row.key: row for row in resources}
        with self._lock:
            state = self._active()
            if {
                key: row.value for key, row in rows.items()
            } == {key: row.value for key, row in state.resources.items()}:
                return state
            state.resources = rows
            return self._write(state)

    def set_floor(self, floor: FloorFact) -> RunWorldState:
        """Persist one explicit, evidence-bound floor transition.

        Floors are current-exploration facts.  They cannot be supplied while
        identity is unknown, inferred from stage history, or moved backwards.
        Repeating the exact same fact is idempotent.
        """

        if not isinstance(floor, FloorFact):
            raise TypeError("floor transition requires FloorFact")
        with self._lock:
            state = self._active()
            if state.floor is not None:
                if floor.floor < state.floor.floor:
                    raise ValueError("floor transition cannot move backwards")
                if floor == state.floor:
                    return state
            state.floor = floor
            return self._write(state)

    def _active(self) -> RunWorldState:
        state = self.read()
        if state.identity_status is not ExplorationIdentityStatus.ACTIVE:
            raise ValueError("physical exploration identity is not active")
        return state

    @staticmethod
    def _unique(operators: Iterable[OperatorFact]) -> dict[str, OperatorFact]:
        result: dict[str, OperatorFact] = {}
        for operator in operators:
            # A single OCR frame can expose the same operator in both a card
            # and a selected-row surface.  That is one observed fact, not a
            # reason to make the entire gameplay state unreadable.
            result.setdefault(operator.operator_id, operator)
        return result
