"""Fing's name, colours and logo: a gloved pointing finger, drawn from one set of shapes.

The same outlines draw the logo (PIL, for the app icon, the tray and Settings) and the hand that animates
on the indicator (Tk canvas polygons, see fx.py), so they always match. Shapes are in a 100 x 100 box with
the fingertip at the top; `pose()` scales, rotates and moves them.
"""

from __future__ import annotations

import math
import os

DEFAULT_NAME = "Fing"  # short for fingers; the user can rename it (Settings → General)
ACCENT_A = "#7c5cff"  # violet
ACCENT_B = "#3b82f6"  # blue
GLOVE = "#ffffff"
INK = "#1b1530"  # the hand's outline
ICON_PATH = os.path.join(os.path.dirname(__file__), "icon.ico")

Point = tuple[float, float]


def _arc(cx: float, cy: float, rx: float, ry: float, a0: float, a1: float, n: int = 10) -> list[Point]:
    return [(cx + rx * math.cos(a), cy + ry * math.sin(a))
            for a in (a0 + (a1 - a0) * i / n for i in range(n + 1))]


def ellipse(cx: float, cy: float, rx: float, ry: float, n: int = 24) -> list[Point]:
    return _arc(cx, cy, rx, ry, 0, 2 * math.pi, n)[:-1]


def rrect(x0: float, y0: float, x1: float, y1: float, r: float) -> list[Point]:
    """A rounded rectangle's outline."""
    h = math.pi / 2
    return (_arc(x1 - r, y0 + r, r, r, -h, 0, 6) + _arc(x1 - r, y1 - r, r, r, 0, h, 6)
            + _arc(x0 + r, y1 - r, r, r, h, 2 * h, 6) + _arc(x0 + r, y0 + r, r, r, 2 * h, 3 * h, 6))


def capsule(x0: float, y0: float, x1: float, y1: float, r: float) -> list[Point]:
    """A finger: a bar with round ends from (x0, y0) to (x1, y1), r thick on each side."""
    a = math.atan2(y1 - y0, x1 - x0)
    h = math.pi / 2
    return _arc(x1, y1, r, r, a - h, a + h, 9) + _arc(x0, y0, r, r, a + h, a + 3 * h, 9)


# Back to front; each piece is drawn filled and outlined, so the outlines of the pieces in front read as the
# gaps between fingers.
POINTER: list[list[Point]] = [
    rrect(36, 84, 70, 97, 4),  # cuff
    rrect(31, 44, 77, 88, 15),  # back of the hand
    capsule(64, 50, 68, 58, 8),  # curled little finger
    capsule(57, 46, 60, 56, 8.5),  # curled ring finger
    capsule(49, 45, 52, 55, 8.5),  # curled middle finger
    capsule(41, 19, 41, 56, 9.5),  # the pointing finger
    capsule(28, 66, 50, 62, 8.5),  # thumb, folded across the front
]
NAIL = rrect(37, 14, 45, 23, 3)  # a hint of fingernail on the pointing finger

OPEN_HAND: list[list[Point]] = [
    rrect(36, 84, 70, 97, 4),
    rrect(30, 44, 76, 88, 16),
    capsule(70, 52, 79, 26, 6.5),  # little finger
    capsule(61, 48, 65, 14, 7),  # ring
    capsule(50, 48, 50, 8, 7.5),  # middle
    capsule(39, 50, 34, 14, 7.5),  # index
    capsule(34, 74, 16, 50, 8),  # thumb
]


def pose(shapes: list[list[Point]], x: float, y: float, size: float, angle: float = 0.0,
         pivot: Point = (50, 100), squash: float = 1.0) -> list[list[Point]]:
    """The shapes `size` px tall, turned `angle` degrees (clockwise) about `pivot` (the wrist by default),
    squashed vertically by `squash` towards the pivot (a press), with the pivot landing on (x, y)."""
    s = size / 100
    c, si = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    px, py = pivot
    out = []
    for shape in shapes:
        pts = []
        for sx, sy in shape:
            dx, dy = (sx - px) * s * (2 - squash) ** 0.5, (sy - py) * s * squash
            pts.append((x + dx * c - dy * si, y + dx * si + dy * c))
        out.append(pts)
    return out


