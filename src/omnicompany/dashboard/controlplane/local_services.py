from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from omnicompany.core.config import omni_workspace_root

local_services_router = APIRouter(prefix="/api/local", tags=["local-services"])

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}

_ALIASES = {
    "progress": "progress-service",
    "whatnow": "progress-service",
}
_service_locks: dict[str, asyncio.Lock] = {}


def _repo_root() -> Path:
    return omni_workspace_root()


def _registry() -> dict[str, dict[str, Any]]:
    repo = _repo_root()
    progress_url = os.environ.get("OMNI_PROGRESS_SERVICE_URL", "http://127.0.0.1:8230").rstrip("/")
    return {
        "progress-service": {
            "id": "progress-service",
            "aliases": ["progress", "whatnow"],
            "base": progress_url,
            "health_path": "/health",
            "ensure_cmd": [
                sys.executable,
                str(repo / "services" / "_progress" / "progress_service" / "ensure_progress_service_running.py"),
            ],
            "cwd": str(repo),
            "manual_start": str(
                repo / "services" / "_progress" / "progress_service" / "start-progress-service.cmd"
            ),
            "timeout": httpx.Timeout(10.0, read=120.0),
        },
    }


def _canonical_service_id(service: str) -> str:
    key = (service or "").strip().lower()
    return _ALIASES.get(key, key)


def _service_config(service: str) -> dict[str, Any]:
    service_id = _canonical_service_id(service)
    cfg = _registry().get(service_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Unknown local service: {service}")
    return cfg


def _join_url(base: str, path: str = "", query: str = "") -> str:
    url = f"{base.rstrip('/')}/{path.lstrip('/')}" if path else base.rstrip("/")
    if query:
        url = f"{url}?{query}"
    return url


def _target_url(service: str, path: str = "", query: str = "") -> str:
    cfg = _service_config(service)
    return _join_url(str(cfg["base"]), path, query)


def _filter_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP}


async def _service_status(service: str) -> dict[str, Any]:
    cfg = _service_config(service)
    target = _join_url(str(cfg["base"]), str(cfg.get("health_path") or "/"))
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(target, headers={"accept-encoding": "identity"})
        return {
            "id": cfg["id"],
            "aliases": cfg.get("aliases", []),
            "base": cfg["base"],
            "running": resp.status_code < 500,
            "status_code": resp.status_code,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "manual_start": cfg.get("manual_start"),
        }
    except httpx.HTTPError as exc:
        return {
            "id": cfg["id"],
            "aliases": cfg.get("aliases", []),
            "base": cfg["base"],
            "running": False,
            "error": str(exc),
            "manual_start": cfg.get("manual_start"),
        }


def _run_ensure_command(cfg: dict[str, Any]) -> dict[str, Any]:
    cmd = cfg.get("ensure_cmd")
    if not cmd:
        return {"ok": False, "reason": "no ensure command registered"}
    kwargs: dict[str, Any] = {
        "cwd": cfg.get("cwd") or str(_repo_root()),
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
        "text": True,
        "timeout": 20,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000
    try:
        proc = subprocess.run([str(part) for part in cmd], **kwargs)
    except subprocess.TimeoutExpired as exc:
        return {"ok": False, "reason": f"ensure command timed out: {exc}"}
    except OSError as exc:
        return {"ok": False, "reason": f"ensure command failed to launch: {exc}"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "output": (proc.stdout or "")[-2000:],
    }


async def _ensure_service(service: str, *, wait_secs: float = 12.0) -> dict[str, Any]:
    service_id = _canonical_service_id(service)
    cfg = _service_config(service_id)
    status = await _service_status(service_id)
    if status.get("running"):
        return {"ok": True, "status": status, "ensured": False}

    lock = _service_locks.setdefault(service_id, asyncio.Lock())
    async with lock:
        status = await _service_status(service_id)
        if status.get("running"):
            return {"ok": True, "status": status, "ensured": False}

        ensure_result = await run_in_threadpool(_run_ensure_command, cfg)
        deadline = time.monotonic() + wait_secs
        while time.monotonic() < deadline:
            await asyncio.sleep(0.4)
            status = await _service_status(service_id)
            if status.get("running"):
                return {"ok": True, "status": status, "ensured": True, "ensure": ensure_result}
        status = await _service_status(service_id)
        return {"ok": False, "status": status, "ensure": ensure_result}


@local_services_router.get("/services")
async def list_local_services() -> dict[str, Any]:
    services = []
    for service_id in _registry():
        services.append(await _service_status(service_id))
    return {"ok": True, "services": services}


@local_services_router.post("/{service}/ensure")
async def ensure_local_service(service: str) -> dict[str, Any]:
    result = await _ensure_service(service)
    return {"ok": bool(result.get("ok")), "service": _canonical_service_id(service), **result}


@local_services_router.api_route(
    "/{service}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def proxy_local_service(service: str, path: str, request: Request) -> Response:
    cfg = _service_config(service)
    ensured = await _ensure_service(str(cfg["id"]))
    if not ensured.get("ok"):
        raise HTTPException(
            status_code=503,
            detail={
                "message": f"{cfg['id']} is not reachable through the dashboard local-service proxy.",
                "manual_start": cfg.get("manual_start"),
                "status": ensured.get("status"),
                "ensure": ensured.get("ensure"),
            },
        )

    body = await request.body()
    headers = _filter_headers(dict(request.headers))
    headers["accept-encoding"] = "identity"
    target = _target_url(str(cfg["id"]), path, request.url.query)

    async with httpx.AsyncClient(timeout=cfg.get("timeout")) as client:
        try:
            upstream = await client.request(
                method=request.method,
                url=target,
                headers=headers,
                content=body,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"{cfg['id']} proxy error: {exc}") from exc

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_filter_headers(dict(upstream.headers)),
        media_type=upstream.headers.get("content-type"),
    )
