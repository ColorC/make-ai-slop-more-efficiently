"""明日方舟界面状态机设施：把「现在在哪个界面」变成可判定的枚举，并提供到目标界面的路径。

**存在理由**：MAA 的任务完成回调不可信（实测 ``StartUp`` 报完成而界面一字未变），
而单个模板锚点也不可信（实测 ``BattleOfficiallyBegin`` 匹配的是暂停按钮，暂停后必然失配）。
因此需要一层独立的、以自建 OCR 锚点为依据的状态判定，供上层做「先确认在哪，再决定做什么」。

**顺序敏感**：锚点检测有成本（未命中约需 16~20s，命中仅 0.9~2.2s），且不同界面的锚点可能
同时存在（例如结算页背后仍是战斗场景）。故按**特异性从高到低**顺序判定，命中即返回，
不做全量探测。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Sequence

__all__ = ["Screen", "ScreenProbe", "SCREEN_ANCHORS", "NAV_STEPS"]


class Screen(str, Enum):
    """可判定的界面状态。"""

    UNKNOWN = "unknown"
    DEVICE_HOME = "device_home"
    TITLE = "title"
    HOME = "home"
    CHAPTER = "chapter"
    STAGE_DETAIL = "stage_detail"
    FORMATION = "formation"
    BATTLE = "battle"
    BATTLE_PAUSED = "battle_paused"
    TAKEOVER_CONFIRM = "takeover_confirm"
    SETTLEMENT = "settlement"


#: 判定顺序：**特异性高的在前**。模态弹窗优先于其背后的界面，
#: 结算优先于战斗（结算页背后仍是战斗场景），暂停优先于战斗。
SCREEN_ANCHORS: tuple[tuple[Screen, str], ...] = (
    (Screen.TAKEOVER_CONFIRM, "Omni@Battle-TakeOverConfirm-Detect"),
    (Screen.SETTLEMENT, "Omni@Settlement-Detect"),
    (Screen.BATTLE_PAUSED, "Omni@Battle-Paused"),
    (Screen.BATTLE, "Omni@Battle-Detect"),
    (Screen.FORMATION, "Omni@Formation-Detect"),
    (Screen.STAGE_DETAIL, "Omni@StageDetail-Detect"),
    (Screen.CHAPTER, "Omni@ChapterScreen-Detect"),
    (Screen.HOME, "Omni@Home-Detect"),
    (Screen.TITLE, "Omni@Title-Detect"),
)


#: 从某界面前进一步的动作。上层据此做有界的状态机推进，而不是盲目重试。
NAV_STEPS: dict[Screen, tuple[str, ...]] = {
    Screen.TAKEOVER_CONFIRM: ("Omni@Battle-TakeOverConfirm",),
    Screen.SETTLEMENT: ("Omni@Settlement-Dismiss",),
    Screen.HOME: ("Omni@Home-GoLastStage",),
    Screen.TITLE: (),          # 交给 StartUp 任务
    Screen.DEVICE_HOME: (),    # 交给 StartUp(launch_game=True)
}


@dataclass(frozen=True, slots=True)
class ScreenProbe:
    """按特异性顺序判定界面。

    :param hit: 执行一个锚点任务并返回是否命中。
    """

    hit: Callable[[str], bool]
    anchors: Sequence[tuple[Screen, str]] = SCREEN_ANCHORS

    def detect(self) -> tuple[Screen, list[str]]:
        """:return: ``(判定结果, 已尝试的锚点列表)``。"""
        tried: list[str] = []
        for screen, task in self.anchors:
            tried.append(task)
            if self.hit(task):
                return screen, tried
        return Screen.UNKNOWN, tried

    def next_steps(self, screen: Screen) -> tuple[str, ...]:
        return NAV_STEPS.get(screen, ())
