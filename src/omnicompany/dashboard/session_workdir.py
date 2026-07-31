"""Shared working-directory policy for dashboard-created agent sessions.

The dashboard can be opened from desktop or mobile, so its session default must
not depend on the browser or on the directory from which ccdaemon was started.
Explicit directories remain supported and are normalized before persistence.
"""

from __future__ import annotations

import os
from pathlib import Path

from omnicompany.core.config import omni_workspace_root


SESSION_DEFAULT_CWD_ENV = "OMNI_SESSION_DEFAULT_CWD"


def default_session_cwd() -> str:
    """Return the stable default for new dashboard CLI/chat sessions.

    In the normal checkout ``omni_workspace_root()`` is
    ``E:\\WindowsWorkspace\\omnicompany``; its parent is therefore the requested
    cross-project root ``E:\\WindowsWorkspace``. The environment override keeps
    tests and non-standard deployments explicit without coupling the default to
    the ccdaemon process CWD.
    """

    configured = os.environ.get(SESSION_DEFAULT_CWD_ENV, "").strip()
    candidate = Path(configured).expanduser() if configured else omni_workspace_root().parent
    return str(candidate.resolve())


def resolve_session_cwd(cwd: str | os.PathLike[str] | None) -> str:
    """Resolve and validate a session CWD, ready to persist in metadata.

    Empty values use :func:`default_session_cwd`. Relative explicit paths are
    interpreted under that default root, which makes ``omnicompany`` useful from
    both a phone and a desktop client. Other absolute directories are accepted
    unchanged (after canonicalization) and therefore remain fully traceable.
    """

    raw = str(cwd).strip() if cwd is not None else ""
    default = Path(default_session_cwd())
    candidate = Path(os.path.expandvars(raw)).expanduser() if raw else default
    if not candidate.is_absolute():
        candidate = default / candidate
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise ValueError(f"cwd does not exist: {candidate}")
    return str(candidate)
