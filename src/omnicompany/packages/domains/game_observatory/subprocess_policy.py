"""Windows process policy for headless Game Observatory commands.

The AI-player worker itself is detached from a console.  Without an explicit
creation flag, any console-subsystem child (``codex.CMD``, ``adb.exe``, or a
device CLI) can allocate a new visible ``conhost.exe`` window.  Every headless
child in this domain must therefore opt into this policy at the actual spawn
site; hiding only the outer worker is insufficient.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def headless_process_kwargs(*, creationflags: int = 0) -> dict[str, Any]:
    """Return platform-safe kwargs for a subprocess that must never show UI."""

    if sys.platform != "win32":
        return {}

    flags = creationflags | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    kwargs: dict[str, Any] = {"creationflags": flags}
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0x00000001)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs