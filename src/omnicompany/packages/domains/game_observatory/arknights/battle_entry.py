# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T08:00:00Z type=battle-entry status=active
# [OMNI] summary="以固定 MAA 任务名和独立日志标签编排 HOME 到战斗 HUD 的检查点式入场。"
# [OMNI] why="临时真机探针曾把日志标签当作任务名，错过战斗窗口并让一场样本自然失败。"
# [OMNI] tags=game-observatory,arknights,maa,battle-entry,checkpoint,fail-closed
"""从 HOME 到已确认战斗界面的有界语义检查点。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .maa_operator import MaaEvent, MaaTaskFailed

__all__ = [
    "BattleEntryCheckpointV1",
    "BattleEntryController",
    "BattleEntryError",
    "BattleEntryResultV1",
]


class BattleEntryError(RuntimeError):
    """入场检查点缺失；调用方必须停止，不能继续下发战斗动作。"""


class BattleEntryOperations(Protocol):
    def run_maa_tasks(
        self, task_names: Sequence[str], *, timeout: float = 60.0
    ) -> Sequence[MaaEvent]: ...

    def battle_begin(self) -> Sequence[MaaEvent]: ...


CaptureCheckpoint = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class BattleEntryCheckpointV1:
    """日志标签与真实 MAA 任务名被刻意分成两个字段。"""

    label: str
    task_name: str
    matched: bool
    events: tuple[MaaEvent, ...]
    evidence_ref: str | None = None
    started_monotonic: float | None = None
    finished_monotonic: float | None = None


@dataclass(frozen=True, slots=True)
class BattleEntryResultV1:
    stage: str
    used_terminal: bool
    battle_begin_count: int
    battle_confirmed: bool
    checkpoints: tuple[BattleEntryCheckpointV1, ...]
    battle_frozen: bool = False


_HOME = "Omni@Home-Detect"
_HOME_LAST_STAGE = "Omni@Home-GoLastStage"
_OPEN_TERMINAL = "Omni@Home-OpenTerminal"
_TERMINAL = "Omni@TerminalHub-Detect"
_STAGE_DETAIL = "Omni@StageDetail-Detect"
_PROXY_ENABLED = "Omni@StageDetail-ProxyEnabled"
_DISABLE_PROXY = "Omni@StageDetail-DisableProxy"
_FORMATION = "Omni@Formation-Detect"
_BATTLE_FREEZE = "Omni@Battle-FreezeAtFirstHUD"
_BATTLE_PAUSED = "Omni@Battle-Paused"
_STAGE_CODE_TASK = {"1-7": "Omni@StageDetail-1-7-Detect"}


class BattleEntryController:
    """最多两次 ``BattleStartAll``，并在每次迁移后要求独立场景锚点。"""

    def __init__(
        self,
        operations: BattleEntryOperations,
        *,
        capture: CaptureCheckpoint | None = None,
    ) -> None:
        self._operations = operations
        self._capture = capture

    def enter_from_home(
        self,
        stage: str,
        *,
        prefer_terminal: bool = False,
    ) -> BattleEntryResultV1:
        code_task = _STAGE_CODE_TASK.get(stage)
        if code_task is None:
            raise BattleEntryError(f"stage route is not registered: {stage}")

        checkpoints: list[BattleEntryCheckpointV1] = []
        self._must(checkpoints, "home", _HOME, timeout=25.0, capture=True)

        used_terminal = prefer_terminal
        if prefer_terminal:
            self._through_terminal(checkpoints)
        elif not self._try(
            checkpoints,
            "home-last-stage-shortcut",
            _HOME_LAST_STAGE,
            timeout=20.0,
        ):
            used_terminal = True
            self._through_terminal(checkpoints)

        self._must(
            checkpoints,
            "stage-detail",
            _STAGE_DETAIL,
            timeout=20.0,
        )
        self._must(
            checkpoints,
            f"stage-code-{stage}",
            code_task,
            timeout=16.0,
            capture=True,
        )
        self._disable_proxy_if_needed(checkpoints)

        begin_count = 1
        self._begin(checkpoints, "battle-start-1")
        if self._try(
            checkpoints,
            "freeze-at-first-hud-after-start-1",
            _BATTLE_FREEZE,
            timeout=18.0,
            capture=True,
        ):
            self._must(
                checkpoints,
                "paused-after-start-1",
                _BATTLE_PAUSED,
                timeout=12.0,
                capture=True,
            )
            return BattleEntryResultV1(
                stage,
                used_terminal,
                begin_count,
                True,
                tuple(checkpoints),
                battle_frozen=True,
            )

        self._must(
            checkpoints,
            "formation-after-start-1",
            _FORMATION,
            timeout=18.0,
            capture=True,
        )
        begin_count = 2
        self._begin(checkpoints, "battle-start-2")
        self._must(
            checkpoints,
            "freeze-at-first-hud-after-start-2",
            _BATTLE_FREEZE,
            timeout=25.0,
            capture=True,
        )
        self._must(
            checkpoints,
            "paused-after-start-2",
            _BATTLE_PAUSED,
            timeout=12.0,
            capture=True,
        )
        return BattleEntryResultV1(
            stage,
            used_terminal,
            begin_count,
            True,
            tuple(checkpoints),
            battle_frozen=True,
        )

    def _through_terminal(
        self, checkpoints: list[BattleEntryCheckpointV1]
    ) -> None:
        self._must(
            checkpoints,
            "open-terminal",
            _OPEN_TERMINAL,
            timeout=25.0,
        )
        self._must(
            checkpoints,
            "terminal",
            _TERMINAL,
            timeout=20.0,
            capture=True,
        )
        self._must(
            checkpoints,
            "terminal-last-stage",
            _HOME_LAST_STAGE,
            timeout=25.0,
        )

    def _disable_proxy_if_needed(
        self, checkpoints: list[BattleEntryCheckpointV1]
    ) -> None:
        if not self._try(
            checkpoints,
            "proxy-enabled",
            _PROXY_ENABLED,
            timeout=12.0,
        ):
            return
        self._must(
            checkpoints,
            "disable-proxy",
            _DISABLE_PROXY,
            timeout=15.0,
        )
        if self._try(
            checkpoints,
            "proxy-after-disable",
            _PROXY_ENABLED,
            timeout=12.0,
        ):
            raise BattleEntryError("proxy remains enabled after one disable action")

    def _begin(
        self, checkpoints: list[BattleEntryCheckpointV1], label: str
    ) -> None:
        started = time.monotonic()
        try:
            events = tuple(self._operations.battle_begin())
        except MaaTaskFailed as exc:
            raise BattleEntryError(f"{label} failed: {exc}") from exc
        checkpoints.append(
            BattleEntryCheckpointV1(
                label,
                "SingleStep/start",
                True,
                events,
                started_monotonic=started,
                finished_monotonic=time.monotonic(),
            )
        )

    def _must(
        self,
        checkpoints: list[BattleEntryCheckpointV1],
        label: str,
        task_name: str,
        *,
        timeout: float,
        capture: bool = False,
    ) -> None:
        if not self._try(
            checkpoints,
            label,
            task_name,
            timeout=timeout,
            capture=capture,
        ):
            raise BattleEntryError(f"checkpoint {label!r} did not match {task_name!r}")

    def _try(
        self,
        checkpoints: list[BattleEntryCheckpointV1],
        label: str,
        task_name: str,
        *,
        timeout: float,
        capture: bool = False,
    ) -> bool:
        started = time.monotonic()
        try:
            events = tuple(
                self._operations.run_maa_tasks([task_name], timeout=timeout)
            )
        except MaaTaskFailed:
            checkpoints.append(
                BattleEntryCheckpointV1(
                    label,
                    task_name,
                    False,
                    (),
                    started_monotonic=started,
                    finished_monotonic=time.monotonic(),
                )
            )
            return False
        evidence = self._capture(label) if capture and self._capture else None
        checkpoints.append(
            BattleEntryCheckpointV1(
                label,
                task_name,
                True,
                events,
                evidence,
                started_monotonic=started,
                finished_monotonic=time.monotonic(),
            )
        )
        return True
