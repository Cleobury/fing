"""Carry out a Plan with real mouse and keyboard input (Win32 mouse, `keyboard` for keys and text)."""

import ctypes
import time

import keyboard

from . import search
from .apps import launch
from .decide import Plan
from .desktop import focus_window

_user32 = ctypes.windll.user32
_BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010)}
_WHEEL = 0x0800
_NOTCH = 120


def _click(x: int, y: int, button: str = "left", count: int = 1) -> None:
    _user32.SetCursorPos(int(x), int(y))
    down, up = _BUTTONS[button]
    for _ in range(count):
        _user32.mouse_event(down, 0, 0, 0, 0)
        _user32.mouse_event(up, 0, 0, 0, 0)
        time.sleep(0.04)


def _scroll(notches: int) -> None:
    _user32.mouse_event(_WHEEL, 0, 0, ctypes.c_uint32((notches * _NOTCH) & 0xFFFFFFFF), 0)


def _move(x: int, y: int) -> None:
    """Move the pointer with real mouse-move input (not just SetCursorPos), so apps register a drag."""
    left, top = _user32.GetSystemMetrics(76), _user32.GetSystemMetrics(77)  # virtual screen origin
    width, height = _user32.GetSystemMetrics(78), _user32.GetSystemMetrics(79)
    nx = round((x - left) * 65535 / max(1, width - 1))
    ny = round((y - top) * 65535 / max(1, height - 1))
    _user32.mouse_event(0x0001 | 0x8000 | 0x4000, nx, ny, 0, 0)  # MOVE | ABSOLUTE | VIRTUALDESK


def drag(x0: int, y0: int, x1: int, y1: int, steps: int = 20) -> None:
    """Press at (x0, y0), glide to (x1, y1) and release there."""
    _move(x0, y0)
    time.sleep(0.05)
    _user32.mouse_event(_BUTTONS["left"][0], 0, 0, 0, 0)
    time.sleep(0.12)  # let the app notice the press before moving (drag threshold)
    for i in range(1, steps + 1):
        _move(round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps))
        time.sleep(0.015)
    time.sleep(0.12)  # hover over the drop target so it can highlight / accept
    _user32.mouse_event(_BUTTONS["left"][1], 0, 0, 0, 0)


def scroll_at(x: int, y: int, notches: int) -> None:
    """Scroll the window under (x, y): positive is up. Wheel input goes to whatever is under the pointer."""
    _user32.SetCursorPos(int(x), int(y))
    _scroll(notches)


def execute(p: Plan, search_hotkey: str | None = None) -> None:
    """Carry out `p`. `search_hotkey` opens PowerToys search for "search_pc"; None uses the Start menu."""
    if p.kind == "click":
        _click(*p.target.center)
    elif p.kind == "double_click":
        _click(*p.target.center, count=2)
    elif p.kind == "right_click":
        _click(*p.target.center, button="right")
    elif p.kind == "type_text":
        if p.target is not None:
            _click(*p.target.center)
            time.sleep(0.15)  # let the field take focus
        keyboard.write(p.text, delay=0.005)
        if p.submit:
            keyboard.send("enter")
    elif p.kind == "press_key":
        keyboard.send(p.key)
    elif p.kind == "scroll":
        _scroll(5 if p.scroll == "up" else -5)
    elif p.kind == "open_app":
        launch(p.app)
    elif p.kind == "switch_app":
        focus_window(p.window)
    elif p.kind == "drag":
        x0, y0 = p.target.center
        x1, y1 = p.drop.center if p.drop is not None else (x0 + p.drop_offset[0], y0 + p.drop_offset[1])
        drag(x0, y0, x1, y1)
    elif p.kind == "search_pc":
        search.open_and_type(p.text, search_hotkey)
    else:
        raise ValueError(f"Unknown action {p.kind}")
