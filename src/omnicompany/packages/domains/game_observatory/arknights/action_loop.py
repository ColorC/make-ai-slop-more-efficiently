# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T07:35:00Z type=action-loop status=active
# [OMNI] summary="串联动作前强刷、一次 MAA 执行、动作后稳定观察、EffectOracle 与恢复建议。"
# [OMNI] why="无效果时盲目重放会把一次感知失败放大成重复部署、技能或撤退。"
# [OMNI] tags=game-observatory,arknights,maa,action-loop,effect-oracle,recovery
"""感知 → 一次执行 → 多次观察 → 独立效果判定的首个编排闭环。"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Protocol, Sequence

from .battle_state import BattleStateParser, BattleStateV1, parse_battle_frame
from .effect_oracle import (
    BattleActionEffectAttemptV1,
    BattleEffectOracle,
    BattleFrameEnvelopeV1,
    EffectOracleResultV1,
    EffectVerdict,
)
from .maa_contracts import BattleActionCall, RecoveryMode
from .maa_operator import MaaEvent, MaaOperator
from .tile_grid import StageGrid

__all__ = [
    "ActionLoopOutcomeV1",
    "ActionLoopStatus",
    "BattleActionLoop",
    "BattleActionPreconditionError",
    "BattleFrameCaptureError",
    "BattleFrameSampler",
    "MaaBattleFrameSampler",
]


class BattleFrameCaptureError(RuntimeError):
    """无法证明刷新成功、取不到 PNG 或解析结果与采集字节不一致。"""


class BattleActionPreconditionError(RuntimeError):
    """新鲜前帧不能确认处于战斗 HUD，故未调用动作执行器。"""


class ActionLoopStatus(str, Enum):
    VERIFIED = "verified"
    STOPPED = "stopped"


class BattleFrameSampler(Protocol):
    def capture(self) -> BattleFrameEnvelopeV1: ...


ActionExecutor = Callable[[BattleActionCall], Sequence[MaaEvent]]
ReobserveHook = Callable[[int, EffectOracleResultV1], None]
BeforeCaptureHook = Callable[[], None]
AfterCaptureHook = Callable[[Path], None]
AfterParseHook = Callable[[BattleFrameEnvelopeV1], None]


@dataclass(frozen=True, slots=True)
class ActionLoopOutcomeV1:
    status: ActionLoopStatus
    attempt: BattleActionEffectAttemptV1
    result: EffectOracleResultV1
    execution_count: int
    observation_count: int
    action_reexecuted: bool
    recommended_recovery: RecoveryMode | None

    @property
    def verified(self) -> bool:
        return self.status is ActionLoopStatus.VERIFIED and self.result.verified


class BattleActionLoop:
    """一次执行、多次观察的效果闭环。

    观察重试只重新截图和解析，绝不再次调用 ``executor``。是否重放动作必须由更高层在重新
    识别场景、检查风险和读取本结果后显式决定。
    """

    def __init__(
        self,
        sampler: BattleFrameSampler,
        executor: ActionExecutor,
        *,
        oracle: BattleEffectOracle | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_after_observations: int = 3,
        on_reobserve: ReobserveHook | None = None,
    ) -> None:
        if max_after_observations < 1:
            raise ValueError("max_after_observations must be at least 1")
        self._sampler = sampler
        self._executor = executor
        self._oracle = oracle or BattleEffectOracle()
        self._clock = clock
        self._max_after_observations = max_after_observations
        self._on_reobserve = on_reobserve

    def run(
        self,
        call: BattleActionCall,
        *,
        before: BattleFrameEnvelopeV1 | None = None,
    ) -> ActionLoopOutcomeV1:
        if before is None:
            self._request_full_observation()
            before = self._sampler.capture()
        if not _battle_hud_confirmed(before.state, action_name=call.action_name):
            raise BattleActionPreconditionError(
                f"{call.action_name} requires a confirmed fresh battle HUD; action was not executed"
            )
        issued = self._clock()
        events = tuple(self._executor(call))
        completed = self._clock()
        if completed < issued:
            raise ValueError("action executor clock moved backwards")

        after_frames: list[BattleFrameEnvelopeV1] = []
        if call.action_name == "Output":
            attempt = _attempt(call, before, after_frames, issued, completed, events)
            result = self._oracle.verify(attempt)
            return _outcome(attempt, result)

        result: EffectOracleResultV1 | None = None
        attempt: BattleActionEffectAttemptV1 | None = None
        for index in range(self._max_after_observations):
            if index > 0 and self._on_reobserve is not None and result is not None:
                self._on_reobserve(index, result)
            self._request_full_observation()
            after_frames.append(self._sampler.capture())
            attempt = _attempt(call, before, after_frames, issued, completed, events)
            result = self._oracle.verify(attempt)
            if result.verdict not in {
                EffectVerdict.NO_EFFECT,
                EffectVerdict.STALE_EVIDENCE,
                EffectVerdict.INCONCLUSIVE,
            }:
                break
        assert attempt is not None and result is not None
        return _outcome(attempt, result)

    def _request_full_observation(self) -> None:
        request = getattr(self._sampler, "request_full_observation", None)
        if callable(request):
            request()


class MaaBattleFrameSampler:
    """把 MaaCore 强制刷新帧写入证据目录并解析成 ``BattleStateV1``。"""

    def __init__(
        self,
        operator: MaaOperator,
        evidence_dir: Path | str,
        *,
        parser: Callable[[Path], BattleStateV1] | None = None,
        stage_grid: StageGrid | None = None,
        clock: Callable[[], float] = time.monotonic,
        before_capture: BeforeCaptureHook | None = None,
        after_capture: AfterCaptureHook | None = None,
        after_parse: AfterParseHook | None = None,
        active_game_clock: Callable[[], float] | None = None,
    ) -> None:
        if parser is not None and stage_grid is not None:
            raise ValueError("parser and stage_grid are mutually exclusive")
        self._operator = operator
        self._evidence_dir = Path(evidence_dir).resolve()
        if parser is not None:
            self._parser = parser
        elif stage_grid is not None:
            self._parser = BattleStateParser(stage_grid=stage_grid)
        else:
            self._parser = parse_battle_frame
        self._clock = clock
        self._before_capture = before_capture
        self._after_capture = after_capture
        self._after_parse = after_parse
        self._active_game_clock = active_game_clock
        self._sequence = 0

    def request_full_observation(self) -> None:
        request = getattr(self._parser, "request_full_observation", None)
        if callable(request):
            request()

    def capture(self) -> BattleFrameEnvelopeV1:
        if self._before_capture is not None:
            self._before_capture()
        requested = self._clock()
        refreshed = self._operator.refresh_frame(block=True)
        if not refreshed:
            raise BattleFrameCaptureError(
                "MaaCore forced refresh failed; cached image was not read"
            )
        payload = self._operator.capture_png(fresh=False)
        captured = self._clock()
        if payload is None:
            raise BattleFrameCaptureError("MaaCore returned no PNG after successful refresh")

        digest = hashlib.sha256(payload).hexdigest()
        self._sequence += 1
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        target = self._evidence_dir / f"frame-{self._sequence:04d}-{digest[:12]}.png"
        target.write_bytes(payload)
        if self._after_capture is not None:
            self._after_capture(target)
        state = self._parser(target)
        if state.frame.sha256 != digest:
            raise BattleFrameCaptureError(
                "BattleState frame hash does not match captured PNG evidence"
            )
        envelope = BattleFrameEnvelopeV1(
            state=state,
            capture_requested_monotonic=requested,
            captured_monotonic=captured,
            refresh_succeeded=True,
            source="maa_async_screencap",
            active_game_seconds=(
                self._active_game_clock() if self._active_game_clock is not None else None
            ),
        )
        if self._after_parse is not None:
            self._after_parse(envelope)
        return envelope


def _attempt(
    call: BattleActionCall,
    before: BattleFrameEnvelopeV1,
    after: Sequence[BattleFrameEnvelopeV1],
    issued: float,
    completed: float,
    events: tuple[MaaEvent, ...],
) -> BattleActionEffectAttemptV1:
    return BattleActionEffectAttemptV1(
        call=call,
        before=before,
        after=tuple(after),
        action_issued_monotonic=issued,
        action_completed_monotonic=completed,
        events=events,
    )


def _outcome(
    attempt: BattleActionEffectAttemptV1,
    result: EffectOracleResultV1,
) -> ActionLoopOutcomeV1:
    return ActionLoopOutcomeV1(
        status=(ActionLoopStatus.VERIFIED if result.verified else ActionLoopStatus.STOPPED),
        attempt=attempt,
        result=result,
        execution_count=1,
        observation_count=1 + len(attempt.after),
        action_reexecuted=False,
        recommended_recovery=_recommended_recovery(result),
    )


def _recommended_recovery(result: EffectOracleResultV1) -> RecoveryMode | None:
    if result.verified:
        return None
    preferred = (
        (RecoveryMode.STOP_AND_REPORT, RecoveryMode.RELOCALIZE)
        if result.verdict
        in {
            EffectVerdict.WRONG_TARGET,
            EffectVerdict.FORBIDDEN_EFFECT,
            EffectVerdict.UNSUPPORTED,
        }
        else (RecoveryMode.REOBSERVE, RecoveryMode.RELOCALIZE)
    )
    for mode in preferred:
        if mode in result.recovery:
            return mode
    return result.recovery[0] if result.recovery else None


def _battle_hud_confirmed(state: BattleStateV1, *, action_name: str) -> bool:
    global_state = state.global_state
    anchors_confirmed = all(
        observation.known
        for observation in (
            global_state.kills,
            global_state.total_enemies,
            global_state.life_points,
            global_state.paused,
        )
    )
    if not anchors_confirmed:
        return False
    # Integer DP is a mandatory deployment precondition.  Skills, retreats and
    # output actions do not spend DP, so a capped/occluded numeral must not
    # suppress an otherwise confirmed visual action.
    return action_name != "Deploy" or global_state.current_cost.known
