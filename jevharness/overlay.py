"""Floating status pill above the taskbar, plus a flash box around the element being acted on.

Both windows are click-through and never take focus, so they can't steal input from the
app being controlled.
"""

import ctypes
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

from .desktop import fullscreen_on_primary

_user32 = ctypes.windll.user32
_KEY = "#010203"  # transparent colour key
DEFAULT_BG = "#202124"
DEFAULT_FG = "#f1f3f4"
DEFAULT_DOTS = {
    "loading": "#9aa0a6",
    "idle": "#80868b",
    "listening": "#ff3b30",
    "thinking": "#fbbc04",
    "done": "#34a853",
    "warn": "#fbbc04",
    "error": "#ea4335",
    "question": "#8ab4f8",
}
_CHECK_BG = "#3c4043"
_CHECK_HOVER = "#34a853"
_SW_HIDE, _SW_SHOWNOACTIVATE = 0, 4
_HWND_TOPMOST = -1
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE = 0x1, 0x2, 0x10


def _mix(a: str, b: str, t: float) -> str:
    """Blend colour `a` towards `b` by t (0..1)."""
    (r1, g1, b1), (r2, g2, b2) = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (a, b))
    return "#%02x%02x%02x" % (round(r1 + (r2 - r1) * t), round(g1 + (g2 - g1) * t), round(b1 + (b2 - b1) * t))


def _work_area() -> tuple[int, int, int, int]:
    """Primary monitor's desktop area excluding the taskbar."""
    r = wintypes.RECT()
    _user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


def _make_passive(win: tk.Toplevel) -> int:
    win.update_idletasks()
    hwnd = _user32.GetParent(win.winfo_id())
    style = _user32.GetWindowLongW(hwnd, -20)
    # TRANSPARENT (click-through) | LAYERED | TOOLWINDOW (no taskbar button) | NOACTIVATE; drop APPWINDOW
    style = (style | 0x20 | 0x80000 | 0x80 | 0x08000000) & ~0x40000
    _user32.SetWindowLongW(hwnd, -20, style)
    return hwnd


def _base_window(root: tk.Tk) -> tk.Toplevel:
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-transparentcolor", _KEY)
    win.configure(bg=_KEY)
    return win


class Overlay:
    HEIGHT = 34
    MARGIN = 16  # gap above the taskbar

    def __init__(self, root: tk.Tk):
        self.root = root
        self.idle_text = "Jev ready · hold Right Ctrl"
        self.idle_state = "idle"  # "warn" while something needs fixing, e.g. no API key
        self.bg, self.fg, self.dots = DEFAULT_BG, DEFAULT_FG, dict(DEFAULT_DOTS)
        self._last = ("loading", "", False)  # what's showing, to redraw after a style change
        self.win = _base_window(root)
        self.canvas = tk.Canvas(self.win, bg=_KEY, highlightthickness=0, height=self.HEIGHT, width=200)
        self.canvas.pack()
        self.font = tkfont.Font(family="Segoe UI", size=10)
        self.hwnd = _make_passive(self.win)
        self.state = "loading"
        self._pulse_on = False
        self._revert_job = None
        self._clickable = False
        self._hidden = False  # hidden because a fullscreen app is in front
        self._text = ""
        self.rect = (0, 0, 0, 0)
        self._dim_listening = _mix(self.dots["listening"], self.bg, 0.55)
        self.on_check = None  # called (on the Tk thread) when the user clicks ✓ "it's done"
        c = self.canvas
        c.tag_bind("check", "<Button-1>", lambda _: self.on_check and self.on_check())
        c.tag_bind("check", "<Enter>", lambda _: (c.itemconfigure("check_bg", fill=_CHECK_HOVER), c.configure(cursor="hand2")))
        c.tag_bind("check", "<Leave>", lambda _: (c.itemconfigure("check_bg", fill=_CHECK_BG), c.configure(cursor="")))
        self.show("loading", "Loading speech model…")
        self._pulse()
        self._keep_on_top()

    DOT = 20  # size of the idle dot

    def show(self, state: str, text: str, hold_ms: int | None = None, check: bool = False) -> None:
        """Display a state; empty text shows just the dot. With hold_ms, return to idle afterwards.
        With check, add a ✓ button on the right for "it's done, stop" (calls on_check)."""
        if self._revert_job:
            self.root.after_cancel(self._revert_job)
            self._revert_job = None
        self.state = state
        self._text = text
        self._last = (state, text, check)
        if self._hidden and not (state == self.idle_state and text == self.idle_text):
            _user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)  # something to say: show even over fullscreen
            self._hidden = False
        text = text if len(text) <= 150 else text[:149] + "…"
        check = check and bool(text)
        c = self.canvas
        c.delete("all")
        if text:
            h = self.HEIGHT
            w = min(1400, self.font.measure(text) + 46) + (self.CHECK_W if check else 0)
            r = h // 2
            c.create_oval(0, 0, h, h, fill=self.bg, outline=self.bg)
            c.create_oval(w - h, 0, w, h, fill=self.bg, outline=self.bg)
            c.create_rectangle(r, 0, w - r, h, fill=self.bg, outline=self.bg)
            c.create_oval(14, r - 5, 24, r + 5, fill=self.dots[state], outline="", tags="dot")
            c.create_text(32, r, text=text, anchor="w", fill=self.fg, font=self.font)
            if check:
                cx, rr = w - r, r - 6
                c.create_oval(cx - rr, r - rr, cx + rr, r + rr, fill=_CHECK_BG, outline="", tags=("check", "check_bg"))
                c.create_text(cx, r, text="✓", fill="#ffffff", font=("Segoe UI", 11, "bold"), tags="check")
        else:
            h = w = self.DOT
            c.create_oval(0, 0, w, h, fill=self.bg, outline=self.bg)
            c.create_oval(5, 5, w - 5, h - 5, fill=self.dots[state], outline="", tags="dot")
        c.configure(width=w, height=h)
        left, _, right, bottom = _work_area()
        x = left + (right - left - w) // 2
        y = bottom - h - self.MARGIN - (self.HEIGHT - h) // 2  # dot sits where the pill's centre would be
        self.win.geometry(f"{w}x{h}{x:+d}{y:+d}")
        self.rect = (x, y, x + w, y + h)
        self._set_clickable(check)
        if hold_ms:
            self._revert_job = self.root.after(hold_ms, lambda: self.show(self.idle_state, self.idle_text))

    CHECK_W = 30  # extra pill width for the ✓ button

    def apply_style(self, bg: str, fg: str, opacity: int, dots: dict[str, str]) -> None:
        """Set the pill's colours and opacity (percent) and redraw what's showing."""
        self.bg = bg if bg.lower() != _KEY else "#010204"  # the colour key would make it invisible
        self.fg = fg
        self.dots = {**DEFAULT_DOTS, **{k: v for k, v in dots.items() if k in DEFAULT_DOTS}}
        self._dim_listening = _mix(self.dots["listening"], self.bg, 0.55)  # the "off" beat of the listening pulse
        self.win.attributes("-alpha", max(20, min(100, opacity)) / 100)
        state, text, check = self._last
        self.show(state, text, check=check)

    def _set_clickable(self, on: bool) -> None:
        """Take mouse clicks only while the ✓ is showing; otherwise clicks pass through to the window below.
        Either way the pill never takes keyboard focus (WS_EX_NOACTIVATE)."""
        if on == self._clickable:
            return
        style = _user32.GetWindowLongW(self.hwnd, -20)
        _user32.SetWindowLongW(self.hwnd, -20, (style & ~0x20) if on else (style | 0x20))
        self._clickable = on

    def _pulse(self) -> None:
        if self.state == "listening":
            self._pulse_on = not self._pulse_on
            self.canvas.itemconfigure("dot", fill=self.dots["listening"] if self._pulse_on else self._dim_listening)
        self.root.after(400, self._pulse)

    def _keep_on_top(self) -> None:
        """Stay above other windows (re-asserted every second, since the taskbar and Start menu can cover it),
        but get out of the way of fullscreen apps and games on the main screen while idle. While the user is
        actively using it (listening, working, asking, showing a result) it stays visible even then."""
        idle = self.state == self.idle_state and self._text == self.idle_text
        hide = idle and fullscreen_on_primary()
        if hide != self._hidden:
            _user32.ShowWindow(self.hwnd, _SW_HIDE if hide else _SW_SHOWNOACTIVATE)
            self._hidden = hide
        if not hide:
            _user32.SetWindowPos(self.hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)
        self.root.after(1000, self._keep_on_top)


