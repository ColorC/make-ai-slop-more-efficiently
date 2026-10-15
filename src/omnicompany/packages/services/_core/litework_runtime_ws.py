# [OMNI] origin=claude-code domain=services/_core ts=2026-09-03 type=service
# [OMNI] material_id="material:services._core.litework_runtime_ws.py"
"""Client for LiTeWork's own remote-runtime WebSocket protocol.

Why this exists next to the CLI bridge: LiTeWork's local transport (a named pipe)
answers exactly one frame per connection, so every terminal read costs a fresh
`node` process — roughly 1.3s.  Live terminal streaming is dispatched only on
LiTeWork's WebSocket path, behind the NaCl-box device handshake its own mobile
and web clients use.

This module implements that handshake and nothing else.  It consumes LiTeWork's
existing surface; it adds no OmniCompany-specific interface to LiTeWork, and it
reads the runtime's published metadata rather than owning any of its state.

Wire protocol (mirrors src/shared/remote-runtime-client.ts):
  1. connect to the runtime's websocket transport
  2. -> {"type":"e2ee_hello","publicKeyB64":<our X25519 public key>}
  3. <- {"type":"e2ee_ready"}                             (plaintext)
  4. -> seal({"type":"e2ee_auth","deviceToken":...})
  5. <- {"type":"e2ee_authenticated"}                     (sealed)
  6. -> seal({"id":...,"deviceToken":...,"method":...,"params":...})
     <- sealed response frames, one per emit for streaming methods
Every sealed frame is base64(nonce || crypto_box_afternm(payload)).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator


NONCE_BYTES = 24
MAX_FRAME_BYTES = 16 * 1024 * 1024
DEFAULT_REQUEST_TIMEOUT = 15.0


class RuntimeUnavailable(RuntimeError):
    """The LiTeWork runtime could not be discovered, reached or authenticated."""


@dataclass(frozen=True)
class RuntimeEndpoint:
    url: str
    server_public_key: bytes
    device_token: str
    runtime_id: str


def user_data_dir(profile_dir: str | os.PathLike[str] | None = None) -> Path:
    if profile_dir:
        return Path(profile_dir)
    override = os.environ.get("ORCA_DEV_USER_DATA_PATH", "").strip()
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "liwork-dev"
    return Path.home() / ".liwork-dev"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeUnavailable(f"cannot read LiTeWork runtime file {path.name}: {exc}") from exc


def discover(profile_dir: str | os.PathLike[str] | None = None) -> RuntimeEndpoint:
    """Locate the running runtime's websocket endpoint and a usable credential."""
    root = user_data_dir(profile_dir)
    metadata = _read_json(root / "orca-runtime.json")
    keypair = _read_json(root / "orca-e2ee-keypair.json")
    devices = _read_json(root / "orca-devices.json")
    if not isinstance(metadata, dict) or not isinstance(keypair, dict):
        raise RuntimeUnavailable("LiTeWork runtime metadata is not an object")

    transports = metadata.get("transports")
    endpoint = next(
        (
            str(item.get("endpoint") or "")
            for item in (transports if isinstance(transports, list) else [])
            if isinstance(item, dict) and item.get("kind") == "websocket"
        ),
        "",
    )
    if not endpoint:
        raise RuntimeUnavailable("the LiTeWork runtime publishes no websocket transport")
    # The runtime advertises its bind address; 0.0.0.0 is not a destination.
    url = endpoint.replace("//0.0.0.0:", "//127.0.0.1:").replace("//[::]:", "//127.0.0.1:")

    public_key_b64 = str(keypair.get("publicKeyB64") or "")
    if not public_key_b64:
        raise RuntimeUnavailable("the LiTeWork runtime keypair has no public key")

    candidates = [
        device for device in (devices if isinstance(devices, list) else [])
        if isinstance(device, dict) and device.get("scope") == "runtime" and device.get("token")
    ]
    if not candidates:
        raise RuntimeUnavailable(
            "no runtime-scope device is paired with LiTeWork; pair one before streaming"
        )
    candidates.sort(key=lambda device: float(device.get("lastSeenAt") or 0), reverse=True)
    return RuntimeEndpoint(
        url=url,
        server_public_key=base64.b64decode(public_key_b64),
        device_token=str(candidates[0]["token"]),
        runtime_id=str(metadata.get("runtimeId") or ""),
    )


