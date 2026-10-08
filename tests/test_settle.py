import numpy as np

from jevharness import settle as s


def grid(fill=0, h=40, w=60):
    return np.full((h, w, 3), fill, np.uint8)


def test_ready_once_still_for_the_quiet_spell():
    w = s.Settle(0.3)
    g = grid()
    assert not w.update(g, 0.0)
    assert not w.update(g, 0.2)
    assert w.update(g, 0.3)


def test_a_change_restarts_the_quiet_spell():
    w = s.Settle(0.3)
    w.update(grid(0), 0.0)
    assert not w.update(grid(200), 0.25)  # the page redrew
    assert not w.update(grid(200), 0.5)
    assert w.update(grid(200), 0.55)


def test_a_caret_sized_change_is_noise():
    w = s.Settle(0.2)
    w.update(grid(), 0.0)
    g = grid()
    g[5, 5] = 255
    assert w.update(g, 0.2)


def test_ignored_cells_dont_hold_it_up():
    """Fing's own pill and highlight, or a video that was already playing."""
    w = s.Settle(0.2)
    ignore = s.rect_mask((40, 60), 0, 0, [(0, 0, 10 * s.STRIDE, 10 * s.STRIDE)])
    a, b = grid(), grid()
    b[:10, :10] = 255
    w.update(a, 0.0, ignore)
    assert w.update(b, 0.2, ignore)


def test_a_busy_cursor_means_not_ready():
    w = s.Settle(0.2)
    w.update(grid(), 0.0)
    assert not w.update(grid(), 0.3, busy=True)
    assert w.update(grid(), 0.5)


def test_a_different_monitor_starts_over():
    w = s.Settle(0.2)
    w.update(grid(), 0.0)
    assert not w.update(grid(h=30), 0.3)
    assert w.update(grid(h=30), 0.5)


def test_rect_mask_is_relative_to_the_monitor_and_clipped():
    m = s.rect_mask((40, 60), 1920, 0, [(1920 + 2 * s.STRIDE, 0, 1920 + 3 * s.STRIDE, s.STRIDE), (0, 0, 100, 100)])
    assert m[0, 2] and m[0, 3] and not m[0, 0]
    assert not m[:, 6:].any() and not m[3:].any()


def test_grow_adds_neighbours():
    m = np.zeros((5, 5), bool)
    m[2, 2] = True
    assert s.grow(m).sum() == 9
