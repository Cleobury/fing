"""When is the screen ready after an action? Frame-by-frame logic for `desktop.wait_until_settled`.

Kept free of Win32 so it can be tested. Frames are coarse grids of pixels ("cells"). The screen is ready
once nothing that matters has changed for a short quiet spell. What doesn't matter: Fing's own pill and
highlight, and anything that was already moving before the action (a video, a spinner, a clock), which
would otherwise keep every wait running to its timeout.
"""

import numpy as np

STRIDE = 16  # sample every 16th pixel each way: cheap, and a blinking caret rarely lands on one
DIFF = 24  # a sample changed if a colour channel moved at least this much (ignores dither and video noise)
MIN_CELLS = 4  # fewer changed samples than this is a caret or a clock's seconds, not the app reacting


def sample(frame: np.ndarray) -> np.ndarray:
    """The coarse grid of an (h, w, 4) BGRA frame."""
    return frame[::STRIDE, ::STRIDE, :3]


def changed(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Which cells differ between two grids of the same shape."""
    return np.abs(a.astype(np.int16) - b).max(axis=2) >= DIFF


def grow(mask: np.ndarray) -> np.ndarray:
    """`mask` plus its neighbouring cells, so the edges of a moving area count as moving too."""
    out = mask.copy()
    out[1:] |= mask[:-1]
    out[:-1] |= mask[1:]
    out[:, 1:] |= out[:, :-1].copy()
    out[:, :-1] |= out[:, 1:].copy()
    return out


def rect_mask(shape: tuple[int, ...], left: int, top: int, rects) -> np.ndarray:
    """Cells of a grid whose top-left pixel is at (left, top) that fall inside any of the screen `rects`."""
    mask = np.zeros(shape[:2], bool)
    for l, t, r, b in rects:
        if r <= l or b <= t:
            continue
        c0, r0 = max(0, (l - left) // STRIDE), max(0, (t - top) // STRIDE)
        c1, r1 = (r - left + STRIDE - 1) // STRIDE + 1, (b - top + STRIDE - 1) // STRIDE + 1
        if c1 > 0 and r1 > 0:
            mask[r0:r1, c0:c1] = True
    return mask


class Settle:
    """Feed it grids as they're captured; `update` says when the screen has been quiet for `quiet_s`."""

    def __init__(self, quiet_s: float):
        self.quiet_s = quiet_s
        self._prev = None
        self._last_change = None

    def update(self, grid: np.ndarray, t: float, ignore: np.ndarray | None = None, busy: bool = False) -> bool:
        """`grid` captured at time `t`. Cells in `ignore` don't count; `busy` (e.g. a wait cursor) counts as a change."""
        if self._prev is None or self._prev.shape != grid.shape:
            # First look, or a different monitor: start the quiet spell from here.
            self._prev, self._last_change = grid, t
            return False
        diff = changed(self._prev, grid)
        if ignore is not None and ignore.shape == diff.shape:
            diff &= ~ignore
        if busy or int(diff.sum()) >= MIN_CELLS:
            self._last_change = t
        self._prev = grid
        return t - self._last_change >= self.quiet_s
