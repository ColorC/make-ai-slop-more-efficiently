"""Non-blocking project-card asset generation for newly registered projects."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from omnicompany.core.projects_registry import assets_dir


_DEFAULT_PIPELINE = Path(
    "C:/workspace/webworks/apps/aigc-lab/project-cards/generate.mjs"
)
_PROJECT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


def _status(status: str, reason: str, **details: Any) -> dict[str, Any]:
    return {"status": status, "reason": reason, **details}


def _autostart_enabled() -> bool:
    value = os.environ.get("OMNI_PROJECT_ASSET_AUTOSTART", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _pipeline_path() -> Path:
    configured = os.environ.get("OMNI_PROJECT_ASSET_PIPELINE", "").strip()
    return Path(configured) if configured else _DEFAULT_PIPELINE


def autostart_project_assets(project_id: str) -> dict[str, Any]:
    """Start the card-art pipeline without waiting for it to finish.

    This helper intentionally accepts only a project identifier and passes it as
    its own argv value.  It never invokes a shell, so project metadata cannot
    alter the command line beyond the ``--only`` argument.
    """
    if not _PROJECT_ID_RE.fullmatch(project_id):
        return _status("failed", "invalid_project_id")
    if not _autostart_enabled():
        return _status("skipped", "disabled")

    pipeline = _pipeline_path()
    if not pipeline.is_file():
        return _status("failed", "pipeline_not_found", pipeline=str(pipeline))

    node = shutil.which("node")
    if not node:
        return _status("failed", "node_not_found", pipeline=str(pipeline))

    try:
        log_path = assets_dir() / ".project-art" / project_id / "autostart.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return _status("failed", "log_directory_unavailable", error=str(exc))

    argv = [node, str(pipeline), f"--only={project_id}"]
    popen_kwargs: dict[str, Any] = {
        "cwd": str(pipeline.parent),
        "stdin": subprocess.DEVNULL,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    try:
        with log_path.open("ab") as log_file:
            process = subprocess.Popen(argv, stdout=log_file, **popen_kwargs)
    except OSError as exc:
        return _status(
            "failed",
            "spawn_failed",
            pipeline=str(pipeline),
            log_path=str(log_path),
            error=str(exc),
        )

    return _status(
        "started",
        "started",
        pid=process.pid,
        pipeline=str(pipeline),
        log_path=str(log_path),
    )
