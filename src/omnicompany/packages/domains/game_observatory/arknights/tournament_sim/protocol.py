# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-10T08:20:00+08:00 type=ndjson-simulator-protocol status=active
"""Strict one-request/one-response NDJSON protocol over ``BattleEngine``.

This module is transport-only.  It never exposes arbitrary state mutation or
omniscient observations, and every forecast delegates to the session's single
``ForecastBudgetLedger``.  Resetting a session therefore cannot reset quota.

Production mode restores and commits the complete protocol state through one
local :class:`AtomicStateStore` transaction per request.  The
private state contains real ``EngineCheckpoint`` values; the public checkpoint
method deliberately remains a known-only summary.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ..tournament_persistent_state import (
    AtomicStateStore,
    LocalStateBindingV1,
    PersistentStateError,
    require_local_state_store,
)
from .budget import ForecastBudgetLedger
from .engine import BattleEngine, IllegalActionError, TournamentSimError
from .models import (
    ActivateSkillAction,
    ContentBundle,
    DeployAction,
    Direction,
    EngineCheckpoint,
    ForecastResult,
    GridPoint,
    InitialState,
    PublicAction,
    RetreatAction,
    SelectTargetAction,
    SetPausedAction,
    SetSpeedAction,
    WaitAction,
    canonical_sha256,
)

PROTOCOL_SCHEMA = "omnicompany.arknights.tournament-sim.ndjson.v1"
PROTOCOL_STATE_SCHEMA = "omnicompany.arknights.tournament-sim.protocol-state.v1"
PROTOCOL_STATE_NAMESPACE = "arknights_tournament_sim_protocol_v1"
MAX_REQUEST_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_ACTIVE_SESSIONS = 64
DEFAULT_MAX_TOTAL_REQUESTS = 100_000
MAX_FORECAST_ACTIONS = 1_024


class _ResourceLimitError(RuntimeError):
    pass


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _Deploy(_StrictContract):
    kind: Literal["deploy"]
    operator_id: str = Field(min_length=1)
    position: GridPoint
    direction: Direction = Direction.RIGHT


class _Retreat(_StrictContract):
    kind: Literal["retreat"]
    instance_id: str = Field(min_length=1)


class _ActivateSkill(_StrictContract):
    kind: Literal["activate_skill"]
    instance_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)


class _SelectTarget(_StrictContract):
    kind: Literal["select_target"]
    instance_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    target_instance_id: str = Field(min_length=1)


class _SetPaused(_StrictContract):
    kind: Literal["set_paused"]
    paused: bool


class _SetSpeed(_StrictContract):
    kind: Literal["set_speed"]
    speed: Literal[1, 2]


class _Wait(_StrictContract):
    kind: Literal["wait"]
    ticks: int = Field(default=1, ge=1, le=300)


_ProtocolAction = Annotated[
    Union[
        _Deploy,
        _Retreat,
        _ActivateSkill,
        _SelectTarget,
        _SetPaused,
        _SetSpeed,
        _Wait,
    ],
    Field(discriminator="kind"),
]
_ACTION_ADAPTER = TypeAdapter(_ProtocolAction)


class _RequestEnvelope(_StrictContract):
    schema_id: Literal[PROTOCOL_SCHEMA]
    request_id: str = Field(min_length=1, max_length=128)
    method: Literal[
        "session.create",
        "session.reset",
        "session.close",
        "observe",
        "legal_actions",
        "step",
        "forecast",
        "checkpoint",
    ]
    params: dict[str, Any]


class _CreateParams(_StrictContract):
    session_id: str = Field(min_length=1, max_length=128)
    template_id: str = Field(min_length=1, max_length=128)


class _SessionParams(_StrictContract):
    session_id: str = Field(min_length=1, max_length=128)


class _ObserveParams(_SessionParams):
    view: Literal["known_only"] = "known_only"


class _StepParams(_SessionParams):
    action: dict[str, Any]


class _ForecastParams(_SessionParams):
    action_sequence: list[dict[str, Any]] = Field(max_length=MAX_FORECAST_ACTIONS)
    horizon_ticks: int = Field(ge=1, le=300)


@dataclass(slots=True)
class _Session:
    template_id: str
    content: ContentBundle
    initial_state: InitialState
    engine: BattleEngine


@dataclass(frozen=True, slots=True)
class SessionTemplate:
    """Host-provisioned content/state and immutable per-run ledger identity."""

    content: ContentBundle
    ledger_id: str
    initial_state: InitialState | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        if not self.ledger_id or len(self.ledger_id) > 256:
            raise ValueError("ledger_id must be within 1..256 characters")
        if self.run_id is not None and (
            not self.run_id or self.run_id != self.run_id.strip() or len(self.run_id) > 256
        ):
            raise ValueError("run_id must be canonical and within 1..256 characters")


@dataclass(frozen=True, slots=True)
class DurableSimulatorSessionBinding:
    """Host-only view proving one restored production session binding."""

    session_id: str
    template_id: str
    content_hash: str
    ledger_id: str
    run_id: str
    observation: KnownOnlyObservation


class NDJSONSimulatorProtocol:
    """In-process protocol endpoint suitable for stdio or a Python SDK wrapper."""

    def __init__(
        self,
        templates: Mapping[str, SessionTemplate] | None = None,
        *,
        max_active_sessions: int = DEFAULT_MAX_ACTIVE_SESSIONS,
        max_total_requests: int = DEFAULT_MAX_TOTAL_REQUESTS,
        state_store: AtomicStateStore | None = None,
        tournament_id: str = "local-tournament",
        run_scope: str = "default",
    ) -> None:
        if max_active_sessions < 1 or max_total_requests < 1:
            raise ValueError("protocol resource limits must be positive")
        self._templates = dict(templates or {})
        if any(not key or len(key) > 128 for key in self._templates):
            raise ValueError("template ids must be within 1..128 characters")
        self._sessions: dict[str, _Session] = {}
        self._retired_session_ids: set[str] = set()
        self._used_template_ids: set[str] = set()
        self._request_ids: set[str] = set()
        self._total_requests = 0
        self._max_active_sessions = max_active_sessions
        self._max_total_requests = max_total_requests
        self._persistent = state_store is not None
        self._store: AtomicStateStore | None = None
        self._binding: LocalStateBindingV1 | None = None
        self._provided_templates = self._serialize_templates(self._templates)
        self._forecast_executions: dict[str, dict[str, Any]] = {}
        self._host_registrations: dict[str, dict[str, Any]] = {}
        if self._persistent:
            self._binding = LocalStateBindingV1(
                tournament_id=tournament_id,
                run_scope=run_scope,
            )
            self._binding.validate()
            self._store = require_local_state_store(
                state_store,
                persistent=True,
                fixture_id=f"sim-protocol:{run_scope}",
            )
            restored = self._store.transact(
                PROTOCOL_STATE_NAMESPACE,
                self._binding,
                self._initialize_persistent_state,
            )
            self._restore_persistent_state(restored)

    def handle_line(self, line: str | bytes) -> str:
        """Consume exactly one line and return exactly one compact JSON response."""

        if self._persistent:
            return self._handle_line_persistent(line)
        return self._handle_line_in_memory(line)

    def _handle_line_in_memory(self, line: str | bytes) -> str:
        """In-memory path used when no local SQLite store is requested."""

        request_id: str | None = None
        try:
            if self._total_requests >= self._max_total_requests:
                return self._encode_error(None, "rate_limited", "request limit reached")
            self._total_requests += 1
            raw = line.encode("utf-8") if isinstance(line, str) else line
            if not raw or len(raw) > MAX_REQUEST_BYTES:
                return self._encode_error(None, "invalid_request", "invalid request")
            payload = json.loads(
                raw,
                object_pairs_hook=_unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-finite number: {value}")
                ),
            )
            if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
                request_id = payload["request_id"]
            request = _RequestEnvelope.model_validate(payload, strict=True)
            request_id = request.request_id
            if request_id in self._request_ids:
                return self._encode_error(request_id, "duplicate_request", "duplicate request")
            self._request_ids.add(request_id)
            result = self._dispatch(request)
            return self._encode_success(request_id, result)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError, ValueError):
            return self._encode_error(request_id, "invalid_request", "invalid request")
        except KeyError:
            return self._encode_error(request_id, "session_not_found", "session not found")
        except IllegalActionError:
            return self._encode_error(request_id, "illegal_action", "illegal action")
        except TournamentSimError:
            return self._encode_error(request_id, "simulation_rejected", "simulation rejected")
        except _ResourceLimitError:
            return self._encode_error(request_id, "resource_limit", "resource limit reached")
        except Exception:  # pragma: no cover - last-resort secrecy boundary
            return self._encode_error(request_id, "internal_error", "internal error")

    def _handle_line_persistent(self, line: str | bytes) -> str:
        request_id: str | None = None
        request: _RequestEnvelope | None = None
        parse_error = False
        try:
            raw = line.encode("utf-8") if isinstance(line, str) else line
            if not raw or len(raw) > MAX_REQUEST_BYTES:
                parse_error = True
            else:
                payload = json.loads(
                    raw,
                    object_pairs_hook=_unique_object,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"non-finite number: {value}")
                    ),
                )
                if isinstance(payload, dict) and isinstance(payload.get("request_id"), str):
                    request_id = payload["request_id"]
                request = _RequestEnvelope.model_validate(payload, strict=True)
                request_id = request.request_id
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, TypeError, ValueError):
            parse_error = True

        response: list[str] = []

        def mutate(current: dict[str, Any] | None) -> Mapping[str, Any]:
            if current is None:
                raise PersistentStateError("simulator protocol durable state disappeared")
            self._restore_persistent_state(current)
            if self._total_requests >= self._max_total_requests:
                response.append(self._encode_error(None, "rate_limited", "request limit reached"))
                return self._serialize_persistent_state()
            self._total_requests += 1
            if parse_error or request is None:
                response.append(
                    self._encode_error(request_id, "invalid_request", "invalid request")
                )
                return self._serialize_persistent_state()
            if request.request_id in self._request_ids:
                response.append(
                    self._encode_error(request.request_id, "duplicate_request", "duplicate request")
                )
                return self._serialize_persistent_state()
            self._request_ids.add(request.request_id)
            try:
                result = self._dispatch(request)
                if request.method == "forecast":
                    forecast_result = ForecastResult.model_validate(result)
                    self._record_forecast_execution(request, forecast_result)
                response.append(self._encode_success(request.request_id, result))
            except (
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValidationError,
                TypeError,
                ValueError,
            ):
                response.append(
                    self._encode_error(request.request_id, "invalid_request", "invalid request")
                )
            except KeyError:
                response.append(
                    self._encode_error(request.request_id, "session_not_found", "session not found")
                )
            except IllegalActionError:
                response.append(
                    self._encode_error(request.request_id, "illegal_action", "illegal action")
                )
            except TournamentSimError:
                response.append(
                    self._encode_error(
                        request.request_id, "simulation_rejected", "simulation rejected"
                    )
                )
            except _ResourceLimitError:
                response.append(
                    self._encode_error(
                        request.request_id, "resource_limit", "resource limit reached"
                    )
                )
            except Exception:  # pragma: no cover - secrecy boundary inside the transaction
                response.append(
                    self._encode_error(request.request_id, "internal_error", "internal error")
                )
            return self._serialize_persistent_state()

        try:
            if self._store is None or self._binding is None:
                raise PersistentStateError("simulator protocol persistent store is unavailable")
            committed = self._store.transact(PROTOCOL_STATE_NAMESPACE, self._binding, mutate)
            self._restore_persistent_state(committed)
        except Exception:  # noqa: BLE001 - durable state is a fail-closed trust boundary
            return self._encode_error(request_id, "internal_error", "internal error")
        return response[0]

    def serve(self, lines: Iterable[str | bytes]) -> Iterator[str]:
        """Yield one response for every supplied input line, including malformed lines."""

        for line in lines:
            yield self.handle_line(
                line.rstrip(b"\r\n") if isinstance(line, bytes) else line.rstrip("\r\n")
            )

    def host_session_binding(self, session_id: str) -> DurableSimulatorSessionBinding:
        """Restore an exact host-private binding without consuming protocol budget."""

        if not self._production or self._store is None or self._binding is None:
            raise PersistentStateError("host session binding requires a production protocol")
        current = self._store.read(PROTOCOL_STATE_NAMESPACE, self._binding)
        if current is None:
            raise PersistentStateError("simulator protocol durable state disappeared")
        self._restore_persistent_state(current)
        try:
            session = self._sessions[session_id]
            template = self._templates[session.template_id]
        except KeyError:
            raise PersistentStateError("simulator session binding is unavailable") from None
        if template.run_id is None:
            raise PersistentStateError("production simulator run binding is unavailable")
        return DurableSimulatorSessionBinding(
            session_id=session_id,
            template_id=session.template_id,
            content_hash=session.content.content_hash,
            ledger_id=session.engine.export_checkpoint().budget.ledger_id,
            run_id=template.run_id,
            observation=session.engine.observe("known_only"),
        )

    def recover_forecast_execution(
        self,
        *,
        request_id: str,
        session_id: str,
        actions: Sequence[PublicAction],
        horizon_ticks: int,
    ) -> ForecastResult | None:
        """Return one exact durable execution, or ``None`` before it exists.

        This is deliberately host-only.  It closes the crash window after the
        simulator transaction commits and before its caller records the result;
        it never executes work, increments a counter, or exposes templates.
        """

        if not self._production or self._store is None or self._binding is None:
            raise PersistentStateError("forecast recovery requires a production protocol")
        normalized_actions = tuple(
            _parse_action(action.model_dump(mode="json")) for action in actions
        )
        current = self._store.read(PROTOCOL_STATE_NAMESPACE, self._binding)
        if current is None:
            raise PersistentStateError("simulator protocol durable state disappeared")
        self._restore_persistent_state(current)
        raw = self._forecast_executions.get(request_id)
        if raw is None:
            return None
        expected_actions = [action.model_dump(mode="json") for action in normalized_actions]
        if (
            raw.get("request_id") != request_id
            or raw.get("session_id") != session_id
            or raw.get("actions") != expected_actions
            or raw.get("horizon_ticks") != horizon_ticks
        ):
            raise TournamentSimError("durable forecast request binding conflicts")
        result = ForecastResult.model_validate(raw.get("result"))
        if raw.get("result_hash") != canonical_sha256(result.model_dump(mode="json")):
            raise PersistentStateError("durable forecast result hash drifted")
        return result

    def _dispatch(self, request: _RequestEnvelope) -> object:
        parsers: dict[str, type[BaseModel]] = {
            "session.create": _CreateParams,
            "session.reset": _SessionParams,
            "session.close": _SessionParams,
            "observe": _ObserveParams,
            "legal_actions": _SessionParams,
            "step": _StepParams,
            "forecast": _ForecastParams,
            "checkpoint": _SessionParams,
        }
        params = parsers[request.method].model_validate(request.params, strict=True)
        if isinstance(params, _CreateParams):
            return self._create(params)
        session = self._sessions[params.session_id]
        if request.method == "session.reset":
            if session.engine.commands:
                raise ValueError("a mutated run cannot be reset")
            session.engine = BattleEngine(
                session.content,
                session.initial_state,
                budget_ledger=ForecastBudgetLedger.from_state(
                    session.engine.export_checkpoint().budget
                ),
                allow_omniscient=False,
            )
            return self._session_summary(params.session_id, session)
        if request.method == "session.close":
            del self._sessions[params.session_id]
            self._retired_session_ids.add(params.session_id)
            return {"session_id": params.session_id, "closed": True}
        if request.method == "observe":
            return session.engine.observe("known_only").model_dump(mode="json")
        if request.method == "legal_actions":
            return {
                "session_id": params.session_id,
                "actions": [
                    action.model_dump(mode="json") for action in session.engine.legal_actions()
                ],
            }
        if isinstance(params, _StepParams):
            action = _parse_action(params.action)
            return session.engine.step(action).model_dump(mode="json")
        if isinstance(params, _ForecastParams):
            actions = tuple(_parse_action(item) for item in params.action_sequence)
            return session.engine.forecast(actions, horizon_ticks=params.horizon_ticks).model_dump(
                mode="json"
            )
        if request.method == "checkpoint":
            return self._checkpoint(params.session_id, session)
        raise AssertionError("unreachable method dispatch")

    def _create(self, params: _CreateParams) -> object:
        if params.session_id in self._sessions or params.session_id in self._retired_session_ids:
            raise ValueError("session exists")
        if params.template_id in self._used_template_ids:
            raise ValueError("run template was already consumed")
        if len(self._sessions) >= self._max_active_sessions:
            raise _ResourceLimitError("active session limit reached")
        template = self._templates.get(params.template_id)
        if template is None:
            raise ValueError("unknown run template")
        content = template.content
        initial = (
            template.initial_state
            if template.initial_state is not None
            else InitialState(
                content_hash=content.content_hash,
                cost=content.initial_cost,
                life_points=content.initial_life_points,
            )
        )
        ledger = ForecastBudgetLedger(template.ledger_id)
        engine = BattleEngine(
            content,
            initial,
            budget_ledger=ledger,
            allow_omniscient=False,
        )
        session = _Session(
            template_id=params.template_id,
            content=content,
            initial_state=initial,
            engine=engine,
        )
        self._sessions[params.session_id] = session
        self._used_template_ids.add(params.template_id)
        return self._session_summary(params.session_id, session)

    @staticmethod
    def _session_summary(session_id: str, session: _Session) -> dict[str, object]:
        budget = session.engine.export_checkpoint().budget
        return {
            "session_id": session_id,
            "content_hash": session.content.content_hash,
            "observation": session.engine.observe("known_only").model_dump(mode="json"),
            "budget": {
                "ledger_id": budget.ledger_id,
                "total_raw_ops": budget.total_raw_ops,
                "total_forecasts": budget.total_forecasts,
            },
        }

    @staticmethod
    def _checkpoint(session_id: str, session: _Session) -> dict[str, object]:
        checkpoint = session.engine.export_checkpoint()
        budget = checkpoint.budget
        body = {
            "session_id": session_id,
            "content_hash": session.content.content_hash,
            "snapshot_hash": session.engine.snapshot.snapshot_hash,
            "tick": session.engine.tick,
            "command_count": len(session.engine.commands),
            "budget": {
                "ledger_id": budget.ledger_id,
                "total_raw_ops": budget.total_raw_ops,
                "total_forecasts": budget.total_forecasts,
                "last_receipt_hash": (
                    budget.receipts[-1].receipt_hash if budget.receipts else None
                ),
            },
        }
        return {**body, "checkpoint_hash": canonical_sha256(body)}

    @staticmethod
    def _serialize_templates(
        templates: Mapping[str, SessionTemplate],
    ) -> dict[str, dict[str, Any]]:
        return {
            template_id: {
                "content": template.content.model_dump(mode="json"),
                "content_hash": template.content.content_hash,
                "ledger_id": template.ledger_id,
                "run_id": template.run_id,
                "initial_state": (
                    template.initial_state.model_dump(mode="json")
                    if template.initial_state is not None
                    else None
                ),
            }
            for template_id, template in sorted(templates.items())
        }

    @staticmethod
    def _deserialize_templates(
        payload: Mapping[str, Any],
    ) -> dict[str, SessionTemplate]:
        templates: dict[str, SessionTemplate] = {}
        for template_id, raw in payload.items():
            if not isinstance(template_id, str) or not isinstance(raw, dict):
                raise PersistentStateError("simulator template registry is corrupt")
            content = ContentBundle.model_validate(raw.get("content"))
            if raw.get("content_hash") != content.content_hash:
                raise PersistentStateError("simulator template content hash drifted")
            initial_raw = raw.get("initial_state")
            initial = InitialState.model_validate(initial_raw) if initial_raw is not None else None
            templates[template_id] = SessionTemplate(
                content=content,
                ledger_id=str(raw.get("ledger_id", "")),
                initial_state=initial,
                run_id=raw.get("run_id") if isinstance(raw.get("run_id"), str) else None,
            )
        return templates

    def _initialize_persistent_state(self, current: dict[str, Any] | None) -> Mapping[str, Any]:
        if current is None:
            if self._production and (
                not self._templates
                or any(template.run_id is None for template in self._templates.values())
            ):
                raise PersistentStateError(
                    "production simulator templates require immutable run bindings"
                )
            return {
                "schema_id": PROTOCOL_STATE_SCHEMA,
                "templates": self._provided_templates,
                "sessions": {},
                "retired_session_ids": [],
                "used_template_ids": [],
                "request_ids": [],
                "total_requests": 0,
                "max_active_sessions": self._max_active_sessions,
                "max_total_requests": self._max_total_requests,
                "forecast_executions": {},
                "host_registrations": {},
            }
        self._restore_persistent_state(current)
        if self._provided_templates and self._provided_templates != current.get("templates"):
            raise PersistentStateError("simulator template registry content drifted")
        return current

    def _restore_persistent_state(self, payload: Mapping[str, Any]) -> None:
        expected_keys = {
            "schema_id",
            "templates",
            "sessions",
            "retired_session_ids",
            "used_template_ids",
            "request_ids",
            "total_requests",
            "max_active_sessions",
            "max_total_requests",
            "forecast_executions",
            "host_registrations",
        }
        if set(payload) != expected_keys or payload.get("schema_id") != PROTOCOL_STATE_SCHEMA:
            raise PersistentStateError("simulator protocol state schema drifted")
        if (
            payload.get("max_active_sessions") != self._max_active_sessions
            or payload.get("max_total_requests") != self._max_total_requests
        ):
            raise PersistentStateError("simulator protocol resource limits drifted")
        raw_templates = payload.get("templates")
        raw_sessions = payload.get("sessions")
        if not isinstance(raw_templates, dict) or not isinstance(raw_sessions, dict):
            raise PersistentStateError("simulator protocol registry is corrupt")
        templates = self._deserialize_templates(raw_templates)
        if self._production and any(template.run_id is None for template in templates.values()):
            raise PersistentStateError("production simulator template run binding is missing")
        sessions: dict[str, _Session] = {}
        for session_id, raw in raw_sessions.items():
            if not isinstance(session_id, str) or not isinstance(raw, dict):
                raise PersistentStateError("simulator session registry is corrupt")
            template_id = raw.get("template_id")
            if not isinstance(template_id, str) or template_id not in templates:
                raise PersistentStateError("simulator session template is missing")
            checkpoint = EngineCheckpoint.model_validate(raw.get("checkpoint"))
            template = templates[template_id]
            engine = BattleEngine.restore_checkpoint(template.content, checkpoint)
            initial = template.initial_state or InitialState(
                content_hash=template.content.content_hash,
                cost=template.content.initial_cost,
                life_points=template.content.initial_life_points,
            )
            sessions[session_id] = _Session(
                template_id=template_id,
                content=template.content,
                initial_state=initial,
                engine=engine,
            )
        retired = set(payload.get("retired_session_ids", []))
        used = set(payload.get("used_template_ids", []))
        request_ids = set(payload.get("request_ids", []))
        if (
            not all(isinstance(value, str) for value in retired | used | request_ids)
            or retired & set(sessions)
            or not {item.template_id for item in sessions.values()} <= used
        ):
            raise PersistentStateError("simulator protocol identity registry is corrupt")
        total_requests = payload.get("total_requests")
        executions = payload.get("forecast_executions")
        registrations = payload.get("host_registrations")
        if (
            not isinstance(total_requests, int)
            or total_requests < 0
            or not isinstance(executions, dict)
            or not isinstance(registrations, dict)
        ):
            raise PersistentStateError("simulator protocol counters are corrupt")
        self._templates = templates
        self._sessions = sessions
        self._retired_session_ids = retired
        self._used_template_ids = used
        self._request_ids = request_ids
        self._total_requests = total_requests
        self._forecast_executions = dict(executions)
        self._host_registrations = dict(registrations)

    def _serialize_persistent_state(self) -> dict[str, Any]:
        return {
            "schema_id": PROTOCOL_STATE_SCHEMA,
            "templates": self._serialize_templates(self._templates),
            "sessions": {
                session_id: {
                    "template_id": session.template_id,
                    "checkpoint": session.engine.export_checkpoint().model_dump(mode="json"),
                }
                for session_id, session in sorted(self._sessions.items())
            },
            "retired_session_ids": sorted(self._retired_session_ids),
            "used_template_ids": sorted(self._used_template_ids),
            "request_ids": sorted(self._request_ids),
            "total_requests": self._total_requests,
            "max_active_sessions": self._max_active_sessions,
            "max_total_requests": self._max_total_requests,
            "forecast_executions": self._forecast_executions,
            "host_registrations": self._host_registrations,
        }

    def _record_forecast_execution(
        self,
        request: _RequestEnvelope,
        result: ForecastResult,
    ) -> None:
        params = _ForecastParams.model_validate(request.params, strict=True)
        session = self._sessions[params.session_id]
        actions = tuple(_parse_action(item) for item in params.action_sequence)
        checkpoint = session.engine.export_checkpoint()
        if request.request_id in self._forecast_executions:
            raise TournamentSimError("forecast execution request replayed")
        self._forecast_executions[request.request_id] = {
            "request_id": request.request_id,
            "session_id": params.session_id,
            "template_id": session.template_id,
            "content_hash": session.content.content_hash,
            "ledger_id": checkpoint.budget.ledger_id,
            "run_id": self._templates[session.template_id].run_id,
            "actions": [action.model_dump(mode="json") for action in actions],
            "horizon_ticks": params.horizon_ticks,
            "result": result.model_dump(mode="json"),
            "result_hash": canonical_sha256(result.model_dump(mode="json")),
            "engine_checkpoint_hash": checkpoint.checkpoint_hash,
        }

    @staticmethod
    def _encode_success(request_id: str, result: object) -> str:
        return json.dumps(
            {
                "schema_id": PROTOCOL_SCHEMA,
                "request_id": request_id,
                "ok": True,
                "result": result,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _encode_error(request_id: str | None, code: str, message: str) -> str:
        return json.dumps(
            {
                "schema_id": PROTOCOL_SCHEMA,
                "request_id": request_id,
                "ok": False,
                "error": {"code": code, "message": message},
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def _parse_action(payload: dict[str, Any]) -> PublicAction:
    parsed = _ACTION_ADAPTER.validate_json(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), strict=True
    )
    if isinstance(parsed, _Deploy):
        return DeployAction(
            operator_id=parsed.operator_id,
            position=parsed.position,
            direction=parsed.direction,
        )
    if isinstance(parsed, _Retreat):
        return RetreatAction(instance_id=parsed.instance_id)
    if isinstance(parsed, _ActivateSkill):
        return ActivateSkillAction(instance_id=parsed.instance_id, skill_id=parsed.skill_id)
    if isinstance(parsed, _SelectTarget):
        return SelectTargetAction(
            instance_id=parsed.instance_id,
            skill_id=parsed.skill_id,
            target_instance_id=parsed.target_instance_id,
        )
    if isinstance(parsed, _SetPaused):
        return SetPausedAction(paused=parsed.paused)
    if isinstance(parsed, _SetSpeed):
        return SetSpeedAction(speed=parsed.speed)
    return WaitAction(ticks=parsed.ticks)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def request_line(request_id: str, method: str, params: dict[str, Any]) -> str:
    """Build a canonical request line for Python clients without hiding validation."""

    envelope = _RequestEnvelope.model_validate(
        {
            "schema_id": PROTOCOL_SCHEMA,
            "request_id": request_id,
            "method": method,
            "params": params,
        },
        strict=True,
    )
    return json.dumps(
        envelope.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "DurableSimulatorSessionBinding",
    "MAX_REQUEST_BYTES",
    "NDJSONSimulatorProtocol",
    "PROTOCOL_SCHEMA",
    "PROTOCOL_STATE_NAMESPACE",
    "PROTOCOL_STATE_SCHEMA",
    "SessionTemplate",
    "request_line",
]
