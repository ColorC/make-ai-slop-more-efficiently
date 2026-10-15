# [OMNI] origin=codex created_by=codex intent="advance newly unlocked main-story stages through fresh visual state, SingleStep actions, and settlement proof"
"""Run one newly unlocked main-story stage with live perception and no proxy."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .action_loop import BattleActionLoop, MaaBattleFrameSampler
from .battle_state import BattleStateParser, ROADBLOCK_NAME
from .live_battle import (
    BattleControlStatus,
    LiveBattleController,
    ObservationCadence,
    StageOnlinePolicy,
    TacticalSlotV1,
)
from .live_battle_run import (
    BattlePausedOverlayGate,
    BattleRunningTemplateGate,
    FrozenFramePacer,
    _battle_process_budget,
    _action_dict,
    _compile_factory,
    _event_dict,
    _state_dict,
    _write_json,
    _write_jsonl,
    _write_png,
    audit_events,
)
from .maa_operator import MaaOperator, MaaTaskFailed, TouchMode
from .operations import ArknightsOperations
from .tile_grid import load_stage_grid

SCHEMA = "omnicompany.arknights.chapter-live-battle.v1"


@dataclass(frozen=True, slots=True)
class StageSpec:
    code: str
    expected_life: int | None
    slots: tuple[TacticalSlotV1, ...]
    maximum_deployments: int = 12


class _DeploymentAwareCadence:
    """Use a sub-second opening until the four-unit forward screen is verified."""

    def __init__(
        self,
        opening: ObservationCadence,
        steady: ObservationCadence,
        deployment_count: Callable[[], int],
    ) -> None:
        self._opening = opening
        self._steady = steady
        self._deployment_count = deployment_count

    def next_delay(self) -> float:
        cadence = self._opening if self._deployment_count() < 4 else self._steady
        return cadence.next_delay()


_RIGHT_GOAL_SLOTS = (
    # Perspective makes (8,4) look adjacent to the lower blue box, but live
    # leak frames prove the two actual approach tiles are (8,3) and (8,2).
    TacticalSlotV1("芬", 8, 3, "Left", "middle", "core-vanguard", 500.0),
    TacticalSlotV1("翎羽", 8, 2, "Left", "top", "lane-vanguard", 400.0),
    # 2-1 has a tutorial ally on (9,1).  Keep every planned tile distinct from
    # that live obstruction and give the bottom blocker priority before a leak.
    TacticalSlotV1("玫兰莎", 7, 3, "Left", "middle", "lane-guard", 245.0),
    TacticalSlotV1("克洛丝", 9, 4, "Left", "bottom", "ranged-dps", 198.0),
    TacticalSlotV1("安赛尔", 8, 1, "Down", "top", "medic", 188.0),
    TacticalSlotV1("米格鲁", 7, 2, "Left", "top", "defender", 174.0),
    TacticalSlotV1("巡林者", 6, 4, "Right", "bottom", "ranged-dps", 158.0),
    TacticalSlotV1("史都华德", 6, 3, "Right", "middle", "caster", 150.0),
    TacticalSlotV1("杜林", 5, 2, "Right", "top", "caster", 145.0),
)

_TWO_TWO_SLOTS = tuple(
    slot for slot in _RIGHT_GOAL_SLOTS if slot.operator not in {"玫兰莎", "杜林"}
) + (
    # The first 2-2 leak was aerial while the fifth action had spent DP on a
    # melee-only guard.  Put the cheap air-capable caster in that opening
    # window; retain three ground blockers through the vanguards + defender.
    TacticalSlotV1("杜林", 5, 2, "Right", "top", "caster", 300.0),
    # 2-2's 51-enemy waves can outlast the opening eight.  These AoE casters
    # stay unavailable while the field is full, then current slot/DP/cooldown
    # truth may authorize a bounded casualty replacement.
    TacticalSlotV1("12F", 4, 2, "Right", "top", "aoe-caster", 220.0),
    TacticalSlotV1("炎熔", 9, 1, "Down", "top", "aoe-caster", 215.0),
)

_S_TWO_TWO_SLOTS = (
    # S2-2 has one central ground entrance plus aerial entrances on the right,
    # all converging on the left goal. Hold the three walkable approach rows at
    # column 4 and put ranged support on the adjacent high tiles.
    # The opening wave is aerial and reaches the left goal before two ground
    # deployments plus one sniper can resolve.  Establish three air-capable
    # firing arcs first; ground enemies enter later through the middle gate.
    TacticalSlotV1("克洛丝", 3, 2, "Right", "top", "ranged-dps", 700.0),
    TacticalSlotV1("巡林者", 3, 4, "Right", "bottom", "ranged-dps", 650.0),
    TacticalSlotV1("杜林", 5, 2, "Left", "top", "caster", 600.0),
    TacticalSlotV1("芬", 4, 3, "Right", "middle", "core-vanguard", 500.0),
    TacticalSlotV1("翎羽", 4, 1, "Right", "top", "lane-vanguard", 400.0),
    TacticalSlotV1("米格鲁", 4, 5, "Right", "bottom", "defender", 280.0),
    TacticalSlotV1("安赛尔", 2, 2, "Right", "middle", "medic", 230.0),
    TacticalSlotV1("史都华德", 5, 4, "Left", "bottom", "caster", 220.0),
    TacticalSlotV1("12F", 6, 1, "Left", "top", "aoe-caster", 205.0),
    TacticalSlotV1("炎熔", 7, 5, "Left", "bottom", "aoe-caster", 200.0),
)

_TWO_SIDED_SLOTS = (
    TacticalSlotV1("芬", 4, 4, "Left", "middle", "core-vanguard", 260.0),
    TacticalSlotV1("翎羽", 6, 4, "Right", "middle", "lane-vanguard", 250.0),
    TacticalSlotV1("克洛丝", 4, 3, "Down", "middle", "ranged-dps", 205.0),
    TacticalSlotV1("安赛尔", 4, 2, "Down", "top", "medic", 192.0),
    TacticalSlotV1("巡林者", 9, 4, "Left", "middle", "ranged-dps", 188.0),
    TacticalSlotV1("玫兰莎", 3, 4, "Right", "middle", "lane-guard", 180.0),
    TacticalSlotV1("米格鲁", 7, 4, "Left", "middle", "defender", 176.0),
    TacticalSlotV1("史都华德", 2, 4, "Right", "middle", "caster", 154.0),
    TacticalSlotV1("杜林", 3, 5, "Up", "bottom", "caster", 146.0),
)

_S_TWO_THREE_SLOTS = (
    # S2-3 supplies reusable five-DP roadblocks.  The opening block diverts the
    # right spawn into the left route.  The MAA stage grid proves both sources
    # then converge through (4,3), immediately above the sole goal at (4,4),
    # so the first verified blocker must stand on that common choke.
    TacticalSlotV1(ROADBLOCK_NAME, 7, 3, "None", "middle", "facility-opening", 1400.0),
    TacticalSlotV1("芬", 4, 3, "Left", "middle", "core-vanguard", 950.0),
    TacticalSlotV1(ROADBLOCK_NAME, 3, 4, "None", "middle", "facility-followup", 900.0),
    TacticalSlotV1(ROADBLOCK_NAME, 4, 5, "None", "bottom", "facility-followup", 890.0),
    # Do not stack the lower-right high tile's health bar over the vanguard:
    # live S2-3 evidence showed MAA's NMS collapses those two detections into
    # (7,5).  The separated center-right arc preserves independent effect truth.
    TacticalSlotV1("克洛丝", 6, 4, "Left", "middle", "ranged-dps", 800.0),
    TacticalSlotV1("12F", 5, 5, "Right", "bottom", "aoe-caster", 720.0),
    TacticalSlotV1("安赛尔", 2, 2, "Right", "middle", "medic", 650.0),
    # Attempt 12 held the choke until kill 18, then a stacked dog wave exceeded
    # Fang's block count while Beagle stood off-route.  Put the three-block
    # defender directly upstream of Fang; Ansel's (2,2) arc covers both tiles.
    TacticalSlotV1("米格鲁", 3, 3, "Left", "middle", "defender", 850.0),
    # The kill-46 elite stack filled all five downstream block slots at once.
    # Melantha adds one upstream block and focused melee damage before healing
    # or a second AoE body consumes the remaining deployment capacity.
    TacticalSlotV1("玫兰莎", 2, 3, "Left", "middle", "lane-guard", 800.0),
    TacticalSlotV1("巡林者", 2, 4, "Right", "middle", "ranged-dps", 520.0),
    # Attempt 10 leaked at active t=7.30 while an affordable 12-DP card had no
    # tactical slot and the next configured body cost 16 DP.
    TacticalSlotV1("杜林", 4, 2, "Down", "top", "caster", 700.0),
    TacticalSlotV1("炎熔", 7, 6, "Up", "bottom", "aoe-caster", 420.0),
)

STAGES = {
    "2-1": StageSpec("2-1", 8, _RIGHT_GOAL_SLOTS),
    "S2-1": StageSpec("S2-1", None, _RIGHT_GOAL_SLOTS),
    "2-2": StageSpec("2-2", None, _TWO_TWO_SLOTS),
    "S2-2": StageSpec("S2-2", None, _S_TWO_TWO_SLOTS),
    "S2-3": StageSpec("S2-3", None, _S_TWO_THREE_SLOTS),
    "2-3": StageSpec("2-3", None, _TWO_SIDED_SLOTS),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _try_task(
    operations: ArknightsOperations,
    name: str,
    *,
    timeout: float = 15.0,
) -> bool:
    try:
        operations.run_maa_tasks([name], timeout=timeout)
        return True
    except MaaTaskFailed:
        return False


def _deploy_cost_gates(decisions: Sequence[Any]) -> list[bool]:
    """Audit each chosen deployment against the integer DP observed beforehand."""
    gates: list[bool] = []
    for decision in decisions:
        if decision.action_name != "Deploy":
            continue
        current = decision.referenced_state.get("cost")
        required = decision.candidates[0].get("cost") if decision.candidates else None
        gates.append(
            isinstance(current, int)
            and not isinstance(current, bool)
            and isinstance(required, int)
            and not isinstance(required, bool)
            and current >= required
        )
    return gates


_ENTRY_INTERSTITIALS = (
    ("daily-supply", "Omni@DailySupply-Detect", "Omni@DailySupply-Confirm"),
    ("monthly-sign-in", "Omni@MonthlySignIn-Detect", "Omni@MonthlySignIn-Close"),
    ("seven-day-sign-in", "Omni@SevenDaySignIn-Detect", "Omni@SevenDaySignIn-Close"),
    ("event-sign-in", "Omni@EventSignIn-Detect", "Omni@EventSignIn-Close"),
    ("wish-wall", "Omni@WishWall-Detect", "Omni@WishWall-Close"),
    ("announcement", "Omni@Announcement-Detect", "Omni@Announcement-Close"),
)


def _dismiss_entry_interstitials(
    operations: ArknightsOperations,
    operator: MaaOperator,
    checkpoints: Path,
    *,
    max_dismissals: int = 12,
) -> tuple[bool, list[str]]:
    """Boundedly peel a serial popup chain, re-sensing after every action."""
    dismissed: list[str] = []
    for _ in range(max_dismissals + 1):
        if _try_task(operations, "Omni@Formation-Detect", timeout=5.0):
            return True, dismissed

        matched = False
        for slug, detector, closer in _ENTRY_INTERSTITIALS:
            if not _try_task(operations, detector, timeout=5.0):
                continue
            occurrence = sum(item == slug for item in dismissed) + 1
            _write_png(operator, checkpoints / f"{slug}-{occurrence}.png")
            operations.run_maa_tasks([closer], timeout=20.0)
            dismissed.append(slug)
            matched = True
            break

        if not matched:
            return False, dismissed

    return _try_task(operations, "Omni@Formation-Detect", timeout=5.0), dismissed


def _enter_from_current_detail(
    operations: ArknightsOperations,
    operator: MaaOperator,
    spec: StageSpec,
    checkpoints: Path,
) -> dict[str, Any]:
    entered_from_chapter_map = _ensure_current_stage_detail(
        operations,
        operator,
        spec,
        checkpoints,
    )
    if not _try_task(operations, f"Omni@StageDetail-{spec.code}-Detect"):
        raise RuntimeError(f"current stage detail is not {spec.code}")
    _write_png(operator, checkpoints / "stage-detail.png")

    proxy_was_enabled = _try_task(operations, "Omni@StageDetail-ProxyEnabled", timeout=10.0)
    if proxy_was_enabled:
        if not _try_task(operations, "Omni@StageDetail-DisableProxy"):
            raise RuntimeError("could not disable proxy before the live battle")
        if _try_task(operations, "Omni@StageDetail-ProxyEnabled", timeout=8.0):
            raise RuntimeError("proxy remained enabled after the disable action")

    operations.battle_bind_stage(spec.code)
    operations.battle_begin()
    battle_begin_count = 1
    if not _try_task(operations, "Omni@Battle-Detect", timeout=18.0):
        if not _try_task(operations, "Omni@Formation-Detect", timeout=18.0):
            raise RuntimeError("neither battle HUD nor formation appeared after start")
        _write_png(operator, checkpoints / "formation.png")
        operations.battle_begin()
        battle_begin_count = 2
        if not _try_task(operations, "Omni@Battle-Detect", timeout=30.0):
            sync_prompt = _try_task(operations, "Omni@DataSyncPrompt-Detect", timeout=12.0)
            if not sync_prompt:
                raise RuntimeError("battle HUD did not appear after formation confirmation")
            _write_png(operator, checkpoints / "data-sync-prompt.png")
            operations.run_maa_tasks(["Omni@DataSyncPrompt-Confirm"], timeout=20.0)
            formation_returned, dismissed = _dismiss_entry_interstitials(
                operations, operator, checkpoints
            )
            if not formation_returned:
                raise RuntimeError(
                    "formation did not return after data synchronization "
                    f"interstitials; dismissed={dismissed}"
                )
            operations.battle_begin()
            battle_begin_count = 3
            if not _try_task(operations, "Omni@Battle-Detect", timeout=45.0):
                raise RuntimeError(
                    "battle HUD did not appear after synchronized formation confirmation"
                )
    _write_png(operator, checkpoints / "battle-entry.png")
    return {
        "battle_begin_count": battle_begin_count,
        "entered_from_chapter_map": entered_from_chapter_map,
        "proxy_was_enabled": proxy_was_enabled,
        "proxy_disabled": not _try_task(operations, "Omni@Battle-ProxyRunning", timeout=8.0),
    }


def _ensure_current_stage_detail(
    operations: ArknightsOperations,
    operator: MaaOperator,
    spec: StageSpec,
    checkpoints: Path,
) -> bool:
    """Accept either an exact detail page or an exact visible chapter-map node."""

    if _try_task(operations, "Omni@StageDetail-Detect"):
        return False
    if not _try_task(operations, "Omni@ChapterScreen-Detect"):
        raise RuntimeError("current screen is neither a stage detail nor chapter map")
    if not _try_task(operations, f"Omni@Chapter-Stage-{spec.code}-Detect"):
        raise RuntimeError(f"stage {spec.code} is not visible on the current chapter map")
    _write_png(operator, checkpoints / "chapter-map.png")
    operations.run_maa_tasks([f"Omni@Chapter-GoStage-{spec.code}"], timeout=20.0)
    if not _try_task(operations, "Omni@StageDetail-Detect", timeout=15.0):
        raise RuntimeError(f"stage {spec.code} detail did not open from the chapter map")
    return True


def _recover_failed_battle(
    operations: ArknightsOperations,
    operator: MaaOperator,
    stage: str,
    checkpoints: Path,
) -> dict[str, Any]:
    """Bounded visual cleanup after a failed live attempt, including refund page."""
    result: dict[str, Any] = {"battle_detected": False, "abandoned": False}
    try:
        result["paused_battle_detected"] = _try_task(operations, "Omni@Battle-Paused", timeout=10.0)
        result["battle_detected"] = result["paused_battle_detected"] or _try_task(
            operations, "Omni@Battle-Detect", timeout=12.0
        )
        if not result["battle_detected"]:
            return result
        operations.abandon_battle()
        result["abandoned"] = True
        _write_png(operator, checkpoints / "recovery-after-abandon.png")
        result["failure_page_detected"] = _try_task(
            operations, "Omni@MissionFailed-Detect", timeout=15.0
        )
        if not result["failure_page_detected"]:
            return result
        operations.dismiss_mission_failed()
        _write_png(operator, checkpoints / "recovery-refund-result.png")
        result["refund_stage_detected"] = _try_task(
            operations, f"Omni@Settlement-Stage-{stage}-Detect", timeout=15.0
        )
        if not result["refund_stage_detected"]:
            return result
        result["settlement_dismiss_attempts"] = 0
        result["returned_to_chapter"] = False
        # The first post-refund tap can finish the result animation without
        # leaving it.  Re-detect after each bounded tap; never click a third time.
        for _attempt in range(2):
            result["settlement_dismiss_attempts"] += 1
            operations.run_maa_tasks(["Omni@Settlement-Dismiss"], timeout=15.0)
            result["returned_to_chapter"] = _try_task(
                operations, "Omni@ChapterScreen-Detect", timeout=15.0
            )
            if result["returned_to_chapter"]:
                break
        _write_png(operator, checkpoints / "recovery-finished.png")
    except Exception as exc:  # noqa: BLE001 - recovery evidence belongs in receipt
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    return result


def _skip_battle_plot_if_present(
    operations: ArknightsOperations,
    operator: MaaOperator,
    checkpoints: Path,
) -> bool:
    """Skip one proven tutorial prompt and freeze the live battle immediately."""

    if not _try_task(operations, "Omni@BattlePlotSkip-Detect", timeout=8.0):
        return False
    _write_png(operator, checkpoints / "battle-plot-skip-detected.png")
    operations.run_maa_tasks(["Omni@BattlePlotSkip-Click"], timeout=8.0)
    if _try_task(operations, "Omni@BattlePlotSkipConfirm-Detect", timeout=3.0):
        _write_png(operator, checkpoints / "battle-plot-skip-confirm-detected.png")
        operations.run_maa_tasks(["Omni@BattlePlotSkipConfirm-Click"], timeout=8.0)
    effect_path = Path(_write_png(operator, checkpoints / "battle-after-plot-skip.png"))
    running_gate = BattleRunningTemplateGate(
        operator.paths.root
        / "resource"
        / "template"
        / "Battle"
        / "BattleFlag"
        / "BattleOfficiallyBegin.png"
    )
    running_truth = running_gate.matches_png(effect_path.read_bytes())
    hud_truth = running_truth or _try_task(operations, "Omni@Battle-Detect", timeout=8.0)
    if not hud_truth:
        raise RuntimeError("battle HUD did not return after the proven plot-skip action")
    try:
        operations.pause_battle()
    except MaaTaskFailed:
        # A Custom task can report a terminal-chain error after its one bounded
        # click.  Only the independent paused overlay may promote the effect.
        pass
    paused_path = Path(
        _write_png(operator, checkpoints / "battle-after-plot-skip-paused.png")
    )
    if not BattlePausedOverlayGate()(paused_path):
        raise RuntimeError("battle did not freeze immediately after the plot skip")
    return True


def run_current_stage(
    *,
    stage: str,
    output_dir: Path,
    adb_path: Path,
    address: str = "emulator-5554",
    connection_config: str = "General",
    touch_mode: str = TouchMode.MAATOUCH,
    maa_root: Path | None = None,
    cadence_min: float = 2.0,
    cadence_max: float = 3.0,
    cadence_seed: int = 0,
    opening_cadence_min: float = 0.2,
    opening_cadence_max: float = 0.35,
    two_x: bool = False,
    resume_battle: bool = False,
    prior_unique_frames: int = 0,
    prior_verified_actions: int = 0,
    prior_deployments: int = 0,
) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"unsupported progression stage: {stage}")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output evidence directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "frames"
    checkpoints = output_dir / "checkpoints"
    frames_dir.mkdir()
    checkpoints.mkdir()

    spec = STAGES[stage]
    started = time.monotonic()
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "stage": stage,
        "started_at": _utc_now(),
        "success": False,
        "resource_boundary": {
            "medicine": 0,
            "stone": 0,
            "premium_currency": 0,
            "sanity_cost": "stage default",
        },
        "control_contract": {
            "game_proxy": "forbidden",
            "Fight": "forbidden",
            "Copilot": "forbidden",
            "video_or_recording": "forbidden",
            "executor": "Custom recognition + SingleStep/action",
        },
    }
    operator: MaaOperator | None = None
    operations: ArknightsOperations | None = None
    control = None
    controller = None
    policy = None
    pacer = None
    parser: BattleStateParser | None = None
    entry_paused = False
    try:
        operator = MaaOperator.create(root=maa_root)
        operator.set_touch_mode(touch_mode)
        operator.set_deployment_with_pause(True)
        if not operator.connect(adb_path, address, connection_config):
            raise RuntimeError(f"cannot connect MAA to {address}")
        operations = ArknightsOperations(operator)
        receipt["maa_version"] = operator.version()
        receipt["device"] = {
            "address": address,
            "connection_config": connection_config,
            "touch_mode": touch_mode,
        }
        if resume_battle:
            if not _try_task(operations, "Omni@Battle-Detect", timeout=15.0):
                raise RuntimeError("resume requested, but no live battle HUD is present")
            operations.battle_bind_stage(stage)
            battle_plot_skipped = _skip_battle_plot_if_present(operations, operator, checkpoints)
            paused = battle_plot_skipped or _try_task(
                operations, "Omni@Battle-Paused", timeout=10.0
            )
            if not paused:
                operations.pause_battle()
                paused = True
            entry_paused = paused
            receipt["entry"] = {
                "resumed_existing_battle": True,
                "paused_before_resume_loop": paused,
                "prior_unique_frames": prior_unique_frames,
                "prior_verified_actions": prior_verified_actions,
                "prior_deployments": prior_deployments,
                "battle_plot_skipped": battle_plot_skipped,
            }
            _write_png(operator, checkpoints / "resumed-battle.png")
        else:
            receipt["entry"] = _enter_from_current_detail(operations, operator, spec, checkpoints)
            entry_paused = _skip_battle_plot_if_present(
                operations, operator, checkpoints
            )
            receipt["entry"]["battle_plot_skipped"] = entry_paused

        if two_x:
            operations.speed_up_battle()
        receipt["two_x_requested"] = two_x

        policy = StageOnlinePolicy(
            maximum_deployments=spec.maximum_deployments,
            slots=spec.slots,
            expected_life=spec.expected_life,
            manual_skill_operators=("玫兰莎",),
        )
        running_gate = BattleRunningTemplateGate(
            operator.paths.root
            / "resource"
            / "template"
            / "Battle"
            / "BattleFlag"
            / "BattleOfficiallyBegin.png"
        )
        pacer = FrozenFramePacer(
            operations,
            _DeploymentAwareCadence(
                ObservationCadence(
                    opening_cadence_min,
                    opening_cadence_max,
                    seed=cadence_seed,
                ),
                ObservationCadence(
                    cadence_min,
                    cadence_max,
                    seed=cadence_seed + 1,
                ),
                lambda: policy.combat_deployment_count,
            ),
            running_gate,
            BattlePausedOverlayGate(),
        )
        if resume_battle or entry_paused:
            pacer.paused = True
        else:
            pacer.freeze_after_entry()
        grid = load_stage_grid(operator.paths.tile_pos_dir, code=stage)
        parser = BattleStateParser(
            stage_grid=grid,
            full_observation_interval=2,
        )
        sampler = MaaBattleFrameSampler(
            operator,
            frames_dir,
            parser=parser,
            before_capture=pacer.before_capture,
            after_capture=pacer.after_capture,
            after_parse=pacer.observe_frame,
            active_game_clock=lambda: pacer.active_game_seconds,
        )

        def execute(call):  # noqa: ANN001, ANN202
            if call.action_name == "Skill":
                location = call.details.get("location")
                if not isinstance(location, list) or len(location) != 2:
                    raise ValueError("visual Skill requires a two-value location")
                return pacer.execute_live_action(
                    lambda: operations.use_skill_at(int(location[0]), int(location[1])),
                    lambda: running_gate.matches_png(operator.capture_png(fresh=True)),
                )
            return operations.run_prepared_battle_actions([call], timeout=45.0)

        action_loop = BattleActionLoop(
            sampler,
            execute,
            max_after_observations=4,
        )
        controller = LiveBattleController(
            sampler,
            action_loop,
            policy,
            _compile_factory(Path(__file__).resolve().parents[6]),
            cadence=ObservationCadence(0.0),
            sleeper=lambda _delay: None,
            maximum_cycles=150,
            maximum_seconds=1800.0,
        )
        control = controller.run()

        receipt["control_before_settlement_checks"] = {
            "status": control.status.value,
            "reason": control.reason,
            "observation_count": len(control.observations),
            "verified_action_count": control.verified_action_count,
            "deployment_count": control.deployment_count,
        }

        scene_changed = control.status is BattleControlStatus.SCENE_CHANGED
        if scene_changed:
            time.sleep(2.0)
        _write_png(operator, checkpoints / "post-control.png")
        settlement = scene_changed and _try_task(operations, "Omni@Settlement-Detect", timeout=20.0)
        post_battle_story_skipped = False
        if (
            scene_changed
            and not settlement
            and _try_task(operations, "Omni@PostBattleStoryPrompt-Detect", timeout=12.0)
        ):
            _write_png(operator, checkpoints / "post-battle-story-prompt.png")
            operations.run_maa_tasks(["Omni@PostBattleStoryPrompt-ConfirmSkip"], timeout=20.0)
            post_battle_story_skipped = True
            settlement = _try_task(operations, "Omni@Settlement-Detect", timeout=30.0)
        # The generic OCR anchor is occasionally occluded by the first-clear
        # proxy-unlock modal.  The independent 3-star template plus exact
        # stage title/code is an equally strong settlement identity proof.
        stars3 = scene_changed and _try_task(operations, "Omni@Settlement-Stars3", timeout=12.0)
        stage_truth = scene_changed and _try_task(
            operations, f"Omni@Settlement-Stage-{stage}-Detect", timeout=12.0
        )
        settlement_anchor = "generic_ocr"
        if not settlement and stars3 and stage_truth:
            settlement = True
            settlement_anchor = "stars3_plus_exact_stage"
        failed = scene_changed and _try_task(operations, "Omni@MissionFailed-Detect", timeout=10.0)
        if settlement:
            _write_png(operator, checkpoints / "settlement.png")

        unique_frames = {
            str(Path(item.state.frame.path).resolve()) for item in control.observations
        }
        life_values = [
            int(item.state.global_state.life_points.value)
            for item in control.observations
            if item.state.global_state.life_points.known
        ]
        actions = [_action_dict(item) for item in control.actions]
        _write_jsonl(
            output_dir / "states.jsonl",
            [_state_dict(item) for item in control.observations],
        )
        _write_jsonl(
            output_dir / "decisions.jsonl",
            [item.as_dict() for item in control.decisions],
        )
        _write_json(output_dir / "actions.json", actions)
        event_audit = audit_events(operator.events)
        deploy_cost_gates = _deploy_cost_gates(control.decisions)
        gates = {
            "scene_changed_after_live_control": (
                control.status is BattleControlStatus.SCENE_CHANGED
            ),
            "settlement_detected": settlement,
            "three_stars": stars3,
            "correct_stage_on_settlement": stage_truth,
            "mission_failed_absent": not failed,
            "minimum_10_unique_live_frames": (prior_unique_frames + len(unique_frames) >= 10),
            "minimum_3_verified_actions": (
                prior_verified_actions + control.verified_action_count >= 3
            ),
            "minimum_3_deployments": (prior_deployments + control.deployment_count >= 3),
            "all_actions_verified": all(item.verified for item in control.actions),
            "no_action_reexecution": all(
                not item.action_reexecuted and item.execution_count == 1 for item in control.actions
            ),
            "life_never_dropped": (
                bool(life_values)
                and policy.expected_life is not None
                and min(life_values) == policy.expected_life
            ),
            "integer_cost_gated_deployments": (
                all(deploy_cost_gates) and (bool(deploy_cost_gates) or prior_deployments > 0)
            ),
            "forbidden_runtime_events_absent": event_audit["passed"],
        }
        receipt.update(
            control={
                "status": control.status.value,
                "reason": control.reason,
                "observation_count": len(control.observations),
                "unique_frame_count": len(unique_frames),
                "combined_unique_frame_count": prior_unique_frames + len(unique_frames),
                "verified_action_count": control.verified_action_count,
                "combined_verified_action_count": prior_verified_actions
                + control.verified_action_count,
                "deployment_count": control.deployment_count,
                "combined_deployment_count": prior_deployments + control.deployment_count,
                "active_game_seconds": policy.memory.active_game_seconds,
                "delays": pacer.delays,
                "opening_cadence_seconds": [
                    opening_cadence_min,
                    opening_cadence_max,
                ],
                "steady_cadence_seconds": [cadence_min, cadence_max],
                "resume_failures": pacer.resume_failures,
                "pause_failures": pacer.pause_failures,
            },
            settlement={
                "detected": settlement,
                "stars3": stars3,
                "stage_code": stage_truth,
                "mission_failed": failed,
                "post_battle_story_skipped": post_battle_story_skipped,
                "anchor": settlement_anchor if settlement else None,
            },
            temporal_cost={
                "net_dp_per_second": policy.memory.net_dp_per_second,
                "dp_regime": policy.memory.dp_regime,
                "cost_progress": policy.memory.last_cost_progress,
                "integer_cost_required_for_deploy": True,
                "progress_or_time_can_authorize_deploy": False,
            },
            event_audit=event_audit,
            gates=gates,
        )
        receipt["success"] = all(gates.values())
        if not receipt["success"]:
            receipt["recovery"] = _recover_failed_battle(operations, operator, stage, checkpoints)
    except Exception as exc:  # noqa: BLE001 - retain live evidence on every failure
        receipt["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if control is None and controller is not None:
            partial = controller.snapshot(reason=f"executor raised {type(exc).__name__}: {exc}")
            _write_jsonl(
                output_dir / "states.jsonl",
                [_state_dict(item) for item in partial.observations],
            )
            _write_jsonl(
                output_dir / "decisions.jsonl",
                [item.as_dict() for item in partial.decisions],
            )
            _write_json(
                output_dir / "actions.json",
                [_action_dict(item) for item in partial.actions],
            )
            receipt["partial_control"] = {
                "observation_count": len(partial.observations),
                "decision_count": len(partial.decisions),
                "verified_action_count": partial.verified_action_count,
                "deployment_count": partial.deployment_count,
            }
        if operator is not None:
            try:
                receipt["failure_frame"] = _write_png(operator, checkpoints / "failure.png")
            except Exception as frame_exc:  # noqa: BLE001
                receipt["failure_frame_error"] = str(frame_exc)
        if operations is not None and operator is not None:
            receipt["recovery"] = _recover_failed_battle(operations, operator, stage, checkpoints)
    finally:
        if parser is not None:
            receipt["perception_timing"] = parser.timing_summary()
        if operator is not None:
            receipt["event_audit"] = audit_events(operator.events)
            _write_jsonl(
                output_dir / "events.jsonl",
                [_event_dict(item, started) for item in operator.events],
            )
            try:
                operator.stop()
            except Exception:  # noqa: BLE001
                pass
        receipt["finished_at"] = _utc_now()
        receipt["duration_seconds"] = round(time.monotonic() - started, 3)
        _write_json(output_dir / "receipt.json", receipt)
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=tuple(STAGES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--adb-path",
        type=Path,
        default=Path(r"C:\Program Files\Netease\MuMu\nx_main\adb.exe"),
    )
    parser.add_argument("--address", default="emulator-5554")
    parser.add_argument("--connection-config", default="General")
    parser.add_argument(
        "--touch-mode",
        choices=(TouchMode.MINITOUCH, TouchMode.MAATOUCH, TouchMode.ADB),
        default=TouchMode.MAATOUCH,
    )
    parser.add_argument("--maa-root", type=Path)
    parser.add_argument("--cadence-min", type=float, default=2.0)
    parser.add_argument("--cadence-max", type=float, default=3.0)
    parser.add_argument("--cadence-seed", type=int, default=0)
    parser.add_argument("--opening-cadence-min", type=float, default=0.2)
    parser.add_argument("--opening-cadence-max", type=float, default=0.35)
    parser.add_argument(
        "--two-x",
        action="store_true",
        help="opt in to 2x battle speed; progression defaults to safer 1x perception",
    )
    parser.add_argument("--resume-battle", action="store_true")
    parser.add_argument("--prior-unique-frames", type=int, default=0)
    parser.add_argument("--prior-verified-actions", type=int, default=0)
    parser.add_argument("--prior-deployments", type=int, default=0)
    parser.add_argument("--max-cpus", type=int, default=4)
    parser.add_argument("--allow-concurrent", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with _battle_process_budget(
        args.address,
        max_cpus=args.max_cpus,
        allow_concurrent=args.allow_concurrent,
    ):
        receipt = run_current_stage(
            stage=args.stage,
            output_dir=args.output,
            adb_path=args.adb_path,
            address=args.address,
            connection_config=args.connection_config,
            touch_mode=args.touch_mode,
            maa_root=args.maa_root,
            cadence_min=args.cadence_min,
            cadence_max=args.cadence_max,
            cadence_seed=args.cadence_seed,
            opening_cadence_min=args.opening_cadence_min,
            opening_cadence_max=args.opening_cadence_max,
            two_x=args.two_x,
            resume_battle=args.resume_battle,
            prior_unique_frames=args.prior_unique_frames,
            prior_verified_actions=args.prior_verified_actions,
            prior_deployments=args.prior_deployments,
        )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
