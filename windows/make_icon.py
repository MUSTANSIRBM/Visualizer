import os
import struct

import pygame

BASE = os.path.dirname(os.path.abspath(__file__))
REAL = 1024
SIZES = [16, 24, 32, 48, 64, 128, 256]

BG = (12, 14, 22, 255)
HILITE = (42, 52, 86, 255)
BLUE = ((18, 53, 107), (127, 212, 255))
RED = ((60, 12, 12), (255, 96, 84))


def gradient_rect(surf, rect, top, bottom):
    x, y, w, h = rect
    for yy in range(h):
        t = yy / max(h - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        pygame.draw.line(surf, (r, g, b, 255), (x, y + yy), (x + w - 1, y + yy))


def make(size):
    big = pygame.Surface((REAL, REAL), pygame.SRCALPHA)
    radius = int(REAL * 0.16)
    big_rect = big.get_rect()
    pygame.draw.rect(big, BG, big_rect, border_radius=radius)
    pygame.draw.rect(
        big, HILITE, (0, 0, REAL, int(REAL * 0.06)),
        border_top_left_radius=radius, border_top_right_radius=radius)

    n = 5
    gap = REAL * 0.045
    bw = (REAL - gap * (n + 1)) / n
    for i in range(n):
        colors = BLUE if i % 2 == 0 else RED
        x = int(gap + i * (bw + gap))
        bar = pygame.Rect(x, int(REAL * 0.20), int(bw), int(REAL * 0.66))
        cap_h = int(bar.h * 0.26)
        gradient_rect(big, (bar.x, bar.y + cap_h, bar.w, bar.h - cap_h), colors[0], colors[1])
        cap = pygame.Rect(bar.x, bar.y, bar.w, cap_h)
        pygame.draw.rect(
            big, colors[1] + (255,), cap,
            border_top_left_radius=int(bw * 0.25), border_top_right_radius=int(bw * 0.25))

    return pygame.transform.smoothscale(big, (size, size))


def build_ico(pngs):
    header = struct.pack('<HHH', 0, 1, len(pngs))
    entries = b''
    offset = 6 + 16 * len(pngs)
    for size, data in pngs:
        dim = 0 if size >= 256 else size
        entries += struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return header + entries + b''.join(d for _, d in pngs)


def main():
    pygame.init()
    pngs = []
    for size in SIZES:
        surf = make(size)
        path = os.path.join(BASE, f"icon_{size}.png")
        pygame.image.save(surf, path)
        with open(path, 'rb') as f:
            pngs.append((size, f.read()))
    with open(os.path.join(BASE, 'icon.ico'), 'wb') as f:
        f.write(build_ico(pngs))
    pygame.quit()
    print(f"generated icon.ico with {len(pngs)} sizes")


if __name__ == '__main__':
    main()