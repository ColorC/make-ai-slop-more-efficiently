# [OMNI] origin=grok domain=game_observatory/arknights type=play-surface
"""Git / text-game play surface for Sami Integrated Strategies.

I/O (MAA/ADB/OCR/pause/click) stays below this module.  Decision records are
authored here from the current compact state *before* any device action.
Deterministic page chains report ``model_roundtrips=0``.  Forecast is a pure
function of a stage+squad snapshot and refuses MAA autopilot JSON as a plan.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .is_shell import ISActionKind, ISScreenKind, legal_actions_for_screen
from .stage_simulation_bridge import forecast_plan
from .stage_world_model import OperatorCombatProfile, StageWorldModel

SCHEMA_STATE = "omnicompany.arknights.play-surface-state.v1"
SCHEMA_DECISION = "omnicompany.arknights.play-surface-decision.v1"
DIFFICULTY_CHALLENGE_NATURE_5 = 5

AUTO_LIFECYCLE_KINDS = frozenset({"continue-settlement", "start-new-exploration"})
INERT_LIFECYCLE_KINDS = frozenset({"open-normal-operation"})
NON_ATTEMPT_KINDS = frozenset(
    {"startup_error", "startup", "title_start", "maa_connect", "connection", "auth"}
)

# Live OCR rarely emits the textbook phrase. Containment beats exact-token
# equality; home is last and needs lobby-only pair so 干员/编队 on recruit
# cannot steal the page.
_PAGE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("theme-browser", ("进入主题", "查看收藏", "尚待探索")),
    (ISScreenKind.ENTRY.value, ("开始探索", "继续探索", "常规行动")),
    ("difficulty", ("ADDITIONAL", "得分效率", "敌人难度", "浪静风平")),
    ("settlement", ("探索结束", "得分汇总", "扩充结束", "联系中断", "风雪蔽目", "驻足在", "查看数据回顾", "TRAPPED", "JOIKLUM", "JOKLUMARKAR", "SKIP", "您完美通过", "您遇到了")),
    ("terminal", ("MAIN THEME", "TO-DO LIST", "前往上一次作战", "P.R.T.S.", "集成战略", "查看全部主题")),
    ("relic", ("选择一个密文板", "不要了，走了", "确认离开", "收下", "可组装的密文板")),
    ("opening", ("选择分队", "矛头分队", "指挥分队", "集群分队", "后勤分队", "选择支援", "选择招募组合", "初始招募", "重装招募", "术师招募", "狙击招募")),
    (ISScreenKind.ROUTE.value, ("指挥等级", "目标生命值", "出发前往", "敌方情报", "完成本层后", "经过此处")),
    (ISScreenKind.SQUAD.value, ("开始行动", "快捷编队", "编队上限", "编队确认", "开局分队")),
    (ISScreenKind.RECRUIT.value, ("临时招募", "干员招募", "希望不足", "招募券", "轻触右侧干员", "选择助战", "确认招募", "攻击范围", "确认要放弃")),
    (ISScreenKind.SHOP.value, ("投资系统", "刷新商品")),
    (ISScreenKind.EVENT.value, ("不期而遇", "选择一项", "朗诵")),

    ("battle", ("撤退", "暂停", "剩余可放置角色", "ISW-NO", "OPERATION")),
    ("reward", ("VANGUARD", "TEXAS", "获得干员", "获得收藏品")),
)
_HOME_PAIR = frozenset({"基建", "采购中心"})
_SKIP_TAP_LABELS = frozenset(
    {
        "希望",
        "NO INFO/",
        "职业",
        "分支+",
        "技",
        "技能",
        "wait",
        "轻触右侧干员以查看详情",
        "轻触右侧干员以查看详",
        "目标生命值",
        "指挥等级",
        "本次作战",
        "完美的战斗",
        "成功通过",
        "收藏品",
        "干员",
        "编队",
        "当前",
        "攻击范围",
        "中等",
    }
)
_RATIO = re.compile(r"^(\d+)\s*/\s*(\d+)$")
_SLOTS = re.compile(r"剩余可放置角色[：:]\s*(\d+)")
_BLOCKER_NAMES = frozenset(
    {"德克萨斯", "推进之王", "塞雷娅", "星熊", "年", "泥岩", "号角", "蛇屠箱", "山", "斑点"}
)
_RECRUIT_PRIORITY = (
    "斑点",
    "德克萨斯",
    "塞雷娅",
    "艾雅法拉",
    "烛煌",
    "伊芙利特",
    "安德切尔",
    "空爆",
    "克洛丝",
    "假日威龙陈",
    "芙蓉",
    "蛇屠箱",
    "山",
)
_RECRUIT_NON_OPERATORS = frozenset(
    {
        "选择助战",
        "确认招募",
        "招募",
        "放弃",
        "临时招募",
        "希望",
        "职业",
        "确认",
        "希望不足",
        "轻触右侧干员以查看详情",
        "轻触右侧干员以查看详",
    }
)
_CHEAP_RECRUITS = ("安德切尔", "空爆", "史都华德", "杜林", "克洛丝", "炎熔", "芙蓉")
_CHEAP_OPEN = ("安德切尔", "空爆", "巡林者", "克洛丝", "炎熔", "斑点", "芙蓉", "塞雷娅", "烛煌", "德克萨斯")
_OPEN_COST = {
    "安德切尔": 3,
    "预备干员-狙击": 3,
    "空爆": 5,
    "巡林者": 7,
    "克洛丝": 8,
    "斑点": 9,
    "德克萨斯": 11,
    "炎熔": 13,
    "芙蓉": 16,
    "塞雷娅": 18,
    "烛煌": 21,
}


def _open_cost_for(label: str) -> int:
    text = str(label or "")
    for name, cost in _OPEN_COST.items():
        if name == text or name in text:
            return cost
    return 8


def _cheapest_needed(items: Sequence[Mapping[str, Any]]) -> int:
    costs = [
        _open_cost_for(str(item.get("label") or item.get("action") or ""))
        for item in items
    ]
    return min(costs) if costs else 8


# Live-proved miss: [520, 430] is not a 苔手 high-ground cell for 安德切尔.
# Snipers / casters / medics try ridge points first; blockers try the path first.
# The runner must keep tapping the list until deploy_landed(cost_before, cost_after).
_RANGED_OPEN = (
    "安德切尔",
    "空爆",
    "巡林者",
    "克洛丝",
    "炎熔",
    "芙蓉",
    "史都华德",
    "烛煌",
    "预备干员-狙击",
    "预备干员-术师",
    "预备干员-医疗",
)
_MELEE_OPEN = (
    "斑点",
    "塞雷娅",
    "德克萨斯",
    "泥岩",
    "星熊",
    "蛇屠箱",
    "山",
    "预备干员-重装",
    "预备干员-先锋",
)
_HIGH_GROUND_TILES: tuple[tuple[int, int], ...] = (
    (640, 240),
    (720, 240),
    (560, 240),
    (800, 240),
    (480, 240),
    (640, 300),
    (720, 300),
    (560, 300),
    (800, 300),
    (680, 180),
    (760, 180),
    (600, 180),
    (700, 360),
    (580, 360),
    (820, 360),
    (520, 200),
    (880, 280),
)
_GROUND_TILES: tuple[tuple[int, int], ...] = (
    (400, 430),
    (520, 430),
    (640, 430),
    (360, 480),
    (480, 480),
    (300, 400),
    (700, 430),
    (450, 380),
    (580, 480),
)


def deploy_tile_candidates(operator: str, *, stage: str | None = None) -> list[tuple[int, int]]:
    """Ordered field points to try after a bench tap. First hit that drops cost wins."""

    del stage  # stage-specific maps can replace this list later
    name = str(operator or "")
    if any(token in name for token in _RANGED_OPEN):
        return list(_HIGH_GROUND_TILES) + list(_GROUND_TILES)
    if any(token in name for token in _MELEE_OPEN):
        return list(_GROUND_TILES) + list(_HIGH_GROUND_TILES)
    return list(_HIGH_GROUND_TILES) + list(_GROUND_TILES)


def deploy_landed(
    cost_before: int | None,
    cost_after: int | None,
    *,
    slots_before: int | None = None,
    slots_after: int | None = None,
) -> bool:
    """A deploy landed when HUD cost or remaining slots strictly dropped.

    Preview overlays often hide n/m; slots still move from 7 to 6.
    Missing OCR is not a land.
    """

    if cost_before is not None and cost_after is not None and int(cost_after) < int(cost_before):
        return True
    if slots_before is not None and slots_after is not None and int(slots_after) < int(slots_before):
        return True
    return False


def deploy_gesture(
    bench: Sequence[int] | tuple[int, int],
    tile: Sequence[int] | tuple[int, int],
) -> dict[str, Any]:
    """One deploy attempt: long drag bench→tile, then a facing tap.

    Live-proved miss: tap-then-380ms-swipe never consumed DP. The client
    needs one continuous drag and a direction confirm after the drop.
    """

    bx, by = int(bench[0]), int(bench[1])
    tx, ty = int(tile[0]), int(tile[1])
    face_x = min(1260, tx + 72) if tx < 1100 else max(20, tx - 72)
    return {
        "kind": "drag-then-face",
        "from": [bx, by],
        "to": [tx, ty],
        "duration_ms": 850,
        "face": [face_x, ty],
        "face_after_land": True,
    }


_SAMI_MARKS = ("银凇", "银淞", "JOKLUM", "JCKLUM", "JQKLUM", "SAMI", "萨米")
_WRONG_THEME_MARKS = (
    "水月",
    "CAERULA",
    "浪静风平",
    "波涛迭起",
    "傀影",
    "萨卡兹",
    "楚卡兹",
    "蔗卡兹",
    "无终奇语",
    "魂灵",
    "巫仪",
    "INSTORY",
    "界园",
    "黑流",
    "生息演算",
    "MIZUKI",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_page(visible_text: Sequence[str]) -> str:
    labels = [str(item).strip() for item in visible_text if str(item).strip()]
    blob = "\n".join(labels)
    label_set = set(labels)
    for page, hints in _PAGE_HINTS:
        if any(hint in blob for hint in hints):
            return page
    if "结局" in label_set:
        return "settlement"
    if "终端" in label_set and label_set & _HOME_PAIR:
        return "home"
    return ISScreenKind.UNKNOWN.value


def match_ocr_target(items: Sequence[Mapping[str, Any]], label: str) -> Mapping[str, Any] | None:
    """Pick the OCR box to tap. Exact first, then containment. Never invent a box."""

    needle = str(label or "").strip()
    if not needle or needle in {"wait", "pause", "deploy", "skill", "retreat"}:
        return None
    exact: list[Mapping[str, Any]] = []
    partial: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if text == needle:
            exact.append(item)
        elif needle in text or text in needle:
            partial.append(item)
    return (exact or partial or [None])[0]


def _item_center(item: Mapping[str, Any]) -> tuple[int, int] | None:
    center = item.get("center") or []
    if len(center) < 2:
        return None
    return int(center[0]), int(center[1])


def pick_sami_theme_enter(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """进入主题 under the Sami strip icon.

    Live 1280x720: three footer 进入主题 sit at y≈643 under 萨米 / 萨卡兹 /
    界园. The Sami one is the leftmost ([284,643] when Sami is [114,574]).
    Mid/right footer buttons open 无终奇语 or 界园. [1080,520] hits 界园.
    """

    sami_x: int | None = None
    enters: list[tuple[int, Mapping[str, Any]]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "")
        center = _item_center(item)
        if center is None:
            continue
        if center[1] >= 550 and any(
            mark in text for mark in ("银凇", "银淞", "JOKLUM", "JCKLUM", "探索者的银")
        ):
            sami_x = center[0]
        if "进入主题" in text:
            enters.append((center[0], item))
    if enters and sami_x is not None:
        enters.sort(key=lambda row: abs(row[0] - sami_x))
        return dict(enters[0][1])
    if enters:
        enters.sort(key=lambda row: row[0])
        return dict(enters[0][1])
    return {"text": "enter-sami-footer", "center": [284, 643]}


def pick_sami_theme_card(items: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Sami icon in the bottom strip only.

    Mid-card OCR at x≈860,y≈574 on a 水月/黑流 page is a false 银淞 and
    closes the browser to terminal. Accept only the left strip band.
    """

    for item in items:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "")
        center = _item_center(item)
        if center is None:
            continue
        if center[1] < 550 or center[0] >= 600:
            continue
        if any(mark in text for mark in ("银凇", "银淞", "JOKLUM", "JCKLUM", "探索者的银")):
            return dict(item)
    return None


