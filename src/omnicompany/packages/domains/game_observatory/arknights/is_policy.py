# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-13T15:30:00+08:00 type=policy-interpreter status=active
"""Deterministic Integrated Strategies policy interpreters (W4-B).

This module turns authored, device-independent policy payloads into
fail-closed decision functions for the four non-battle IS screens:

- :class:`RoutePlan`      ordered mainline preferences plus conditional
                          branches; selects one exact current visible node id.
- :class:`EventPolicy`    event-name entries with option priority and
                          activation conditions (including chaos_level).
- :class:`RecruitPolicy`  MAA-compatible ``priority``/``is_key``/``is_start``,
                          dynamic priority ``offsets``, squad-completeness
                          conditions, and hope affordability.
- :class:`ShopPolicy`     conditional purchase priority and ingot
                          affordability.

Everything is deterministic, pure, and serializable.  Policies only prefer
targets among the exact current visible candidates supplied by the caller.
Nothing here executes MAA plugins, reads community strategy files, embeds an
operator strength ranking, or carries coordinates / hidden outcomes / future
route content.

MAA compatibility is schema-level only.  The accepted field shapes follow the
pinned MAA roguelike resource files:

- recruit: ``recruit_priority`` -> ``priority``,
  ``recruit_priority_when_team_full`` -> ``priority_when_team_full``,
  ``recruit_priority_offsets`` -> ``offsets`` (``groups``/``is_less``/
  ``threshold``/``offset``), ``is_key``, ``is_start``, and
  ``team_complete_condition`` (``groups``/``threshold``).
- shop: items keyed by ``name`` like ``shopping.json`` entries; the MAA
  ``priority`` array order is expressed as explicit integer priorities.
- event: entries keyed by the encounter ``name``; the optional ``option_num``
  field is accepted for parity but the MAA ``choose`` answer field is
  deliberately unsupported (it is a hidden outcome).

Failure semantics: a route deviation, an uncovered event, unknown visible
option semantics, an unavailable mainline target, incomplete squad facts,
insufficient currency, or missing run metrics all return a typed
``decision_required`` :class:`PolicyDecision`; a guess is never substituted.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from omnicompany.core.pycompat import StrEnum
from typing import Any

__all__ = [
    "Condition",
    "ConditionOperator",
    "DecisionReason",
    "EventChoice",
    "EventOptionCandidate",
    "EventOptionRule",
    "EventPolicy",
    "EventRule",
    "ISPolicyError",
    "METRIC_FIELDS",
    "PolicyDecision",
    "PolicySchemaError",
    "RecruitCandidate",
    "RecruitChoice",
    "RecruitOffsetRule",
    "RecruitOperatorRule",
    "RecruitPolicy",
    "RouteChoice",
    "RouteNodeCandidate",
    "RouteNodeTarget",
    "RoutePlan",
    "RoutePreference",
    "ShopChoice",
    "ShopItemCandidate",
    "ShopItemRule",
    "ShopPolicy",
    "SquadCompletenessRule",
    "SquadFacts",
    "validate_run_metrics",
]

_DECISION_SCHEMA = "omnicompany.arknights.is-policy-decision.v1"
_ROUTE_SCHEMA = "omnicompany.arknights.is-policy.route.v1"
_EVENT_SCHEMA = "omnicompany.arknights.is-policy.event.v1"
_RECRUIT_SCHEMA = "omnicompany.arknights.is-policy.recruit.v1"
_SHOP_SCHEMA = "omnicompany.arknights.is-policy.shop.v1"

_ROUTE_KIND = "route"
_EVENT_KIND = "event"
_RECRUIT_KIND = "recruit"
_SHOP_KIND = "shop"


class ISPolicyError(ValueError):
    """Base error for policy parsing and validation."""


class PolicySchemaError(ISPolicyError):
    """A policy payload, condition, or run-metric object is malformed."""


class DecisionReason(StrEnum):
    """Typed reasons for a ``decision_required`` result."""

    AMBIGUOUS = "ambiguous"
    CONDITIONS_UNMET = "conditions_unmet"
    INCOMPLETE_SQUAD_FACTS = "incomplete_squad_facts"
    INCOMPLETE_STATE_FACTS = "incomplete_state_facts"
    INSUFFICIENT_CURRENCY = "insufficient_currency"
    NO_VISIBLE_CANDIDATE = "no_visible_candidate"
    ROUTE_DEVIATION = "route_deviation"
    UNAVAILABLE_MAINLINE_TARGET = "unavailable_mainline_target"
    UNCOVERED_EVENT = "uncovered_event"
    UNKNOWN_OPTION_SEMANTICS = "unknown_option_semantics"


class ConditionOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


# Finite allowlist of current-run metric fields.  Conditions may reference
# only these fields; any other key in a condition or in the caller-supplied
# metric mapping is rejected (fail closed).  All values are non-negative
# integers on the run HUD.
METRIC_FIELDS = frozenset(
    {
        "life",  # current target life (目标生命)
        "anti_interference",  # current anti-interference index (抗干扰指数)
        "chaos_level",  # current collapsal paradigm level (坍缩等级)
        "hope",  # current hope (希望)
        "ingot",  # current originium ingot (源石锭)
        "shield",  # current shield points (护盾值)
    }
)


def _strict_int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicySchemaError(f"{where} must be an integer, not a boolean or float")
    return value


def _strict_non_negative_int(value: object, where: str) -> int:
    result = _strict_int(value, where)
    if result < 0:
        raise PolicySchemaError(f"{where} must not be negative")
    return result


def _strict_bool(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise PolicySchemaError(f"{where} must be a boolean")
    return value


def _strict_string(value: object, where: str, *, allow_none: bool = False) -> str | None:
    if allow_none and value is None:
        return None
    if not isinstance(value, str):
        raise PolicySchemaError(f"{where} must be a string")
    return value


def _object_keys(value: Mapping[str, object], expected: set[str], where: str) -> None:
    observed = set(value)
    if observed != expected:
        raise PolicySchemaError(
            f"{where} fields drifted: missing={sorted(expected - observed)}, "
            f"extra={sorted(observed - expected)}"
        )


def _allowed_keys(value: Mapping[str, object], allowed: set[str], where: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise PolicySchemaError(f"{where} contains unknown fields: {extra}")


def _canonical_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PolicySchemaError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise PolicySchemaError(f"non-finite JSON number is forbidden: {value}")


def _loads_strict_json(text: str) -> object:
    if not isinstance(text, str):
        raise PolicySchemaError("policy JSON must be a string")
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except json.JSONDecodeError as exc:
        raise PolicySchemaError(f"invalid policy JSON: {exc}") from exc


@dataclass(frozen=True, slots=True)
class Condition:
    """One comparison against one current-run metric field."""

    field: str
    op: ConditionOperator
    value: int

    def __post_init__(self) -> None:
        if self.field not in METRIC_FIELDS:
            raise PolicySchemaError(f"unknown condition field: {self.field!r}")
        _strict_non_negative_int(self.value, f"condition {self.field}.value")

    def satisfied_by(self, actual: int) -> bool:
        if self.op is ConditionOperator.EQ:
            return actual == self.value
        if self.op is ConditionOperator.NE:
            return actual != self.value
        if self.op is ConditionOperator.LT:
            return actual < self.value
        if self.op is ConditionOperator.LE:
            return actual <= self.value
        if self.op is ConditionOperator.GT:
            return actual > self.value
        return actual >= self.value

    def implied_bounds(self) -> tuple[int | None, int | None]:
        """Inclusive (min, max) this condition implies for the field."""
        if self.op is ConditionOperator.EQ:
            return self.value, self.value
        if self.op is ConditionOperator.GT:
            return self.value + 1, None
        if self.op is ConditionOperator.GE:
            return self.value, None
        if self.op is ConditionOperator.LT:
            return None, self.value - 1
        if self.op is ConditionOperator.LE:
            return None, self.value
        return None, None

    def to_dict(self) -> dict[str, object]:
        return {"field": self.field, "op": self.op.value, "value": self.value}


def _parse_operator(value: object, where: str) -> ConditionOperator:
    if not isinstance(value, str):
        raise PolicySchemaError(f"{where} must be a string")
    try:
        return ConditionOperator(value)
    except ValueError as exc:
        raise PolicySchemaError(f"{where} has an unsupported operator: {value!r}") from exc


def _parse_conditions(raw: object, where: str) -> tuple[Condition, ...]:
    """Parse an AND-ed condition list, rejecting contradictory constraints."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise PolicySchemaError(f"{where}.conditions must be a list")
    conditions: list[Condition] = []
    bounds: dict[str, tuple[int | None, int | None]] = {}
    for index, item in enumerate(raw):
        name = f"{where}.conditions[{index}]"
        if not isinstance(item, Mapping):
            raise PolicySchemaError(f"{name} must be an object")
        _object_keys(item, {"field", "op", "value"}, name)
        field = _strict_string(item.get("field"), f"{name}.field")
        assert field is not None
        op = _parse_operator(item.get("op"), f"{name}.op")
        value = _strict_int(item.get("value"), f"{name}.value")
        condition = Condition(field=field, op=op, value=value)
        conditions.append(condition)
        low, high = condition.implied_bounds()
        if low is None and high is None:
            continue
        previous_low, previous_high = bounds.get(field, (0, None))
        merged_low = max(previous_low if previous_low is not None else 0, low if low is not None else 0)
        merged_high: int | None
        if previous_high is None:
            merged_high = high
        elif high is None:
            merged_high = previous_high
        else:
            merged_high = min(previous_high, high)
        if merged_high is not None and merged_low > merged_high:
            raise PolicySchemaError(f"{where} contains contradictory conditions on {field!r}")
        bounds[field] = (merged_low, merged_high)
    return tuple(conditions)


