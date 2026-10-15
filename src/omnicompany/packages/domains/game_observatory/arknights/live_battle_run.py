# [OMNI] origin=codex domain=game_observatory/arknights ts=2026-08-09T10:12:00Z type=live-runner status=active
# [OMNI] summary="在真机上冻结新鲜帧、在线决策、SingleStep 执行、独立验效并产出无代理审计回执。"
# [OMNI] tags=game-observatory,arknights,live-battle,maa,evidence,acceptance
"""1-7 无代理实时战斗运行器。"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from .action_loop import BattleActionLoop, MaaBattleFrameSampler
from .battle_entry import BattleEntryController
from .battle_state import BattleStateParser, RapidOCRBattleReader
from .effect_oracle import BattleFrameEnvelopeV1
from .live_battle import (
    BattleControlResultV1,
    BattleControlStatus,
    BattleDecisionV1,
    LiveBattleController,
    ObservationCadence,
    OneSevenOnlinePolicy,
)
from .maa_contracts import (
    OperationAuthorization,
    build_maa_contract_catalog,
    compile_battle_action,
)
from .maa_operator import MaaEvent, MaaOperator, MaaTaskFailed, TouchMode
from .maa_semantics import load_maa_semantic_registry
from .operations import ArknightsOperations
from .tile_grid import load_stage_grid
from ..models import SourcePixelRect

SCHEMA = "omnicompany.arknights.live-agent-battle.v1"
FORBIDDEN_TASKCHAINS = frozenset({"Fight", "Copilot", "SSSCopilot"})
FORBIDDEN_PRTS_TASKS = frozenset({"UsePrts", "PRTS1", "PRTS2", "PRTS3"})


def _default_cpu_budget() -> int:
    available = max(1, os.cpu_count() or 1)
    configured = int(os.environ.get("OMNI_BATTLE_MAX_CPUS") or 4)
    return max(1, min(configured, available))


def _select_affinity_mask(current_mask: int, max_cpus: int) -> int:
    selected = 0
    remaining = max(1, int(max_cpus))
    bit = 1
    while bit <= current_mask and remaining:
        if current_mask & bit:
            selected |= bit
            remaining -= 1
        bit <<= 1
    return selected or current_mask


def _apply_process_budget(max_cpus: int) -> dict[str, Any]:
    """Protect interactive CLI traffic from a future live-battle run."""

    result: dict[str, Any] = {
        "priority": "below_normal",
        "max_cpus": max(1, int(max_cpus)),
        "affinity_applied": False,
    }
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetPriorityClass.restype = wintypes.BOOL
        kernel32.GetProcessAffinityMask.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        kernel32.GetProcessAffinityMask.restype = wintypes.BOOL
        kernel32.SetProcessAffinityMask.argtypes = [
            wintypes.HANDLE,
            ctypes.c_size_t,
        ]
        kernel32.SetProcessAffinityMask.restype = wintypes.BOOL
        process = kernel32.GetCurrentProcess()
        kernel32.SetPriorityClass(process, 0x00004000)  # BELOW_NORMAL_PRIORITY_CLASS
        process_mask = ctypes.c_size_t()
        system_mask = ctypes.c_size_t()
        if kernel32.GetProcessAffinityMask(
            process, ctypes.byref(process_mask), ctypes.byref(system_mask)
        ):
            selected = _select_affinity_mask(int(process_mask.value), result["max_cpus"])
            if kernel32.SetProcessAffinityMask(process, selected):
                result["affinity_applied"] = True
                result["affinity_mask"] = selected
        return result

    try:
        current = sorted(os.sched_getaffinity(0))
        selected_cpus = current[: result["max_cpus"]]
        os.sched_setaffinity(0, selected_cpus)
        result["affinity_applied"] = True
        result["affinity_cpus"] = selected_cpus
    except (AttributeError, OSError):
        pass
    try:
        os.nice(5)
    except OSError:
        pass
    return result


@contextmanager
def _battle_process_budget(
    address: str,
    *,
    max_cpus: int,
    allow_concurrent: bool,
) -> Iterator[dict[str, Any]]:
    """One live controller per emulator, with an explicit escape hatch."""

    budget = _apply_process_budget(max_cpus)
    budget["allow_concurrent"] = bool(allow_concurrent)
    if allow_concurrent:
        yield budget
        return

    safe_address = re.sub(r"[^A-Za-z0-9_.-]+", "_", address)[:120] or "default"
    lock_dir = Path(tempfile.gettempdir()) / "omnicompany-battle-budget"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"arknights-{safe_address}.lock"
    handle = lock_path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                f"another live battle already owns emulator {address}; "
                "wait for it or pass --allow-concurrent explicitly"
            ) from exc
        budget["lock"] = str(lock_path)
        yield budget
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


class FrozenFramePacer:
    """截取未暂停帧后立即暂停，让较慢的视觉解析不消耗游戏内时间。"""

    def __init__(
        self,
        operations: ArknightsOperations,
        cadence: ObservationCadence,
        battle_running_detector: Callable[[Path], bool],
        battle_paused_detector: Callable[[Path], bool] | None = None,
    ) -> None:
        self.operations = operations
        self.cadence = cadence
        self.battle_running_detector = battle_running_detector
        self.battle_paused_detector = battle_paused_detector
        self.paused = False
        self.delays: list[float] = []
        self.resume_failures: list[str] = []
        self.pause_failures: list[str] = []
        self.scene_transition_frames: list[str] = []
        self.unexpected_paused_frames: list[str] = []
        self.active_game_seconds = 0.0
        self.terminal_sampling = False
        self.pause_count = 0
        self.total_pause_seconds = 0.0
        self._pause_started_monotonic: float | None = None

    def _mark_paused(self) -> None:
        if self.paused:
            return
        self.paused = True
        self.pause_count += 1
        self._pause_started_monotonic = time.monotonic()

    def _mark_resumed(self) -> None:
        if not self.paused:
            return
        now = time.monotonic()
        if self._pause_started_monotonic is not None:
            self.total_pause_seconds += now - self._pause_started_monotonic
        self._pause_started_monotonic = None
        self.paused = False

    def pause_metrics(self) -> dict[str, Any]:
        total = self.total_pause_seconds
        if self.paused and self._pause_started_monotonic is not None:
            total += time.monotonic() - self._pause_started_monotonic
        return {
            "pause_count": self.pause_count,
            "total_pause_seconds": round(total, 6),
            "currently_paused": self.paused,
            "active_game_seconds": round(self.active_game_seconds, 6),
            "measurement": "control-layer monotonic wall time between confirmed pause/resume transitions",
        }

    def before_capture(self) -> None:
        if self.paused:
            try:
                self.operations.resume_battle()
            except MaaTaskFailed as exc:
                self.resume_failures.append(str(exc))
                raise
            else:
                self._mark_resumed()
        delay = self.cadence.next_delay()
        if self.terminal_sampling:
            delay = min(delay, 1.0)
        self.delays.append(delay)
        time.sleep(delay)
        self.active_game_seconds += delay

    def after_capture(self, _path: Path) -> None:
        if not self.battle_running_detector(_path):
            if self.battle_paused_detector is not None and self.battle_paused_detector(_path):
                self.unexpected_paused_frames.append(str(_path.resolve()))
                self._mark_paused()
                if len(self.unexpected_paused_frames) >= 2:
                    raise MaaTaskFailed(
                        "battle remained paused after two single-click resume attempts"
                    )
                return
            self.scene_transition_frames.append(str(_path.resolve()))
            self._mark_resumed()
            return
        if self.terminal_sampling:
            # Once only the final enemy remains, do not race a pause click
            # against the settlement transition.  A pause icon proved in the
            # captured pixels can disappear before the click lands, and a
            # late click on the results screen would dismiss our truth frame.
            self._mark_resumed()
            return
        self.unexpected_paused_frames.clear()
        try:
            self.operations.pause_battle()
        except MaaTaskFailed as exc:
            self.pause_failures.append(str(exc))
            self._mark_resumed()
            raise
        self._mark_paused()

    def observe_frame(self, frame: BattleFrameEnvelopeV1) -> None:
        global_state = frame.state.global_state
        if not global_state.kills.known or not global_state.total_enemies.known:
            return
        kills = int(global_state.kills.value)
        total = int(global_state.total_enemies.value)
        if total > 0 and kills >= total - 1:
            self.terminal_sampling = True

    def freeze_after_entry(self) -> None:
        """Stop live game time before slow OCR/model initialization begins."""
        self.operations.pause_battle()
        self._mark_paused()

    def adopt_entry_freeze(self) -> None:
        """Adopt the pause already proved by the MAA entry chain without toggling it."""
        self._mark_paused()

    def execute_live_action(
        self,
        action: Callable[[], Any],
        battle_still_running: Callable[[], bool],
    ) -> Any:
        """Resume only for a direct action, then visually guard the closing pause."""

        if not self.paused:
            raise RuntimeError("live action requires a confirmed paused battle")
        started = time.monotonic()
        self.operations.resume_battle()
        self._mark_resumed()
        try:
            return action()
        finally:
            try:
                if battle_still_running():
                    self.operations.pause_battle()
                    self._mark_paused()
            finally:
                self.active_game_seconds += time.monotonic() - started


class BattleRunningTemplateGate:
    """Prove that the live pause icon exists before issuing a pause click."""

    ROI = (1165, 20, 75, 65)

    def __init__(self, template_path: Path, *, threshold: float = 0.7) -> None:
        import cv2  # type: ignore[import-not-found]
        import numpy as np

        self.threshold = threshold
        self._cv2 = cv2
        template = cv2.imdecode(
            np.fromfile(template_path, dtype=np.uint8),
            cv2.IMREAD_GRAYSCALE,
        )
        if template is None:
            raise ValueError(f"cannot decode battle-running template: {template_path}")
        self.template = template

    def __call__(self, image_path: Path) -> bool:
        import numpy as np

        image = self._cv2.imdecode(
            np.fromfile(image_path, dtype=np.uint8),
            self._cv2.IMREAD_GRAYSCALE,
        )
        return self._matches_image(image)

    def matches_png(self, payload: bytes) -> bool:
        import numpy as np

        image = self._cv2.imdecode(
            np.frombuffer(payload, dtype=np.uint8),
            self._cv2.IMREAD_GRAYSCALE,
        )
        return self._matches_image(image)

    def _matches_image(self, image: Any) -> bool:
        if image is None:
            return False
        height, width = image.shape[:2]
        x, y, roi_width, roi_height = self.ROI
        left = round(x * width / 1280)
        top = round(y * height / 720)
        right = round((x + roi_width) * width / 1280)
        bottom = round((y + roi_height) * height / 720)
        roi = image[top:bottom, left:right]
        if roi.shape[0] < self.template.shape[0] or roi.shape[1] < self.template.shape[1]:
            return False
        scores = self._cv2.matchTemplate(
            roi,
            self.template,
            self._cv2.TM_CCOEFF_NORMED,
        )
        return float(scores.max()) >= self.threshold


class BattlePausedOverlayGate:
    """Recognize the five large PAUSE glyphs without treating settlement as battle."""

    ROI = (470, 270, 340, 140)

    def __call__(self, image_path: Path) -> bool:
        import cv2  # type: ignore[import-not-found]
        import numpy as np

        image = cv2.imdecode(
            np.fromfile(image_path, dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if image is None:
            return False
        height, width = image.shape[:2]
        x, y, roi_width, roi_height = self.ROI
        left = round(x * width / 1280)
        top = round(y * height / 720)
        right = round((x + roi_width) * width / 1280)
        bottom = round((y + roi_height) * height / 720)
        roi = image[top:bottom, left:right]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        # PAUSE is alpha-blended over the battlefield.  On a bright operator or
        # effect the same glyph can fall below V=155 and gain some saturation,
        # so colour alone is not a stable gate.  Keep a wider colour mask and
        # require the five letter-sized components to form one horizontal word.
        mask = ((hsv[:, :, 1] < 70) & (hsv[:, :, 2] > 140)).astype("uint8")
        _count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            mask,
            8,
        )
        x_scale = width / 1280
        y_scale = height / 720
        area_scale = x_scale * y_scale
        glyphs = [
            (int(left), int(top), int(glyph_width), int(glyph_height))
            for left, top, glyph_width, glyph_height, area in stats[1:]
            if area >= 700 * area_scale
            and 30 * x_scale <= glyph_width <= 65 * x_scale
            and 55 * y_scale <= glyph_height <= 90 * y_scale
            and 10 * y_scale <= top <= 45 * y_scale
        ]
        glyphs.sort()
        for index in range(len(glyphs) - 4):
            word = glyphs[index : index + 5]
            tops = [glyph[1] for glyph in word]
            heights = [glyph[3] for glyph in word]
            left_gaps = [word[position + 1][0] - word[position][0] for position in range(4)]
            span = word[-1][0] + word[-1][2] - word[0][0]
            if (
                max(tops) - min(tops) <= 8 * y_scale
                and max(heights) - min(heights) <= 12 * y_scale
                and all(42 * x_scale <= gap <= 78 * x_scale for gap in left_gaps)
                and 240 * x_scale <= span <= 310 * x_scale
            ):
                return True
        return False


class StaleAfterActionSampler:
    """One-shot stale observation injection; fresh pixels are captured but hidden once."""

    def __init__(
        self,
        sampler: MaaBattleFrameSampler,
        *,
        enabled: bool,
        on_fresh_frame: Callable[[BattleFrameEnvelopeV1], None] | None = None,
    ) -> None:
        self.sampler = sampler
        self.enabled = enabled
        self.on_fresh_frame = on_fresh_frame
        self.armed = False
        self.injected = 0
        self.last_returned: BattleFrameEnvelopeV1 | None = None

    def arm(self) -> None:
        if self.enabled and self.injected == 0 and self.last_returned is not None:
            self.armed = True

    def capture(self) -> BattleFrameEnvelopeV1:
        fresh = self.sampler.capture()
        if self.on_fresh_frame is not None:
            self.on_fresh_frame(fresh)
        if self.armed and self.last_returned is not None:
            self.armed = False
            self.injected += 1
            stale = BattleFrameEnvelopeV1(
                state=self.last_returned.state,
                capture_requested_monotonic=fresh.capture_requested_monotonic,
                captured_monotonic=fresh.captured_monotonic,
                refresh_succeeded=True,
                source=f"{fresh.source}+injected-stale-state",
                active_game_seconds=fresh.active_game_seconds,
            )
            self.last_returned = stale
            return stale
        self.last_returned = fresh
        return fresh


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_png(operator: MaaOperator, target: Path) -> str:
    payload = operator.capture_png(fresh=True)
    if not payload:
        raise RuntimeError(f"no fresh PNG for {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return str(target.resolve())


def _checkpoint_capture(operator: MaaOperator, directory: Path):
    def capture(label: str) -> str | None:
        return _write_png(operator, directory / f"{label}.png")

    return capture


def _try_task(
    operations: ArknightsOperations,
    task_name: str,
    *,
    timeout: float = 20.0,
) -> tuple[bool, tuple[MaaEvent, ...]]:
    try:
        return True, tuple(operations.run_maa_tasks([task_name], timeout=timeout))
    except MaaTaskFailed:
        return False, ()


def _event_task(event: MaaEvent) -> str:
    details = event.details.get("details", {})
    return str(details.get("task", "")) if isinstance(details, dict) else ""


def audit_events(events: Sequence[MaaEvent]) -> dict[str, Any]:
    forbidden_prts = [
        event
        for event in events
        if event.what in FORBIDDEN_PRTS_TASKS or _event_task(event) in FORBIDDEN_PRTS_TASKS
    ]
    replay_chains = [event for event in events if event.taskchain in FORBIDDEN_TASKCHAINS]
    copilot_actions = [event for event in events if event.what == "CopilotAction"]
    custom_visual_skills = [
        event
        for event in events
        if event.message.name == "SubTaskStart"
        and _event_task(event).startswith(("Omni@Battle-Skill-", "Battle-Skill-"))
        and not _event_task(event).endswith("Confirm")
    ]
    skill_daemon = []
    for event in copilot_actions:
        details = event.details.get("details", {})
        if isinstance(details, dict) and details.get("action") == "SkillDaemon":
            skill_daemon.append(event)
    return {
        "event_count": len(events),
        "use_prts_or_prts_count": len(forbidden_prts),
        "replay_taskchain_count": len(replay_chains),
        "skill_daemon_count": len(skill_daemon),
        "single_step_action_event_count": len(copilot_actions),
        "custom_visual_skill_event_count": len(custom_visual_skills),
        "semantic_action_event_count": len(copilot_actions) + len(custom_visual_skills),
        "forbidden_prts_tasks": sorted({_event_task(item) or item.what for item in forbidden_prts}),
        "replay_taskchains": sorted({item.taskchain for item in replay_chains}),
        "passed": not forbidden_prts and not replay_chains and not skill_daemon,
    }


def _event_dict(event: MaaEvent, started: float) -> dict[str, Any]:
    return {
        "message": event.message.name,
        "what": event.what,
        "taskchain": event.taskchain,
        "task": _event_task(event),
        "details": event.details,
        "elapsed_seconds": round(event.monotonic - started, 3),
    }


def _state_dict(frame: BattleFrameEnvelopeV1) -> dict[str, Any]:
    return {
        "capture_requested_monotonic": frame.capture_requested_monotonic,
        "captured_monotonic": frame.captured_monotonic,
        "refresh_succeeded": frame.refresh_succeeded,
        "source": frame.source,
        "active_game_seconds": frame.active_game_seconds,
        "state": frame.state.model_dump(mode="json", by_alias=True),
    }


def _action_dict(outcome: Any) -> dict[str, Any]:
    return {
        "action": outcome.attempt.call.action_name,
        "details": outcome.attempt.call.details,
        "verified": outcome.verified,
        "verdict": outcome.result.verdict.value,
        "execution_count": outcome.execution_count,
        "observation_count": outcome.observation_count,
        "action_reexecuted": outcome.action_reexecuted,
        "before_frame_sha256": outcome.attempt.before.state.frame.sha256,
        "after_frame_sha256": outcome.result.selected_frame_sha256,
        "checks": [asdict(item) for item in outcome.result.checks],
    }


def _find_no_leak_completion_frame(
    observations: Sequence[BattleFrameEnvelopeV1],
) -> BattleFrameEnvelopeV1 | None:
    """Return the last fresh 1-7 HUD proving 41/41 with all ten lives."""

    for envelope in reversed(observations):
        global_state = envelope.state.global_state
        if not all(
            item.known
            for item in (
                global_state.kills,
                global_state.total_enemies,
                global_state.life_points,
            )
        ):
            continue
        kills = int(global_state.kills.value)
        total = int(global_state.total_enemies.value)
        life = int(global_state.life_points.value)
        if total == 41 and kills == total and life == 10:
            return envelope
    return None


def _read_settlement_frame_truth(
    image_path: Path,
    *,
    reader: Callable[[Path, SourcePixelRect], Any] | None = None,
) -> dict[str, Any]:
    read = (reader or RapidOCRBattleReader())(
        image_path,
        SourcePixelRect(x=0, y=0, width=1280, height=720),
    )
    tokens = tuple(
        {
            "text": str(token.text),
            "confidence": float(token.confidence),
        }
        for token in read.tokens
    )
    compact = tuple("".join(item["text"].split()).upper() for item in tokens)
    return {
        "detected": any("行动结束" in text or "MISSIONRESULTS" in text for text in compact),
        "stage_code": "1-7" in compact,
        "proxy_save_prompt": any("保存" in text and "代理指挥阵容" in text for text in compact),
        "tokens": tokens,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
        encoding="utf-8",
    )


def _compile_factory(repository_root: Path):
    source = repository_root / "var" / "vendor" / "MAA-src"
    resource = repository_root / "var" / "vendor" / "MAA" / "resource"
    registry = load_maa_semantic_registry(source, resource)
    catalog = build_maa_contract_catalog(registry, source)
    authorization = OperationAuthorization(allow_account_state_change=True)

    def compile_decision(decision: BattleDecisionV1):
        if decision.action_name is None or decision.details is None:
            raise ValueError("cannot compile a non-actionable decision")
        return compile_battle_action(
            catalog,
            decision.action_name,
            decision.details,
            authorization=authorization,
        )

    return compile_decision


def _dismiss_settlement_to_stage(
    operations: ArknightsOperations,
    stage: str,
) -> tuple[bool, bool, bool, bool]:
    _try_task(operations, "Omni@Settlement-Dismiss", timeout=18.0)
    detail, _events = _try_task(operations, "Omni@StageDetail-Detect", timeout=12.0)
    detail_code = False
    if detail:
        detail_code, _events = _try_task(
            operations, f"Omni@StageDetail-{stage}-Detect", timeout=12.0
        )
        return detail, detail_code, False, False
    chapter, _events = _try_task(operations, "Omni@ChapterScreen-Detect", timeout=12.0)
    chapter_code = False
    if chapter:
        chapter_code, _events = _try_task(operations, "Omni@Chapter-Stage-1-7-Detect", timeout=12.0)
    return detail, detail_code, chapter, chapter_code


def _recover_chapter_to_home(operations: ArknightsOperations) -> bool:
    chapter, _events = _try_task(operations, "Omni@ChapterScreen-Detect", timeout=12.0)
    if not chapter:
        return False
    moved, _events = _try_task(operations, "Omni@NavOverlay-GoHome", timeout=12.0)
    if not moved:
        opened, _events = _try_task(operations, "Omni@Chapter-GoHome", timeout=12.0)
        if not opened:
            raise RuntimeError("chapter screen was confirmed but navigation click failed")
        moved, _events = _try_task(operations, "Omni@NavOverlay-GoHome", timeout=12.0)
        if not moved:
            raise RuntimeError("navigation overlay could not select HOME")
    home, _events = _try_task(operations, "Omni@Home-Detect", timeout=20.0)
    if not home:
        raise RuntimeError("chapter navigation completed but HOME was not confirmed")
    return True


def _recover_to_home(operations: ArknightsOperations) -> list[str]:
    """清掉前一失败样本留下的暂停战斗，不借用 SkillDaemon 或代理完成它。"""

    log: list[str] = []
    home, _events = _try_task(operations, "Omni@Home-Detect", timeout=12.0)
    if home:
        return ["home already confirmed"]
    settlement, _events = _try_task(operations, "Omni@Settlement-Detect", timeout=12.0)
    if settlement:
        stars3, _events = _try_task(operations, "Omni@Settlement-Stars3", timeout=12.0)
        stage_code, _events = _try_task(
            operations, "Omni@Settlement-Stage-1-7-Detect", timeout=12.0
        )
        log.append(
            "dismissed prior settlement after read-only truth: "
            f"stars3={stars3}, stage_1_7={stage_code}"
        )
        dismissed, _events = _try_task(operations, "Omni@Settlement-Dismiss", timeout=12.0)
        if not dismissed:
            raise RuntimeError("confirmed settlement could not be dismissed once")
    paused, _events = _try_task(operations, "Omni@Battle-Paused", timeout=10.0)
    active = paused
    if not active:
        active, _events = _try_task(operations, "Omni@Battle-Detect", timeout=10.0)
    if active:
        if not paused:
            operations.pause_battle()
            log.append("paused active battle for cleanup")
        opened, _events = _try_task(operations, "Omni@Battle-OpenSettings", timeout=12.0)
        if not opened:
            raise RuntimeError("active battle cleanup could not open settings")
        operations.abandon_battle()
        log.append("abandoned previous incomplete battle without proxy/SkillDaemon")
        _try_task(operations, "Omni@MissionFailed-Detect", timeout=12.0)
        for _attempt in range(3):
            _try_task(operations, "Omni@Settlement-Dismiss", timeout=12.0)
            home, _events = _try_task(operations, "Omni@Home-Detect", timeout=10.0)
            if home:
                return log + ["home confirmed after active battle cleanup"]
    if _recover_chapter_to_home(operations):
        return log + ["home confirmed from chapter screen"]
    try:
        operations.go_to_main_screen()
    except MaaTaskFailed:
        if _recover_chapter_to_home(operations):
            return log + ["home confirmed from chapter screen after StartUp fallback"]
        raise
    home, _events = _try_task(operations, "Omni@Home-Detect", timeout=20.0)
    if not home:
        raise RuntimeError("could not confirm HOME before live battle")
    return log + ["home confirmed through StartUp recovery"]


def run_live_battle(
    *,
    output_dir: Path,
    adb_path: Path,
    address: str,
    stage: str = "1-7",
    maa_root: Path | None = None,
    cadence_min: float = 2.5,
    cadence_max: float = 3.5,
    cadence_seed: int = 0,
    inject_stale_once: bool = False,
) -> dict[str, Any]:
    if stage != "1-7":
        raise ValueError("the online policy is currently frozen only for stage 1-7")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"output evidence directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "frames"
    checkpoints_dir = output_dir / "checkpoints"
    frames_dir.mkdir()
    checkpoints_dir.mkdir()
    repository_root = Path(__file__).resolve().parents[6]
    started = time.monotonic()
    receipt: dict[str, Any] = {
        "schema": SCHEMA,
        "stage": stage,
        "started_at": _utc_now(),
        "success": False,
        "resource_boundary": {
            "medicine": 0,
            "expiring_medicine": 0,
            "stone": 0,
            "premium_currency": 0,
            "battles": 1,
        },
        "control_contract": {
            "game_proxy": "forbidden",
            "fight_task": "forbidden",
            "copilot_replay_task": "forbidden",
            "video_or_recording": "forbidden",
            "low_level_executor": "Custom + SingleStep/action only",
        },
        "cadence": {
            "minimum_seconds": cadence_min,
            "maximum_seconds": cadence_max,
            "seed": cadence_seed,
        },
    }
    operator: MaaOperator | None = None
    control: BattleControlResultV1 | None = None
    pacer: FrozenFramePacer | None = None
    injector: StaleAfterActionSampler | None = None
    error: Exception | None = None
    try:
        operator = MaaOperator.create(root=maa_root)
        operator.set_touch_mode(TouchMode.MAATOUCH)
        operator.set_deployment_with_pause(True)
        if not operator.connect(adb_path, address, "General"):
            raise RuntimeError(f"cannot connect MAA to {address}")
        receipt["maa_version"] = operator.version()
        receipt["device"] = address
        operations = ArknightsOperations(operator)
        receipt["startup_recovery"] = _recover_to_home(operations)
        operations.battle_bind_stage(stage)
        entry = BattleEntryController(
            operations,
            capture=_checkpoint_capture(operator, checkpoints_dir),
        ).enter_from_home(stage, prefer_terminal=True)
        receipt["entry"] = {
            "battle_confirmed": entry.battle_confirmed,
            "battle_frozen": entry.battle_frozen,
            "battle_begin_count": entry.battle_begin_count,
            "used_terminal": entry.used_terminal,
            "checkpoints": [
                {
                    "label": item.label,
                    "task_name": item.task_name,
                    "matched": item.matched,
                    "evidence_ref": item.evidence_ref,
                    "started_monotonic": item.started_monotonic,
                    "finished_monotonic": item.finished_monotonic,
                    "duration_seconds": (
                        None
                        if item.started_monotonic is None
                        or item.finished_monotonic is None
                        else round(
                            item.finished_monotonic - item.started_monotonic,
                            6,
                        )
                    ),
                    "events": [
                        {
                            "message": event.message.name,
                            "what": event.what,
                            "taskchain": event.taskchain,
                            "monotonic": event.monotonic,
                            "since_checkpoint_start_seconds": (
                                None
                                if item.started_monotonic is None
                                else round(
                                    event.monotonic - item.started_monotonic,
                                    6,
                                )
                            ),
                        }
                        for event in item.events
                    ],
                }
                for item in entry.checkpoints
            ],
        }

        battle_running_gate = BattleRunningTemplateGate(
            operator.paths.root
            / "resource"
            / "template"
            / "Battle"
            / "BattleFlag"
            / "BattleOfficiallyBegin.png"
        )
        pacer = FrozenFramePacer(
            operations,
            ObservationCadence(cadence_min, cadence_max, seed=cadence_seed),
            battle_running_gate,
            BattlePausedOverlayGate(),
        )
        if entry.battle_frozen:
            pacer.adopt_entry_freeze()
        else:
            pacer.freeze_after_entry()
        receipt["entry_frozen_before_vision_init"] = True
        receipt["entry_frozen_by_maa_chain"] = entry.battle_frozen
        grid = load_stage_grid(operator.paths.tile_pos_dir, code=stage)
        parser = BattleStateParser(stage_grid=grid)
        base_sampler = MaaBattleFrameSampler(
            operator,
            frames_dir,
            parser=parser,
            before_capture=pacer.before_capture,
            after_capture=pacer.after_capture,
            after_parse=pacer.observe_frame,
            active_game_clock=lambda: pacer.active_game_seconds,
        )
        injector = StaleAfterActionSampler(
            base_sampler,
            enabled=inject_stale_once,
            on_fresh_frame=pacer.observe_frame,
        )

        action_calls = 0

        def execute(call):  # noqa: ANN001, ANN202
            nonlocal action_calls
            action_calls += 1
            if call.action_name == "Skill":
                location = call.details.get("location")
                if not isinstance(location, list) or len(location) != 2:
                    raise ValueError("visual Skill requires a two-value location")
                events = pacer.execute_live_action(
                    lambda: operations.use_skill_at(
                        int(location[0]),
                        int(location[1]),
                    ),
                    lambda: battle_running_gate.matches_png(operator.capture_png(fresh=True)),
                )
            else:
                events = operations.run_prepared_battle_actions(
                    [call],
                    timeout=45.0,
                )
            if inject_stale_once and action_calls == 1:
                injector.arm()
            return events

        action_loop = BattleActionLoop(
            injector,
            execute,
            max_after_observations=4,
        )
        policy = OneSevenOnlinePolicy(maximum_deployments=8)
        control = LiveBattleController(
            injector,
            action_loop,
            policy,
            _compile_factory(repository_root),
            cadence=ObservationCadence(0.0),
            sleeper=lambda _delay: None,
            maximum_cycles=120,
            maximum_seconds=1800.0,
        ).run()

        settlement_visual: dict[str, Any] = {
            "detected": False,
            "stage_code": False,
            "proxy_save_prompt": False,
            "tokens": (),
        }
        settlement_evidence_path: Path | None = None
        if control.status is BattleControlStatus.SCENE_CHANGED and control.observations:
            settlement_evidence_path = Path(control.observations[-1].state.frame.path)
            try:
                settlement_visual = _read_settlement_frame_truth(settlement_evidence_path)
            except Exception as exc:  # noqa: BLE001 - visual truth fails closed
                settlement_visual["error"] = f"{type(exc).__name__}: {exc}"
        settlement_ocr = False
        if not settlement_visual["detected"]:
            settlement_ocr, _events = _try_task(operations, "Omni@Settlement-Detect", timeout=20.0)
        settlement = bool(settlement_visual["detected"] or settlement_ocr)
        if settlement_visual["detected"] and settlement_evidence_path is not None:
            receipt["settlement_frame"] = str(settlement_evidence_path.resolve())
        elif settlement:
            receipt["settlement_frame"] = _write_png(operator, checkpoints_dir / "settlement.png")
        else:
            receipt["settlement_frame"] = None
        stars3, _events = (
            _try_task(operations, "Omni@Settlement-Stars3", timeout=15.0)
            if settlement
            else (False, ())
        )
        settlement_stage_code = bool(settlement_visual["stage_code"])
        if settlement and not settlement_stage_code:
            settlement_stage_code, _events = _try_task(
                operations,
                "Omni@Settlement-Stage-1-7-Detect",
                timeout=15.0,
            )
        failed, _events = (
            (False, ())
            if settlement
            else _try_task(operations, "Omni@MissionFailed-Detect", timeout=12.0)
        )
        stage_detail = False
        stage_code = False
        chapter_after_win = False
        chapter_stage_code = False
        proxy_save_rejected = False
        if settlement:
            if settlement_visual["proxy_save_prompt"]:
                proxy_save_rejected, _events = _try_task(
                    operations,
                    "Omni@Settlement-RejectProxySave",
                    timeout=10.0,
                )
            (
                stage_detail,
                post_stage_code,
                chapter_after_win,
                chapter_stage_code,
            ) = _dismiss_settlement_to_stage(operations, stage)
            stage_code = bool(settlement_stage_code or post_stage_code or chapter_stage_code)
            if stage_detail:
                receipt["stage_detail_frame"] = _write_png(
                    operator, checkpoints_dir / "stage-detail-after-win.png"
                )
            elif chapter_after_win:
                receipt["chapter_after_win_frame"] = _write_png(
                    operator, checkpoints_dir / "chapter-after-win.png"
                )
        elif control.status is BattleControlStatus.SCENE_CHANGED:
            chapter_after_win, _events = _try_task(
                operations, "Omni@ChapterScreen-Detect", timeout=12.0
            )
            if chapter_after_win:
                chapter_stage_code, _events = _try_task(
                    operations,
                    "Omni@Chapter-Stage-1-7-Detect",
                    timeout=12.0,
                )
                receipt["chapter_after_win_frame"] = _write_png(
                    operator, checkpoints_dir / "chapter-after-win.png"
                )
                stage_code = chapter_stage_code

        unique_frames = {item.state.frame.sha256 for item in control.observations}
        no_leak_completion = _find_no_leak_completion_frame(control.observations)
        equivalent_victory = bool(
            no_leak_completion is not None and chapter_after_win and chapter_stage_code
        )
        decisions_for_actions = [item for item in control.decisions if item.actionable]
        actions = [_action_dict(item) for item in control.actions]
        decision_frame_match = len(decisions_for_actions) == len(actions) and all(
            decision.referenced_state.get("frame_sha256") == action["before_frame_sha256"]
            for decision, action in zip(decisions_for_actions, actions, strict=True)
        )
        life_values = [
            int(item.state.global_state.life_points.value)
            for item in control.observations
            if item.state.global_state.life_points.known
        ]
        event_audit = audit_events(operator.events)
        gates = {
            "scene_changed_after_battle": control.status is BattleControlStatus.SCENE_CHANGED,
            "settlement_or_equivalent_victory_truth": settlement or equivalent_victory,
            "three_stars_or_no_leak_41_of_41": stars3 or equivalent_victory,
            "mission_failed_absent": not failed,
            "post_battle_navigation_confirmed": stage_detail or chapter_after_win,
            "stage_code_after_win": stage_code,
            "minimum_20_fresh_observations": len(unique_frames) >= 20,
            "minimum_5_verified_actions": control.verified_action_count >= 5,
            "minimum_3_deployments": control.deployment_count >= 3,
            "all_action_effects_verified": all(item.verified for item in control.actions),
            "no_action_reexecution": all(
                not item.action_reexecuted and item.execution_count == 1 for item in control.actions
            ),
            "decision_frame_matches_action_before": decision_frame_match,
            "life_never_dropped": bool(life_values) and min(life_values) == 10,
            "forbidden_runtime_events_absent": event_audit["passed"],
            "fault_injection_exactly_once_if_requested": (
                injector.injected == 1 if inject_stale_once else injector.injected == 0
            ),
        }
        receipt.update(
            control={
                "status": control.status.value,
                "reason": control.reason,
                "observation_count": len(control.observations),
                "unique_frame_count": len(unique_frames),
                "decision_count": len(control.decisions),
                "verified_action_count": control.verified_action_count,
                "deployment_count": control.deployment_count,
                "delays": pacer.delays,
                "resume_failures": pacer.resume_failures,
                "pause_failures": pacer.pause_failures,
                "scene_transition_frames": pacer.scene_transition_frames,
                "unexpected_paused_frames": pacer.unexpected_paused_frames,
                "pause_metrics": pacer.pause_metrics(),
            },
            temporal_cost={
                "active_game_seconds": policy.memory.active_game_seconds,
                "last_cost_progress": policy.memory.last_cost_progress,
                "cost_capped": policy.memory.cost_capped,
                "net_dp_per_second": policy.memory.net_dp_per_second,
                "dp_regime": policy.memory.dp_regime,
                "rate_sample_count": len(policy.memory.net_dp_rates),
                "seconds_until_next_dp": policy.memory.seconds_until_next_dp,
                "integer_cost_required_for_deploy": True,
                "progress_or_time_can_authorize_deploy": False,
            },
            settlement={
                "detected": settlement,
                "stars3": stars3,
                "stage_code_on_settlement": settlement_stage_code,
                "mission_failed": failed,
                "stage_detail": stage_detail,
                "stage_code": stage_code,
                "visual_truth": settlement_visual,
                "proxy_save_rejected": proxy_save_rejected,
                "chapter_after_win": chapter_after_win,
                "chapter_stage_code": chapter_stage_code,
                "no_leak_completion": no_leak_completion is not None,
                "no_leak_completion_frame": (
                    str(no_leak_completion.state.frame.path)
                    if no_leak_completion is not None
                    else None
                ),
                "equivalent_victory": equivalent_victory,
            },
            fault_injection={
                "requested": inject_stale_once,
                "injected_count": injector.injected,
                "mode": "discard one fresh parsed state after first action; reobserve only",
            },
            event_audit=event_audit,
            gates=gates,
        )
        _write_jsonl(
            output_dir / "states.jsonl",
            [_state_dict(item) for item in control.observations],
        )
        _write_jsonl(
            output_dir / "decisions.jsonl",
            [item.as_dict() for item in control.decisions],
        )
        _write_json(output_dir / "actions.json", actions)
        receipt["success"] = all(gates.values())
    except Exception as exc:  # noqa: BLE001 - evidence must survive every live failure
        error = exc
        receipt["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        if operator is not None:
            try:
                receipt["failure_frame"] = _write_png(operator, checkpoints_dir / "failure.png")
            except Exception as frame_exc:  # noqa: BLE001
                receipt["failure_frame_error"] = str(frame_exc)
    finally:
        if pacer is not None:
            receipt["pause_metrics"] = pacer.pause_metrics()
        if operator is not None:
            receipt["event_audit"] = audit_events(operator.events)
            _write_jsonl(
                output_dir / "events.jsonl",
                [_event_dict(item, started) for item in operator.events],
            )
            try:
                operator.stop()
            except Exception:  # noqa: BLE001 - receipt already has runtime truth
                pass
        receipt["finished_at"] = _utc_now()
        receipt["duration_seconds"] = round(time.monotonic() - started, 3)
        _write_json(output_dir / "receipt.json", receipt)
    if error is not None:
        raise error
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", default="1-7")
    parser.add_argument(
        "--adb-path",
        type=Path,
        default=Path(r"C:\Program Files\Netease\MuMu\nx_main\adb.exe"),
    )
    parser.add_argument("--address", default="emulator-5554")
    parser.add_argument("--maa-root", type=Path)
    parser.add_argument("--cadence-min", type=float, default=2.5)
    parser.add_argument("--cadence-max", type=float, default=3.5)
    parser.add_argument("--cadence-seed", type=int, default=0)
    parser.add_argument("--inject-stale-once", action="store_true")
    parser.add_argument(
        "--max-cpus",
        type=int,
        default=_default_cpu_budget(),
        help="CPU affinity budget for this heavy run (default: 4 or OMNI_BATTLE_MAX_CPUS).",
    )
    parser.add_argument(
        "--allow-concurrent",
        action="store_true",
        help="Explicitly allow another live controller on the same emulator.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_cpus < 1:
        raise SystemExit("--max-cpus must be at least 1")
    with _battle_process_budget(
        args.address,
        max_cpus=args.max_cpus,
        allow_concurrent=args.allow_concurrent,
    ) as resource_budget:
        receipt = run_live_battle(
            output_dir=args.output,
            adb_path=args.adb_path,
            address=args.address,
            stage=args.stage,
            maa_root=args.maa_root,
            cadence_min=args.cadence_min,
            cadence_max=args.cadence_max,
            cadence_seed=args.cadence_seed,
            inject_stale_once=args.inject_stale_once,
        )
        receipt["resource_budget"] = resource_budget
        _write_json(args.output / "receipt.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
