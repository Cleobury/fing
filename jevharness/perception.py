"""Screen capture + Windows built-in OCR into a list of text elements with screen coordinates."""

import asyncio
import statistics
import time
from dataclasses import dataclass

import mss
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

from .desktop import cursor_pos, foreground_center, foreground_window_title, monitor_at

# Words on one OCR line further apart than this many line-heights are separate elements
# (e.g. a toolbar that OCR reads as one line: "File   Edit   View").
_GAP_SPLIT = 1.5


@dataclass
class Element:
    id: str
    text: str
    left: int
    top: int
    width: int
    height: int

    @property
    def center(self) -> tuple[int, int]:
        return self.left + self.width // 2, self.top + self.height // 2

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.left + self.width, self.top + self.height


@dataclass
class Screen:
    monitor: dict  # mss monitor: left, top, width, height (physical pixels, virtual-screen coords)
    window_title: str
    elements: list[Element]
    ocr_ms: float
    shot: object = None  # the mss screenshot, for the AI planner's vision input


def _intersects(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


class Perception:
    def __init__(self):
        self._engine = OcrEngine.try_create_from_user_profile_languages()
        if self._engine is None:
            raise RuntimeError("Windows OCR is unavailable: add an OCR-capable language in Settings > Time & language")

    def capture(self, exclude: list[tuple[int, int, int, int]] = (), follow: str = "cursor") -> Screen:
        """OCR the monitor under the mouse (follow="cursor") or holding the active window (follow="foreground").

        Elements overlapping `exclude` rects (our own overlay) are dropped.
        """
        t0 = time.perf_counter()
        title = foreground_window_title()
        point = (foreground_center() if follow == "foreground" else None) or cursor_pos()
        with mss.MSS() as sct:
            mon = monitor_at(sct.monitors[1:], *point)
            shot = sct.grab(mon)

        writer = DataWriter()
        writer.write_bytes(bytes(shot.bgra))
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, shot.width, shot.height
        )
        result = asyncio.run(self._recognize(bitmap))

        elements: list[Element] = []
        for line in result.lines:
            for words in self._split(list(line.words)):
                xs = [w.bounding_rect.x for w in words]
                ys = [w.bounding_rect.y for w in words]
                x2 = [w.bounding_rect.x + w.bounding_rect.width for w in words]
                y2 = [w.bounding_rect.y + w.bounding_rect.height for w in words]
                el = Element(
                    id=f"e{len(elements) + 1}",
                    text=" ".join(w.text for w in words),
                    left=mon["left"] + int(min(xs)),
                    top=mon["top"] + int(min(ys)),
                    width=int(max(x2) - min(xs)),
                    height=int(max(y2) - min(ys)),
                )
                if not any(_intersects(el.rect, r) for r in exclude):
                    elements.append(el)
        return Screen(mon, title, elements, (time.perf_counter() - t0) * 1000, shot)

    async def _recognize(self, bitmap):
        return await self._engine.recognize_async(bitmap)

    @staticmethod
    def _split(words):
        if not words:
            return []
        height = statistics.median(w.bounding_rect.height for w in words) or 1
        groups = [[words[0]]]
        for prev, w in zip(words, words[1:], strict=False):
            gap = w.bounding_rect.x - (prev.bounding_rect.x + prev.bounding_rect.width)
            if gap > _GAP_SPLIT * height:
                groups.append([])
            groups[-1].append(w)
        return groups
