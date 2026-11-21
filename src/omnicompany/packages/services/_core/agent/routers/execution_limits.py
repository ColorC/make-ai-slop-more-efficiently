# [OMNI] origin=human domain=services/_core type=router agent=ai-ide-2b20d28d ts=2026-07-26T09:07:26Z
# [OMNI] summary="Shared execution controls for Agent shell tools."
# [OMNI] why="_core/agent 服务的路由节点"
# [OMNI] tags=_core,agent,routers,router
"""Shared execution controls for Agent shell tools.

Elapsed time is not evidence that a process is unhealthy.  A caller may set an
explicit total deadline, but the facility does not invent a 30/60 second limit.
Long calls stay observable through progress events and remain externally
abortable.
"""

from __future__ import annotations

import re
import os
from typing import Any, Literal, Mapping


FACILITY_TIMEOUT_MARKER = "[FACILITY_TIMEOUT]"
SHELL_POLICY_MARKER = "[SHELL_POLICY]"
ShellDialect = Literal["bash", "powershell"]


class ShellCommandPolicyError(ValueError):
    """The command is unsafe or belongs to a different shell dialect."""


_COMMAND_BOUNDARY = r"(?:^|(?:&&|\|\||[;|])\s*)"
_BLOCKED_STREAM_RE = re.compile(
    _COMMAND_BOUNDARY + r"(?:command\s+)?(?P<name>head|find|rg)(?:\.exe)?(?=\s|$)",
    re.IGNORECASE,
)
_POWERSHELL_RECURSIVE_LIST_RE = re.compile(
    r"(?:^|[;|]\s*)(?:Get-ChildItem|gci|dir|ls)\b[^;|]*\s-Recurse\b",
    re.IGNORECASE,
)
_BASH_RECURSIVE_LIST_RE = re.compile(
    _COMMAND_BOUNDARY + r"(?:command\s+)?ls\b[^;|]*(?:\s|^)-[A-Za-z]*R[A-Za-z]*\b",
    re.IGNORECASE,
)
_BASH_DIRECTORY_WRITE_RE = re.compile(
    _COMMAND_BOUNDARY + r"(?:command\s+)?(?:mkdir|install\s+-d)(?=\s|$)",
    re.IGNORECASE,
)
_POWERSHELL_DIRECTORY_WRITE_RE = re.compile(
    _COMMAND_BOUNDARY + r"(?:mkdir|md|New-Item)(?=\s|$)",
    re.IGNORECASE,
)
_BASH_TEXT_WRITE_RE = re.compile(
    _COMMAND_BOUNDARY + r"(?:tee|sed\s+-i)(?=\s|$)",
    re.IGNORECASE,
)
_POWERSHELL_TEXT_WRITE_RE = re.compile(
    _COMMAND_BOUNDARY
    + r"(?:Set-Content|Add-Content|Out-File|Export-Csv|Export-Clixml)(?=\s|$)",
    re.IGNORECASE,
)
_POWERSHELL_IN_BASH_RE = re.compile(
    _COMMAND_BOUNDARY
    + r"(?:Get-ChildItem|Get-Content|Select-Object|Select-String|Test-Path|"
    r"New-Item|Remove-Item|Set-Content|Add-Content|Out-File|Where-Object|"
    r"ForEach-Object|Resolve-Path)(?=\s|$)",
    re.IGNORECASE,
)
_BASH_IN_POWERSHELL_RE = re.compile(
    _COMMAND_BOUNDARY + r"(?:export|source|unset|chmod|chown)(?=\s|$)",
    re.IGNORECASE,
)
_NESTED_SHELL_RE = re.compile(
    _COMMAND_BOUNDARY
    + r"(?:&\s*)?(?:bash|sh|zsh|wsl|cmd|powershell|pwsh)(?:\.exe)?(?=\s|$)",
    re.IGNORECASE,
)


