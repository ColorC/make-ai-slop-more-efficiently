"""Single recorded control session shared by browser and automatic players."""

from __future__ import annotations

import base64
from contextlib import nullcontext
import hashlib
import io
import json
import math
import os
import queue
import subprocess
import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ..delegation.actuator_mumu import MuMuIpcActuator
from ..delegation.contracts import ActionRequestV1
from ..delegation.game_loop import _identify_guard_screen
from ..delegation.navigation import SurfaceRegistry
from ..delegation.interaction_frontier import REWARD_QUEUE_ROI, scan_interaction_frontier
from ..replay import ActionRecordV1, ReplayStore
from ..replay.quota import QuotaManager
from .capture import CaptureFeed, CapturedFrame
from .interface_map import _replace_with_permission_retry
from .interface_catalog import load_catalog, summarize_graph


PROJECT_ROOT = Path(__file__).resolve().parents[6]
DEFAULT_ROOT = PROJECT_ROOT / "data/domains/game_observatory/game_models/romantic-restaurant/streams"
INTERFACE_CATALOG_PATH = PROJECT_ROOT / "config/game_observatory/romantic_restaurant/interface_catalog.v1.json"


def _frontier_worker(frame_rgba: np.ndarray, surface: str, semantic_state: dict, surfaces: dict) -> list[dict[str, Any]]:
    """Run optional OCR in a spawned, single-worker process."""
    import cv2
    cv2.setNumThreads(1)
    frame_rgb = cv2.cvtColor(frame_rgba, cv2.COLOR_RGBA2RGB)
    return scan_interaction_frontier(frame_rgb, surface, semantic_state, {"surfaces": surfaces})


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    _replace_with_permission_retry(str(temporary), path)


class _SharedCaptureIpc:
    """Adapt the existing actuator to the one live capture source."""

    def __init__(self, feed: CaptureFeed) -> None:
        self.feed = feed

    def _source(self, timeout_s: float = 2.0) -> Any:
        # The feed drops its source while it rebuilds one after a renderer
        # crash.  Delegating straight through in that window turned the
        # actuator's own reconnect rung into an AttributeError on None, which
        # is how the 14:18Z and 15:18Z ladders reported "handle_reconnect
        # failed" on a device that was about to come back.
        deadline = time.monotonic() + timeout_s
        while True:
            source = self.feed.source
            if source is not None:
                return source
            if time.monotonic() >= deadline or self.feed.stopped.is_set():
                raise RuntimeError("recording_unavailable: capture source is rebuilding")
            time.sleep(0.05)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source(), name)

    def connect(self) -> int:
        return self._source().handle

    def disconnect(self) -> None:
        # The capture feed owns the device handle for the whole shared session.
        return None

    def capture(self) -> tuple[bytes, int, int]:
        frame = self.feed.latest
        if frame is None or self.feed.error or self.feed.stopped.is_set():
            raise RuntimeError("recording_unavailable")
        return frame.pixels, frame.width, frame.height

    def touch_down(self, x: int, y: int, *, finger_id: int = 1) -> None:
        with self.feed.control_lock():
            self._source().touch_down(x, y, finger_id=finger_id)

    def touch_move(self, x: int, y: int, *, finger_id: int = 1) -> None:
        with self.feed.control_lock():
            self._source().touch_move(x, y, finger_id=finger_id)

    def touch_up(self, *, finger_id: int = 1) -> None:
        with self.feed.control_lock():
            self._source().touch_up(finger_id=finger_id)

    def refresh_display_id(self, *args: Any, **kwargs: Any) -> Any:
        with self.feed.control_lock():
            return self._source().refresh_display_id(*args, **kwargs)


