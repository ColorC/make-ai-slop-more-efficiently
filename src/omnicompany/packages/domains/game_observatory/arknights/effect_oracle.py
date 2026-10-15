# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T07:05:00Z type=effect-oracle status=active
# [OMNI] summary="用动作后新鲜 BattleState 或声明事件独立核验 MAA 战斗动作效果。"
# [OMNI] why="MAA 回调可以在游戏界面完全没变化时成功；效果必须由独立状态迁移证明。"
# [OMNI] tags=game-observatory,arknights,maa,effect-oracle,battle-state,freshness
"""MAA 战斗动作的独立效果判定首切片。

本模块不截图、不执行动作，只消费带刷新证明的 ``BattleStateV1`` 与 MAA 事件。这样执行器、
感知器和效果判定器的证据互不替代，缓存帧、回调假成功和未知字段都会闭门失败。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from .battle_state import (
    BattleStateV1,
    DeployableOperatorV1,
    FriendlyOperatorV1,
    ROADBLOCK_NAME,
    TileCoordinateV1,
)
from .maa_contracts import BattleActionCall, RecoveryMode
from .maa_operator import MaaEvent, MaaMessage

__all__ = [
    "BattleActionEffectAttemptV1",
    "BattleEffectOracle",
    "BattleFrameEnvelopeV1",
    "EffectCheckStatus",
    "EffectCheckV1",
    "EffectOracleResultV1",
    "EffectVerdict",
]


class EffectVerdict(str, Enum):
    """EffectOracle 的闭集结果；只有前两项表示效果已独立验证。"""

    VERIFIED_VISUAL = "verified_visual"
    VERIFIED_EVENT = "verified_event"
    NO_EFFECT = "no_effect"
    STALE_EVIDENCE = "stale_evidence"
    INCONCLUSIVE = "inconclusive"
    WRONG_TARGET = "wrong_target"
    FORBIDDEN_EFFECT = "forbidden_effect"
    UNSUPPORTED = "unsupported"


class EffectCheckStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class BattleFrameEnvelopeV1:
    """一帧战场状态及其采集时序证明。

    ``refresh_succeeded`` 表示该帧来自成功的强制刷新，而不是空闲时反复读取 MAA 缓存。
    """

    state: BattleStateV1
    capture_requested_monotonic: float
    captured_monotonic: float
    refresh_succeeded: bool
    source: str = "maa_async_screencap"
    active_game_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.capture_requested_monotonic < 0 or self.captured_monotonic < 0:
            raise ValueError("frame monotonic timestamps must be non-negative")
        if self.captured_monotonic < self.capture_requested_monotonic:
            raise ValueError("frame cannot be captured before its capture request")
        if not self.source.strip():
            raise ValueError("frame source must be non-empty")
        if self.active_game_seconds is not None and self.active_game_seconds < 0:
            raise ValueError("active game time must be non-negative")


@dataclass(frozen=True, slots=True)
class BattleActionEffectAttemptV1:
    call: BattleActionCall
    before: BattleFrameEnvelopeV1
    after: tuple[BattleFrameEnvelopeV1, ...]
    action_issued_monotonic: float
    action_completed_monotonic: float
    events: tuple[MaaEvent, ...] = ()

    def __post_init__(self) -> None:
        if self.action_issued_monotonic < 0:
            raise ValueError("action issued timestamp must be non-negative")
        if self.action_completed_monotonic < self.action_issued_monotonic:
            raise ValueError("action cannot complete before it is issued")


@dataclass(frozen=True, slots=True)
class EffectCheckV1:
    predicate: str
    status: EffectCheckStatus
    detail: str


@dataclass(frozen=True, slots=True)
class EffectOracleResultV1:
    contract_id: str
    action_name: str
    verdict: EffectVerdict
    checks: tuple[EffectCheckV1, ...]
    selected_frame_sha256: str | None
    action_event_observed: bool
    generic_callback_is_sufficient: bool
    recovery: tuple[RecoveryMode, ...]

    @property
    def verified(self) -> bool:
        return self.verdict in {
            EffectVerdict.VERIFIED_VISUAL,
            EffectVerdict.VERIFIED_EVENT,
        }


_EVENT_ACTION_NAMES = {
    "Skill": "UseSkill",
    "SpeedUp": "SwitchSpeed",
}

_DIRECTION_CANONICAL = {
    "Right": "Right",
    "RIGHT": "Right",
    "right": "Right",
    "右": "Right",
    "Left": "Left",
    "LEFT": "Left",
    "left": "Left",
    "左": "Left",
    "Up": "Up",
    "UP": "Up",
    "up": "Up",
    "上": "Up",
    "Down": "Down",
    "DOWN": "Down",
    "down": "Down",
    "下": "Down",
    "None": "None",
    "NONE": "None",
    "none": "None",
    "无": "None",
}

# Canonical 1280x720 centers projected from MAA's sub_02-03 tile grid.  They
# are kept in one visual-oracle registry so action callers continue to express
# semantic tiles only.
_S2_3_ROADBLOCK_CENTERS = {
    (7, 3): (944.2472, 277.6077),
    (3, 4): (533.1040, 365.7300),
    (4, 5): (640.0000, 463.9202),
}


class BattleEffectOracle:
    """从独立状态迁移或声明事件判定战斗动作效果。"""

    def __init__(self) -> None:
        # A conservative detector may miss a previously verified operator in
        # one frame.  Retain only effects this oracle itself proved so their
        # later reappearance is not mistaken for a second deployment.
        self._verified_deploy_tiles: set[tuple[int, int]] = set()

    def verify(self, attempt: BattleActionEffectAttemptV1) -> EffectOracleResultV1:
        action_event = _find_action_event(attempt)
        if attempt.call.action_name == "Output":
            return self._verify_output(attempt, action_event)

        if not attempt.before.refresh_succeeded:
            return _result(
                attempt,
                EffectVerdict.STALE_EVIDENCE,
                (
                    EffectCheckV1(
                        "fresh_before_frame",
                        EffectCheckStatus.FAIL,
                        "before state was not produced by a successful forced capture",
                    ),
                ),
                action_event=action_event,
            )
        after = _select_fresh_after(attempt)
        if after is None:
            return _result(
                attempt,
                EffectVerdict.STALE_EVIDENCE,
                (
                    EffectCheckV1(
                        "fresh_after_frame",
                        EffectCheckStatus.FAIL,
                        "no successful forced capture was requested after action completion",
                    ),
                ),
                action_event=action_event,
            )
        if attempt.before.captured_monotonic > attempt.action_issued_monotonic:
            return _result(
                attempt,
                EffectVerdict.STALE_EVIDENCE,
                (
                    EffectCheckV1(
                        "before_precedes_action",
                        EffectCheckStatus.FAIL,
                        "before frame was captured after the action was issued",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        if _frame_shape(attempt.before) != _frame_shape(after):
            return _result(
                attempt,
                EffectVerdict.INCONCLUSIVE,
                (
                    EffectCheckV1(
                        "stable_frame_geometry",
                        EffectCheckStatus.FAIL,
                        "before/after frame dimensions differ; relocalization is required",
                    ),
                ),
                after=after,
                action_event=action_event,
            )

        verifier = {
            "Deploy": self._verify_deploy,
            "Skill": self._verify_skill,
            "Retreat": self._verify_retreat,
        }.get(attempt.call.action_name)
        if verifier is None:
            return _result(
                attempt,
                EffectVerdict.UNSUPPORTED,
                (
                    EffectCheckV1(
                        "implemented_effect_predicate",
                        EffectCheckStatus.UNKNOWN,
                        f"{attempt.call.action_name} needs additional visual/domain fields",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        result = verifier(attempt, after, action_event)
        target = _target_tile(attempt.call.details)
        if result.verified and target is not None:
            if attempt.call.action_name == "Deploy":
                self._verified_deploy_tiles.add(target)
            elif attempt.call.action_name == "Retreat":
                self._verified_deploy_tiles.discard(target)
        return result

    def _verify_deploy(
        self,
        attempt: BattleActionEffectAttemptV1,
        after: BattleFrameEnvelopeV1,
        action_event: MaaEvent | None,
    ) -> EffectOracleResultV1:
        target = _target_tile(attempt.call.details)
        if target is None:
            return _unknown_visual_result(attempt, after, action_event, "target tile")
        requested_name = str(attempt.call.details.get("name", ""))
        requested_direction = _DIRECTION_CANONICAL.get(
            str(attempt.call.details.get("direction", ""))
        )
        if requested_name == ROADBLOCK_NAME and requested_direction == "None":
            return _verify_directionless_roadblock(
                attempt,
                after,
                action_event,
                target,
            )

        before_ops = _known_operators(attempt.before.state)
        after_ops = _known_operators(after.state)
        if before_ops is None or after_ops is None:
            return _unknown_visual_result(attempt, after, action_event, "deployed operator set")

        before_tiles = _operators_by_tile(before_ops)
        after_tiles = _operators_by_tile(after_ops)
        if before_tiles is None or after_tiles is None:
            return _unknown_visual_result(attempt, after, action_event, "operator tile")
        new_tiles = set(after_tiles) - set(before_tiles)
        if target not in new_tiles:
            verdict = EffectVerdict.WRONG_TARGET if new_tiles else EffectVerdict.NO_EFFECT
            return _result(
                attempt,
                verdict,
                (
                    EffectCheckV1(
                        "operator_appears_on_target_tile",
                        EffectCheckStatus.FAIL,
                        f"target={target}, new_tiles={sorted(new_tiles)}",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        unexpected = new_tiles - {target} - self._verified_deploy_tiles
        if unexpected:
            persistent = _persistent_unexpected_deploy_tiles(
                attempt,
                before_tiles=set(before_tiles),
                target=target,
            ) - self._verified_deploy_tiles
            if persistent:
                return _result(
                    attempt,
                    EffectVerdict.FORBIDDEN_EFFECT,
                    (
                        EffectCheckV1(
                            "only_target_tile_changes",
                            EffectCheckStatus.FAIL,
                            (
                                "unexpected new operator tiles persisted across "
                                f"fresh frames: {sorted(persistent)}"
                            ),
                        ),
                    ),
                    after=after,
                    action_event=action_event,
                )
            return _result(
                attempt,
                EffectVerdict.INCONCLUSIVE,
                (
                    EffectCheckV1(
                        "only_target_tile_changes",
                        EffectCheckStatus.UNKNOWN,
                        (
                            "single-frame extra operator detections require "
                            f"reobservation: {sorted(unexpected)}"
                        ),
                    ),
                ),
                after=after,
                action_event=action_event,
            )

        operator = after_tiles[target]
        identity = _deployed_identity_matches(
            attempt.before.state,
            after.state,
            operator,
            requested_name,
        )
        reusable_identity = False
        if identity is not True and requested_direction == "None":
            identity = _reusable_deployment_identity_matches(
                attempt.before.state,
                after.state,
                requested_name,
            )
            reusable_identity = identity is True
        if identity is not True:
            status = (
                EffectCheckStatus.FAIL
                if identity is False and operator.name.known
                else EffectCheckStatus.UNKNOWN
            )
            verdict = (
                EffectVerdict.WRONG_TARGET
                if identity is False and operator.name.known
                else EffectVerdict.INCONCLUSIVE
            )
            return _result(
                attempt,
                verdict,
                (
                    EffectCheckV1(
                        "deployed_identity_matches",
                        status,
                        (
                            f"requested={requested_name!r}, "
                            f"observed={operator.name.value!r}; "
                            "on-field identity, exact deployment-card departure, or "
                            "stable reusable facility card "
                            "was not independently established"
                        ),
                    ),
                ),
                after=after,
                action_event=action_event,
            )

        observed_direction = "None"
        direction_detail = "directionless facility"
        if requested_direction != "None":
            observed_direction = (
                _DIRECTION_CANONICAL.get(str(operator.direction.value))
                if operator.direction.known
                else None
            )
            direction_detail = "single-frame classified direction"
            if observed_direction is None:
                observed_direction = _consensus_deploy_direction(attempt, target)
                direction_detail = "multi-frame classifier consensus"
            if observed_direction is None:
                return _unknown_visual_result(attempt, after, action_event, "deploy direction")
            if observed_direction != requested_direction:
                return _result(
                    attempt,
                    EffectVerdict.WRONG_TARGET,
                    (
                        EffectCheckV1(
                            "deployed_direction_matches",
                            EffectCheckStatus.FAIL,
                            f"requested={requested_direction}, observed={observed_direction}",
                        ),
                    ),
                    after=after,
                    action_event=action_event,
                )

        support = _card_or_cost_changed(
            attempt.before.state,
            after.state,
            requested_name,
        )
        if support is None:
            return _unknown_visual_result(attempt, after, action_event, "card/cost transition")
        if not support:
            return _result(
                attempt,
                EffectVerdict.INCONCLUSIVE,
                (
                    EffectCheckV1(
                        "card_or_cost_changes",
                        EffectCheckStatus.FAIL,
                        "target appeared, but independent card/cost support did not change",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        identity_detail = (
            f"reusable facility card {requested_name!r} remained uniquely stable"
            if reusable_identity
            else (
                f"on-field identity matched {requested_name!r}"
                if operator.name.known
                else f"deployment card {requested_name!r} uniquely departed"
            )
        )
        return _result(
            attempt,
            EffectVerdict.VERIFIED_VISUAL,
            (
                EffectCheckV1(
                    "deploy_state_transition",
                    EffectCheckStatus.PASS,
                    (
                        f"{requested_name} appeared at {target} facing "
                        f"{observed_direction} ({direction_detail}); {identity_detail}"
                    ),
                ),
                EffectCheckV1(
                    "card_or_cost_changes",
                    EffectCheckStatus.PASS,
                    "deployment card or cost state changed",
                ),
            ),
            after=after,
            action_event=action_event,
        )

    def _verify_skill(
        self,
        attempt: BattleActionEffectAttemptV1,
        after: BattleFrameEnvelopeV1,
        action_event: MaaEvent | None,
    ) -> EffectOracleResultV1:
        before_ops = _known_operators(attempt.before.state)
        after_ops = _known_operators(after.state)
        if before_ops is None or after_ops is None:
            return _unknown_visual_result(attempt, after, action_event, "deployed operator set")
        requested_tile = _target_tile(attempt.call.details)
        before_lookup = (
            {"location": [requested_tile[0], requested_tile[1]]}
            if requested_tile is not None
            else attempt.call.details
        )
        before_target = _find_in_operators(before_ops, before_lookup)
        if before_target is None:
            return _unknown_visual_result(attempt, after, action_event, "skill target before action")
        requested_name = str(attempt.call.details.get("name", ""))
        if (
            requested_name
            and before_target.name.known
            and before_target.name.value != requested_name
        ):
            return _result(
                attempt,
                EffectVerdict.WRONG_TARGET,
                (
                    EffectCheckV1(
                        "skill_target_identity_matches_before_action",
                        EffectCheckStatus.FAIL,
                        f"expected={requested_name!r}, observed={before_target.name.value!r}",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        target_tile = _operator_tile(before_target)
        if target_tile is None:
            return _unknown_visual_result(attempt, after, action_event, "skill target tile")
        after_target = _find_in_operators(
            after_ops,
            {"location": [target_tile[0], target_tile[1]]},
        )
        if after_target is None:
            return _result(
                attempt,
                EffectVerdict.FORBIDDEN_EFFECT,
                (
                    EffectCheckV1(
                        "skill_target_remains_deployed",
                        EffectCheckStatus.FAIL,
                        "skill target disappeared after the action",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        if (
            requested_name
            and after_target.name.known
            and after_target.name.value != requested_name
        ):
            return _result(
                attempt,
                EffectVerdict.WRONG_TARGET,
                (
                    EffectCheckV1(
                        "skill_target_identity_is_stable",
                        EffectCheckStatus.FAIL,
                        f"expected={requested_name!r}, observed={after_target.name.value!r}",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        before_ready = before_target.skill_ready
        after_ready = after_target.skill_ready
        if not before_ready.known or not after_ready.known:
            return _unknown_visual_result(attempt, after, action_event, "skill readiness")
        if before_ready.value is not True:
            return _result(
                attempt,
                EffectVerdict.INCONCLUSIVE,
                (
                    EffectCheckV1(
                        "skill_was_ready_before_action",
                        EffectCheckStatus.FAIL,
                        f"before skill_ready={before_ready.value!r}",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        if after_ready.value is True:
            return _result(
                attempt,
                EffectVerdict.NO_EFFECT,
                (
                    EffectCheckV1(
                        "skill_ready_transitions_to_cooldown",
                        EffectCheckStatus.FAIL,
                        "skill remained ready after the action",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        return _result(
            attempt,
            EffectVerdict.VERIFIED_VISUAL,
            (
                EffectCheckV1(
                    "skill_ready_transitions_to_cooldown",
                    EffectCheckStatus.PASS,
                    "target skill readiness changed true -> false",
                ),
            ),
            after=after,
            action_event=action_event,
        )

    def _verify_retreat(
        self,
        attempt: BattleActionEffectAttemptV1,
        after: BattleFrameEnvelopeV1,
        action_event: MaaEvent | None,
    ) -> EffectOracleResultV1:
        before_ops = _known_operators(attempt.before.state)
        after_ops = _known_operators(after.state)
        if before_ops is None or after_ops is None:
            return _unknown_visual_result(attempt, after, action_event, "deployed operator set")
        before_target = _find_in_operators(before_ops, attempt.call.details)
        if before_target is None:
            return _unknown_visual_result(attempt, after, action_event, "retreat target before action")
        after_target = _find_in_operators(after_ops, attempt.call.details)
        before_tiles = _operators_by_tile(before_ops)
        after_tiles = _operators_by_tile(after_ops)
        if before_tiles is None or after_tiles is None:
            return _unknown_visual_result(attempt, after, action_event, "operator tile")
        removed_tiles = set(before_tiles) - set(after_tiles)
        target_tile = _operator_tile(before_target)
        if after_target is not None:
            unexpected = removed_tiles - ({target_tile} if target_tile else set())
            verdict = EffectVerdict.WRONG_TARGET if unexpected else EffectVerdict.NO_EFFECT
            return _result(
                attempt,
                verdict,
                (
                    EffectCheckV1(
                        "retreat_target_disappears",
                        EffectCheckStatus.FAIL,
                        f"target remains; removed_tiles={sorted(removed_tiles)}",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        if target_tile is None or target_tile not in removed_tiles:
            return _unknown_visual_result(attempt, after, action_event, "retreat target tile")
        unexpected = removed_tiles - {target_tile}
        if unexpected:
            return _result(
                attempt,
                EffectVerdict.FORBIDDEN_EFFECT,
                (
                    EffectCheckV1(
                        "only_retreat_target_disappears",
                        EffectCheckStatus.FAIL,
                        f"additional operators disappeared at {sorted(unexpected)}",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        requested_name = str(attempt.call.details.get("name", ""))
        support = _card_or_cost_changed(attempt.before.state, after.state, requested_name)
        if support is None:
            return _unknown_visual_result(attempt, after, action_event, "card/cost transition")
        if not support:
            return _result(
                attempt,
                EffectVerdict.INCONCLUSIVE,
                (
                    EffectCheckV1(
                        "card_or_cost_changes",
                        EffectCheckStatus.FAIL,
                        "target disappeared, but card/cost support did not change",
                    ),
                ),
                after=after,
                action_event=action_event,
            )
        return _result(
            attempt,
            EffectVerdict.VERIFIED_VISUAL,
            (
                EffectCheckV1(
                    "retreat_state_transition",
                    EffectCheckStatus.PASS,
                    f"target disappeared from {target_tile} with card/cost support",
                ),
            ),
            after=after,
            action_event=action_event,
        )

    def _verify_output(
        self,
        attempt: BattleActionEffectAttemptV1,
        action_event: MaaEvent | None,
    ) -> EffectOracleResultV1:
        if action_event is None:
            return _result(
                attempt,
                EffectVerdict.NO_EFFECT,
                (
                    EffectCheckV1(
                        "declared_output_event",
                        EffectCheckStatus.FAIL,
                        "matching CopilotAction Output event was not observed",
                    ),
                ),
            )
        details = _event_action_details(action_event)
        expected_doc = str(attempt.call.details.get("doc", ""))
        observed_doc = str(details.get("doc", ""))
        if observed_doc != expected_doc:
            return _result(
                attempt,
                EffectVerdict.WRONG_TARGET,
                (
                    EffectCheckV1(
                        "output_document_matches",
                        EffectCheckStatus.FAIL,
                        f"expected={expected_doc!r}, observed={observed_doc!r}",
                    ),
                ),
                action_event=action_event,
            )
        return _result(
            attempt,
            EffectVerdict.VERIFIED_EVENT,
            (
                EffectCheckV1(
                    "declared_output_event",
                    EffectCheckStatus.PASS,
                    f"matching Output event observed at {action_event.monotonic}",
                ),
            ),
            action_event=action_event,
        )


def _select_fresh_after(
    attempt: BattleActionEffectAttemptV1,
) -> BattleFrameEnvelopeV1 | None:
    candidates = [
        frame
        for frame in attempt.after
        if frame.refresh_succeeded
        and frame.capture_requested_monotonic > attempt.action_completed_monotonic
        and frame.captured_monotonic >= frame.capture_requested_monotonic
    ]
    # 稳定窗口允许效果延迟出现；每次重观测都应使用最新一张合格帧，而不是永远卡在
    # 第一张“新鲜但尚未生效”的画面上。所有帧仍保留在 attempt.after 供证据复核。
    return max(candidates, key=lambda frame: frame.captured_monotonic, default=None)


def _frame_shape(frame: BattleFrameEnvelopeV1) -> tuple[int, int]:
    return frame.state.frame.width, frame.state.frame.height


def _find_action_event(attempt: BattleActionEffectAttemptV1) -> MaaEvent | None:
    expected = _EVENT_ACTION_NAMES.get(attempt.call.action_name, attempt.call.action_name)
    for event in attempt.events:
        if event.monotonic < attempt.action_issued_monotonic:
            continue
        details = _event_action_details(event)
        if details.get("action") == expected:
            return event
    return None


def _event_action_details(event: MaaEvent) -> dict[str, object]:
    if event.message != MaaMessage.SubTaskExtraInfo or event.what != "CopilotAction":
        return {}
    details = event.details.get("details")
    return details if isinstance(details, dict) else {}


def _known_operators(state: BattleStateV1) -> tuple[FriendlyOperatorV1, ...] | None:
    deployed = state.friendly.deployed
    return deployed.value if deployed.known else None


def _known_cards(state: BattleStateV1) -> tuple[DeployableOperatorV1, ...] | None:
    deployable = state.friendly.deployable
    return deployable.value if deployable.known else None


def _operator_tile(operator: FriendlyOperatorV1) -> tuple[int, int] | None:
    if not operator.tile.known or operator.tile.value is None:
        return None
    return operator.tile.value.col, operator.tile.value.row


def _operators_by_tile(
    operators: Sequence[FriendlyOperatorV1],
) -> dict[tuple[int, int], FriendlyOperatorV1] | None:
    result: dict[tuple[int, int], FriendlyOperatorV1] = {}
    for operator in operators:
        tile = _operator_tile(operator)
        if tile is None or tile in result:
            return None
        result[tile] = operator
    return result


def _persistent_unexpected_deploy_tiles(
    attempt: BattleActionEffectAttemptV1,
    *,
    before_tiles: set[tuple[int, int]],
    target: tuple[int, int],
) -> set[tuple[int, int]]:
    unexpected_sets: list[set[tuple[int, int]]] = []
    for frame in attempt.after:
        if (
            not frame.refresh_succeeded
            or frame.capture_requested_monotonic
            <= attempt.action_completed_monotonic
        ):
            continue
        operators = _known_operators(frame.state)
        by_tile = _operators_by_tile(operators) if operators is not None else None
        if by_tile is None:
            continue
        new_tiles = set(by_tile) - before_tiles
        if target not in new_tiles:
            continue
        unexpected_sets.append(new_tiles - {target})
    if len(unexpected_sets) < 2:
        return set()
    return set.intersection(*unexpected_sets[-2:])


_DIRECTION_SCORE = re.compile(r"^(Right|Down|Left|Up)=([0-9]+(?:\.[0-9]+)?)$")


def _direction_candidate(operator: FriendlyOperatorV1) -> str | None:
    if operator.direction.known and operator.direction.value is not None:
        return _DIRECTION_CANONICAL.get(str(operator.direction.value))
    scores: dict[str, float] = {}
    for raw in operator.direction.evidence.raw_text:
        match = _DIRECTION_SCORE.fullmatch(raw)
        if match:
            scores[match.group(1)] = float(match.group(2))
    if len(scores) != 4:
        return None
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if ranked[0][1] < 0.6 or ranked[0][1] - ranked[1][1] < 0.2:
        return None
    return ranked[0][0]


def _consensus_deploy_direction(
    attempt: BattleActionEffectAttemptV1,
    target: tuple[int, int],
) -> str | None:
    candidates: list[str] = []
    for frame in attempt.after:
        if (
            not frame.refresh_succeeded
            or frame.capture_requested_monotonic
            <= attempt.action_completed_monotonic
        ):
            continue
        operators = _known_operators(frame.state)
        by_tile = _operators_by_tile(operators) if operators is not None else None
        if by_tile is None or target not in by_tile:
            continue
        candidate = _direction_candidate(by_tile[target])
        if candidate is not None:
            candidates.append(candidate)
    if len(candidates) < 2 or len(set(candidates)) != 1:
        return None
    return candidates[0]


def _target_tile(details: dict[str, object]) -> tuple[int, int] | None:
    raw = details.get("location")
    if not isinstance(raw, list) or len(raw) != 2:
        return None
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw):
        return None
    return int(raw[0]), int(raw[1])


def _find_in_operators(
    operators: Sequence[FriendlyOperatorV1], details: dict[str, object]
) -> FriendlyOperatorV1 | None:
    target = _target_tile(details)
    requested_name = str(details.get("name", ""))
    matches: list[FriendlyOperatorV1] = []
    for operator in operators:
        if target is not None and _operator_tile(operator) != target:
            continue
        if requested_name:
            if not operator.name.known or operator.name.value != requested_name:
                continue
        matches.append(operator)
    return matches[0] if len(matches) == 1 else None


def _find_card(
    cards: Iterable[DeployableOperatorV1], name: str
) -> DeployableOperatorV1 | None:
    matches = [card for card in cards if card.name.known and card.name.value == name]
    return matches[0] if len(matches) == 1 else None


def _known_card_names(
    cards: Sequence[DeployableOperatorV1],
) -> frozenset[str] | None:
    names: list[str] = []
    for card in cards:
        if not card.name.known or not card.name.value:
            return None
        names.append(card.name.value)
    if len(set(names)) != len(names):
        return None
    return frozenset(names)


def _deployed_identity_matches(
    before: BattleStateV1,
    after: BattleStateV1,
    operator: FriendlyOperatorV1,
    requested_name: str,
) -> bool | None:
    """用场上姓名，或严格的部署卡唯一消失，证明新对象身份。"""
    if operator.name.known:
        return operator.name.value == requested_name
    if not requested_name:
        return None
    before_cards = _known_cards(before)
    after_cards = _known_cards(after)
    if before_cards is None or after_cards is None:
        return None
    before_names = _known_card_names(before_cards)
    after_names = _known_card_names(after_cards)
    if after_names is not None:
        if before_names is not None:
            if requested_name not in before_names:
                return None
            return after_names == before_names - {requested_name}
        known_before = [
            str(card.name.value)
            for card in before_cards
            if card.name.known and card.name.value
        ]
        if len(known_before) != len(set(known_before)):
            return None
        if known_before.count(requested_name) != 1:
            return None
        other_known = set(known_before) - {requested_name}
        return requested_name not in after_names and other_known.issubset(after_names)

    # One unrelated card detector can remain unknown while the bottom bar
    # reflows.  Preserve a strict identity proof by requiring exactly one card
    # to depart, every other known identity to survive, and the multiset of
    # unrelated unknown-card cost/cooldown states to remain unchanged.
    known_before = [
        str(card.name.value)
        for card in before_cards
        if card.name.known and card.name.value
    ]
    if len(known_before) != len(set(known_before)):
        return None
    if known_before.count(requested_name) != 1:
        return None
    known_after = [
        str(card.name.value)
        for card in after_cards
        if card.name.known and card.name.value
    ]
    if requested_name in known_after:
        return None
    other_known = set(known_before) - {requested_name}
    if not other_known.issubset(known_after):
        return None
    if len(after_cards) != len(before_cards) - 1:
        return None
    before_unknown = [
        _unknown_card_fingerprint(card)
        for card in before_cards
        if not card.name.known or not card.name.value
    ]
    after_unknown = [
        _unknown_card_fingerprint(card)
        for card in after_cards
        if not card.name.known or not card.name.value
    ]
    if any(item is None for item in (*before_unknown, *after_unknown)):
        return None
    return Counter(before_unknown) == Counter(after_unknown)


def _reusable_deployment_identity_matches(
    before: BattleStateV1,
    after: BattleStateV1,
    requested_name: str,
) -> bool | None:
    """Prove a directionless reusable facility whose deployment card remains."""

    if not requested_name:
        return None
    before_cards = _known_cards(before)
    after_cards = _known_cards(after)
    if before_cards is None or after_cards is None:
        return None
    before_names = _known_card_names(before_cards)
    after_names = _known_card_names(after_cards)
    if before_names is None or after_names is None:
        return None
    if requested_name not in before_names or requested_name not in after_names:
        return False
    if len(before_cards) != len(after_cards):
        return False
    return before_names == after_names


def _verify_directionless_roadblock(
    attempt: BattleActionEffectAttemptV1,
    after: BattleFrameEnvelopeV1,
    action_event: MaaEvent | None,
    target: tuple[int, int],
) -> EffectOracleResultV1:
    """Verify an S2-3 roadblock without pretending it has an operator health bar."""

    identity = _reusable_deployment_identity_matches(
        attempt.before.state,
        after.state,
        ROADBLOCK_NAME,
    )
    if identity is not True:
        return _unknown_visual_result(
            attempt,
            after,
            action_event,
            "stable reusable roadblock card",
        )
    support = _card_or_cost_changed(
        attempt.before.state,
        after.state,
        ROADBLOCK_NAME,
    )
    if support is None:
        return _unknown_visual_result(
            attempt,
            after,
            action_event,
            "roadblock card/cost transition",
        )
    changed, detail, other_changed = _roadblock_tile_visual_change(
        attempt.before.state,
        after.state,
        target,
    )
    if changed is None:
        return _unknown_visual_result(
            attempt,
            after,
            action_event,
            "roadblock target-tile visual transition",
        )
    if not changed:
        verdict = (
            EffectVerdict.WRONG_TARGET
            if other_changed
            else (EffectVerdict.INCONCLUSIVE if support else EffectVerdict.NO_EFFECT)
        )
        return _result(
            attempt,
            verdict,
            (
                EffectCheckV1(
                    "roadblock_appears_on_target_tile",
                    EffectCheckStatus.FAIL,
                    detail,
                ),
            ),
            after=after,
            action_event=action_event,
        )
    if not support:
        return _result(
            attempt,
            EffectVerdict.INCONCLUSIVE,
            (
                EffectCheckV1(
                    "roadblock_card_or_cost_changes",
                    EffectCheckStatus.FAIL,
                    "target changed, but the reusable card and integer cost did not",
                ),
            ),
            after=after,
            action_event=action_event,
        )
    return _result(
        attempt,
        EffectVerdict.VERIFIED_VISUAL,
        (
            EffectCheckV1(
                "roadblock_deploy_state_transition",
                EffectCheckStatus.PASS,
                (
                    f"{ROADBLOCK_NAME} visibly appeared at {target} ({detail}); "
                    "its unique reusable card remained and card/cost state changed"
                ),
            ),
            EffectCheckV1(
                "roadblock_card_or_cost_changes",
                EffectCheckStatus.PASS,
                "reusable facility card or integer cost state changed",
            ),
        ),
        after=after,
        action_event=action_event,
    )


def _roadblock_tile_visual_change(
    before: BattleStateV1,
    after: BattleStateV1,
    target: tuple[int, int],
) -> tuple[bool | None, str, tuple[tuple[int, int], ...]]:
    center = _S2_3_ROADBLOCK_CENTERS.get(target)
    if center is None:
        return None, f"unsupported roadblock target={target}", ()
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np

        before_image = cv2.imdecode(
            np.fromfile(before.frame.path, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        after_image = cv2.imdecode(
            np.fromfile(after.frame.path, dtype=np.uint8), cv2.IMREAD_COLOR
        )
    except (OSError, ValueError):
        return None, "fresh frame files could not be decoded", ()
    if before_image is None or after_image is None:
        return None, "fresh frame files could not be decoded", ()
    if before_image.shape != after_image.shape:
        return None, "before/after frame dimensions differ", ()
    height, width = before_image.shape[:2]
    metrics: dict[tuple[int, int], tuple[float, float]] = {}
    for tile, canonical_center in _S2_3_ROADBLOCK_CENTERS.items():
        x = round(canonical_center[0] * width / 1280)
        y = round(canonical_center[1] * height / 720)
        half_width = max(24, round(48 * width / 1280))
        half_height = max(24, round(48 * height / 720))
        left, right = max(0, x - half_width), min(width, x + half_width)
        top, bottom = max(0, y - half_height), min(height, y + half_height)
        before_crop = before_image[top:bottom, left:right]
        after_crop = after_image[top:bottom, left:right]
        if before_crop.size == 0 or after_crop.size == 0:
            return None, f"empty visual crop for tile={tile}", ()
        difference = cv2.absdiff(before_crop, after_crop)
        maximum_channel = difference.max(axis=2)
        metrics[tile] = (
            float(difference.mean()),
            float((maximum_channel > 25).mean()),
        )
    changed_tiles = tuple(
        tile
        for tile, (mean_difference, changed_fraction) in metrics.items()
        if mean_difference >= 20.0 and changed_fraction >= 0.35
    )
    mean_difference, changed_fraction = metrics[target]
    detail = (
        f"target_mean_absdiff={mean_difference:.3f}, "
        f"target_changed_fraction={changed_fraction:.4f}, "
        f"changed_registered_tiles={list(changed_tiles)}"
    )
    return target in changed_tiles, detail, tuple(
        tile for tile in changed_tiles if tile != target
    )


def _unknown_card_fingerprint(
    card: DeployableOperatorV1,
) -> tuple[int, bool, bool] | None:
    """Identify an unrelated unknown card without relying on its shifting index."""

    if not card.cost.known or card.cost.value is None:
        return None
    if not card.available.known or card.available.value is None:
        return None
    if not card.cooling.known or card.cooling.value is None:
        return None
    return (
        int(card.cost.value),
        bool(card.available.value),
        bool(card.cooling.value),
    )


def _card_or_cost_changed(
    before: BattleStateV1, after: BattleStateV1, operator_name: str
) -> bool | None:
    evidence_available = False
    before_cost = before.global_state.current_cost
    after_cost = after.global_state.current_cost
    if before_cost.known and after_cost.known:
        evidence_available = True
        if before_cost.value != after_cost.value:
            return True

    before_cards = _known_cards(before)
    after_cards = _known_cards(after)
    if before_cards is not None and after_cards is not None and operator_name:
        before_card = _find_card(before_cards, operator_name)
        after_card = _find_card(after_cards, operator_name)
        if before_card is not None:
            evidence_available = True
            if after_card is None:
                return True
            before_state = (
                before_card.available.value if before_card.available.known else None,
                before_card.cooling.value if before_card.cooling.known else None,
            )
            after_state = (
                after_card.available.value if after_card.available.known else None,
                after_card.cooling.value if after_card.cooling.known else None,
            )
            if before_state != after_state:
                return True
    return False if evidence_available else None


def _unknown_visual_result(
    attempt: BattleActionEffectAttemptV1,
    after: BattleFrameEnvelopeV1,
    action_event: MaaEvent | None,
    field: str,
) -> EffectOracleResultV1:
    return _result(
        attempt,
        EffectVerdict.INCONCLUSIVE,
        (
            EffectCheckV1(
                f"known_{field.replace('/', '_').replace(' ', '_')}",
                EffectCheckStatus.UNKNOWN,
                f"{field} is unknown or ambiguous",
            ),
        ),
        after=after,
        action_event=action_event,
    )


def _result(
    attempt: BattleActionEffectAttemptV1,
    verdict: EffectVerdict,
    checks: tuple[EffectCheckV1, ...],
    *,
    after: BattleFrameEnvelopeV1 | None = None,
    action_event: MaaEvent | None = None,
) -> EffectOracleResultV1:
    return EffectOracleResultV1(
        contract_id=attempt.call.contract_id,
        action_name=attempt.call.action_name,
        verdict=verdict,
        checks=checks,
        selected_frame_sha256=after.state.frame.sha256 if after else None,
        action_event_observed=action_event is not None,
        generic_callback_is_sufficient=False,
        recovery=attempt.call.recovery.modes,
    )
