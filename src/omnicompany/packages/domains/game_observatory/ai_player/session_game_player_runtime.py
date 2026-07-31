"""Process-local runtime for repeated, already-learned game routes.

The detached explore worker owns this runtime.  Provider processes inherit a
small descriptor and forward eligible ``navigate`` requests over loopback.  The
worker then executes the existing guarded CLI callback with process-local
objects, so evidence, budget, policy, lease, and deferred-sedimentation
contracts remain authoritative while expensive Python/runtime setup is reused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from omnicompany.game_player_runtime_client import (
    EXTERNAL_AGENT_INVOCATION_ID_ENV,
    EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV,
    EXTERNAL_AGENT_SESSION_ID_ENV,
    SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV,
    SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_SCHEMA,
    SESSION_GAME_PLAYER_RUNTIME_REQUEST_ID_ENV,
    SESSION_GAME_PLAYER_RUNTIME_REQUEST_SCHEMA,
    SESSION_GAME_PLAYER_RUNTIME_RESPONSE_SCHEMA,
    SessionGamePlayerRuntimeError,
    SessionRuntimeForwardResult,
    forward_navigate_to_session_runtime,
)

def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SessionGamePlayerRuntimeError(
            f"published session runtime descriptor is unreadable: {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SessionGamePlayerRuntimeError(
            f"published session runtime descriptor is not an object: {path}"
        )
    return value


def _request_digest(request: dict[str, Any]) -> str:
    material = {
        key: value
        for key, value in request.items()
        if key not in {"deadline_at"}
    }
    raw = json.dumps(
        material,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _require_persisted_signed_skill_run(
    observatory_root: Path,
    environment_id: str,
    skill_run_id: str,
) -> None:
    """Prove that an in-memory work item already has a durable signed source."""

    from .skill_attestation import skill_runtime_signer_and_trust_store
    from .store import AIPlayerStore
    from ..store import ObservatoryStore

    observatory = ObservatoryStore(observatory_root)
    _signer, trust_store = skill_runtime_signer_and_trust_store(observatory.root)
    player = AIPlayerStore(
        observatory,
        skill_validator_trust_store=trust_store,
    )
    skill_run = player.get_skill_run(environment_id, skill_run_id)
    if skill_run is None:
        raise SessionGamePlayerRuntimeError(
            f"background sedimentation requires a persisted SkillRun: {skill_run_id}"
        )
    player.verify_skill_run_provenance(skill_run)


def _settle_one_persisted_skill_run(
    observatory_root: Path,
    environment_id: str,
    skill_run_id: str,
) -> None:
    """Run the existing crash-safe drain for one canonical queue item."""

    from .deferred_sedimentation import (
        deferred_skill_run_is_settled,
        settle_deferred_skill_runs,
    )
    from .skill_attestation import skill_runtime_signer_and_trust_store
    from .store import AIPlayerStore
    from ..store import ObservatoryStore

    settle_deferred_skill_runs(
        observatory_root,
        environment_id=environment_id,
        skill_run_ids=[skill_run_id],
    )
    observatory = ObservatoryStore(observatory_root)
    _signer, trust_store = skill_runtime_signer_and_trust_store(observatory.root)
    player = AIPlayerStore(
        observatory,
        skill_validator_trust_store=trust_store,
    )
    skill_run = player.get_skill_run(environment_id, skill_run_id)
    if skill_run is None or not deferred_skill_run_is_settled(player, skill_run):
        raise SessionGamePlayerRuntimeError(
            f"background sedimentation did not settle persisted SkillRun: {skill_run_id}"
        )


class _SessionDeferredSedimentationQueue:
    """One process-local serial consumer over durable SkillRun IDs.

    The queue is only an acceleration boundary.  Evidence and the signed
    SkillRun are canonical before ``enqueue`` accepts an ID, while the existing
    turn-boundary drain remains the recovery path after a process loss.
    """

    def __init__(
        self,
        *,
        observatory_root: Path,
        environment_id: str,
        sedimenter: Callable[[Path, str, str], None] | None = None,
        persisted_guard: Callable[[Path, str, str], None] | None = None,
    ) -> None:
        self.observatory_root = observatory_root
        self.environment_id = environment_id
        self._sedimenter = sedimenter or _settle_one_persisted_skill_run
        self._persisted_guard = persisted_guard or _require_persisted_signed_skill_run
        self._condition = threading.Condition()
        self._pending: deque[str] = deque()
        self._known_ids: set[str] = set()
        self._in_flight: str | None = None
        self._failure: tuple[str, BaseException] | None = None
        self._completed_durations: dict[str, float] = {}
        self._closing = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None:
                return
            if self._closing:
                raise SessionGamePlayerRuntimeError(
                    "background sedimentation queue is already closing"
                )
            self._thread = threading.Thread(
                target=self._run,
                name="session-game-player-sedimentation",
                daemon=True,
            )
            self._thread.start()

    def enqueue(self, skill_run_ids: list[str]) -> dict[str, Any]:
        ordered = list(dict.fromkeys(skill_run_ids))
        if not ordered or any(not item.strip() for item in ordered):
            raise SessionGamePlayerRuntimeError(
                "background sedimentation requires persisted SkillRun IDs"
            )
        for skill_run_id in ordered:
            self._persisted_guard(
                self.observatory_root,
                self.environment_id,
                skill_run_id,
            )
        with self._condition:
            self._raise_failure_locked()
            if self._closing:
                raise SessionGamePlayerRuntimeError(
                    "background sedimentation queue is closing"
                )
            accepted: list[str] = []
            for skill_run_id in ordered:
                if skill_run_id in self._known_ids:
                    continue
                self._known_ids.add(skill_run_id)
                self._pending.append(skill_run_id)
                accepted.append(skill_run_id)
            self._condition.notify_all()
            return {
                "schema": (
                    "game-observatory.ai-player.session-background-sedimentation.v1"
                ),
                "mode": "session_background",
                "skill_run_ids": ordered,
                "newly_queued_skill_run_ids": accepted,
            }

    def wait_before_request(self, *, deadline_at: float) -> None:
        with self._condition:
            while self._pending or self._in_flight is not None:
                self._raise_failure_locked()
                remaining = deadline_at - time.time()
                if remaining <= 0:
                    raise SessionGamePlayerRuntimeError(
                        "previous background sedimentation did not finish before "
                        "the request deadline"
                    )
                self._condition.wait(timeout=remaining)
            self._raise_failure_locked()

    def close(self, *, timeout_seconds: float) -> None:
        with self._condition:
            self._closing = True
            self._condition.notify_all()
            thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, timeout_seconds))
            if thread.is_alive():
                raise SessionGamePlayerRuntimeError(
                    "background sedimentation did not stop within the close timeout"
                )
        with self._condition:
            self._thread = None
            self._raise_failure_locked()

    def completed_duration(self, skill_run_id: str) -> float | None:
        with self._condition:
            return self._completed_durations.get(skill_run_id)

    def _raise_failure_locked(self) -> None:
        if self._failure is None:
            return
        skill_run_id, error = self._failure
        raise SessionGamePlayerRuntimeError(
            "background sedimentation failed for persisted SkillRun "
            f"{skill_run_id}: {type(error).__name__}: {error}"
        ) from error

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._closing:
                    self._condition.wait()
                if not self._pending:
                    return
                skill_run_id = self._pending.popleft()
                self._in_flight = skill_run_id
            started = time.perf_counter()
            try:
                self._sedimenter(
                    self.observatory_root,
                    self.environment_id,
                    skill_run_id,
                )
            except BaseException as exc:  # noqa: BLE001 - thread boundary is fail-closed
                with self._condition:
                    self._failure = (skill_run_id, exc)
                    self._in_flight = None
                    self._condition.notify_all()
                return
            with self._condition:
                self._completed_durations[skill_run_id] = time.perf_counter() - started
                self._in_flight = None
                self._condition.notify_all()


class _RuntimeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        server_address: tuple[str, int],
        runtime: "SessionGamePlayerRuntime",
    ) -> None:
        self.runtime = runtime
        super().__init__(server_address, _RuntimeRequestHandler)


class _RuntimeRequestHandler(BaseHTTPRequestHandler):
    server: _RuntimeHTTPServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path != "/v1/navigate":
            self._reply(404, {"error": "unknown runtime endpoint"})
            return
        authorization = self.headers.get("Authorization", "")
        expected = f"Bearer {self.server.runtime.bearer_token}"
        if not hmac.compare_digest(authorization, expected):
            self._reply(401, {"error": "runtime bearer authentication failed"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 64 * 1024:
            self._reply(400, {"error": "runtime request body size is invalid"})
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            self._reply(400, {"error": f"runtime request is invalid JSON: {exc}"})
            return
        if not isinstance(body, dict):
            self._reply(400, {"error": "runtime request is not an object"})
            return
        status, response = self.server.runtime.handle_request(body)
        self._reply(status, response)

    def _reply(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class SessionGamePlayerRuntime:
    """One frozen-worker sidecar serving repeated known-route navigation."""

    def __init__(
        self,
        *,
        descriptor_path: Path,
        observatory_root: Path,
        environment_id: str,
        session_id: str,
        run_id: str,
        process_nonce: str,
        runtime_snapshot_sha256: str,
        runtime_fence_signal_path: Path,
        executor: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
        state_guard: Callable[[dict[str, Any]], None] | None = None,
        sedimenter: Callable[[Path, str, str], None] | None = None,
        persisted_skill_run_guard: Callable[[Path, str, str], None] | None = None,
        sedimentation_close_timeout_seconds: float = 10.0,
    ) -> None:
        self.descriptor_path = descriptor_path.resolve()
        self.observatory_root = observatory_root.resolve()
        self.environment_id = environment_id
        self.session_id = session_id
        self.run_id = run_id
        self.process_nonce = process_nonce
        self.runtime_snapshot_sha256 = runtime_snapshot_sha256
        self.runtime_fence_signal_path = runtime_fence_signal_path.resolve()
        self.bearer_token = secrets.token_urlsafe(32)
        self._executor = executor
        self._state_guard = state_guard
        self._server: _RuntimeHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._runtime_objects: dict[str, Any] = {}
        self._request_lock = threading.Lock()
        self._responses: dict[str, tuple[str, dict[str, Any]]] = {}
        self._sedimentation_close_timeout_seconds = max(
            0.0,
            sedimentation_close_timeout_seconds,
        )
        self._sedimentation_queue = _SessionDeferredSedimentationQueue(
            observatory_root=self.observatory_root,
            environment_id=self.environment_id,
            sedimenter=sedimenter,
            persisted_guard=persisted_skill_run_guard,
        )

    @property
    def descriptor(self) -> dict[str, Any]:
        server = self._server
        if server is None:
            raise SessionGamePlayerRuntimeError("session runtime is not running")
        return {
            "schema": SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_SCHEMA,
            "endpoint": f"http://127.0.0.1:{server.server_address[1]}/v1/navigate",
            "bearer_token": self.bearer_token,
            "observatory_root": str(self.observatory_root),
            "environment_id": self.environment_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "process_nonce": self.process_nonce,
            "runtime_snapshot_sha256": self.runtime_snapshot_sha256,
            "worker_pid": os.getpid(),
            "published_at": time.time(),
        }

    def start(self) -> "SessionGamePlayerRuntime":
        if self._server is not None:
            return self
        server = _RuntimeHTTPServer(("127.0.0.1", 0), self)
        self._server = server
        self._sedimentation_queue.start()
        self._runtime_objects["deferred_sedimentation_enqueue"] = (
            self._sedimentation_queue.enqueue
        )
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name=f"session-game-player-runtime-{self.run_id}",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        _atomic_write_json(self.descriptor_path, self.descriptor)
        return self

    def close(self) -> None:
        close_error: BaseException | None = None
        server = self._server
        self._server = None
        if server is not None:
            server.shutdown()
            server.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)
        try:
            self._sedimentation_queue.close(
                timeout_seconds=self._sedimentation_close_timeout_seconds,
            )
        except BaseException as exc:  # noqa: BLE001 - cleanup must still finish
            close_error = exc
        context = self._runtime_objects.get("cli_context")
        context_runtime = getattr(context, "runtime", {})
        route_runtime = self._runtime_objects.get("session_live_route_runtime")
        if isinstance(context_runtime, dict):
            route_runtime = context_runtime.get(
                "session_live_route_runtime",
                route_runtime,
            )
        close = getattr(route_runtime, "close", None)
        if callable(close):
            close()
        self._runtime_objects.clear()
        try:
            self.descriptor_path.unlink(missing_ok=True)
        except OSError:
            pass
        if close_error is not None:
            raise close_error

    def __enter__(self) -> "SessionGamePlayerRuntime":
        return self.start()

    def __exit__(self, *_args: object) -> None:
        self.close()

    def handle_request(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            self._validate_request_identity(request)
        except SessionGamePlayerRuntimeError as exc:
            return 409, self._error_response(request, str(exc))
        request_id = str(request["request_id"])
        digest = _request_digest(request)
        with self._request_lock:
            try:
                self._sedimentation_queue.wait_before_request(
                    deadline_at=float(request["deadline_at"]),
                )
            except BaseException as exc:  # noqa: BLE001 - IPC owns the boundary
                return 200, self._error_response(
                    request,
                    f"{type(exc).__name__}: {exc}",
                )
            existing = self._responses.get(request_id)
            if existing is not None:
                existing_digest, response = existing
                if existing_digest != digest:
                    return 409, self._error_response(
                        request,
                        "runtime request_id was reused with different arguments",
                    )
                replayed = {**response, "replayed": True}
                return 200, replayed
            try:
                self._guard_writable_session(request)
            except BaseException as exc:  # noqa: BLE001 - IPC owns the thread boundary
                response = self._error_response(
                    request,
                    f"{type(exc).__name__}: {exc}",
                )
            else:
                try:
                    response = self._execute(request)
                except BaseException as exc:  # noqa: BLE001 - action may be accepted
                    response = self._error_response(
                        request,
                        f"{type(exc).__name__}: {exc}",
                        accepted=True,
                    )
            try:
                self._yield_device_lease()
            except Exception as exc:  # noqa: BLE001 - ownership uncertainty is terminal
                response = self._error_response(
                    request,
                    "session runtime could not yield its device lease: "
                    f"{type(exc).__name__}: {exc}",
                    accepted=bool(response.get("accepted")),
                )
            self._responses[request_id] = (digest, response)
            return 200, response

    def _validate_request_identity(self, request: dict[str, Any]) -> None:
        expected = {
            "schema": SESSION_GAME_PLAYER_RUNTIME_REQUEST_SCHEMA,
            "operation": "navigate",
            "environment_id": self.environment_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "process_nonce": self.process_nonce,
            "runtime_snapshot_sha256": self.runtime_snapshot_sha256,
        }
        for key, value in expected.items():
            if request.get(key) != value:
                raise SessionGamePlayerRuntimeError(
                    f"session runtime identity mismatch for {key}"
                )
        for key in ("request_id", "invocation_id", "invocation_sequence"):
            if not isinstance(request.get(key), str) or not str(request[key]).strip():
                raise SessionGamePlayerRuntimeError(
                    f"session runtime request is missing {key}"
                )
        arguments = request.get("arguments")
        if not isinstance(arguments, dict):
            raise SessionGamePlayerRuntimeError("session runtime arguments are missing")
        if not isinstance(arguments.get("goal"), str) or not arguments["goal"].strip():
            raise SessionGamePlayerRuntimeError("session runtime goal is missing")
        if not isinstance(arguments.get("source_step_id"), str) or not arguments[
            "source_step_id"
        ].strip():
            raise SessionGamePlayerRuntimeError("session runtime source step is missing")
        deadline_at = request.get("deadline_at")
        if not isinstance(deadline_at, (int, float)) or deadline_at <= time.time():
            raise SessionGamePlayerRuntimeError("session runtime request deadline expired")

    def _guard_writable_session(self, request: dict[str, Any]) -> None:
        if self.runtime_fence_signal_path.exists():
            raise SessionGamePlayerRuntimeError(
                "session runtime is fenced by a facility contract change"
            )
        if self._state_guard is not None:
            self._state_guard(request)
            return
        context = self._context()
        from .session_control import AIPlayerSessionControl

        control = AIPlayerSessionControl(context.player())
        session = control.get_session(self.environment_id, self.session_id)
        if session is None or session.state != "running":
            raise SessionGamePlayerRuntimeError(
                "session runtime rejected a paused or non-running session"
            )
        control.assert_session_lease_active(self.environment_id, self.session_id)

    def _yield_device_lease(self) -> None:
        route_runtime = self._runtime_objects.get("session_live_route_runtime")
        context = self._runtime_objects.get("cli_context")
        context_runtime = getattr(context, "runtime", {})
        if isinstance(context_runtime, dict):
            route_runtime = context_runtime.get(
                "session_live_route_runtime",
                route_runtime,
            )
        yield_lease = getattr(route_runtime, "yield_lease", None)
        if callable(yield_lease):
            yield_lease()

    def _context(self) -> Any:
        context = self._runtime_objects.get("cli_context")
        if context is not None:
            return context
        from omnicompany.cli.commands.game import _PlayerCLIContext

        context = _PlayerCLIContext(
            root=self.observatory_root,
            as_json=True,
            agent_brief=True,
            runtime={
                "session_runtime_host": True,
                "session_runtime_learned_routes_only": True,
                "deferred_sedimentation_enqueue": self._sedimentation_queue.enqueue,
                "suppress_output": True,
            },
        )
        self._runtime_objects["cli_context"] = context
        return context

    def _execute(self, request: dict[str, Any]) -> dict[str, Any]:
        if self._executor is not None:
            payload = self._executor(request, self._runtime_objects)
            return {
                "schema": SESSION_GAME_PLAYER_RUNTIME_RESPONSE_SCHEMA,
                "request_id": request["request_id"],
                "accepted": True,
                "replayed": False,
                "exit_code": int(payload.get("exit_code", 0)),
                "error": payload.get("error"),
                "emissions": list(payload.get("emissions", [])),
            }

        from click import ClickException
        from click.exceptions import Exit
        from omnicompany.cli.commands.game import player_navigate_known_route
        from omnicompany.packages.domains.game_observatory.ai_player.known_route_program import (
            KnownRouteProgram,
        )
        from omnicompany.packages.domains.game_observatory.ai_player.live_step import (
            LiveStepRouteRuntime,
        )

        context = self._context()
        if not isinstance(context.runtime.get("known_route_program"), KnownRouteProgram):
            context.runtime["known_route_program"] = KnownRouteProgram(context.player())
        if not isinstance(
            context.runtime.get("session_live_route_runtime"),
            LiveStepRouteRuntime,
        ):
            context.runtime["session_live_route_runtime"] = LiveStepRouteRuntime.create(
                facility=context.facility(),
                player=context.player(),
            )
        sink: list[dict[str, Any]] = []
        context.runtime["emission_sink"] = sink
        arguments = request["arguments"]
        callback = getattr(player_navigate_known_route.callback, "__wrapped__", None)
        if not callable(callback):
            raise SessionGamePlayerRuntimeError("known-route callback is unavailable")
        previous_environment = {
            EXTERNAL_AGENT_INVOCATION_ID_ENV: os.environ.get(
                EXTERNAL_AGENT_INVOCATION_ID_ENV
            ),
            EXTERNAL_AGENT_SESSION_ID_ENV: os.environ.get(EXTERNAL_AGENT_SESSION_ID_ENV),
            EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV: os.environ.get(
                EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV
            ),
        }
        os.environ[EXTERNAL_AGENT_INVOCATION_ID_ENV] = str(request["invocation_id"])
        os.environ[EXTERNAL_AGENT_SESSION_ID_ENV] = self.session_id
        os.environ[EXTERNAL_AGENT_INVOCATION_SEQUENCE_ENV] = str(
            request["invocation_sequence"]
        )
        exit_code = 0
        error: str | None = None
        try:
            callback(
                context,
                goal=str(arguments["goal"]),
                environment_id=self.environment_id,
                session_id=self.session_id,
                source_step_id=str(arguments["source_step_id"]),
                max_skills=int(arguments.get("max_skills", 12)),
                max_safety=str(arguments.get("max_safety", "economic")),
            )
        except Exit as exc:
            exit_code = int(exc.exit_code or 1)
        except ClickException as exc:
            exit_code = int(exc.exit_code or 1)
            error = exc.format_message()
        finally:
            for name, value in previous_environment.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        return {
            "schema": SESSION_GAME_PLAYER_RUNTIME_RESPONSE_SCHEMA,
            "request_id": request["request_id"],
            "accepted": True,
            "replayed": False,
            "exit_code": exit_code,
            "error": error,
            "emissions": sink,
        }

    @staticmethod
    def _error_response(
        request: dict[str, Any],
        error: str,
        *,
        accepted: bool = False,
    ) -> dict[str, Any]:
        return {
            "schema": SESSION_GAME_PLAYER_RUNTIME_RESPONSE_SCHEMA,
            "request_id": request.get("request_id"),
            "accepted": accepted,
            "replayed": False,
            "exit_code": 1,
            "error": error,
            "emissions": [],
        }


__all__ = [
    "SESSION_GAME_PLAYER_RUNTIME_DESCRIPTOR_ENV",
    "SESSION_GAME_PLAYER_RUNTIME_REQUEST_ID_ENV",
    "SessionGamePlayerRuntime",
    "SessionGamePlayerRuntimeError",
    "SessionRuntimeForwardResult",
    "forward_navigate_to_session_runtime",
]
