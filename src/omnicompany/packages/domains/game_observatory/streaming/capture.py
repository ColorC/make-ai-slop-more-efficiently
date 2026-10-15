"""One paced MuMu capture feed, FFmpeg encoding and durable video segments.

FFmpeg (via PyAV) owns codec framing and MP4 muxing. The browser receives whole
encoded access units with their capture timestamps, never a guessed NAL timer.
Slow viewers resynchronize at a keyframe; the recording is independent of them.
"""

from __future__ import annotations

import gc
import json
import queue
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable
from collections import deque

from ..delegation.mumu_ipc import MuMuIpc
from .interface_map import _replace_with_permission_retry


MANIFEST_RETRY_DELAYS_S = (0.05, 0.1, 0.2, 0.5)


@dataclass(frozen=True)
class CapturedFrame:
    seq: int
    mono_ns: int
    wall_ns: int
    pixels: bytes
    width: int
    height: int
    capture_ms: float


def pack_video(metadata: dict[str, Any], payload: bytes) -> bytes:
    header = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    return len(header).to_bytes(4, "big") + header + payload


# A dropped MuMu renderer IPC handle, or a fault inside its DLL, is a facility
# blink under long runs (four of them on 2026-09-16, one every 20-40 minutes).
# Reconnect a bounded number of times per window so a genuinely dead emulator
# still stops the feed instead of spinning.
SOURCE_RECOVERY_LIMIT = 5
SOURCE_RECOVERY_WINDOW_S = 120.0
SOURCE_RECOVERY_BACKOFF_S = 0.5


