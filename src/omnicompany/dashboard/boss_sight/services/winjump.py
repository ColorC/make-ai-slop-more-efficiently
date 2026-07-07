# [OMNI] origin=claude-code domain=dashboard/boss_sight/services ts=2026-06-21T00:00:00Z type=service
# [OMNI] material_id="material:dashboard.boss_sight.services.winjump.py"
"""窗口跳转 — 把某个 app 的主窗口激活到最前(非提权)。Windows only。

机制(2026-06-21 实测可行, VSCode→Codex 跳转 SWITCHED:True): AttachThreadInput 把当前前台
线程的输入队列挂到目标窗口线程, 绕过 Windows 的"前台锁", 再 SetForegroundWindow。非管理员、
不弹控制台窗口。给总控派发的 send_active_window 用: 路由判"发给某外部已活跃对话"时, 把那个
app 的窗口弄到最前(配合剪贴板, 用户粘贴即可)。

⚠ 注意: 这只到"窗口"粒度。VSCode 把多条 claude/codex 对话放在同一个窗口的 tab 里, 激活窗口
不切 tab —— 切到具体对话的精准跳转得走 Claude 扩展的本地 WS 通道(~/.claude/ide/<port>.lock),
那是另一个活, 见 dispatch 计划。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# 位置 → 候选进程名(不带 .exe, 大小写不敏感)
_LOC_PROC: dict[str, list[str]] = {
    "vscode": ["Code"],
    "vscode-powershell": ["Code"],
    "omni-web-in-vscode": ["Code"],
    "codex桌面": ["Codex"],
    "codex": ["Codex"],
    "chrome": ["chrome"],
    "omni-web": ["chrome", "msedge"],
    "poof-powershell": ["poof"],
}


def _is_win() -> bool:
    return sys.platform == "win32"


def _u32():
    u = ctypes.windll.user32
    u.GetForegroundWindow.restype = wintypes.HWND
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SetForegroundWindow.restype = wintypes.BOOL
    u.IsIconic.argtypes = [wintypes.HWND]
    u.IsIconic.restype = wintypes.BOOL
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.ShowWindow.restype = wintypes.BOOL
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.restype = ctypes.c_int
    u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    u.AttachThreadInput.restype = wintypes.BOOL
    u.EnumWindows.restype = wintypes.BOOL
    return u


def _enum_windows() -> list[tuple[int, int, str]]:
    """可见、有标题的顶层窗口: [(hwnd, pid, title)]。"""
    u = _u32()
    out: list[tuple[int, int, str]] = []
    CB = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lp):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        out.append((int(hwnd), int(pid.value), buf.value))
        return True

    u.EnumWindows(CB(_cb), 0)
    return out


def _proc_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001
        return ""


def activate_hwnd(hwnd: int) -> bool:
    u = _u32()
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = u.GetForegroundWindow()
    t1 = u.GetWindowThreadProcessId(fg, None) if fg else 0
    t2 = u.GetWindowThreadProcessId(hwnd, None)
    if t1 and t1 != t2:
        u.AttachThreadInput(t1, t2, True)
    ok = bool(u.SetForegroundWindow(hwnd))
    if t1 and t1 != t2:
        u.AttachThreadInput(t1, t2, False)
    return ok


def set_clipboard(text: str) -> bool:
    """用 CF_UNICODETEXT 正确写剪贴板(Windows `clip` 命令会把 UTF-8 中文搞乱, 这里走 ctypes)。"""
    if not _is_win():
        return False
    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
    u.SetClipboardData.restype = ctypes.c_void_p
    CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
    if not u.OpenClipboard(None):
        return False
    try:
        u.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        h = k.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = k.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        k.GlobalUnlock(h)
        u.SetClipboardData(CF_UNICODETEXT, h)
        return True
    finally:
        u.CloseClipboard()


def send_paste(delay_ms: int = 140) -> None:
    """给当前前台窗口发 Ctrl+V(把剪贴板粘进聚焦的输入框)。

    ⚠ 粘进的是"当前聚焦处"。case1 用于"发给已活跃对话, 复制到对话框里"(用户 2026-06-21 明确要)。
    只在 activate 确认目标窗口已到前台后才调, 降低粘错地方的概率; 真粘错用户可 Ctrl+Z。
    """
    import time
    time.sleep(delay_ms / 1000.0)
    u = ctypes.windll.user32
    VK_CONTROL, VK_V, KEYUP = 0x11, 0x56, 0x0002
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_V, 0, 0, 0)
    u.keybd_event(VK_V, 0, KEYUP, 0)
    u.keybd_event(VK_CONTROL, 0, KEYUP, 0)


def _procs_for(location: str | None) -> list[str]:
    if not location:
        return []
    if location in _LOC_PROC:
        return _LOC_PROC[location]
    for k, v in _LOC_PROC.items():  # 模糊: "vscode-..." 命中 "vscode"
        if k in location or location in k:
            return v
    return []


def activate_location(location: str, *, title_hint: str | None = None, paste: bool = False) -> dict:
    """把 location 对应 app 的主窗口激活到最前。title_hint 多窗口时优选标题含该串的;
    paste=True 则在确认窗口到前台后发 Ctrl+V(把剪贴板粘进聚焦输入框)。"""
    if not _is_win():
        return {"ok": False, "error": "非 Windows 平台"}
    procs = _procs_for(location)
    if not procs:
        return {"ok": False, "error": f"未知位置: {location}"}
    want = {p.lower() for p in procs}
    wins = _enum_windows()
    cands = [(h, p, t) for (h, p, t) in wins if _proc_name(p).lower().removesuffix(".exe") in want]
    if not cands:
        return {"ok": False, "error": f"没找到 {procs} 的窗口", "considered": len(wins)}
    h, p, t, matched = cands[0][0], cands[0][1], cands[0][2], "process"
    if title_hint:
        for hh, pp, tt in cands:
            if title_hint in tt:
                h, p, t, matched = hh, pp, tt, "title_hint"
                break
    ok = activate_hwnd(h)
    pasted = False
    if ok and paste:
        # 只在目标确实到了前台时才粘, 降低粘错地方
        u = _u32()
        if u.GetForegroundWindow() == h:
            send_paste()
            pasted = True
    return {"ok": ok, "title": t, "pid": p, "matched": matched, "pasted": pasted}


__all__ = ["activate_location", "activate_hwnd"]