_ROUTE_TYPE_TOKENS = ("COMBAT", "EMERGENCY", "LOST", "FOUND", "作战", "紧急", "不期", "遭遇", "安全")


def pick_tap_box(
    items: Sequence[Mapping[str, Any]],
    label: str,
    *,
    page: str = "",
) -> Mapping[str, Any] | None:
    """Prefer a map-interior control. Skip HUD and a horizontal type-legend row."""

    boxes: list[tuple[str, int, int, Mapping[str, Any]]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        center = item.get("center") or []
        if not text or len(center) < 2:
            continue
        boxes.append((text, int(center[0]), int(center[1]), item))
    scored: list[tuple[int, int, Mapping[str, Any]]] = []
    needle = str(label or "").strip()
    for text, x, y, item in boxes:
        if y < 90 or y > 650 or x < 80 or x > 1800:
            continue
        score = 0
        if page == "route" and any(token in text for token in ("出发前往", "开始行动", "进入")):
            score = 5
        elif needle and text == needle and x >= 80:
            score = 4
        elif needle and (needle in text or text in needle):
            score = 3
        elif page == "route" and any(
            token in text.upper() or token in text for token in ("作战", "COMBAT", "EMERGENCY")
        ):
            score = 2
        if score == 0:
            continue
        # Reachable IS nodes sit on the current branch, usually left-of-center.
        dist = (x - 520) ** 2 + (y - 320) ** 2
        scored.append((score, -dist, item))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return scored[0][2] if scored else match_ocr_target(items, label)


def _visible_choice_actions(page: str, texts: Sequence[str]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in texts:
        label = str(raw).strip()
        if (
            not label
            or label in seen
            or label in _SKIP_TAP_LABELS
            or label.isdigit()
            or len(label) < 2
            or any(label.startswith(prefix) for prefix in ("目标生命", "指挥等级", "本次作战", "完美的战", "成功通过", "完成本层", "经过此处"))
        ):
            continue
        if _RATIO.match(label.replace(" ", "")) or label.startswith(("/", "D", "！", "@")):
            continue
        seen.add(label)
        if page == "recruit":
            if label in {"取消", "否"}:
                kind = "recruit.cancel"
            elif label in {"招募", "确认", "进阶", "确认招募"}:
                kind = "recruit.confirm"
            else:
                kind = "recruit.choose"
        elif page == "entry":
            kind = "is.enter"
        elif page == "route":
            kind = "route.select_visible_node"
        elif page == "event":
            kind = "event.choose_option"
        elif page == "shop":
            kind = "shop.buy"
        elif page == "squad":
            kind = "squad.confirm" if "确认" in label else "squad.edit"
        elif page == "battle":
            if label[0].isdigit() or any(
                label.startswith(prefix)
                for prefix in ("剩余", "ISW", "OP", "Load", "N", "@", "X", "度假", "乌萨斯", "共同", "死囚")
            ):
                continue
            if len(label) > 6:
                continue
            kind = "battle.deploy"
        elif page == "settlement":
            if len(label) > 8:
                continue
            kind = (
                "settlement.restart"
                if any(token in label for token in ("开始探索", "继续探索"))
                else "settlement.continue"
            )
        elif page == "reward":
            kind = "reward.confirm"
        elif page == "relic":
            kind = "relic.choose"
        elif page == "terminal":
            if any(token in label for token in ("集成战略", "查看全部主题")):
                kind = "home.integrated-strategies"
            else:
                continue
        elif page == "theme-browser":
            if "进入主题" in label:
                kind = "theme.enter"
            else:
                continue
        elif page == "difficulty":
            if any(token in label for token in ("挑战自然", "选择")):
                kind = "difficulty.choose"
            else:
                continue
        elif page == "opening":
            if label in {"选择分队", "选择支援", "选择招募组合"}:
                continue
            if any(
                token in label
                for token in (
                    "分队",
                    "确定",
                    "确认",
                    "先手",
                    "稳扎",
                    "取长",
                    "随心",
                    "招募券",
                    "重装",
                    "先锋",
                    "近卫",
                    "探索",
                    "术师",
                    "狙击",
                )
            ):
                kind = "opening.choose"
            else:
                continue
        else:
            continue
        actions.append({"action": kind, "label": label})
    return actions


def _hope_from_labels(texts: Sequence[str]) -> int | None:
    labels = [str(item).strip() for item in texts]
    for index, label in enumerate(labels):
        if label == "希望" and index + 1 < len(labels) and labels[index + 1].isdigit():
            return int(labels[index + 1])
    return None


def _cost_from_labels(texts: Sequence[str]) -> int | None:
    """Battle DP is n/m with cap >= 10. Life uses the smaller cap."""

    for raw in texts:
        match = _RATIO.match(str(raw).replace(" ", ""))
        if match and int(match.group(2)) >= 10:
            return int(match.group(1))
    return None


def _slots_from_labels(texts: Sequence[str]) -> int | None:
    """Remaining deploy slots from the battle HUD line."""

    for raw in texts:
        match = _SLOTS.search(str(raw))
        if match:
            return int(match.group(1))
    return None


def _life_from_labels(texts: Sequence[str]) -> int | None:
    labels = [str(item).strip() for item in texts]
    for index, label in enumerate(labels):
        if "目标生命" in label and index + 1 < len(labels):
            match = _RATIO.match(labels[index + 1].replace(" ", ""))
            if match:
                return int(match.group(1))
    for label in labels:
        match = _RATIO.match(label.replace(" ", ""))
        if match and int(match.group(2)) <= 10:
            return int(match.group(1))
    return None


def compact_state(
    *,
    visible_text: Sequence[str] | None = None,
    observation: Mapping[str, Any] | None = None,
    events: Sequence[Mapping[str, Any]] | None = None,
    resources: Mapping[str, Any] | None = None,
    squad: Sequence[Mapping[str, Any]] | None = None,
    battlefield: Mapping[str, Any] | None = None,
    floor: int | None = None,
    difficulty: int = DIFFICULTY_CHALLENGE_NATURE_5,
) -> dict[str, Any]:
    """Build one contestant-facing state. Coordinates never appear."""

    texts: list[str] = []
    if observation:
        texts.extend(str(item) for item in observation.get("visible_text") or [])
        page = str(observation.get("screen") or classify_page(texts))
        legal = [
            {
                "target_id": str(item.get("target_id")),
                "action": str(item.get("action")),
                "label": str(item.get("label")),
            }
            for item in observation.get("legal_targets") or []
            if isinstance(item, Mapping)
        ]
        nodes = [item["label"] for item in legal if item.get("label")]
    else:
        if events:
            for row in events:
                if not isinstance(row, Mapping):
                    continue
                details = row.get("details") if isinstance(row.get("details"), Mapping) else {}
                inner = details.get("details") if isinstance(details.get("details"), Mapping) else {}
                name = inner.get("name") or details.get("name") or row.get("name")
                if name:
                    texts.append(str(name))
                for key in ("task", "text"):
                    value = inner.get(key) or details.get(key)
                    if value:
                        texts.append(str(value))
        if visible_text:
            texts.extend(str(item) for item in visible_text)
        page = classify_page(texts)
        try:
            screen = ISScreenKind(page)
        except ValueError:
            screen = ISScreenKind.UNKNOWN
        legal = [{"action": action.value, "label": action.value} for action in legal_actions_for_screen(screen)]
        if page == "battle":
            bench = _visible_choice_actions("battle", texts)
            legal = bench or [
                {"action": "battle.pause", "label": "pause"},
                {"action": "battle.deploy", "label": "deploy"},
                {"action": "battle.skill", "label": "skill"},
                {"action": "battle.retreat", "label": "retreat"},
            ]
        if page == "settlement":
            legal = [
                {"action": "settlement.continue", "label": "联系中断"},
                {"action": "settlement.continue", "label": "确定"},
                {"action": "settlement.restart", "label": "开始探索"},
                {"action": "settlement.restart", "label": "继续探索"},
            ]
        if page == "entry":
            legal = [
                {"action": ISActionKind.ENTER.value, "label": "开始探索"},
                {"action": ISActionKind.RESUME.value, "label": "继续探索"},
                {"action": ISActionKind.ENTER.value, "label": "常规行动"},
                {"action": ISActionKind.ENTER.value, "label": "探索者的银淞止境"},
                {"action": ISActionKind.ENTER.value, "label": "探索者的银凇止境"},
            ]
        if page == "home":
            legal = [
                {"action": "home.terminal", "label": "终端"},
                {"action": "home.squad", "label": "编队"},
            ]
        if page == "terminal":
            legal = [
                {"action": "home.integrated-strategies", "label": "集成战略"},
            ]
        if page == "theme-browser":
            legal = [
                {"action": "theme.enter", "label": "进入主题"},
                {"action": "theme.scroll-older", "label": "scroll-older-themes"},
            ]
        if page == "difficulty":
            legal = [
                {"action": "difficulty.set", "label": "挑战自然"},
                {"action": "difficulty.confirm", "label": "选择"},
                {"action": "nav.back", "label": "back"},
            ]
        if page == "opening":
            if any("初始招募" in str(item) or "OPERATOR" in str(item) for item in texts):
                legal = [
                    {"action": "opening.open-ticket", "label": "重装招募"},
                    {"action": "opening.open-ticket", "label": "狙击招募"},
                ]
            else:
                legal = [
                    {"action": "opening.choose-squad", "label": "指挥分队"},
                    {"action": "opening.choose-squad", "label": "后勤分队"},
                    {"action": "opening.confirm", "label": "确定"},
                ]
        visible_choices = _visible_choice_actions(page, texts)
        if visible_choices:
            legal = visible_choices
        if page == "battle":
            known = []
            for member in squad or []:
                if isinstance(member, Mapping):
                    name = str(member.get("name") or member.get("label") or "").strip()
                else:
                    name = str(member).strip()
                if name:
                    known.append(name)
            have = {str(item.get("label") or "") for item in legal}
            extras = [
                {"action": "battle.deploy", "label": name}
                for name in known
                if name not in have
            ]
            if extras:
                legal = extras + [
                    item
                    for item in legal
                    if item.get("label") not in {"pause", "deploy", "skill", "retreat"}
                ]
        nodes = [item["label"] for item in legal]
    if not legal:
        legal = [{"action": "wait.reobserve", "label": "wait"}]
        nodes = ["wait"]
    merged_resources = dict(resources or {})
    hope = _hope_from_labels(texts)
    if hope is not None and "hope" not in merged_resources:
        merged_resources["hope"] = hope
    life = _life_from_labels(texts)
    if life is not None and "life" not in merged_resources:
        merged_resources["life"] = life
    cost = _cost_from_labels(texts)
    if cost is not None and "cost" not in merged_resources:
        merged_resources["cost"] = cost
    return {
        "schema": SCHEMA_STATE,
        "page": page,
        "floor": floor,
        "difficulty": int(difficulty),
        "visible_nodes": nodes,
        "resources": merged_resources,
        "squad": list(squad or []),
        "battlefield": dict(battlefield or {}),
        "legal_actions": legal,
        "visible_text": list(dict.fromkeys(texts)),
    }


def is_gameplay_attempt(record: Mapping[str, Any]) -> bool:
    """StartUp / title / connect failures are not training attempts."""

    kind = str(record.get("kind") or record.get("status") or "")
    if kind in NON_ATTEMPT_KINDS:
        return False
    error = str(record.get("error") or "")
    if "StartUp" in error or "title" in error.lower() or "maa_connect" in kind:
        return False
    if record.get("settlement") or record.get("game_pass") is not None:
        return True
    if record.get("stages"):
        return True
    return kind in {"attempt_end", "natural_end", "clear"}


def finish_and_restart(
    *,
    facility_call: Callable[[str, Any, Mapping[str, Any]], Mapping[str, Any]],
    root: Any = None,
) -> dict[str, Any]:
    """Deterministic settlement → next exploration. Stops at first real choice."""

    completed: list[str] = []
    for _step in range(12):
        observed = facility_call("screen-options", root, {"force_fresh": True})
        options = observed.get("options") if isinstance(observed, Mapping) else None
        if not isinstance(options, list):
            raise RuntimeError("finish-and-restart could not read screen options")
        if not options:
            screen = facility_call("screen-state", root, {})
            choices = screen.get("choices") if isinstance(screen, Mapping) else None
            texts = [str(item).strip() for item in choices or [] if str(item).strip()]
            if not ({"END", "扩充结束"} & set(texts)):
                return {
                    "ok": True,
                    "transitions": completed,
                    "model_roundtrips": 0,
                    "stopped_at": "unknown-or-choice",
                }
            result = facility_call("back", root, {})
            if not isinstance(result, Mapping) or result.get("ok") is not True:
                raise RuntimeError("finish-and-restart could not dismiss ending summary")
            completed.append("dismiss-ending-summary")
            continue
        classified = [
            (item, str(item.get("option_kind") or "").strip())
            for item in options
            if isinstance(item, Mapping)
        ]
        if len(classified) != len(options) or any(not kind for _item, kind in classified):
            return {
                "ok": True,
                "transitions": completed,
                "model_roundtrips": 0,
                "stopped_at": "choice",
            }
        automatic = [
            (item, kind)
            for item, kind in classified
            if kind in AUTO_LIFECYCLE_KINDS
        ]
        if len(automatic) != 1 or any(
            kind not in AUTO_LIFECYCLE_KINDS | INERT_LIFECYCLE_KINDS
            for _item, kind in classified
        ):
            return {
                "ok": True,
                "transitions": completed,
                "model_roundtrips": 0,
                "stopped_at": "choice",
            }
        item, kind = automatic[0]
        if kind == "start-new-exploration":
            result = facility_call("prepare-opening", root, {})
        else:
            choice_id = str(item.get("choice_id") or "").strip()
            if not choice_id:
                raise RuntimeError("finish-and-restart option has no choice_id")
            result = facility_call("screen-choose", root, {"choice_id": choice_id})
        if not isinstance(result, Mapping) or result.get("ok") is not True:
            raise RuntimeError(f"finish-and-restart action failed: {kind}")
        completed.append(kind)
        if kind == "start-new-exploration":
            return {
                "ok": True,
                "transitions": completed,
                "model_roundtrips": 0,
                "stopped_at": "first-real-choice",
            }
    raise RuntimeError("finish-and-restart exceeded its deterministic step budget")


def decide_from_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Author one semantic choice from compact state. Does not touch the device."""

    page = str(state.get("page") or "")
    actions = list(state.get("legal_actions") or [])
    resources = state.get("resources") if isinstance(state.get("resources"), Mapping) else {}
    life_known = "life" in resources
    life = int(resources.get("life") or 0)
    chosen: Mapping[str, Any] | None = None
    reason = "first-legal"

    def _label(item: Mapping[str, Any]) -> str:
        return str(item.get("label") or item.get("action") or "")

    blob = "".join(str(item) for item in (state.get("visible_text") or []))
    sami_visible = any(mark in blob for mark in _SAMI_MARKS)
    foreign_visible = any(mark in blob for mark in _WRONG_THEME_MARKS)
    if page in {"entry", "difficulty", "unknown"} and foreign_visible and not sami_visible:
        chosen, reason = {"action": "nav.back", "label": "back"}, "leave-wrong-theme"
    for token, start_reason in (
        ("开始探索", "start-new-run"),
        ("常规行动", "start-new-run"),
        ("查看全部主题", "open-theme-list"),
        ("集成战略", "enter-is-tab"),
    ):
        if chosen is not None:
            break
        if token == "常规行动" and foreign_visible and not sami_visible:
            continue
        if token == "开始探索" and page in {"difficulty", "opening", "route", "squad", "battle"}:
            continue
        start_new = [item for item in actions if token in _label(item)]
        if start_new:
            chosen, reason = start_new[0], start_reason
    if chosen is not None:
        pass
    elif page == "theme-browser":
        enter = [item for item in actions if "进入主题" in _label(item)]
        sami_focused = any(
            token in blob for token in ("物资准备齐整", "向北方进", "穿过冰原")
        )
        if enter and sami_focused:
            chosen, reason = enter[0], "enter-sami-theme"
        elif sami_visible and not foreign_visible:
            chosen, reason = (enter[0] if enter else {"action": "theme.enter", "label": "进入主题"}), "enter-sami-theme"
        else:
            # Mixed strip OCR invents 银淞 at the featured-card mid-right and
            # that tap closes to terminal. Swipe until 物资准备齐整 is up.
            chosen, reason = {"action": "theme.scroll-older", "label": "scroll-older-themes"}, "scroll-to-sami"
    elif page == "difficulty":
        challenge = [item for item in actions if "挑战自然" in _label(item)]
        confirm = [item for item in actions if _label(item) == "选择"]
        five_stack = any(
            mark in blob
            for mark in ("精英敌人的攻击", "非完美作战", "所有敌人的攻击", "高寒")
        )
        sami_diff = sami_visible or "挑战自然" in blob or five_stack
        if confirm and sami_diff:
            chosen, reason = confirm[0], "confirm-difficulty-5"
        elif challenge and sami_diff:
            chosen, reason = challenge[0], "set-difficulty-5"
        elif foreign_visible and not sami_diff:
            chosen, reason = {"action": "nav.back", "label": "back"}, "leave-wrong-theme"
        elif confirm:
            chosen, reason = confirm[0], "confirm-difficulty-5"
        elif challenge:
            chosen, reason = challenge[0], "set-difficulty-5"
        else:
            chosen, reason = {"action": "nav.back", "label": "back"}, "leave-wrong-theme"
    elif page == "opening":
        confirm = [item for item in actions if _label(item) in {"确定", "确认"}]
        command = [item for item in actions if "指挥分队" in _label(item)]
        logistics = [item for item in actions if "后勤分队" in _label(item)]
        cluster = [item for item in actions if "集群分队" in _label(item)]
        stable = [item for item in actions if "稳扎稳打" in _label(item)]
        defender_tickets = [item for item in actions if "重装" in _label(item)]
        if "选择招募组合" in blob or any("招募券" in _label(item) for item in actions):
            if defender_tickets:
                chosen, reason = defender_tickets[0], "opening-blocker-tickets"
        defender_ticket = [item for item in actions if "重装招募" in _label(item) or "招募一个重装" in _label(item)]
        if chosen is None and ("初始招募" in blob or "OPERATOR" in blob):
            have_blocker = any(name in blob for name in ("塞雷娅", "斑点", "泥岩", "星熊", "蛇屠箱"))
            caster = [item for item in actions if "术师" in _label(item)]
            sniper = [item for item in actions if "狙击" in _label(item)]
            explore = [item for item in actions if "探索" == _label(item) or _label(item).endswith("探索")]
            have_caster = any(name in blob for name in ("烛煌", "艾雅法拉", "伊芙利特"))
            if not have_blocker and not have_caster:
                chosen, reason = (
                    defender_ticket[0] if defender_ticket else {"action": "opening.open-ticket", "label": "重装招募"}
                ), "opening-recruit-blocker-first"
            elif caster and not have_caster:
                chosen, reason = caster[0], "opening-recruit-caster"
            elif sniper:
                chosen, reason = sniper[0], "opening-recruit-sniper"
            elif explore:
                chosen, reason = explore[0], "leave-opening-to-route"
            else:
                chosen, reason = {"action": "opening.leave", "label": "探索"}, "leave-opening-to-route"
        if chosen is None and stable:
            chosen, reason = stable[0], "opening-stable-support"
        elif chosen is None and command:
            chosen, reason = command[0], "opening-keep-life"
        elif chosen is None and logistics:
            chosen, reason = logistics[0], "opening-keep-recruit"
        elif chosen is None and cluster:
            chosen, reason = cluster[0], "opening-keep-cost"
        elif chosen is None and confirm:
            chosen, reason = confirm[0], "confirm-opening"
        elif chosen is None:
            chosen, reason = (actions[0] if actions else None, "opening-first")
    elif page == "settlement":
        skip = [item for item in actions if _label(item) == "SKIP"]
        interrupt = [item for item in actions if "联系中断" in _label(item)]
        restart = [item for item in actions if any(token in _label(item) for token in ("开始探索", "继续探索"))]
        poem = [item for item in actions if "风雪蔽目" in _label(item)]
        grounded = any("驻足" in _label(item) for item in actions)
        if skip:
            chosen, reason = skip[0], "skip-settlement-recap"
        elif interrupt:
            chosen, reason = interrupt[0], "dismiss-settlement"
        elif restart or "开始探索" in blob:
            chosen, reason = (
                restart[0] if restart else {"action": "settlement.restart", "label": "开始探索"}
            ), "start-new-run"
        elif "通过层数" in blob or "分数合计" in blob:
            # Live-proved: BACK from the stats page lands on 开始探索 lobby.
            chosen, reason = {"action": "nav.back", "label": "back"}, "leave-settlement-to-lobby"
        elif poem and grounded:
            # Poem BACK is a no-op. Dismiss to stats, then BACK next tick.
            chosen, reason = poem[0], "dismiss-ending-poem"
        else:
            chosen, reason = {"action": "nav.back", "label": "back"}, "leave-settlement-to-lobby"
    elif page == ISScreenKind.SQUAD.value:
        start = [item for item in actions if "开始行动" in _label(item)]
        confirm = [item for item in actions if _label(item) in {"确认", "编队确认"}]
        if start:
            chosen, reason = start[0], "start-operation"
        elif confirm:
            chosen, reason = confirm[0], "confirm-squad"
        else:
            chosen, reason = (actions[0] if actions else None, "squad-first")
    elif page == "relic":
        leave_ok = [item for item in actions if "确认离开" in _label(item)]
        accept = [item for item in actions if _label(item) == "收下"]
        skip = [item for item in actions if "不要了" in _label(item) or "走了" in _label(item)]
        choose = [item for item in actions if _label(item) == "选择"]
        if leave_ok:
            chosen, reason = leave_ok[0], "cancel-leave-cipher"
        elif accept:
            chosen, reason = accept[0], "accept-cipher"
        elif choose:
            chosen, reason = choose[0], "take-cipher"
        elif skip:
            chosen, reason = skip[0], "skip-cipher"
        else:
            chosen, reason = (actions[0] if actions else None, "relic-first")
    elif page == ISScreenKind.ROUTE.value:
        enter = [item for item in actions if any(token in _label(item) for token in ("出发前往", "开始行动", "开始作战", "进入", "朗诵"))]
        combat = [
            item
            for item in actions
            if any(token in _label(item).upper() for token in ("作战", "紧急", "COMBAT", "EMERGENCY"))
            and "本次" not in _label(item)
        ]
        safe = [item for item in actions if any(token in _label(item) for token in ("安全", "希望", "休息", "LOST"))]
        if enter:
            chosen, reason = enter[0], "enter-selected-node"
        elif (life >= 2 or not life_known) and combat:
            emergency = [
                item
                for item in combat
                if "EMERGENCY" in _label(item).upper() or "紧急" in _label(item)
            ]
            regular = [
                item
                for item in combat
                if item not in emergency
            ]
            exact = [item for item in regular if _label(item) in {"作战"}]
            # Three-op opening squads leaked 死囚之夜 / 度假村冤魂. Take a
            # normal combat node first; emergency only if nothing else.
            chosen, reason = (exact or regular or emergency)[0], "life-ok-take-combat"
        elif safe:
            chosen, reason = safe[0], "low-life-take-safe"
        elif actions:
            chosen, reason = actions[0], "route-first-visible"
    elif page == ISScreenKind.RECRUIT.value:
        if "确认要放弃" in blob:
            cancel = [item for item in actions if any(token in _label(item) for token in ("取消", "否"))]
            chosen, reason = (
                cancel[0] if cancel else {"action": "nav.back", "label": "取消"}
            ), "keep-recruit-offer"
        else:
            blockers = [
                item
                for item in actions
                if any(token in _label(item) for token in ("先锋", "重装", "挡"))
                or _label(item) in _BLOCKER_NAMES
            ]
            hard_confirm = [item for item in actions if _label(item) == "确认招募"]
            confirm = [item for item in actions if _label(item) in {"确认招募", "招募", "确认"}]
            prefer = sorted(
                [
                    item
                    for item in actions
                    if any(name == _label(item) or name in _label(item) for name in _RECRUIT_PRIORITY)
                ],
                key=lambda item: next(
                    (
                        index
                        for index, name in enumerate(_RECRUIT_PRIORITY)
                        if name == _label(item) or name in _label(item)
                    ),
                    99,
                ),
            )
            if "希望不足" in blob:
                costly = ("艾雅法拉", "烛煌", "伊芙利特", "塞雷娅", "星熊", "泥岩", "假日威龙陈")
                prefer = [
                    item
                    for item in prefer
                    if not any(name == _label(item) or name in _label(item) for name in costly)
                ]
                cheap = [
                    item
                    for item in actions
                    if any(name == _label(item) or name in _label(item) for name in _CHEAP_RECRUITS)
                ]
                cheap = sorted(
                    cheap,
                    key=lambda item: next(
                        (
                            index
                            for index, name in enumerate(_CHEAP_RECRUITS)
                            if name == _label(item) or name in _label(item)
                        ),
                        99,
                    ),
                )
                if cheap:
                    prefer = cheap + prefer
            detailing = any(token in blob for token in ("攻击范围", "当前", "最高"))
            others = [
                item
                for item in actions
                if _label(item) not in _RECRUIT_NON_OPERATORS
                and "招募" not in _label(item)
                and "助战" not in _label(item)
                and len(_label(item)) >= 2
            ]
            if prefer and not detailing:
                chosen, reason = prefer[0], "pick-priority-operator"
            elif hard_confirm and detailing:
                chosen, reason = hard_confirm[0], "confirm-recruit"
            elif prefer:
                chosen, reason = prefer[0], "pick-priority-operator"
            elif others and not detailing:
                chosen, reason = others[0], "pick-visible-operator"
            elif blockers:
                chosen, reason = blockers[0], "fill-blocker"
            elif hard_confirm and detailing:
                chosen, reason = hard_confirm[0], "confirm-recruit"
            elif confirm and detailing:
                chosen, reason = confirm[0], "confirm-recruit"
            else:
                chosen, reason = (others[0] if others else actions[0] if actions else None, "recruit-first")
    elif page == "battle":
        named = sorted(
            [
                item
                for item in actions
                if _label(item) in _BLOCKER_NAMES
                or any(
                    token in _label(item)
                    for token in (
                        "斑点",
                        "芙蓉",
                        "德克萨斯",
                        "塞雷娅",
                        "烛煌",
                        "克洛丝",
                        "炎熔",
                        "空爆",
                        "安德切尔",
                        "巡林者",
                    )
                )
            ],
            key=lambda item: next(
                (index for index, name in enumerate(_CHEAP_OPEN) if name in _label(item)),
                99,
            ),
        )
        deploys = [
            item
            for item in actions
            if "deploy" in str(item.get("action") or "")
            and _label(item) not in {"deploy", "pause", "skill", "retreat"}
        ]
        cost = resources.get("cost")
        cost_known = cost is not None
        need = _cheapest_needed(named or deploys)
        too_poor = (not cost_known) or int(cost) < need
        if named and too_poor:
            chosen, reason = (
                {"action": "battle.wait-cost", "label": "wait"},
                "wait-for-cost",
            )
        elif named:
            chosen, reason = named[0], "open-with-blocker"
        elif deploys and too_poor:
            chosen, reason = (
                {"action": "battle.wait-cost", "label": "wait"},
                "wait-for-cost",
            )
        elif deploys:
            chosen, reason = deploys[0], "open-with-deploy"
        else:
            chosen, reason = {"action": "battle.wait-cost", "label": "wait"}, "wait-for-bench"
    elif actions:
        chosen, reason = actions[0], "first-legal"

    if chosen is None:
        raise ValueError("decide_from_state has no legal action")
    action = dict(chosen)
    if reason in {"open-with-blocker", "open-with-deploy"}:
        action["tiles"] = [
            list(point) for point in deploy_tile_candidates(_label(action))
        ]
    return {
        "schema": SCHEMA_DECISION,
        "at": _now(),
        "page": page,
        "reason": reason,
        "action": action,
        "source": "ai-play-surface",
        "executed": False,
    }


def persist_decision(path: Path, decision: Mapping[str, Any]) -> Mapping[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(dict(decision), ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
    return decision


def forecast_battle_snapshot(
    model: StageWorldModel,
    plan: Mapping[str, Any],
    profiles_by_query: Mapping[str, OperatorCombatProfile],
    *,
    horizon_seconds: float = 30.0,
    scenario: str = "most-likely",
) -> dict[str, Any]:
    """Predict leak / block / cost / wave risk. Autopilot JSON is not a plan."""

    if _looks_like_maa_autopilot(plan):
        raise ValueError("forecast refuses MAA autopilot/copilot JSON as a battle plan")
    receipt = forecast_plan(
        model,
        plan,
        profiles_by_query,
        horizon_seconds=horizon_seconds,
        scenario=scenario,
    )
    result = receipt["result"]
    flying = sum(
        1
        for event in model.spawn_events
        if event.get("time_seconds", event.get("time", 0)) <= horizon_seconds
        and _spawn_is_flying(model, event)
    )
    deployed = result.get("deployed") or []
    return {
        "ok": True,
        "leak": result.get("leaks"),
        "block": len(deployed),
        "cost": result.get("cost"),
        "life_points": result.get("life_points"),
        "kills": result.get("kills"),
        "wave_risk": flying,
        "horizon_seconds": horizon_seconds,
        "source": "stage-simulation-forecast",
        "plan_name": plan.get("name"),
        "receipt": receipt,
    }


def _looks_like_maa_autopilot(plan: Mapping[str, Any]) -> bool:
    if plan.get("source") in {"maa_autopilot", "maa_copilot", "textbook"}:
        return True
    if "deploy_plan" in plan and "replacement_home" in plan:
        return True
    path = str(plan.get("path") or plan.get("file") or "")
    return "autopilot" in path.replace("\\", "/").lower() and path.endswith(".json")


def _spawn_is_flying(model: StageWorldModel, event: Mapping[str, Any]) -> bool:
    route_index = event.get("route_index", event.get("route"))
    if route_index is None:
        return False
    for route in model.routes:
        if not isinstance(route, Mapping):
            continue
        indices = route.get("source_indices") or []
        if route_index in indices:
            return route.get("movement_layer") == "flying"
    return False


def launch_observe(visible_text: Sequence[str], **kwargs: Any) -> dict[str, Any]:
    """Play-surface entry used by the launcher and by live follow-up."""

    return compact_state(visible_text=visible_text, **kwargs)
