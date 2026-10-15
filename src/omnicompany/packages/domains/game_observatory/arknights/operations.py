# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T06:48:00Z type=operations status=active
# [OMNI] summary="明日方舟语义操作入口，将意图与闭门校验后的 MAA 调用统一交给 MaaOperator。"
# [OMNI] why="调用方不应携带像素坐标，也不应绕过任务、动作和授权语义边界。"
# [OMNI] tags=game-observatory,arknights,maa,operations
"""明日方舟语义操作设施：把游戏内动作表达为意图，执行委托给 MAA。

设计原则（ISR-R18）：
    调用方只说「去主界面」「打 1-7」「暂停」「在 (4,3) 部署史尔特尔朝左」，
    **不出现任何像素坐标，也不出现 MAA 的任务名**。
    任务名集中在本文件的常量里，坐标全部由 MAA 自己的模板/OCR 决定。

参数与取值**以 MAA 源码与官方协议文档为准**（MAA 为 AGPL-3.0 开源）：
    * ``Custom`` 的可编程入口是 ``task_names``（``CustomTask::set_params``，且会校验每个
      任务名是否存在，不存在则 set_params 失败）。
    * ``SingleStep`` 的分派字段是 **``subtype``**，``stage`` 子类型的 details 键是
      **``stage_name``**（``SingleStepTask::set_params``）。
    * 战斗动作类型与触发条件见 ``docs/zh-cn/protocol/copilot-schema.md``。

> **一处方法论更正**：早期版本曾从 ``MaaCore.dll`` 的字符串表反推这些参数，因为发行包里
> 没带 docs。这是弯路——MAA 是开源项目，源码与协议文档都在仓库里。反推共造成 8 处偏差，
> 其中 ``subtask``→``subtype``、``stage``→``stage_name`` 两处会让整条 SingleStep 通路
> **静默失效**（``set_params`` 返回 false）。凡有源码可读，就不该扒二进制。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Sequence

from .maa_operator import MaaEvent, MaaOperator

if TYPE_CHECKING:
    from .maa_contracts import BattleActionCall, PublicTaskCall

__all__ = [
    "ActionType",
    "ArknightsOperations",
    "ClientType",
    "Direction",
    "MaaTask",
]


class MaaTask:
    """MAA 非 Debug 构建公开的 17 类任务名。"""

    FIGHT = "Fight"
    START_UP = "StartUp"
    CLOSE_DOWN = "CloseDown"
    AWARD = "Award"
    MALL = "Mall"
    INFRAST = "Infrast"
    RECRUIT = "Recruit"
    ROGUELIKE = "Roguelike"
    COPILOT = "Copilot"
    SSS_COPILOT = "SSSCopilot"
    PARADOX_COPILOT = "ParadoxCopilot"
    SINGLE_STEP = "SingleStep"
    VIDEO_RECOGNITION = "VideoRecognition"
    DEPOT = "Depot"
    OPER_BOX = "OperBox"
    RECLAMATION = "Reclamation"
    CUSTOM = "Custom"

    @classmethod
    def values(cls) -> tuple[str, ...]:
        """以 ``Assistant::append_task`` 的非 Debug 分支顺序返回公开任务。"""

        return (
            cls.FIGHT,
            cls.START_UP,
            cls.CLOSE_DOWN,
            cls.AWARD,
            cls.MALL,
            cls.INFRAST,
            cls.RECRUIT,
            cls.ROGUELIKE,
            cls.COPILOT,
            cls.SSS_COPILOT,
            cls.PARADOX_COPILOT,
            cls.SINGLE_STEP,
            cls.VIDEO_RECOGNITION,
            cls.DEPOT,
            cls.OPER_BOX,
            cls.RECLAMATION,
            cls.CUSTOM,
        )


class _T:
    """``tasks.json`` 中的具体任务名，供 ``Custom`` 组合调用。"""

    BATTLE_PAUSE = "BattlePause"
    BATTLE_FREEZE_FIRST_HUD = "Omni@Battle-FreezeAtFirstHUD"
    BATTLE_RESUME = "Omni@Battle-ResumeOnce"
    BATTLE_USE_SELECTED_SKILL = "BattleUseSkillJustClick"
    BATTLE_IN_PROGRESS = "BattleOfficiallyBegin"
    BATTLE_SPEED_UP = "BattleSpeedUp"
    BATTLE_OPEN_SETTINGS = "Omni@Battle-OpenSettings"
    ABANDON_CONFIRM = "NormalBattleAbandon"
    MISSION_FAILED_DISMISS = "Omni@MissionFailed-Dismiss"
    RETURN = "Return"
    CLICK_CORNER = "ClickCorner"


class ClientType(str, Enum):
    OFFICIAL = "Official"
    BILIBILI = "Bilibili"
    TXWY = "txwy"
    YOSTAR_EN = "YoStarEN"
    YOSTAR_JP = "YoStarJP"
    YOSTAR_KR = "YoStarKR"


class Direction(str, Enum):
    """部署朝向。取值经 DLL 字符串表核对。"""

    LEFT = "Left"
    RIGHT = "Right"
    UP = "Up"
    DOWN = "Down"
    NONE = "None"


class ActionType(str, Enum):
    """SingleStep 战斗动作类型。

    **取值以 ``CopilotConfig::parse_actions`` 的 ``ActionTypeMapping`` 为准**，它是唯一
    真源：官方协议文档的行内列表少列了 ``DrawCard`` 与 ``CheckIfStartOver``。

    **危险默认**：MAA 对未知 type 只写一条 warn 然后 ``continue`` —— 动作被**静默丢弃**，
    不会报错。因此调用方必须用本枚举而非裸字符串。
    """

    DEPLOY = "Deploy"
    SKILL = "Skill"
    RETREAT = "Retreat"
    SPEED_UP = "SpeedUp"
    BULLET_TIME = "BulletTime"
    SKILL_USAGE = "SkillUsage"
    OUTPUT = "Output"
    SKILL_DAEMON = "SkillDaemon"
    MOVE_CAMERA = "MoveCamera"
    DRAW_CARD = "DrawCard"
    CHECK_IF_START_OVER = "CheckIfStartOver"
    RESET_STOPWATCH = "ResetStopwatch"




@dataclass(frozen=True, slots=True)
class SanityReading:
    """从 ``SanityBeforeStage`` 回调解析出的理智读数。"""

    current: int
    maximum: int

    @property
    def ratio(self) -> float:
        return self.current / self.maximum if self.maximum else 0.0


class ArknightsOperations:
    """语义操作层。所有方法都不接受、也不产生像素坐标。"""

    def __init__(self, op: MaaOperator) -> None:
        self._op = op
        self._battle_stage: str | None = None

    @property
    def maa(self) -> MaaOperator:
        return self._op

    # ---- 零成本动作：不进战斗、不花理智 ----

    def go_to_main_screen(
        self, client: ClientType = ClientType.OFFICIAL, *, launch_game: bool = False
    ) -> list[MaaEvent]:
        """从任意界面回到主界面（可选顺带启动游戏）。不消耗任何资源。

        这是验证操作通道是否真正贯通的最廉价动作：要走模板匹配、OCR 与点击全链。
        """
        return self.maa.run(
            [
                (
                    MaaTask.START_UP,
                    {
                        "enable": True,
                        "client_type": client.value,
                        "start_game_enabled": launch_game,
                    },
                )
            ],
            timeout=180.0,
        )

    def run_maa_tasks(
        self, task_names: Sequence[str], *, timeout: float = 60.0
    ) -> list[MaaEvent]:
        """用 ``Custom`` 执行 MAA 任务图中的任意任务序列。

        这是 MAA 暴露的可编程入口，让我们复用它的模板/OCR 识别而不必自己算坐标。
        """
        if not task_names:
            raise ValueError("task_names 不能为空")
        return self.maa.run(
            [(MaaTask.CUSTOM, {"enable": True, "task_names": list(task_names)})],
            timeout=timeout,
        )

    def start_rehearsal_battle(self) -> list[MaaEvent]:
        """Start an already-selected rehearsal from the squad page."""

        return self.run_maa_tasks(["BattleStartExercise"], timeout=30.0)

    def enter_rehearsal_mode(self) -> list[MaaEvent]:
        """Enter rehearsal through the reviewed stage-detail OCR task."""

        return self.run_maa_tasks(["Omni@PracticeMode-Enter"], timeout=15.0)

    def run_prepared_task(
        self, call: PublicTaskCall, *, timeout: float = 900.0
    ) -> list[MaaEvent]:
        """执行已经过参数/风险/授权契约编译的公开任务调用。"""

        return self.maa.run([call.as_operator_task()], timeout=timeout)

    def run_prepared_battle_actions(
        self, actions: Sequence[BattleActionCall], *, timeout: float = 180.0
    ) -> list[MaaEvent]:
        """把已闭门校验的战斗动作封装为一次 ``SingleStep`` 调用。"""

        return self.battle_actions([dict(action.details) for action in actions], timeout=timeout)

    # ---- 战斗内控制（暂停周期执行模型） ----

    def pause_battle(self) -> list[MaaEvent]:
        return self.run_maa_tasks([_T.BATTLE_PAUSE], timeout=30.0)

    def resume_battle(self) -> list[MaaEvent]:
        return self.run_maa_tasks([_T.BATTLE_RESUME], timeout=30.0)

    def use_selected_ready_skill(self) -> list[MaaEvent]:
        """Click MAA's canonical skill control on an already-selected operator.

        The caller must first prove that the paused operator detail overlay is
        visible and says READY. The control location remains owned by MAA's
        task graph rather than leaking into the semantic API.
        """

        return self.run_maa_tasks([_T.BATTLE_USE_SELECTED_SKILL], timeout=5.0)

    def use_skill_at(self, col: int, row: int) -> list[MaaEvent]:
        """Use a visually confirmed skill through the stage-calibrated Custom task."""

        stage_tasks = {
            "S2-3": {
                (2, 3): "Omni@Battle-Skill-S2-3-2-3",
            },
        }
        if self._battle_stage in stage_tasks:
            task = stage_tasks[self._battle_stage].get((col, row))
            if task is None:
                raise ValueError(
                    f"unsupported visual skill tile: ({col},{row}) "
                    f"for stage {self._battle_stage}"
                )
            return self.run_maa_tasks([task], timeout=15.0)

        supported = {
            (1, 2),
            (1, 3),
            (1, 4),
            (4, 1),
            (4, 3),
            (4, 5),
            (5, 2),
            (5, 4),
            (6, 2),
            (6, 4),
            (7, 3),
        }
        if (col, row) not in supported:
            raise ValueError(f"unsupported visual skill tile: ({col},{row})")
        return self.run_maa_tasks(
            [f"Omni@Battle-Skill-{col}-{row}"],
            timeout=15.0,
        )

    def speed_up_battle(self) -> list[MaaEvent]:
        return self.run_maa_tasks([_T.BATTLE_SPEED_UP], timeout=30.0)

    def go_back(self) -> list[MaaEvent]:
        """点击当前界面的返回按钮；具体坐标由 MAA 的 ``Return`` 任务识别。"""
        return self.run_maa_tasks([_T.RETURN], timeout=30.0)

    def abandon_battle(self) -> list[MaaEvent]:
        """放弃当前作战。

        用于「不打完关卡」：进战斗完成观测后干净退出。注意**理智在开始时已扣除，
        放弃不退还**。
        """
        events = self.run_maa_tasks([_T.BATTLE_OPEN_SETTINGS], timeout=15.0)
        events.extend(self.run_maa_tasks([_T.ABANDON_CONFIRM], timeout=60.0))
        return events

    def dismiss_mission_failed(self) -> list[MaaEvent]:
        """Dismiss the visually confirmed full-screen failure/result page once."""
        return self.run_maa_tasks([_T.MISSION_FAILED_DISMISS], timeout=20.0)

    # ---- 正式作战（会消耗理智，慎用） ----

    def fight(
        self,
        stage: str,
        *,
        times: int = 1,
        medicine: int = 0,
        stone: int = 0,
        series: int | None = None,
        client: ClientType = ClientType.OFFICIAL,
        timeout: float = 900.0,
    ) -> list[MaaEvent]:
        """导航到 ``stage`` 并用代理指挥作战。

        **会消耗理智。** ``medicine``／``stone`` 默认 0，即绝不自动嗑药、绝不碎石。
        """
        params: dict[str, Any] = {
            "enable": True,
            "stage": stage,
            "times": times,
            "medicine": medicine,
            "expiring_medicine": 0,
            "stone": stone,
            "report_to_penguin": False,
            "client_type": client.value,
            "DrGrandet": False,
        }
        if series is not None:
            params["series"] = series
        return self.maa.run([(MaaTask.FIGHT, params)], timeout=timeout)

    # ---- SingleStep：最细粒度战斗控制 ----
    #
    # 参数以 MAA 源码 ``SingleStepTask::set_params`` 为准：
    #   * 分派字段是 **``subtype``**，不是 ``subtask``；
    #   * ``subtype="stage"`` 的 details 键是 **``stage_name``**，不是 ``stage``；
    #   * ``subtype="start"`` 内部就是跑 ``BattleStartAll``；
    #   * ``subtype="action"`` 的 details 交给 ``CopilotConfig::parse_actions``，
    #     即**单个动作对象**，字段见官方作业协议。
    # 早期版本按 DLL 字符串表写成 ``subtask``／``stage``，会让 ``set_params`` 直接返回
    # false（整条 SingleStep 通路静默失效）。

    def battle_bind_stage(self, stage_name: str) -> list[MaaEvent]:
        """告知 MAA 当前关卡，使其载入该关格子数据（``set_stage_name_cache``）。"""
        events = self.maa.run(
            [
                (
                    MaaTask.SINGLE_STEP,
                    {
                        "enable": True,
                        "type": "copilot",
                        "subtype": "stage",
                        "details": {"stage_name": stage_name},
                    },
                )
            ],
            timeout=60.0,
        )
        self._battle_stage = stage_name
        return events

    def battle_begin(self) -> list[MaaEvent]:
        """开始战斗（内部执行 ``BattleStartAll``）。"""
        return self.maa.run(
            [
                (
                    MaaTask.SINGLE_STEP,
                    {"enable": True, "type": "copilot", "subtype": "start"},
                )
            ],
            timeout=120.0,
        )

    def battle_begin_then_pause(self) -> list[MaaEvent]:
        """Start and arm the first-HUD pause in one uninterrupted MAA job."""

        return self.maa.run(
            [
                (
                    MaaTask.SINGLE_STEP,
                    {"enable": True, "type": "copilot", "subtype": "start"},
                ),
                (
                    MaaTask.CUSTOM,
                    {
                        "enable": True,
                        "task_names": [_T.BATTLE_FREEZE_FIRST_HUD],
                    },
                ),
            ],
            timeout=120.0,
        )

    def battle_begin_actions_then_pause(
        self,
        actions: Sequence[dict[str, Any]],
        *,
        timeout: float = 240.0,
    ) -> list[MaaEvent]:
        """Run a complete timed Copilot program from the squad page as one job."""

        if not actions:
            raise ValueError("actions cannot be empty")
        return self.maa.run(
            [
                (
                    MaaTask.SINGLE_STEP,
                    {"enable": True, "type": "copilot", "subtype": "start"},
                ),
                (
                    MaaTask.SINGLE_STEP,
                    {
                        "enable": True,
                        "type": "copilot",
                        "subtype": "action",
                        "details": {"actions": list(actions)},
                    },
                ),
                (
                    MaaTask.CUSTOM,
                    {"enable": True, "task_names": [_T.BATTLE_PAUSE]},
                ),
            ],
            timeout=timeout,
        )

    def battle_action(
        self,
        action: ActionType,
        *,
        name: str | None = None,
        col: int | None = None,
        row: int | None = None,
        direction: Direction | None = None,
        kills: int | None = None,
        costs: int | None = None,
        cost_changes: int | None = None,
        cooling: int | None = None,
        elapsed_time: int | None = None,
        skill_usage: int | None = None,
        skill_times: int | None = None,
        skip_if_not_ready: bool | None = None,
        pre_delay: int | None = None,
        post_delay: int | None = None,
        distance: tuple[float, float] | None = None,
        doc: str | None = None,
        **extra: Any,
    ) -> list[MaaEvent]:
        """执行**一个**战斗动作。多个动作请用 :meth:`battle_actions`（一次下发更省往返）。

        坐标用**格子** ``(col, row)`` 表达，映射到协议的 ``location: [x, y]``
        （``x`` 取 ``location[0]``、``y`` 取 ``location[1]``，见 ``parse_actions``）。
        调用方永不接触像素。

        触发条件之间是**且**的关系。官方协议对费用类条件有明确警告：``costs`` 受潜能影响
        可能不准；``costs`` 与 ``cost_changes`` **仅在费用为两位数时识别较准，三位数可能
        识别错、官方不推荐**。故以可复现性为目标时应优先用 ``kills``。

        :param elapsed_time: 全局计时条件。**注意字段名**：官方文档写作 ``time_elapsed``，
            但 ``parse_actions`` 实际读的是 ``elapsed_time`` —— 以代码为准，写错会被静默
            忽略。使用前须先下一个 :meth:`reset_stopwatch`。
        """
        return self.battle_actions(
            [
                self.make_action(
                    action,
                    name=name,
                    col=col,
                    row=row,
                    direction=direction,
                    kills=kills,
                    costs=costs,
                    cost_changes=cost_changes,
                    cooling=cooling,
                    elapsed_time=elapsed_time,
                    skill_usage=skill_usage,
                    skill_times=skill_times,
                    skip_if_not_ready=skip_if_not_ready,
                    pre_delay=pre_delay,
                    post_delay=post_delay,
                    distance=distance,
                    doc=doc,
                    **extra,
                )
            ]
        )

    @staticmethod
    def make_action(
        action: ActionType,
        *,
        name: str | None = None,
        col: int | None = None,
        row: int | None = None,
        direction: Direction | None = None,
        kills: int | None = None,
        costs: int | None = None,
        cost_changes: int | None = None,
        cooling: int | None = None,
        elapsed_time: int | None = None,
        skill_usage: int | None = None,
        skill_times: int | None = None,
        skip_if_not_ready: bool | None = None,
        pre_delay: int | None = None,
        post_delay: int | None = None,
        distance: tuple[float, float] | None = None,
        doc: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """构造一个符合官方作业协议的动作对象（不执行）。"""
        details: dict[str, Any] = {"type": action.value}
        if name is not None:
            details["name"] = name
        if col is not None and row is not None:
            details["location"] = [col, row]
        elif (col is None) != (row is None):
            raise ValueError("col 与 row 必须同时给出")
        if direction is not None:
            details["direction"] = direction.value
        for key, val in (
            ("kills", kills),
            ("costs", costs),
            ("cost_changes", cost_changes),
            ("cooling", cooling),
            ("elapsed_time", elapsed_time),
            ("skill_usage", skill_usage),
            ("skill_times", skill_times),
            ("skip_if_not_ready", skip_if_not_ready),
            ("pre_delay", pre_delay),
            ("post_delay", post_delay),
            ("doc", doc),
        ):
            if val is not None:
                details[key] = val
        if distance is not None:
            details["distance"] = [distance[0], distance[1]]
        if action is ActionType.MOVE_CAMERA and "distance" not in details:
            raise ValueError("MoveCamera 必须给 distance —— MAA 会直接 at('distance') 抛错")
        details.update(extra)
        return details

    def battle_actions(
        self, actions: Sequence[dict[str, Any]], *, timeout: float = 180.0
    ) -> list[MaaEvent]:
        """一次下发多个战斗动作。

        **details 的形状是 ``{"actions": [...]}``**，不是单个动作对象——
        ``CopilotConfig::parse_actions`` 直接 ``json.at("actions").as_array()``，
        缺 ``actions`` 键会让 ``set_params`` 抛错、``append_task`` 返回失败。
        （实测踩过：按单个动作对象下发，四次部署全部 ``append_task 失败``。）
        """
        if not actions:
            raise ValueError("actions 不能为空")
        return self.maa.run(
            [
                (
                    MaaTask.SINGLE_STEP,
                    {
                        "enable": True,
                        "type": "copilot",
                        "subtype": "action",
                        "details": {"actions": list(actions)},
                    },
                )
            ],
            timeout=timeout,
        )

    def battle_actions_from_pause(
        self, actions: Sequence[dict[str, Any]], *, timeout: float = 180.0
    ) -> list[MaaEvent]:
        """Execute actions from a paused decision frame and finish paused.

        ``deployment_with_pause`` pauses after MAA starts the card drag; it
        does not let SingleStep discover a card while the game's PAUSE overlay
        is still up. Keep resume, action, and final pause in one native queue
        so Python never leaves uncontrolled live gameplay between them.
        """
        if not actions:
            raise ValueError("actions cannot be empty")
        return self.maa.run(
            [
                (
                    MaaTask.CUSTOM,
                    {"enable": True, "task_names": [_T.BATTLE_RESUME]},
                ),
                (
                    MaaTask.SINGLE_STEP,
                    {
                        "enable": True,
                        "type": "copilot",
                        "subtype": "action",
                        "details": {"actions": list(actions)},
                    },
                ),
                (
                    MaaTask.CUSTOM,
                    {"enable": True, "task_names": [_T.BATTLE_PAUSE]},
                ),
            ],
            timeout=timeout,
        )

    def deploy(
        self,
        operator: str,
        col: int,
        row: int,
        direction: Direction,
        **conditions: Any,
    ) -> list[MaaEvent]:
        return self.battle_action(
            ActionType.DEPLOY,
            name=operator,
            col=col,
            row=row,
            direction=direction,
            **conditions,
        )

    def retreat(
        self, *, name: str | None = None, col: int | None = None,
        row: int | None = None, **conditions: Any,
    ) -> list[MaaEvent]:
        """撤退。正常部署的干员推荐用 ``name``；同名召唤物用 ``col``/``row``。"""
        return self.battle_action(
            ActionType.RETREAT, name=name, col=col, row=row, **conditions
        )

    def use_skill(
        self, *, name: str | None = None, col: int | None = None,
        row: int | None = None, **conditions: Any,
    ) -> list[MaaEvent]:
        """开技能。正常部署的干员推荐用 ``name``；场地装置用 ``col``/``row``。"""
        return self.battle_action(
            ActionType.SKILL, name=name, col=col, row=row, **conditions
        )

    def speed_up(self) -> list[MaaEvent]:
        """切换二倍速。**可切换**：用一次变二倍速，再用一次变回一倍速。"""
        return self.battle_action(ActionType.SPEED_UP)

    def reset_stopwatch(self) -> list[MaaEvent]:
        """重置全局计时器，``time_elapsed`` 条件的前置。"""
        return self.battle_action(ActionType.RESET_STOPWATCH)

    def idle_until_end(self) -> list[MaaEvent]:
        """摆完挂机：只用「好了就用」的技能，其余不做，直到战斗结束。"""
        return self.battle_action(ActionType.SKILL_DAEMON)


    # ---- 从事件流里读状态 ----

    @staticmethod
    def read_sanity(events: Sequence[MaaEvent]) -> SanityReading | None:
        """从事件里取理智读数。MAA 通过 ``SanityBeforeStage`` 免费提供，无需自建识别。"""
        for ev in reversed(events):
            if ev.what != "SanityBeforeStage":
                continue
            d = ev.details.get("details", {})
            cur, mx = d.get("current_sanity"), d.get("max_sanity")
            if isinstance(cur, int) and isinstance(mx, int):
                return SanityReading(cur, mx)
        return None

    @staticmethod
    def find_events(events: Sequence[MaaEvent], what: str) -> list[MaaEvent]:
        return [e for e in events if e.what == what]
