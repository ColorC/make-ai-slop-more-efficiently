# [OMNI] origin=grok created_by=grok intent="v5 AI-owned clear: freedom surface decides, MAA semantic hand acts"
"""AI-owned Sami clear runtime.

Decisions are authored from compact semantic state *before* any device call.
The hand is MAA tile/name operations only. Pixel coordinates, MAA autopilot
JSON, and unpaused battle thinking are rejected.

Recruit and job selection go through ``contestant_surface``. Combat jobs use
the ``battle_plan_queue`` step schema. Conservative placement uses Tile-Pos
grids, never handwritten screen points.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .battle_plan_queue import BattleFacts, BattlePlanQueue, parse_battle_plan
from .contestant_surface import (
    PLUGIN_SCHEMA,
    ContestantPlugin,
    RunContext,
    ScoreProfile,
    evaluate_surface,
    load_sami_default_table,
)
from .play_surface import _looks_like_maa_autopilot, forecast_battle_snapshot
from .stage_simulation_bridge import compile_simulator_bundle
from .stage_world_model import OperatorCombatProfile, StageWorldModel, StageWorldModelError, load_operator_profile
from .tile_grid import StageGrid

__all__ = [
    "DEFAULT_OPENING_PLUGIN",
    "DECISION_SCHEMA",
    "OwnedClearError",
    "OwnedDecision",
    "PauseRequiredError",
    "PixelBannedError",
    "SemanticHands",
    "author_decision",
    "conservative_job",
    "default_score_profile",
    "dispatch_decision",
    "forecast_owned_job",
    "load_first_forecastable_ro3",
    "persist_decision",
    "require_paused_for_think",
    "scan_for_pixels",
    "state_from_observe",
    "pick_named_element",
]

DECISION_SCHEMA = "omnicompany.arknights.owned-clear-decision.v1"
DIFFICULTY_CHALLENGE_NATURE_5 = 5
SKILL_WINDOW_DEALERS = frozenset(
    {
        "维什戴尔",
        "山",
        "棘刺",
        "艾雅法拉",
        "煌",
        "史尔特尔",
        "能天使",
        "泥岩",
        "塞雷娅",
        "号角",
        "铃兰",
        "烛煌",
        "伊芙利特",
        "闪灵",
        "夜莺",
    }
)
PIXEL_ACTION_KEYS = frozenset({"x", "y", "from", "to", "face", "px", "py", "point", "points"})
_FACING = {"left": "Left", "right": "Right", "up": "Up", "down": "Down"}

DEFAULT_OPENING_PLUGIN: dict[str, object] = {
    "schema": PLUGIN_SCHEMA,
    "score_rules": [
        {
            "when": {},
            "boost": {"operators": [name], "offset": 250},
        }
        for name in sorted(SKILL_WINDOW_DEALERS)
    ],
    "job_rules": [
        {"stage": "苔手", "when": {"has_operator": "山"}, "job_id": "taishou-mountain"},
        {"stage": "苔手", "job_id": "taishou-conservative"},
    ],
}


class OwnedClearError(ValueError):
    """A v5 owned-clear contract was violated."""


class PauseRequiredError(OwnedClearError):
    """Battle think/sim was requested while the game was still running."""


class PixelBannedError(OwnedClearError):
    """A decision or action carried operator-chosen screen coordinates."""


class SemanticHands(Protocol):
    def pause(self) -> object: ...
    def deploy(self, operator: str, col: int, row: int, direction: str) -> object: ...
    def use_skill(self, operator: str) -> object: ...
    def retreat(self, operator: str) -> object: ...
    def recruit(self, name: str) -> object: ...
    def confirm_recruit(self) -> object: ...
    def choose_route(self, node_id: str, label: str) -> object: ...
    def start_operation(self) -> object: ...
    def confirm_difficulty(self) -> object: ...
    def start_exploration(self) -> object: ...
    def choose_normal_operation(self) -> object: ...
    def choose_squad(self, name: str) -> object: ...
    def confirm_opening(self) -> object: ...
    def leave_opening(self) -> object: ...


@dataclass(frozen=True, slots=True)
class OwnedDecision:
    kind: str
    reason: str
    maa: Mapping[str, object]
    page: str
    executed: bool = False
    extras: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload = {
            "schema": DECISION_SCHEMA,
            "at": datetime.now(timezone.utc).isoformat(),
            "kind": self.kind,
            "reason": self.reason,
            "page": self.page,
            "maa": dict(self.maa),
            "executed": self.executed,
            "source": "owned-clear-runtime",
        }
        payload.update(self.extras)
        scan_for_pixels(payload)
        return payload


def default_score_profile(*, profile_id: str = "sami-default", author: str = "maa") -> ScoreProfile:
    return ScoreProfile.from_maa_table(
        load_sami_default_table(),
        profile_id=profile_id,
        author=author,
    )


def state_from_observe(observed: Mapping[str, Any]) -> dict[str, Any]:
    """Map a live observe payload to the compact owned-clear state."""

    choices = [str(item) for item in (observed.get("choices") or [])]
    blob = " ".join(choices)
    battle = observed.get("battle") if isinstance(observed.get("battle"), Mapping) else None
    if battle:
        return {
            "page": "battle",
            "difficulty": 5,
            "paused": bool(battle.get("paused") or battle.get("is_paused")),
            "cost": battle.get("cost"),
            "time": battle.get("time"),
            "leaked": int(battle.get("leaked") or battle.get("leaks") or 0),
            "operator_down": bool(battle.get("operator_down")),
            "squad": list(battle.get("squad") or []),
        }
    if any(token in blob for token in ("临时招募", "确认招募", "轻触右侧干员")):
        known = (
            "维什戴尔",
            "山",
            "棘刺",
            "安德切尔",
            "芬",
            "克洛丝",
            "史都华德",
            "焰影苇草",
            "塞雷娅",
            "星熊",
            "泥岩",
            "号角",
            "年",
            "闪灵",
            "夜莺",
            "艾雅法拉",
        )
        visible = [{"name": name, "cost": 0} for name in known if name in blob]
        return {
            "page": "recruit",
            "difficulty": 5,
            "paused": True,
            "visible_recruits": visible or [{"name": "安德切尔", "cost": 0}],
            "confirm_visible": "确认招募" in blob,
        }
    if any(
        token in blob
        for token in (
            "矛头分队",
            "指挥分队",
            "集群分队",
            "后勤分队",
            "选择分队",
            "选择支援",
            "稳扎稳打",
            "初始招募",
            "选择招募组合",
            "招募券",
        )
    ):
        pick = "squad"
        label = "指挥分队"
        if "选择招募组合" in blob or "招募券" in blob:
            pick = "ticket"
            if "重装、术师" in blob or "重装,术师" in blob:
                label = "重装、术师"
            elif "近卫、辅助" in blob:
                label = "近卫、辅助"
            elif "先锋、狙击" in blob:
                label = "先锋、狙击"
            else:
                label = "术师"
        elif "你确定要这么做" in blob:
            pick = "confirm"
        elif any(token in blob for token in ("确定", "确认")) and "选择分队" not in blob:
            pick = "confirm"
        elif "稳扎稳打" in blob:
            pick = "support"
            label = "稳扎稳打"
        elif "探索" in blob and "开始探索" not in blob and "开始探" not in blob:
            pick = "leave"
        return {
            "page": "opening",
            "difficulty": 5,
            "paused": True,
            "opening_pick": pick,
            "opening_label": label,
            "confirm_visible": pick == "confirm",
            "leave_visible": pick == "leave",
        }
    if "常规行动" in blob and any(token in blob for token in ("银凇", "银淞", "JOKLUM")) and "开始行动" not in blob:
        return {"page": "theme", "difficulty": 5, "paused": True}
    if any(token in blob for token in ("开始探索", "开始探")) and any(
        token in blob for token in ("银凇", "银淞", "JOKLUM")
    ):
        return {"page": "entry", "difficulty": 5, "paused": True}
    if any(token in blob for token in ("出发前往", "敌方情报")):
        nodes = []
        for label in ("紧急作战", "作战", "不期而遇", "诡意行商", "安全的角落"):
            if label in blob:
                nodes.append({"node_id": label, "label": label})
        return {
            "page": "route",
            "difficulty": 5,
            "paused": True,
            "visible_nodes": nodes or [{"node_id": "作战", "label": "作战"}],
        }
    if "开始行动" in blob:
        return {"page": "squad", "difficulty": 5, "paused": True}
    if any(token in blob for token in ("挑战自然", "浪静风平", "得分效率")):
        return {"page": "difficulty", "difficulty": 5, "paused": True}
    return {"page": "difficulty", "difficulty": 5, "paused": True, "visible_text": choices}


def pick_named_element(elements: Sequence[Mapping[str, Any]], name: str) -> Mapping[str, Any]:
    """Choose the operator card, not a tooltip or a tiny roster icon."""

    if not name.strip():
        raise OwnedClearError("name must be non-blank")
    exact = [
        item
        for item in elements
        if str(item.get("text") or "").strip() == name
    ]
    if exact:
        return max(exact, key=lambda item: (item.get("center") or [0, 0])[0])
    short = [
        item
        for item in elements
        if name in str(item.get("text") or "") and len(str(item.get("text") or "")) <= len(name) + 2
    ]
    if len(short) == 1:
        return short[0]
    raise OwnedClearError(f"no unique card for {name!r}")


def require_paused_for_think(state: Mapping[str, Any], *, for_sim: bool = False) -> None:
    page = str(state.get("page") or "")
    if page != "battle":
        return
    if state.get("paused") is True:
        return
    what = "sim" if for_sim else "think"
    raise PauseRequiredError(f"{what} refused: battle is not paused")


def scan_for_pixels(payload: object, *, _path: str = "$") -> None:
    if isinstance(payload, Mapping):
        keys = set(payload)
        if keys & PIXEL_ACTION_KEYS:
            raise PixelBannedError(f"pixel keys at {_path}: {sorted(keys & PIXEL_ACTION_KEYS)}")
        if "tiles" in payload:
            raise PixelBannedError(f"pixel tile list at {_path}.tiles")
        for key, value in payload.items():
            scan_for_pixels(value, _path=f"{_path}.{key}")
        return
    if isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload):
            scan_for_pixels(item, _path=f"{_path}[{index}]")


def conservative_job(
    grid: StageGrid,
    operators: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Blue-door block + highland DPS. Tile-Pos only; no screen points."""

    ends = [(col, row) for col, row, tile in grid.iter_tiles() if tile.is_end]
    melee = [(col, row) for col, row, tile in grid.iter_tiles() if tile.deployable_melee]
    ranged = [(col, row) for col, row, tile in grid.iter_tiles() if tile.deployable_ranged]
    if not melee and not ranged:
        raise OwnedClearError(f"stage {grid.code} has no deployable tiles")
    anchor = ends[0] if ends else (melee[0] if melee else ranged[0])

    def _near(candidates: list[tuple[int, int]]) -> tuple[int, int]:
        if not candidates:
            raise OwnedClearError("no tiles for this deployment class")
        return min(candidates, key=lambda item: abs(item[0] - anchor[0]) + abs(item[1] - anchor[1]))

    facing = "left" if any(col > anchor[0] for col, _row, tile in grid.iter_tiles() if tile.is_start) else "right"
    steps: list[dict[str, Any]] = []
    used: set[tuple[int, int]] = set()
    at_seconds = 8
    for index, operator in enumerate(operators):
        name = str(operator.get("name") or "").strip()
        if not name:
            continue
        melee_unit = str(operator.get("position") or "MELEE").upper() == "MELEE"
        pool = melee if melee_unit else ranged
        if not pool:
            pool = melee or ranged
        tile = _near([item for item in pool if item not in used] or pool)
        used.add(tile)
        steps.append(
            {
                "operator": name,
                "col": tile[0],
                "row": tile[1],
                "facing": facing,
                "at_seconds": at_seconds,
            }
        )
        at_seconds += 6
    plan = {"deployments": steps}
    scan_for_pixels(plan)
    return plan


