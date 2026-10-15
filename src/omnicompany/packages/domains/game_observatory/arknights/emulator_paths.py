# [OMNI] origin=game-observatory.arknights-tournament created_by=claude
# [OMNI] intent=解析本机 MuMu 的 adb/MuMuManager 路径, 让比赛设施在多台机器上同源运行
"""Find the emulator's tools instead of assuming one machine's install.

The match ran from a single workstation for its first ten rounds, so the adb
and MuMuManager paths were written as constants. They stopped being true the
moment a second box joined: the same MuMu ships under ``MuMu`` on one machine
and ``MuMu Player 12`` on another, on a different drive. Rather than branch per
host, ask the filesystem, and let an environment variable win when someone
needs to point at an install this list has never heard of.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Checked in order. Newer MuMu releases install as "MuMu Player 12"; the
#: shorter name is the older layout still present on the first workstation.
_ROOTS = (
    r"C:\Program Files\Netease\MuMu",
    r"C:\Program Files\Netease\MuMu Player 12",
    r"D:\Program Files\Netease\MuMu Player 12",
    r"D:\Program Files\Netease\MuMu",
    r"C:\Program Files (x86)\Netease\MuMu",
)

#: adb lives beside the manager in ``nx_main``; some builds also ship one under
#: ``shell``, which is the same binary.
_SUBDIRS = ("nx_main", "shell")


def _find(filename: str, env_var: str) -> Path:
    override = os.environ.get(env_var, "").strip()
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"{env_var} points at a missing file: {candidate}")
    for root in _ROOTS:
        for subdir in _SUBDIRS:
            candidate = Path(root) / subdir / filename
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(
        f"{filename} was not found in any known MuMu install; "
        f"set {env_var} to its full path"
    )


def adb_path() -> Path:
    """The adb binary shipped with the local MuMu install."""
    return _find("adb.exe", "OMNI_ARKNIGHTS_ADB")


def manager_path() -> Path:
    """MuMuManager, used to inspect and revive emulator instances."""
    return _find("MuMuManager.exe", "OMNI_ARKNIGHTS_MUMU_MANAGER")