def _evaluate_conditions(
    conditions: Sequence[Condition], metrics: Mapping[str, int]
) -> DecisionReason | None:
    """Return None when active, else the fail-closed reason.

    Missing facts outrank unmet comparisons: an unobservable condition is
    never silently treated as satisfied.
    """
    missing = [condition.field for condition in conditions if condition.field not in metrics]
    if missing:
        return DecisionReason.INCOMPLETE_STATE_FACTS
    if any(not condition.satisfied_by(metrics[condition.field]) for condition in conditions):
        return DecisionReason.CONDITIONS_UNMET
    return None


def validate_run_metrics(metrics: Mapping[str, object]) -> dict[str, int]:
    """Strictly validate the caller's current-run metric mapping.

    Unknown keys, booleans as numbers, non-integer or negative values are
    rejected.  Returns a plain dict in sorted key order.
    """
    if not isinstance(metrics, Mapping):
        raise PolicySchemaError("run metrics must be an object")
    result: dict[str, int] = {}
    for key in sorted(metrics):
        if not isinstance(key, str) or key not in METRIC_FIELDS:
            raise PolicySchemaError(f"unknown run metric field: {key!r}")
        result[key] = _strict_non_negative_int(metrics[key], f"metrics.{key}")
    return result


@dataclass(frozen=True, slots=True)
class RouteNodeCandidate:
    """One currently visible route node supplied by the recognizer."""

    node_id: str
    label: str

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise ValueError("route node id must not be blank")
        if not self.label.strip():
            raise ValueError("route node label must not be blank")


@dataclass(frozen=True, slots=True)
class EventOptionCandidate:
    """One currently visible event option supplied by the recognizer."""

    option_id: str
    text: str

    def __post_init__(self) -> None:
        if not self.option_id.strip():
            raise ValueError("event option id must not be blank")
        if not self.text.strip():
            raise ValueError("event option text must not be blank")


@dataclass(frozen=True, slots=True)
class RecruitCandidate:
    """One currently visible recruit with its on-screen hope cost."""

    recruit_id: str
    name: str
    cost: int

    def __post_init__(self) -> None:
        if not self.recruit_id.strip():
            raise ValueError("recruit id must not be blank")
        if not self.name.strip():
            raise ValueError("recruit name must not be blank")
        _strict_non_negative_int(self.cost, "recruit cost")


@dataclass(frozen=True, slots=True)
class ShopItemCandidate:
    """One currently visible shop item with its on-screen ingot price."""

    item_id: str
    name: str
    price: int

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("shop item id must not be blank")
        if not self.name.strip():
            raise ValueError("shop item name must not be blank")
        _strict_non_negative_int(self.price, "shop item price")


