"""Named controls in the active window from Windows UI Automation (the accessibility tree).

Apps tell screen readers the name of every button, tab, link and field, including icon-only ones OCR can't
read ("Settings", "Close", "Search"). Reading those names is local and quick, so Jev can pick them like OCR
text before anything asks a vision model.

UI Automation is COM, so it runs on one thread of its own (initialised for COM there, separately from the
WinRT/PortAudio threads). Any failure just means no extra elements: OCR carries on as before.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

log = logging.getLogger(__name__)

# UIA control type ids worth clicking or typing into, and what to call them.
KINDS = {
    50000: "button", 50002: "checkbox", 50003: "combo box", 50004: "text box", 50005: "link", 50006: "image",
    50007: "list item", 50011: "menu item", 50013: "radio button", 50015: "slider", 50019: "tab",
    50024: "tree item", 50029: "item", 50031: "button", 50035: "column header",
}
_NAME, _RECT, _TYPE, _ENABLED, _OFFSCREEN = 30005, 30001, 30003, 30010, 30022  # UIA property ids
_DESCENDANTS = 4  # TreeScope_Descendants
MAX_CONTROLS = 150
WAKE_WALKS = 2  # full-tree walks per window to switch on a Chromium/Electron app's web-content accessibility
TIMEOUT_S = 1.5  # a huge or hung app's tree isn't worth waiting for: OCR alone is fine


class Controls:
    def __init__(self):
        self._pool = ThreadPoolExecutor(1, thread_name_prefix="uia", initializer=self._init_thread)
        self._uia = None
        self._busy = None  # the last query, while it's still running (a slow app): skip rather than queue up
        self._walks: dict[int, int] = {}  # window -> full-tree walks done to wake its accessibility
        self.broken = False

    @staticmethod
    def _init_thread() -> None:
        if "comtypes" not in sys.modules:
            sys.coinit_flags = 0  # comtypes initialises COM for the importing thread: make it multithreaded
        import comtypes

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # already initialised on this thread

    def start(self, hwnd: int) -> Future | None:
        """Start reading window `hwnd`'s controls in the background (e.g. while OCR runs); pass the result to
        `collect`. None when there's nothing to read or the last read is still going."""
        if self.broken or not hwnd or (self._busy is not None and not self._busy.done()):
            return None
        self._busy = self._pool.submit(self._query, hwnd)
        return self._busy

    def collect(self, job: Future | None) -> list[tuple[str, str, tuple[int, int, int, int]]]:
        """(name, kind, (left, top, right, bottom)) of the enabled, on-screen controls `start` found, in tree
        order. Empty if UI Automation failed, was slow, or the window exposes nothing."""
        if job is None:
            return []
        try:
            return job.result(timeout=TIMEOUT_S)
        except FutureTimeout:
            log.info("UI Automation took over %.1f s; using OCR alone", TIMEOUT_S)
            return []
        except Exception:
            # Can't set up at all (e.g. comtypes missing): stop trying. One window refusing (an elevated app): skip it.
            self.broken = self._uia is None
            log.exception("UI Automation failed%s", "; using OCR alone from now on" if self.broken else " for this window")
            return []

    def _setup(self):
        import comtypes.client

        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia

        auto = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        cache = auto.CreateCacheRequest()
        for prop in (_NAME, _RECT, _TYPE):
            cache.AddProperty(prop)
        kinds = None
        for t in KINDS:
            c = auto.CreatePropertyCondition(_TYPE, t)
            kinds = c if kinds is None else auto.CreateOrCondition(kinds, c)
        visible = auto.CreateAndCondition(auto.CreatePropertyCondition(_OFFSCREEN, False),
                                          auto.CreatePropertyCondition(_ENABLED, True))
        self._uia = auto, cache, auto.CreateAndCondition(visible, kinds)

    def _query(self, hwnd: int) -> list[tuple[str, str, tuple[int, int, int, int]]]:
        if self._uia is None:
            self._setup()
        auto, cache, condition = self._uia
        root = auto.ElementFromHandle(ctypes.c_void_p(hwnd))
        # Chromium and Electron apps (Brave, Discord, VS Code…) only build their page's accessibility tree once
        # something walks it, and report almost nothing until then. Walk a new window's whole tree first, and once
        # more if a Chromium window still looks empty.
        walks = self._walks.get(hwnd, 0)
        if walks == 0:
            self._wake(auto, root, hwnd)
        found = root.FindAllBuildCache(_DESCENDANTS, condition, cache)
        if found.Length <= 3 and walks < WAKE_WALKS and _is_chromium(hwnd):
            self._wake(auto, root, hwnd)
            found = root.FindAllBuildCache(_DESCENDANTS, condition, cache)
        out = []
        for i in range(found.Length):
            el = found.GetElement(i)
            kind = KINDS.get(el.CachedControlType, "")
            name = " ".join((el.CachedName or "").split())[:120]
            if not name:
                if kind != "text box":
                    continue
                name = "text box"  # an unlabelled field is still somewhere to type
            r = el.CachedBoundingRectangle
            out.append((name, kind, (r.left, r.top, r.right, r.bottom)))
            if len(out) >= MAX_CONTROLS:
                break
        return out

    def _wake(self, auto, root, hwnd: int) -> None:
        if len(self._walks) > 500:
            self._walks.clear()  # window handles are reused: don't grow forever
        self._walks[hwnd] = self._walks.get(hwnd, 0) + 1
        root.FindAll(_DESCENDANTS, auto.CreateTrueCondition())


def _is_chromium(hwnd: int) -> bool:
    cls = ctypes.create_unicode_buffer(64)
    ctypes.windll.user32.GetClassNameW(hwnd, cls, 64)
    return cls.value.startswith("Chrome_WidgetWin")
