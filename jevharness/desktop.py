"""Win32 desktop helpers: cursor, foreground window, monitors, waiting for the screen to settle.

Kept free of WinRT/PortAudio imports so it can be imported before Whisper loads.
"""

import ctypes
import os
import re
import time
from ctypes import wintypes

import mss
import numpy as np

_user32 = ctypes.windll.user32


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def foreground_window() -> int:
    return _user32.GetForegroundWindow()


def foreground_window_title() -> str:
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(_user32.GetForegroundWindow(), buf, 512)
    return buf.value


def foreground_center() -> tuple[int, int] | None:
    r = wintypes.RECT()
    if not _user32.GetWindowRect(_user32.GetForegroundWindow(), ctypes.byref(r)) or r.right <= r.left:
        return None
    return (r.left + r.right) // 2, (r.top + r.bottom) // 2


_PRIMARY = object()


def monitor_at(monitors: list[dict], x: int, y: int, default=_PRIMARY) -> dict | None:
    """The mss monitor containing (x, y), else `default` (the primary, unless given)."""
    found = next(
        (m for m in monitors if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]),
        None)
    if found is not None:
        return found
    return next(m for m in monitors if m.get("is_primary")) if default is _PRIMARY else default


_kernel32 = ctypes.windll.kernel32
_dwmapi = ctypes.windll.dwmapi
_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}  # taskbar and desktop


def _window_exe(hwnd: int) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = _kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        ok = _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return os.path.splitext(os.path.basename(buf.value))[0] if ok else ""
    finally:
        _kernel32.CloseHandle(handle)


def app_windows() -> list[tuple[int, str, str]]:
    """Visible top-level app windows as (hwnd, title, exe name), most recently active first."""
    found: list[tuple[int, str, str]] = []

    def visit(hwnd, _):
        if not _user32.IsWindowVisible(hwnd) or _user32.GetWindow(hwnd, 4):  # GW_OWNER: skip dialogs/popups
            return True
        if _user32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW (e.g. our own overlay)
            return True
        cloaked = wintypes.DWORD()
        _dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))  # DWMWA_CLOAKED
        if cloaked.value:  # suspended UWP apps, windows on other virtual desktops
            return True
        cls = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, cls, 256)
        title = ctypes.create_unicode_buffer(512)
        _user32.GetWindowTextW(hwnd, title, 512)
        if title.value and cls.value not in _SHELL_CLASSES:
            found.append((hwnd, title.value, _window_exe(hwnd)))
        return True

    _user32.EnumWindows(_EnumWindowsProc(visit), 0)  # enumerates in z-order: most recent first
    return found


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def window_belongs_to(app_name: str, title: str, exe: str) -> bool:
    """Whether a window is the app's.

    Titles decide first: "<app>" or "… - <app>" / "… | <app>" (a version suffix like "Obsidian 1.13" is fine).
    Only a title without such a suffix falls back to the program name (steamwebhelper.exe for Steam,
    explorer.exe for File Explorer), so an app installed from a browser ("TickTick - Inbox - TickTick", run by
    brave.exe) doesn't count as the browser.
    """
    name, stem = _compact(app_name), _compact(exe)
    if not name:
        return False
    suffix = re.split(r" - | \| ", title)[-1]
    if _compact(title) == name or _compact(suffix).startswith(name):
        return True
    has_suffix = suffix != title
    exe_match = len(stem) >= 4 and (stem.startswith(name) or stem.endswith(name) or name.endswith(stem))
    return exe_match and not has_suffix


def find_app_window(app_name: str) -> int | None:
    return next((h for h, title, exe in app_windows() if window_belongs_to(app_name, title, exe)), None)


def foreground_is(app_name: str) -> bool:
    hwnd = _user32.GetForegroundWindow()
    return bool(hwnd) and window_belongs_to(app_name, foreground_window_title(), _window_exe(hwnd))


def focus_window(hwnd: int) -> None:
    """Bring a window to the front, restoring it if minimised. Windows only lets the foreground thread
    hand over focus, so briefly attach to that thread's input queue."""
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg_thread = _user32.GetWindowThreadProcessId(_user32.GetForegroundWindow(), None)
    me = _kernel32.GetCurrentThreadId()
    attached = fg_thread and fg_thread != me and _user32.AttachThreadInput(me, fg_thread, True)
    try:
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(me, fg_thread, False)


_user32.MonitorFromPoint.restype = wintypes.HMONITOR
_user32.MonitorFromWindow.restype = wintypes.HMONITOR
_user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


def monitor_bounds(x: int, y: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the monitor containing (x, y), or the nearest one."""
    mi = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
    _user32.GetMonitorInfoW(_user32.MonitorFromPoint(wintypes.POINT(x, y), 2), ctypes.byref(mi))
    m = mi.rcMonitor
    return m.left, m.top, m.right, m.bottom


def fullscreen_at(x: int, y: int) -> bool:
    """Whether the active window covers the whole monitor containing (x, y): exclusive fullscreen games,
    borderless "windowed fullscreen", fullscreen video. The desktop itself doesn't count."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    _user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in _SHELL_CLASSES:
        return False
    monitor = _user32.MonitorFromPoint(wintypes.POINT(x, y), 2)  # MONITOR_DEFAULTTONEAREST
    if _user32.MonitorFromWindow(hwnd, 2) != monitor:
        return False
    mi = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
    r = wintypes.RECT()
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(mi)) or not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return False
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


def wait_until_settled(timeout_s: float, cancelled=lambda: False, min_s: float = 0.25) -> None:
    """Block until the active window's monitor stops changing (page loaded, animation done) or timeout."""
    deadline = time.perf_counter() + timeout_s
    time.sleep(min_s)
    prev, stable = None, 0
    with mss.MSS() as sct:
        while time.perf_counter() < deadline and not cancelled():
            mon = monitor_at(sct.monitors[1:], *(foreground_center() or cursor_pos()))
            # Coarse grid of pixels: cheap, and ignores a blinking caret.
            img = np.frombuffer(sct.grab(mon).bgra, np.uint8).reshape(mon["height"], mon["width"], 4)[::24, ::24, :3]
            if prev is not None and prev.shape == img.shape and np.abs(img.astype(np.int16) - prev).mean() < 0.8:
                stable += 1
                if stable >= 2:
                    return
            else:
                stable = 0
            prev = img
            time.sleep(0.15)