class PlaySession:
    def __init__(self, base: Path = DEFAULT_ROOT) -> None:
        self.base = Path(base).resolve()
        self.root = self.base / ("session-" + time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
        self.control_path = self.base / "control.json"
        self.capture = CaptureFeed(self.root)
        self._input_lock = threading.RLock()
        self._snapshot_lock = threading.RLock()
        self._write_lock = threading.RLock()
        self._input_ledger: Any = None
        self._input_ledger_enabled = False
        self._observations: queue.Queue[tuple[CapturedFrame, dict | None, str]] = queue.Queue(maxsize=128)
        self._frame_refs: dict[int, dict] = {}
        self._classified_screens: dict[str, str] = {}
        self._acks: dict[str, dict] = {}
        self._automatic_evidence: dict[str, dict] = {}
        self._client_sequences: dict[str, int] = {}
        self._viewer_stats: dict[str, dict] = {}
        self._pointers: dict[tuple[str, int], dict] = {}
        self._last_observation_ns = 0
        self._frontier_pool: ProcessPoolExecutor | None = None
        self._frontier_future: Future | None = None
        self._frontier_cache: dict[str, tuple[str, int, dict[str, Any]]] = {}
        self._last_action: dict | None = None
        self._last_input_ns = 0
        self._monitor_thread: threading.Thread | None = None
        self.registry: SurfaceRegistry | None = None
        self.map_store: Any = None
        self.run: Any = None
        self.actuator: Any = None
        self.supervisor: Any = None
        self.mode = "paused"
        self.owner: str | None = None
        self.started = False
        self.input_count = 0
        self.observation_count = 0
        self.observation_gaps = 0
        self.last_state: dict[str, Any] | None = None
        self.last_error: str | None = None

    def start(self) -> dict:
        with self._input_lock:
            if self.started:
                return self.status()
            self.root.mkdir(parents=True, exist_ok=True)
            self.registry = SurfaceRegistry(PROJECT_ROOT / "config/game_observatory/romantic_restaurant/navigation.v1.json")
            from .interface_map import InterfaceMap
            self.map_store = InterfaceMap(self.base / "map")
            replay_root = self.root / "replay"
            # This session retains its records. Never inherit the historical 10GB
            # policy that evicts old replay runs while pursuing this user goal.
            store = ReplayStore(replay_root, quota=QuotaManager(replay_root, limit_bytes=2**60),
                                webp_method=0, frame_workers=4)
            self.run = store.open_run("romantic-restaurant", "mumu2", "3.95.0-294",
                                      "127.0.0.1:16448", "browser-and-auto-play")
            self.capture.on_frame = self._on_frame
            self.capture.start()
            if not self.capture.ready.wait(15) or self.capture.error:
                self.capture.stop()
                raise RuntimeError(self.capture.error or "capture did not produce a video frame")
            self.actuator = MuMuIpcActuator(
                _SharedCaptureIpc(self.capture), self.run, post_frame_delay_ms=120,
                redline_pre_frame_guard=self._guard,
                forbidden_surface_names=self.registry.forbidden_surface_names,
                authorized_purchase_intents=self.registry.authorized_purchase_intents,
            )
            self.actuator.connect()
            from .supervisor import AutoSupervisor
            self.supervisor = AutoSupervisor(
                self.base, os.environ.get("RR_STREAM_SERVICE_URL", "http://127.0.0.1:8213"),
                self.control_path, target_level=40,
            )
            self.started = True
            self._input_ledger_enabled = True
            atomic_json(self.control_path, {"mode": self.mode, "owner": None, "session": self.root.name})
            atomic_json(self.base / "current.json", {"root": str(self.root), "control": str(self.control_path)})
            self._monitor_thread = threading.Thread(target=self._monitor, name="rr-observe", daemon=True)
            self._monitor_thread.start()
            self.run.note("Continuous recording started; normal browser and automatic play share this session.")
            return self.status()

    def _guard(self, pixels: bytes, width: int, height: int, channels: str) -> str:
        with Image.frombytes(channels, (width, height), pixels) as image:
            return _identify_guard_screen(np.asarray(image.convert("RGB")), self.registry).name

    def _on_frame(self, frame: CapturedFrame) -> None:
        # All frames are encoded. Semantic observation is separate and becomes
        # more frequent for two seconds after input, without stalling video.
        busy = 0 <= frame.mono_ns - self._last_input_ns < 2_000_000_000
        # 2026-09-20: each of these becomes a full-resolution WebP still, about
        # 290 KB and 60 ms of encode, and the run was writing 3.8 GB an hour of
        # them on a machine it shares.  Halving the cadence halves both.  The
        # evidence that decides anything is not ambient: an action's own pre and
        # post frames are captured explicitly by the actuator, and the interface
        # map needs a frame per screen state, not ten a second -- consecutive
        # stills differ on about a tenth of their pixels here, all of it
        # character and coin animation, so nothing is deduplicable and nothing
        # in between is a different screen.
        period = 200_000_000 if busy else 2_000_000_000
        if frame.mono_ns - self._last_observation_ns < period:
            return
        try:
            action = {**self._last_action, "overlapping": False} if busy and self._last_action else None
            self._observations.put_nowait((frame, action, "post_action" if busy else "interval"))
            self._last_observation_ns = frame.mono_ns
        except queue.Full:
            # The original content remains in video; record an explicit semantic
            # sampling gap instead of silently dropping original input or video.
            self.last_error = "semantic observation queue is full; continuous video retained"
            self.observation_gaps += 1

    def _append(self, name: str, value: dict) -> None:
        with self._write_lock:
            if name == "inputs.ndjson" and self._input_ledger_enabled:
                if self._input_ledger is None:
                    self._input_ledger = (self.root / name).open(
                        "a", encoding="utf-8", buffering=1
                    )
                handle = self._input_ledger
                handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                return
            with (self.root / name).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()

    def _close_input_ledger(self) -> None:
        with self._write_lock:
            handle, self._input_ledger = self._input_ledger, None
            self._input_ledger_enabled = False
            if handle is not None:
                handle.flush()
                handle.close()

    def _frame_ref(self, frame: CapturedFrame, trigger: str, action_id: str | None = None) -> dict:
        with self._snapshot_lock:
            if frame.seq in self._frame_refs:
                return self._frame_refs[frame.seq]
            record = self.run.add_frame_raw(frame.pixels, frame.width, frame.height, channels="RGBA",
                                            trigger=trigger, action_id=action_id)
            ref = {"id": record.frame_id, "seq": frame.seq, "mono_ns": frame.mono_ns,
                   "session_id": self.root.name,
                   "wall_ns": frame.wall_ns, "width": frame.width, "height": frame.height,
                   "pts_us": (frame.mono_ns - self.capture.started_mono_ns) // 1000,
                   "path": (self.run.run_dir / record.path).relative_to(self.base).as_posix()}
            self._frame_refs[frame.seq] = ref
            if len(self._frame_refs) > 1200:
                for key in sorted(self._frame_refs)[:600]:
                    del self._frame_refs[key]
            return ref

    def _video_frame_ref(self, frame: CapturedFrame) -> dict:
        # The paced recorder already captures every one of these pixels before
        # the input is injected. Reference that frame instead of re-encoding
        # the same video as sixty separate still images per second.
        return {"id": f"v.{self.root.name}.{frame.seq}", "storage": "video",
                "seq": frame.seq, "mono_ns": frame.mono_ns, "wall_ns": frame.wall_ns,
                "session_id": self.root.name, "width": frame.width, "height": frame.height,
                "pts_us": (frame.mono_ns - self.capture.started_mono_ns) // 1000,
                "manifest": f"{self.root.name}/video-manifest.json"}

    def _classify_observation(self, frame: CapturedFrame, ref: dict) -> dict:
        with Image.frombytes("RGBA", (frame.width, frame.height), frame.pixels) as image:
            rgb = image.convert("RGB")
            screen = _identify_guard_screen(np.asarray(rgb), self.registry)
            reduced = rgb.resize((135, 240), Image.Resampling.BILINEAR)
            ref["visual_hash"] = hashlib.sha256(reduced.tobytes()).hexdigest()
            grey = np.asarray(rgb.convert("L").resize((17, 16), Image.Resampling.BILINEAR))
            ref["perceptual_hash"] = np.packbits(grey[:, 1:] > grey[:, :-1]).tobytes().hex()
            ref["semantic_signature"] = screen.name
        ref.update(screen_id=screen.name, screen_confidence=screen.confidence)
        return ref

    def _frontier_for_frame(self, frame: CapturedFrame, ref: dict) -> dict[str, Any] | None:
        """Submit bounded passive frontier work without waiting in monitor."""

        surface = ref.get("screen_id")
        if not isinstance(surface, str) or self.registry is None:
            return None
        try:
            rgba = np.frombuffer(frame.pixels, dtype=np.uint8).reshape((frame.height, frame.width, 4))
            rgb = rgba[:, :, :3]
            # Include queue, top/bottom HUD, and a coarse whole-surface sample:
            # changes outside the reward badge still invalidate the cache.
            roi = REWARD_QUEUE_ROI
            x1, y1, x2, y2 = (
                round(roi[0] * frame.width / 1080), round(roi[1] * frame.height / 1920),
                round(roi[2] * frame.width / 1080), round(roi[3] * frame.height / 1920),
            )
            signature = b"|".join((
                surface.encode("utf-8"), rgb[y1:y2, x1:x2].tobytes(),
                rgb[: max(1, frame.height // 4) : 8, ::8].tobytes(),
                rgb[-max(1, frame.height // 8) ::8, ::8].tobytes(),
                rgb[::16, ::16].tobytes(),
            ))
            digest = hashlib.sha256(signature).hexdigest()

            if self._frontier_future is not None and self._frontier_future.done():
                future, self._frontier_future = self._frontier_future, None
                try:
                    result = future.result()
                    source_ref, source_surface, source_digest, source_ns = future._rr_source  # type: ignore[attr-defined]
                    evidence = {"source_frame_id": source_ref["id"], "source_frame": source_ref,
                                "source_digest": source_digest, "candidates": result}
                    self._append("frontiers.ndjson", evidence)
                    self._frontier_cache[source_surface] = (
                        source_digest, source_ns,
                        evidence,
                    )
                except Exception as exc:
                    self._append("errors.ndjson", {"wall_ns": time.time_ns(), "error": f"frontier: {type(exc).__name__}: {exc}"})

            cached = self._frontier_cache.get(surface)
            if cached is not None and cached[0] == digest:
                return cached[2]
            if (not self.capture.stopped.is_set() and self._frontier_future is None
                    and (cached is None or frame.mono_ns - cached[1] >= 2_000_000_000)):
                self._submit_frontier(rgba, surface, digest, {}, ref)
            return None
        except Exception as exc:
            # Frontier discovery is advisory.  A recognizer failure must not
            # stop frame classification, capture, or the input path.
            self._append("errors.ndjson", {
                "wall_ns": time.time_ns(),
                "error": f"frontier: {type(exc).__name__}: {exc}",
                "frame_seq": frame.seq,
            })
            return None

    def _submit_frontier(self, rgba: np.ndarray, surface: str, digest: str, semantic_state: dict, ref: dict) -> None:
        if self._frontier_pool is None:
            self._frontier_pool = ProcessPoolExecutor(max_workers=1, mp_context=__import__("multiprocessing").get_context("spawn"))
        future = self._frontier_pool.submit(_frontier_worker, rgba, surface, semantic_state, dict(self.registry.surfaces))
        future._rr_source = (dict(ref), surface, digest, ref["mono_ns"])  # type: ignore[attr-defined]
        self._frontier_future = future

    def _monitor(self) -> None:
        # NumPy/Pillow release the GIL during the expensive image operations.
        # Bound concurrent work and commit in capture-queue order so faster
        # classifiers cannot invent backwards transitions in the interface map.
        pending = deque()
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="rr-identify") as pool:
            while not self.capture.stopped.is_set() or not self._observations.empty() or pending:
                current_frame: CapturedFrame | None = None
                current_ref: dict | None = None
                current_action: dict | None = None
                try:
                    while len(pending) < 6:
                        try:
                            frame, action, trigger = self._observations.get(timeout=0 if pending else .1)
                        except queue.Empty:
                            break
                        ref = dict(self._frame_ref(frame, trigger, action.get("event_id") if action else None))
                        pending.append((pool.submit(self._classify_observation, frame, ref), action, frame, ref))
                    if not pending:
                        continue
                    future, action, current_frame, current_ref = pending.popleft()
                    current_action = action
                    ref = future.result()
                    current_ref = ref
                    if ref.get("screen_id"):
                        self._classified_screens[ref["id"]] = ref["screen_id"]
                        if len(self._classified_screens) > 8192:
                            del self._classified_screens[next(iter(self._classified_screens))]
                    if action:
                        action = dict(action)
                        # Resolve the exact pre-frame after classification.
                        # The last displayed state may be seconds behind, and
                        # later inputs must not invalidate an earlier frame.
                        pre_screen = self._classified_screens.get(action.get("pre_frame_id"))
                        if pre_screen:
                            action["pre_screen_id"] = pre_screen
                        else:
                            action = None
                    frontier = self._frontier_for_frame(current_frame, ref)
                    if frontier is not None:
                        ref["frontier"] = frontier
                    self.map_store.observe(ref, action)
                    self.observation_count += 1
                    self.last_state = ref
                    if self.last_error and self.last_error.startswith("observation:"):
                        self.last_error = None
                except Exception as exc:
                    self.last_error = f"observation: {type(exc).__name__}: {exc}"
                    error = {"wall_ns": time.time_ns(), "error": self.last_error}
                    if current_frame is not None:
                        error["frame_seq"] = current_frame.seq
                    if current_ref is not None:
                        error["frame_ref"] = current_ref
                    if current_action is not None:
                        error["action"] = current_action
                    self._append("errors.ndjson", error)

    STALE_FRAME_NS = 250_000_000
    # A stall is not a dead feed.  2026-09-20 15:57Z: three segments in a row
    # died on "latest frame is stale" while the capture thread was healthy
    # either side of them -- a 2.58 s generation-2 collection in this process
    # froze the capture thread too, and the first remote call to land inside
    # that window refused, which ends a whole segment.  Waiting out a stall
    # costs a moment; refusing costs the run.  A feed that really has stopped
    # produces nothing in this window either, so it still refuses.
    STALE_FRAME_GRACE_NS = 1_500_000_000
    STALE_FRAME_POLL_S = 0.02

    def _require_recording(self) -> CapturedFrame:
        frame = self.capture.latest
        if not self.started or self.capture.error or self.capture.stopped.is_set() or frame is None:
            raise RuntimeError("recording_unavailable")
        if time.perf_counter_ns() - frame.mono_ns > self.STALE_FRAME_NS:
            deadline = time.perf_counter_ns() + self.STALE_FRAME_GRACE_NS
            while time.perf_counter_ns() < deadline:
                time.sleep(self.STALE_FRAME_POLL_S)
                if self.capture.error or self.capture.stopped.is_set():
                    raise RuntimeError("recording_unavailable")
                fresh = self.capture.latest
                if fresh is not None and time.perf_counter_ns() - fresh.mono_ns <= self.STALE_FRAME_NS:
                    frame = fresh
                    break
            else:
                raise RuntimeError("recording_unavailable: latest frame is stale")
        self.run.frame_writer._raise_if_rejected()
        if self.run.frame_writer._fatal_error is not None:
            raise RuntimeError("recording_unavailable: screenshot writer failed")
        return frame

    def set_mode(self, mode: str, client_id: str = "") -> dict:
        if mode not in {"auto", "manual", "paused"}:
            raise ValueError("invalid play mode")
        if mode == "manual" and not client_id:
            raise ValueError("manual control requires a client id")
        with self._input_lock:
            self._require_recording()
            for owner in list({key[0] for key in self._pointers}):
                self.release_client(owner)
            self.mode, self.owner = mode, client_id if mode == "manual" else None
            atomic_json(self.control_path, {"mode": mode, "owner": self.owner, "session": self.root.name,
                                            "updated_wall_ns": time.time_ns()})
            self._append("control.ndjson", {"mode": mode, "owner": self.owner, "wall_ns": time.time_ns()})
            if mode == "auto" and self.supervisor is not None:
                self.registry = SurfaceRegistry(PROJECT_ROOT / "config/game_observatory/romantic_restaurant/navigation.v1.json")
                self.supervisor.resume()
                self.supervisor.ensure_running()
            return self.status()

    def clear_attention(self, actor: str = "person", note: str = "") -> dict:
        """Let a person close an attention episode they have dealt with.

        2026-09-21.  Until this existed the only way out was editing
        attention-state.json and restarting the service, because resume()
        refuses while an episode is open and no child runs to close one.
        """

        if self.supervisor is None:
            raise ValueError("automatic play is not configured for this session")
        self.supervisor.clear_attention(actor=actor, note=note)
        return self.status()

    def input_event(
        self,
        payload: dict,
        client_id: str = "",
        *,
        received_mono_ns: int | None = None,
        received_wall_ns: int | None = None,
        queue_wait_ms: float = 0.0,
        _control_gate_held: bool = False,
    ) -> dict:
        # The API receive pump supplies the timestamps from the instant the
        # websocket payload was received.  Keep those separate from the
        # intent/injection timestamps so queueing can be diagnosed without
        # rewriting the actual arrival time.
        received_mono_ns = received_mono_ns or time.perf_counter_ns()
        received_wall_ns = received_wall_ns or time.time_ns()
        if self.started:
            self._append("inputs.ndjson", {"status": "received", "source": "manual", "payload": payload,
                                          "received_mono_ns": received_mono_ns,
                                          "wall_ns": received_wall_ns,
                                          "received_wall_ns": received_wall_ns,
                                          "queue_wait_ms": max(0.0, queue_wait_ms)})
        client_id = client_id or str(payload.get("client_id", ""))
        event_id = str(payload.get("event_id", ""))
        kind = payload.get("kind")
        if not client_id or not event_id or len(event_id) > 128 or len(client_id) > 128:
            raise ValueError("input identity is missing or invalid")
        if kind not in {"down", "move", "up", "cancel"}:
            raise ValueError("unsupported pointer event")
        seq, pointer_id = payload.get("seq"), payload.get("pointer_id", 1)
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise ValueError("invalid input sequence")
        if isinstance(pointer_id, bool) or not isinstance(pointer_id, int) or not 0 <= pointer_id <= 2**31-1:
            raise ValueError("invalid pointer identity")
        nx, ny = float(payload.get("nx", 0)), float(payload.get("ny", 0))
        if not math.isfinite(nx) or not math.isfinite(ny) or not 0 <= nx <= 1 or not 0 <= ny <= 1:
            raise ValueError("input coordinates must be normalized")
        with self._input_lock:
            cache_key = client_id + ":" + event_id
            if cache_key in self._acks:
                return self._acks[cache_key]
            if seq <= self._client_sequences.get(client_id, -1):
                raise ValueError("input sequence is not increasing")
            if self.mode != "manual" or self.owner != client_id:
                raise RuntimeError("control_handed_over")
            frame = self._require_recording()
            key = (client_id, pointer_id)
            if kind == "down" and key in self._pointers:
                raise ValueError("pointer is already down")
            if kind in {"move", "up"} and key not in self._pointers:
                raise ValueError("pointer is not down")
            finger_id = self._pointers.get(key, {}).get("finger_id")
            if finger_id is None:
                used = {item["finger_id"] for item in self._pointers.values()}
                finger_id = next((value for value in range(1, 11) if value not in used), None)
                if finger_id is None:
                    raise ValueError("too many simultaneous pointers")
            preframe_started = time.perf_counter_ns()
            pre = self._video_frame_ref(frame) if kind == "move" else self._frame_ref(frame, "pre_action", event_id)
            preframe_ms = (time.perf_counter_ns() - preframe_started) / 1e6
            # Every raw pointer input retains its own pre-frame reference and
            # continuous video. Classify gesture boundaries plus the 10 Hz
            # post-input sampling; pointer moves must not wait for image AI.
            if kind != "move":
                self._observations.put((frame, None, "pre_action"), timeout=1)
            x, y = min(round(nx*frame.width), frame.width-1), min(round(ny*frame.height), frame.height-1)
            event = {**payload, "client_id": client_id, "event_id": event_id, "source": "manual",
                     "received_mono_ns": received_mono_ns, "received_wall_ns": received_wall_ns,
                     "wall_ns": time.time_ns(), "queue_wait_ms": max(0.0, queue_wait_ms),
                     "preframe_ms": preframe_ms,
                     "pre_frame": pre, "pre_frame_id": pre["id"], "status": "intent", "x": x, "y": y,
                     "control_gate": "input-priority"}
            gate_started = time.perf_counter_ns()
            intent_logged = False
            sdk_ms = 0.0
            try:
                with (nullcontext() if _control_gate_held else self.capture.control_lock()):
                    event["control_lock_wait_ms"] = (time.perf_counter_ns() - gate_started) / 1e6
                    self._append("inputs.ndjson", event)
                    intent_logged = True
                    sdk_started = time.perf_counter_ns()
                    try:
                        if kind == "down":
                            self._pointers[key] = {"finger_id": finger_id, "event": event, "x": x, "y": y,
                                                   "started_ns": time.perf_counter_ns(), "moved": False}
                            self.capture.source.touch_down(x, y, finger_id=finger_id)
                        elif kind == "move":
                            self.capture.source.touch_move(x, y, finger_id=finger_id)
                            self._pointers[key]["moved"] = True
                        elif key in self._pointers:
                            self.capture.source.touch_up(finger_id=finger_id)
                    finally:
                        sdk_ms = (time.perf_counter_ns() - sdk_started) / 1e6
            except Exception as exc:
                if not intent_logged:
                    event["control_lock_wait_ms"] = (time.perf_counter_ns() - gate_started) / 1e6
                    self._append("inputs.ndjson", event)
                self._append("inputs.ndjson", {"event_id": event_id, "source": "manual", "status": "failed",
                                               "error": f"{type(exc).__name__}: {exc}",
                                               "control_lock_wait_ms": event.get("control_lock_wait_ms"),
                                               "sdk_ms": sdk_ms, "wall_ns": time.time_ns()})
                self.release_client(client_id)
                raise RuntimeError("input_injection_failed") from exc
            event["sdk_ms"] = sdk_ms
            gesture = self._pointers.pop(key, None) if kind in {"up", "cancel"} else None
            now = time.perf_counter_ns()
            self._last_input_ns = now
            self.input_count += 1
            self._last_action = {"event_id": event_id, "kind": kind, "source": "manual", "seq": self.input_count,
                                 "pre_frame_id": pre["id"], "pre_screen_id": (self.last_state or {}).get("screen_id"),
                                 "nx": nx, "ny": ny, "intent": "手动操作"}
            self._client_sequences[client_id] = seq
            ack = {"ok": True, "event_id": event_id, "seq": seq, "source": "manual",
                   "pre_frame_id": pre["id"], "pre_video_seq": frame.seq,
                   "injected_mono_ns": now, "received_mono_ns": event["received_mono_ns"],
                   "received_wall_ns": event["received_wall_ns"],
                   "queue_wait_ms": event["queue_wait_ms"], "preframe_ms": event["preframe_ms"],
                   "control_gate": "input-priority",
                   "control_lock_wait_ms": event["control_lock_wait_ms"], "sdk_ms": sdk_ms}
            self._append("inputs.ndjson", {**ack, "status": "injected", "wall_ns": time.time_ns()})
            self._acks[cache_key] = ack
            if len(self._acks) > 4096:
                del self._acks[next(iter(self._acks))]
            if gesture:
                start = gesture["event"]
                self.run.add_action(ActionRecordV1(
                    run_id=self.run.run_id, action_id=start["event_id"], kind="drag" if gesture["moved"] else "tap",
                    intent="手动拖拽" if gesture["moved"] else "手动点击",
                    params={"source": "manual", "pointer_id": pointer_id, "x1": gesture["x"], "y1": gesture["y"],
                            "x2": x, "y2": y, "release_event_id": event_id},
                    pre_frame_id=start["pre_frame_id"], post_frame_ids=[],
                    duration_ms=(now-gesture["started_ns"])/1e6, outcome="cancelled" if kind == "cancel" else "injected",
                ))
            return ack

    def cancel_input_event(
        self,
        payload: dict,
        client_id: str,
        *,
        received_mono_ns: int,
        received_wall_ns: int,
        queue_wait_ms: float,
        reason: str,
    ) -> dict:
        """Record an accepted but not injected websocket event terminally."""
        return self._record_input_terminal(
            payload, client_id, received_mono_ns=received_mono_ns,
            received_wall_ns=received_wall_ns, queue_wait_ms=queue_wait_ms,
            status="cancelled", reason=reason,
        )

    def _record_input_terminal(
        self,
        payload: dict,
        client_id: str,
        *,
        received_mono_ns: int,
        received_wall_ns: int,
        queue_wait_ms: float,
        status: str,
        reason: str,
    ) -> dict:
        event_id = payload.get("event_id")
        fields = {
            "source": "manual", "payload": payload, "client_id": client_id,
            "received_mono_ns": received_mono_ns, "received_wall_ns": received_wall_ns,
            "queue_wait_ms": max(0.0, queue_wait_ms),
        }
        if self.started:
            self._append("inputs.ndjson", {"status": "received", **fields, "wall_ns": received_wall_ns})
            self._append("inputs.ndjson", {
                "status": status, "event_id": event_id, **fields,
                "reason": reason, "wall_ns": time.time_ns(),
            })
        return {
            "ok": False, "event_id": event_id, "status": status,
            "error": ("input_cancelled: " if status == "cancelled" else "input_failed: ") + reason,
            "received_mono_ns": received_mono_ns, "received_wall_ns": received_wall_ns,
            "queue_wait_ms": max(0.0, queue_wait_ms),
        }

    def release_client(self, client_id: str) -> None:
        with self._input_lock:
            for key, gesture in list(self._pointers.items()):
                if key[0] != client_id:
                    continue
                event_id = "release-" + uuid.uuid4().hex
                frame = self.capture.latest
                pre = self._frame_ref(frame, "pre_action", event_id) if frame and self.run else None
                self._append("inputs.ndjson", {"event_id": event_id, "kind": "cancel", "status": "intent",
                                               "reason": "client_released", "source": "facility",
                                               "client_id": client_id, "pointer_id": key[1], "pre_frame": pre,
                                               "received_mono_ns": time.perf_counter_ns(), "wall_ns": time.time_ns()})
                try:
                    with self.capture.control_lock():
                        self.capture.source.touch_up(finger_id=gesture["finger_id"])
                    self._append("inputs.ndjson", {"event_id": event_id, "kind": "cancel", "status": "injected",
                                                   "source": "facility", "injected_mono_ns": time.perf_counter_ns(),
                                                   "client_id": client_id, "pointer_id": key[1], "wall_ns": time.time_ns()})
                except Exception as exc:
                    self._append("inputs.ndjson", {"event_id": event_id, "status": "failed", "source": "facility",
                                                   "error": f"{type(exc).__name__}: {exc}", "wall_ns": time.time_ns()})
                finally:
                    del self._pointers[key]
            if self.owner == client_id:
                self.mode, self.owner = "paused", None
                atomic_json(self.control_path, {"mode": "paused", "owner": None, "session": self.root.name})

    def auto_rpc(self, method: str, kwargs: dict | None = None) -> Any:
        kwargs = kwargs or {}
        with self._input_lock:
            frame = self._require_recording()
            if method == "connect":
                return self.actuator.connection.model_dump(mode="json")
            if method == "screenshot":
                return {"png_base64": base64.b64encode(self._frame_png(frame)).decode("ascii"), "capture_ms": frame.capture_ms}
            if method == "refresh_display_id":
                return self.actuator.refresh_display_id(reason=str(kwargs.get("reason", "automatic-transition")))
            if method == "observe_business_save_evidence":
                return self.actuator.observe_business_save_evidence(**kwargs)
            if method == "health":
                return {"input_health_events": list(self.actuator.input_health_events),
                        "connection": self.actuator.connection.model_dump(mode="json")}
            if method == "close":
                return None
            if method == "device_command":
                if self.mode != "auto":
                    raise RuntimeError("control_handed_over")
                return self._device_command(kwargs, frame)
            if method != "act":
                raise ValueError("unsupported automatic control method")
            if self.mode != "auto":
                raise RuntimeError("control_handed_over")
            request = ActionRequestV1.model_validate(kwargs["request"])
            event_id = "auto-" + uuid.uuid4().hex
            pre = self._frame_ref(frame, "pre_action", event_id)
            self._observations.put((frame, None, "pre_action"), timeout=1)
            self._append("inputs.ndjson", {"event_id": event_id, "source": "auto", "status": "intent",
                                           "request": request.model_dump(), "pre_frame": pre,
                                           "received_mono_ns": time.perf_counter_ns(), "wall_ns": time.time_ns()})
            try:
                result = self.actuator.act(request)
            except Exception as exc:
                self._append("inputs.ndjson", {"event_id": event_id, "source": "auto", "status": "failed",
                                               "error": f"{type(exc).__name__}: {exc}", "wall_ns": time.time_ns()})
                raise
            self.input_count += request.kind != "wait"
            self._last_input_ns = time.perf_counter_ns()
            self._last_action = {"event_id": event_id, "kind": request.kind, "source": "auto", "seq": self.input_count,
                                 "pre_frame_id": pre["id"], "pre_screen_id": (self.last_state or {}).get("screen_id"),
                                 "nx": request.params.get("x", request.params.get("x1", 0))/frame.width,
                                 "ny": request.params.get("y", request.params.get("y1", 0))/frame.height,
                                 "intent": request.intent}
            self._append("inputs.ndjson", {"event_id": event_id, "source": "auto", "status": "result",
                                           "result": result.model_dump(mode="json"), "wall_ns": time.time_ns()})
            def replay_frame(frame_id: str) -> dict:
                return {"id": frame_id, "path": f"frames/{frame_id}.webp"}
            evidence = {
                "namespace": "shared-stream", "session_id": self.root.name,
                "run_id": self.run.run_id, "frames_root": str(self.run.run_dir),
                "actions_path": str(self.run.run_dir / "actions.ndjson"),
                "pre_frame": replay_frame(result.pre_frame_id) if result.pre_frame_id else None,
                "post_frames": [replay_frame(frame_id) for frame_id in result.post_frame_ids],
                "input_event_id": event_id, "pre_video_frame": pre,
            }
            self._automatic_evidence[result.action_id] = evidence
            if len(self._automatic_evidence) > 4096:
                del self._automatic_evidence[next(iter(self._automatic_evidence))]
            return result.model_dump(mode="json")

    def automatic_evidence(self, action_id: str) -> dict | None:
        with self._input_lock:
            return self._automatic_evidence.get(action_id)

    def _device_command(self, kwargs: dict, frame: CapturedFrame) -> dict:
        """Record only the fixed system transitions used by the existing player."""
        serial = "127.0.0.1:16448"
        package = "com.mergegames.gossipharbor"
        if kwargs.get("serial", serial) != serial or kwargs.get("package", package) != package:
            raise ValueError("device target does not match this play session")
        commands = {
            "home": ["input", "keyevent", "KEYCODE_HOME"],
            "launch": ["am", "start", "-n", package + "/com.microfun.Main"],
            "launcher": ["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
            "force-stop": ["am", "force-stop", package],
        }
        operation = kwargs.get("operation")
        if operation not in commands:
            raise ValueError("unsupported device operation")
        event_id = "system-" + uuid.uuid4().hex
        pre = self._frame_ref(frame, "pre_action", event_id)
        self._observations.put((frame, None, "pre_action"), timeout=1)
        self._append("inputs.ndjson", {"event_id": event_id, "source": "auto", "kind": "device_command",
                                      "operation": operation, "status": "intent", "pre_frame": pre,
                                      "received_mono_ns": time.perf_counter_ns(), "wall_ns": time.time_ns()})
        adb = Path("C:/Program Files/Netease/MuMu/nx_main/adb.exe")
        command = [str(adb), "-s", serial, "shell", *commands[operation]]
        try:
            result = subprocess.run(command, capture_output=True, timeout=30,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            result = subprocess.CompletedProcess(command, -1, b"", f"{type(exc).__name__}: {exc}".encode())
        self.input_count += 1
        self._last_input_ns = time.perf_counter_ns()
        self._last_action = {"event_id": event_id, "kind": "device_command", "source": "auto",
                             "seq": self.input_count, "pre_frame_id": pre["id"],
                             "pre_screen_id": (self.last_state or {}).get("screen_id"),
                             "intent": "游戏存档与前台恢复"}
        receipt = {"returncode": result.returncode, "stdout_base64": base64.b64encode(result.stdout).decode(),
                   "stderr_base64": base64.b64encode(result.stderr).decode()}
        self._append("inputs.ndjson", {"event_id": event_id, "source": "auto", "kind": "device_command",
                                      "operation": operation, "status": "injected" if result.returncode == 0 else "failed",
                                      "returncode": result.returncode,
                                      "injected_mono_ns": self._last_input_ns, "wall_ns": time.time_ns()})
        return receipt

    @staticmethod
    def _frame_png(frame: CapturedFrame) -> bytes:
        with Image.frombytes("RGBA", (frame.width, frame.height), frame.pixels) as image:
            output = io.BytesIO()
            image.save(output, format="PNG", compress_level=1)
            return output.getvalue()

    def frame_jpeg(self) -> bytes:
        frame = self._require_recording()
        with Image.frombytes("RGBA", (frame.width, frame.height), frame.pixels) as image:
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=90)
            return output.getvalue()

    def graph(self) -> dict:
        snapshot = self.map_store.snapshot() if self.map_store else {"entities": [], "states": [], "transitions": []}
        # Read on every request: the catalog is a small hand-edited file, and a
        # stale copy held for the life of the session would hide every edit.
        return summarize_graph(snapshot, load_catalog(INTERFACE_CATALOG_PATH))

    def record_viewer_stats(self, payload: dict) -> dict:
        client_id = payload.get("client_id")
        if not isinstance(client_id, str) or not 1 <= len(client_id) <= 128:
            raise ValueError("invalid viewer identity")
        fields = ("received", "decoded", "presented", "decoderDropped", "renderDropped", "client_perf_ms")
        sample = {"client_id": client_id, "wall_ns": time.time_ns()}
        for key in fields:
            value = payload.get(key)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError("invalid viewer counter")
            sample[key] = value
        sample["visibilityState"] = "visible" if payload.get("visibilityState") == "visible" else "hidden"
        rtt = payload.get("lastAckRttMs")
        if isinstance(rtt, (int, float)) and not isinstance(rtt, bool) and math.isfinite(rtt) and rtt >= 0:
            sample["lastAckRttMs"] = rtt
        with self._write_lock:
            prior = self._viewer_stats.get(client_id)
            if prior and sample["client_perf_ms"] > prior["client_perf_ms"]:
                seconds = (sample["client_perf_ms"] - prior["client_perf_ms"]) / 1000
                for counter in ("received", "decoded", "presented"):
                    if sample[counter] >= prior[counter]:
                        sample[counter + "_fps"] = (sample[counter] - prior[counter]) / seconds
            self._viewer_stats[client_id] = sample
            if self.started:
                self._append("viewers.ndjson", sample)
        return {"ok": True}

    def status(self) -> dict:
        latest = self.capture.latest
        writer = getattr(self.run, "frame_writer", None)
        lag_ms = max(0, latest.mono_ns - self.last_state["mono_ns"]) / 1e6 if latest and self.last_state else None
        return {"started": self.started, "mode": self.mode, "owner": self.owner,
                "session": self.root.name, "root": str(self.root), "control_state_file": str(self.control_path),
                "input_count": self.input_count, "observation_count": self.observation_count,
                "observation_queue": self._observations.qsize(), "observation_lag_ms": lag_ms,
                "observation_gaps": self.observation_gaps,
                "frame_writer": {"pending": len(getattr(writer, "_pending", {})),
                                 "capacity": getattr(writer, "queue_size", None),
                                 "workers": getattr(writer, "frame_workers", None),
                                 "webp_method": getattr(writer, "webp_method", None),
                                 "lost": getattr(writer, "lost_count", 0)},
                "automatic": self.supervisor.status() if self.supervisor is not None else None,
                "viewer_stats": list(self._viewer_stats.values()),
                "capture": self.capture.status(), "last_observation": self.last_state,
                "error": self.last_error or self.capture.error}

    def stop(self) -> dict:
        with self._input_lock:
            try:
                for owner in list({key[0] for key in self._pointers}):
                    self.release_client(owner)
                self.mode = "paused"
                atomic_json(self.control_path, {"mode": "paused", "owner": None, "session": self.root.name})
                if self.supervisor is not None:
                    self.supervisor.stop()
                self.capture.stop()
                if self._frontier_pool is not None:
                    self._frontier_pool.shutdown(wait=False, cancel_futures=True)
                if self._monitor_thread:
                    self._monitor_thread.join(timeout=20)
                if self.run:
                    self.run.close("operator_stop", {"input_count": self.input_count})
            finally:
                self._close_input_ledger()
                self.started = False
            return self.status()


_session: PlaySession | None = None
_session_lock = threading.Lock()


def get_session() -> PlaySession:
    global _session
    with _session_lock:
        if _session is None or (_session.capture.stopped.is_set() and not _session.started):
            _session = PlaySession(Path(os.environ.get("RR_STREAM_ROOT", str(DEFAULT_ROOT))))
        return _session
