# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-17T00:00:00+08:00 type=lib status=active
# [OMNI] summary="omni remote 单次操作会话: 渲染+下发+解析回执+落本机回执与台账。"
"""One ``omni remote`` operation: every remote call it makes, and its receipt + ledger line."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .profiles import NodeProfile
from .pstext import assert_plain_payload, command_chars, prelude
from .store import append_ledger, ledger_path, new_op_id, receipt_dir, write_json

MARKER = "OMNI-REMOTE-RESULT "
OUT_BEGIN = "OMNI-REMOTE-OUTPUT-BEGIN"
OUT_END = "OMNI-REMOTE-OUTPUT-END"


def parse_stdout(stdout: str) -> tuple[dict[str, Any] | None, str, str]:
    """Return (marker json, captured script output, direct console text outside the markers)."""
    data = None
    lines = stdout.splitlines()
    for line in reversed(lines):
        if line.startswith(MARKER):
            try:
                data = json.loads(line[len(MARKER):])
            except ValueError:
                data = None
            break
    output, direct = "", stdout
    if OUT_BEGIN in stdout:
        before, _, rest = stdout.partition(OUT_BEGIN)
        body = rest.partition(OUT_END)[0]
        for newline in ("\r\n", "\n"):
            if body.startswith(newline):
                body = body[len(newline):]
                break
        for newline in ("\r\n", "\n"):
            if body.endswith(newline):
                body = body[: -len(newline)]
                break
        output = body.replace("\r\n", "\n")
        direct = before
    else:
        direct = "\n".join(line for line in lines if not line.startswith(MARKER))
    return data, output, direct.strip("\r\n")


class Session:
    def __init__(self, profile: NodeProfile, backend: Any, op: str, *, label: str | None = None) -> None:
        self.profile = profile
        self.backend = backend
        self.op = op
        self.label = label
        self.op_id = new_op_id()
        self.receipt_dir = receipt_dir(self.op_id)
        self.calls: list[dict[str, Any]] = []
        self.started = time.monotonic()

    # path helpers: the fake backend maps every remote path under a local root
    def r(self, remote: str) -> str:
        return self.backend.map_path(remote) if remote else remote

    @property
    def owned_roots(self) -> list[str]:
        return [self.r(p) for p in self.profile.owned_roots]

    @property
    def write_roots(self) -> list[str]:
        return [self.r(p) for p in self.profile.write_roots]

    def prelude(self) -> str:
        return prelude(self.owned_roots, self.write_roots)

    def guard(self, text: str, what: str) -> None:
        if self.profile.forbid_encoded_payload:
            assert_plain_payload(text, node=self.profile.name, what=what)

    def exec(self, body: str, *, timeout_s: int, label: str, mode: str | None = None) -> dict[str, Any]:
        command = self.prelude() + "\n" + body
        self.guard(command, f"{label} 命令")
        n = len(self.calls) + 1
        command_path = self.receipt_dir / f"command-{n}-{label}.ps1"
        command_path.write_text(command, encoding="utf-8")
        native = self.backend.exec_ps(command, timeout_s=timeout_s, mode=mode)
        data, output, direct = parse_stdout(native.get("stdout") or "")
        native_path = write_json(self.receipt_dir / f"native-{n}-{label}.json", native)
        call = {
            "n": n,
            "label": label,
            "task_id": native.get("task_id"),
            "status": native.get("status"),
            "exit_code": native.get("exit_code"),
            "termination": native.get("termination"),
            "wall_s": native.get("wall_s"),
            "queue_latency_ms": native.get("queue_latency_ms"),
            "chars": command_chars(command),
            "transport_errors": native.get("transport_errors") or [],
            "command": str(command_path),
            "native": str(native_path),
        }
        self.calls.append(call)
        return {"call": call, "native": native, "data": data, "output": output, "direct": direct}

    def finish(self, result: dict[str, Any], *, ledger_extra: dict[str, Any] | None = None) -> dict[str, Any]:
        wall = round(time.monotonic() - self.started, 2)
        result_path = self.receipt_dir / "result.json"
        paths = {
            "result": str(result_path),
            "receipt_dir": str(self.receipt_dir),
            "ledger": str(ledger_path()),
            **(result.get("paths") or {}),
        }
        full = {
            "schema": "omni.remote.op/1",
            "op_id": self.op_id,
            "op": self.op,
            "node": self.profile.name,
            "node_id": self.profile.node_id,
            "backend": self.backend.name,
            "label": self.label,
            **result,
            "paths": paths,
            "calls": self.calls,
            "wall_s": wall,
        }
        write_json(result_path, full)
        entry = {
            "op_id": self.op_id,
            "op": self.op,
            "node": self.profile.name,
            "backend": self.backend.name,
            "ok": bool(full.get("ok")),
            "exit_code": full.get("exit_code"),
            "phase": full.get("phase"),
            "wall_s": wall,
            "tasks": [c.get("task_id") for c in self.calls],
            "chars": sum(int(c.get("chars") or 0) for c in self.calls),
            "label": self.label,
            "result": str(result_path),
            **(ledger_extra or {}),
        }
        append_ledger(entry)
        return full


def refused(profile: NodeProfile | None, backend: Any, op: str, error: Any, *, label: str | None = None) -> dict[str, Any]:
    """Record a local refusal (nothing was sent) with the same receipt shape."""
    if profile is None:
        return {"schema": "omni.remote.op/1", "op": op, "ok": False, "phase": "refused", "exit_code": None,
                "refused": {"rule": getattr(error, "rule", "refused"), "message": str(error), "detail": getattr(error, "detail", {})}}
    session = Session(profile, backend, op, label=label)
    return session.finish({
        "ok": False,
        "phase": "refused",
        "exit_code": None,
        "refused": {"rule": getattr(error, "rule", "refused"), "message": str(error), "detail": getattr(error, "detail", {})},
    })


def save_text(session: Session, name: str, text: str) -> str:
    path: Path = session.receipt_dir / name
    path.write_text(text or "", encoding="utf-8")
    return str(path)
