"""Fing's hand: the animations that play over the screen while it works for you.

  tap        a gloved finger presses the spot Fing just clicked, with a ripple and a burst of sparks
  wave       an open hand pops out of the indicator and waves (Fing is ready)
  celebrate  confetti from the indicator when a request is done
  hop        the finger points at each numbered option in turn when Fing asks which one

Like the indicator, the window is click-through and never takes focus, and it's only as big as the animation
needs. Only one animation plays at a time: a new one replaces the old. `cut()` (any thread) hides it at once,
so a screen read never sees the hand over what it's reading.
"""

from __future__ import annotations

import math
import random
import threading
import tkinter as tk

from . import brand
from .overlay import _HWND_TOPMOST, _KEY, _SW_HIDE, _SW_SHOWNOACTIVATE, _SWP_NOACTIVATE, _SWP_NOMOVE, \
    _SWP_NOSIZE, _base_window, _make_passive, _mix, _user32

FRAME_MS = 20
CONFETTI = ("#7c5cff", "#3b82f6", "#22c55e", "#fbbc04", "#f43f5e", "#22d3ee", "#ffffff")


def _ease_out(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_back(t: float) -> float:
    """Ease out with a little overshoot (a pop)."""
    c = 1.9
    return 1 + (c + 1) * (t - 1) ** 3 + c * (t - 1) ** 2


def _clamp(t: float) -> float:
    return min(1.0, max(0.0, t))


class Fx:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.enabled = True
        self.win = _base_window(root)
        self.canvas = tk.Canvas(self.win, bg=_KEY, highlightthickness=0, width=1, height=1)
        self.canvas.pack()
        self.hwnd = _make_passive(self.win)
        _user32.ShowWindow(self.hwnd, _SW_HIDE)
        self.rect = (0, 0, 0, 0)  # where it's showing (empty while hidden)
        self.bg = "#202124"  # the indicator's colour, for anything drawn over it
        self._job = None
        self._origin = (0, 0)
        self._cut = threading.Event()
        self.idle = threading.Event()
        self.idle.set()

    # ---- the animations -------------------------------------------------------------------------------------

    def tap(self, x: int, y: int, colour: str = "#34a853") -> None:
        """The finger presses (x, y) and lifts away, leaving a ripple and sparks."""
        size, angle = 58, -24
        sparks = [i * math.tau / 8 + random.uniform(-0.15, 0.15) for i in range(8)]

        def frame(c: tk.Canvas, f: int) -> bool:
            # Drops in (0-3), presses (3-7), lifts away (8-22); the ripple and sparks spread from the press.
            if f < 4:
                t = _ease_out(f / 3)
                self._pointer(c, x + 22 * (1 - t), y + 26 * (1 - t), size * (0.8 + 0.2 * t), angle - 10 * (1 - t))
            elif f < 8:
                self._pointer(c, x, y, size, angle, squash=1 - 0.16 * math.sin(math.pi * (f - 3) / 4))
            elif f < 22:
                t = _ease_out((f - 8) / 13)
                self._pointer(c, x + 34 * t, y + 44 * t, size * (1 - 0.55 * t), angle + 12 * t)
            if 4 <= f < 26:
                t = (f - 4) / 21
                for k, (r0, w) in enumerate(((4, 4.0), (0, 2.5))):
                    tt = _clamp(t * 1.25 - 0.25 * k)
                    if 0 < tt < 1:
                        r = r0 + 30 * _ease_out(tt)
                        self._oval(c, x, y, r, outline=_mix(colour, "#ffffff", 0.25 * tt), width=max(1.0, w * (1 - tt)))
            if 4 <= f < 15:
                t = _ease_out((f - 4) / 10)
                for a in sparks:
                    r0, r1 = 10 + 18 * t, 16 + 22 * t
                    self._line(c, x + r0 * math.cos(a), y + r0 * math.sin(a), x + r1 * math.cos(a),
                               y + r1 * math.sin(a), fill=colour, width=max(1.0, 3 * (1 - t)))
            return f < 26

        self._play((x - 50, y - 50, x + 110, y + 120), frame)

    def wave(self, x: int, y: int) -> None:
        """An open hand rises out of (x, y) (the indicator's dot), waves twice and sinks back."""
        size = 46

        def frame(c: tk.Canvas, f: int) -> bool:
            if f < 10:
                grow, angle = _ease_back(f / 9), -20 * (1 - f / 9)
            elif f < 42:
                grow, angle = 1.0, 20 * math.sin(math.tau * (f - 10) / 16)
            else:
                grow, angle = 1 - _ease_out((f - 42) / 8), 0
            if grow > 0.05:
                shapes = brand.pose(brand.OPEN_HAND, x, y - 8, size * grow, angle)
                self._hand(c, shapes)
            if f < 14:  # a little burst behind it as it appears
                t = f / 13
                for k in range(6):
                    a = -math.pi / 2 + (k - 2.5) * 0.45
                    r0, r1 = 16 + 30 * t, 22 + 34 * t
                    self._line(c, x + r0 * math.cos(a), y - 12 + r0 * math.sin(a), x + r1 * math.cos(a),
                               y - 12 + r1 * math.sin(a), fill=brand.ACCENT_A if k % 2 else brand.ACCENT_B,
                               width=max(1.0, 3 * (1 - t)))
            return f < 50

        self._play((x - 75, y - 95, x + 75, y + 12), frame)

    def celebrate(self, x: int, y: int) -> None:
        """Confetti bursts up out of (x, y) and tumbles down."""
        bits = []
        for i in range(22):
            a = -math.pi / 2 + random.uniform(-1.1, 1.1)
            v = random.uniform(5.5, 9.5)
            bits.append((v * math.cos(a), v * math.sin(a), random.uniform(0, math.tau), random.uniform(-0.5, 0.5),
                         CONFETTI[i % len(CONFETTI)], random.uniform(3, 5.5)))

        def frame(c: tk.Canvas, f: int) -> bool:
            if f < 8:  # a ring flashes out first
                t = _ease_out(f / 7)
                self._oval(c, x, y, 6 + 26 * t, outline=_mix("#34a853", "#ffffff", t * 0.6), width=max(1.0, 4 * (1 - t)))
            for vx, vy, spin, dspin, colour, s in bits:
                px, py = x + vx * f * 0.5, y + vy * f + 0.32 * f * f  # thrown up, falling back under gravity
                a = spin + dspin * f
                w, h = s, s * 0.55 * abs(math.cos(a * 1.7)) + 1  # flipping over as it falls
                ca, sa = math.cos(a), math.sin(a)
                pts = [(px + dx * ca - dy * sa, py + dx * sa + dy * ca)
                       for dx, dy in ((-w, -h), (w, -h), (w, h), (-w, h))]
                self._poly(c, pts, fill=colour, outline="")
            return f < 34

        self._play((x - 140, y - 150, x + 140, y + 60), frame)

    def hop(self, points: list[tuple[int, int]]) -> None:
        """Point at each of `points` in turn (the numbered options of a question), then go."""
        points = points[:6]
        if not points:
            return
        size, angle, per = 44, -30, 22  # frames at each option

        def frame(c: tk.Canvas, f: int) -> bool:
            i, k = divmod(f, per)
            if i >= len(points):
                return False
            x, y = points[i]
            if k < 7 and i > 0:  # glide over from the last one
                t = _ease_out(k / 6)
                px, py = points[i - 1]
                x, y = px + (x - px) * t, py + (y - py) * t
            bob = 5 * math.sin(math.tau * k / 11)
            shrink = 1 - _ease_out((k - (per - 6)) / 6) if i == len(points) - 1 and k > per - 6 else 1
            if f < 5:
                shrink = _ease_back(f / 4)
            if shrink > 0.05:
                self._pointer(c, x + 3 + bob * 0.6, y + 3 + bob, size * shrink, angle)
            return True

        xs, ys = [p[0] for p in points], [p[1] for p in points]
        self._play((min(xs) - 30, min(ys) - 30, max(xs) + 80, max(ys) + 90), frame)

    # ---- drawing -----------------------------------------------------------------------------------------------

    def _pointer(self, c: tk.Canvas, x: float, y: float, size: float, angle: float, squash: float = 1.0) -> None:
        """The pointing hand with its fingertip on (x, y)."""
        tx, ty = brand.fingertip(0, 0, size, angle, squash)
        self._hand(c, brand.pose(brand.POINTER, x - tx, y - ty, size, angle, squash=squash))

    def _hand(self, c: tk.Canvas, shapes) -> None:
        for pts in shapes:
            self._poly(c, pts, fill=brand.GLOVE, outline=brand.INK, width=2)

    def _poly(self, c: tk.Canvas, pts, **kw) -> None:
        ox, oy = self._origin
        c.create_polygon([v for x, y in pts for v in (x - ox, y - oy)], **kw)

    def _oval(self, c: tk.Canvas, x: float, y: float, r: float, **kw) -> None:
        ox, oy = self._origin
        c.create_oval(x - r - ox, y - r - oy, x + r - ox, y + r - oy, **kw)

    def _line(self, c: tk.Canvas, x0: float, y0: float, x1: float, y1: float, **kw) -> None:
        ox, oy = self._origin
        c.create_line(x0 - ox, y0 - oy, x1 - ox, y1 - oy, capstyle="round", **kw)

    # ---- playing ------------------------------------------------------------------------------------------------

    def _play(self, box: tuple[int, int, int, int], frame) -> None:
        """Run `frame(canvas, n)` every FRAME_MS in a window covering `box` (screen pixels) until it returns
        False."""
        self.stop()
        if not self.enabled:
            return
        x0, y0, x1, y1 = map(round, box)
        self._origin = (x0, y0)
        self._cut.clear()
        self.idle.clear()
        self.canvas.configure(width=x1 - x0, height=y1 - y0)
        self.win.geometry(f"{x1 - x0}x{y1 - y0}{x0:+d}{y0:+d}")
        self.rect = (x0, y0, x1, y1)
        _user32.ShowWindow(self.hwnd, _SW_SHOWNOACTIVATE)
        _user32.SetWindowPos(self.hwnd, _HWND_TOPMOST, 0, 0, 0, 0, _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE)
        self._step(frame, 0)

    def _step(self, frame, n: int) -> None:
        self._job = None
        c = self.canvas
        c.delete("all")
        if self._cut.is_set() or not frame(c, n):
            self.stop()
            return
        self._job = self.root.after(FRAME_MS, lambda: self._step(frame, n + 1))

    def stop(self) -> None:
        """Hide whatever is playing (Tk thread)."""
        if self._job:
            self.root.after_cancel(self._job)
            self._job = None
        self.canvas.delete("all")
        _user32.ShowWindow(self.hwnd, _SW_HIDE)
        self.rect = (0, 0, 0, 0)
        self.idle.set()

    def cut(self, wait_s: float = 0.1) -> None:
        """Any thread: stop the animation and wait (briefly) until it's off the screen."""
        if self.idle.is_set():
            return
        self._cut.set()
        self.idle.wait(wait_s)
