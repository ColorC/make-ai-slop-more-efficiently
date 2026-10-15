# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T10:00:00Z type=live-battle status=active
# [OMNI] summary="基于每一帧当前局势重新评分，以 SingleStep 单动作和 EffectOracle 驱动整场实战。"
# [OMNI] why="固定时间轴、代理指挥或作业回放不能证明 Agent 在战斗中感知和决策。"
# [OMNI] tags=game-observatory,arknights,live-battle,online-policy,closed-loop
"""无代理的实时战斗决策与控制循环。"""

from __future__ import annotations

import random
import statistics
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol, Sequence

from .action_loop import ActionLoopOutcomeV1, BattleActionLoop, BattleFrameSampler
from .battle_state import (
    BattleStateV1,
    DeployableOperatorV1,
    EnemyPressureV1,
    FriendlyOperatorV1,
)
from .effect_oracle import BattleFrameEnvelopeV1
from .maa_contracts import BattleActionCall

__all__ = [
    "BattleControlResultV1",
    "BattleControlStatus",
    "BattleDecisionV1",
    "BattleMemoryV1",
    "DecisionKind",
    "LiveBattleController",
    "ObservationCadence",
    "OneSevenOnlinePolicy",
    "StageOnlinePolicy",
    "TacticalSlotV1",
]


class DecisionKind(StrEnum):
    DEPLOY = "deploy"
    SKILL = "skill"
    WAIT = "wait"
    STOP = "stop"


class BattleControlStatus(StrEnum):
    SCENE_CHANGED = "scene_changed"
    STOPPED = "stopped"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True, slots=True)
class TacticalSlotV1:
    operator: str
    col: int
    row: int
    direction: str
    lane: str
    role: str
    base_score: float

    @property
    def tile(self) -> tuple[int, int]:
        return self.col, self.row


@dataclass(frozen=True, slots=True)
class BattleDecisionV1:
    kind: DecisionKind
    reason: str
    referenced_state: dict[str, Any]
    action_name: str | None = None
    details: dict[str, Any] | None = None
    score: float | None = None
    candidates: tuple[dict[str, Any], ...] = ()

    @property
    def actionable(self) -> bool:
        return self.kind in {DecisionKind.DEPLOY, DecisionKind.SKILL}

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "reason": self.reason,
            "referenced_state": self.referenced_state,
            "action_name": self.action_name,
            "details": self.details,
            "score": self.score,
            "candidates": list(self.candidates),
        }


