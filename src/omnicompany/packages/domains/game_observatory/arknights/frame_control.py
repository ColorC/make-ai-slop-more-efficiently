# [OMNI] origin=codex created_by=codex intent="provide resident at-most-once start-safe and ensure-paused guards"
"""Low-latency frame truth and asynchronous battle-entry guarding.

This module deliberately does not run OCR.  Battle entry is a survival hot
path: a fixed HUD template proves a running battle, while the fixed PAUSE
glyph proves a paused battle.  Full OCR remains a slower milestone
cross-check after the game clock is known to be stopped.
"""

from __future__ import annotations

import io
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from PIL import Image

from .intent_runtime import (
    BattleControlSnapshot,
    IntentRecord,
    IntentStatus,
    IntentStore,
    SafeBattleStart,
    SnapshotValidity,
    ensure_paused,
)


class FrameBattleControlClassifier:
    """Classify canonical MAA frames without OCR or filesystem writes."""

    CANONICAL_SIZE = (1280, 720)
    _CONTROL_ROI = (1165, 20, 1245, 85)
    _PAUSE_GLYPH_ROI = (494, 292, 787, 367)

    def __init__(
        self,
        running_template: Path | str,
        *,
        hud_template: Path | str | None = None,
        running_threshold: float = 0.82,
        hud_threshold: float = 0.75,
        pause_threshold: int = 145,
    ) -> None:
        template = cv2.imread(str(running_template), cv2.IMREAD_GRAYSCALE)
        if template is None or template.size == 0:
            raise ValueError(f"cannot read battle HUD template: {running_template}")
        self._running_template = template
        self._hud_template = None
        if hud_template is not None:
            self._hud_template = cv2.imread(str(hud_template), cv2.IMREAD_GRAYSCALE)
            if self._hud_template is None or self._hud_template.size == 0:
                raise ValueError(f"cannot read battle HUD proof template: {hud_template}")
        self.running_threshold = float(running_threshold)
        self.hud_threshold = float(hud_threshold)
        self.pause_threshold = int(pause_threshold)

    @staticmethod
    def _canonical_rgb(png: bytes) -> np.ndarray:
        image = Image.open(io.BytesIO(png)).convert("RGB")
        if image.size != FrameBattleControlClassifier.CANONICAL_SIZE:
            image = image.resize(
                FrameBattleControlClassifier.CANONICAL_SIZE, Image.Resampling.BILINEAR
            )
        return np.asarray(image)

    def _running_score(self, rgb: np.ndarray) -> float:
        x1, y1, x2, y2 = self._CONTROL_ROI
        gray = cv2.cvtColor(rgb[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
        th, tw = self._running_template.shape
        if gray.shape[0] < th or gray.shape[1] < tw:
            return 0.0
        scores = cv2.matchTemplate(
            gray, self._running_template, cv2.TM_CCOEFF_NORMED
        )
        score = float(np.nanmax(scores))
        return score if np.isfinite(score) else 0.0

    def _pause_component_count(self, rgb: np.ndarray) -> int:
        x1, y1, x2, y2 = self._PAUSE_GLYPH_ROI
        gray = cv2.cvtColor(rgb[y1:y2, x1:x2], cv2.COLOR_RGB2GRAY)
        mask = np.where(gray >= self.pause_threshold, 255, 0).astype(np.uint8)
        _count, _labels, stats, _centres = cv2.connectedComponentsWithStats(mask)
        return sum(
            1
            for _x, _y, width, height, area in stats[1:]
            if 30 <= width <= 80 and 45 <= height <= 75 and area >= 500
        )

    def _hud_score(self, rgb: np.ndarray) -> float:
        if self._hud_template is None:
            return 1.0
        gray = cv2.cvtColor(rgb[0:60, 400:1050], cv2.COLOR_RGB2GRAY)
        scores = cv2.matchTemplate(
            gray, self._hud_template, cv2.TM_CCOEFF_NORMED
        )
        score = float(np.nanmax(scores))
        return score if np.isfinite(score) else 0.0

    def classify(
        self,
        png: bytes,
        *,
        state_version: int,
        captured_monotonic: float,
    ) -> BattleControlSnapshot:
        rgb = self._canonical_rgb(png)
        return self._classify_rgb(
            rgb,
            state_version=state_version,
            captured_monotonic=captured_monotonic,
            source_prefix="resident-maa-png",
        )

    def classify_bgr(
        self,
        bgr: bytes | np.ndarray,
        *,
        state_version: int,
        captured_monotonic: float,
    ) -> BattleControlSnapshot:
        """Classify MaaCore's continuous canonical BGR buffer directly."""

        expected_shape = (
            self.CANONICAL_SIZE[1],
            self.CANONICAL_SIZE[0],
            3,
        )
        if isinstance(bgr, bytes):
            expected_bytes = int(np.prod(expected_shape))
            if len(bgr) != expected_bytes:
                raise ValueError(
                    f"canonical BGR frame must contain {expected_bytes} bytes, got {len(bgr)}"
                )
            array = np.frombuffer(bgr, dtype=np.uint8).reshape(expected_shape)
        else:
            array = np.asarray(bgr, dtype=np.uint8)
            if array.shape != expected_shape:
                raise ValueError(
                    f"canonical BGR frame must have shape {expected_shape}, got {array.shape}"
                )
        rgb = cv2.cvtColor(array, cv2.COLOR_BGR2RGB)
        return self._classify_rgb(
            rgb,
            state_version=state_version,
            captured_monotonic=captured_monotonic,
            source_prefix="resident-maa-bgr",
        )

    def _classify_rgb(
        self,
        rgb: np.ndarray,
        *,
        state_version: int,
        captured_monotonic: float,
        source_prefix: str,
    ) -> BattleControlSnapshot:
        pause_components = self._pause_component_count(rgb)
        running_score = self._running_score(rgb)
        hud_score = self._hud_score(rgb)
        source = (
            f"{source_prefix}:"
            f"running={running_score:.4f},hud={hud_score:.4f},"
            f"pause_components={pause_components}"
        )
        if pause_components >= 4 and hud_score >= self.hud_threshold:
            return BattleControlSnapshot(
                loaded=True,
                paused=True,
                state_version=state_version,
                captured_monotonic=captured_monotonic,
                source=source,
            )
        if running_score >= self.running_threshold:
            return BattleControlSnapshot(
                loaded=True,
                paused=False,
                state_version=state_version,
                captured_monotonic=captured_monotonic,
                source=source,
            )
        return BattleControlSnapshot(
            loaded=None,
            paused=None,
            state_version=state_version,
            captured_monotonic=captured_monotonic,
            source=source,
            validity=SnapshotValidity.UNAVAILABLE,
        )


class ResidentBattleEntryGuard:
    """Run ``SafeBattleStart`` continuously outside the request round trip."""

    def __init__(
        self,
        store: IntentStore,
        *,
        snapshot: Callable[[], BattleControlSnapshot],
        pause_input: Callable[[], None],
        poll_seconds: float = 0.05,
        entry_deadline_seconds: float = 45.0,
        paused_confirmations: int = 3,
        running_stabilization_seconds: float = 0.75,
        max_pause_attempts: int = 3,
        pause_on_first_running: bool = False,
    ) -> None:
        if not 0.01 <= poll_seconds <= 0.5:
            raise ValueError("poll_seconds must be between 10ms and 500ms")
        if entry_deadline_seconds <= 0:
            raise ValueError("entry_deadline_seconds must be positive")
        if paused_confirmations < 2:
            raise ValueError("paused_confirmations must be at least 2")
        if not 0.01 <= running_stabilization_seconds <= 5.0:
            raise ValueError("running_stabilization_seconds must be between 0.01 and 5s")
        if not 1 <= max_pause_attempts <= 5:
            raise ValueError("max_pause_attempts must be between 1 and 5")
        self.store = store
        self.snapshot = snapshot
        self.pause_input = pause_input
        self.poll_seconds = poll_seconds
        self.entry_deadline_seconds = entry_deadline_seconds
        self.paused_confirmations = int(paused_confirmations)
        self.running_stabilization_seconds = float(running_stabilization_seconds)
        self.max_pause_attempts = int(max_pause_attempts)
        self.pause_on_first_running = bool(pause_on_first_running)
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def begin(
        self,
        *,
        intent_id: str,
        requested_state_version: int,
        start_input: Callable[[], None],
        semantic_target: str = "battle-start-control",
    ) -> IntentRecord:
        machine = SafeBattleStart(
            self.store,
            intent_id=intent_id,
            requested_state_version=requested_state_version,
            request_payload={"semantic_target": semantic_target},
        )
        callback_error: BaseException | None = None
        try:
            record = machine.start(start_input)
        except BaseException as error:  # input outcome is unknown; guard it anyway
            callback_error = error
            record = self.store.get(intent_id)
            assert record is not None
        self._launch_once(intent_id, machine)
        if callback_error is not None:
            raise callback_error
        return record

    def _launch_once(self, intent_id: str, machine: SafeBattleStart) -> None:
        with self._lock:
            existing = self._threads.get(intent_id)
            if existing is not None and existing.is_alive():
                return
            thread = threading.Thread(
                target=self._run,
                args=(machine,),
                name=f"battle-entry-{intent_id[:24]}",
                daemon=True,
            )
            self._threads[intent_id] = thread
            thread.start()

    def _run(self, machine: SafeBattleStart) -> None:
        deadline = time.monotonic() + self.entry_deadline_seconds
        paused_captures: list[float] = []
        running_captures: list[float] = []
        post_pause_running_since: float | None = None
        while True:
            record = self.store.get(machine.intent_id)
            if record is None or record.status is IntentStatus.VERIFIED_SUCCESS:
                return
            if time.monotonic() >= deadline:
                self.store.transition(
                    machine.intent_id,
                    IntentStatus.SAFETY_HOLD,
                    detail="battle entry deadline expired without paused proof",
                )
                return
            try:
                snapshot = self.snapshot()
                if snapshot.loaded and snapshot.paused:
                    running_captures.clear()
                    post_pause_running_since = None
                    captured = float(snapshot.captured_monotonic)
                    if not paused_captures or captured > paused_captures[-1]:
                        paused_captures.append(captured)
                        paused_captures = paused_captures[-self.paused_confirmations :]
                    if len(paused_captures) < self.paused_confirmations:
                        time.sleep(self.poll_seconds)
                        continue
                    snapshot = replace(
                        snapshot,
                        source=(
                            f"{snapshot.source},stable_paused_samples="
                            f"{self.paused_confirmations},window_ms="
                            f"{(paused_captures[-1] - paused_captures[0]) * 1000:.1f}"
                        ),
                    )
                else:
                    paused_captures.clear()
                if snapshot.loaded and snapshot.paused is False:
                    captured = float(snapshot.captured_monotonic)
                    if not running_captures or captured > running_captures[-1]:
                        running_captures.append(captured)
                        running_captures = running_captures[-128:]
                    record = self.store.get(machine.intent_id)
                    assert record is not None
                    if not record.pause_input_sent:
                        stable_for = running_captures[-1] - running_captures[0]
                        if (
                            not self.pause_on_first_running
                            and stable_for < self.running_stabilization_seconds
                        ):
                            time.sleep(self.poll_seconds)
                            continue
                    else:
                        if post_pause_running_since is None:
                            post_pause_running_since = captured
                        stable_after_pause = captured - post_pause_running_since
                        if stable_after_pause >= self.running_stabilization_seconds:
                            retry_events = {
                                event.kind
                                for event in self.store.events(machine.intent_id)
                                if event.kind.startswith("pause_retry_")
                            }
                            attempt = 2 + len(retry_events)
                            if attempt <= self.max_pause_attempts:
                                kind = f"pause_retry_{attempt}_reserved"
                                if self.store.reserve_event_once(machine.intent_id, kind):
                                    self.pause_input()
                                post_pause_running_since = captured
                record = machine.feed(
                    snapshot,
                    pause_input=self.pause_input,
                    now_monotonic=time.monotonic(),
                )
                if record.status is IntentStatus.VERIFIED_SUCCESS:
                    return
            except Exception as error:  # keep the safety loop alive, but never input blind
                self.store.transition(
                    machine.intent_id,
                    IntentStatus.SAFETY_HOLD,
                    detail=f"battle snapshot failed: {type(error).__name__}: {error}",
                )
            time.sleep(self.poll_seconds)

    def wait(self, intent_id: str, timeout: float) -> IntentRecord:
        with self._lock:
            thread = self._threads.get(intent_id)
        if thread is not None:
            thread.join(timeout)
        record = self.store.get(intent_id)
        if record is None:
            raise KeyError(intent_id)
        return record


class ResidentEnsurePausedGuard:
    """Continuously reconcile one idempotent ensure-paused request."""

    def __init__(
        self,
        store: IntentStore,
        *,
        snapshot: Callable[[], BattleControlSnapshot],
        pause_input: Callable[[], None],
        poll_seconds: float = 0.05,
        deadline_seconds: float = 5.0,
    ) -> None:
        if not 0.01 <= poll_seconds <= 0.5:
            raise ValueError("poll_seconds must be between 10ms and 500ms")
        if deadline_seconds <= 0:
            raise ValueError("deadline_seconds must be positive")
        self.store = store
        self.snapshot = snapshot
        self.pause_input = pause_input
        self.poll_seconds = poll_seconds
        self.deadline_seconds = deadline_seconds
        self._lock = threading.Lock()
        self._threads: dict[str, threading.Thread] = {}

    def begin(
        self, *, intent_id: str, requested_state_version: int
    ) -> IntentRecord:
        record = ensure_paused(
            self.store,
            intent_id=intent_id,
            requested_state_version=requested_state_version,
            snapshot=self.snapshot(),
            pause_input=self.pause_input,
            now_monotonic=time.monotonic(),
        )
        if record.status not in {
            IntentStatus.VERIFIED_SUCCESS,
            IntentStatus.REJECTED_BEFORE_INPUT,
            IntentStatus.FAILED_BEFORE_INPUT,
        }:
            self._launch_once(intent_id, requested_state_version)
        return record

    def _launch_once(self, intent_id: str, requested_state_version: int) -> None:
        with self._lock:
            existing = self._threads.get(intent_id)
            if existing is not None and existing.is_alive():
                return
            thread = threading.Thread(
                target=self._run,
                args=(intent_id, requested_state_version),
                name=f"ensure-paused-{intent_id[:24]}",
                daemon=True,
            )
            self._threads[intent_id] = thread
            thread.start()

    def _run(self, intent_id: str, requested_state_version: int) -> None:
        deadline = time.monotonic() + self.deadline_seconds
        while time.monotonic() < deadline:
            time.sleep(self.poll_seconds)
            try:
                record = ensure_paused(
                    self.store,
                    intent_id=intent_id,
                    requested_state_version=requested_state_version,
                    snapshot=self.snapshot(),
                    pause_input=self.pause_input,
                    now_monotonic=time.monotonic(),
                )
                if record.status in {
                    IntentStatus.VERIFIED_SUCCESS,
                    IntentStatus.REJECTED_BEFORE_INPUT,
                    IntentStatus.FAILED_BEFORE_INPUT,
                }:
                    return
            except Exception as error:
                self.store.transition(
                    intent_id,
                    IntentStatus.SAFETY_HOLD,
                    detail=f"pause reconciliation failed: {type(error).__name__}: {error}",
                )
        self.store.transition(
            intent_id,
            IntentStatus.SAFETY_HOLD,
            detail="ensure-paused deadline expired without paused proof",
        )

    def wait(self, intent_id: str, timeout: float) -> IntentRecord:
        with self._lock:
            thread = self._threads.get(intent_id)
        if thread is not None:
            thread.join(timeout)
        record = self.store.get(intent_id)
        if record is None:
            raise KeyError(intent_id)
        return record
