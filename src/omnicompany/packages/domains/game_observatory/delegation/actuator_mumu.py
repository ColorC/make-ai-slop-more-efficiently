# [OMNI] origin=codex created_by=codex intent="provide a direct MuMu IPC actuator backend"
"""MuMu renderer IPC actuator with raw replay capture."""

from __future__ import annotations

import hashlib
import io
import math
import time
import uuid
from collections.abc import Callable
from typing import Any

from PIL import Image

from ..replay import ActionRecordV1, RunHandle
from .actuator import ActuatorResolutionMismatchError, EXPECTED_RESOLUTION
from .contracts import (
    ActionRequestV1,
    ActionResultV1,
    ActuatorConnectionV1,
    ActuatorTimingV1,
)
from .mumu_ipc import MuMuIpc, MuMuIpcError


AdbTapProbe = Callable[[int, int], dict[str, Any]]
AppRestartProbe = Callable[[], dict[str, Any]]
# Launcher-intent relaunch of the game process (one adb call, no HOME and no
# force-stop) plus a pidof-style process check; the pair behind connect-class
# failure recovery.
GameLaunchProbe = Callable[[], dict[str, Any]]
AppAliveProbe = Callable[[], bool]
# Given one captured RGBA frame, return a harmless point to tap to prove input
# landed, or None when the frame is not a surface this may tap at all.
RecoveryVerifyPoint = Callable[[bytes, int, int], "tuple[int, int] | None"]
RECOVERY_TAP_INTENT = "recovery:verify-input-tap"
EXPECTED_SCREEN_SETTLE_TIMEOUT_MS = 5_000
_SAFE_TAP_INTENT_PREFIXES = (
    "popup:",
    "navigation:",
    "main-task:",
)
_BUSINESS_TAP_INTENT_PREFIXES = ("generate:", "deliver:")
# How many unchanged business taps in a row it takes to call the renderer dead.
# A business tap is judged on a narrow evidence set -- the energy counter and
# the prompt box for a production, the coin counter for a delivery -- and a
# free producer (Spread_CostEnergy == 0) moves none of them, so two unchanged
# taps is an ordinary round, not a frozen renderer.  The save is the authority
# here and it clears this streak every round through
# observe_business_save_evidence, so a streak this long has to span rounds the
# save also declined to vouch for.  A non-business tap keeps the old count of
# two, because its whole-frame comparison means what it says.
LIVE_FRAME_LADDER_STREAK = 5
SAFE_TAP_LADDER_STREAK = 2


class MuMuIpcInputDeadError(RuntimeError):
    """Renderer IPC accepted input but produced no visual effect after recovery."""


class MuMuIpcInputRestartRecoveredError(RuntimeError):
    """Input was dead, an app restart brought it back, and this action was lost.

    F4-07 open item 2.  The action that detected the freeze never landed and the
    app has been relaunched underneath it, so the round it belonged to must be
    abandoned and re-observed rather than continued against a state the plan no
    longer describes.  This is a recovery, not a failure: the caller counts it,
    logs it and keeps playing.
    """


def _elapsed_ms(started_ns: int) -> float:
    return max((time.perf_counter_ns() - started_ns) / 1_000_000, 0.000001)


def _dhash64(rgba: bytes, width: int, height: int) -> int:
    with Image.frombytes("RGBA", (width, height), rgba) as source:
        gray = source.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
        pixels = list(gray.getdata())
    value = 0
    for row in range(8):
        offset = row * 9
        for col in range(8):
            value = (value << 1) | int(pixels[offset + col] < pixels[offset + col + 1])
    return value


def _dhash_distance(
    before: bytes,
    after: bytes,
    width: int,
    height: int,
) -> int:
    return (_dhash64(before, width, height) ^ _dhash64(after, width, height)).bit_count()


def _region_dhash64(
    rgba: bytes,
    width: int,
    height: int,
    box: tuple[int, int, int, int],
) -> int:
    with Image.frombytes("RGBA", (width, height), rgba) as source:
        left, top, right, bottom = box
        region = source.crop(
            (
                max(0, left),
                max(0, top),
                min(width, right),
                min(height, bottom),
            )
        )
        gray = region.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
        pixels = list(gray.getdata())
    value = 0
    for row in range(8):
        offset = row * 9
        for col in range(8):
            value = (value << 1) | int(pixels[offset + col] < pixels[offset + col + 1])
    return value


def _business_tap_evidence_distance(
    request: ActionRequestV1,
    before: bytes,
    after: bytes,
    width: int,
    height: int,
) -> int:
    """Ignore ambient animation and inspect only semantic evidence regions."""

    prompt_box = (210, 1730, 875, 1900)
    if request.intent.startswith("generate:"):
        # A production tap either selects the generator (prompt changes) or spends
        # energy.  Generator sparkles are deliberately outside this evidence set.
        boxes = (prompt_box, (330, 40, 405, 85))
    else:
        # A confirmed order delivery necessarily changes the coin number.
        boxes = ((575, 40, 650, 85),)
    return max(
        (
            _region_dhash64(before, width, height, box) ^ _region_dhash64(after, width, height, box)
        ).bit_count()
        for box in boxes
    )