@dataclass(slots=True)
class BattleMemoryV1:
    verified_actions: list[BattleDecisionV1] = field(default_factory=list)
    deployed_by_tile: dict[tuple[int, int], str] = field(default_factory=dict)
    skills_used: set[tuple[int, int]] = field(default_factory=set)
    consecutive_unknown: int = 0
    active_game_seconds: float | None = None
    last_cost: float | None = None
    last_cost_game_seconds: float | None = None
    last_cost_progress: float | None = None
    cost_capped: bool = False
    pending_verified_spend: int = 0
    net_dp_rates: list[float] = field(default_factory=list)

    @property
    def deployment_count(self) -> int:
        return sum(
            decision.kind is DecisionKind.DEPLOY
            for decision in self.verified_actions
        )

    def record_verified(self, decision: BattleDecisionV1) -> None:
        if not decision.actionable or decision.details is None:
            raise ValueError("only actionable decisions can be recorded as verified")
        self.verified_actions.append(decision)
        location = decision.details.get("location")
        if not isinstance(location, list) or len(location) != 2:
            return
        tile = int(location[0]), int(location[1])
        if decision.kind is DecisionKind.DEPLOY:
            self.deployed_by_tile[tile] = str(decision.details["name"])
            operator_name = str(decision.details["name"])
            deployable = decision.referenced_state.get("deployable") or []
            matching_costs = [
                int(item["cost"])
                for item in deployable
                if item.get("name") == operator_name
                and isinstance(item.get("cost"), int)
            ]
            if len(matching_costs) == 1:
                self.pending_verified_spend += matching_costs[0]
        elif decision.kind is DecisionKind.SKILL:
            self.skills_used.add(tile)

    def observe_frame(self, frame: BattleFrameEnvelopeV1) -> None:
        game_seconds = frame.active_game_seconds
        if game_seconds is None:
            return
        self.active_game_seconds = game_seconds
        observed_cost = frame.state.global_state.current_cost
        if not observed_cost.known or observed_cost.value is None:
            return
        observed_progress = frame.state.global_state.cost_progress
        progress = (
            float(observed_progress.value)
            if observed_progress.known and observed_progress.value is not None
            else None
        )
        progress_raw = observed_progress.evidence.raw_text
        self.cost_capped = "mode=capped" in progress_raw
        self.last_cost_progress = progress
        if self.cost_capped:
            # A MAX frame proves DP is pinned at the in-battle cap.  Suspend
            # rate sampling so the flat 99 plateau cannot distort regeneration.
            self.last_cost = None
            self.last_cost_game_seconds = None
            self.pending_verified_spend = 0
            return
        cost = float(observed_cost.value) + (progress or 0.0)
        if (
            self.last_cost is not None
            and self.last_cost_game_seconds is not None
            and game_seconds > self.last_cost_game_seconds
        ):
            delta = cost - self.last_cost + self.pending_verified_spend
            rate = delta / (game_seconds - self.last_cost_game_seconds)
            self.net_dp_rates.append(rate)
            del self.net_dp_rates[:-5]
            self.pending_verified_spend = 0
        self.last_cost = cost
        self.last_cost_game_seconds = game_seconds

    @property
    def seconds_until_next_dp(self) -> float | None:
        rate = self.net_dp_per_second
        progress = self.last_cost_progress
        if self.cost_capped or rate is None or rate <= 0 or progress is None:
            return None
        return max(0.0, (1.0 - progress) / rate)

    @property
    def net_dp_per_second(self) -> float | None:
        if not self.net_dp_rates:
            return None
        return float(statistics.median(self.net_dp_rates))

    @property
    def dp_regime(self) -> str:
        rate = self.net_dp_per_second
        if rate is None:
            return "unknown"
        if rate > 0.2:
            return "positive"
        if rate < -0.2:
            return "negative"
        return "stalled"


class OnlineBattlePolicy(Protocol):
    memory: BattleMemoryV1

    def decide(self, state: BattleStateV1) -> BattleDecisionV1: ...

    def observe_frame(self, frame: BattleFrameEnvelopeV1) -> None: ...

    def record_verified(self, decision: BattleDecisionV1) -> None: ...


class ActionCompiler(Protocol):
    def __call__(self, decision: BattleDecisionV1) -> BattleActionCall: ...


class ObservationCadence:
    """改变重感知节奏而不改变动作时序；随机数种子写入回执即可复现。"""

    def __init__(
        self,
        minimum_seconds: float,
        maximum_seconds: float | None = None,
        *,
        seed: int = 0,
    ) -> None:
        maximum = minimum_seconds if maximum_seconds is None else maximum_seconds
        if minimum_seconds < 0 or maximum < minimum_seconds:
            raise ValueError("invalid observation cadence range")
        self.minimum_seconds = minimum_seconds
        self.maximum_seconds = maximum
        self.seed = seed
        self._random = random.Random(seed)

    def next_delay(self) -> float:
        return self._random.uniform(self.minimum_seconds, self.maximum_seconds)


