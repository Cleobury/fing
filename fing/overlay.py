"""Floating status pill above the taskbar, plus a flash box around the element being acted on.

Both windows are click-through and never take focus, so they can't steal input from the
app being controlled.
"""

import ctypes
import math
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

from .desktop import fullscreen_at, monitor_bounds

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
    "yolo": "#c77dff",  # idle in YOLO mode
}
_MOVE_TEXT = "Drag to move · double-click to drop it here"
_CHECK_BG = "#3c4043"
_CHECK_HOVER = "#34a853"
_SW_HIDE, _SW_SHOWNOACTIVATE = 0, 4
_HWND_TOPMOST = -1
_SWP_NOSIZE, _SWP_NOMOVE, _SWP_NOACTIVATE = 0x1, 0x2, 0x10


def _mix(a: str, b: str, t: float) -> str:
    """Blend colour `a` towards `b` by t (0..1)."""
    (r1, g1, b1), (r2, g2, b2) = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (a, b))
    return "#%02x%02x%02x" % (round(r1 + (r2 - r1) * t), round(g1 + (g2 - g1) * t), round(b1 + (b2 - b1) * t))


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_in(t: float) -> float:
    return t ** 3


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


POSITIONS = ("bottom-centre", "bottom-left", "bottom-right", "top-centre", "top-left", "top-right", "custom")
# How the pill grows from where it's placed: "dot" keeps the dot fixed and grows away from it (for a corner);
# "centre" keeps the whole pill centred on the spot, so it grows both ways (for the middle of the screen).
ANCHORS = ("dot", "centre")


