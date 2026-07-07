"""Autostart guard for the progress service daemon.

Idempotent: if something is already listening on 127.0.0.1:8230 it exits without
spawning a duplicate. Otherwise it launches the internalized binary with a hidden
window (CREATE_NO_WINDOW | DETACHED_PROCESS), bound to loopback, with
PROGRESS_SERVICE_DATA_DIR pointing at omnicompany/data/services/whatnow.

Registered as the scheduled task `OmniProgressDaemon` (logon + periodic repeat),
mirroring the machine convention (OmniCronHeartbeat / OmniRemoteControl) because
ONLOGON alone is unreliable on this host.

The task action must wrap this script with the zero-window launcher, i.e.
  wscript.exe "omnicompany/scripts/run_hidden.vbs" "<venv python.exe>" "<this file>"
Bare `python.exe <this file>` flashes a console for the whole run (python.exe is
a console-subsystem binary and schtasks interactive tasks get a visible console);
wscript is GUI-subsystem and launches the child with window style 0, so no
console ever becomes visible (fixed 2026-07-06).
"""
import os
import socket
import subprocess
import sys

PORT = 8230
BIN = r"E:\WindowsWorkspace\omnicompany\services\_progress\progress_service\target\debug\progressd.exe"
CWD = r"E:\WindowsWorkspace\omnicompany\services\_progress\progress_service"
DATA_DIR = r"E:\WindowsWorkspace\omnicompany\data\services\whatnow"
LOG = os.path.join(DATA_DIR, "progressd.log")


def already_listening() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        return s.connect_ex(("127.0.0.1", PORT)) == 0
    finally:
        s.close()


def main() -> int:
    if already_listening():
        print("progress service already up on 127.0.0.1:%d - nothing to do" % PORT)
        return 0

    env = dict(os.environ)
    env["PROGRESS_SERVICE_DATA_DIR"] = DATA_DIR
    env["WHATNOW_DATA_DIR"] = DATA_DIR
    extra = os.path.join(os.environ.get("USERPROFILE", ""), ".multica", "bin")
    npm = os.path.join(os.environ.get("APPDATA", ""), "npm")
    env["PATH"] = extra + ";" + npm + ";" + env.get("PATH", "")

    CREATE_NO_WINDOW = 0x08000000
    DETACHED_PROCESS = 0x00000008

    os.makedirs(DATA_DIR, exist_ok=True)
    logf = open(LOG, "ab")
    p = subprocess.Popen(
        [BIN],
        cwd=CWD,
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
        close_fds=True,
    )
    print("launched progressd pid=%d data=%s" % (p.pid, DATA_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
