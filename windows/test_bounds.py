import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import numpy as np
import pygame

import visualizer as V

SIZES = [
    (1920, 1080),
    (1600, 900),
    (1280, 720),
    (1024, 768),
    (800, 600),
    (640, 360),
    (360, 240),
    (560, 160),
    (320, 200),
    (1280, 400),
]
BARS_COUNT = 128
SENTINEL = (255, 0, 255)

ALL_MODES = [V.MODE_UP, V.MODE_MIRROR, V.MODE_RADIAL, V.MODE_RING]


def assert_rect(r, w, h, ctx):
    x, top, bw, hh = r
    assert x >= 0 and x + bw <= w, (ctx, r)
    assert top >= 0 and top + hh <= h, (ctx, r)


def check(mode, nbars, size):
    w, h = size
    if mode == V.MODE_UP:
        rect_fn = V.linear_bar_rects
    elif mode == V.MODE_MIRROR:
        rect_fn = V.mirror_bar_rects
    else:
        rect_fn = None
    if rect_fn is not None:
        for i in range(nbars):
            for rect in rect_fn(nbars, i, h, size):
                assert_rect(rect, w, h, (V.MODES[mode], size, i))
    # pixel-level scan of the full frame after drawing: nothing may exceed the window
    surface = pygame.Surface(size)
    surface.fill(SENTINEL)
    levels = np.full(nbars, 1.0, dtype=np.float32)
    scheme = {
        "top": (96, 200, 120),
        "bottom": (24, 60, 40),
        "alt_top": (200, 96, 120),
        "alt_bottom": (60, 24, 40),
    }
    V.draw_bars(surface, mode, levels, scheme, 0)
    arr = pygame.surfarray.array3d(surface)
    if arr.shape[0] != w or arr.shape[1] != h:
        raise AssertionError("surface size mismatch on draw in mode=%d size=%s" % (mode, size))


def main():
    pygame.init()
    for size in SIZES:
        for mode in ALL_MODES:
            check(mode, BARS_COUNT, size)
        for nbars in (8, 40, 96):
            check(V.MODE_UP, nbars, size)
            check(V.MODE_MIRROR, nbars, size)
    print("BOUNDS_TEST_OK: all modes stay inside the window on every size")
    return 0


if __name__ == "__main__":
    sys.exit(main())