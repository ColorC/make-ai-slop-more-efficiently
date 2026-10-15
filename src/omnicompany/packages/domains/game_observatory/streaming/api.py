"""HTTP and WebSocket surface for the Romantic Restaurant play room."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
import json
import math
import mimetypes
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse

from .session import get_session

router = APIRouter(tags=["game-observatory-play"])
_WEB = Path(__file__).resolve().parents[1] / "web"
_SESSION_BOOTSTRAP = (
    Path(__file__).resolve().parents[4]
    / "dashboard/frontend/src/lib/sessionBootstrap.ts"
)
_SESSION_BOOTSTRAP_JS = _WEB / "session-bootstrap.js"
_CYTOSCAPE = (
    Path(__file__).resolve().parents[4]
    / "dashboard/frontend/node_modules/cytoscape/dist/cytoscape.min.js"
)


def _session() -> Any:
    return get_session()


def _same_origin(ws: WebSocket) -> bool:
    """Allow native sockets without Origin and browser same-origin sockets."""
    origin = ws.headers.get("origin")
    if not origin:
        return True
    expected = (ws.headers.get("x-forwarded-host") or ws.headers.get("host") or "").split(",", 1)[0].strip()
    parsed = urlsplit(origin)
    return bool(parsed.netloc) and parsed.netloc.casefold() == expected.casefold()


async def _reject_cross_origin(ws: WebSocket) -> bool:
    if _same_origin(ws):
        return False
    await ws.close(code=1008, reason="cross-origin websocket")
    return True


async def _call(method: str, *args: Any, **kwargs: Any) -> Any:
    session = _session()
    return await asyncio.to_thread(getattr(session, method), *args, **kwargs)


@router.get("/game-observatory/play/", response_class=HTMLResponse)
def play_page() -> HTMLResponse:
    return HTMLResponse((_WEB / "play.html").read_text(encoding="utf-8"))


@router.get("/game-observatory/play/play.css")
def play_css() -> FileResponse:
    return FileResponse(_WEB / "play.css", media_type="text/css")


@router.get("/game-observatory/play/play.js")
def play_js() -> FileResponse:
    return FileResponse(_WEB / "play.js", media_type="text/javascript")


@router.get("/game-observatory/play/session-bootstrap.js")
def session_bootstrap_js() -> FileResponse:
    if not _SESSION_BOOTSTRAP.is_file() or not _SESSION_BOOTSTRAP_JS.is_file():
        raise HTTPException(status_code=404, detail="session bootstrap unavailable")
    return FileResponse(_SESSION_BOOTSTRAP_JS, media_type="text/javascript")


@router.get("/game-observatory/play/play-map.css")
def play_map_css() -> FileResponse:
    return FileResponse(_WEB / "play-map.css", media_type="text/css")


@router.get("/game-observatory/play/play-map.js")
def play_map_js() -> FileResponse:
    return FileResponse(_WEB / "play-map.js", media_type="text/javascript")


@router.get("/game-observatory/play/cytoscape.min.js")
def cytoscape_js() -> FileResponse:
    if not _CYTOSCAPE.is_file():
        raise HTTPException(status_code=404, detail="graph renderer unavailable")
    return FileResponse(_CYTOSCAPE, media_type="text/javascript")


@router.get("/api/game-observatory/play/status")
async def play_status() -> Any:
    return await _call("status")


def _recent_actions(base: Path, limit: int) -> list[dict[str, Any]]:
    """Read bounded ledger tails across recording restarts, preserving frame roots."""
    base = base.resolve()
    paths = sorted(base.glob("session-*/replay/romantic-restaurant/run.*/actions.ndjson"),
                   key=lambda path: path.stat().st_mtime_ns, reverse=True)
    actions: list[dict[str, Any]] = []
    for path in paths[:12]:
        if not path.resolve().is_relative_to(base):
            continue
        with path.open("rb") as handle:
            handle.seek(0, 2)
            start = max(0, handle.tell() - 65536)
            handle.seek(start)
            raw = handle.read(65536)
        lines = raw.splitlines()
        if start:
            lines = lines[1:]
        if raw and not raw.endswith((b"\n", b"\r")):
            lines = lines[:-1]
        prefix = path.parent.relative_to(base).as_posix()
        for line in lines:
            try:
                action = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(action, dict) and isinstance(action.get("action_id"), str):
                actions.append({**action, "replay_prefix": prefix})
        if len(actions) >= limit:
            break
    def timestamp(action: dict[str, Any]) -> float:
        value = action.get("t_wall")
        try:
            if isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            return float(value or 0)
        except (ValueError, TypeError):
            return 0.0
    unique = {(item["replay_prefix"], item["action_id"]): item for item in actions}
    return sorted(unique.values(), key=timestamp, reverse=True)[:limit]


@router.get("/api/game-observatory/play/recent-actions")
async def play_recent_actions(limit: int = 40) -> dict[str, Any]:
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="invalid action limit")
    return {"actions": await asyncio.to_thread(_recent_actions, _session().base, limit)}


@router.get("/api/game-observatory/play/frame.jpg")
async def play_frame() -> Response:
    return Response(content=await _call("frame_jpeg"), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@router.get("/api/game-observatory/play/graph")
async def play_graph() -> Any:
    try:
        return await _call("graph")
    except Exception as exc:
        # The service runs with its output discarded, so a bare 500 left the
        # map page saying only "暂时无法加载".  Name the failure in the response.
        raise HTTPException(
            status_code=500, detail=f"interface map snapshot failed: {type(exc).__name__}: {exc}"
        ) from exc


@router.get("/api/game-observatory/play/observations")
async def play_observations(offset: int = 0, limit: int = 100, entity_id: str | None = None) -> Any:
    if offset < 0 or limit < 1 or limit > 1000:
        raise HTTPException(status_code=422, detail="invalid observation page")
    session = _session()
    method = getattr(session, "observations", None)
    if method is None:
        store = getattr(session, "map_store", None)
        method = getattr(store, "observations", None)
    if method is None:
        raise HTTPException(status_code=503, detail="observations unavailable")
    try:
        return await asyncio.to_thread(method, offset, limit, entity_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/game-observatory/play/observation/{frame_id}")
async def play_observation(frame_id: str) -> Any:
    if not frame_id or len(frame_id) > 256 or "/" in frame_id or "\\" in frame_id:
        raise HTTPException(status_code=404, detail="observation not found")
    session = _session()
    method = getattr(session, "observation_by_id", None)
    if method is None:
        method = getattr(getattr(session, "map_store", None), "observation_by_id", None)
    if method is None:
        raise HTTPException(status_code=503, detail="observations unavailable")
    try:
        observation = await asyncio.to_thread(method, frame_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if observation is None:
        raise HTTPException(status_code=404, detail="observation not found")
    return observation


@router.post("/api/game-observatory/play/viewer-stats")
async def play_viewer_stats(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(status_code=413, detail="viewer stats payload too large")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="viewer stats must be JSON") from exc
    required = {
        "client_id", "received", "decoded", "presented", "decoderDropped",
        "renderDropped", "client_perf_ms",
    }
    optional = {"visibilityState", "lastAckRttMs"}
    if not isinstance(payload, dict) or not required.issubset(payload) or set(payload) - required - optional:
        raise HTTPException(status_code=422, detail="invalid viewer stats fields")
    if not isinstance(payload["client_id"], str) or not payload["client_id"] or len(payload["client_id"]) > 128:
        raise HTTPException(status_code=422, detail="invalid viewer stats client")
    for name in ("received", "decoded", "presented", "decoderDropped", "renderDropped"):
        value = payload[name]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HTTPException(status_code=422, detail=f"invalid viewer stats {name}")
    for name in ("client_perf_ms", "lastAckRttMs"):
        value = payload.get(name)
        if name == "client_perf_ms" and value is None:
            raise HTTPException(status_code=422, detail=f"invalid viewer stats {name}")
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise HTTPException(status_code=422, detail=f"invalid viewer stats {name}")
    if "visibilityState" in payload and payload["visibilityState"] not in {"visible", "hidden", "prerender", "unloaded"}:
        raise HTTPException(status_code=422, detail="invalid viewer stats visibility")
    try:
        await _call("record_viewer_stats", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/game-observatory/play/start")
async def play_start() -> Any:
    await _call("start")
    return await _call("status")


@router.post("/api/game-observatory/play/stop")
async def play_stop() -> Any:
    session = _session()
    if not session.started:
        return await _call("status")
    return await _call("stop")


@router.post("/api/game-observatory/play/attention/clear")
async def play_attention_clear(request: Request) -> Any:
    """A person has looked at the stuck screen and dealt with it."""

    raw = await request.body()
    payload: Any = {}
    if raw:
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="body must be a JSON object")
    actor = payload.get("actor") or "person"
    note = payload.get("note") or ""
    if not isinstance(actor, str) or not isinstance(note, str):
        raise HTTPException(status_code=422, detail="actor and note must be strings")
    if len(actor) > 128 or len(note) > 1024:
        raise HTTPException(status_code=422, detail="actor or note too long")
    try:
        return await _call("clear_attention", actor, note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/game-observatory/play/control")
async def play_control(request: Request) -> Any:
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("mode"), str):
        raise HTTPException(status_code=422, detail="mode is required")
    await _call("set_mode", payload["mode"], str(payload.get("client_id") or "http"))
    return await _call("status")


@router.post("/api/game-observatory/play/auto-rpc")
async def play_auto_rpc(request: Request) -> dict[str, Any]:
    payload = await request.json()
    if not isinstance(payload, dict) or not isinstance(payload.get("method"), str):
        raise HTTPException(status_code=422, detail="method is required")
    kwargs = payload.get("kwargs") or {}
    if not isinstance(kwargs, dict):
        raise HTTPException(status_code=422, detail="kwargs must be an object")
    try:
        async with _INPUT_LOCK:
            result = await _call("auto_rpc", payload["method"], kwargs)
            evidence = None
            if payload["method"] == "act" and isinstance(result, dict):
                action_id = result.get("action_id")
                if action_id is not None:
                    evidence = await _call("automatic_evidence", action_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        reason = str(exc)
        if reason == "control_handed_over" or reason.startswith("recording_unavailable"):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    response = {"result": result}
    if payload["method"] == "act":
        response["evidence"] = evidence
    return response


def _safe_file(session: Any, relative: str) -> Path:
    root = Path(session.root).resolve()
    base = Path(getattr(session, "base", root)).resolve()
    normalized = str(relative).replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    if not parts or normalized.startswith("/") or ":" in parts[0]:
        raise HTTPException(status_code=404, detail="file not found")
    if parts[0].startswith("session-"):
        # Cross-session replay assets are addressed from the shared base, but
        # only through an explicitly named session directory.
        candidate = base.joinpath(*parts).resolve()
        allowed_root = (base / parts[0]).resolve()
        if not allowed_root.is_dir():
            raise HTTPException(status_code=404, detail="file not found")
    else:
        candidate = root.joinpath(*parts).resolve()
        allowed_root = root
    if candidate == allowed_root or allowed_root not in candidate.parents:
        raise HTTPException(status_code=404, detail="file not found")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return candidate


async def _file_chunks(path: Path, start: int, length: int):
    remaining = length
    handle = await asyncio.to_thread(path.open, "rb")
    try:
        await asyncio.to_thread(handle.seek, start)
        while remaining:
            chunk = await asyncio.to_thread(handle.read, min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)


@router.get("/api/game-observatory/play/files/{path:path}")
async def play_file(path: str, request: Request) -> Response:
    candidate = _safe_file(_session(), path)
    size = candidate.stat().st_size
    start, end, status = 0, max(size - 1, 0), 200
    range_header = request.headers.get("range")
    if range_header:
        if not range_header.startswith("bytes=") or "," in range_header:
            raise HTTPException(status_code=416, detail="unsupported range")
        raw = range_header[6:].strip()
        left, _, right = raw.partition("-")
        try:
            if left:
                start = int(left)
                end = int(right) if right else end
            else:
                suffix = int(right)
                start = max(size - suffix, 0)
        except ValueError as exc:
            raise HTTPException(status_code=416, detail="invalid range") from exc
        if start < 0 or start >= size or end < start:
            raise HTTPException(status_code=416, detail="range unsatisfiable")
        end = min(end, size - 1)
        status = 206
    length = max(end - start + 1, 0)
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(_file_chunks(candidate, start, length), status_code=status,
                             media_type=mimetypes.guess_type(candidate.name)[0] or "application/octet-stream",
                             headers=headers)


def _wire_packet(item: Any, fallback_seq: int) -> bytes:
    # CaptureFeed publishes the complete framed message.  Keep it intact so
    # the encoder's access unit and PTS are never repackaged or split here.
    if isinstance(item, (bytes, bytearray, memoryview)):
        return bytes(item)
    if isinstance(item, dict):
        payload = item.get("packet", item.get("data", item.get("payload")))
        metadata = dict(item.get("metadata") or item)
        for key in ("packet", "data", "payload", "metadata"):
            metadata.pop(key, None)
    elif isinstance(item, tuple) and len(item) == 2:
        metadata, payload = item
        metadata = dict(metadata or {})
    else:
        metadata, payload = {}, item
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("capture subscriber returned a non-byte packet")
    metadata.setdefault("seq", fallback_seq)
    metadata.setdefault("pts_us", 0)
    metadata.setdefault("keyframe", False)
    metadata.setdefault("width", 0)
    metadata.setdefault("height", 0)
    metadata.setdefault("codec", "avc1.42c02a")
    metadata.setdefault("captured_mono_ns", 0)
    import json
    encoded = json.dumps(metadata, separators=(",", ":")).encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded + bytes(payload)


@router.websocket("/api/game-observatory/play/video")
async def play_video(ws: WebSocket) -> None:
    if await _reject_cross_origin(ws):
        return
    session = _session()
    subscriber = await asyncio.to_thread(session.capture.subscribe)
    fallback_seq = 0
    try:
        await ws.accept()
        await ws.send_json(await _call("status"))
        while True:
            try:
                item = await asyncio.to_thread(subscriber.get, True, 0.5)
            except queue.Empty:
                def _capture_running() -> bool:
                    state = session.status()
                    return bool(state.get("started") and state.get("capture", {}).get("running"))
                if not await asyncio.to_thread(_capture_running):
                    break
                continue
            fallback_seq += 1
            await ws.send_bytes(_wire_packet(item, fallback_seq))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await asyncio.to_thread(session.capture.unsubscribe, subscriber)


_INPUT_LOCK = asyncio.Lock()
_INPUT_QUEUE_MAX = 64
_INPUT_BATCH_MAX = 8
_INPUT_MOVE_GATE_BUDGET_NS = 4_000_000


@dataclass(frozen=True)
class _InputEnvelope:
    payload: dict[str, Any]
    client_id: str
    received_mono_ns: int
    received_wall_ns: int
    enqueued_mono_ns: int
    cancel_event: threading.Event


def _input_error(envelope: _InputEnvelope, error: Exception, *, queue_wait_ms: float) -> dict[str, Any]:
    return {
        "ok": False,
        "event_id": envelope.payload.get("event_id"),
        "error": str(error),
        "received_mono_ns": envelope.received_mono_ns,
        "received_wall_ns": envelope.received_wall_ns,
        "queue_wait_ms": max(0.0, queue_wait_ms),
    }


def _cancel_input(
    session: Any,
    envelope: _InputEnvelope,
    *,
    queue_wait_ms: float,
    reason: str,
) -> dict[str, Any]:
    recorder = getattr(session, "cancel_input_event", None)
    if recorder is not None:
        return recorder(
            envelope.payload,
            envelope.client_id,
            received_mono_ns=envelope.received_mono_ns,
            received_wall_ns=envelope.received_wall_ns,
            queue_wait_ms=queue_wait_ms,
            reason=reason,
        )
    return {
        "ok": False,
        "event_id": envelope.payload.get("event_id"),
        "status": "cancelled",
        "error": "input_cancelled: " + reason,
        "received_mono_ns": envelope.received_mono_ns,
        "received_wall_ns": envelope.received_wall_ns,
        "queue_wait_ms": max(0.0, queue_wait_ms),
    }


def _failed_input(
    session: Any,
    envelope: _InputEnvelope,
    *,
    queue_wait_ms: float,
    reason: str,
) -> dict[str, Any]:
    recorder = getattr(session, "_record_input_terminal", None)
    if recorder is not None:
        return recorder(
            envelope.payload,
            envelope.client_id,
            received_mono_ns=envelope.received_mono_ns,
            received_wall_ns=envelope.received_wall_ns,
            queue_wait_ms=queue_wait_ms,
            status="failed",
            reason=reason,
        )
    return _input_error(envelope, RuntimeError(reason), queue_wait_ms=queue_wait_ms)


def _execute_input_batch(
    session: Any,
    batch: list[_InputEnvelope],
    *,
    cancel_reason: str = "websocket_disconnected",
) -> list[dict[str, Any]]:
    """Execute one already ordered batch on the single input worker.

    The worker never calls the device concurrently.  Consecutive moves for
    the same pointer may share the capture control gate for a bounded 4 ms;
    down/up/cancel always use the session's normal per-event boundary path.
    """
    results: list[dict[str, Any]] = []

    def execute(envelope: _InputEnvelope, *, gate_held: bool, started_ns: int) -> dict[str, Any]:
        queue_wait_ms = (started_ns - envelope.enqueued_mono_ns) / 1e6
        if envelope.cancel_event.is_set():
            return _cancel_input(session, envelope, queue_wait_ms=queue_wait_ms, reason=cancel_reason)
        try:
            ack = session.input_event(
                envelope.payload,
                envelope.client_id,
                received_mono_ns=envelope.received_mono_ns,
                received_wall_ns=envelope.received_wall_ns,
                queue_wait_ms=queue_wait_ms,
                _control_gate_held=gate_held,
            )
            return ack if isinstance(ack, dict) else {"ok": True, "result": ack}
        except Exception as exc:  # preserve one ACK for every received event
            return _input_error(envelope, exc, queue_wait_ms=queue_wait_ms)

    def run() -> None:
        index = 0
        while index < len(batch):
            envelope = batch[index]
            payload = envelope.payload
            if payload.get("kind") != "move":
                started_ns = time.perf_counter_ns()
                results.append(execute(envelope, gate_held=False, started_ns=started_ns))
                index += 1
                continue

            pointer = (envelope.client_id, payload.get("pointer_id", 1))
            end = index + 1
            while end < len(batch) and end - index < _INPUT_BATCH_MAX:
                candidate = batch[end]
                if candidate.payload.get("kind") != "move" or (
                    candidate.client_id,
                    candidate.payload.get("pointer_id", 1),
                ) != pointer:
                    break
                end += 1

            cursor = index
            while cursor < end:
                try:
                    with session.capture.control_lock():
                        # Start the 4 ms budget only after this gate is
                        # acquired.  A slow waiter must still make progress
                        # on its first move instead of repeatedly reopening
                        # the gate with zero progress.
                        budget_started = time.perf_counter_ns()
                        first_cursor = cursor
                        while cursor < end:
                            now = time.perf_counter_ns()
                            if cursor > first_cursor and now - budget_started >= _INPUT_MOVE_GATE_BUDGET_NS:
                                break
                            results.append(execute(batch[cursor], gate_held=True, started_ns=now))
                            cursor += 1
                except Exception as exc:
                    # A stopped capture gate must still produce explicit ACKs for
                    # every queued event; the session remains responsible for any
                    # pointer release it needs after an injection failure.
                    while cursor < end:
                        started_ns = time.perf_counter_ns()
                        wait_ms = (started_ns - batch[cursor].enqueued_mono_ns) / 1e6
                        if batch[cursor].cancel_event.is_set():
                            results.append(_cancel_input(
                                session, batch[cursor], queue_wait_ms=wait_ms, reason=cancel_reason,
                            ))
                        else:
                            results.append(_failed_input(
                                session, batch[cursor], queue_wait_ms=wait_ms,
                                reason=f"{type(exc).__name__}: {exc}",
                            ))
                        cursor += 1
            index = end

    # Keep the session state lock across a batch.  It is an RLock, so the
    # per-event input_event/release_client calls remain re-entrant, and the
    # lock order stays session -> capture, matching stop/set_mode.
    session_lock = getattr(session, "_input_lock", None)
    with (session_lock if session_lock is not None else nullcontext()):
        run()
    return results


@router.websocket("/api/game-observatory/play/input")
async def play_input(ws: WebSocket) -> None:
    if await _reject_cross_origin(ws):
        return
    await ws.accept()
    client_id = ""
    session = _session()
    input_queue: asyncio.Queue[_InputEnvelope] = asyncio.Queue(maxsize=_INPUT_QUEUE_MAX)
    receive_finished = asyncio.Event()
    cancel_event = threading.Event()
    cancel_reason = "websocket_disconnected"
    client_ids: list[str] = []
    connected = True

    async def receive_pump() -> None:
        nonlocal client_id, connected, cancel_reason
        try:
            while True:
                payload = await ws.receive_json()
                received_mono_ns = time.perf_counter_ns()
                received_wall_ns = time.time_ns()
                if not isinstance(payload, dict):
                    await ws.send_json({"ok": False, "error": "input must be an object"})
                    continue
                client_id = str(payload.get("client_id") or client_id or "browser")
                if client_id not in client_ids:
                    client_ids.append(client_id)
                envelope = _InputEnvelope(
                    payload=payload,
                    client_id=client_id,
                    received_mono_ns=received_mono_ns,
                    received_wall_ns=received_wall_ns,
                    enqueued_mono_ns=time.perf_counter_ns(),
                    cancel_event=cancel_event,
                )
                # Queue.put is deliberate bounded backpressure: received
                # events are never dropped or coalesced when the worker lags.
                try:
                    await input_queue.put(envelope)
                except asyncio.CancelledError:
                    cancel_reason = "receive_pump_cancelled"
                    cancel_event.set()
                    await asyncio.to_thread(
                        _cancel_input, session, envelope,
                        queue_wait_ms=(time.perf_counter_ns() - envelope.enqueued_mono_ns) / 1e6,
                        reason=cancel_reason,
                    )
                    raise
        except (WebSocketDisconnect, RuntimeError, ValueError):
            cancel_reason = "websocket_disconnected"
            connected = False
            cancel_event.set()
        finally:
            receive_finished.set()

    async def execute_pump() -> None:
        nonlocal connected, cancel_reason
        while not receive_finished.is_set() or not input_queue.empty():
            try:
                first = await asyncio.wait_for(input_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            batch = [first]
            while len(batch) < _INPUT_BATCH_MAX:
                try:
                    batch.append(input_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            async with _INPUT_LOCK:
                results = await asyncio.to_thread(
                    _execute_input_batch, session, batch, cancel_reason=cancel_reason,
                )
            for envelope, ack in zip(batch, results):
                input_queue.task_done()
                if not connected:
                    continue
                try:
                    await ws.send_json(ack)
                except (WebSocketDisconnect, RuntimeError):
                    connected = False
                    cancel_reason = "ack_send_failed"
                    cancel_event.set()

    receive_task = asyncio.create_task(receive_pump())
    execute_task = asyncio.create_task(execute_pump())
    try:
        await asyncio.gather(receive_task, execute_task)
    finally:
        cancel_event.set()
        for task in (receive_task, execute_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(receive_task, execute_task, return_exceptions=True)
        pending: list[_InputEnvelope] = []
        while True:
            try:
                pending.append(input_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if pending:
            await asyncio.to_thread(
                _execute_input_batch, session, pending, cancel_reason=cancel_reason,
            )
            for _ in pending:
                input_queue.task_done()
        if hasattr(session, "release_client"):
            for released_client in reversed(client_ids):
                await asyncio.to_thread(session.release_client, released_client)


__all__ = ["router"]
