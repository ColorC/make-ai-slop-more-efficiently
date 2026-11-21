# [OMNI] origin=claude-code domain=omnicompany/core/remote ts=2026-10-24T20:10:00+08:00 type=lib status=active
# [OMNI] summary="omni remote presence: 问各在线节点距上次键鼠输入多少秒，判断用户此刻在哪台机器（share 不给节点时用）。"
"""Which machine is the user at right now.

desktop-node runs tasks inside the interactive console session, so ``GetLastInputInfo`` there
reads the real seconds since the last keyboard/mouse input. Automation that injects input also
resets it, so a busy bot machine looks "occupied" too — ``pick`` therefore returns every node
active within the window (sharing to one extra machine is cheap), and only falls back to the
single most recently used node when nobody is active.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from . import ops
from .profiles import NodeProfile
from .pstext import RemoteRefused

MARKER = "OMNI-PRESENCE "
ACTIVE_WINDOW_S = 600

PROBE_PS = """Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class OmniPresence {
  [StructLayout(LayoutKind.Sequential)] struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
  [DllImport("user32.dll")] static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
  [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  public static uint ForegroundPid() { uint pid; GetWindowThreadProcessId(GetForegroundWindow(), out pid); return pid; }
  public static long IdleMs() {
    var info = new LASTINPUTINFO(); info.cbSize = (uint)Marshal.SizeOf(info);
    if (!GetLastInputInfo(ref info)) return -1;
    return (long)(uint)((uint)Environment.TickCount - info.dwTime);
  }
}
'@
$ms = [OmniPresence]::IdleMs()
$fg = Get-Process -Id ([OmniPresence]::ForegroundPid()) -ErrorAction SilentlyContinue
$out = [ordered]@{ idle_s = $(if ($ms -lt 0) { $null } else { [math]::Round($ms / 1000) }); session = (Get-Process -Id $PID).SessionId; user = $env:USERNAME
  foreground = $(if ($fg) { $fg.ProcessName } else { $null }); foreground_title = $(if ($fg) { $fg.MainWindowTitle } else { $null }) }
'OMNI-PRESENCE ' + ($out | ConvertTo-Json -Compress)
"""


def probe(profile: NodeProfile, backend: Any, *, timeout_s: int = 60) -> dict[str, Any]:
    """One node: idle seconds since the last input in the session desktop-node runs in."""
    entry: dict[str, Any] = {"node": profile.name, "idle_s": None}
    try:
        result = ops.run(profile, backend, None, inline=PROBE_PS, timeout_s=timeout_s, op="presence", label="presence probe")
    except Exception as error:  # an offline/refusing node must not sink the other probes
        return {**entry, "error": f"{type(error).__name__}: {error}"}
    entry["receipt"] = (result.get("paths") or {}).get("result")
    output = (result.get("paths") or {}).get("output")
    text = Path(output).read_text(encoding="utf-8", errors="replace") if output and Path(output).is_file() else ""
    for line in reversed(text.splitlines()):
        if line.startswith(MARKER):
            try:
                data = json.loads(line[len(MARKER):])
            except ValueError:
                break
            return {**entry, **{k: data.get(k) for k in ("idle_s", "session", "user", "foreground", "foreground_title")}}
    message = (result.get("first_error") or {}).get("message") or "没有拿到 presence 输出"
    return {**entry, "error": message}


def probe_all(profiles: list[NodeProfile], backend_for: Callable[[NodeProfile], Any]) -> list[dict[str, Any]]:
    if not profiles:
        return []
    with ThreadPoolExecutor(max_workers=len(profiles)) as pool:
        return list(pool.map(lambda p: probe(p, backend_for(p)), profiles))


def pick(probes: list[dict[str, Any]], *, active_window_s: int = ACTIVE_WINDOW_S) -> list[str]:
    """Nodes active within the window; else the single most recently used one."""
    known = [p for p in probes if isinstance(p.get("idle_s"), (int, float))]
    if not known:
        detail = "; ".join(f"{p['node']}: {p.get('error')}" for p in probes) or "没有配 share_root 的节点"
        raise RemoteRefused("presence", f"检测不到用户在哪台机器（{detail}）；用 --to <节点> 指定")
    active = sorted((p for p in known if p["idle_s"] <= active_window_s), key=lambda p: p["idle_s"])
    if active:
        return [p["node"] for p in active]
    return [min(known, key=lambda p: p["idle_s"])["node"]]