class Highlight:
    PAD = 4

    def __init__(self, root: tk.Tk):
        self.root = root
        self.win = _base_window(root)
        self.canvas = tk.Canvas(self.win, bg=_KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.hwnd = _make_passive(self.win)
        _user32.ShowWindow(self.hwnd, _SW_HIDE)
        self._job = None

    BADGE = 30

    def flash(self, rect: tuple[int, int, int, int], ms: int, color: str = "#34a853") -> None:
        self.mark([rect], ms, color, numbered=False)

    def mark(self, rects: list[tuple[int, int, int, int]], ms: int | None, color: str = "#8ab4f8", numbered: bool = True) -> None:
        """Outline each rect (numbered 1, 2, … for questions). ms=None keeps them until hide()."""
        if self._job:
            self.root.after_cancel(self._job)
            self._job = None
        if not rects:
            return self.hide()
        p, bd = self.PAD, self.BADGE if numbered else 0
        x0 = min(r[0] for r in rects) - p - bd
        y0 = min(r[1] for r in rects) - p - bd // 2
        x1 = max(r[2] for r in rects) + p
        y1 = max(r[3] for r in rects) + p
        w, h = x1 - x0, y1 - y0
        self.win.geometry(f"{w}x{h}{x0:+d}{y0:+d}")
        c = self.canvas
        c.configure(width=w, height=h)
        c.delete("all")
        for i, (l, t, r, b) in enumerate(rects, 1):
            c.create_rectangle(l - x0 - p + 2, t - y0 - p + 2, r - x0 + p - 2, b - y0 + p - 2, outline=color, width=3)
            if numbered:
                bx, by = l - x0 - p - bd + 2, t - y0 - p - bd // 2 + 2
                c.create_oval(bx, by, bx + bd - 2, by + bd - 2, fill=color, outline="")
                c.create_text(bx + bd // 2 - 1, by + bd // 2 - 1, text=str(i), fill="#202124", font=("Segoe UI", 11, "bold"))
        _user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
        _user32.SetWindowPos(self.hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)
        if ms:
            self._job = self.root.after(ms, self.hide)

    def hide(self) -> None:
        self._job = None
        _user32.ShowWindow(self.hwnd, _SW_HIDE)