class Overlay:
    HEIGHT = 34
    MARGIN = 16  # gap from the screen edge / taskbar

    def __init__(self, root: tk.Tk):
        self.root = root
        self.idle_text = "Fing ready · hold Right Ctrl"
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
        self._tick = 0  # frames of the animation loop (see _animate)
        self._shake_job = None
        self._ring_job = None  # the mic open/close ring animation in progress
        self._dot_at = (self.DOT // 2, self.DOT // 2, 9)  # the dot's centre, and the largest ring that fits around it
        self._revert_job = None
        self._clickable = False
        self._hidden = False  # hidden because a fullscreen app is in front
        self._text = ""
        self._shown = ""  # the text on the pill right now: behind _text while it's being typed in or deleted out
        self._type_job = None
        self._shake_typed = False  # shake once the text being typed is all in (see shake())
        self.always_on_top = False  # stay showing over fullscreen apps too, even while idle (see _keep_on_top)
        self.typing = True  # type the text in and delete it out, rather than swapping it at once
        self.rect = (0, 0, 0, 0)
        self._dim_listening = _mix(self.dots["listening"], self.bg, 0.55)
        self.on_check = None  # called (on the Tk thread) when the user clicks ✓ "it's done"
        # Where it sits: a preset on the main screen, or "custom" = centred on (custom_x, custom_y) anywhere.
        self.position = "bottom-centre"
        self.custom_x = self.custom_y = 0
        self.anchor = "dot"  # see ANCHORS
        self._moving = None  # while being dragged: the callback for the new (x, y); see start_move()
        self._align = "left"  # which way the text currently grows from the dot; see _anchor()
        self._drag_from = (0, 0)
        c = self.canvas
        c.tag_bind("check", "<Button-1>", lambda _: self.on_check and self.on_check())
        c.tag_bind("check", "<Enter>", lambda _: (c.itemconfigure("check_bg", fill=_CHECK_HOVER), c.configure(cursor="hand2")))
        c.tag_bind("check", "<Leave>", lambda _: (c.itemconfigure("check_bg", fill=_CHECK_BG), c.configure(cursor="")))
        self.show("loading", "Loading speech model…")
        self._animate()
        self._keep_on_top()

    DOT = 20  # size of the idle dot

    @property
    def text(self) -> str:
        return self._text

    def show(self, state: str, text: str, hold_ms: int | None = None, check: bool = False) -> None:
        """Display a state; empty text shows just the dot. With hold_ms, return to idle afterwards.
        With check, add a ✓ button on the right for "it's done, stop" (calls on_check)."""
        if self._moving and not text.startswith(_MOVE_TEXT):
            self._last = (state, text, check)  # shown once the move is finished
            return
        if self._revert_job:
            self.root.after_cancel(self._revert_job)
            self._revert_job = None
        self.state = state
        self._text = text
        self._shake_typed = False
        if not self._moving:
            self._last = (state, text, check)
        if self._hidden and not (state == self.idle_state and text == self.idle_text):
            _user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)  # something to say: show even over fullscreen
            self._hidden = False
        text = text if len(text) <= 1000 else text[:999] + "…"
        check = check and bool(text)
        if self._type_job:
            self.root.after_cancel(self._type_job)
            self._type_job = None
        if self.typing and not self._moving and text != self._shown:
            self._type(state, text, check, hold_ms, len(self._shown))
            return
        self._render(state, text, check)
        if hold_ms:
            self._revert_job = self.root.after(hold_ms, lambda: self.show(self.idle_state, self.idle_text))

    TYPE_MS = 20  # frame time of the typing animation
    TYPE_FRAMES, DELETE_FRAMES = 30, 15  # at most this many frames to type a text in / delete one out

    def _type(self, state: str, text: str, check: bool, hold_ms: int | None, deleting_from: int) -> None:
        """One frame of typing: first delete what's showing back to where it differs from `text` (a few letters
        a frame for long ones, so it never drags on), then type the rest in. The ✓ appears once it's all typed,
        and hold_ms counts from then."""
        self._type_job = None
        shown = self._shown
        if text.startswith(shown):
            shown = text[:len(shown) + max(1, math.ceil(len(text) / self.TYPE_FRAMES))]
        else:
            keep = 0
            while keep < min(len(shown), len(text)) and shown[keep] == text[keep]:
                keep += 1
            shown = shown[:max(keep, len(shown) - max(1, math.ceil(deleting_from / self.DELETE_FRAMES)))]
        self._render(state, shown, check and shown == text)
        if shown != text:
            self._type_job = self.root.after(self.TYPE_MS, lambda: self._type(state, text, check, hold_ms, deleting_from))
            return
        if self._shake_typed:
            self._shake_typed = False
            self.shake()
        if hold_ms:
            self._revert_job = self.root.after(hold_ms, lambda: self.show(self.idle_state, self.idle_text))

    def _render(self, state: str, text: str, check: bool) -> None:
        """Draw the pill showing `text` (empty: just the dot) and put it in place."""
        self._shown = text
        ax, ay, align = self._anchor()
        self._align = align
        # On the right of the screen the pill is mirrored: dot on the right, text growing to its left, ✓ at the left.
        mirrored = align == "right"
        c = self.canvas
        c.delete("all")
        if text:
            w, h, dot_y = self._draw_text(text, state, check, mirrored, ax, ay)
        else:
            h = w = self.DOT
            dot_y = h // 2
            c.create_oval(0, 0, w, h, fill=self.bg, outline=self.bg)
            c.create_oval(5, 5, w - 5, h - 5, fill=self.dots[state], outline="", tags="dot")
            self._dot_at = (w // 2, h // 2, w // 2 - 1)
        c.configure(width=w, height=h)
        if self._shake_job:
            self.root.after_cancel(self._shake_job)
            self._shake_job = None
        x, y = self._place(w, dot_y, ax, ay, align)
        # Always "+x+y", even when negative (a monitor left of or above the main one): Tk reads "-x" as the
        # distance from the right edge of the screen.
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.rect = (x, y, x + w, y + h)
        self._set_clickable(check or bool(self._moving))

    CHECK_W = 30  # extra pill width for the ✓ button
    PAD_X = 32  # text inset from the dot end (the dot sits in the first 32 px)
    PAD_Y = 8  # extra top/bottom padding on multi-line boxes

    def _draw_text(self, text: str, state: str, check: bool, mirrored: bool, ax: int, ay: int) -> tuple[int, int, int]:
        """Draw a pill (one line) or a rounded box (several lines) for `text`; returns (width, height, dot_y).

        Lines are separated by "\n" and wrapped to the room left between the dot and the far edge of the screen,
        so the box is always wide enough and never runs off it. Rows read top to bottom; the dot stays on its
        anchor, on the first row (or the last near the bottom of the screen), so the box never goes under the
        taskbar.
        """
        c = self.canvas
        left, top, right, bottom = monitor_bounds(ax, ay)
        if self.anchor == "centre":  # as wide as fits centred on the spot, but at least half the monitor (_place nudges it on)
            room = min(right - left - 2 * self.MARGIN, max(2 * (min(ax - left, right - ax) - self.MARGIN), (right - left) // 2))
        elif mirrored:
            room = ax + self._DOT_X - left - self.MARGIN
        else:
            room = right - self.MARGIN - (ax - self._DOT_X)
        extra = self.CHECK_W if check else 0
        max_text = max(200, room - self.PAD_X - 14 - extra)
        first, *rest = text.split("\n")
        header = self._wrap(first, max_text)
        body = [row for line in rest for row in self._wrap(line, max_text)]
        rows = header + body  # always read top to bottom
        line_h = self.font.metrics("linespace") + 4
        single = len(rows) == 1
        h = self.HEIGHT if single else len(rows) * line_h + 2 * self.PAD_Y
        w = max(self.font.measure(row) for row in rows) + self.PAD_X + 14 + extra
        centre = (lambda i: h // 2) if single else (lambda i: self.PAD_Y + i * line_h + line_h // 2)
        # The dot (the fixed anchor) sits on the first row, or on the last one near the bottom of the screen,
        # so the box grows away from the nearer edge and never goes under the taskbar.
        dot_row = len(rows) - 1 if ay > (top + bottom) / 2 else 0
        dot_y = centre(dot_row)

        radius = h // 2 if single else 14
        self._round_rect(0, 0, w, h, radius)
        dot_x = w - self._DOT_X if mirrored else self._DOT_X
        c.create_oval(dot_x - 5, dot_y - 5, dot_x + 5, dot_y + 5, fill=self.dots[state], outline="", tags="dot")
        self._dot_at = (dot_x, dot_y, min(15, dot_y - 1, h - dot_y - 1))
        text_x = 14 + extra if mirrored else self.PAD_X  # mirrored: dot on the right, text still left-aligned
        for i, row in enumerate(rows):
            c.create_text(text_x, centre(i), text=row, anchor="w", fill=self.fg, font=self.font)
        if check:
            half = self.HEIGHT // 2
            cx, rr = (half if mirrored else w - half), half - 6
            c.create_oval(cx - rr, dot_y - rr, cx + rr, dot_y + rr, fill=_CHECK_BG, outline="", tags=("check", "check_bg"))
            c.create_text(cx, dot_y, text="✓", fill="#ffffff", font=("Segoe UI", 11, "bold"), tags="check")
        return w, h, dot_y

    def _wrap(self, line: str, max_w: int) -> list[str]:
        """Word-wrap one line to max_w pixels (very long words are split)."""
        rows, row = [], ""
        for word in line.split(" "):
            candidate = f"{row} {word}" if row else word
            if self.font.measure(candidate) <= max_w:
                row = candidate
                continue
            if row:
                rows.append(row)
            while self.font.measure(word) > max_w:  # a single word wider than the box
                cut = max(1, int(len(word) * max_w / self.font.measure(word)) - 1)
                rows.append(word[:cut])
                word = word[cut:]
            row = word
        rows.append(row)
        return rows

    def _round_rect(self, x0: int, y0: int, x1: int, y1: int, r: int) -> None:
        c, fill = self.canvas, self.bg
        r = min(r, (x1 - x0) // 2, (y1 - y0) // 2)
        c.create_rectangle(x0 + r, y0, x1 - r, y1, fill=fill, outline=fill)
        c.create_rectangle(x0, y0 + r, x1, y1 - r, fill=fill, outline=fill)
        for cx, cy in ((x0, y0), (x1 - 2 * r, y0), (x0, y1 - 2 * r), (x1 - 2 * r, y1 - 2 * r)):
            c.create_oval(cx, cy, cx + 2 * r, cy + 2 * r, fill=fill, outline=fill)

    _DOT_X = 19  # the dot's centre, measured from the pill's dot end

    def _anchor(self) -> tuple[int, int, str]:
        """Where the dot sits (it never moves), and which way the text grows from it: "left" means the dot is
        on the left of the pill and the text grows rightwards; "right" is mirrored, growing leftwards. A dragged
        ("custom") position grows away from the nearer edge of its monitor, so it never runs off the screen.
        With the "centre" anchor it's the spot the pill is centred on instead (see _place)."""
        if self.position == "custom":
            ax, ay = self.custom_x, self.custom_y
            left, _, right, _ = monitor_bounds(ax, ay)
            return ax, ay, "right" if ax > (left + right) / 2 else "left"
        left, top, right, bottom = _work_area()
        vertical, horizontal = self.position.split("-")
        ay = top + self.MARGIN + self.HEIGHT // 2 if vertical == "top" else bottom - self.MARGIN - self.HEIGHT // 2
        if horizontal == "left":
            return left + self.MARGIN + self._DOT_X, ay, "left"
        if horizontal == "right":
            return right - self.MARGIN - self._DOT_X, ay, "right"
        return (left + right) // 2, ay, "left"

    def _place(self, w: int, dot_y: int, ax: int, ay: int, align: str) -> tuple[int, int]:
        """Top-left corner for a box of width w (or the idle dot) whose dot, dot_y from its top, lands exactly
        on the anchor. Centred, the box is centred on the anchor instead, nudged along to stay on its monitor (so
        near a corner it lines up with the corner, as with the "dot" anchor)."""
        y = ay - dot_y
        if w == self.DOT:
            return ax - w // 2, y
        if self.anchor == "centre":
            left, _, right, _ = monitor_bounds(ax, ay)
            return max(left + self.MARGIN, min(ax - w // 2, right - self.MARGIN - w)), y
        return (ax - self._DOT_X if align == "left" else ax - (w - self._DOT_X)), y

    def set_position(self, position: str, x: int = 0, y: int = 0, anchor: str = "dot") -> None:
        if self._moving:
            return  # being dragged: the drag decides where it goes
        self.position = position if position in POSITIONS else "bottom-centre"
        self.anchor = anchor if anchor in ANCHORS else "dot"
        self.custom_x, self.custom_y = x, y
        state, text, check = self._last
        self.show(state, text, check=check)

    def start_move(self, on_done) -> None:
        """Let the user drag the pill anywhere (any monitor); a double-click drops it there and calls
        on_done(x, y) with its new centre. It takes clicks while being moved, but still never takes focus."""
        self.custom_x, self.custom_y, _ = self._anchor()  # start from where it is now
        self.position = "custom"
        self._moving = on_done
        c = self.canvas
        c.bind("<ButtonPress-1>", self._drag_start)
        c.bind("<B1-Motion>", self._drag)
        c.bind("<Double-Button-1>", lambda _: self.finish_move())
        c.configure(cursor="fleur")
        self.show("question", _MOVE_TEXT)

    def _drag_start(self, e) -> None:
        self._drag_from = (e.x_root - self.custom_x, e.y_root - self.custom_y)

    def _drag(self, e) -> None:
        self.custom_x, self.custom_y = e.x_root - self._drag_from[0], e.y_root - self._drag_from[1]
        ax, ay, align = self._anchor()
        if align != self._align:
            self.show("question", _MOVE_TEXT)  # crossed the middle of the screen: flip the layout
            return
        w, h = self.rect[2] - self.rect[0], self.rect[3] - self.rect[1]
        x, y = self._place(w, h // 2, ax, ay, align)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self.rect = (x, y, x + w, y + h)

    def finish_move(self, keep: bool = True) -> None:
        """Drop the pill where it is (calling the start_move callback), or with keep=False abandon the move."""
        if self._moving is None:
            return
        on_done, self._moving = (self._moving if keep else None), None
        c = self.canvas
        for seq in ("<ButtonPress-1>", "<B1-Motion>", "<Double-Button-1>"):
            c.unbind(seq)
        c.configure(cursor="")
        state, text, check = self._last
        self.show(state, text, check=check)
        if on_done:
            on_done(self.custom_x, self.custom_y)

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

    RING_MS = 25  # the mic open/close animation's frame time

    def mic_opened(self) -> None:
        """The mic just opened (whatever opened it): the dot pops and a soft glow and two rings ripple out of it."""
        self._ring(opening=True)

    def mic_closed(self) -> None:
        """The mic just closed: two rings fold back into the dot, which settles with a small bounce."""
        self._ring(opening=False)

    # Each layer: (first frame, frames it lasts). Opening: glow, then two rings a beat apart, while the dot pops.
    _OPEN = {"glow": (0, 14), "ring1": (0, 18), "ring2": (6, 18), "pop": (0, 10)}
    _CLOSE = {"ring1": (0, 14), "ring2": (4, 14), "pop": (16, 8)}

    def _ring(self, opening: bool, frame: int = 0) -> None:
        if frame == 0 and self._ring_job:
            self.root.after_cancel(self._ring_job)
        self._ring_job = None
        c = self.canvas
        c.delete("ring")
        x, y, biggest = self._dot_at
        layers = self._OPEN if opening else self._CLOSE
        total = max(a + n for a, n in layers.values())
        if frame >= total:
            c.coords("dot", x - 5, y - 5, x + 5, y + 5)
            return
        own = self.dots["listening"] if opening else self.dots.get(self.state, self.dots["idle"])

        def progress(layer: str) -> float | None:
            first, n = layers.get(layer, (0, 0))
            return (frame - first) / (n - 1) if n and first <= frame < first + n else None

        if (t := progress("glow")) is not None:  # a soft halo that swells and fades
            r = 6 + (biggest * 0.8 - 6) * _ease_out(t)
            c.create_oval(x - r, y - r, x + r, y + r, fill=_mix(own, self.bg, 0.65 + 0.35 * t), outline="", tags="ring")
        for name, width in (("ring1", 3.0), ("ring2", 2.0)):
            if (t := progress(name)) is None:
                continue
            if opening:  # out from the dot, thinning and fading
                r = 6 + (biggest - 6) * _ease_out(t)
                colour, w = _mix(own, self.bg, t), max(1.0, width * (1 - t))
            else:  # in from the edge, brightening as it lands
                r = biggest - (biggest - 6) * _ease_in(t)
                colour, w = _mix(own, self.bg, 1 - t), 1.0 + (width - 1) * t
            c.create_oval(x - r, y - r, x + r, y + r, outline=colour, width=w, tags="ring")
        if (t := progress("pop")) is not None:  # the dot swells a little and settles back
            r = 5 + 2.5 * math.sin(math.pi * t)
            c.coords("dot", x - r, y - r, x + r, y + r)
        # Under the dot and the text, over the pill's background.
        c.tag_lower("ring", "dot")
        self._ring_job = self.root.after(self.RING_MS, lambda: self._ring(opening, frame + 1))

    def dot_screen(self) -> tuple[int, int]:
        """Where the dot is on the screen (for the hand animations that come out of it)."""
        x, y, _ = self._dot_at
        return self.rect[0] + int(x), self.rect[1] + int(y)

    def shake(self) -> None:
        """Shake the pill side to side, like a head saying no (something went wrong)."""
        if self._moving:
            return
        if self._type_job:  # the pill is still changing size: shake it once the text is in
            self._shake_typed = True
            return
        x0, y0, x1, y1 = self.rect
        offsets = (7, -7, 6, -6, 4, -4, 2, -1, 0)

        def step(i: int) -> None:
            self._shake_job = None
            if i >= len(offsets) or self.rect != (x0, y0, x1, y1):
                return
            self.win.geometry(f"+{x0 + offsets[i]:d}+{y0:d}")
            self._shake_job = self.root.after(35, lambda: step(i + 1))

        if self._shake_job:
            self.root.after_cancel(self._shake_job)
        step(0)

    ANIM_MS = 40  # the animation loop's frame time

    def _animate(self) -> None:
        """The dot's running animations: it pulses while listening (every 10th frame flips it) and, while
        working, four sparks chase each other round it."""
        self._tick += 1
        c = self.canvas
        if self.state == "listening" and self._tick % 10 == 0:
            self._pulse_on = not self._pulse_on
            c.itemconfigure("dot", fill=self.dots["listening"] if self._pulse_on else self._dim_listening)
        c.delete("orbit")
        if self.state == "thinking" and not self._ring_job:
            x, y, biggest = self._dot_at
            r = max(7.0, min(11.0, biggest - 3))
            own = self.dots["thinking"]
            for k in range(4):
                a = self._tick * 0.3 - k * 0.75
                size = 2.8 - 0.55 * k
                px, py = x + r * math.cos(a), y + r * math.sin(a)
                c.create_oval(px - size, py - size, px + size, py + size, fill=_mix(own, self.bg, 0.22 * k), outline="",
                              tags="orbit")
        self.root.after(self.ANIM_MS, self._animate)

    def _keep_on_top(self) -> None:
        """Stay above other windows (re-asserted every second, since the taskbar and Start menu can cover it),
        but get out of the way of a fullscreen app or game on its monitor while idle. While the user is
        actively using it (listening, working, asking, showing a result, being moved) it stays visible even then.
        With always_on_top it never hides, and is put back on top four times a second."""
        idle = self.state == self.idle_state and self._text == self.idle_text and not self._moving
        l, t, r, b = self.rect
        hide = idle and not self.always_on_top and fullscreen_at((l + r) // 2, (t + b) // 2)
        if hide != self._hidden:
            _user32.ShowWindow(self.hwnd, _SW_HIDE if hide else _SW_SHOWNOACTIVATE)
            self._hidden = hide
        if not hide:
            _user32.SetWindowPos(self.hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)
        self.root.after(250 if self.always_on_top else 1000, self._keep_on_top)


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
        self.rect = (0, 0, 0, 0)  # where it's showing (empty while hidden)

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
        self.win.geometry(f"{w}x{h}+{x0}+{y0}")
        self.win.update_idletasks()  # move it before showing it (see Fx._play)
        self.rect = (x0, y0, x1, y1)
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
        self.rect = (0, 0, 0, 0)
        _user32.ShowWindow(self.hwnd, _SW_HIDE)