class RuntimeSession:
    """One authenticated connection; many concurrent requests and streams."""

    def __init__(self, endpoint: RuntimeEndpoint) -> None:
        self._endpoint = endpoint
        self._ws: Any = None
        self._shared: bytes = b""
        self._reader: asyncio.Task[None] | None = None
        self._waiters: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._closed = asyncio.Event()

    # -- framing -----------------------------------------------------------

    def _seal(self, payload: dict[str, Any]) -> str:
        from nacl import bindings, utils

        nonce = utils.random(NONCE_BYTES)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return base64.b64encode(nonce + bindings.crypto_secretbox(body, nonce, self._shared)).decode()

    def _open(self, frame: str | bytes) -> dict[str, Any]:
        from nacl import bindings

        raw = base64.b64decode(frame) if isinstance(frame, str) else bytes(frame)
        plaintext = bindings.crypto_secretbox_open(raw[NONCE_BYTES:], raw[:NONCE_BYTES], self._shared)
        value = json.loads(plaintext)
        return value if isinstance(value, dict) else {}

    # -- lifecycle ---------------------------------------------------------

    async def connect(self, *, timeout: float = 10.0) -> None:
        import websockets
        from nacl import bindings, utils

        secret = utils.random(32)
        self._shared = bindings.crypto_box_beforenm(self._endpoint.server_public_key, secret)
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self._endpoint.url, max_size=MAX_FRAME_BYTES), timeout
            )
            await self._ws.send(json.dumps({
                "type": "e2ee_hello",
                "publicKeyB64": base64.b64encode(bindings.crypto_scalarmult_base(secret)).decode(),
            }))
            ready = json.loads(await asyncio.wait_for(self._ws.recv(), timeout))
            if ready.get("type") != "e2ee_ready":
                raise RuntimeUnavailable(f"unexpected handshake frame: {ready.get('type')!r}")
            await self._ws.send(self._seal({
                "type": "e2ee_auth",
                "deviceToken": self._endpoint.device_token,
            }))
            authenticated = self._open(await asyncio.wait_for(self._ws.recv(), timeout))
            if authenticated.get("type") != "e2ee_authenticated":
                raise RuntimeUnavailable(f"LiTeWork refused the device credential: {authenticated}")
        except RuntimeUnavailable:
            await self.close()
            raise
        except Exception as exc:  # noqa: BLE001 - every failure is one unreachable runtime
            await self.close()
            raise RuntimeUnavailable(f"LiTeWork runtime handshake failed: {exc}") from exc
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        self._closed.set()
        reader, self._reader = self._reader, None
        if reader is not None and not reader.done():
            reader.cancel()
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001 - closing a dead socket is not an error
                pass
        for queue in list(self._waiters.values()):
            queue.put_nowait({"__closed__": True})
        self._waiters.clear()

    async def _read_loop(self) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            async for raw in ws:
                frame = self._open(raw)
                if frame.get("_keepalive"):
                    continue
                queue = self._waiters.get(str(frame.get("id") or ""))
                if queue is not None:
                    queue.put_nowait(frame)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a dropped socket ends every stream on it
            pass
        finally:
            for queue in list(self._waiters.values()):
                queue.put_nowait({"__closed__": True})

    # -- calls -------------------------------------------------------------

    @staticmethod
    def _unwrap(frame: dict[str, Any]) -> dict[str, Any]:
        if frame.get("__closed__"):
            raise RuntimeUnavailable("the LiTeWork runtime connection closed")
        if frame.get("ok") is not True:
            error = frame.get("error") if isinstance(frame.get("error"), dict) else {}
            raise RuntimeUnavailable(str(error.get("message") or "LiTeWork RPC failed"))
        result = frame.get("result")
        return result if isinstance(result, dict) else {}

    async def _send(self, request_id: str, method: str, params: dict[str, Any]) -> None:
        if self._ws is None:
            raise RuntimeUnavailable("the LiTeWork runtime connection is not open")
        await self._ws.send(self._seal({
            "id": request_id,
            "deviceToken": self._endpoint.device_token,
            "method": method,
            "params": params,
        }))

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        request_id = str(uuid.uuid4())
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._waiters[request_id] = queue
        try:
            await self._send(request_id, method, params or {})
            return self._unwrap(await asyncio.wait_for(queue.get(), timeout))
        finally:
            self._waiters.pop(request_id, None)

    async def stream(
        self, method: str, params: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield every emit of a streaming method until the socket or caller ends it."""
        request_id = str(uuid.uuid4())
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._waiters[request_id] = queue
        try:
            await self._send(request_id, method, params or {})
            while True:
                frame = await queue.get()
                if frame.get("__closed__"):
                    return
                yield self._unwrap(frame)
        finally:
            self._waiters.pop(request_id, None)


@asynccontextmanager
async def runtime_session(
    profile_dir: str | os.PathLike[str] | None = None,
) -> AsyncIterator[RuntimeSession]:
    session = RuntimeSession(discover(profile_dir))
    await session.connect()
    try:
        yield session
    finally:
        await session.close()