def fingertip(x: float, y: float, size: float, angle: float = 0.0, squash: float = 1.0) -> Point:
    """Where the pointing finger's tip lands for a POINTER posed with the same arguments."""
    return pose([[(41, 9.5)]], x, y, size, angle, squash=squash)[0][0]


# ---- the logo -----------------------------------------------------------------------------------------------

def _hex(c: str) -> tuple[int, int, int]:
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def logo(size: int, ring: str | None = None):
    """The app icon: the white pointing glove on a violet-to-blue rounded square, tapping (two arcs at the
    fingertip). With `ring`, a coloured ring round the edge instead (the tray shows its state that way)."""
    from PIL import Image, ImageDraw

    k = 8 if size < 64 else 4  # draw big and shrink, for smooth edges
    n = size * k
    grad = Image.new("RGBA", (n, n))
    (r1, g1, b1), (r2, g2, b2) = _hex(ACCENT_A), _hex(ACCENT_B)
    gd = ImageDraw.Draw(grad)
    for i in range(2 * n):  # diagonal gradient, top-left to bottom-right
        t = i / (2 * n)
        gd.line([(i, 0), (0, i)], fill=(round(r1 + (r2 - r1) * t), round(g1 + (g2 - g1) * t), round(b1 + (b2 - b1) * t), 255),
                width=2)
    mask = Image.new("L", (n, n), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, n - 1, n - 1), radius=round(n * 0.26), fill=255)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    if ring:
        d.rounded_rectangle((0, 0, n - 1, n - 1), radius=round(n * 0.26), outline=ring, width=round(n * 0.09))

    hand_h = n * 0.9
    angle = -14
    hx, hy = n * 0.56, n * 1.05
    tip = fingertip(hx, hy, hand_h, angle)
    width = max(1, round(n * 0.028))
    if size >= 32:  # the tap: two short arcs above-left of the fingertip
        for r in (0.1, 0.17):
            rr = n * r
            d.arc((tip[0] - rr, tip[1] - rr, tip[0] + rr, tip[1] + rr), 200, 280, fill=(255, 255, 255, 220),
                  width=round(n * 0.03))
    for pts in pose(POINTER, hx, hy, hand_h, angle):
        d.polygon(pts, fill=GLOVE, outline=INK, width=width)
    if size >= 48:
        d.polygon(pose([NAIL], hx, hy, hand_h, angle)[0], fill="#e9e4ff")
    return img.resize((size, size), Image.LANCZOS)


def write_icon(path: str = ICON_PATH) -> None:
    """Regenerate icon.ico (Windows picks the size it needs from the set)."""
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    big = logo(256)
    big.save(path, sizes=[(s, s) for s in sizes], append_images=[logo(s) for s in sizes[:-1]])


def svg(size: int = 64) -> str:
    """The logo as SVG, for the phone page and the README."""
    hand_h, angle = 90.0, -14
    hx, hy = 56.0, 105.0
    tip = fingertip(hx, hy, hand_h, angle)

    def path(pts):
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + " Z"

    hand = "".join(f'<path d="{path(p)}"/>' for p in pose(POINTER, hx, hy, hand_h, angle))
    nail = path(pose([NAIL], hx, hy, hand_h, angle)[0])
    arcs = ""
    for r in (10, 17):
        (x0, y0), (x1, y1) = ((tip[0] + r * math.cos(math.radians(a)), tip[1] + r * math.sin(math.radians(a)))
                              for a in (200, 280))
        arcs += f'<path d="M{x0:.1f},{y0:.1f} A{r},{r} 0 0 1 {x1:.1f},{y1:.1f}"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="{size}" height="{size}">'
            f'<defs><linearGradient id="fing-g" x1="0" y1="0" x2="1" y2="1">'
            f'<stop offset="0" stop-color="{ACCENT_A}"/><stop offset="1" stop-color="{ACCENT_B}"/></linearGradient></defs>'
            f'<rect width="100" height="100" rx="26" fill="url(#fing-g)"/>'
            f'<g fill="none" stroke="#fff" stroke-opacity=".86" stroke-width="3" stroke-linecap="round">{arcs}</g>'
            f'<g fill="{GLOVE}" stroke="{INK}" stroke-width="2.8" stroke-linejoin="round">{hand}</g>'
            f'<path d="{nail}" fill="#e9e4ff"/></svg>')


if __name__ == "__main__":  # python -m fing.brand: rebuild icon.ico after changing the logo
    write_icon()
    print("Wrote", ICON_PATH)
