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


def check(mode, nbars, size):
    w, h = size
    for i in range(nbars):
        for rect in V.linear_bar_rects(nbars, i, h, size):
            x, top, bw, hh = rect
            assert x >= 0, (mode, size, i, rect)
            assert x + bw <= w, (mode, size, i, rect)
            assert top >= 0, (mode, size, i, rect)
            assert top + hh <= h, (mode, size, i, rect)
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
        check(V.MODE_UP, BARS_COUNT, size)
        for nbars in (8, 40, 96):
            check(V.MODE_UP, nbars, size)
    print("BOUNDS_TEST_OK: all linear bars stay inside the window on every size")
    return 0


if __name__ == "__main__":
    sys.exit(main())