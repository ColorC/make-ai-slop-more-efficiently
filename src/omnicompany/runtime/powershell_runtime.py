"""Canonical PowerShell 7 executable resolution for OmniCompany."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


_POWERSHELL_NAMES = frozenset({"powershell", "powershell.exe", "pwsh", "pwsh.exe"})


def _command_name(value: str) -> str:
    return str(value).replace("\\", "/").rsplit("/", 1)[-1].lower()


def resolve_powershell7() -> str | None:
    """Return an executable path for PowerShell 7, if installed.

    Include the WindowsApps execution alias because it may be added after the
    parent process (for example ccdaemon) was started and therefore not be in
    that process's inherited PATH.
    """
    for name in ("pwsh", "pwsh.exe"):
        resolved = shutil.which(name)
        if resolved:
            return resolved

    if os.name != "nt":
        return None

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        alias = Path(local_app_data) / "Microsoft" / "WindowsApps" / "pwsh.exe"
        if alias.is_file():
            return str(alias)

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    msi_path = Path(program_files) / "PowerShell" / "7" / "pwsh.exe"
    if msi_path.is_file():
        return str(msi_path)

    # MSIX installs use a versioned directory; choose the newest one so an
    # upgrade does not require a source change.
    windows_apps = Path(program_files) / "WindowsApps"
    try:
        candidates = sorted(
            windows_apps.glob("Microsoft.PowerShell_*_x64__8wekyb3d8bbwe/pwsh.exe"),
            reverse=True,
        )
    except OSError:
        candidates = []
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def require_powershell7() -> str:
    """Return PowerShell 7 or raise an actionable error."""
    resolved = resolve_powershell7()
    if resolved is None:
        raise RuntimeError(
            "OmniCompany requires PowerShell 7 (pwsh.exe); "
            "install it before starting a PowerShell session."
        )
    return resolved


def normalize_powershell_command(command: list[str]) -> list[str]:
    """Rewrite any PowerShell command token to the canonical PS7 executable."""
    if os.name != "nt" or not command or _command_name(command[0]) not in _POWERSHELL_NAMES:
        return list(command)
    return [require_powershell7(), *command[1:]]


__all__ = ["normalize_powershell_command", "require_powershell7", "resolve_powershell7"]