@dataclass(frozen=True, slots=True)
class RouteNodeTarget:
    """A mainline/branch preference target matched against visible nodes."""

    node_id: str | None
    label: str | None

    def __post_init__(self) -> None:
        if self.node_id is None and self.label is None:
            raise PolicySchemaError("route target requires node_id or label")
        if self.node_id is not None and not self.node_id.strip():
            raise PolicySchemaError("route target node_id must not be blank")
        if self.label is not None and not self.label.strip():
            raise PolicySchemaError("route target label must not be blank")

    def matches(self, candidate: RouteNodeCandidate) -> bool:
        if self.node_id is not None and candidate.node_id != self.node_id:
            return False
        if self.label is not None and candidate.label != self.label:
            return False
        return True

    def key(self) -> tuple[str, str]:
        return self.node_id or "", self.label or ""

    def to_dict(self) -> dict[str, object]:
        return {"node_id": self.node_id, "label": self.label}


@dataclass(frozen=True, slots=True)
class RoutePreference:
    """One ordered route preference with optional activation conditions."""

    target: RouteNodeTarget
    priority: int
    conditions: tuple[Condition, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "target": self.target.to_dict(),
            "priority": self.priority,
            "conditions": [condition.to_dict() for condition in self.conditions],
        }


def _parse_target(raw: object, where: str) -> RouteNodeTarget:
    if not isinstance(raw, Mapping):
        raise PolicySchemaError(f"{where} must be an object")
    _object_keys(raw, {"node_id", "label"}, where)
    node_id = _strict_string(raw.get("node_id"), f"{where}.node_id", allow_none=True)
    label = _strict_string(raw.get("label"), f"{where}.label", allow_none=True)
    return RouteNodeTarget(node_id=node_id, label=label)


def _parse_preferences(raw: object, where: str) -> tuple[RoutePreference, ...]:
    if not isinstance(raw, list):
        raise PolicySchemaError(f"{where} must be a list")
    preferences: list[RoutePreference] = []
    for index, item in enumerate(raw):
        name = f"{where}[{index}]"
        if not isinstance(item, Mapping):
            raise PolicySchemaError(f"{name} must be an object")
        _allowed_keys(item, {"target", "priority", "conditions"}, name)
        target = _parse_target(item.get("target"), f"{name}.target")
        priority = _strict_int(item.get("priority"), f"{name}.priority")
        conditions = _parse_conditions(item.get("conditions"), name)
        preferences.append(RoutePreference(target=target, priority=priority, conditions=conditions))
    return tuple(preferences)


def _reject_duplicate_priorities(
    entries: Sequence[RoutePreference], where: str
) -> None:
    seen: dict[int, tuple[str, str]] = {}
    for entry in entries:
        key = entry.target.key()
        previous = seen.get(entry.priority)
        if previous is not None:
            raise PolicySchemaError(
                f"{where} contains duplicate priority {entry.priority} "
                f"between targets {previous} and {key}"
            )
        seen[entry.priority] = key


def _reject_duplicate_targets(entries: Sequence[RoutePreference], where: str) -> None:
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = entry.target.key()
        if key in seen:
            raise PolicySchemaError(f"{where} contains duplicate targets: {key}")
        seen.add(key)


@dataclass(frozen=True, slots=True)
class RoutePlan:
    """Ordered mainline preferences plus conditional branches.

    Mainline entries always outrank branch entries.  Within a tier, higher
    priority wins; duplicate priorities within one tier are rejected at parse
    time because they would make the choice ambiguous.
    """

    mainline: tuple[RoutePreference, ...]
    branches: tuple[RoutePreference, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _ROUTE_SCHEMA,
            "mainline": [entry.to_dict() for entry in _sorted_preferences(self.mainline)],
            "branches": [entry.to_dict() for entry in _sorted_preferences(self.branches)],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> RoutePlan:
        if not isinstance(raw, Mapping):
            raise PolicySchemaError("route policy must be an object")
        _object_keys(raw, {"schema", "mainline", "branches"}, "route policy")
        if raw.get("schema") != _ROUTE_SCHEMA:
            raise PolicySchemaError("unsupported route policy schema")
        mainline = _parse_preferences(raw.get("mainline"), "mainline")
        if not mainline:
            raise PolicySchemaError("mainline must be a non-empty preference list")
        branches = _parse_preferences(raw.get("branches"), "branches")
        _reject_duplicate_priorities(mainline, "mainline")
        _reject_duplicate_priorities(branches, "branches")
        _reject_duplicate_targets(mainline, "mainline")
        _reject_duplicate_targets(branches, "branches")
        return cls(mainline=mainline, branches=branches)

    @classmethod
    def from_json(cls, text: str) -> RoutePlan:
        raw = _loads_strict_json(text)
        if not isinstance(raw, Mapping):
            raise PolicySchemaError("route policy root must be an object")
        return cls.from_dict(raw)

    def choose(
        self, visible: Sequence[RouteNodeCandidate], metrics: Mapping[str, object]
    ) -> PolicyDecision:
        """Select one exact current visible node or return ``decision_required``."""
        nodes = _validate_candidates(visible, "route nodes")
        state = validate_run_metrics(metrics)
        if not nodes:
            return PolicyDecision.required(
                _ROUTE_KIND, DecisionReason.NO_VISIBLE_CANDIDATE, "no visible route nodes"
            )

        # Missing a metric used anywhere in the policy is a callback, even
        # when an earlier preference happens to match.
        for entry in (*self.mainline, *self.branches):
            if _evaluate_conditions(entry.conditions, state) is DecisionReason.INCOMPLETE_STATE_FACTS:
                return PolicyDecision.required(
                    _ROUTE_KIND, DecisionReason.INCOMPLETE_STATE_FACTS,
                    "route preference references missing run metrics",
                )

        # Activated branches are explicit deviations and take precedence.
        branch_ready_but_missing = False
        for entry in _sorted_preferences(self.branches):
            result = _evaluate_conditions(entry.conditions, state)
            if result is DecisionReason.CONDITIONS_UNMET:
                continue
            matches = [node for node in nodes if entry.target.matches(node)]
            if len(matches) == 1:
                node = matches[0]
                return PolicyDecision.selected(
                    _ROUTE_KIND, RouteChoice(node.node_id, node.label, entry.priority, True)
                )
            if len(matches) > 1:
                return PolicyDecision.required(
                    _ROUTE_KIND, DecisionReason.AMBIGUOUS,
                    "branch preference matches multiple visible nodes",
                )
            branch_ready_but_missing = True
        if branch_ready_but_missing:
            return PolicyDecision.required(
                _ROUTE_KIND, DecisionReason.ROUTE_DEVIATION,
                "no visible node matches an active branch preference",
            )

        mainline_ready_but_missing = False
        for entry in _sorted_preferences(self.mainline):
            result = _evaluate_conditions(entry.conditions, state)
            if result is DecisionReason.INCOMPLETE_STATE_FACTS:
                return PolicyDecision.required(
                    _ROUTE_KIND, result, "mainline preference references missing run metrics"
                )
            if result is DecisionReason.CONDITIONS_UNMET:
                continue
            matches = [node for node in nodes if entry.target.matches(node)]
            if len(matches) == 1:
                node = matches[0]
                return PolicyDecision.selected(
                    _ROUTE_KIND,
                    RouteChoice(
                        node_id=node.node_id,
                        label=node.label,
                        priority=entry.priority,
                        branch=False,
                    ),
                )
            if len(matches) > 1:
                return PolicyDecision.required(
                    _ROUTE_KIND, DecisionReason.AMBIGUOUS,
                    "route preference matches multiple visible nodes",
                )
            mainline_ready_but_missing = True

        if mainline_ready_but_missing:
            return PolicyDecision.required(
                _ROUTE_KIND,
                DecisionReason.UNAVAILABLE_MAINLINE_TARGET,
                "no visible node matches an active mainline preference",
            )
        return PolicyDecision.required(
            _ROUTE_KIND,
            DecisionReason.CONDITIONS_UNMET,
            "no route preference is active under current run metrics",
        )


def _sorted_preferences(entries: Sequence[RoutePreference]) -> list[RoutePreference]:
    return sorted(entries, key=lambda entry: (-entry.priority, entry.target.key()))


@dataclass(frozen=True, slots=True)
class EventOptionRule:
    """One prioritized option rule.  Exactly one of ``option_id``/``text`` matches."""

    option_id: str | None
    text: str | None
    priority: int
    conditions: tuple[Condition, ...] = ()

    def __post_init__(self) -> None:
        if (self.option_id is None) == (self.text is None):
            raise PolicySchemaError("event option rule requires exactly one of option_id/text")
        if self.option_id is not None and not self.option_id.strip():
            raise PolicySchemaError("event option rule option_id must not be blank")
        if self.text is not None and not self.text.strip():
            raise PolicySchemaError("event option rule text must not be blank")

    def matches(self, candidate: EventOptionCandidate) -> bool:
        if self.option_id is not None:
            return candidate.option_id == self.option_id
        assert self.text is not None
        return candidate.text == self.text

    def key(self) -> tuple[str, str]:
        return self.option_id or "", self.text or ""

    def to_dict(self) -> dict[str, object]:
        return {
            "option_id": self.option_id,
            "text": self.text,
            "priority": self.priority,
            "conditions": [condition.to_dict() for condition in self.conditions],
        }


@dataclass(frozen=True, slots=True)
class EventRule:
    """One covered event name with option rules and activation conditions."""

    name: str
    option_num: int | None
    options: tuple[EventOptionRule, ...]
    conditions: tuple[Condition, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "option_num": self.option_num,
            "conditions": [condition.to_dict() for condition in self.conditions],
            "options": [
                option.to_dict() for option in _sorted_event_options(self.options)
            ],
        }


def _sorted_event_options(options: Sequence[EventOptionRule]) -> list[EventOptionRule]:
    return sorted(options, key=lambda option: (-option.priority, option.key()))


def _parse_event_rule(name: object, raw: object, where: str) -> EventRule:
    if not isinstance(name, str) or not name.strip():
        raise PolicySchemaError(f"{where} event names must be non-blank strings")
    if not isinstance(raw, Mapping):
        raise PolicySchemaError(f"{where} must be an object")
    _allowed_keys(raw, {"option_num", "options", "conditions"}, where)
    option_num_raw = raw.get("option_num")
    option_num = (
        None
        if option_num_raw is None
        else _strict_int(option_num_raw, f"{where}.option_num")
    )
    if option_num is not None and option_num < 1:
        raise PolicySchemaError(f"{where}.option_num must be positive")
    conditions = _parse_conditions(raw.get("conditions"), where)
    raw_options = raw.get("options")
    if not isinstance(raw_options, list) or not raw_options:
        raise PolicySchemaError(f"{where}.options must be a non-empty list")
    options: list[EventOptionRule] = []
    seen_priorities: dict[int, tuple[str, str]] = {}
    seen_match_keys: set[tuple[str, str]] = set()
    for index, item in enumerate(raw_options):
        option_where = f"{where}.options[{index}]"
        if not isinstance(item, Mapping):
            raise PolicySchemaError(f"{option_where} must be an object")
        _allowed_keys(item, {"option_id", "text", "priority", "conditions"}, option_where)
        option = EventOptionRule(
            option_id=_strict_string(item.get("option_id"), f"{option_where}.option_id", allow_none=True),
            text=_strict_string(item.get("text"), f"{option_where}.text", allow_none=True),
            priority=_strict_int(item.get("priority"), f"{option_where}.priority"),
            conditions=_parse_conditions(item.get("conditions"), option_where),
        )
        key = option.key()
        if key in seen_match_keys:
            raise PolicySchemaError(f"{where} contains duplicate option rules: {key}")
        seen_match_keys.add(key)
        previous = seen_priorities.get(option.priority)
        if previous is not None:
            raise PolicySchemaError(
                f"{where} contains duplicate option priority {option.priority} "
                f"between {previous} and {key}"
            )
        seen_priorities[option.priority] = key
        options.append(option)
    return EventRule(name=name, option_num=option_num, options=tuple(options), conditions=conditions)


@dataclass(frozen=True, slots=True)
class EventPolicy:
    """Event-name policy: option priority plus activation conditions.

    The MAA encounter ``choose`` answer field is deliberately unsupported;
    outcomes stay with the game.  Unknown events and visible options without
    a rule both require a callback instead of a guess.
    """

    events: Mapping[str, EventRule]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _EVENT_SCHEMA,
            "events": {name: self.events[name].to_dict() for name in sorted(self.events)},
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> EventPolicy:
        if not isinstance(raw, Mapping):
            raise PolicySchemaError("event policy must be an object")
        _object_keys(raw, {"schema", "events"}, "event policy")
        if raw.get("schema") != _EVENT_SCHEMA:
            raise PolicySchemaError("unsupported event policy schema")
        raw_events = raw.get("events")
        if not isinstance(raw_events, Mapping) or not raw_events:
            raise PolicySchemaError("events must be a non-empty object")
        events: dict[str, EventRule] = {}
        for name in sorted(raw_events):
            events[name] = _parse_event_rule(name, raw_events[name], f"events[{name!r}]")
        return cls(events=events)

    @classmethod
    def from_json(cls, text: str) -> EventPolicy:
        raw = _loads_strict_json(text)
        if not isinstance(raw, Mapping):
            raise PolicySchemaError("event policy root must be an object")
        return cls.from_dict(raw)

    def choose(
        self,
        event_name: str,
        visible: Sequence[EventOptionCandidate],
        metrics: Mapping[str, object],
    ) -> PolicyDecision:
        """Choose one visible option of a covered event, else ``decision_required``."""
        if not isinstance(event_name, str) or not event_name.strip():
            raise ValueError("event_name must be a non-blank string")
        rule = self.events.get(event_name)
        if rule is None:
            return PolicyDecision.required(
                _EVENT_KIND, DecisionReason.UNCOVERED_EVENT, f"uncovered event: {event_name}"
            )
        options = _validate_candidates(visible, "event options")
        state = validate_run_metrics(metrics)

        gate = _evaluate_conditions(rule.conditions, state)
        if gate is DecisionReason.INCOMPLETE_STATE_FACTS:
            return PolicyDecision.required(
                _EVENT_KIND, gate, f"event {event_name!r} activation references missing run metrics"
            )
        if gate is DecisionReason.CONDITIONS_UNMET:
            return PolicyDecision.required(
                _EVENT_KIND,
                gate,
                f"event {event_name!r} rule is inactive under current run metrics",
            )

        covered: set[int] = set()
        conditions_unmet_seen = False
        for option in _sorted_event_options(rule.options):
            matches = [index for index, candidate in enumerate(options) if option.matches(candidate)]
            covered.update(matches)
            result = _evaluate_conditions(option.conditions, state)
            if result is DecisionReason.INCOMPLETE_STATE_FACTS:
                return PolicyDecision.required(
                    _EVENT_KIND, result, "event option rule references missing run metrics"
                )
            if result is DecisionReason.CONDITIONS_UNMET:
                conditions_unmet_seen = True
                continue
            if len(matches) == 1:
                candidate = options[matches[0]]
                return PolicyDecision.selected(
                    _EVENT_KIND,
                    EventChoice(
                        option_id=candidate.option_id,
                        text=candidate.text,
                        priority=option.priority,
                    ),
                )
            if len(matches) > 1:
                return PolicyDecision.required(
                    _EVENT_KIND,
                    DecisionReason.AMBIGUOUS,
                    f"option rule for {event_name!r} matches multiple visible options",
                )

        uncovered = [
            candidate for index, candidate in enumerate(options) if index not in covered
        ]
        if uncovered:
            return PolicyDecision.required(
                _EVENT_KIND,
                DecisionReason.UNKNOWN_OPTION_SEMANTICS,
                f"event {event_name!r} shows options with no policy rule: "
                + ", ".join(candidate.text for candidate in uncovered),
            )
        if conditions_unmet_seen:
            return PolicyDecision.required(
                _EVENT_KIND,
                DecisionReason.CONDITIONS_UNMET,
                f"no option rule for {event_name!r} is active under current run metrics",
            )
        if not options:
            return PolicyDecision.required(
                _EVENT_KIND, DecisionReason.NO_VISIBLE_CANDIDATE, "no visible event options"
            )
        return PolicyDecision.required(
            _EVENT_KIND,
            DecisionReason.NO_VISIBLE_CANDIDATE,
            f"no visible option of {event_name!r} can be selected",
        )


@dataclass(frozen=True, slots=True)
class SquadCompletenessRule:
    """One MAA ``team_complete_condition`` rule."""

    groups: tuple[str, ...]
    threshold: int

    def __post_init__(self) -> None:
        if not self.groups or any(not group.strip() for group in self.groups):
            raise PolicySchemaError("squad completeness groups must be non-blank strings")
        _strict_int(self.threshold, "squad completeness threshold")
        if self.threshold < 1:
            raise PolicySchemaError("squad completeness threshold must be positive")

    def key(self) -> tuple[tuple[str, ...], int]:
        return self.groups, self.threshold

    def to_dict(self) -> dict[str, object]:
        return {"groups": list(self.groups), "threshold": self.threshold}


@dataclass(frozen=True, slots=True)
class RecruitOffsetRule:
    """One MAA ``recruit_priority_offsets`` entry.

    Holds when the summed current squad count of ``groups`` is
    ``<= threshold`` (``is_less``) or ``>= threshold``; a missing
    ``threshold`` defaults to 1.  All holding offsets apply additively.
    """

    groups: tuple[str, ...]
    is_less: bool = False
    threshold: int = 1
    offset: int = 0

    def __post_init__(self) -> None:
        if not self.groups or any(not group.strip() for group in self.groups):
            raise PolicySchemaError("recruit offset groups must be non-blank strings")
        if not isinstance(self.is_less, bool):
            raise PolicySchemaError("recruit offset is_less must be a boolean")
        _strict_non_negative_int(self.threshold, "recruit offset threshold")
        _strict_int(self.offset, "recruit offset")

    def holds(self, counts: Mapping[str, int]) -> bool:
        total = sum(counts[group] for group in self.groups)
        return total <= self.threshold if self.is_less else total >= self.threshold

    def to_dict(self) -> dict[str, object]:
        return {
            "groups": list(self.groups),
            "is_less": self.is_less,
            "threshold": self.threshold,
            "offset": self.offset,
        }


@dataclass(frozen=True, slots=True)
class RecruitOperatorRule:
    """One MAA-compatible operator rule.

    ``is_key``/``is_start`` are echoed on the decision payload; they do not
    change ranking.  When the squad is complete,
    ``priority_when_team_full`` replaces ``priority`` (MAA semantics).
    """

    name: str
    priority: int
    priority_when_team_full: int | None = None
    is_key: bool = False
    is_start: bool = False
    offsets: tuple[RecruitOffsetRule, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise PolicySchemaError("recruit operator name must not be blank")
        _strict_int(self.priority, f"operator {self.name}.priority")
        if self.priority_when_team_full is not None:
            _strict_int(self.priority_when_team_full, f"operator {self.name}.priority_when_team_full")
        if not isinstance(self.is_key, bool) or not isinstance(self.is_start, bool):
            raise PolicySchemaError(f"operator {self.name} is_key/is_start must be booleans")

    def to_dict(self) -> dict[str, object]:
        return {
            "priority": self.priority,
            "priority_when_team_full": self.priority_when_team_full,
            "is_key": self.is_key,
            "is_start": self.is_start,
            "offsets": [offset.to_dict() for offset in self.offsets],
        }


def _parse_offset_rule(raw: object, where: str) -> RecruitOffsetRule:
    if not isinstance(raw, Mapping):
        raise PolicySchemaError(f"{where} must be an object")
    _allowed_keys(raw, {"groups", "is_less", "threshold", "offset"}, where)
    raw_groups = raw.get("groups")
    if not isinstance(raw_groups, list) or not raw_groups:
        raise PolicySchemaError(f"{where}.groups must be a non-empty list of strings")
    groups: list[str] = []
    for index, group in enumerate(raw_groups):
        if not isinstance(group, str) or not group.strip():
            raise PolicySchemaError(f"{where}.groups[{index}] must be a non-blank string")
        groups.append(group)
    is_less = (
        False
        if raw.get("is_less") is None
        else _strict_bool(raw.get("is_less"), f"{where}.is_less")
    )
    threshold = (
        1
        if raw.get("threshold") is None
        else _strict_non_negative_int(raw.get("threshold"), f"{where}.threshold")
    )
    offset = _strict_int(raw.get("offset"), f"{where}.offset")
    return RecruitOffsetRule(
        groups=tuple(groups), is_less=is_less, threshold=threshold, offset=offset
    )


def _parse_operator_rule(name: object, raw: object, where: str) -> RecruitOperatorRule:
    if not isinstance(name, str) or not name.strip():
        raise PolicySchemaError(f"{where} operator names must be non-blank strings")
    if not isinstance(raw, Mapping):
        raise PolicySchemaError(f"{where} must be an object")
    _allowed_keys(
        raw,
        {"priority", "priority_when_team_full", "is_key", "is_start", "offsets"},
        where,
    )
    priority = _strict_int(raw.get("priority"), f"{where}.priority")
    when_full_raw = raw.get("priority_when_team_full")
    when_full = (
        None
        if when_full_raw is None
        else _strict_int(when_full_raw, f"{where}.priority_when_team_full")
    )
    is_key = _strict_bool(raw.get("is_key", False), f"{where}.is_key")
    is_start = _strict_bool(raw.get("is_start", False), f"{where}.is_start")
    raw_offsets = raw.get("offsets")
    offsets: tuple[RecruitOffsetRule, ...] = ()
    if raw_offsets is not None:
        if not isinstance(raw_offsets, list):
            raise PolicySchemaError(f"{where}.offsets must be a list")
        offsets = tuple(
            _parse_offset_rule(item, f"{where}.offsets[{index}]")
            for index, item in enumerate(raw_offsets)
        )
    return RecruitOperatorRule(
        name=name,
        priority=priority,
        priority_when_team_full=when_full,
        is_key=is_key,
        is_start=is_start,
        offsets=offsets,
    )


@dataclass(frozen=True, slots=True)
class SquadFacts:
    """Observed current squad counts keyed by MAA group/role/operator names."""

    group_counts: Mapping[str, int]

    def __post_init__(self) -> None:
        counts: dict[str, int] = {}
        for key in sorted(self.group_counts):
            if not isinstance(key, str) or not key.strip():
                raise ValueError("squad group names must be non-blank strings")
            counts[key] = _strict_non_negative_int(self.group_counts[key], f"squad group {key!r}")
        object.__setattr__(self, "group_counts", counts)


@dataclass(frozen=True, slots=True)
class RecruitPolicy:
    """MAA-compatible recruit policy with dynamic priority offsets.

    Operator names with equal numeric priority are allowed (as in MAA
    resource files); a tie among the best affordable visible recruits is a
    typed ``ambiguous`` ``decision_required``.  Unseen recruits are never
    inferred, and unaffordable recruits are never selected.
    """

    operators: Mapping[str, RecruitOperatorRule]
    team_complete_condition: tuple[SquadCompletenessRule, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _RECRUIT_SCHEMA,
            "operators": {
                name: self.operators[name].to_dict() for name in sorted(self.operators)
            },
            "team_complete_condition": [
                rule.to_dict() for rule in self.team_complete_condition
            ],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> RecruitPolicy:
        if not isinstance(raw, Mapping):
            raise PolicySchemaError("recruit policy must be an object")
        _object_keys(raw, {"schema", "operators", "team_complete_condition"}, "recruit policy")
        if raw.get("schema") != _RECRUIT_SCHEMA:
            raise PolicySchemaError("unsupported recruit policy schema")
        raw_operators = raw.get("operators")
        if not isinstance(raw_operators, Mapping) or not raw_operators:
            raise PolicySchemaError("operators must be a non-empty object")
        operators: dict[str, RecruitOperatorRule] = {}
        for name in sorted(raw_operators):
            operators[name] = _parse_operator_rule(name, raw_operators[name], f"operators[{name!r}]")
        completeness: list[SquadCompletenessRule] = []
        seen_completeness: set[tuple[tuple[str, ...], int]] = set()
        raw_conditions = raw.get("team_complete_condition")
        if raw_conditions is not None:
            if not isinstance(raw_conditions, list):
                raise PolicySchemaError("team_complete_condition must be a list")
            for index, item in enumerate(raw_conditions):
                where = f"team_complete_condition[{index}]"
                if not isinstance(item, Mapping):
                    raise PolicySchemaError(f"{where} must be an object")
                _object_keys(item, {"groups", "threshold"}, where)
                raw_groups = item.get("groups")
                if not isinstance(raw_groups, list) or not raw_groups:
                    raise PolicySchemaError(f"{where}.groups must be a non-empty list of strings")
                groups: list[str] = []
                for group_index, group in enumerate(raw_groups):
                    if not isinstance(group, str) or not group.strip():
                        raise PolicySchemaError(f"{where}.groups[{group_index}] must be a non-blank string")
                    groups.append(group)
                rule = SquadCompletenessRule(
                    groups=tuple(groups),
                    threshold=_strict_int(item.get("threshold"), f"{where}.threshold"),
                )
                if rule.key() in seen_completeness:
                    raise PolicySchemaError(f"duplicate team_complete_condition: {rule.key()}")
                seen_completeness.add(rule.key())
                completeness.append(rule)
        completeness.sort(key=lambda rule: rule.key())
        return cls(operators=operators, team_complete_condition=tuple(completeness))

    @classmethod
    def from_json(cls, text: str) -> RecruitPolicy:
        raw = _loads_strict_json(text)
        if not isinstance(raw, Mapping):
            raise PolicySchemaError("recruit policy root must be an object")
        return cls.from_dict(raw)

    def _squad_complete(self, counts: Mapping[str, int]) -> tuple[bool, DecisionReason | None]:
        for rule in self.team_complete_condition:
            missing = [group for group in rule.groups if group not in counts]
            if missing:
                return False, DecisionReason.INCOMPLETE_SQUAD_FACTS
        complete = all(
            sum(counts[group] for group in rule.groups) >= rule.threshold
            for rule in self.team_complete_condition
        )
        return complete, None

    def choose(
        self,
        visible: Sequence[RecruitCandidate],
        metrics: Mapping[str, object],
        squad: SquadFacts,
    ) -> PolicyDecision:
        """Pick one current visible affordable recruit, else ``decision_required``."""
        candidates = _validate_candidates(visible, "recruits")
        state = validate_run_metrics(metrics)
        if "hope" not in state:
            return PolicyDecision.required(
                _RECRUIT_KIND, DecisionReason.INCOMPLETE_STATE_FACTS,
                "recruit choice requires the current hope metric",
            )
        counts = squad.group_counts
        squad_complete, incomplete = self._squad_complete(counts)
        if incomplete is not None:
            return PolicyDecision.required(
                _RECRUIT_KIND,
                incomplete,
                "team completeness cannot be judged from observed squad facts",
            )

        ranked: list[tuple[int, RecruitCandidate, RecruitOperatorRule]] = []
        unaffordable_seen = False
        for candidate in candidates:
            rule = self.operators.get(candidate.name)
            if rule is None:
                continue  # an unseen recruit is never inferred or ranked
            effective = rule.priority
            if squad_complete and rule.priority_when_team_full is not None:
                effective = rule.priority_when_team_full
            for offset in rule.offsets:
                missing = [group for group in offset.groups if group not in counts]
                if missing:
                    return PolicyDecision.required(
                        _RECRUIT_KIND,
                        DecisionReason.INCOMPLETE_SQUAD_FACTS,
                        f"recruit offsets for {candidate.name!r} reference unobserved "
                        f"squad groups: {missing}",
                    )
                if offset.holds(counts):
                    effective += offset.offset
            if candidate.cost > state["hope"]:
                unaffordable_seen = True
                continue
            ranked.append((effective, candidate, rule))

        if not ranked:
            if unaffordable_seen:
                return PolicyDecision.required(
                    _RECRUIT_KIND, DecisionReason.INSUFFICIENT_CURRENCY,
                    "no visible recruit is affordable with current hope",
                )
            return PolicyDecision.required(
                _RECRUIT_KIND, DecisionReason.NO_VISIBLE_CANDIDATE,
                "no visible recruit matches the policy",
            )
        best = max(effective for effective, _, _ in ranked)
        winners = [item for item in ranked if item[0] == best]
        if len(winners) > 1:
            names = sorted(item[1].name for item in winners)
            return PolicyDecision.required(
                _RECRUIT_KIND,
                DecisionReason.AMBIGUOUS,
                "multiple recruits share the highest effective priority: " + ", ".join(names),
            )
        effective, candidate, rule = winners[0]
        return PolicyDecision.selected(
            _RECRUIT_KIND,
            RecruitChoice(
                recruit_id=candidate.recruit_id,
                name=candidate.name,
                cost=candidate.cost,
                effective_priority=effective,
                is_key=rule.is_key,
                is_start=rule.is_start,
            ),
        )


@dataclass(frozen=True, slots=True)
class ShopItemRule:
    """One conditional shop purchase rule, keyed by item name."""

    name: str
    priority: int
    conditions: tuple[Condition, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise PolicySchemaError("shop item name must not be blank")
        _strict_int(self.priority, f"shop item {self.name}.priority")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "priority": self.priority,
            "conditions": [condition.to_dict() for condition in self.conditions],
        }


@dataclass(frozen=True, slots=True)
class ShopPolicy:
    """Conditional purchase priority with ingot affordability."""

    items: tuple[ShopItemRule, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SHOP_SCHEMA,
            "items": [item.to_dict() for item in sorted(self.items, key=lambda r: (-r.priority, r.name))],
        }

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> ShopPolicy:
        if not isinstance(raw, Mapping):
            raise PolicySchemaError("shop policy must be an object")
        _object_keys(raw, {"schema", "items"}, "shop policy")
        if raw.get("schema") != _SHOP_SCHEMA:
            raise PolicySchemaError("unsupported shop policy schema")
        raw_items = raw.get("items")
        if not isinstance(raw_items, list) or not raw_items:
            raise PolicySchemaError("items must be a non-empty list")
        items: list[ShopItemRule] = []
        seen_names: set[str] = set()
        seen_priorities: dict[int, str] = {}
        for index, item in enumerate(raw_items):
            where = f"items[{index}]"
            if not isinstance(item, Mapping):
                raise PolicySchemaError(f"{where} must be an object")
            _allowed_keys(item, {"name", "priority", "conditions"}, where)
            name = _strict_string(item.get("name"), f"{where}.name")
            assert name is not None
            priority = _strict_int(item.get("priority"), f"{where}.priority")
            if name in seen_names:
                raise PolicySchemaError(f"duplicate shop item name: {name!r}")
            seen_names.add(name)
            previous = seen_priorities.get(priority)
            if previous is not None:
                raise PolicySchemaError(
                    f"duplicate shop priority {priority} between {previous!r} and {name!r}"
                )
            seen_priorities[priority] = name
            items.append(
                ShopItemRule(
                    name=name,
                    priority=priority,
                    conditions=_parse_conditions(item.get("conditions"), where),
                )
            )
        return cls(items=tuple(items))

    @classmethod
    def from_json(cls, text: str) -> ShopPolicy:
        raw = _loads_strict_json(text)
        if not isinstance(raw, Mapping):
            raise PolicySchemaError("shop policy root must be an object")
        return cls.from_dict(raw)

    def choose(
        self, visible: Sequence[ShopItemCandidate], metrics: Mapping[str, object]
    ) -> PolicyDecision:
        """Buy the highest-priority affordable visible item, else ``decision_required``."""
        items = _validate_candidates(visible, "shop items")
        state = validate_run_metrics(metrics)
        if "ingot" not in state:
            return PolicyDecision.required(
                _SHOP_KIND, DecisionReason.INCOMPLETE_STATE_FACTS,
                "shop choice requires the current ingot metric",
            )

        unaffordable_seen = False
        conditions_unmet_seen = False
        for rule in sorted(self.items, key=lambda item: (-item.priority, item.name)):
            matches = [candidate for candidate in items if candidate.name == rule.name]
            if not matches:
                continue
            if len(matches) > 1:
                return PolicyDecision.required(
                    _SHOP_KIND,
                    DecisionReason.AMBIGUOUS,
                    f"shop rule {rule.name!r} matches multiple visible items",
                )
            result = _evaluate_conditions(rule.conditions, state)
            if result is DecisionReason.INCOMPLETE_STATE_FACTS:
                return PolicyDecision.required(
                    _SHOP_KIND, result, f"shop rule {rule.name!r} references missing run metrics"
                )
            if result is DecisionReason.CONDITIONS_UNMET:
                conditions_unmet_seen = True
                continue
            candidate = matches[0]
            if candidate.price > state["ingot"]:
                unaffordable_seen = True
                continue
            return PolicyDecision.selected(
                _SHOP_KIND,
                ShopChoice(
                    item_id=candidate.item_id,
                    name=candidate.name,
                    price=candidate.price,
                    priority=rule.priority,
                ),
            )

        if unaffordable_seen:
            return PolicyDecision.required(
                _SHOP_KIND, DecisionReason.INSUFFICIENT_CURRENCY,
                "no visible shop item is affordable with current ingots",
            )
        if conditions_unmet_seen:
            return PolicyDecision.required(
                _SHOP_KIND, DecisionReason.CONDITIONS_UNMET,
                "no shop item rule is active under current run metrics",
            )
        return PolicyDecision.required(
            _SHOP_KIND, DecisionReason.NO_VISIBLE_CANDIDATE,
            "no visible shop item matches the policy",
        )


@dataclass(frozen=True, slots=True)
class RouteChoice:
    """Selected route node payload.  No coordinates or hidden data."""

    node_id: str
    label: str
    priority: int
    branch: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "priority": self.priority,
            "branch": self.branch,
        }


@dataclass(frozen=True, slots=True)
class EventChoice:
    """Selected event option payload."""

    option_id: str
    text: str
    priority: int

    def to_dict(self) -> dict[str, object]:
        return {"option_id": self.option_id, "text": self.text, "priority": self.priority}


@dataclass(frozen=True, slots=True)
class RecruitChoice:
    """Selected recruit payload, including flags but never a strength score."""

    recruit_id: str
    name: str
    cost: int
    effective_priority: int
    is_key: bool
    is_start: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "recruit_id": self.recruit_id,
            "name": self.name,
            "cost": self.cost,
            "effective_priority": self.effective_priority,
            "is_key": self.is_key,
            "is_start": self.is_start,
        }


@dataclass(frozen=True, slots=True)
class ShopChoice:
    """Selected shop purchase payload."""

    item_id: str
    name: str
    price: int
    priority: int

    def to_dict(self) -> dict[str, object]:
        return {"item_id": self.item_id, "name": self.name, "price": self.price, "priority": self.priority}


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Either one selected current-visible choice or a typed ``decision_required``."""

    schema: str
    kind: str
    outcome: str
    reason: str | None
    detail: str
    choice: RouteChoice | EventChoice | RecruitChoice | ShopChoice | None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": self.schema,
            "kind": self.kind,
            "outcome": self.outcome,
            "reason": self.reason,
            "detail": self.detail,
            "choice": self.choice.to_dict() if self.choice is not None else None,
        }
        return payload

    def to_json(self) -> str:
        return _canonical_json(self.to_dict())

    @classmethod
    def selected(cls, kind: str, choice: RouteChoice | EventChoice | RecruitChoice | ShopChoice) -> PolicyDecision:
        return cls(
            schema=_DECISION_SCHEMA,
            kind=kind,
            outcome="selected",
            reason=None,
            detail="",
            choice=choice,
        )

    @classmethod
    def required(cls, kind: str, reason: DecisionReason, detail: str) -> PolicyDecision:
        if not detail.strip():
            raise ValueError("decision_required detail must not be blank")
        return cls(
            schema=_DECISION_SCHEMA,
            kind=kind,
            outcome="decision_required",
            reason=reason.value,
            detail=detail,
            choice=None,
        )


_ID_ATTRIBUTES = {
    RouteNodeCandidate: "node_id",
    EventOptionCandidate: "option_id",
    RecruitCandidate: "recruit_id",
    ShopItemCandidate: "item_id",
}


def _validate_candidates(visible: Sequence[Any], where: str) -> tuple[Any, ...]:
    if isinstance(visible, (str, bytes)) or not isinstance(visible, Sequence):
        raise ValueError(f"{where} must be a sequence of candidate objects")
    candidates = tuple(visible)
    if not candidates:
        return candidates
    attribute = _ID_ATTRIBUTES.get(type(candidates[0]))
    if attribute is None:
        raise ValueError(f"unsupported {where} candidate type: {type(candidates[0]).__name__}")
    ids = [getattr(candidate, attribute) for candidate in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError(f"visible {where} ids must be unique")
    return candidates
