# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-28 type=battle-program-executor status=active
"""Direct execution of the shared battle-program IR by :class:`BattleEngine`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..battle_program_ir import (
    BattleProgramAudit,
    BattleProgramIR,
    ProgramAction,
    ProgramDeployAction,
    ProgramRetreatAction,
    ProgramSkillAction,
    ProgramSpeedUpAction,
    ProgramTile,
    require_engine_compatible,
    tile_to_canonical,
)
from .model_primitives import BattleStatus, Direction
from .models import (
    ActivateSkillAction,
    DeployAction,
    GridPoint,
    RetreatAction,
    SetSpeedAction,
    WaitAction,
    canonical_sha256,
)

if TYPE_CHECKING:
    from .engine import BattleEngine


PROGRAM_EXECUTION_SCHEMA = "omnicompany.arknights.program-execution.v4"
MAX_PROGRAM_EXECUTION_TICKS = 216_000
# Resident battle state exposes ``negative_recovery_multiplier = 0.5`` during
# the exact paused LIVE run.  Deployment gesture transport therefore advances
# the DP clock at half rate rather than the earlier underfit 0.2 calibration.
DEFAULT_DEPLOYMENT_INPUT_COST_RECOVERY_MULTIPLIER_MILLI = 500
DEFAULT_DEPLOYMENT_INPUT_BATTLE_PROGRESS_MULTIPLIER_MILLI = 1000
DeploymentInputLeadPolicy = Literal["nominal", "lower", "upper"]


class _ReceiptContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProgramConditionObservation(_ReceiptContract):
    tick: int = Field(ge=0)
    cost: int = Field(ge=0)
    kills: int = Field(ge=0)
    cost_change: int
    cooling: int = Field(ge=0)
    costs_satisfied: bool
    kills_satisfied: bool
    cost_changes_satisfied: bool
    cooling_satisfied: bool
    absolute_tick_satisfied: bool
    latched_conditions: tuple[
        Literal["cost_changes", "kills", "costs", "cooling", "absolute_tick"],
        ...,
    ]
    all_satisfied: bool


class ProgramActionExecution(_ReceiptContract):
    action_id: str
    kind: Literal["deploy", "skill", "retreat", "speed_up"]
    status: Literal[
        "executed",
        "skipped_not_ready",
        "timed_out",
        "execution_limit",
        "battle_ended",
        "non_temporal_failure",
    ]
    failure_mechanism: str | None = None
    ir_operator_id: str
    maa_name: str
    engine_operator_id: str
    start_tick: int = Field(ge=0)
    condition_satisfied_tick: int | None = Field(default=None, ge=0)
    condition_observed_tick: int | None = Field(default=None, ge=0)
    native_ready_tick: int | None = Field(default=None, ge=0)
    input_issue_tick: int | None = Field(default=None, ge=0)
    effect_tick: int | None = Field(default=None, ge=0)
    intended_input_tick: int = Field(ge=0)
    intended_effect_tick: int = Field(ge=0)
    input_tick_delta: int | None = None
    effect_tick_delta: int | None = None
    end_tick: int = Field(ge=0)
    condition_wait_ticks: int = Field(ge=0)
    configured_condition_observation_delay_ticks: int = Field(ge=0)
    condition_observation_wait_ticks: int = Field(ge=0)
    pre_delay_ticks: int = Field(ge=0)
    native_wait_ticks: int = Field(ge=0)
    native_cost_wait_ticks: int = Field(ge=0)
    deployment_input_lead_policy: DeploymentInputLeadPolicy
    configured_input_lead_ticks: int = Field(ge=0)
    configured_input_cost_recovery_multiplier_milli: int = Field(ge=0, le=1000)
    configured_input_battle_progress_multiplier_milli: int = Field(ge=0, le=1000)
    configured_input_spawn_progress_multiplier_milli: int = Field(ge=0, le=1000)
    input_lead_wait_ticks: int = Field(ge=0)
    post_delay_ticks: int = Field(ge=0)
    source_tile: ProgramTile | None = None
    engine_tile: ProgramTile | None = None
    condition_observation: ProgramConditionObservation


class BattleProgramExecutionReceipt(_ReceiptContract):
    schema_id: Literal[PROGRAM_EXECUTION_SCHEMA] = PROGRAM_EXECUTION_SCHEMA
    program_id: str
    program_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    audit_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    deployment_input_lead_policy: DeploymentInputLeadPolicy
    deployment_condition_observation_delay_ticks: int = Field(ge=0)
    deployment_condition_observation_delay_ticks_interval: tuple[int, int] | None
    deployment_input_cost_recovery_multiplier_milli: int = Field(ge=0, le=1000)
    deployment_input_battle_progress_multiplier_milli: int = Field(ge=0, le=1000)
    start_tick: int = Field(ge=0)
    end_tick: int = Field(ge=0)
    completed: bool
    stop_reason: str | None = None
    actions: tuple[ProgramActionExecution, ...]
    final_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    def computed_hash(self) -> str:
        return canonical_sha256(self.model_dump(mode="json", exclude={"receipt_hash"}))


def _cooling_count(engine: "BattleEngine") -> int:
    return sum(item.redeploy_remaining > 0 for item in engine.roster.values())


def _observe_conditions(
    engine: "BattleEngine",
    action: ProgramAction,
    *,
    baseline_cost: int,
    latched_conditions: tuple[str, ...] = (),
) -> ProgramConditionObservation:
    trigger = action.trigger
    delta = engine.cost - baseline_cost
    cost_change_ok = trigger.cost_changes is None or delta >= trigger.cost_changes
    cooling = _cooling_count(engine)
    flags = {
        "costs_satisfied": trigger.costs is None or engine.cost >= trigger.costs,
        "kills_satisfied": trigger.kills is None or engine.kills >= trigger.kills,
        "cost_changes_satisfied": cost_change_ok,
        "cooling_satisfied": trigger.cooling is None or cooling >= trigger.cooling,
        "absolute_tick_satisfied": (
            trigger.absolute_tick is None or engine.tick >= trigger.absolute_tick
        ),
    }
    return ProgramConditionObservation(
        tick=engine.tick,
        cost=engine.cost,
        kills=engine.kills,
        cost_change=delta,
        cooling=cooling,
        **flags,
        latched_conditions=latched_conditions,
        all_satisfied=len(latched_conditions) == sum(
            value is not None
            for value in (
                trigger.cost_changes,
                trigger.kills,
                trigger.costs,
                trigger.cooling,
                trigger.absolute_tick,
            )
        ),
    )


_ORDERED_CONDITIONS = (
    ("cost_changes", "cost_changes_satisfied"),
    ("kills", "kills_satisfied"),
    ("costs", "costs_satisfied"),
    ("cooling", "cooling_satisfied"),
    ("absolute_tick", "absolute_tick_satisfied"),
)


def _deployed_instance(engine: "BattleEngine", operator_id: str) -> str | None:
    matches = sorted(
        instance_id
        for instance_id, state in engine.operators.items()
        if state.operator_id == operator_id
    )
    if len(matches) > 1:
        raise RuntimeError(f"multiple deployed instances for operator_id {operator_id!r}")
    return matches[0] if matches else None


def _execution_payload(
    *,
    program: BattleProgramIR,
    audit: BattleProgramAudit,
    engine: "BattleEngine",
    start_tick: int,
    completed: bool,
    stop_reason: str | None,
    actions: list[ProgramActionExecution],
    deployment_input_lead_policy: DeploymentInputLeadPolicy,
    deployment_condition_observation_delay_ticks: int,
    deployment_condition_observation_delay_ticks_interval: tuple[int, int] | None,
    deployment_input_cost_recovery_multiplier_milli: int,
    deployment_input_battle_progress_multiplier_milli: int,
) -> dict[str, object]:
    return {
        "schema_id": PROGRAM_EXECUTION_SCHEMA,
        "program_id": program.program_id,
        "program_hash": program.program_hash,
        "content_hash": engine.content.content_hash,
        "audit_hash": audit.audit_hash,
        "deployment_input_lead_policy": deployment_input_lead_policy,
        "deployment_condition_observation_delay_ticks": (
            deployment_condition_observation_delay_ticks
        ),
        "deployment_condition_observation_delay_ticks_interval": (
            deployment_condition_observation_delay_ticks_interval
        ),
        "deployment_input_cost_recovery_multiplier_milli": (
            deployment_input_cost_recovery_multiplier_milli
        ),
        "deployment_input_battle_progress_multiplier_milli": (
            deployment_input_battle_progress_multiplier_milli
        ),
        "start_tick": start_tick,
        "end_tick": engine.tick,
        "completed": completed,
        "stop_reason": stop_reason,
        "actions": [item.model_dump(mode="json") for item in actions],
        "final_snapshot_hash": engine.snapshot.snapshot_hash,
    }


def execute_battle_program(
    engine: "BattleEngine",
    program: BattleProgramIR,
    *,
    max_ticks: int = MAX_PROGRAM_EXECUTION_TICKS,
    deployment_input_lead_policy: DeploymentInputLeadPolicy = "nominal",
    deployment_condition_observation_delay_ticks: int = 0,
    deployment_condition_observation_delay_ticks_interval: tuple[int, int] | None = None,
    deployment_input_cost_recovery_multiplier_milli: int = (
        DEFAULT_DEPLOYMENT_INPUT_COST_RECOVERY_MULTIPLIER_MILLI
    ),
    deployment_input_battle_progress_multiplier_milli: int = (
        DEFAULT_DEPLOYMENT_INPUT_BATTLE_PROGRESS_MULTIPLIER_MILLI
    ),
) -> BattleProgramExecutionReceipt:
    """Execute ordered IR actions without a second caller-facing action format.

    The only translation boundary is internal and typed: after all shared IR
    conditions are satisfied, one corresponding ``PublicAction`` is issued to
    the kernel.  Deploy and skill actions additionally wait for the same native
    readiness conditions documented by MAA. Deploy actions preserve the IR's
    threshold-to-effect input lead instead of collapsing operator selection,
    gesture transport, and placement into the kernel's next tick.
    """

    if max_ticks < 1:
        raise ValueError("max_ticks must be positive")
    if deployment_input_lead_policy not in {"nominal", "lower", "upper"}:
        raise ValueError("unknown deployment_input_lead_policy")
    if deployment_condition_observation_delay_ticks < 0:
        raise ValueError("deployment_condition_observation_delay_ticks cannot be negative")
    if deployment_condition_observation_delay_ticks_interval is not None:
        lower, upper = deployment_condition_observation_delay_ticks_interval
        if lower < 0 or upper < lower:
            raise ValueError(
                "deployment_condition_observation_delay_ticks_interval is invalid"
            )
        if not lower <= deployment_condition_observation_delay_ticks <= upper:
            raise ValueError(
                "deployment_condition_observation_delay_ticks is outside its interval"
            )
    if not 0 <= deployment_input_cost_recovery_multiplier_milli <= 1000:
        raise ValueError(
            "deployment_input_cost_recovery_multiplier_milli must be within 0..1000"
        )
    if not 0 <= deployment_input_battle_progress_multiplier_milli <= 1000:
        raise ValueError(
            "deployment_input_battle_progress_multiplier_milli must be within 0..1000"
        )
    audit = require_engine_compatible(engine.content, program)
    program_start = engine.tick
    execution_limit = program_start + max_ticks
    receipts: list[ProgramActionExecution] = []
    operators = {item.operator_id: item for item in program.operators}
    bindings = {item.ir_operator_id: item for item in audit.operator_bindings}
    stop_reason: str | None = None

    def wait_one(
        action_start: int,
        timeout_ticks: int | None,
        *,
        cost_recovery_multiplier_milli: int = 1000,
        battle_progress_multiplier_milli: int = 1000,
        spawn_progress_multiplier_milli: int | None = None,
    ) -> str | None:
        if engine.status is not BattleStatus.RUNNING:
            return "battle_ended"
        if engine.tick >= execution_limit:
            return "execution_limit"
        if timeout_ticks is not None and engine.tick - action_start >= timeout_ticks:
            return "timed_out"
        engine.step(
            WaitAction(
                ticks=1,
                cost_recovery_multiplier_milli=cost_recovery_multiplier_milli,
                battle_progress_multiplier_milli=battle_progress_multiplier_milli,
                spawn_progress_multiplier_milli=spawn_progress_multiplier_milli,
            )
        )
        return None

    for action in program.actions:
        action_start = engine.tick
        baseline_cost = engine.cost
        condition_wait = 0
        condition_observation_wait = 0
        native_wait = 0
        native_cost_wait = 0
        input_lead_wait = 0
        configured_input_lead_ticks = action.intent.input_lead_ticks
        configured_condition_observation_delay_ticks = 0
        configured_input_cost_recovery_multiplier_milli = (
            action.intent.input_lead_cost_recovery_multiplier_milli
            if action.intent.input_lead_cost_recovery_multiplier_milli is not None
            else deployment_input_cost_recovery_multiplier_milli
        )
        configured_input_battle_progress_multiplier_milli = (
            action.intent.input_lead_battle_progress_multiplier_milli
            if action.intent.input_lead_battle_progress_multiplier_milli is not None
            else deployment_input_battle_progress_multiplier_milli
        )
        configured_input_spawn_progress_multiplier_milli = (
            action.intent.input_lead_spawn_progress_multiplier_milli
            if action.intent.input_lead_spawn_progress_multiplier_milli is not None
            else configured_input_battle_progress_multiplier_milli
        )
        if (
            isinstance(action, ProgramDeployAction)
            and action.intent.input_lead_ticks_interval is not None
            and deployment_input_lead_policy != "nominal"
        ):
            configured_input_lead_ticks = action.intent.input_lead_ticks_interval[
                0 if deployment_input_lead_policy == "lower" else 1
            ]
        if (
            isinstance(action, ProgramDeployAction)
            and action.intent.input_lead_cost_recovery_multiplier_milli_interval is not None
            and deployment_input_lead_policy != "nominal"
        ):
            configured_input_cost_recovery_multiplier_milli = (
                action.intent.input_lead_cost_recovery_multiplier_milli_interval[
                    0 if deployment_input_lead_policy == "lower" else 1
                ]
            )
        if isinstance(action, ProgramDeployAction):
            configured_condition_observation_delay_ticks = (
                action.intent.condition_observation_delay_ticks
                if action.intent.condition_observation_delay_ticks is not None
                else deployment_condition_observation_delay_ticks
            )
            observation_delay_interval = (
                action.intent.condition_observation_delay_ticks_interval
                if action.intent.condition_observation_delay_ticks_interval is not None
                else deployment_condition_observation_delay_ticks_interval
            )
            if observation_delay_interval is not None and deployment_input_lead_policy != "nominal":
                configured_condition_observation_delay_ticks = observation_delay_interval[
                    0 if deployment_input_lead_policy == "lower" else 1
                ]
        pre_wait = 0
        post_wait = 0
        source_tile: ProgramTile | None = None
        engine_tile: ProgramTile | None = None
        condition_tick: int | None = None
        condition_observed_tick: int | None = None
        native_ready_tick: int | None = None
        input_tick: int | None = None
        effect_tick: int | None = None
        failure_mechanism: str | None = None
        binding = bindings[action.operator_id]
        engine_operator_id = binding.engine_operator_id

        latched_conditions: list[str] = []
        observation = _observe_conditions(
            engine,
            action,
            baseline_cost=baseline_cost,
            latched_conditions=(),
        )
        terminal_status: str | None = None
        for condition_name, observation_field in _ORDERED_CONDITIONS:
            if getattr(action.trigger, condition_name) is None:
                continue
            while not getattr(observation, observation_field):
                terminal_status = wait_one(action_start, action.timeout_ticks)
                if terminal_status is not None:
                    failure_mechanism = f"ir_trigger_wait:{condition_name}"
                    break
                condition_wait += 1
                observation = _observe_conditions(
                    engine,
                    action,
                    baseline_cost=baseline_cost,
                    latched_conditions=tuple(latched_conditions),
                )
            if terminal_status is not None:
                break
            latched_conditions.append(condition_name)
            observation = _observe_conditions(
                engine,
                action,
                baseline_cost=baseline_cost,
                latched_conditions=tuple(latched_conditions),
            )
        if terminal_status is None:
            condition_tick = engine.tick

        while (
            terminal_status is None
            and isinstance(action, ProgramDeployAction)
            and condition_observation_wait
            < configured_condition_observation_delay_ticks
        ):
            terminal_status = wait_one(action_start, action.timeout_ticks)
            if terminal_status is not None:
                failure_mechanism = "deploy_condition_observation_delay"
                break
            condition_observation_wait += 1
        if terminal_status is None:
            condition_observed_tick = engine.tick

        while terminal_status is None and pre_wait < action.pre_delay_ticks:
            terminal_status = wait_one(action_start, action.timeout_ticks)
            if terminal_status is not None:
                failure_mechanism = "pre_input_delay"
                break
            pre_wait += 1

        public_action: (
            DeployAction | ActivateSkillAction | RetreatAction | SetSpeedAction | None
        ) = None
        if terminal_status is None and isinstance(action, ProgramDeployAction):
            source_tile = action.tile
            engine_tile = tile_to_canonical(
                action.tile,
                width=program.map_width,
                height=program.map_height,
            )
            point = GridPoint(col=engine_tile.col, row=engine_tile.row)
            definition = next(
                item for item in engine.content.operators if item.operator_id == engine_operator_id
            )
            if _deployed_instance(engine, engine_operator_id) is not None:
                terminal_status = "non_temporal_failure"
                failure_mechanism = "operator_already_deployed"
            elif any(
                state.position == point
                for state in (*engine.operators.values(), *engine.summons.values())
            ):
                terminal_status = "non_temporal_failure"
                failure_mechanism = "target_tile_occupied"
            else:
                candidate = DeployAction(
                    operator_id=engine_operator_id,
                    position=point,
                    direction=Direction(action.direction),
                )
                while candidate not in engine.legal_actions():
                    waiting_for_cost = definition.deploy_cost > engine.cost
                    terminal_status = wait_one(action_start, action.timeout_ticks)
                    if terminal_status is not None:
                        failure_mechanism = (
                            "native_deploy_cost_wait"
                            if waiting_for_cost
                            else "native_deploy_readiness_wait"
                        )
                        break
                    native_wait += 1
                    if waiting_for_cost:
                        native_cost_wait += 1
                if terminal_status is None:
                    native_ready_tick = engine.tick
                    public_action = candidate

        elif terminal_status is None and isinstance(action, ProgramSkillAction):
            instance_id = _deployed_instance(engine, engine_operator_id)
            skill_id = operators[action.operator_id].engine_skill_id
            if instance_id is None or skill_id is None:
                terminal_status = "non_temporal_failure"
                failure_mechanism = (
                    "operator_not_deployed" if instance_id is None else "engine_skill_id_missing"
                )
            else:
                candidate = ActivateSkillAction(instance_id=instance_id, skill_id=skill_id)
                if action.skip_if_not_ready and candidate not in engine.legal_actions():
                    terminal_status = "skipped_not_ready"
                    failure_mechanism = "native_skill_not_ready"
                else:
                    while candidate not in engine.legal_actions():
                        terminal_status = wait_one(action_start, action.timeout_ticks)
                        if terminal_status is not None:
                            failure_mechanism = "native_skill_readiness_wait"
                            break
                        native_wait += 1
                    if terminal_status is None:
                        native_ready_tick = engine.tick
                        public_action = candidate

        elif terminal_status is None and isinstance(action, ProgramRetreatAction):
            instance_id = _deployed_instance(engine, engine_operator_id)
            if instance_id is None:
                terminal_status = "non_temporal_failure"
                failure_mechanism = "operator_not_deployed"
            else:
                native_ready_tick = engine.tick
                public_action = RetreatAction(instance_id=instance_id)

        elif terminal_status is None and isinstance(action, ProgramSpeedUpAction):
            native_ready_tick = engine.tick
            public_action = SetSpeedAction(speed=2 if engine.speed == 1 else 1)

        if terminal_status is None and public_action is not None:
            if engine.tick >= execution_limit:
                terminal_status = "execution_limit"
                failure_mechanism = "input_issue_limit"
            else:
                input_tick = engine.tick
                if isinstance(action, ProgramDeployAction):
                    if configured_input_lead_ticks < 1:
                        terminal_status = "non_temporal_failure"
                        failure_mechanism = "deploy_input_lead_must_cover_kernel_effect_tick"
                    else:
                        required_wait = configured_input_lead_ticks - 1
                        while input_lead_wait < required_wait:
                            terminal_status = wait_one(
                                action_start,
                                action.timeout_ticks,
                                cost_recovery_multiplier_milli=(
                                    configured_input_cost_recovery_multiplier_milli
                                ),
                                battle_progress_multiplier_milli=(
                                    configured_input_battle_progress_multiplier_milli
                                ),
                                spawn_progress_multiplier_milli=(
                                    configured_input_spawn_progress_multiplier_milli
                                ),
                            )
                            if terminal_status is not None:
                                failure_mechanism = "deploy_input_lead_delay"
                                break
                            input_lead_wait += 1
                        if terminal_status is None and engine.status is not BattleStatus.RUNNING:
                            terminal_status = "battle_ended"
                            failure_mechanism = "deploy_input_lead_delay"
                        elif terminal_status is None and engine.tick >= execution_limit:
                            terminal_status = "execution_limit"
                            failure_mechanism = "deploy_input_lead_delay"
                        elif terminal_status is None and public_action not in engine.legal_actions():
                            terminal_status = "non_temporal_failure"
                            failure_mechanism = (
                                "deploy_readiness_changed_during_input_lead"
                            )
                if terminal_status is None:
                    engine.step(public_action)
                    effect_tick = engine.tick

        executed = terminal_status is None
        while executed and post_wait < action.post_delay_ticks:
            post_stop = wait_one(action_start, None)
            if post_stop is not None:
                stop_reason = f"{action.action_id}:{post_stop}:post_input_delay"
                break
            post_wait += 1

        status = "executed" if terminal_status is None else terminal_status
        receipt = ProgramActionExecution(
            action_id=action.action_id,
            kind=action.kind,
            status=status,
            failure_mechanism=failure_mechanism,
            ir_operator_id=action.operator_id,
            maa_name=action.operator.name,
            engine_operator_id=engine_operator_id,
            start_tick=action_start,
            condition_satisfied_tick=condition_tick,
            condition_observed_tick=condition_observed_tick,
            native_ready_tick=native_ready_tick,
            input_issue_tick=input_tick,
            effect_tick=effect_tick,
            intended_input_tick=action.intent.tap_tick,
            intended_effect_tick=action.intended_effect_tick,
            input_tick_delta=(None if input_tick is None else input_tick - action.intent.tap_tick),
            effect_tick_delta=(
                None if effect_tick is None else effect_tick - action.intended_effect_tick
            ),
            end_tick=engine.tick,
            condition_wait_ticks=condition_wait,
            configured_condition_observation_delay_ticks=(
                configured_condition_observation_delay_ticks
            ),
            condition_observation_wait_ticks=condition_observation_wait,
            pre_delay_ticks=pre_wait,
            native_wait_ticks=native_wait,
            native_cost_wait_ticks=native_cost_wait,
            deployment_input_lead_policy=deployment_input_lead_policy,
            configured_input_lead_ticks=configured_input_lead_ticks,
            configured_input_cost_recovery_multiplier_milli=(
                configured_input_cost_recovery_multiplier_milli
            ),
            configured_input_battle_progress_multiplier_milli=(
                configured_input_battle_progress_multiplier_milli
            ),
            configured_input_spawn_progress_multiplier_milli=(
                configured_input_spawn_progress_multiplier_milli
            ),
            input_lead_wait_ticks=input_lead_wait,
            post_delay_ticks=post_wait,
            source_tile=source_tile,
            engine_tile=engine_tile,
            condition_observation=observation,
        )
        receipts.append(receipt)
        if terminal_status == "skipped_not_ready":
            continue
        if terminal_status is not None:
            stop_reason = f"{action.action_id}:{terminal_status}:{failure_mechanism}"
            break
        if stop_reason is not None:
            break

    completed = len(receipts) == len(program.actions) and stop_reason is None
    payload = _execution_payload(
        program=program,
        audit=audit,
        engine=engine,
        start_tick=program_start,
        completed=completed,
        stop_reason=stop_reason,
        actions=receipts,
        deployment_input_lead_policy=deployment_input_lead_policy,
        deployment_condition_observation_delay_ticks=(
            deployment_condition_observation_delay_ticks
        ),
        deployment_condition_observation_delay_ticks_interval=(
            deployment_condition_observation_delay_ticks_interval
        ),
        deployment_input_cost_recovery_multiplier_milli=(
            deployment_input_cost_recovery_multiplier_milli
        ),
        deployment_input_battle_progress_multiplier_milli=(
            deployment_input_battle_progress_multiplier_milli
        ),
    )
    receipt = BattleProgramExecutionReceipt(
        **payload,
        receipt_hash=canonical_sha256(payload),
    )
    if receipt.receipt_hash != receipt.computed_hash():
        raise RuntimeError("program execution receipt hash is inconsistent")
    return receipt


__all__ = [
    "DEFAULT_DEPLOYMENT_INPUT_COST_RECOVERY_MULTIPLIER_MILLI",
    "DeploymentInputLeadPolicy",
    "MAX_PROGRAM_EXECUTION_TICKS",
    "PROGRAM_EXECUTION_SCHEMA",
    "BattleProgramExecutionReceipt",
    "ProgramActionExecution",
    "ProgramConditionObservation",
    "execute_battle_program",
]
