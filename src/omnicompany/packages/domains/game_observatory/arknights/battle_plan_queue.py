# [OMNI] origin=codex created_by=codex intent="validate and sequence evidenced Arknights battle plans"
"""Offline, deterministic battle-plan validation and queueing.

Canonical wire encoding is the established ``{"deployments": [...]}`` object.
Each deployment has the historical ``operator``/``name``, ``col``, ``row``,
``facing``/``direction``, and optional ``at_seconds`` keys.  It normalizes in
array order to stable IDs ``deployment-0001``, ``deployment-0002``, etc.; an
explicit unique ``step_id`` is retained.  The enriched ``{"steps": [...]}``
form adds predicates and skill/retreat/speed/pause actions.  It is accepted as
an extension, never as a replacement for deployments.  Parsing is deterministic:
operator/direction aliases normalize to their canonical names and ``at_seconds``
normalizes to a ``time_gte`` predicate.

This module deliberately models semantic intent only.  It has no screen,
coordinate, or input implementation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from omnicompany.core.pycompat import StrEnum
from typing import Any, Mapping


class PlanSchemaError(ValueError):
    """The submitted JSON value is not a canonical battle plan."""


class QueueState(StrEnum):
    READY = "ready"
    WAITING = "waiting"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    GUARD_STOPPED = "guard_stopped"
    DECISION_REQUIRED = "decision_required"
    LIVE_BATTLE = "live_battle"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class BattleFacts:
    """Explicit evidence used by predicates; absent or stale facts fail closed."""

    cost: int | None = None
    kills: int | None = None
    wave: int | None = None
    hp_pct: float | None = None
    leaked: int | None = None
    time: float | None = None
    evidence_fresh: bool = False


@dataclass(frozen=True, slots=True)
class Predicate:
    cost_gte: int | None = None
    kills_min: int | None = None
    kills_max: int | None = None
    kills_in: frozenset[int] | None = None
    wave: int | None = None
    hp_op: str | None = None
    hp_value: float | None = None
    leaked_gte: int | None = None
    time_gte: float | None = None


@dataclass(frozen=True, slots=True)
class BattleStep:
    step_id: str
    action: str
    payload: Mapping[str, Any]
    when: Predicate = Predicate()


@dataclass(frozen=True, slots=True)
class BattlePlan:
    steps: tuple[BattleStep, ...]


@dataclass(frozen=True, slots=True)
class QueueResult:
    state: QueueState
    step: BattleStep | None = None
    detail: str = ""


def _canonical_deployment(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an established deployment and return its deterministic form."""
    allowed = {"step_id", "operator", "name", "col", "row", "facing", "direction", "at_seconds"}
    if set(payload) - allowed:
        raise PlanSchemaError("deploy payload has unknown keys")
    operator = payload.get("operator", payload.get("name"))
    direction = payload.get("facing", payload.get("direction"))
    if not isinstance(operator, str) or not operator.strip() or ("operator" in payload and "name" in payload):
        raise PlanSchemaError("deploy requires exactly one operator/name")
    if not isinstance(payload.get("col"), int) or isinstance(payload.get("col"), bool) or not isinstance(payload.get("row"), int) or isinstance(payload.get("row"), bool):
        raise PlanSchemaError("deploy col and row must be integers")
    if direction not in {"left", "right", "up", "down"} or ("facing" in payload and "direction" in payload):
        raise PlanSchemaError("deploy requires exactly one four-way facing/direction")
    result = {"operator": operator.strip(), "col": payload["col"], "row": payload["row"], "facing": direction}
    if "at_seconds" in payload:
        result["at_seconds"] = _number(payload["at_seconds"], "at_seconds")
    return result


