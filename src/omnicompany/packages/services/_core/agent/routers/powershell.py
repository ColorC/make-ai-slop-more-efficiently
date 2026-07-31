# [OMNI] origin=claude-code domain=services/agent/routers ts=2026-05-04 type=router
# [OMNI] material_id="material:core.agent.routers.powershell.windows_native_executor.py"
"""PowerShellRouter — Windows 原生 powershell.exe 调用工具.

DevBashRouter 用 git bash (跨平台 unix 工具). 但有些 Windows 原生场景 (查 Service /
Get-WmiObject / 操作 .exe / npm.cmd / Test-Path 在 Windows 路径) git bash 不行
或绕路, 直接用 powershell 更顺.

跟 CC PowerShellTool 对齐 (build-src/src/tools/PowerShellTool/PowerShellTool.tsx):
  - getCachedPowerShellPath() → powershell.exe / pwsh.exe
  - -NoProfile -NonInteractive (不读 user profile, 不开 UI)
  - 输出 stdout + stderr + exit code 合并 (跟 DevBash 同 pattern)
  - 5MB 输出截断 + thread 边读边截 (OOM 安全)
  - timeout 默认 120s 上限 1200s

设计选择:
  - cwd 走 ToolContext.allowed_powershell_roots (跟 bash 各自 allowlist, 区分权限)
  - 命令黑名单 (Stop-Computer / Restart-Computer / Format-Volume / Remove-Item -Force -Recurse)
  - 平台限定: 仅 Windows (其他系统 raise)
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, ClassVar

from omnicompany.packages.services._core.agent.routers.single_tool import (
    SingleToolRouter,
    ToolContext,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


_DANGEROUS_PS_PATTERNS = [
    re.compile(r"\bStop-Computer\b", re.IGNORECASE),
    re.compile(r"\bRestart-Computer\b", re.IGNORECASE),
    re.compile(r"\bFormat-Volume\b", re.IGNORECASE),
    re.compile(r"\bClear-Disk\b", re.IGNORECASE),
    re.compile(r"\bRemove-Item\b.*?-(?:Force|Recurse).*?-(?:Force|Recurse)", re.IGNORECASE),
    re.compile(r"\bRemove-Item.*?[Cc]:\\(?!Users).+", re.IGNORECASE),  # rm 系统盘 (User 区例外)
    re.compile(r"\bSet-ExecutionPolicy\b.*?Unrestricted", re.IGNORECASE),
    re.compile(r"Invoke-Expression\s+\(.*Invoke-WebRequest", re.IGNORECASE),  # IEX(IWR ...)
    re.compile(r"\biex\s+\(.*iwr", re.IGNORECASE),
    re.compile(r"\bShutdown\.exe\b", re.IGNORECASE),
]


def _ps_danger(cmd: str) -> str | None:
    for pat in _DANGEROUS_PS_PATTERNS:
        if pat.search(cmd):
            return pat.pattern
    return None


class PowerShellRouter(SingleToolRouter):
    """Windows native powershell.exe — 跑 .ps1 / cmdlet / Windows 原生命令."""

    TOOL_NAME: ClassVar[str] = "powershell"
    DESCRIPTION: ClassVar[str] = (
        "Run a Windows PowerShell command (cmdlet 或 .ps1 脚本片段). "
        "用于 git bash 不擅长的 Windows 原生场景 — Get-Service / Get-Process / "
        "Get-WmiObject / Test-Path Windows 路径 / .cmd .exe 调用 / Service 控制 等.\n"
        "- cwd 必须在 ctx.allowed_powershell_roots 内.\n"
        "- 危险 cmdlet (Stop-Computer / Format-Volume / IEX(IWR) 等) 拒.\n"
        "- 默认 -NonInteractive -NoProfile (不读 user profile, 不开 UI).\n"
        "- stdout + stderr + exit code 合并返回, 各 5MB 截断防 OOM.\n"
        "- timeout 默认 120s, 上限 1200s. 长流程改 nohup 模式 (Start-Process -NoNewWindow).\n"
        "- Linux/Mac 上调用此工具会报错 (Windows-only)."
    )
    INPUT_SCHEMA: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "PowerShell command or script snippet to execute.",
            },
            "cwd": {
                "type": "string",
                "description": "Absolute working directory (must be within allowed_powershell_roots).",
            },
            "timeout_sec": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1200,
                "description": "Timeout seconds. Default 120, max 1200.",
            },
        },
        "required": ["command", "cwd"],
    }
    IS_CONCURRENCY_SAFE: ClassVar[bool] = False
    IS_READONLY: ClassVar[bool] = False

    def _execute(self, args: dict, ctx: ToolContext) -> str:
        if os.name != "nt":
            raise ToolExecutionError(
                "PowerShellRouter is Windows-only (os.name != 'nt'). "
                "Use bash on Linux/Mac."
            )
        cmd = (args.get("command") or "").strip()
        raw_cwd = (args.get("cwd") or "").strip()
        timeout = max(1, min(int(args.get("timeout_sec", 120)), 1200))
        if not cmd:
            raise ToolExecutionError("command is required")
        if not raw_cwd:
            raise ToolExecutionError("cwd is required (absolute path within allowed_powershell_roots)")

        # 黑名单
        danger = _ps_danger(cmd)
        if danger:
            raise ToolExecutionError(
                f"powershell REFUSED: command matches dangerous regex `{danger}`. "
                f"Destructive cmdlets (Stop-Computer / Format-Volume / IEX(IWR) / etc.) refused."
            )

        # cwd 白名单
        allowed_roots = getattr(ctx, "allowed_powershell_roots", None) or ()
        if not allowed_roots:
            raise ToolExecutionError(
                "powershell REFUSED: no allowed_powershell_roots in tool context. "
                "Worker.build_tool_context() must declare allowlist."
            )
        try:
            cwd_abs = Path(raw_cwd).resolve()
        except Exception as e:
            raise ToolExecutionError(f"can't resolve cwd {raw_cwd!r}: {e}")
        if not cwd_abs.is_dir():
            raise ToolExecutionError(f"cwd does not exist or is not a dir: {cwd_abs}")
        ok = False
        roots_resolved = []
        for r in allowed_roots:
            try:
                rr = Path(r).resolve()
                roots_resolved.append(str(rr))
                cwd_abs.relative_to(rr)
                ok = True
                break
            except (ValueError, Exception):
                continue
        if not ok:
            listing = "\n  - ".join(roots_resolved)
            raise ToolExecutionError(
                f"powershell REFUSED: cwd {cwd_abs} outside allowed_powershell_roots.\n"
                f"Allowed:\n  - {listing}"
            )

        # spawn powershell.exe — prefer pwsh (PS7+) if available, fallback to powershell.exe (PS5)
        import shutil as _shutil
        ps_path = (
            _shutil.which("pwsh")
            or _shutil.which("pwsh.exe")
            or _shutil.which("powershell")
            or _shutil.which("powershell.exe")
            or "powershell.exe"
        )
        try:
            proc = subprocess.Popen(
                [ps_path, "-NoProfile", "-NonInteractive", "-Command", cmd],
                cwd=str(cwd_abs),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except Exception as e:
            raise ToolExecutionError(f"PowerShell Popen failed: {e}")

        # 边读边截 (跟 DevBashRouter 同 pattern)
        _MAX_STREAM = 5 * 1024 * 1024

        def _capped_read(stream: Any, dest: list[Any]) -> None:
            chunks: list[str] = []
            total = 0
            truncated = False
            try:
                while True:
                    try:
                        chunk = stream.read(65536)
                    except (OSError, ValueError):
                        break
                    if not chunk:
                        break
                    if total < _MAX_STREAM:
                        allowed = _MAX_STREAM - total
                        if len(chunk) <= allowed:
                            chunks.append(chunk)
                            total += len(chunk)
                        else:
                            chunks.append(chunk[:allowed])
                            total += allowed
                            truncated = True
                    else:
                        truncated = True
            finally:
                dest.append(("".join(chunks), total, truncated))

        out_buf: list[Any] = []
        err_buf: list[Any] = []
        t_out = threading.Thread(target=_capped_read, args=(proc.stdout, out_buf), daemon=True)
        t_err = threading.Thread(target=_capped_read, args=(proc.stderr, err_buf), daemon=True)
        t_out.start(); t_err.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try: proc.kill()
                except Exception: pass
            rc = -9
            timed_out = True
        t_out.join(timeout=2); t_err.join(timeout=2)
        stdout, sout_total, sout_trunc = out_buf[0] if out_buf else ("", 0, False)
        stderr, serr_total, serr_trunc = err_buf[0] if err_buf else ("", 0, False)
        stdout = stdout.rstrip("\n"); stderr = stderr.rstrip("\n")
        if sout_trunc:
            stdout += f"\n\n[TRUNCATED · stdout 5 MiB cap · 实读 {sout_total} bytes]"
        if serr_trunc:
            stderr += f"\n\n[TRUNCATED · stderr 5 MiB cap · 实读 {serr_total} bytes]"

        if timed_out:
            raise ToolExecutionError(
                f"powershell TIMEOUT after {timeout}s (killed). Command: `{cmd[:120]}...`\n"
                f"stdout: {stdout[:300]}\nstderr: {stderr[:300]}\n"
                f"For long-running PS, use `Start-Process -NoNewWindow ...` background pattern."
            )

        parts = []
        if stdout: parts.append(stdout)
        if stderr: parts.append(f"[stderr]\n{stderr}")
        parts.append(f"[exit={rc}]")
        return "\n".join(parts)


__all__ = ["PowerShellRouter"]