def validate_shell_command(command: str, *, dialect: ShellDialect) -> str:
    """Fail closed on shell-dialect, blocking, path-creation, and text-write mistakes.

    Agent shell tools are for invoking deterministic programs. File search,
    text reads/writes, and directory creation have dedicated tools with exact
    path contracts. Keeping those actions out of free-form shell avoids the
    recurring Windows failures where ``head``/``find`` wait forever, Bash
    syntax is sent to PowerShell, or a malformed path creates a literal
    directory with the wrong name.
    """

    if dialect not in {"bash", "powershell"}:
        raise ShellCommandPolicyError(f"unsupported shell dialect: {dialect!r}")
    if not isinstance(command, str) or not command.strip():
        raise ShellCommandPolicyError("command must be a non-empty string")
    if "\x00" in command:
        raise ShellCommandPolicyError("command contains a NUL byte")
    if "\r" in command or "\n" in command:
        raise ShellCommandPolicyError(
            "multi-line shell commands are refused; use one declared command or a reviewed script file"
        )

    normalized = command.strip()
    if _NESTED_SHELL_RE.search(normalized):
        raise ShellCommandPolicyError(
            "nested shell invocation is refused; call the tool for the intended dialect directly"
        )
    blocked_stream = _BLOCKED_STREAM_RE.search(normalized)
    if blocked_stream:
        name = blocked_stream.group("name").lower()
        replacement = (
            "the dedicated Glob tool"
            if name == "find"
            else "the dedicated Glob/Grep tools with an exact root and exclusions"
            if name == "rg"
            else "the dedicated Read tool or PowerShell Select-Object -First"
        )
        raise ShellCommandPolicyError(
            f"{name} is refused because it can wait on stdin or leave a long-running "
            f"Windows child process; use {replacement}"
        )

    if dialect == "bash":
        if _POWERSHELL_IN_BASH_RE.search(normalized) or "$env:" in normalized:
            raise ShellCommandPolicyError(
                "PowerShell syntax was sent to the Bash tool; use the PowerShell tool explicitly"
            )
        if _BASH_DIRECTORY_WRITE_RE.search(normalized):
            raise ShellCommandPolicyError(
                "shell directory creation is refused; declare the exact write path and use "
                "the guarded write_file/edit facility"
            )
        if _BASH_RECURSIVE_LIST_RE.search(normalized):
            raise ShellCommandPolicyError(
                "recursive ls is refused; use the dedicated Glob tool with an exact root"
            )
        if _BASH_TEXT_WRITE_RE.search(normalized) or re.search(r"(?<!&)>(?!&)", normalized):
            raise ShellCommandPolicyError(
                "shell text redirection is refused because quoting and encoding are not "
                "reviewable; use the UTF-8 guarded write_file/edit facility"
            )
    else:
        if (
            _BASH_IN_POWERSHELL_RE.search(normalized)
            or re.search(r"(?:^|\s)mkdir\s+-p(?:\s|$)", normalized, re.IGNORECASE)
            or re.search(r"(?:^|\s)rm\s+-rf(?:\s|$)", normalized, re.IGNORECASE)
            or "<<" in normalized
            or "&&" in normalized
            or "||" in normalized
        ):
            raise ShellCommandPolicyError(
                "Bash syntax was sent to the PowerShell tool; use PowerShell cmdlets and "
                "`; if ($?) { ... }`, or call the Bash tool explicitly"
            )
        if _POWERSHELL_DIRECTORY_WRITE_RE.search(normalized):
            raise ShellCommandPolicyError(
                "PowerShell directory creation is refused; declare the exact write path "
                "and use the guarded write_file/edit facility"
            )
        if _POWERSHELL_RECURSIVE_LIST_RE.search(normalized):
            raise ShellCommandPolicyError(
                "recursive Get-ChildItem is refused; use the dedicated Glob tool with "
                "an exact root and exclusions"
            )
        if _POWERSHELL_TEXT_WRITE_RE.search(normalized) or re.search(r">(?!&)", normalized):
            raise ShellCommandPolicyError(
                "PowerShell text-file output is refused because Windows PowerShell encoding "
                "is version-dependent; use the UTF-8 guarded write_file/edit facility"
            )

    return normalized


def utf8_subprocess_env(extra: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a subprocess environment with an explicit UTF-8 contract."""

    env = os.environ.copy()
    env.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def powershell_utf8_command(command: str) -> str:
    """Set both PowerShell and native-child streams to UTF-8 without a temp file."""

    return (
        "$omniUtf8 = New-Object System.Text.UTF8Encoding($false); "
        "[Console]::InputEncoding = $omniUtf8; "
        "[Console]::OutputEncoding = $omniUtf8; "
        "$OutputEncoding = $omniUtf8; "
        "$env:PYTHONUTF8 = '1'; "
        "$env:PYTHONIOENCODING = 'utf-8'; "
        f"& {{ {command} }}"
    )


def reject_decode_replacement(*streams: str) -> None:
    """Refuse apparently-corrupt output instead of treating it as valid evidence."""

    if any("\ufffd" in stream for stream in streams):
        raise ShellCommandPolicyError(
            "subprocess output contains Unicode replacement characters; "
            "stop and repair the producer/decoder UTF-8 contract"
        )


def resolve_shell_timeout(
    args: Mapping[str, Any],
    *,
    default: int | None = None,
) -> int | None:
    """Return an optional caller-selected total deadline.

    Missing/``None`` means no total deadline.  Health is then governed by
    streaming observability and the external abort signal rather than an
    arbitrary wall-clock constant.
    """

    raw_timeout = args.get("timeout_sec", default)
    if raw_timeout is None:
        return None
    try:
        timeout = int(raw_timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_sec must be an integer") from exc
    if timeout < 1:
        raise ValueError("timeout_sec must be at least 1")
    return timeout
