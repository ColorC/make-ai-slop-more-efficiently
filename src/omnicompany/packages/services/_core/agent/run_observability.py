# [OMNI] origin=codex domain=services/agent ts=2026-07-15
# [OMNI] material_id="material:core.agent.run_observability.runtime.py"
"""Fail-closed, file-backed liveness for every modern AgentNodeLoop run.

EventBus remains the semantic audit trail.  This small status file is the
process supervisor's independent liveness channel, including the period before
the first model stream event arrives.  Losing this channel is an execution
failure; callers must terminate the process tree, repair observation, and
restart from the latest durable workspace state.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Any

from omnicompany.core.config import resolve_runtime_data_dir


class AgentObservabilityError(RuntimeError):
    """The Agent cannot prove its current process/stage/heartbeat."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_trace_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return (safe or "unknown-trace")[:160]


class AgentRunObserver:
    """Atomically publish one Agent run's latest observable state."""

    def __init__(
        self,
        *,
        trace_id: str,
        agent_name: str,
        domain: str = "",
        status_file: str | os.PathLike[str] | None = None,
        stdout_log: str = "",
        stderr_log: str = "",
        event_db: str = "",
        trace_export: str = "",
    ) -> None:
        self.trace_id = trace_id
        self.agent_name = agent_name
        self.domain = domain
        self.path = (
            Path(status_file).expanduser().resolve()
            if status_file
            else resolve_runtime_data_dir("agent_observability")
            / f"{_safe_trace_id(trace_id)}.json"
        )
        self.started_at = _utc_now()
        self.started_monotonic = time.monotonic()
        self.sequence = 0
        self._base = {
            "schema_version": 1,
            "trace_id": trace_id,
            "agent_name": agent_name,
            "domain": domain,
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "started_at": self.started_at,
            "status_file": str(self.path),
            "stdout_log": stdout_log,
            "stderr_log": stderr_log,
            "event_db": event_db,
            "trace_export": trace_export,
        }

    @classmethod
    def from_input(cls, input_data: dict[str, Any], *, trace_id: str, agent_name: str) -> "AgentRunObserver":
        return cls(
            trace_id=trace_id,
            agent_name=agent_name,
            domain=str(input_data.get("domain") or ""),
            status_file=input_data.get("agent_observability_file"),
            stdout_log=str(input_data.get("agent_stdout_log") or ""),
            stderr_log=str(input_data.get("agent_stderr_log") or ""),
            event_db=str(input_data.get("agent_event_db") or ""),
            trace_export=str(input_data.get("agent_trace_export") or ""),
        )

    def update(self, stage: str, **details: Any) -> dict[str, Any]:
        """Write a fresh heartbeat or fail the Agent immediately."""
        self.sequence += 1
        payload = {
            **self._base,
            "sequence": self.sequence,
            "stage": stage,
            "heartbeat_at": _utc_now(),
            "elapsed_seconds": round(time.monotonic() - self.started_monotonic, 3),
            "details": details,
        }
        temp_path = self.path.with_name(
            f".{self.path.name}.{os.getpid()}.{self.sequence}.tmp"
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            # Windows readers can briefly hold a sharing lock between open()
            # and close().  That is not a real loss of the existing status
            # file, so absorb only this bounded replace collision.  Persistent
            # failure still raises AgentObservabilityError and stops the run.
            for replace_attempt in range(10):
                try:
                    os.replace(temp_path, self.path)
                    break
                except PermissionError:
                    if os.name != "nt" or replace_attempt == 9:
                        raise
                    time.sleep(0.02 * (replace_attempt + 1))
        except Exception as exc:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise AgentObservabilityError(
                f"Agent observability gap at stage={stage!r}: cannot update {self.path}: {exc}"
            ) from exc
        return payload

    def announce(self, stage: str, **details: Any) -> dict[str, Any]:
        payload = self.update(stage, **details)
        print(
            f"[agent-observe pid={payload['pid']} trace={self.trace_id}] "
            f"stage={stage} status={self.path}",
            file=sys.stderr,
            flush=True,
        )
        return payload