def _tap_evidence_distance(
    request: ActionRequestV1,
    before: bytes,
    after: bytes,
    width: int,
    height: int,
) -> int:
    if request.intent.startswith(_BUSINESS_TAP_INTENT_PREFIXES):
        return _business_tap_evidence_distance(
            request,
            before,
            after,
            width,
            height,
        )
    return _dhash_distance(before, after, width, height)


# F4-09.  What an input is allowed to land on.  The board takes anything; a
# registered red-line surface takes only its own registered close; any other
# surface takes only the intents whose whole purpose is to leave it.
BOARD_SURFACE = "main-board"
SURFACE_TRAVERSAL_INTENTS = (
    "popup:",
    "navigation:",
    "main-task:",
    "wait:",
)
# These are sentinel results from the shared screen guard, rather than
# registered surfaces on which an action can be judged settled.  In
# particular, an exclusion-only predicate must not treat an unresolved frame
# as success merely because it is different from the excluded name.
NON_SETTLED_SCREEN_NAMES = frozenset({"unknown", "transition", "overlay-unknown"})


class MuMuIpcActuator:
    """Execute capture and touch directly through MuMu's renderer IPC."""

    def __init__(
        self,
        ipc: MuMuIpc,
        run: RunHandle,
        *,
        post_frame_delay_ms: int = 150,
        settle_timeout_ms: int = 1500,
        sleep: Callable[[float], None] = time.sleep,
        adb_tap_probe: AdbTapProbe | None = None,
        app_restart_probe: AppRestartProbe | None = None,
        recovery_verify_point: RecoveryVerifyPoint | None = None,
        app_restart_settle_s: float = 25.0,
        max_app_restarts: int = 2,
        game_launch_probe: GameLaunchProbe | None = None,
        app_alive_probe: AppAliveProbe | None = None,
        game_relaunch_wait_s: float = 30.0,
        game_relaunch_poll_s: float = 2.0,
        max_game_relaunches: int = 2,
        redline_pre_frame_guard: Callable[[bytes, int, int, str], str | None] | None = None,
        forbidden_surface_names: frozenset[str] | None = None,
        authorized_purchase_intents: dict[str, str] | None = None,
    ) -> None:
        if post_frame_delay_ms < 0 or settle_timeout_ms < 0:
            raise ValueError("actuator timing values must be non-negative")
        if app_restart_settle_s < 0:
            raise ValueError("app_restart_settle_s must be non-negative")
        if max_app_restarts < 0:
            raise ValueError("max_app_restarts must be non-negative")
        if game_relaunch_wait_s < 0:
            raise ValueError("game_relaunch_wait_s must be non-negative")
        if game_relaunch_poll_s <= 0:
            raise ValueError("game_relaunch_poll_s must be positive")
        if max_game_relaunches < 0:
            raise ValueError("max_game_relaunches must be non-negative")
        self.ipc = ipc
        self.run = run
        self.post_frame_delay_ms = post_frame_delay_ms
        self.settle_timeout_ms = settle_timeout_ms
        self.sleep = sleep
        self.adb_tap_probe = adb_tap_probe
        self.app_restart_probe = app_restart_probe
        self.recovery_verify_point = recovery_verify_point
        self.app_restart_settle_s = app_restart_settle_s
        self.max_app_restarts = max_app_restarts
        self.app_restart_recoveries = 0
        self.game_launch_probe = game_launch_probe
        self.app_alive_probe = app_alive_probe
        self.game_relaunch_wait_s = game_relaunch_wait_s
        self.game_relaunch_poll_s = game_relaunch_poll_s
        self.max_game_relaunches = max_game_relaunches
        self.game_relaunch_recoveries = 0
        # F4-09.  The frame the audit judges an input by is the one captured
        # here, immediately before the input is sent.  Until now the decision
        # used the screen guard's frame from a second or two earlier, and on
        # 2026-09-07 00:30:57Z that gap was enough: the guard read the board at
        # 0.994 confidence, the update-game-modal came back 1.9 s later, and a
        # generator tap went out on top of it.  Judging the same frame the
        # audit judges closes the gap.
        self.redline_pre_frame_guard = redline_pre_frame_guard
        self.forbidden_surface_names = frozenset(forbidden_surface_names or ())
        # 2026-09-17.  The one purchase the account owner authorized on a
        # red-line surface (buy energy with gems, twice a day).  The daily limit
        # is the loop's to enforce; this only lets that exact intent through.
        self.authorized_purchase_intents = dict(authorized_purchase_intents or {})
        # F4-09.  Set by the loop while the board is full under an unfollowable
        # guide.  The authoritative save has already proved the board is there
        # and has no free cell, so the two actions that free one are allowed
        # even though the frame still reads as the guide's surface.  Never set
        # for a registered red-line surface: those keep their own rule.
        self.board_full_under_guide_intents: tuple[str, ...] = ()
        self.redline_refusals: list[dict[str, Any]] = []
        self.connection: ActuatorConnectionV1 | None = None
        self.input_health_events: list[dict[str, Any]] = []
        self._unchanged_safe_taps = 0
        self._unchanged_business_taps = 0
        self._tap_visual_evidence: dict[str, bool] = {}
        self._closed = True

    def connect(self) -> ActuatorConnectionV1:
        if self.connection is not None and not self._closed:
            return self.connection
        started = time.perf_counter_ns()
        self._call_with_game_relaunch("connect", self.ipc.connect)
        resolution = self.ipc.query_resolution()
        if resolution != EXPECTED_RESOLUTION:
            self.ipc.disconnect()
            raise ActuatorResolutionMismatchError(
                f"expected {EXPECTED_RESOLUTION[0]}x{EXPECTED_RESOLUTION[1]}, "
                f"got {resolution[0]}x{resolution[1]}"
            )
        self.connection = ActuatorConnectionV1(
            serial=f"mumu:{self.ipc.instance_index}",
            adb_path=str(self.ipc.dll_path),
            maafw_version="not-used",
            screencap_method="MuMuRendererIPC",
            input_method="MuMuRendererIPC",
            available_screencap_methods=["MuMuRendererIPC"],
            available_input_methods=["MuMuRendererIPC"],
            resolution=resolution,
            connect_ms=_elapsed_ms(started),
            controller_info={
                "instance_index": self.ipc.instance_index,
                "app_package": self.ipc.app_package,
                "display_id": self.ipc.display_id,
            },
        )
        self._closed = False
        return self.connection

    def _require_connected(self) -> None:
        if self._closed or self.connection is None:
            raise RuntimeError("MuMu IPC actuator is disconnected")

    def _update_connection_display(self, resolution: tuple[int, int]) -> None:
        if resolution != EXPECTED_RESOLUTION:
            raise ActuatorResolutionMismatchError(
                f"expected {EXPECTED_RESOLUTION[0]}x{EXPECTED_RESOLUTION[1]}, "
                f"got {resolution[0]}x{resolution[1]}"
            )
        if self.connection is not None:
            self.connection.resolution = resolution
            self.connection.controller_info["display_id"] = self.ipc.display_id

    def _reconnect_and_measure(self) -> tuple[int, tuple[int, int]]:
        self.ipc.reconnect()
        return self.ipc.display_id, self.ipc.query_resolution()

    def _call_with_game_relaunch(self, operation: str, call: Callable[[], Any]) -> Any:
        """Retry a connect-class IPC call after relaunching a dead game process.

        2026-09-11 ruling, after segment CA of F4-10-long-S1-20260911T184029Z
        and a second identical stop the same night: the game process died 5.5
        hours into a healthy session and every later segment start -- or
        mid-segment reconnect -- met ``nemu_get_display_id failed: ret=-1``,
        which rr_play could only surface as an exit-2 crash the guardian then
        escalated.  A dead process needs no kill and no HOME: fire the
        launcher intent, poll pidof until the process shows up (bounded by
        ``game_relaunch_wait_s``), and retry the IPC call, at most
        ``max_game_relaunches`` times.  With no probes wired, or with the
        retries spent, the original error travels its old path unchanged.
        """

        try:
            return call()
        except MuMuIpcError as error:
            original = error
            if self.game_launch_probe is None or self.app_alive_probe is None:
                raise
        attempts: list[dict[str, Any]] = []
        polls_per_attempt = max(
            1, math.ceil(self.game_relaunch_wait_s / self.game_relaunch_poll_s)
        )
        for attempt in range(1, self.max_game_relaunches + 1):
            entry: dict[str, Any] = {"attempt": attempt}
            try:
                entry["launch"] = self.game_launch_probe()
            except Exception as exc:  # noqa: BLE001 - recorded; the cap still binds
                entry["error"] = f"{type(exc).__name__}: {exc}"
                attempts.append(entry)
                continue
            alive = False
            polls = 0
            for _ in range(polls_per_attempt):
                try:
                    seen = self.app_alive_probe()
                except Exception:  # noqa: BLE001 - "cannot tell" is not "gone"
                    seen = True
                if seen:
                    alive = True
                    break
                polls += 1
                self.sleep(self.game_relaunch_poll_s)
            entry["process_alive"] = alive
            entry["wait_polls"] = polls
            try:
                result = call()
            except MuMuIpcError as exc:
                entry["error"] = str(exc)
                attempts.append(entry)
                continue
            entry["recovered"] = True
            attempts.append(entry)
            self.game_relaunch_recoveries += 1
            self._record_input_health(
                {
                    "event": "game_relaunch_recovery",
                    "operation": operation,
                    "attempts": attempts,
                }
            )
            self.run.alert(
                f"game_relaunch_recovery: {operation} failed with the game "
                "process gone; the launcher relaunch and the retry connected"
            )
            return result
        self._record_input_health(
            {
                "event": "game_relaunch_failed",
                "operation": operation,
                "attempts": attempts,
            }
        )
        self.run.alert(
            f"game_relaunch_failed: {operation} still failing after "
            f"{self.max_game_relaunches} relaunches: {original}"
        )
        raise original

    def _record_input_health(self, event: dict[str, Any]) -> None:
        self.input_health_events.append(event)
        note = getattr(self.run, "note", None)
        if callable(note):
            note(f"MuMu IPC input health: {event}")

    def refresh_display_id(self, *, reason: str) -> int:
        """Refresh the app display after a registered transition completes."""

        self._require_connected()
        previous = self.ipc.display_id
        recovery = "display_refresh"
        try:
            display_id = self.ipc.refresh_display_id()
            resolution = self.ipc.query_resolution()
        except Exception:
            recovery = "handle_reconnect"
            display_id, resolution = self._call_with_game_relaunch(
                "reconnect", self._reconnect_and_measure
            )
        self._update_connection_display(resolution)
        self._unchanged_safe_taps = 0
        self._unchanged_business_taps = 0
        self._record_input_health(
            {
                "event": "transition_display_refresh",
                "reason": reason,
                "previous_display_id": previous,
                "display_id": display_id,
                "recovery": recovery,
            }
        )
        if display_id is None:
            raise RuntimeError("MuMu IPC display refresh returned no display id")
        return display_id

    def screenshot_raw(self) -> tuple[bytes, int, int, str, float]:
        self._require_connected()
        started = time.perf_counter_ns()
        rgba, width, height = self.ipc.capture()
        return rgba, width, height, "RGBA", _elapsed_ms(started)

    def screenshot(self) -> tuple[bytes, float]:
        started = time.perf_counter_ns()
        rgba, width, height, _channels, _capture_ms = self.screenshot_raw()
        with Image.frombytes("RGBA", (width, height), rgba) as source:
            output = io.BytesIO()
            source.save(output, format="PNG")
        return output.getvalue(), _elapsed_ms(started)

    def _capture_and_store(
        self,
        *,
        trigger: str,
        action_id: str,
    ) -> tuple[object, bytes, float, float]:
        rgba, width, height, _channels, capture_ms = self.screenshot_raw()
        enqueue_started = time.perf_counter_ns()
        record = self.run.add_frame_raw(
            rgba,
            width,
            height,
            channels="RGBA",
            trigger=trigger,
            action_id=action_id,
        )
        return record, rgba, capture_ms, _elapsed_ms(enqueue_started)

    def _pre_frame_surface(self, pre_pixels: bytes | None) -> str | None:
        """What the frame this input was sent on identifies as, if anything."""

        if self.redline_pre_frame_guard is None or pre_pixels is None:
            return None
        width, height = EXPECTED_RESOLUTION
        return self.redline_pre_frame_guard(pre_pixels, width, height, "RGBA")

    def _on_board(self, pre_pixels: bytes | None) -> bool:
        """Whether the recovery ladder is allowed to run for this input.

        Without a guard to ask, the old behaviour stands: the ladder runs.  With
        one, only a tap that was aimed at the board may be called dead input.
        """

        surface = self._pre_frame_surface(pre_pixels)
        return surface is None or surface == BOARD_SURFACE

    def _redline_refusal(
        self,
        request: ActionRequestV1,
        pre_pixels: bytes | None,
        pre_frame_id: str | None,
    ) -> str | None:
        """Name the surface this input must not be sent onto, if any.

        Two rules, both fail-closed.  A registered red-line surface takes only
        its own registered close, because refusing that would strand the run on
        the surface it is leaving.  Any other surface that is not the board
        takes only the navigation, popup, main-task and wait intents that exist
        to move between surfaces; a business action there is refused, because
        the screen guard cleared the board a second or two earlier and the
        screen has since become something else.
        """

        if self.redline_pre_frame_guard is None or pre_pixels is None:
            return None
        width, height = EXPECTED_RESOLUTION
        surface = self.redline_pre_frame_guard(pre_pixels, width, height, "RGBA")
        if surface is None or surface == BOARD_SURFACE:
            return None
        authorized = self.authorized_purchase_intents.get(surface)
        if surface in self.forbidden_surface_names:
            allowed = request.intent.startswith(f"popup:close:{surface}:") or (
                request.intent == authorized
            )
        else:
            allowed = request.intent.startswith(SURFACE_TRAVERSAL_INTENTS) or (
                bool(self.board_full_under_guide_intents)
                and request.intent.startswith(self.board_full_under_guide_intents)
            ) or (
                # 2026-09-17: the shop's registered purchase prefix, one intent
                # per shelf item; the loop enforces value, reserve and ledger.
                isinstance(authorized, str)
                and authorized.endswith(":")
                and request.intent.startswith(authorized)
            )
        if allowed:
            return None
        self.redline_refusals.append(
            {
                "intent": request.intent,
                "surface": surface,
                "pre_frame_id": pre_frame_id,
                "params": dict(request.params),
            }
        )
        return surface

    def _refused_result(
        self,
        action_id: str,
        pre_frame_id: str | None,
        surface: str,
        request: ActionRequestV1,
        *,
        pre_capture_ms: float,
        store_ms: float,
        enqueue_ms: float,
        started: int,
    ) -> ActionResultV1:
        """An action that was never sent, recorded as plainly as one that was."""

        total_ms = _elapsed_ms(started)
        record = ActionRecordV1(
            action_id=action_id,
            run_id=self.run.run_id,
            kind=request.kind,
            intent=f"refused_by_pre_frame:{surface}:{request.intent}",
            params=dict(request.params),
            pre_frame_id=pre_frame_id,
            post_frame_ids=[],
            duration_ms=total_ms,
            outcome=f"refused_by_pre_frame:{surface}",
        )
        self.run.add_action(record)
        return ActionResultV1(
            action_id=action_id,
            pre_frame_id=pre_frame_id,
            post_frame_ids=[],
            timing=ActuatorTimingV1(
                pre_capture_ms=pre_capture_ms,
                input_ms=0.0,
                settle_ms=0.0,
                post_capture_ms=0.0,
                store_ms=store_ms,
                total_ms=total_ms,
            ),
            settled=False,
            error=f"refused_by_pre_frame: the frame in hand is {surface}",
        )

    def _move(self, request: ActionRequestV1, *, drag: bool) -> None:
        params = request.params
        x1, y1 = params["x1"], params["y1"]
        x2, y2 = params["x2"], params["y2"]
        duration_ms = params["duration_ms"]
        distance = math.hypot(x2 - x1, y2 - y1)
        steps = max(8 if drag else 2, math.ceil(distance / 24))
        step_s = duration_ms / steps / 1000
        self.ipc.touch_down(x1, y1, finger_id=1)
        try:
            for index in range(1, steps + 1):
                self.sleep(step_s)
                fraction = index / steps
                self.ipc.touch_move(
                    round(x1 + (x2 - x1) * fraction),
                    round(y1 + (y2 - y1) * fraction),
                    finger_id=1,
                )
        finally:
            self.ipc.touch_up(finger_id=1)

    def _perform_input(self, request: ActionRequestV1) -> None:
        if request.kind == "wait":
            self.sleep(request.params["ms"] / 1000)
        elif request.kind == "tap":
            self.ipc.touch_down(request.params["x"], request.params["y"], finger_id=1)
            try:
                self.sleep(0.05)
            finally:
                self.ipc.touch_up(finger_id=1)
        else:
            self._move(request, drag=request.kind == "drag")

    def _retry_tap_and_capture(
        self,
        request: ActionRequestV1,
        *,
        action_id: str,
    ) -> tuple[object, bytes, float, float, float, float]:
        input_started = time.perf_counter_ns()
        self._perform_input(request)
        input_ms = _elapsed_ms(input_started)
        settle_started = time.perf_counter_ns()
        if self.post_frame_delay_ms:
            self.sleep(self.post_frame_delay_ms / 1000)
        settle_ms = _elapsed_ms(settle_started)
        record, pixels, capture_ms, store_ms = self._capture_and_store(
            trigger="post_action",
            action_id=action_id,
        )
        return record, pixels, input_ms, settle_ms, capture_ms, store_ms

    def _recovery_tap(self, x: int, y: int) -> dict[str, Any]:
        """One harmless verification tap, recorded in the replay timeline.

        It is not a business action: it never enters the round's executed list,
        the game-action count or the business-tap health streaks.  It is still
        adjudicated by the pre-frame red-line audit like every other tap, and it
        is only ever issued on a point the caller's verifier chose from the
        current frame.
        """

        action_id = f"r.{time.monotonic_ns()}.{uuid.uuid4().hex[:6]}"
        started = time.perf_counter_ns()
        _record, before, _capture_ms, _store_ms = self._capture_and_store(
            trigger="pre_action",
            action_id=action_id,
        )
        pre_frame_id = _record.frame_id
        self.ipc.touch_down(x, y, finger_id=1)
        try:
            self.sleep(0.05)
        finally:
            self.ipc.touch_up(finger_id=1)
        if self.post_frame_delay_ms:
            self.sleep(self.post_frame_delay_ms / 1000)
        post_record, after, _capture_ms, _store_ms = self._capture_and_store(
            trigger="post_action",
            action_id=action_id,
        )
        width, height = (
            self.connection.resolution
            if self.connection is not None
            else EXPECTED_RESOLUTION
        )
        distance = _dhash_distance(before, after, width, height)
        landed = distance > 0
        self.run.add_action(
            ActionRecordV1(
                run_id=self.run.run_id,
                action_id=action_id,
                kind="tap",
                intent=RECOVERY_TAP_INTENT,
                params={"x": int(x), "y": int(y)},
                pre_frame_id=pre_frame_id,
                post_frame_ids=[post_record.frame_id],
                duration_ms=_elapsed_ms(started),
                outcome="recovery_verified" if landed else "recovery_unverified",
            )
        )
        return {
            "action_id": action_id,
            "point": [int(x), int(y)],
            "dhash_distance": distance,
            "input_landed": landed,
        }

    def _restart_app_and_verify(self, attempt: int) -> dict[str, Any]:
        """HOME, relaunch, reconnect, then prove input lands with one safe tap."""

        entry: dict[str, Any] = {"attempt": attempt}
        if self.app_restart_probe is None:
            entry["error"] = "no app restart probe is configured"
            return entry
        try:
            entry["restart"] = self.app_restart_probe()
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            return entry
        self.sleep(self.app_restart_settle_s)
        try:
            self.ipc.reconnect()
            self._update_connection_display(self.ipc.query_resolution())
            entry["display_id"] = self.ipc.display_id
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            return entry
        if self.recovery_verify_point is None:
            entry["error"] = "no recovery verification point provider is configured"
            return entry
        try:
            rgba, width, height, _channels, _capture_ms = self.screenshot_raw()
            point = self.recovery_verify_point(rgba, width, height)
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
            return entry
        if point is None:
            # The relaunched app is not on a surface this may tap.  Not an
            # error: the next attempt, or the caller, decides what happens.
            entry["verification"] = {"declined": "no safe verification point"}
            return entry
        try:
            entry["verification"] = self._recovery_tap(int(point[0]), int(point[1]))
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry

    def _recover_dead_input_by_restart(self, health_event: dict[str, Any]) -> bool:
        """Escalate a dead renderer to an app restart, at most twice."""

        attempts: list[dict[str, Any]] = []
        recovered = False
        for attempt in range(1, self.max_app_restarts + 1):
            entry = self._restart_app_and_verify(attempt)
            attempts.append(entry)
            verification = entry.get("verification")
            if isinstance(verification, dict) and verification.get("input_landed"):
                recovered = True
                break
            if "error" in entry and self.app_restart_probe is None:
                break
        health_event["app_restarts"] = attempts
        if recovered:
            self.app_restart_recoveries += 1
            self._unchanged_safe_taps = 0
            self._unchanged_business_taps = 0
        return recovered

    @staticmethod
    def _monitors_input_health(request: ActionRequestV1) -> bool:
        return (
            request.kind == "tap"
            and request.capture_pre
            and request.capture_post
            and request.intent.startswith(_SAFE_TAP_INTENT_PREFIXES + _BUSINESS_TAP_INTENT_PREFIXES)
        )

    def _screen_settle_satisfied(
        self,
        request: ActionRequestV1,
        pixels: bytes,
        width: int,
        height: int,
    ) -> bool:
        """Evaluate an explicit screen postcondition on one captured frame."""

        if request.expected_screen is None and request.excluded_screen is None:
            return False
        if self.redline_pre_frame_guard is None:
            raise RuntimeError("screen settle predicate requires a screen guard")
        screen = self.redline_pre_frame_guard(pixels, width, height, "RGBA")
        if screen is None or screen in NON_SETTLED_SCREEN_NAMES:
            return False
        return (
            (request.expected_screen is None or screen == request.expected_screen)
            and (request.excluded_screen is None or screen != request.excluded_screen)
        )

    def observe_business_save_evidence(
        self,
        action_id: str,
        *,
        confirmed: bool,
    ) -> None:
        """Accept authoritative save change as input evidence after adjudication."""

        visual_evidence = self._tap_visual_evidence.pop(action_id, None)
        if not confirmed:
            return
        self._unchanged_business_taps = 0
        self._record_input_health(
            {
                "event": "business_save_evidence",
                "action_id": action_id,
                "visual_evidence": visual_evidence,
                "save_change_confirmed": True,
            }
        )

    def act(self, request: ActionRequestV1) -> ActionResultV1:
        self._require_connected()
        action_id = f"a.{time.monotonic_ns()}.{uuid.uuid4().hex[:6]}"
        started = time.perf_counter_ns()
        pre_capture_ms = 0.0
        input_ms = 0.0
        settle_ms = 0.0
        post_capture_ms = 0.0
        store_ms = 0.0
        enqueue_ms = 0.0
        pre_frame_id: str | None = None
        post_frame_ids: list[str] = []
        pre_pixels: bytes | None = None
        settled = request.settle == "fixed"
        error: str | None = None
        try:
            if (
                (request.expected_screen is not None or request.excluded_screen is not None)
                and self.redline_pre_frame_guard is None
            ):
                raise RuntimeError("screen settle predicate requires a screen guard")
            if request.capture_pre:
                (
                    record,
                    pre_pixels,
                    pre_capture_ms,
                    frame_store_ms,
                ) = self._capture_and_store(trigger="pre_action", action_id=action_id)
                pre_frame_id = record.frame_id
                store_ms += frame_store_ms
                enqueue_ms += frame_store_ms

            refusal = self._redline_refusal(request, pre_pixels, pre_frame_id)
            if refusal is not None:
                return self._refused_result(
                    action_id,
                    pre_frame_id,
                    refusal,
                    request,
                    pre_capture_ms=pre_capture_ms,
                    store_ms=store_ms,
                    enqueue_ms=enqueue_ms,
                    started=started,
                )

            input_started = time.perf_counter_ns()
            self._perform_input(request)
            input_ms = _elapsed_ms(input_started)

            settle_started = time.perf_counter_ns()
            if self.post_frame_delay_ms:
                self.sleep(self.post_frame_delay_ms / 1000)
            settle_ms += _elapsed_ms(settle_started)

            previous: bytes | None = None
            if request.capture_post:
                record, previous, capture_ms, frame_store_ms = self._capture_and_store(
                    trigger="post_action", action_id=action_id
                )
                post_capture_ms += capture_ms
                post_frame_ids.append(record.frame_id)
                store_ms += frame_store_ms
                enqueue_ms += frame_store_ms

            if (
                (request.expected_screen is not None or request.excluded_screen is not None)
                and request.capture_post
            ):
                settled = self._screen_settle_satisfied(
                    request, previous or b"", self.connection.resolution[0], self.connection.resolution[1]
                )
                deadline = time.perf_counter_ns() + max(
                    self.settle_timeout_ms, EXPECTED_SCREEN_SETTLE_TIMEOUT_MS
                ) * 1_000_000
                while not settled and time.perf_counter_ns() < deadline:
                    wait_started = time.perf_counter_ns()
                    self.sleep(0.1)
                    settle_ms += _elapsed_ms(wait_started)
                    record, current, capture_ms, frame_store_ms = self._capture_and_store(
                        trigger="post_action", action_id=action_id
                    )
                    post_capture_ms += capture_ms
                    post_frame_ids.append(record.frame_id)
                    store_ms += frame_store_ms
                    enqueue_ms += frame_store_ms
                    previous = current
                    settled = self._screen_settle_satisfied(
                        request, current, self.connection.resolution[0], self.connection.resolution[1]
                    )
            elif request.settle == "stable" and request.capture_post:
                settled = False
                previous_hash = hashlib.blake2b(previous or b"", digest_size=8).digest()
                deadline = time.perf_counter_ns() + self.settle_timeout_ms * 1_000_000
                while time.perf_counter_ns() < deadline:
                    wait_started = time.perf_counter_ns()
                    self.sleep(0.1)
                    settle_ms += _elapsed_ms(wait_started)
                    record, current, capture_ms, frame_store_ms = self._capture_and_store(
                        trigger="post_action", action_id=action_id
                    )
                    post_capture_ms += capture_ms
                    post_frame_ids.append(record.frame_id)
                    store_ms += frame_store_ms
                    enqueue_ms += frame_store_ms
                    current_hash = hashlib.blake2b(current, digest_size=8).digest()
                    if current_hash == previous_hash:
                        settled = True
                        previous = current
                        break
                    previous_hash = current_hash
                    previous = current

            if (
                self._monitors_input_health(request)
                and pre_pixels is not None
                and previous is not None
                and self.connection is not None
            ):
                width, height = self.connection.resolution
                distance = _tap_evidence_distance(
                    request,
                    pre_pixels,
                    previous,
                    width,
                    height,
                )
                business_tap = request.intent.startswith(_BUSINESS_TAP_INTENT_PREFIXES)
                if business_tap:
                    self._tap_visual_evidence[action_id] = distance > 0
                streak_name = "_unchanged_business_taps" if business_tap else "_unchanged_safe_taps"
                unchanged_streak = getattr(self, streak_name) + 1 if distance == 0 else 0
                setattr(self, streak_name, unchanged_streak)
                if unchanged_streak >= 2 and not self._on_board(pre_pixels):
                    # F4-09, planner ruling of 2026-09-07.  A screen that does
                    # not move is only a dead renderer if the tap was aimed at
                    # the board.  On any other surface it means the guide will
                    # not advance, which is a game state, not a device fault:
                    # the 02:27Z stop went down the recovery ladder and
                    # restarted the app over an unadvancing story prompt.
                    self.input_health_events.append(
                        {
                            "event": "unchanged_off_board",
                            "action_id": action_id,
                            "intent": request.intent,
                            "unchanged_streak": unchanged_streak,
                            "surface": self._pre_frame_surface(pre_pixels),
                        }
                    )
                    setattr(self, streak_name, 0)
                elif (
                    unchanged_streak >= 2
                    and business_tap
                    and unchanged_streak < LIVE_FRAME_LADDER_STREAK
                ):
                    # 2026-09-16.  Two free pulls inside one round read exactly
                    # like a frozen renderer, and the round's save read -- the
                    # only thing that can tell them apart -- does not arrive
                    # until adjudication, after both taps.  Wait for a streak
                    # that no save read has cleared instead of stopping a live
                    # session on the second tap.
                    self.input_health_events.append(
                        {
                            "event": "unchanged_business_region_unvouched",
                            "action_id": action_id,
                            "intent": request.intent,
                            "unchanged_streak": unchanged_streak,
                            "ladder_streak": LIVE_FRAME_LADDER_STREAK,
                            "whole_frame_moved": pre_pixels != previous,
                        }
                    )
                elif unchanged_streak >= (
                    LIVE_FRAME_LADDER_STREAK if business_tap else SAFE_TAP_LADDER_STREAK
                ):
                    health_event: dict[str, Any] = {
                        "event": "tap_recovery",
                        "action_id": action_id,
                        "intent": request.intent,
                        "evidence_kind": (
                            "business_regions_or_save" if business_tap else "whole_frame"
                        ),
                        "unchanged_streak": unchanged_streak,
                        "initial_dhash_distance": distance,
                    }

                    def retry() -> int:
                        nonlocal input_ms
                        nonlocal settle_ms
                        nonlocal post_capture_ms
                        nonlocal store_ms
                        nonlocal enqueue_ms
                        nonlocal previous
                        (
                            retry_record,
                            retry_pixels,
                            retry_input_ms,
                            retry_settle_ms,
                            retry_capture_ms,
                            retry_store_ms,
                        ) = self._retry_tap_and_capture(
                            request,
                            action_id=action_id,
                        )
                        input_ms += retry_input_ms
                        settle_ms += retry_settle_ms
                        post_capture_ms += retry_capture_ms
                        store_ms += retry_store_ms
                        enqueue_ms += retry_store_ms
                        post_frame_ids.append(retry_record.frame_id)
                        previous = retry_pixels
                        return _tap_evidence_distance(
                            request,
                            pre_pixels,
                            retry_pixels,
                            width,
                            height,
                        )

                    refresh_distance: int | None = None
                    previous_display_id = self.ipc.display_id
                    try:
                        display_id = self.ipc.refresh_display_id()
                        self._update_connection_display(self.ipc.query_resolution())
                        refresh_distance = retry()
                        health_event["display_refresh"] = {
                            "previous_display_id": previous_display_id,
                            "display_id": display_id,
                            "dhash_distance": refresh_distance,
                        }
                    except Exception as exc:
                        health_event["display_refresh"] = {
                            "previous_display_id": previous_display_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    if refresh_distance is not None and refresh_distance > 0:
                        health_event["outcome"] = "recovered_after_display_refresh"
                        self._record_input_health(health_event)
                        setattr(self, streak_name, 0)
                        settled = True
                    else:
                        reconnect_distance: int | None = None
                        try:
                            previous_handle = self.ipc.handle
                            handle = self.ipc.reconnect()
                            self._update_connection_display(self.ipc.query_resolution())
                            reconnect_distance = retry()
                            health_event["handle_reconnect"] = {
                                "previous_handle": previous_handle,
                                "handle": handle,
                                "display_id": self.ipc.display_id,
                                "dhash_distance": reconnect_distance,
                            }
                        except Exception as exc:
                            health_event["handle_reconnect"] = {
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        if reconnect_distance is not None and reconnect_distance > 0:
                            health_event["outcome"] = "recovered_after_handle_reconnect"
                            self._record_input_health(health_event)
                            setattr(self, streak_name, 0)
                            settled = True
                        else:
                            adb_contrast: dict[str, Any] = {"attempted": False}
                            if self.adb_tap_probe is not None:
                                adb_contrast["attempted"] = True
                                try:
                                    adb_contrast.update(
                                        self.adb_tap_probe(
                                            request.params["x"],
                                            request.params["y"],
                                        )
                                    )
                                    if self.post_frame_delay_ms:
                                        self.sleep(self.post_frame_delay_ms / 1000)
                                    (
                                        adb_record,
                                        adb_pixels,
                                        adb_capture_ms,
                                        adb_store_ms,
                                    ) = self._capture_and_store(
                                        trigger="post_action",
                                        action_id=action_id,
                                    )
                                    post_frame_ids.append(adb_record.frame_id)
                                    post_capture_ms += adb_capture_ms
                                    store_ms += adb_store_ms
                                    enqueue_ms += adb_store_ms
                                    adb_contrast["dhash_distance"] = _tap_evidence_distance(
                                        request,
                                        pre_pixels,
                                        adb_pixels,
                                        width,
                                        height,
                                    )
                                except Exception as exc:
                                    adb_contrast["error"] = f"{type(exc).__name__}: {exc}"
                            health_event["adb_contrast"] = adb_contrast
                            # F4-07 open item 2: the renderer is dead but the
                            # host is not.  F4-06 lost 3 of 12 segments here and
                            # HOME + LAUNCHER relaunch fixed every one by hand.
                            if self._recover_dead_input_by_restart(health_event):
                                health_event["outcome"] = (
                                    "recovered_after_app_restart"
                                )
                                self._record_input_health(health_event)
                                raise MuMuIpcInputRestartRecoveredError(
                                    "input was dead, the app was relaunched and a "
                                    "verification tap landed; this action was lost"
                                )
                            health_event["outcome"] = "input_dead_unrecovered"
                            self._record_input_health(health_event)
                            raise MuMuIpcInputDeadError(
                                "monitored tap remained unchanged after display refresh, "
                                "handle reconnect and app restart"
                            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            settled = False
            self.run.alert(f"MuMu IPC action {action_id} failed: {error}")

        total_ms = _elapsed_ms(started)
        action = ActionRecordV1(
            run_id=self.run.run_id,
            action_id=action_id,
            kind=request.kind,
            intent=request.intent,
            params=dict(request.params),
            pre_frame_id=pre_frame_id,
            post_frame_ids=post_frame_ids,
            duration_ms=total_ms,
            outcome="error" if error else ("settled" if settled else "settle_timeout"),
        )
        action_store_started = time.perf_counter_ns()
        self.run.add_action(action)
        action_store_ms = _elapsed_ms(action_store_started)
        store_ms += action_store_ms
        return ActionResultV1(
            action_id=action_id,
            pre_frame_id=pre_frame_id,
            post_frame_ids=post_frame_ids,
            timing=ActuatorTimingV1(
                pre_capture_ms=pre_capture_ms,
                input_ms=input_ms,
                settle_ms=settle_ms,
                post_capture_ms=post_capture_ms,
                store_ms=store_ms,
                store_breakdown_ms={
                    "webp_encode": 0.0,
                    "sha256": 0.0,
                    "ensure_capacity": 0.0,
                    "write_file": action_store_ms,
                    "enqueue": enqueue_ms,
                },
                total_ms=_elapsed_ms(started),
            ),
            settled=settled,
            error=error,
        )

    def close(self) -> None:
        if not self._closed:
            self.ipc.disconnect()
        self.connection = None
        self._closed = True


__all__ = [
    "MuMuIpcActuator",
    "MuMuIpcInputDeadError",
    "MuMuIpcInputRestartRecoveredError",
    "RECOVERY_TAP_INTENT",
]
