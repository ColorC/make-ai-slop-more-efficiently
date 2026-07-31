"""Workspace-scoped local IPC daemon for a warm OmniParser engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socketserver
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROTOCOL_VERSION = "game-observatory.omniparser-daemon.v1"
WORKER_VERSION = "1"
MAX_REQUEST_BYTES = 64 * 1024


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    pending.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(pending, path)


def daemon_job_hash(
    *,
    image_sha256: str,
    output_dir: Path,
    config_hash: str,
) -> str:
    body = json.dumps(
        {
            "image_sha256": image_sha256,
            "output_dir": str(output_dir.resolve()),
            "config_hash": config_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class _FakeEngine:
    """Small process-real engine used only by bounded daemon lifecycle tests."""

    startup_seconds = 0.0

    def locate(self, image_path: Path, output_dir: Path) -> dict[str, Any]:
        from PIL import Image

        delay = float(os.environ.get("OMNICOMPANY_OMNIPARSER_FAKE_DELAY", "0"))
        if delay:
            time.sleep(delay)
        output_dir.mkdir(parents=True, exist_ok=True)
        annotated = output_dir / "annotated.png"
        shutil.copyfile(image_path, annotated)
        with Image.open(image_path) as image:
            width, height = image.size
        image_sha = hashlib.sha256(image_path.read_bytes()).hexdigest()
        raw = output_dir / "raw-elements.json"
        raw.write_text("[]", encoding="utf-8")
        raw_ocr = output_dir / "raw-micro-glyph-ocr.json"
        raw_ocr.write_text("[]", encoding="utf-8")
        result = {
            "schema": "game-observatory.visual-locator-run.v1",
            "locator": "omniparser-v2",
            "generated_at": _utc_now(),
            "image": {
                "path": str(image_path),
                "sha256": image_sha,
                "width": width,
                "height": height,
            },
            "config": {"box_threshold": 0.05, "coordinate_space": "source_pixels"},
            "metrics": {"startup_seconds": 0.0, "parse_seconds": delay},
            "elements": [],
            "annotated_image": str(annotated),
            "raw_elements": str(raw),
            "raw_micro_glyph_ocr": str(raw_ocr),
        }
        _atomic_json(output_dir / "result.json", result)
        return result


def _acquire_process_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"1")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


class _DaemonServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    secret: str
    config_hash: str
    weights_hash: str
    runtime_root: Path
    engine: Any
    inference_lock: threading.Lock
    started_at: str


class _RequestHandler(socketserver.StreamRequestHandler):
    def _reply(self, payload: dict[str, Any]) -> None:
        self.wfile.write(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            + b"\n"
        )

    def handle(self) -> None:
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            self._reply({"ok": False, "error": "invalid_request_size"})
            return
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            self._reply({"ok": False, "error": "invalid_json"})
            return
        server = self.server
        assert isinstance(server, _DaemonServer)
        if request.get("secret") != server.secret:
            self._reply({"ok": False, "error": "unauthorized"})
            return
        operation = request.get("operation")
        if operation == "health":
            self._reply(
                {
                    "ok": True,
                    "protocol": PROTOCOL_VERSION,
                    "worker_version": WORKER_VERSION,
                    "pid": os.getpid(),
                    "config_hash": server.config_hash,
                    "weights_hash": server.weights_hash,
                    "started_at": server.started_at,
                    "busy": server.inference_lock.locked(),
                }
            )
            return
        if operation == "shutdown":
            self._reply({"ok": True, "stopping": True, "pid": os.getpid()})
            threading.Thread(target=server.shutdown, daemon=True).start()
            return
        if operation != "locate":
            self._reply({"ok": False, "error": "unknown_operation"})
            return
        if request.get("config_hash") != server.config_hash:
            self._reply({"ok": False, "error": "config_hash_mismatch"})
            return
        image = Path(str(request.get("image") or "")).resolve()
        output_dir = Path(str(request.get("output_dir") or "")).resolve()
        if not image.is_file():
            self._reply({"ok": False, "error": "image_missing"})
            return
        try:
            output_dir.relative_to(server.runtime_root.parent.parent)
        except ValueError:
            self._reply({"ok": False, "error": "output_outside_store"})
            return
        image_sha = hashlib.sha256(image.read_bytes()).hexdigest()
        if image_sha != request.get("image_sha256"):
            self._reply({"ok": False, "error": "image_hash_mismatch"})
            return
        expected_job_hash = daemon_job_hash(
            image_sha256=image_sha,
            output_dir=output_dir,
            config_hash=server.config_hash,
        )
        if request.get("job_hash") != expected_job_hash:
            self._reply({"ok": False, "error": "job_hash_mismatch"})
            return
        if not server.inference_lock.acquire(blocking=False):
            self._reply({"ok": False, "error": "worker_busy", "retryable": True})
            return
        started = time.perf_counter()
        try:
            server.engine.locate(image, output_dir)
            self._reply(
                {
                    "ok": True,
                    "job_hash": expected_job_hash,
                    "result": str(output_dir / "result.json"),
                    "inference_seconds": round(time.perf_counter() - started, 6),
                    "pid": os.getpid(),
                }
            )
        except Exception as exc:  # noqa: BLE001 - isolated process boundary
            self._reply(
                {
                    "ok": False,
                    "error": "inference_failed",
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                }
            )
        finally:
            server.inference_lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="game-observatory-omniparser-daemon")
    parser.add_argument("--omniparser-home", type=Path, required=True)
    parser.add_argument("--weights-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--secret", required=True)
    parser.add_argument("--config-hash", required=True)
    parser.add_argument("--weights-hash", required=True)
    parser.add_argument("--box-threshold", type=float, default=0.05)
    parser.add_argument("--fake-engine", action="store_true")
    args = parser.parse_args(argv)

    runtime_root = args.runtime_root.resolve()
    daemon_root = runtime_root / "daemon"
    identity_path = daemon_root / "identity.json"
    lock_path = daemon_root / "worker.lock"
    lock_handle = _acquire_process_lock(lock_path)
    if lock_handle is None:
        return 17
    started_at = _utc_now()
    identity = {
        "schema": PROTOCOL_VERSION,
        "worker_version": WORKER_VERSION,
        "status": "warming",
        "pid": os.getpid(),
        "port": args.port,
        "config_hash": args.config_hash,
        "weights_hash": args.weights_hash,
        "started_at": started_at,
        "runtime_root": str(runtime_root),
        "lock_path": str(lock_path),
    }
    _atomic_json(identity_path, identity)
    try:
        with _DaemonServer(("127.0.0.1", args.port), _RequestHandler) as server:
            server.secret = args.secret
            server.config_hash = args.config_hash
            server.weights_hash = args.weights_hash
            server.runtime_root = runtime_root
            server.inference_lock = threading.Lock()
            server.started_at = started_at
            if args.fake_engine:
                server.engine = _FakeEngine()
            else:
                from omniparser_worker import OmniParserEngine

                server.engine = OmniParserEngine(
                    omniparser_home=args.omniparser_home,
                    weights_root=args.weights_root,
                    box_threshold=args.box_threshold,
                )
            identity["status"] = "ready"
            identity["ready_at"] = _utc_now()
            identity["engine_startup_seconds"] = round(
                float(server.engine.startup_seconds), 6
            )
            _atomic_json(identity_path, identity)
            server.serve_forever(poll_interval=0.2)
    except Exception as exc:  # noqa: BLE001 - durable startup diagnosis
        identity["status"] = "failed"
        identity["failed_at"] = _utc_now()
        identity["error_type"] = type(exc).__name__
        identity["error"] = str(exc)
        _atomic_json(identity_path, identity)
        return 1
    finally:
        lock_handle.close()
    identity["status"] = "stopped"
    identity["stopped_at"] = _utc_now()
    _atomic_json(identity_path, identity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PROTOCOL_VERSION", "WORKER_VERSION", "daemon_job_hash", "main"]