def author_decision(
    state: Mapping[str, Any],
    *,
    profile: ScoreProfile,
    plugin: ContestantPlugin | Mapping[str, object] | None = None,
    script: str | None = None,
    job: Mapping[str, Any] | None = None,
    grid: StageGrid | None = None,
) -> OwnedDecision:
    """Write one semantic decision. Does not touch the device."""

    require_paused_for_think(state)
    page = str(state.get("page") or "unknown")
    if int(state.get("difficulty") or 0) not in {0, DIFFICULTY_CHALLENGE_NATURE_5}:
        raise OwnedClearError("owned clear only accepts difficulty 5 (or unset before start)")
    if job is not None and _looks_like_maa_autopilot(job):
        raise OwnedClearError("MAA autopilot/copilot JSON cannot be the live job")

    if page == "battle" and (int(state.get("leaked") or 0) > 0 or state.get("operator_down") is True):
        return OwnedDecision("pause", "leak-or-down-hook", {"op": "pause"}, page)

    if page == "opening":
        pick = str(state.get("opening_pick") or "")
        if pick == "confirm" or state.get("confirm_visible"):
            return OwnedDecision("opening", "confirm-command-squad", {"op": "confirm_opening"}, page)
        if pick == "leave" or state.get("leave_visible"):
            return OwnedDecision("opening", "leave-opening-to-route", {"op": "leave_opening"}, page)
        if pick == "support":
            name = str(state.get("opening_label") or "稳扎稳打")
            return OwnedDecision("opening", "opening-stable-support", {"op": "choose_squad", "name": name}, page)
        if pick == "ticket":
            name = str(state.get("opening_label") or "重装、术师")
            return OwnedDecision("opening", "opening-blocker-tickets", {"op": "choose_squad", "name": name}, page)
        return OwnedDecision(
            "opening",
            "opening-command-squad",
            {"op": "choose_squad", "name": str(state.get("opening_label") or "指挥分队")},
            page,
        )
    if page == "entry":
        return OwnedDecision("start", "start-new-run", {"op": "start_exploration"}, page)
    if page == "theme":
        return OwnedDecision("start", "enter-sami-normal-operation", {"op": "choose_normal_operation"}, page)
    if page == "difficulty":
        return OwnedDecision("difficulty", "confirm-challenge-nature-5", {"op": "confirm_difficulty"}, page)

    if page == "recruit":
        if state.get("confirm_visible") and state.get("picked"):
            return OwnedDecision("recruit", "confirm-recruit", {"op": "confirm_recruit"}, page)
        return _recruit_decision(state, profile=profile, plugin=plugin, script=script)

    if page == "route":
        nodes = [item for item in (state.get("visible_nodes") or []) if isinstance(item, Mapping)]
        combat = [item for item in nodes if any(token in str(item.get("label") or "") for token in ("作战", "紧急", "险路"))]
        chosen = combat[0] if combat else (nodes[0] if nodes else None)
        if chosen is None:
            raise OwnedClearError("route page has no visible nodes")
        return OwnedDecision(
            "route",
            "prefer-combat-node",
            {"op": "choose_route", "node_id": str(chosen.get("node_id") or ""), "label": str(chosen.get("label") or "")},
            page,
        )

    if page == "squad":
        return OwnedDecision("start", "start-operation", {"op": "start_operation"}, page)

    if page == "battle":
        return _battle_decision(state, profile=profile, plugin=plugin, script=script, job=job, grid=grid)

    raise OwnedClearError(f"no owned decision for page {page!r}")


