"""One-shot execution of an explicitly authorized, deferred human request.

The OS trigger calls the regular cron CLI. Agent coordination still uses the
quiet mailbox; this path is only for a human's requested future prompt.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from omnicompany.core.config import omni_workspace_root
from omnicompany.core.session_messages import _post_input


def _verify_target(target: str, native: str) -> dict:
    import psutil

    state = json.loads((omni_workspace_root() / "data" / "pty_hosts" / f"{target}.json").read_text(encoding="utf-8"))
    provider = state.get("provider")
    if state.get("id") != target or not state.get("alive") or not (provider == "codex" or str(provider).startswith("codex_")):
        raise ValueError("expected live Codex PTY not found")
    root = psutil.Process(int(state["child_pid"]))
    for process in [root, *root.children(recursive=True)]:
        try:
            if process.name().lower() != "codex.exe":
                continue
            if any(Path(item.path).name.endswith(f"-{native}.jsonl") for item in process.open_files()):
                return {"target": target, "native_session_id": native, "codex_pid": process.pid,
                        "evidence": "live_codex_rollout_file_handle"}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    raise ValueError("target Codex process does not hold the expected native session; refusing wrong-session wake")


def execute(request_file: Path, *, dry_run: bool = False, now: datetime | None = None) -> dict:
    request = json.loads(request_file.read_text(encoding="utf-8"))
    if request.get("schema") != "omnicompany.scheduled-user-request.v1" or not request.get("user_authorization"):
        raise ValueError("an explicit human authorization and supported request schema are required")
    target = str(request.get("target_pty_id") or "")
    native = str(request.get("native_session_id") or "")
    if not re.fullmatch(r"[a-f0-9]{16,32}", target) or not re.fullmatch(r"[a-f0-9-]{36}", native):
        raise ValueError("exact PTY and native session IDs are required")
    prompt = str(request.get("prompt") or "").strip()
    if not prompt or len(prompt) > 12000 or "[OMNI AGENT MESSAGE" in prompt:
        raise ValueError("expected a deferred human request, not an agent envelope")
    due = datetime.fromisoformat(request["run_at"])
    if due.tzinfo is None:
        raise ValueError("run_at requires an explicit timezone")
    current = now or datetime.now(timezone.utc)
    attempt = request_file.with_suffix(".attempt.json")
    receipt = request_file.with_suffix(".receipt.json")
    if attempt.exists():
        return {"status": "already_attempted", "receipt": str(receipt), "sent": False}
    if not dry_run and current < due:
        return {"status": "not_due", "run_at": due.isoformat(), "sent": False}
    try:
        identity = _verify_target(target, native)
    except Exception as exc:
        if not dry_run:
            receipt.write_text(json.dumps({"status": "identity_rejected", "checked_at": current.isoformat(),
                                          "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    if dry_run:
        return {"status": "verified", "run_at": due.isoformat(), "identity": identity, "sent": False}
    # Record before transport: an ambiguous timeout must never duplicate input.
    try:
        with attempt.open("x", encoding="utf-8") as stream:
            json.dump({"started_at": current.isoformat(), "identity": identity}, stream, ensure_ascii=False)
    except FileExistsError:
        return {"status": "already_attempted", "receipt": str(receipt), "sent": False}
    result = {"request_id": request.get("request_id"), "identity": identity, "attempted_at": current.isoformat()}
    try:
        response = _post_input(target, prompt)
        result["transport"] = response
        # A mailbox write is not a wake. Require the provider-input receipt.
        result["status"] = "submitted" if response.get("ok") and response.get("submission") == "submitted" and not response.get("quiet") else "unconfirmed"
    except Exception as exc:
        result.update(status="transport_error", error=f"{type(exc).__name__}: {exc}")
    receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