def _number(value: Any, name: str, *, integer: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlanSchemaError(f"{name} must be a number")
    if not math.isfinite(value):
        raise PlanSchemaError(f"{name} must be finite")
    if integer and not isinstance(value, int):
        raise PlanSchemaError(f"{name} must be an integer")
    if value < 0:
        raise PlanSchemaError(f"{name} must be non-negative")
    return value


def _object(value: Any, name: str, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or set(value) - keys:
        raise PlanSchemaError(f"{name} has unknown keys or is not an object")
    return value


def _parse_when(raw: Any) -> Predicate:
    if raw is None:
        return Predicate()
    data = _object(raw, "when", {"cost_gte", "kills", "wave", "hp_pct", "leaked_gte", "time_gte"})
    cost = _number(data["cost_gte"], "cost_gte", integer=True) if "cost_gte" in data else None
    wave = _number(data["wave"], "wave", integer=True) if "wave" in data else None
    leaked = _number(data["leaked_gte"], "leaked_gte", integer=True) if "leaked_gte" in data else None
    elapsed = _number(data["time_gte"], "time_gte") if "time_gte" in data else None
    kills_min = kills_max = None
    kills_in = None
    if "kills" in data:
        kills = _object(data["kills"], "kills", {"min", "max", "in"})
        if not kills:
            raise PlanSchemaError("kills cannot be empty")
        kills_min = _number(kills["min"], "kills.min", integer=True) if "min" in kills else None
        kills_max = _number(kills["max"], "kills.max", integer=True) if "max" in kills else None
        if kills_min is not None and kills_max is not None and kills_min > kills_max:
            raise PlanSchemaError("kills min exceeds max")
        if "in" in kills:
            values = kills["in"]
            if not isinstance(values, list) or not values:
                raise PlanSchemaError("kills.in must be a non-empty array")
            parsed = frozenset(_number(v, "kills.in", integer=True) for v in values)
            if len(parsed) != len(values):
                raise PlanSchemaError("kills.in cannot contain duplicates")
            if (kills_min is not None and min(parsed) < kills_min) or (kills_max is not None and max(parsed) > kills_max):
                raise PlanSchemaError("kills.in contradicts kills range")
            kills_in = parsed
    hp_op = hp_value = None
    if "hp_pct" in data:
        hp = _object(data["hp_pct"], "hp_pct", {"op", "value"})
        if set(hp) != {"op", "value"} or hp["op"] not in {">=", ">", "<=", "<", "=="}:
            raise PlanSchemaError("hp_pct requires a known comparison operator and value")
        hp_op = str(hp["op"])
        hp_value = _number(hp["value"], "hp_pct.value")
        if hp_value > 100:
            raise PlanSchemaError("hp_pct.value cannot exceed 100")
    return Predicate(cost, kills_min, kills_max, kills_in, wave, hp_op, hp_value, leaked, elapsed)


def parse_battle_plan(value: Any) -> BattlePlan:
    """Parse deployments compatibility encoding or the enriched steps encoding."""
    root = _object(value, "plan", {"deployments", "steps"})
    if set(root) == {"deployments"}:
        deployments = root["deployments"]
        if not isinstance(deployments, list):
            raise PlanSchemaError("deployments must be an array")
        seen: set[str] = set()
        steps: list[BattleStep] = []
        for index, raw in enumerate(deployments):
            if not isinstance(raw, dict):
                raise PlanSchemaError(f"deployments[{index}] must be an object")
            payload = _canonical_deployment(raw)
            step_id = raw.get("step_id", f"deployment-{index + 1:04d}")
            if not isinstance(step_id, str) or not step_id.strip() or step_id in seen:
                raise PlanSchemaError("deployment step_id must be non-blank and unique")
            seen.add(step_id)
            when = Predicate(time_gte=payload.pop("at_seconds")) if "at_seconds" in payload else Predicate()
            steps.append(BattleStep(step_id, "deploy", payload, when))
        return BattlePlan(tuple(steps))
    if set(root) != {"steps"} or not isinstance(root["steps"], list):
        raise PlanSchemaError("plan requires deployments or steps array")
    seen: set[str] = set()
    steps: list[BattleStep] = []
    for index, raw in enumerate(root["steps"]):
        step = _object(raw, f"steps[{index}]", {"step_id", "action", "when"})
        if "step_id" not in step or "action" not in step or set(step) - {"step_id", "action", "when"}:
            raise PlanSchemaError("each step requires step_id and action")
        step_id = step["step_id"]
        if not isinstance(step_id, str) or not step_id.strip() or step_id in seen:
            raise PlanSchemaError("step_id must be non-blank and unique")
        seen.add(step_id)
        action = _object(step["action"], "action", {"deploy", "skill", "retreat", "speed", "pause"})
        if len(action) != 1:
            raise PlanSchemaError("action must contain exactly one semantic payload")
        name, payload = next(iter(action.items()))
        if not isinstance(payload, dict):
            raise PlanSchemaError("action payload must be an object")
        if name == "deploy":
            payload = _canonical_deployment(payload)
            if "at_seconds" in payload:
                at_seconds = payload.pop("at_seconds")
                existing = _parse_when(step.get("when"))
                if existing.time_gte is not None:
                    raise PlanSchemaError("deploy cannot specify both when.time_gte and at_seconds")
                when = Predicate(existing.cost_gte, existing.kills_min, existing.kills_max, existing.kills_in, existing.wave, existing.hp_op, existing.hp_value, existing.leaked_gte, at_seconds)
            else:
                when = _parse_when(step.get("when"))
        elif name in {"skill", "retreat"}:
            operator = payload.get("operator", payload.get("name"))
            if not isinstance(operator, str) or not operator.strip() or ("operator" in payload and "name" in payload):
                raise PlanSchemaError(f"{name} requires exactly one operator/name")
        elif name == "speed":
            if set(payload) != {"value"} or payload["value"] not in {1, 2}:
                raise PlanSchemaError("speed requires value 1 or 2")
        elif payload:
            raise PlanSchemaError("pause payload must be empty")
        steps.append(BattleStep(step_id, name, dict(payload), when if name == "deploy" else _parse_when(step.get("when"))))
    return BattlePlan(tuple(steps))


def predicate_can_still_match(predicate: Predicate, facts: BattleFacts) -> bool:
    """Whether monotonic battle facts leave a bounded wait meaningful.

    Exact wave/kills and upper ranges become impossible once passed.  A leak is
    never a permission to inject input, including a leak predicate.
    """
    if not facts.evidence_fresh or (facts.leaked is not None and facts.leaked > 0):
        return False
    if predicate.wave is not None and facts.wave is not None and facts.wave > predicate.wave:
        return False
    if predicate.kills_max is not None and facts.kills is not None and facts.kills > predicate.kills_max:
        return False
    if predicate.kills_in is not None and facts.kills is not None and facts.kills > max(predicate.kills_in):
        return False
    return True


def predicate_matches(predicate: Predicate, facts: BattleFacts) -> bool:
    """Return true only for complete, fresh evidence satisfying every condition."""
    if not facts.evidence_fresh:
        return False
    checks: list[bool] = []
    if predicate.cost_gte is not None:
        checks.append(facts.cost is not None and facts.cost >= predicate.cost_gte)
    if predicate.kills_min is not None:
        checks.append(facts.kills is not None and facts.kills >= predicate.kills_min)
    if predicate.kills_max is not None:
        checks.append(facts.kills is not None and facts.kills <= predicate.kills_max)
    if predicate.kills_in is not None:
        checks.append(facts.kills is not None and facts.kills in predicate.kills_in)
    if predicate.wave is not None:
        checks.append(facts.wave is not None and facts.wave == predicate.wave)
    if predicate.hp_op is not None:
        value = facts.hp_pct
        target = predicate.hp_value
        checks.append(value is not None and target is not None and {">=": value >= target, ">": value > target, "<=": value <= target, "<": value < target, "==": value == target}[predicate.hp_op])
    if predicate.leaked_gte is not None:
        checks.append(facts.leaked is not None and facts.leaked >= predicate.leaked_gte)
    if predicate.time_gte is not None:
        checks.append(facts.time is not None and facts.time >= predicate.time_gte)
    return all(checks)


@dataclass(slots=True)
class BattlePlanQueue:
    plan: BattlePlan
    completed: set[str] = field(default_factory=set)
    reconciliation_step_id: str | None = None
    guard_reason: str | None = None
    decision_reason: str | None = None
    terminated: bool = False

    def next_ready(self, facts: BattleFacts) -> QueueResult:
        if self.terminated:
            return QueueResult(QueueState.COMPLETE, detail="plan terminated")
        if self.guard_reason is not None:
            return QueueResult(QueueState.GUARD_STOPPED, detail=self.guard_reason)
        if self.reconciliation_step_id is not None:
            return QueueResult(QueueState.RECONCILIATION_REQUIRED, detail=self.reconciliation_step_id)
        if self.decision_reason is not None:
            return QueueResult(QueueState.DECISION_REQUIRED, detail=self.decision_reason)
        for step in self.plan.steps:
            if step.step_id not in self.completed:
                if predicate_matches(step.when, facts):
                    return QueueResult(QueueState.READY, step)
                if not predicate_can_still_match(step.when, facts):
                    self.decision_reason = f"{step.step_id}: predicate cannot still become true"
                    return QueueResult(QueueState.DECISION_REQUIRED, step, self.decision_reason)
                return QueueResult(QueueState.WAITING, step)
        return QueueResult(QueueState.LIVE_BATTLE)

    def mark_verified_success(self, step_id: str) -> None:
        pending = next(
            (step for step in self.plan.steps if step.step_id not in self.completed),
            None,
        )
        if pending is None or pending.step_id != step_id:
            raise KeyError(step_id)
        self.completed.add(step_id)
        if self.reconciliation_step_id == step_id:
            self.reconciliation_step_id = None

    def mark_input_sent_unverified(self, step_id: str) -> None:
        pending = next(
            (step for step in self.plan.steps if step.step_id not in self.completed),
            None,
        )
        if pending is None or pending.step_id != step_id:
            raise KeyError(step_id)
        self.reconciliation_step_id = step_id

    def reconcile(self, *, effect_observed: bool | None, proof: str) -> QueueResult:
        """Resolve the sole uncertain step without ever instructing a replay."""

        step_id = self.reconciliation_step_id
        if step_id is None:
            raise RuntimeError("no step requires reconciliation")
        if not proof.strip():
            raise ValueError("explicit reconciliation proof is required")
        if effect_observed is None:
            return QueueResult(
                QueueState.RECONCILIATION_REQUIRED,
                detail=f"{step_id}: {proof}",
            )
        self.reconciliation_step_id = None
        if effect_observed:
            self.mark_verified_success(step_id)
            return QueueResult(QueueState.READY, detail=f"{step_id}: effect proved")
        self.decision_reason = f"{step_id}: no effect proved; submit a revised plan"
        return QueueResult(QueueState.DECISION_REQUIRED, detail=self.decision_reason)

    def require_guard(self, reason: str) -> None:
        self.guard_reason = reason.strip() or "plan guard requires fresh paused proof"

    def clear_guard_with_paused_proof(self, *, paused: bool, fresh: bool) -> bool:
        if fresh and paused:
            self.guard_reason = None
            return True
        return False

    def terminate(self) -> None:
        self.terminated = True