def dispatch_decision(decision: OwnedDecision, hands: SemanticHands, *, ledger: Path | None = None) -> dict[str, Any]:
    """Persist the decision, then execute only the semantic MAA hand."""

    record = persist_decision(ledger, decision) if ledger is not None else decision.to_dict()
    scan_for_pixels(record)
    op = str(decision.maa.get("op") or "")
    result: object
    if op == "pause":
        result = hands.pause()
    elif op == "deploy":
        result = hands.deploy(
            str(decision.maa["operator"]),
            int(decision.maa["col"]),
            int(decision.maa["row"]),
            str(decision.maa["direction"]),
        )
    elif op == "skill":
        result = hands.use_skill(str(decision.maa["operator"]))
    elif op == "retreat":
        result = hands.retreat(str(decision.maa["operator"]))
    elif op == "recruit":
        result = hands.recruit(str(decision.maa["name"]))
    elif op == "confirm_recruit":
        result = hands.confirm_recruit()
    elif op == "choose_route":
        result = hands.choose_route(str(decision.maa.get("node_id") or ""), str(decision.maa.get("label") or ""))
    elif op == "start_operation":
        result = hands.start_operation()
    elif op == "confirm_difficulty":
        result = hands.confirm_difficulty()
    elif op == "start_exploration":
        result = hands.start_exploration()
    elif op == "choose_normal_operation":
        result = hands.choose_normal_operation()
    elif op == "choose_squad":
        result = hands.choose_squad(str(decision.maa.get("name") or "指挥分队"))
    elif op == "confirm_opening":
        result = hands.confirm_opening()
    elif op == "leave_opening":
        result = hands.leave_opening()
    else:
        raise OwnedClearError(f"unknown semantic op: {op!r}")
    return {"ok": True, "decision": record, "receipt": result}