class CaptureFeed:
    """Continuous recorder and bounded per-viewer live delivery."""

    def __init__(
        self, root: Path, *, fps: int = 60, segment_seconds: float = 60,
        source_factory: Callable[[], Any] = MuMuIpc,
        encoder: str = "h264_nvenc", timing_sample_every: int = 30,
        idle_fps: int = 12, idle_after_s: float = 10.0,
    ) -> None:
        if not 1 <= fps <= 120 or segment_seconds <= 0:
            raise ValueError("invalid capture rate or segment duration")
        if not 1 <= idle_fps <= fps or idle_after_s < 0:
            raise ValueError("invalid idle capture rate")
        if isinstance(timing_sample_every, bool) or not isinstance(timing_sample_every, int) or timing_sample_every < 1:
            raise ValueError("invalid timing sample interval")
        self.root = Path(root).resolve()
        self.fps = fps
        # 2026-09-20: nobody is watching for most of a multi-day run, and
        # capturing 1080x1920 RGBA sixty times a second to encode video no one
        # will ever play cost a whole core of a machine the run shares.  The
        # rate drops once the last viewer has been gone for ``idle_after_s``
        # and is restored the moment one subscribes, so the 60 Hz stream this
        # facility exists to serve is unchanged for anyone actually looking.
        # Automatic play reads ``latest`` rather than the stream, and its
        # actuator already settles 120 ms after each input, so the coarser idle
        # frame clock is invisible to it.
        self.idle_fps = idle_fps
        self.idle_after_s = float(idle_after_s)
        self._last_viewer_ns: int = 0
        self.segment_seconds = segment_seconds
        self.source_factory = source_factory
        self.encoder = encoder
        self.timing_sample_every = timing_sample_every
        self.latest: CapturedFrame | None = None
        self.lock = threading.RLock()
        self._control_condition = threading.Condition(self.lock)
        self._control_waiters = 0
        self._capture_active = False
        self._subscriber_lock = threading.Lock()
        self._manifest_lock = threading.Lock()
        self._manifest_retry_wakeup = threading.Event()
        self._manifest_stop = threading.Event()
        self._manifest_retry_thread: threading.Thread | None = None
        self._manifest_snapshot: dict[str, Any] | None = None
        self._manifest_pending = False
        self._manifest_retry_attempts = 0
        self._manifest_write_failures = 0
        self._manifest_last_error: str | None = None
        self._manifest_last_success_wall_ns = 0
        self._manifest_stopping = False
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self.force_keyframe = threading.Event()
        self.frames: queue.Queue[CapturedFrame | None] = queue.Queue(maxsize=12)
        self.subscribers: dict[queue.Queue[bytes], bool] = {}
        self.threads: list[threading.Thread] = []
        self.source: Any = None
        self.error: str | None = None
        # Bounded recovery from a dropped or faulting capture source.
        self.source_recoveries = 0
        self.last_source_error: str | None = None
        self._source_recovery_times: list[float] = []
        self.started_mono_ns = 0
        self.started_wall_ns = 0
        self.captured = 0
        self.encoded = 0
        self.viewer_resyncs = 0
        self.capture_late = 0
        self.segments: list[dict[str, Any]] = []
        self._capture_samples: list[float] = []
        self._gc_events: deque[tuple[int, float, int]] = deque(maxlen=64)
        self._gc_callback: Callable[[str, dict], None] | None = None
        self.on_frame: Callable[[CapturedFrame], None] | None = None

    def start(self) -> None:
        if self.threads and any(t.is_alive() for t in self.threads):
            return
        if self.threads:
            raise RuntimeError("a stopped capture feed cannot be restarted")
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "video").mkdir(exist_ok=True)
        self._manifest_stopping = False
        self._manifest_stop.clear()
        self.started_mono_ns = time.perf_counter_ns()
        self.started_wall_ns = time.time_ns()
        self._install_gc_probe()
        self.threads = [
            threading.Thread(target=self._capture, name="rr-capture", daemon=True),
            threading.Thread(target=self._encode, name="rr-encode", daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def _reconnect_source(self, exc: Exception) -> bool:
        """Re-open the capture source after a transient failure.

        Returns ``False`` when the budget is spent, which lets the caller fail
        the feed the way it always did.
        """

        now = time.monotonic()
        window_start = now - SOURCE_RECOVERY_WINDOW_S
        self._source_recovery_times = [
            stamp for stamp in self._source_recovery_times if stamp >= window_start
        ]
        if len(self._source_recovery_times) >= SOURCE_RECOVERY_LIMIT:
            return False
        self._source_recovery_times.append(now)
        self.source_recoveries += 1
        self.last_source_error = f"{type(exc).__name__}: {exc}"
        source = self.source
        self.source = None
        if source is not None:
            try:
                source.disconnect()
            except Exception:
                pass
        time.sleep(SOURCE_RECOVERY_BACKOFF_S)
        try:
            self.source = self.source_factory()
            self.source.connect()
        except Exception:
            return False
        return True

    def _fail(self, exc: BaseException) -> None:
        self.error = f"{type(exc).__name__}: {exc}"
        self.stopped.set()
        with self._control_condition:
            self._control_condition.notify_all()
        self.ready.set()

    @contextmanager
    def control_lock(self):
        """Serialize device control and let waiting control preempt capture."""
        acquired = False
        with self._control_condition:
            self._control_waiters += 1
            self._control_condition.notify_all()
            try:
                while self._capture_active and not self.stopped.is_set():
                    self._control_condition.wait(timeout=0.1)
                if self.stopped.is_set():
                    raise RuntimeError("capture stopped")
                self._control_waiters -= 1
                acquired = True
                self._control_condition.notify_all()
                yield
            finally:
                if not acquired:
                    self._control_waiters -= 1
                self._control_condition.notify_all()

    def effective_fps(self, now_ns: int | None = None) -> int:
        """The rate this instant, which is the idle one only with no viewers."""

        if self.subscribers:
            self._last_viewer_ns = now_ns if now_ns is not None else time.perf_counter_ns()
            return self.fps
        if self._last_viewer_ns == 0:
            return self.idle_fps
        now = now_ns if now_ns is not None else time.perf_counter_ns()
        gone_s = (now - self._last_viewer_ns) / 1e9
        return self.idle_fps if gone_s >= self.idle_after_s else self.fps

    def _capture(self) -> None:
        period = 1_000_000_000 // self.fps
        try:
            self.source = self.source_factory()
            self.source.connect()
            due = time.perf_counter_ns()
            while not self.stopped.is_set():
                now = time.perf_counter_ns()
                period = 1_000_000_000 // self.effective_fps(now)
                if now < due:
                    # Python sleep uses a high-resolution waitable timer on Windows;
                    # Event.wait may round each frame to the 15.6 ms system tick.
                    time.sleep((due - now) / 1e9)
                if self.stopped.is_set():
                    break
                with self._control_condition:
                    while self._control_waiters and not self.stopped.is_set():
                        self._control_condition.wait(timeout=0.1)
                    if self.stopped.is_set():
                        break
                    self._capture_active = True
                    frame = None
                    try:
                        pixels, width, height = self.source.capture()
                        self.captured += 1
                        timing = getattr(self.source, "last_capture_timing", None)
                        frame = CapturedFrame(
                            self.captured, time.perf_counter_ns(), time.time_ns(),
                            pixels, width, height, float(getattr(timing, "total_ms", 0)),
                        )
                        self.latest = frame
                        self._capture_samples.append(frame.capture_ms)
                        if len(self._capture_samples) > 600:
                            del self._capture_samples[:300]
                    except Exception as exc:
                        # The MuMu renderer IPC drops its handle and even faults
                        # inside the DLL under long runs.  That is a facility
                        # blink, not a reason to end the recording: reconnect a
                        # bounded number of times, leave the gap visible in the
                        # frame counters, and only give up when it keeps failing.
                        if not self._reconnect_source(exc):
                            raise
                    finally:
                        self._capture_active = False
                        self._control_condition.notify_all()
                if frame is None:
                    due = time.perf_counter_ns()
                    continue
                # A stop requested during capture must still retain the frame
                # that just completed. Only an encoder failure cancels enqueue.
                while not self.error:
                    try:
                        self.frames.put(frame, timeout=.1)
                        break
                    except queue.Full:
                        continue
                if self.on_frame:
                    self.on_frame(frame)
                due += period
                now = time.perf_counter_ns()
                if now - due > period:
                    self.capture_late += 1
                    # Do not invent a burst of duplicated frames after a stall.
                    due = now
        except BaseException as exc:
            self._fail(exc)
        finally:
            while True:
                try:
                    self.frames.put(None, timeout=.1)
                    break
                except queue.Full:
                    if self.error:
                        break
            if self.source:
                try:
                    self.source.disconnect()
                except Exception:
                    pass

    def _encode(self) -> None:
        container = None
        try:
            import av
            segment = None
            stream = None
            pending: dict[int, CapturedFrame] = {}
            encode_timings: dict[int, float] = {}
            segment_index = 0
            segment_duration_us = int(self.segment_seconds * 1e6)
            next_boundary_us: int | None = None
            with (self.root / "video-frames.ndjson").open("a", encoding="utf-8", buffering=1) as ledger:
                while True:
                    try:
                        frame = self.frames.get(timeout=.5)
                    except queue.Empty:
                        if self.stopped.is_set():
                            break
                        continue
                    if frame is None:
                        break
                    relative_us = (frame.mono_ns - self.started_mono_ns) // 1000
                    if container is None:
                        pattern = str(self.root / "video/segment-%06d.mp4")
                        container = av.open(pattern, "w", format="segment", options={
                            "segment_time": str(self.segment_seconds),
                            "reset_timestamps": "1",
                            "segment_format": "mp4",
                            "segment_format_options": "movflags=frag_keyframe+empty_moov+default_base_moof",
                        })
                        stream = container.add_stream(self.encoder, rate=self.fps)
                        stream.codec_context.flags |= av.codec.context.Flags.global_header
                        stream.width, stream.height = frame.width, frame.height
                        stream.pix_fmt = "rgba" if self.encoder == "h264_nvenc" else "yuv420p"
                        stream.bit_rate = 6_000_000
                        stream.time_base = Fraction(1, 1_000_000)
                        stream.codec_context.time_base = Fraction(1, 1_000_000)
                        stream.options = ({
                            "preset": "p1", "tune": "ull", "zerolatency": "1",
                            "delay": "0", "rc-lookahead": "0", "bf": "0",
                            "g": str(self.fps), "profile": "baseline",
                        } if self.encoder == "h264_nvenc" else {
                            "preset": "ultrafast", "tune": "zerolatency",
                            "bf": "0", "g": str(self.fps), "profile": "baseline",
                        })
                    video_frame = av.VideoFrame(frame.width, frame.height, "rgba")
                    video_frame.planes[0].update(frame.pixels)
                    video_frame.pts = relative_us
                    video_frame.time_base = Fraction(1, 1_000_000)
                    if self.force_keyframe.is_set():
                        video_frame.pict_type = av.video.frame.PictureType.I
                        self.force_keyframe.clear()
                    pending[relative_us] = frame
                    encode_started_ns = time.perf_counter_ns()
                    encoded_packets = list(stream.encode(video_frame))
                    encode_ms = (time.perf_counter_ns() - encode_started_ns) / 1e6
                    encode_timings[frame.seq] = encode_ms
                    for packet in encoded_packets:
                        packet_pts_us = round(packet.pts * packet.time_base * 1_000_000)
                        if segment is None or (
                            packet.is_keyframe and next_boundary_us is not None and packet_pts_us >= next_boundary_us
                        ):
                            if segment is not None:
                                segment["complete"] = True
                                segment_index += 1
                            name = f"video/segment-{segment_index:06d}.mp4"
                            first_frame = pending.get(packet_pts_us, frame)
                            segment = {"path": name, "start_pts_us": packet_pts_us,
                                       "first_seq": first_frame.seq,
                                       "start_wall_ns": first_frame.wall_ns, "complete": False}
                            self.segments.append(segment)
                            next_boundary_us = ((packet_pts_us // segment_duration_us) + 1) * segment_duration_us
                        self._publish_packet(
                            packet, pending, segment, ledger, container, stream,
                            encode_timings=encode_timings,
                        )
                if container:
                    for packet in stream.encode():
                        packet_pts_us = round(packet.pts * packet.time_base * 1_000_000)
                        if segment is None:
                            segment = {"path": f"video/segment-{segment_index:06d}.mp4",
                                       "start_pts_us": packet_pts_us, "first_seq": frame.seq,
                                       "start_wall_ns": frame.wall_ns, "complete": False}
                            self.segments.append(segment)
                        self._publish_packet(packet, pending, segment, ledger, container, stream,
                                             encode_timings=encode_timings)
                    container.close()
                    container = None
                    if segment is not None:
                        segment["complete"] = True
        except BaseException as exc:
            self._fail(exc)
        finally:
            if container:
                try:
                    container.close()
                except Exception:
                    pass
            self._write_manifest()

    def _publish_packet(self, packet: Any, pending: dict[int, CapturedFrame], segment: dict,
                        ledger: Any, container: Any, stream: Any,
                        *, encode_timings: dict[int, float] | None = None) -> None:
        pts_us = round(packet.pts * packet.time_base * 1_000_000)
        frame = pending.pop(pts_us)
        encode_ms = encode_timings.pop(frame.seq, None) if encode_timings is not None else None
        payload = bytes(packet)
        # MP4 owns global headers. WebCodecs Annex B keyframes need those same
        # FFmpeg-produced SPS/PPS bytes prepended, without a custom NAL parser.
        extra = stream.codec_context.extradata or b""
        if packet.is_keyframe and extra:
            payload = extra + payload
        sps_offset = extra.find(b"\x00\x00\x00\x01\x67")
        codec = "avc1." + extra[sps_offset+5:sps_offset+8].hex() if sps_offset >= 0 else "avc1.42c02a"
        # The segment muxer resets MP4 timestamps. Live metadata keeps the
        # absolute encoder PTS used for cross-frame evidence.
        packet.stream = stream
        mux_started_ns = time.perf_counter_ns()
        container.mux(packet)
        mux_ms = (time.perf_counter_ns() - mux_started_ns) / 1e6
        metadata = {"seq": frame.seq, "pts_us": pts_us, "keyframe": packet.is_keyframe,
                    "width": frame.width, "height": frame.height, "codec": codec,
                    "captured_mono_ns": frame.mono_ns, "captured_wall_ns": frame.wall_ns,
                    "segment": segment["path"], "segment_time_us": pts_us - segment["start_pts_us"]}
        timing_sample = frame.seq % self.timing_sample_every == 0
        # Measure the normal framing path once. A second compact JSON framing
        # pass occurs only for periodic samples or an actual >30 ms outlier.
        wire_started_ns = time.perf_counter_ns()
        wire = pack_video(metadata, payload)
        publish_ms = (time.perf_counter_ns() - wire_started_ns) / 1e6
        if timing_sample or frame.capture_ms > 30 or (encode_ms or 0) > 30 or mux_ms > 30 or publish_ms > 30:
            # Keep the diagnostic payload small and sparse. published_wall_ns
            # is a host wall-clock anchor emitted with the same sequence before
            # subscriber enqueue (it is not an enqueue-completion timestamp).
            metadata["timing"] = {
                "capture_ms": frame.capture_ms,
                "encode_ms": encode_ms,
                "mux_ms": mux_ms,
                "publish_ms": publish_ms,
            }
            timing_now_ns = time.perf_counter_ns()
            metadata["timing"]["gc_pause_ms"] = sum(
                duration for start_ns, duration, _generation in tuple(self._gc_events)
                if frame.mono_ns <= start_ns <= timing_now_ns
            )
            metadata["timing"]["published_wall_ns"] = time.time_ns()
            wire = pack_video(metadata, payload)
        # Source PTS remains in the ledger and live protocol; MP4 starts at zero.
        ledger.write(json.dumps(metadata, separators=(",", ":")) + "\n")
        segment["last_seq"] = frame.seq
        segment["last_pts_us"] = pts_us
        self.encoded += 1
        self.ready.set()
        with self._subscriber_lock:
            for subscriber, needs_keyframe in list(self.subscribers.items()):
                if needs_keyframe and not metadata["keyframe"]:
                    continue
                try:
                    subscriber.put_nowait(wire)
                    self.subscribers[subscriber] = False
                except queue.Full:
                    while not subscriber.empty():
                        try:
                            subscriber.get_nowait()
                        except queue.Empty:
                            break
                    self.subscribers[subscriber] = True
                    self.viewer_resyncs += 1
                    self.force_keyframe.set()
        if frame.seq % self.fps == 0:
            self._write_manifest()

    def _write_manifest(self) -> None:
        """Publish the latest manifest without allowing filesystem locks to kill capture.

        The encode loop calls this periodically and from its ``finally`` block.
        It only coalesces a status snapshot and wakes one daemon; all filesystem
        I/O, including the atomic replace and retries, happens in that daemon.
        The last snapshot remains pending until a later attempt succeeds.
        """

        if not self.root.exists():
            return
        snapshot = self.status()
        with self._manifest_lock:
            self._manifest_snapshot = snapshot
            self._manifest_pending = True
            self._manifest_retry_wakeup.set()
        self._ensure_manifest_retry_thread()

    def _publish_manifest(self, snapshot: dict[str, Any]) -> None:
        """Write a complete temp file and atomically replace the public file.

        Only the manifest writer calls this method.  Keeping the temp name
        stable avoids leaving a family of stale files after an external reader
        releases its handle.  The state lock is deliberately not held during
        filesystem I/O or backoff, so a periodic snapshot request never waits
        behind a sharing violation.
        """

        path = self.root / "video-manifest.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        _replace_with_permission_retry(str(temp), path)

    def _record_manifest_failure(self, exc: BaseException) -> None:
        self._manifest_write_failures += 1
        self._manifest_retry_attempts += 1
        self._manifest_last_error = f"{type(exc).__name__}: {exc}"

    def _ensure_manifest_retry_thread(self) -> None:
        with self._manifest_lock:
            thread = self._manifest_retry_thread
            if thread is not None and thread.is_alive():
                return
            self._manifest_retry_thread = threading.Thread(
                target=self._manifest_retry_loop,
                name="rr-manifest-retry",
                daemon=True,
            )
            self._manifest_retry_thread.start()

    def _manifest_retry_loop(self) -> None:
        try:
            retry_index = 0
            attempted_snapshot: dict[str, Any] | None = None
            while True:
                self._manifest_retry_wakeup.wait()
                self._manifest_retry_wakeup.clear()
                with self._manifest_lock:
                    snapshot = self._manifest_snapshot
                    stopping = self._manifest_stopping
                    if not self._manifest_pending or snapshot is None:
                        if stopping:
                            return
                        continue
                if snapshot is not attempted_snapshot:
                    retry_index = 0
                    attempted_snapshot = snapshot
                try:
                    self._publish_manifest(snapshot)
                except Exception as exc:
                    with self._manifest_lock:
                        self._record_manifest_failure(exc)
                        stopping = self._manifest_stopping
                    if stopping:
                        return
                    if retry_index >= len(MANIFEST_RETRY_DELAYS_S):
                        retry_index = 0
                        continue
                    delay = MANIFEST_RETRY_DELAYS_S[retry_index]
                    retry_index += 1
                    # Do not let periodic snapshot requests turn a failed
                    # publish into a busy loop.  A stop interrupts the
                    # backoff and performs one final bounded attempt.
                    self._manifest_stop.wait(delay)
                    self._manifest_retry_wakeup.set()
                    continue
                with self._manifest_lock:
                    # A newer snapshot may have arrived while the writer was
                    # in the filesystem call.  Keep it pending for the next
                    # pass rather than publishing an older status as current.
                    if self._manifest_snapshot is snapshot:
                        self._manifest_pending = False
                        self._manifest_retry_attempts = 0
                    self._manifest_last_success_wall_ns = time.time_ns()
                    stopping = self._manifest_stopping
                    newer_pending = self._manifest_pending
                retry_index = 0
                if stopping:
                    return
                if newer_pending:
                    self._manifest_retry_wakeup.set()
        finally:
            with self._manifest_lock:
                self._manifest_retry_thread = None

    def subscribe(self) -> queue.Queue[bytes]:
        subscriber: queue.Queue[bytes] = queue.Queue(maxsize=12)
        with self._subscriber_lock:
            self.subscribers[subscriber] = True
            self.force_keyframe.set()
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[bytes]) -> None:
        with self._subscriber_lock:
            self.subscribers.pop(subscriber, None)

    def status(self) -> dict[str, Any]:
        elapsed = max((time.perf_counter_ns() - self.started_mono_ns) / 1e9, .001) if self.started_mono_ns else 0
        samples = sorted(self._capture_samples)
        return {"running": bool(self.threads) and not self.stopped.is_set() and self.error is None,
                "target_fps": self.fps, "captured": self.captured, "encoded": self.encoded,
                "elapsed_s": elapsed, "capture_fps": self.captured / elapsed if elapsed else 0,
                "encode_fps": self.encoded / elapsed if elapsed else 0,
                "capture_p95_ms": samples[int((len(samples)-1)*.95)] if samples else None,
                "effective_fps": self.effective_fps(), "idle_fps": self.idle_fps,
                "late_capture_intervals": self.capture_late, "viewer_resyncs": self.viewer_resyncs,
                "viewers": len(self.subscribers), "error": self.error,
                "source_recoveries": self.source_recoveries,
                "last_source_error": self.last_source_error,
                "manifest": {
                    "stale": self._manifest_pending,
                    "pending": self._manifest_pending,
                    "write_failures": self._manifest_write_failures,
                    "retry_attempts": self._manifest_retry_attempts,
                    "last_error": self._manifest_last_error,
                    "last_success_wall_ns": self._manifest_last_success_wall_ns,
                },
                "gc_events": [
                    {"start_mono_ns": start_ns, "duration_ms": duration, "generation": generation}
                    for start_ns, duration, generation in tuple(self._gc_events)
                ],
                "started_wall_ns": self.started_wall_ns, "segments": list(self.segments)}

    def stop(self) -> None:
        self._manifest_stopping = True
        self._manifest_stop.set()
        self._manifest_retry_wakeup.set()
        self.stopped.set()
        with self._control_condition:
            self._control_condition.notify_all()
        for thread in self.threads:
            thread.join(timeout=10)
        retry_thread = self._manifest_retry_thread
        if retry_thread is not None and retry_thread is not threading.current_thread():
            retry_thread.join(timeout=1)
        self._remove_gc_probe()
        self._write_manifest()
        retry_thread = self._manifest_retry_thread
        if retry_thread is not None and retry_thread is not threading.current_thread():
            retry_thread.join(timeout=1)

    def _install_gc_probe(self) -> None:
        started_ns: int | None = None

        def callback(phase: str, info: dict) -> None:
            nonlocal started_ns
            now = time.perf_counter_ns()
            if phase == "start":
                started_ns = now
            elif phase == "stop" and started_ns is not None:
                generation = info.get("generation", -1)
                duration_ms = (now - started_ns) / 1e6
                generation = int(generation)
                if duration_ms >= 1 or generation == 2:
                    self._gc_events.append((started_ns, duration_ms, generation))
                started_ns = None

        self._gc_callback = callback
        gc.callbacks.append(callback)

    def _remove_gc_probe(self) -> None:
        callback, self._gc_callback = self._gc_callback, None
        if callback is not None:
            try:
                gc.callbacks.remove(callback)
            except ValueError:
                pass
