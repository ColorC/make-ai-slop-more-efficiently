# [OMNI] origin=ai-ide ts=2026-05-10 type=infra
# [OMNI] material_id="material:dashboard.ccdaemon.subprocess_hide.windows_no_console.py"
"""ccdaemon Windows 子进程隐藏空 console 窗口.

claude-agent-sdk 内部用 anyio.open_process spawn claude.cmd 子进程, 没传
creationflags. Windows 下默认创建可见 console 窗口, 用户 dogfood 期间桌面
堆十几个空黑窗口骚扰严重 (用户 2026-05-10 反馈).

修法: ccdaemon 进程启动时 monkey-patch anyio/asyncio/subprocess 以及 Windows
底层 _winapi.CreateProcess, 给所有 spawn 默认加 CREATE_NO_WINDOW
(0x08000000) creationflag. multiprocessing 的 Windows spawn 会绕过
subprocess.Popen 直调 _winapi.CreateProcess, 所以底层也必须覆盖。

副作用范围: ccdaemon 进程内 anyio.open_process 调用全部 hide window. SDK 是
我们已知唯一 anyio 子进程用户, 别处 (uvicorn 内部 reload watcher 不用 anyio
subprocess). 风险可控.

替代方案考虑过:
- 改 claude-agent-sdk 源码: 第三方包不动
- subprocess.Popen 全局 patch: anyio 不走 subprocess
- PR SDK 加 creationflags 字段: 长期主义但等不及
- pty 路线: 用户要 chat 不要 pty
"""

from __future__ import annotations

import asyncio
import subprocess
import sys


def install_subprocess_hide() -> None:
    """ccdaemon main.py 顶部调一次. 非 Windows 直接 noop."""
    if sys.platform != "win32":
        return

    try:
        import anyio
    except ImportError:
        return

    CREATE_NO_WINDOW = 0x08000000

    def _with_no_window(kwargs):
        if "creationflags" in kwargs and kwargs["creationflags"] is not None:
            kwargs["creationflags"] = kwargs["creationflags"] | CREATE_NO_WINDOW
        else:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        return kwargs

    if not getattr(anyio.open_process, "_omni_no_window_patched", False):
        _original_open_process = anyio.open_process
        _original_create_subprocess_exec = asyncio.create_subprocess_exec
        _original_create_subprocess_shell = asyncio.create_subprocess_shell
        _original_popen = subprocess.Popen

        async def _patched_open_process(*args, **kwargs):
            return await _original_open_process(*args, **_with_no_window(kwargs))

        async def _patched_create_subprocess_exec(*args, **kwargs):
            return await _original_create_subprocess_exec(*args, **_with_no_window(kwargs))

        async def _patched_create_subprocess_shell(*args, **kwargs):
            return await _original_create_subprocess_shell(*args, **_with_no_window(kwargs))

        class _PatchedPopen(_original_popen):  # type: ignore[misc, valid-type]
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **_with_no_window(kwargs))

        _patched_open_process._omni_no_window_patched = True  # type: ignore[attr-defined]
        _patched_create_subprocess_exec._omni_no_window_patched = True  # type: ignore[attr-defined]
        _patched_create_subprocess_shell._omni_no_window_patched = True  # type: ignore[attr-defined]
        _PatchedPopen._omni_no_window_patched = True  # type: ignore[attr-defined]
        anyio.open_process = _patched_open_process  # type: ignore[assignment]
        asyncio.create_subprocess_exec = _patched_create_subprocess_exec  # type: ignore[assignment]
        asyncio.create_subprocess_shell = _patched_create_subprocess_shell  # type: ignore[assignment]
        subprocess.Popen = _PatchedPopen  # type: ignore[assignment]

    # multiprocessing.popen_spawn_win32 does not use subprocess.Popen.  It calls
    # _winapi.CreateProcess directly with creation_flags=0, which created the
    # long-lived visible Python resource-monitor window.  Patch that shared
    # lowest-level entry as well so both current and future multiprocessing
    # contexts inherit the same no-console policy.
    try:
        import _winapi
    except ImportError:  # pragma: no cover - Windows-only module
        return
    if getattr(_winapi.CreateProcess, "_omni_no_window_patched", False):
        return
    _original_create_process = _winapi.CreateProcess

    def _patched_winapi_create_process(
        application_name,
        command_line,
        process_attributes,
        thread_attributes,
        inherit_handles,
        creation_flags,
        environment,
        current_directory,
        startup_info,
    ):
        return _original_create_process(
            application_name,
            command_line,
            process_attributes,
            thread_attributes,
            inherit_handles,
            int(creation_flags or 0) | CREATE_NO_WINDOW,
            environment,
            current_directory,
            startup_info,
        )

    _patched_winapi_create_process._omni_no_window_patched = True  # type: ignore[attr-defined]
    _winapi.CreateProcess = _patched_winapi_create_process  # type: ignore[assignment]