def persist_decision(path: Path, decision: OwnedDecision | Mapping[str, Any]) -> dict[str, Any]:
    payload = decision.to_dict() if isinstance(decision, OwnedDecision) else dict(decision)
    scan_for_pixels(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return payload


def load_first_forecastable_ro3(
    *,
    ro3_database: Path | None = None,
    knowledge_database: Path | None = None,
) -> tuple[StageWorldModel, OperatorCombatProfile, dict[str, Any]]:
    """First deterministic real-ro3 level + owned operator the simulator can compile."""

    import hashlib
    import sqlite3

    root = Path(__file__).resolve().parents[6]
    ro3_path = ro3_database or (
        root / "var/runtime/arknights/is4-worker-sandbox/out/arknights-is4.sqlite3"
    )
    knowledge_path = knowledge_database or (
        root
        / "var/runtime/arknights/lightweight-tournament/shared/knowledge/"
        / "arknights-public-knowledge.v1.sqlite"
    )
    if not ro3_path.is_file() or not knowledge_path.is_file():
        raise OwnedClearError("real ro3 or knowledge database is missing")
    ro3 = sqlite3.connect(f"file:{ro3_path.as_posix()}?mode=ro", uri=True)
    knowledge = sqlite3.connect(f"file:{knowledge_path.as_posix()}?mode=ro", uri=True)
    try:
        operators = knowledge.execute(
            """
            SELECT DISTINCT resolved_operator_id
            FROM account_roster
            WHERE owned=1 AND resolved_operator_id IS NOT NULL
            ORDER BY resolved_operator_id
            """
        ).fetchall()
        levels = ro3.execute(
            """
            SELECT level_id_norm, level_id_orig, raw_json
            FROM levels
            WHERE level_id_norm LIKE 'obt/roguelike/ro3/%'
            ORDER BY level_id_norm
            """
        ).fetchall()
        for level_id, original_id, raw_json in levels:
            level = json.loads(raw_json)
            enemies = ro3.execute(
                """
                SELECT enemy_id, level, merged_enemy_data_json
                FROM enemy_refs
                WHERE level_id_norm=?
                ORDER BY ref_index
                """,
                (level_id,),
            ).fetchall()
            model = StageWorldModel(
                loaded_stage={
                    "name": original_id,
                    "mode": "ro3",
                    "level_ids": [level_id],
                    "stage_ids": [original_id],
                },
                level=level,
                resolved_enemies={
                    "enemies": {
                        "enemy_refs": [
                            {
                                "id": enemy_id,
                                "level": enemy_level,
                                "resolved": merged is not None,
                                "attributes": (json.loads(merged).get("attributes") if merged else {}),
                                **(
                                    {
                                        key: value
                                        for key, value in json.loads(merged).items()
                                        if key != "attributes"
                                    }
                                    if merged
                                    else {}
                                ),
                            }
                            for enemy_id, enemy_level, merged in enemies
                        ]
                    }
                },
                source={
                    "database": str(ro3_path),
                    "level_id": level_id,
                    "raw_json_sha256": hashlib.sha256(raw_json.encode()).hexdigest(),
                },
            )
            if not model.routes or not model.spawn_events:
                continue
            for (operator_id,) in operators:
                try:
                    profile = load_operator_profile(knowledge_path, operator_id)
                except StageWorldModelError:
                    continue
                legal_tiles = model._legal_tiles(profile)
                if not legal_tiles:
                    continue
                plan = {
                    "name": f"real-ro3-{level_id}-{operator_id}",
                    "deployments": [
                        {
                            "operator": operator_id,
                            "at_seconds": 0,
                            "col": legal_tiles[0][0],
                            "row": legal_tiles[0][1],
                            "facing": "right",
                        }
                    ],
                }
                try:
                    compile_simulator_bundle(model, [profile], scenario="most-likely")
                except StageWorldModelError:
                    continue
                return model, profile, plan
    finally:
        ro3.close()
        knowledge.close()
    raise OwnedClearError("tracked real ro3 data has no forecastable level/operator pair")


def forecast_owned_job(
    model: StageWorldModel,
    plan: Mapping[str, Any],
    profiles: Mapping[str, OperatorCombatProfile],
    *,
    horizon_seconds: float = 30.0,
) -> dict[str, Any]:
    if _looks_like_maa_autopilot(plan):
        raise OwnedClearError("forecast refuses MAA autopilot/copilot JSON as a battle plan")
    deployments = plan
    if "steps" in plan and "deployments" not in plan:
        deployments = {
            "name": plan.get("name"),
            "deployments": [
                {
                    "operator": step["action"]["deploy"]["operator"],
                    "col": step["action"]["deploy"]["col"],
                    "row": step["action"]["deploy"]["row"],
                    "facing": step["action"]["deploy"]["facing"],
                    "at_seconds": step["action"]["deploy"].get("at_seconds", 0),
                }
                for step in plan["steps"]
                if isinstance(step, Mapping) and "deploy" in (step.get("action") or {})
            ],
        }
    return forecast_battle_snapshot(model, deployments, profiles, horizon_seconds=horizon_seconds)


def _recruit_decision(
    state: Mapping[str, Any],
    *,
    profile: ScoreProfile,
    plugin: ContestantPlugin | Mapping[str, object] | None,
    script: str | None,
) -> OwnedDecision:
    visible = [item for item in (state.get("visible_recruits") or []) if isinstance(item, Mapping)]
    names = [str(item.get("name") or "") for item in visible if item.get("name")]
    if not names:
        raise OwnedClearError("recruit page has no visible operators")
    evaluation = evaluate_surface(
        profile,
        RunContext(
            operators=tuple(str(item) for item in (state.get("squad") or [])),
            relics=tuple(str(item) for item in (state.get("relics") or [])),
            stage=str(state.get("stage") or "") or None,
        ),
        plugin=plugin if plugin is not None else DEFAULT_OPENING_PLUGIN,
        script=script,
        operators=names,
    )
    ranked = sorted(evaluation.scores.values(), key=lambda item: (-item.final, item.operator))
    if not ranked:
        raise OwnedClearError("freedom surface produced no scores for visible recruits")
    pick = ranked[0].operator
    if pick == "安德切尔" and len(ranked) > 1 and ranked[0].default_priority <= ranked[1].default_priority:
        raise OwnedClearError("安德切尔-first solely by cheap DP is rejected")
    extras = {
        "ranking": [
            {"operator": item.operator, "final": item.final, "default": item.default_priority}
            for item in ranked
        ],
        "job_id": evaluation.job_id,
    }
    return OwnedDecision("recruit", "freedom-surface-score", {"op": "recruit", "name": pick}, "recruit", extras=extras)


def _battle_decision(
    state: Mapping[str, Any],
    *,
    profile: ScoreProfile,
    plugin: ContestantPlugin | Mapping[str, object] | None,
    script: str | None,
    job: Mapping[str, Any] | None,
    grid: StageGrid | None,
) -> OwnedDecision:
    evaluation = evaluate_surface(
        profile,
        RunContext(
            operators=tuple(str(item) for item in (state.get("squad") or [])),
            relics=tuple(str(item) for item in (state.get("relics") or [])),
            stage=str(state.get("stage") or "") or None,
        ),
        plugin=plugin if plugin is not None else DEFAULT_OPENING_PLUGIN,
        script=script,
    )
    plan = job
    if plan is None and grid is not None:
        deployable = [item for item in (state.get("deployable") or []) if isinstance(item, Mapping)]
        plan = conservative_job(grid, deployable)
    if plan is None:
        raise OwnedClearError("battle think needs a programmatic job or a Tile-Pos grid")
    queue = BattlePlanQueue(parse_battle_plan(plan))
    facts = BattleFacts(
        cost=state.get("cost") if isinstance(state.get("cost"), int) else None,
        leaked=int(state.get("leaked") or 0),
        time=state.get("time") if isinstance(state.get("time"), (int, float)) else None,
        evidence_fresh=True,
    )
    ready = queue.next_ready(facts)
    if ready.step is None:
        return OwnedDecision(
            "wait",
            ready.detail or ready.state.value,
            {"op": "pause"},
            "battle",
            extras={"job_id": evaluation.job_id, "queue": ready.state.value},
        )
    if ready.step.action == "deploy":
        facing = str(ready.step.payload["facing"])
        return OwnedDecision(
            "deploy",
            "programmatic-job",
            {
                "op": "deploy",
                "operator": ready.step.payload["operator"],
                "col": ready.step.payload["col"],
                "row": ready.step.payload["row"],
                "direction": _FACING[facing],
            },
            "battle",
            extras={"job_id": evaluation.job_id, "step_id": ready.step.step_id},
        )
    if ready.step.action == "skill":
        return OwnedDecision(
            "skill",
            "programmatic-job",
            {"op": "skill", "operator": ready.step.payload.get("operator") or ready.step.payload.get("name")},
            "battle",
            extras={"job_id": evaluation.job_id, "step_id": ready.step.step_id},
        )
    if ready.step.action == "retreat":
        return OwnedDecision(
            "retreat",
            "programmatic-job-prefer-retreat",
            {"op": "retreat", "operator": ready.step.payload.get("operator") or ready.step.payload.get("name")},
            "battle",
            extras={"job_id": evaluation.job_id, "step_id": ready.step.step_id},
        )
    if ready.step.action == "pause":
        return OwnedDecision("pause", "job-hook", {"op": "pause"}, "battle", extras={"step_id": ready.step.step_id})
    raise OwnedClearError(f"unsupported job action: {ready.step.action}")
