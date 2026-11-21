"""Grant-scoped, read-only Material bridge for external AgentRuntime tools.

The bridge is transport and authorization glue only.  Material remains owned by
the injected reader; no body, revision, or subscription state is copied here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import secrets
import threading
from typing import Any, Mapping, Protocol


class MaterialBridgeError(RuntimeError):
    """The Material bridge could not establish a safe read boundary."""


@dataclass(frozen=True, slots=True)
class MaterialProjection:
    """One bounded view read directly from a Material authority."""

    material_ref: str
    revision: str
    content: Mapping[str, Any]


class MaterialProjectionReader(Protocol):
    """Authority-owned query port; implementations must not query EventBus stock."""

    def read(self, material_ref: str) -> MaterialProjection: ...


@dataclass(frozen=True, slots=True)
class MaterialBridgeAccess:
    bridge_url: str
    bridge_token: str


@dataclass(frozen=True, slots=True)
class _Lease:
    grant_id: str
    policy_ref: str
    token_digest: bytes
    allowed_material_refs: frozenset[str]
    expires_at: datetime


def _parse_expiry(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MaterialBridgeError("capability grant requires expires_at")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise MaterialBridgeError("capability grant expires_at is not ISO-8601") from error
    if parsed.tzinfo is None:
        raise MaterialBridgeError("capability grant expires_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


class _BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, bridge: "AgentRuntimeMaterialBridge") -> None:
        self.bridge = bridge
        super().__init__(("127.0.0.1", 0), _MaterialReadHandler)


class _MaterialReadHandler(BaseHTTPRequestHandler):
    server: _BridgeServer
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP hook
        if self.path != "/v1/material/read":
            self._reply(404, {"error": "not_found"})
            return
        length = self.headers.get("content-length")
        if length is None:
            self._reply(411, {"error": "content_length_required"})
            return
        try:
            size = int(length)
        except ValueError:
            self._reply(400, {"error": "invalid_content_length"})
            return
        if size < 1 or size > self.server.bridge.max_request_bytes:
            self._reply(413, {"error": "request_too_large"})
            return
        try:
            request = json.loads(self.rfile.read(size))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._reply(400, {"error": "invalid_json"})
            return
        material_ref = request.get("material_ref") if isinstance(request, dict) else None
        if not isinstance(material_ref, str) or not material_ref:
            self._reply(400, {"error": "invalid_material_ref"})
            return
        authorization = self.headers.get("authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        denial = self.server.bridge._authorize(
            grant_id=self.headers.get("x-omni-grant-id", ""),
            policy_ref=self.headers.get("x-omni-policy-ref", ""),
            token=token,
            material_ref=material_ref,
        )
        if denial is not None:
            self._reply(403, {"error": denial})
            return
        try:
            projection = self.server.bridge.reader.read(material_ref)
        except Exception:  # authority errors must not leak filesystem or body details
            self._reply(404, {"error": "material_unavailable"})
            return
        if projection.material_ref != material_ref:
            self._reply(502, {"error": "authority_ref_mismatch"})
            return
        self._reply(
            200,
            {
                "material_ref": projection.material_ref,
                "revision": projection.revision,
                "content": projection.content,
            },
        )

    def _reply(self, status: int, value: Mapping[str, Any]) -> None:
        try:
            body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            status = 502
            body = b'{"error":"authority_projection_not_json"}'
        if len(body) > self.server.bridge.max_response_bytes:
            status = 413
            body = b'{"error":"projection_too_large"}'
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class AgentRuntimeMaterialBridge:
    """Serve exact-ref Material projections to local runtime processes."""

    def __init__(
        self,
        reader: MaterialProjectionReader,
        *,
        max_request_bytes: int = 4096,
        max_response_bytes: int = 262_144,
    ) -> None:
        if max_request_bytes < 1 or max_response_bytes < 1:
            raise ValueError("Material bridge byte limits must be positive")
        self.reader = reader
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self._leases: dict[str, _Lease] = {}
        self._lock = threading.Lock()
        self._server: _BridgeServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "AgentRuntimeMaterialBridge":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self) -> None:
        if self._server is not None:
            return
        server = _BridgeServer(self)
        thread = threading.Thread(
            target=server.serve_forever,
            name="omni-agent-runtime-material-bridge",
            daemon=True,
        )
        self._server = server
        self._thread = thread
        thread.start()

    def grant(self, capability_grant: Mapping[str, Any]) -> MaterialBridgeAccess:
        """Create a short-lived bridge credential from an Omni capability grant."""
        grant_id = capability_grant.get("grant_id")
        policy_ref = capability_grant.get("policy_ref")
        refs = capability_grant.get("allowed_material_refs")
        if not isinstance(grant_id, str) or not grant_id:
            raise MaterialBridgeError("capability grant requires grant_id")
        if not isinstance(policy_ref, str) or not policy_ref:
            raise MaterialBridgeError("capability grant requires policy_ref")
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            raise MaterialBridgeError("allowed_material_refs must be a list of non-empty strings")
        expires_at = _parse_expiry(capability_grant.get("expires_at"))
        if expires_at <= datetime.now(timezone.utc):
            raise MaterialBridgeError("capability grant is expired")
        token = secrets.token_urlsafe(32)
        lease = _Lease(
            grant_id=grant_id,
            policy_ref=policy_ref,
            token_digest=_token_digest(token),
            allowed_material_refs=frozenset(refs),
            expires_at=expires_at,
        )
        with self._lock:
            self._leases[grant_id] = lease
        self.start()
        assert self._server is not None
        host, port = self._server.server_address
        return MaterialBridgeAccess(
            bridge_url=f"http://{host}:{port}/v1/material/read",
            bridge_token=token,
        )

    def revoke(self, grant_id: str) -> None:
        with self._lock:
            self._leases.pop(grant_id, None)

    def close(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        with self._lock:
            self._leases.clear()
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)

    def _authorize(
        self,
        *,
        grant_id: str,
        policy_ref: str,
        token: str,
        material_ref: str,
    ) -> str | None:
        with self._lock:
            lease = self._leases.get(grant_id)
        if lease is None:
            return "unknown_grant"
        if lease.expires_at <= datetime.now(timezone.utc):
            self.revoke(grant_id)
            return "expired_grant"
        if policy_ref != lease.policy_ref:
            return "policy_mismatch"
        if not token or not hmac.compare_digest(_token_digest(token), lease.token_digest):
            return "invalid_token"
        if material_ref not in lease.allowed_material_refs:
            return "material_ref_not_granted"
        return None


class MaterialWikiProjectionReader:
    """Read a current canonical Material Wiki version without EventBus lookup."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def read(self, material_ref: str) -> MaterialProjection:
        document = self._service.get(material_ref)
        version = document.current or document.head
        return MaterialProjection(
            material_ref=document.material_id,
            revision=version.version,
            content={
                "project_id": document.project_id,
                "profile_id": document.profile_id,
                "title": document.title,
                "canonical_ref": document.canonical_ref,
                "status": version.status,
                "body": version.body,
                "created_at": version.created_at,
            },
        )


__all__ = [
    "AgentRuntimeMaterialBridge",
    "MaterialBridgeAccess",
    "MaterialBridgeError",
    "MaterialProjection",
    "MaterialProjectionReader",
    "MaterialWikiProjectionReader",
]
