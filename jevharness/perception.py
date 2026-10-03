"""Screen capture + Windows built-in OCR into a list of text elements with screen coordinates."""

import asyncio
import statistics
import time
from dataclasses import dataclass

import mss
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

from .accessibility import Controls
from .desktop import cursor_pos, foreground_center, foreground_window, foreground_window_title, monitor_at

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
    kind: str = ""  # what UI Automation says it is ("button", "tab", "text box"…); blank for plain OCR text

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


# Named parts of a monitor for a closer look, as fractions (left, top, right, bottom). Overlapping on purpose,
# so a control on a boundary is whole in at least one of them.
REGIONS = {
    "top-left": (0, 0, 0.5, 0.5), "top-right": (0.5, 0, 1, 0.5),
    "bottom-left": (0, 0.5, 0.5, 1), "bottom-right": (0.5, 0.5, 1, 1),
    "top": (0, 0, 1, 0.35), "bottom": (0, 0.65, 1, 1), "left": (0, 0, 0.35, 1), "right": (0.65, 0, 1, 1),
    "centre": (0.2, 0.2, 0.8, 0.8),
}


def region_box(name: str, width: int, height: int) -> tuple[int, int, int, int]:
    fx0, fy0, fx1, fy1 = REGIONS.get(name, (0, 0, 1, 1))
    return int(fx0 * width), int(fy0 * height), int(fx1 * width), int(fy1 * height)


def merge_elements(screen: Screen, extra: list[Element]) -> Screen:
    """The screen plus newly found elements (e.g. from a closer look), skipping ones already there."""
    def same(a: Element, b: Element) -> bool:
        return a.text.lower() == b.text.lower() and abs(a.center[0] - b.center[0]) < 40 and abs(a.center[1] - b.center[1]) < 40

    merged = list(screen.elements)
    for el in extra:
        if not any(same(el, m) for m in merged):
            merged.append(Element(f"e{len(merged) + 1}", el.text, el.left, el.top, el.width, el.height, el.kind))
    return Screen(screen.monitor, screen.window_title, merged, screen.ocr_ms, screen.shot)


def _intersects(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _same_label(a: str, b: str) -> bool:
    a, b = a.lower().strip(), b.lower().strip()
    return bool(a and b) and (a in b or b in a)


def add_controls(elements: list[Element], controls: list, mon: dict, exclude=()) -> list[Element]:
    """OCR elements plus UI Automation controls ((name, kind, rect) from accessibility.Controls).

    A control whose name OCR already read inside its box just tags that OCR element with its kind; the rest
    (icon buttons, unlabelled fields, text OCR missed) become new elements. Controls off this monitor, tiny,
    or as big as a whole panel are skipped.
    """
    out = list(elements)
    area = mon["width"] * mon["height"]
    for name, kind, (l, t, r, b) in controls:
        w, h = r - l, b - t
        cx, cy = l + w // 2, t + h // 2
        if w < 4 or h < 4 or w * h > area * 0.25:
            continue
        if not (mon["left"] <= cx < mon["left"] + mon["width"] and mon["top"] <= cy < mon["top"] + mon["height"]):
            continue
        if any(_intersects((l, t, r, b), x) for x in exclude):
            continue
        box = (l - 4, t - 4, r + 4, b + 4)
        inside = [e for e in out if box[0] <= e.center[0] < box[2] and box[1] <= e.center[1] < box[3]]
        if match := next((e for e in inside if _same_label(e.text, name)), None):
            match.kind = match.kind or kind
            continue
        if any(e.kind and e.text.lower() == name.lower() and e.rect == (l, t, r, b) for e in out):
            continue  # the same control listed twice
        out.append(Element(f"e{len(out) + 1}", name, l, t, w, h, kind))
    return out


class Perception:
    def __init__(self):
        self._engine = OcrEngine.try_create_from_user_profile_languages()
        if self._engine is None:
            raise RuntimeError("Windows OCR is unavailable: add an OCR-capable language in Settings > Time & language")
        self.controls = Controls()
        self.use_controls = True  # read named controls with UI Automation along with OCR (Settings → Jev)

    def capture(self, exclude: list[tuple[int, int, int, int]] = (), follow: str = "cursor",
                scale: float = 1.0, region: str | None = None, invert: bool = False) -> Screen:
        """OCR the monitor under the mouse (follow="cursor") or holding the active window (follow="foreground").

        Elements overlapping `exclude` rects (our own overlay) are dropped. For a closer look, `scale` enlarges
        the image before OCR (small text it would otherwise miss) and `region` (see REGIONS) limits it to part
        of the monitor, and `invert` reads a high-contrast negative (light text on dark backgrounds). Element
        positions are always real screen pixels.

        A normal full read also adds the active window's named controls from UI Automation (see add_controls).
        """
        t0 = time.perf_counter()
        title = foreground_window_title()
        plain = scale == 1.0 and region is None and not invert
        controls = self.controls.start(foreground_window()) if plain and self.use_controls else None
        point = (foreground_center() if follow == "foreground" else None) or cursor_pos()
        with mss.MSS() as sct:
            mon = monitor_at(sct.monitors[1:], *point)
            shot = sct.grab(mon)

        ox, oy = mon["left"], mon["top"]
        if plain:
            data, width, height = bytes(shot.bgra), shot.width, shot.height
        else:
            from PIL import Image

            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            if region is not None:
                x0, y0, x1, y1 = region_box(region, shot.width, shot.height)
                img = img.crop((x0, y0, x1, y1))
                ox, oy = ox + x0, oy + y0
            if invert:
                from PIL import ImageOps

                img = ImageOps.autocontrast(ImageOps.invert(img.convert("L"))).convert("RGB")
            scale = min(scale, (OcrEngine.max_image_dimension - 1) / max(img.size))  # the OCR engine's size limit
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
            data, (width, height) = img.convert("RGBA").tobytes("raw", "BGRA"), img.size

        writer = DataWriter()
        writer.write_bytes(data)
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, width, height)
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
                    left=ox + int(min(xs) / scale),
                    top=oy + int(min(ys) / scale),
                    width=int((max(x2) - min(xs)) / scale),
                    height=int((max(y2) - min(ys)) / scale),
                )
                if not any(_intersects(el.rect, r) for r in exclude):
                    elements.append(el)
        if controls is not None:
            elements = add_controls(elements, self.controls.collect(controls), mon, exclude)
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
