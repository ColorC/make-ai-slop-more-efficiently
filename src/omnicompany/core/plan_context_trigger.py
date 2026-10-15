"""Small per-PTY handoff markers for next-turn plan context injection.

The canonical identity and plan binding remain in ``cc_session_bindings.json``.
This projection only answers one hot-path question: does this PTY have a newer
plan switch that the prompt hook has not injected yet?  Keeping one tiny file
per PTY avoids parsing and rewriting the multi-megabyte session registry for
every user prompt.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


_THREAD_LOCK = threading.Lock()
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _state_dir() -> Path:
    explicit = os.environ.get("OMNI_CC_DAEMON_STATE_DIR")
    if explicit:
        return Path(explicit)
    from omnicompany.core.config import omni_workspace_root

    return omni_workspace_root() / "data"


def _marker_path(pty_id: str, state_dir: Path | None = None) -> Path:
    safe_id = _SAFE_ID_RE.sub("_", str(pty_id).strip())[:200]
    if not safe_id:
        raise ValueError("pty_id must not be empty")
    return (state_dir or _state_dir()) / "plan_context_triggers" / f"{safe_id}.json"


@contextmanager
def _guard(path: Path) -> Iterator[None]:
    """Serialize the tiny read-modify-write marker across hook/CLI processes."""

    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_unlocked(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_unlocked(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def publish_plan_change(
    pty_id: str,
    plan_id: str | None,
    *,
    already_injected: bool = False,
    state_dir: Path | None = None,
) -> dict[str, Any]:
    """Publish a new switch generation without duplicating binding authority."""

    path = _marker_path(pty_id, state_dir)
    with _guard(path):
        previous = _read_unlocked(path)
        generation = max(
            time.time_ns(), int(previous.get("generation") or 0) + 1
        )
        payload = {
            "pty_id": str(pty_id),
            "plan_id": str(plan_id or ""),
            "generation": generation,
            "injected_generation": generation if already_injected else int(
                previous.get("injected_generation") or 0
            ),
            "changed_at": time.time(),
        }
        _write_unlocked(path, payload)
        return payload


def pending_plan_change(
    pty_id: str, *, state_dir: Path | None = None
) -> dict[str, Any] | None:
    """Read only one small marker; return None for no pending plan context."""

    path = _marker_path(pty_id, state_dir)
    value = _read_unlocked(path)
    generation = int(value.get("generation") or 0)
    injected = int(value.get("injected_generation") or 0)
    if not value.get("plan_id") or generation <= injected:
        return None
    return value


def claim_plan_change(
    pty_id: str, generation: int, *, state_dir: Path | None = None
) -> bool:
    """Acknowledge only the exact generation whose context was built."""

    path = _marker_path(pty_id, state_dir)
    with _guard(path):
        current = _read_unlocked(path)
        if (
            int(current.get("generation") or 0) != int(generation)
            or int(current.get("injected_generation") or 0) >= int(generation)
            or not current.get("plan_id")
        ):
            return False
        current["injected_generation"] = int(generation)
        current["injected_at"] = time.time()
        _write_unlocked(path, current)
        return True


__all__ = ["publish_plan_change", "pending_plan_change", "claim_plan_change"]