class OneSevenOnlinePolicy:
    """1-7 的状态驱动战术评分器。

    槽位是关卡知识；它们没有时间条件，也没有固定执行顺序。每一帧都根据当下卡片、费用、
    占位、击杀阶段、生命和可见三路压力重新筛选并排序。只有当前可支付、当前可部署且目标
    为空的候选才能形成动作。
    """

    _SLOTS: tuple[TacticalSlotV1, ...] = (
        TacticalSlotV1("芬", 1, 3, "Right", "middle", "core-vanguard", 230.0),
        TacticalSlotV1("翎羽", 4, 1, "Right", "top", "lane-vanguard", 172.0),
        TacticalSlotV1("玫兰莎", 4, 5, "Right", "bottom", "lane-guard", 168.0),
        TacticalSlotV1("克洛丝", 5, 2, "Right", "top", "ranged-dps", 158.0),
        TacticalSlotV1("安赛尔", 1, 4, "Right", "bottom", "medic", 148.0),
        TacticalSlotV1("米格鲁", 4, 3, "Right", "middle", "defender", 145.0),
        TacticalSlotV1("巡林者", 5, 4, "Right", "bottom", "ranged-dps", 132.0),
        TacticalSlotV1("Lancet-2", 1, 2, "Right", "top", "medic-robot", 126.0),
        TacticalSlotV1("杜林", 6, 2, "Right", "top", "caster", 116.0),
        TacticalSlotV1("史都华德", 6, 4, "Right", "bottom", "caster", 114.0),
    )
    _MANUAL_SKILL_OPERATORS = frozenset({"玫兰莎", "安赛尔", "米格鲁", "史都华德"})

    def __init__(
        self,
        *,
        maximum_deployments: int = 8,
        slots: Sequence[TacticalSlotV1] | None = None,
        expected_life: int | None = 10,
        manual_skill_operators: Sequence[str] | None = None,
    ) -> None:
        if maximum_deployments < 3:
            raise ValueError("maximum_deployments must be at least 3")
        if expected_life is not None and expected_life < 1:
            raise ValueError("expected_life must be at least 1")
        self.maximum_deployments = maximum_deployments
        self._slots = tuple(slots) if slots is not None else self._SLOTS
        self._facility_names = frozenset(
            slot.operator
            for slot in self._slots
            if slot.role.startswith("facility-")
        )
        self.expected_life = expected_life
        self._manual_skill_operator_set = (
            frozenset(manual_skill_operators)
            if manual_skill_operators is not None
            else self._MANUAL_SKILL_OPERATORS
        )
        self.memory = BattleMemoryV1()

    @property
    def combat_deployment_count(self) -> int:
        """Verified combat bodies; reusable pathing facilities do not count."""

        return sum(
            name not in self._facility_names
            for name in self.memory.deployed_by_tile.values()
        )

    def record_verified(self, decision: BattleDecisionV1) -> None:
        self.memory.record_verified(decision)
        self.memory.consecutive_unknown = 0

    def observe_frame(self, frame: BattleFrameEnvelopeV1) -> None:
        self.memory.observe_frame(frame)
        life = frame.state.global_state.life_points
        if self.expected_life is None and life.known:
            # Calibrate from the first fresh life read even when some unrelated
            # field (commonly opening DP OCR) is still unknown.  Delaying this
            # until a fully actionable state could adopt an already-leaked
            # value as the baseline.
            self.expected_life = int(life.value)

    def decide(self, state: BattleStateV1) -> BattleDecisionV1:
        referenced = _referenced_state(state)
        deployment_budget_exhausted = (
            self.memory.deployment_count >= self.maximum_deployments
        )
        referenced["temporal"] = {
            "active_game_seconds": self.memory.active_game_seconds,
            "cost_progress": state.global_state.cost_progress.value,
            "cost_capped": self.memory.cost_capped,
            "net_dp_per_second": self.memory.net_dp_per_second,
            "dp_regime": self.memory.dp_regime,
            "rate_sample_count": len(self.memory.net_dp_rates),
            "seconds_until_next_dp": self.memory.seconds_until_next_dp,
            "integer_cost_required_for_deploy": True,
            "progress_or_time_can_authorize_deploy": False,
            "deployment_budget_exhausted": deployment_budget_exhausted,
        }
        # A lightweight observation intentionally defers cards and battlefield
        # classifiers, but a freshly read life loss must still stop the run
        # immediately instead of waiting for the next full tactical frame.
        life_observation = state.global_state.life_points
        if (
            life_observation.known
            and self.expected_life is not None
            and int(life_observation.value) < self.expected_life
        ):
            return BattleDecisionV1(
                DecisionKind.STOP,
                (
                    f"life dropped to {int(life_observation.value)} below the observed "
                    f"stage baseline {self.expected_life}; three-star truth is no longer attainable"
                ),
                referenced,
            )
        unknown = _required_unknown_fields(state)
        if deployment_budget_exhausted:
            # No future branch can deploy, so an occluded/capped DP numeral is
            # irrelevant to observation and visually-gated skills.  Before the
            # deployment budget is exhausted, integer DP remains mandatory.
            unknown = [name for name in unknown if name not in {"cost", "deployable"}]
        if unknown:
            self.memory.consecutive_unknown += 1
            if self.memory.consecutive_unknown >= 6:
                return BattleDecisionV1(
                    DecisionKind.STOP,
                    f"required visual fields remained unknown for {self.memory.consecutive_unknown} frames",
                    referenced,
                )
            return BattleDecisionV1(
                DecisionKind.WAIT,
                f"reobserve because required visual fields are unknown: {', '.join(unknown)}",
                referenced,
            )
        self.memory.consecutive_unknown = 0

        if state.global_state.paused.value is True:
            return BattleDecisionV1(
                DecisionKind.WAIT,
                "captured battle frame is still paused; reobserve after low-level resume recovery",
                referenced,
            )

        life = int(state.global_state.life_points.value)
        kills = int(state.global_state.kills.value)
        total = int(state.global_state.total_enemies.value)
        if self.expected_life is None:
            # Establish an unknown stage's three-star life invariant from the
            # first trustworthy live HUD frame instead of guessing metadata.
            self.expected_life = life
        if life < self.expected_life:
            return BattleDecisionV1(
                DecisionKind.STOP,
                (
                    f"life dropped to {life} below the observed stage baseline "
                    f"{self.expected_life}; three-star truth is no longer attainable"
                ),
                referenced,
            )
        if kills >= total:
            return BattleDecisionV1(
                DecisionKind.WAIT,
                "all enemies are defeated; wait for settlement transition",
                referenced,
            )
        pressure = state.enemies.visible_pressure.value
        assert pressure is not None
        if (
            total - kills <= 1
            and pressure.visible_health_bars == 0
            and self.memory.deployment_count >= 3
        ):
            return BattleDecisionV1(
                DecisionKind.WAIT,
                (
                    "one enemy remains with no visible pressure; hold all clicks "
                    "for the settlement transition"
                ),
                referenced,
            )

        skill = self._skill_decision(state, referenced)
        if skill is not None:
            return skill
        deploy = self._deploy_decision(state, referenced)
        if deploy is not None:
            return deploy
        tempo = {
            "negative": "DP is visually declining; preserve the integer affordability gate",
            "stalled": "DP is visually stalled; do not assume another point will arrive",
            "positive": "DP is visually recovering; continue reobserving the integer value",
            "unknown": "DP tempo is not yet established",
        }[self.memory.dp_regime]
        return BattleDecisionV1(
            DecisionKind.WAIT,
            f"no currently affordable state-valid action; {tempo}",
            referenced,
        )

    def _skill_decision(
        self,
        state: BattleStateV1,
        referenced: dict[str, Any],
    ) -> BattleDecisionV1 | None:
        pressure = state.enemies.visible_pressure.value
        assert pressure is not None
        kills = int(state.global_state.kills.value)
        total = int(state.global_state.total_enemies.value)
        if pressure.visible_health_bars == 0 and kills < max(8, total // 4):
            return None
        deployed = state.friendly.deployed.value
        assert deployed is not None
        choices: list[tuple[float, tuple[int, int], str]] = []
        for operator in deployed:
            tile = _known_tile(operator)
            if tile is None or tile in self.memory.skills_used:
                continue
            name = self.memory.deployed_by_tile.get(tile)
            if name not in self._manual_skill_operator_set:
                continue
            if not operator.skill_ready.known or operator.skill_ready.value is not True:
                continue
            lane = _lane_for_tile(tile)
            lane_pressure = _lane_pressure(pressure, lane)
            score = 300.0 + lane_pressure * 40.0 + kills / max(total, 1) * 25.0
            choices.append((score, tile, name))
        if not choices:
            return None
        score, tile, name = max(choices, key=lambda item: item[0])
        return BattleDecisionV1(
            DecisionKind.SKILL,
            f"{name} skill is visually ready and { _lane_for_tile(tile) } pressure/progress warrants use",
            referenced,
            action_name="Skill",
            details={"name": name, "location": [tile[0], tile[1]]},
            score=round(score, 3),
            candidates=tuple(
                {
                    "operator": candidate_name,
                    "location": list(candidate_tile),
                    "score": round(candidate_score, 3),
                }
                for candidate_score, candidate_tile, candidate_name in sorted(
                    choices, reverse=True
                )
            ),
        )

    def _deploy_decision(
        self,
        state: BattleStateV1,
        referenced: dict[str, Any],
    ) -> BattleDecisionV1 | None:
        if self.memory.deployment_count >= self.maximum_deployments:
            return None
        current_cost = int(state.global_state.current_cost.value)
        kills = int(state.global_state.kills.value)
        total = int(state.global_state.total_enemies.value)
        pressure = state.enemies.visible_pressure.value
        assert pressure is not None
        cards = _cards_by_name(state.friendly.deployable.value or ())
        occupied = {
            tile
            for operator in (state.friendly.deployed.value or ())
            if (tile := _known_tile(operator)) is not None
        }
        occupied.update(self.memory.deployed_by_tile)
        core_tiles = {
            candidate.tile
            for candidate in self._slots
            if candidate.role == "core-vanguard"
        }
        candidates: list[tuple[float, TacticalSlotV1, int]] = []
        for slot in self._slots:
            if slot.tile in occupied:
                continue
            # A reusable facility must not consume the whole opening while the
            # first wave advances. Keep the mandatory routing block first,
            # then establish three independently verified combat bodies before
            # spending two more decisions on route refinement.
            if slot.role == "facility-followup" and self.combat_deployment_count < 3:
                continue
            card = cards.get(slot.operator)
            if card is None or not _card_is_available(card, current_cost):
                continue
            cost = int(card.cost.value)
            lane_pressure = _lane_pressure(pressure, slot.lane)
            phase = kills / max(total, 1)
            score = slot.base_score + lane_pressure * 24.0
            if slot.role == "core-vanguard" and not (core_tiles & occupied):
                score += 160.0
            if slot.role in {"lane-vanguard", "core-vanguard"} and phase < 0.25:
                score += 35.0
            if slot.role == "medic" and len(occupied) >= 3:
                score += 45.0
            if slot.role in {"defender", "lane-guard"} and pressure.visible_health_bars:
                score += 30.0
            # When tactical value ties, leave more cost available for the next
            # live decision; this is still current-state scoring, not timing.
            score -= cost * 0.15
            candidates.append((score, slot, cost))
        if not candidates:
            return None
        score, slot, cost = max(candidates, key=lambda item: item[0])
        ranked = tuple(
            {
                "operator": candidate.operator,
                "location": [candidate.col, candidate.row],
                "lane": candidate.lane,
                "role": candidate.role,
                "cost": candidate_cost,
                "score": round(candidate_score, 3),
            }
            for candidate_score, candidate, candidate_cost in sorted(
                candidates, key=lambda item: item[0], reverse=True
            )
        )
        return BattleDecisionV1(
            DecisionKind.DEPLOY,
            (
                f"{slot.operator} is available at cost {cost}; {slot.lane} lane score is highest "
                f"under live cost={current_cost}, kills={kills}/{total}, "
                f"visible_pressure={pressure.visible_health_bars}"
            ),
            referenced,
            action_name="Deploy",
            details={
                "name": slot.operator,
                "location": [slot.col, slot.row],
                "direction": slot.direction,
            },
            score=round(score, 3),
            candidates=ranked,
        )


class StageOnlinePolicy(OneSevenOnlinePolicy):
    """The same frame-by-frame scorer with a stage-supplied tactical layout.

    ``OneSevenOnlinePolicy`` remains the frozen compatibility entry point used by
    the original acceptance run.  New stages supply their own slots and initial
    life baseline, while retaining the integer-DP gate, repeated perception and
    post-action verification contract.
    """


@dataclass(frozen=True, slots=True)
class BattleControlResultV1:
    status: BattleControlStatus
    reason: str
    observations: tuple[BattleFrameEnvelopeV1, ...]
    decisions: tuple[BattleDecisionV1, ...]
    actions: tuple[ActionLoopOutcomeV1, ...]
    delays: tuple[float, ...]

    @property
    def verified_action_count(self) -> int:
        return sum(outcome.verified for outcome in self.actions)

    @property
    def deployment_count(self) -> int:
        return sum(decision.kind is DecisionKind.DEPLOY for decision in self.decisions)


class LiveBattleController:
    """重复采样、决策、单动作执行与独立验效，直到画面离开战斗 HUD。"""

    def __init__(
        self,
        sampler: BattleFrameSampler,
        action_loop: BattleActionLoop,
        policy: OnlineBattlePolicy,
        compiler: ActionCompiler,
        *,
        cadence: ObservationCadence,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        maximum_cycles: int = 180,
        maximum_seconds: float = 360.0,
    ) -> None:
        self._sampler = sampler
        self._action_loop = action_loop
        self._policy = policy
        self._compiler = compiler
        self._cadence = cadence
        self._sleeper = sleeper
        self._clock = clock
        self._maximum_cycles = maximum_cycles
        self._maximum_seconds = maximum_seconds
        self._observations: list[BattleFrameEnvelopeV1] = []
        self._decisions: list[BattleDecisionV1] = []
        self._actions: list[ActionLoopOutcomeV1] = []
        self._delays: list[float] = []

    def snapshot(
        self,
        *,
        status: BattleControlStatus = BattleControlStatus.STOPPED,
        reason: str = "partial live-control snapshot",
    ) -> BattleControlResultV1:
        """Return progress retained even when an executor raises mid-action."""
        return BattleControlResultV1(
            status,
            reason,
            tuple(self._observations),
            tuple(self._decisions),
            tuple(self._actions),
            tuple(self._delays),
        )

    def run(self) -> BattleControlResultV1:
        started = self._clock()
        self._observations = observations = []
        self._decisions = decisions = []
        self._actions = actions = []
        self._delays = delays = []
        seen_hashes: set[str] = set()
        non_hud_streak = 0
        for _cycle in range(self._maximum_cycles):
            if self._clock() - started > self._maximum_seconds:
                return BattleControlResultV1(
                    BattleControlStatus.TIMED_OUT,
                    "live battle exceeded maximum_seconds",
                    tuple(observations),
                    tuple(decisions),
                    tuple(actions),
                    tuple(delays),
                )
            frame = self._sampler.capture()
            observations.append(frame)
            seen_hashes.add(frame.state.frame.sha256)
            self._observe_policy_frame(frame)
            if not _battle_scene_confirmed(frame.state):
                non_hud_streak += 1
                decision = self._policy.decide(frame.state)
                decisions.append(decision)
                if non_hud_streak >= 2:
                    return BattleControlResultV1(
                        BattleControlStatus.SCENE_CHANGED,
                        "two consecutive fresh frames no longer confirm battle HUD",
                        tuple(observations),
                        tuple(decisions),
                        tuple(actions),
                        tuple(delays),
                    )
                delay = self._cadence.next_delay()
                delays.append(delay)
                self._sleeper(delay)
                continue
            non_hud_streak = 0
            decision = self._policy.decide(frame.state)
            decisions.append(decision)
            if decision.kind is DecisionKind.STOP:
                return BattleControlResultV1(
                    BattleControlStatus.STOPPED,
                    decision.reason,
                    tuple(observations),
                    tuple(decisions),
                    tuple(actions),
                    tuple(delays),
                )
            if decision.actionable:
                call = self._compiler(decision)
                outcome = self._action_loop.run(call, before=frame)
                actions.append(outcome)
                observations.extend(outcome.attempt.after)
                seen_hashes.update(
                    item.state.frame.sha256 for item in outcome.attempt.after
                )
                if not outcome.verified:
                    return BattleControlResultV1(
                        BattleControlStatus.STOPPED,
                        (
                            f"{decision.action_name} effect was not independently verified: "
                            f"{outcome.result.verdict.value}"
                        ),
                        tuple(observations),
                        tuple(decisions),
                        tuple(actions),
                        tuple(delays),
                    )
                self._policy.record_verified(decision)
                continue
            delay = self._cadence.next_delay()
            delays.append(delay)
            self._sleeper(delay)
        return BattleControlResultV1(
            BattleControlStatus.TIMED_OUT,
            f"live battle exceeded maximum_cycles with {len(seen_hashes)} unique frames",
            tuple(observations),
            tuple(decisions),
            tuple(actions),
            tuple(delays),
        )

    def _observe_policy_frame(self, frame: BattleFrameEnvelopeV1) -> None:
        observer = getattr(self._policy, "observe_frame", None)
        if callable(observer):
            observer(frame)


def _battle_scene_confirmed(state: BattleStateV1) -> bool:
    return all(
        item.known
        for item in (
            state.global_state.kills,
            state.global_state.total_enemies,
            state.global_state.life_points,
            state.global_state.paused,
        )
    )


def _required_unknown_fields(state: BattleStateV1) -> list[str]:
    fields = {
        "cost": state.global_state.current_cost,
        "kills": state.global_state.kills,
        "total_enemies": state.global_state.total_enemies,
        "life_points": state.global_state.life_points,
        "paused": state.global_state.paused,
        "deployable": state.friendly.deployable,
        "deployed": state.friendly.deployed,
        "enemy_pressure": state.enemies.visible_pressure,
    }
    return [name for name, value in fields.items() if not value.known]


def _referenced_state(state: BattleStateV1) -> dict[str, Any]:
    deployable: list[dict[str, Any]] | None = None
    if state.friendly.deployable.known:
        deployable = [
            {
                "name": card.name.value if card.name.known else None,
                "cost": card.cost.value if card.cost.known else None,
                "available": card.available.value if card.available.known else None,
                "cooling": card.cooling.value if card.cooling.known else None,
            }
            for card in (state.friendly.deployable.value or ())
        ]
    deployed: list[dict[str, Any]] | None = None
    if state.friendly.deployed.known:
        deployed = [
            {
                "tile": list(tile) if (tile := _known_tile(operator)) else None,
                "skill_ready": (
                    operator.skill_ready.value if operator.skill_ready.known else None
                ),
            }
            for operator in (state.friendly.deployed.value or ())
        ]
    pressure = state.enemies.visible_pressure.value
    return {
        "frame_sha256": state.frame.sha256,
        "cost": state.global_state.current_cost.value,
        "cost_progress": state.global_state.cost_progress.value,
        "kills": state.global_state.kills.value,
        "total_enemies": state.global_state.total_enemies.value,
        "life_points": state.global_state.life_points.value,
        "paused": state.global_state.paused.value,
        "deployable": deployable,
        "deployed": deployed,
        "enemy_pressure": pressure.model_dump(mode="json") if pressure else None,
    }


def _cards_by_name(
    cards: Sequence[DeployableOperatorV1],
) -> dict[str, DeployableOperatorV1]:
    return {
        str(card.name.value): card
        for card in cards
        if card.name.known and card.name.value is not None
    }


def _card_is_available(card: DeployableOperatorV1, current_cost: int) -> bool:
    return bool(
        card.cost.known
        and card.available.known
        and card.cooling.known
        and card.available.value is True
        and card.cooling.value is False
        and int(card.cost.value) <= current_cost
    )


def _known_tile(operator: FriendlyOperatorV1) -> tuple[int, int] | None:
    if not operator.tile.known or operator.tile.value is None:
        return None
    return operator.tile.value.col, operator.tile.value.row


def _lane_for_tile(tile: tuple[int, int]) -> str:
    if tile[1] <= 2:
        return "top"
    if tile[1] >= 4:
        return "bottom"
    return "middle"


def _lane_pressure(pressure: EnemyPressureV1, lane: str) -> int:
    return {
        "top": pressure.top_lane,
        "middle": pressure.middle_lane,
        "bottom": pressure.bottom_lane,
    }[lane]
