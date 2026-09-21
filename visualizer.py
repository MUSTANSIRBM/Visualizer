import ctypes
import math
import os
import sys
import threading
import time

import numpy as np
import pygame
import pyaudiowpatch as pyaudio

RATE_DEFAULT = 48000
CHUNK = 1024
FFT_N = CHUNK * 2

DB_FLOOR = -70.0
DB_HEAD = -8.0
GAIN_EXP = 1.1
GAIN_ATTACK = 0.08
GAIN_RELEASE = 0.015
RISE = 0.70
FALL = 0.90
FLOOR_RATIO = 1.5
FLOOR_MIN = 0.010
MIN_FREQ = 30.0
MAX_FREQ = 18000.0

MIN_BARS = 8
MAX_BARS = 128
DEFAULT_BARS = 40
TARGET_FPS = 60

COLOR_SCHEMES = []


def parse_hex(h):
    h = h.strip().lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def resource_path(name):
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def load_color_schemes(path):
    schemes = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            entry = {
                "name": parts[0],
                "bottom": parse_hex(parts[1]),
                "top": parse_hex(parts[2]),
            }
            if len(parts) >= 5:
                entry["alt_bottom"] = parse_hex(parts[3])
                entry["alt_top"] = parse_hex(parts[4])
            schemes.append(entry)
    if not schemes:
        raise ValueError("No color schemes defined")
    return schemes


def enable_dpi_awareness():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def set_dark_title_bar():
    try:
        hwnd = pygame.display.get_wm_info().get("window")
        if not hwnd:
            hwnd = ctypes.windll.user32.GetActiveWindow()
        if not hwnd:
            return
        value = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:
        pass


class AudioCapture:
    def __init__(self):
        self.p = pyaudio.PyAudio()
        self.chunk = CHUNK
        self.fft_n = FFT_N
        self.lock = threading.Lock()
        self.ring = None
        self.rate = RATE_DEFAULT
        self.device_name = None
        self.error = None
        self.stream = None
        self._make_stream()

    def _make_stream(self):
        try:
            loopback = self._default_loopback()
            if loopback is None:
                raise RuntimeError("No WASAPI loopback device found")
            self.device_name = loopback["name"]
            self.rate = int(loopback["defaultSampleRate"]) or RATE_DEFAULT
            channels = loopback["maxInputChannels"] or 2

            def callback(in_data, frame_count, time_info, status):
                data = np.frombuffer(in_data, dtype=np.int16).astype(np.float32)
                if data.size and data.size % channels == 0:
                    mono = data.reshape(-1, channels).mean(axis=1) / 32768.0
                    with self.lock:
                        if self.ring is None:
                            self.ring = np.zeros(self.fft_n, dtype=np.float32)
                        if mono.size >= self.fft_n:
                            self.ring[:] = mono[-self.fft_n:]
                        else:
                            shift = mono.size
                            self.ring[:-shift] = self.ring[shift:]
                            self.ring[-shift:] = mono
                return None, pyaudio.paContinue

            stream = self.p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=self.rate,
                input=True,
                input_device_index=loopback["index"],
                frames_per_buffer=self.chunk,
                stream_callback=callback,
            )
            stream.start_stream()
            self.stream = stream
            self.error = None
        except Exception as exc:
            self.stream = None
            self.error = f"{type(exc).__name__}: {exc}"

    def _default_loopback(self):
        try:
            wasapi = self.p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except OSError:
            return None
        default_out = self.p.get_device_info_by_index(wasapi["defaultOutputDevice"])
        for lp in self.p.get_loopback_device_info_generator():
            if default_out["name"] in lp["name"]:
                return lp
        for lp in self.p.get_loopback_device_info_generator():
            return lp
        return None

    def reopen(self):
        if self.stream is not None:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self._make_stream()

    def history(self):
        with self.lock:
            if self.ring is None:
                return np.zeros(self.fft_n, dtype=np.float32)
            return self.ring.copy()

    def close(self):
        try:
            if self.stream is not None:
                self.stream.stop_stream()
                self.stream.close()
        except Exception:
            pass
        try:
            self.p.terminate()
        except Exception:
            pass


def compute_bands(history, rate, nbars, window):
    spec = np.abs(np.fft.rfft(history * window))
    max_freq = min(MAX_FREQ, rate * 0.45)
    low = min(MIN_FREQ, max_freq * 0.5)
    edges = np.exp(np.linspace(np.log(low), np.log(max_freq), nbars + 1))
    levels = np.empty(nbars, dtype=np.float32)
    for k in range(nbars):
        lo = max(int(edges[k] * FFT_N / rate), 1)
        hi = min(int(edges[k + 1] * FFT_N / rate), len(spec) - 1)
        if hi <= lo:
            hi = lo + 1
        levels[k] = spec[lo:hi].mean()
    norm = levels / (FFT_N / 2.0)
    db = 20.0 * np.log10(norm + 1e-12)
    return db


def auto_gain(db, gain):
    peak = float(db.max()) if db.size else -70.0
    target = DB_HEAD - peak
    target = min(max(target, -30.0), 70.0)
    if target < gain:
        gain += (target - gain) * GAIN_ATTACK
    else:
        gain += (target - gain) * GAIN_RELEASE
    return gain


def db_to_frac(db, gain):
    frac = (db + gain - DB_FLOOR) / (DB_HEAD - DB_FLOOR)
    return np.clip(frac, 0.0, 1.0) ** GAIN_EXP


def smooth_levels(smooth, target):
    up = target > smooth
    smooth[up] = RISE * smooth[up] + (1.0 - RISE) * target[up]
    smooth[~up] = FALL * smooth[~up] + (1.0 - FALL) * target[~up]


def silence_gate(gate, rms, noise_floor, opened):
    floor = noise_floor
    if gate < 0.5 and opened:
        if rms < floor or floor <= 0:
            floor = rms
        else:
            floor = 0.92 * floor + 0.08 * rms
    elif rms < floor and floor > 0:
        floor = rms
    want = rms > max(floor * FLOOR_RATIO, FLOOR_MIN)
    if want:
        gate += (1.0 - gate) * (1.0 - RISE * 0.5)
    else:
        gate -= gate * 0.10
    return max(min(gate, 1.0), 0.0), floor


def scheme_colors(frac, index, scheme, frame):
    top = scheme["top"]
    bottom = scheme["bottom"]
    wave = 1.0
    if "alt_top" in scheme:
        if index % 2 == 1:
            top = scheme["alt_top"]
            bottom = scheme["alt_bottom"]
        wave = 0.5 + 0.5 * math.sin(index * 0.90 + frame * 0.30)
    k = min((0.30 + 0.75 * frac) * (0.55 + 0.45 * wave), 1.0)
    return (
        (int(top[0] * k), int(top[1] * k), int(top[2] * k)),
        (int(bottom[0] * k), int(bottom[1] * k), int(bottom[2] * k)),
    )


def grad_color(cb, ct, t):
    return (int(cb[0] + (ct[0] - cb[0]) * t),
            int(cb[1] + (ct[1] - cb[1]) * t),
            int(cb[2] + (ct[2] - cb[2]) * t))


_grad_cache = {}


def _gradient_surface(cb, ct, w):
    key = (cb[0], cb[1], cb[2], ct[0], ct[1], ct[2], w)
    surf = _grad_cache.get(key)
    if surf is None:
        if len(_grad_cache) > 256:
            _grad_cache.clear()
        surf = pygame.Surface((w, 16))
        for row in range(16):
            t = row / 15.0
            pygame.draw.line(surf, grad_color(cb, ct, t), (0, row), (w - 1, row))
        _grad_cache[key] = surf
    return surf


def draw_gradient_column(surface, x, w, col_top, col_h, cb, ct, outer_top, cap_r, screen_rect):
    if col_h <= 0 or w <= 0:
        return
    if cap_r and col_h > cap_r * 2:
        cap_h = cap_r * 2
        body_h = col_h - cap_h
        body = pygame.Surface((w, body_h))
        pygame.transform.scale(_gradient_surface(cb, ct, w), (w, body_h), body)
        if outer_top:
            surface.blit(body, (x, col_top + cap_h))
            pygame.draw.rect(surface, grad_color(cb, ct, 0.0),
                             (x, col_top, w, cap_h),
                             border_top_left_radius=cap_r,
                             border_top_right_radius=cap_r)
        else:
            surface.blit(body, (x, col_top))
            pygame.draw.rect(surface, grad_color(cb, ct, 1.0),
                             (x, col_top + body_h, w, cap_h),
                             border_bottom_left_radius=cap_r,
                             border_bottom_right_radius=cap_r)
    else:
        body = pygame.Surface((w, col_h))
        pygame.transform.scale(_gradient_surface(cb, ct, w), (w, col_h), body)
        surface.blit(body, (x, col_top))


def draw_ui(surface, fps, nbars, capture, gate, size, scheme_name):
    font = pygame.font.Font(None, 24)
    small = pygame.font.Font(None, 20)

    if capture.error is not None:
        status, st_color = f"ERROR: {capture.error[:40]}", (255, 90, 90)
    elif gate < 0.5:
        status, st_color = "SILENT", (255, 200, 60)
    else:
        status, st_color = "LIVE", (90, 255, 90)

    dev = capture.device_name or "No device"
    if len(dev) > 42:
        dev = dev[:40] + "..."

    pad = 12
    box_w = 380
    box_h = 140
    bg = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 170))
    surface.blit(bg, (pad, pad))

    x = pad + 12
    y = pad + 10
    surface.blit(font.render(f"Status: {status}", True, st_color), (x, y)); y += 24
    surface.blit(small.render(f"Device: {dev}", True, (200, 210, 225)), (x, y)); y += 22
    surface.blit(small.render(f"FPS: {fps:.0f} | Bars: {nbars} | {RATE_DEFAULT // 1000}kHz | {scheme_name}", True, (200, 210, 225)), (x, y)); y += 22
    surface.blit(small.render(f"C color  +/- bars  R device  F11 fs  F UI  ESC quit", True, (140, 160, 200)), (x, y))
    _ = size


def main():
    global COLOR_SCHEMES
    try:
        COLOR_SCHEMES = load_color_schemes(resource_path("colors.txt"))
    except Exception as exc:
        print(f"Failed to load colors.txt: {exc}")
        sys.exit(1)
    enable_dpi_awareness()
    pygame.init()
    try:
        pygame.display.set_icon(pygame.image.load(resource_path("icon.png")))
    except Exception:
        pass
    pygame.display.set_caption("Audio Visualizer")
    width, height = 1280, 720
    screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
    set_dark_title_bar()
    window_size = (width, height)
    clock = pygame.time.Clock()

    window = np.hanning(FFT_N).astype(np.float32)
    capture = AudioCapture()

    nbars = DEFAULT_BARS
    show_ui = True
    fullscreen = False
    running = True
    scheme_index = 0

    smooth = np.zeros(nbars, dtype=np.float32)
    gate = 0.0
    opened = False
    noise_floor = 0.0
    gain = 0.0
    frame = 0

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_F11:
                    if not fullscreen:
                        window_size = screen.get_size()
                    fullscreen = not fullscreen
                    if fullscreen:
                        dw, dh = pygame.display.get_desktop_sizes()[0]
                        screen = pygame.display.set_mode((dw, dh), pygame.FULLSCREEN)
                    else:
                        screen = pygame.display.set_mode(window_size, pygame.RESIZABLE)
                        set_dark_title_bar()
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS):
                    nbars = min(nbars + 4, MAX_BARS)
                    smooth = np.zeros(nbars, dtype=np.float32)
                elif event.key == pygame.K_MINUS:
                    nbars = max(nbars - 4, MIN_BARS)
                    smooth = np.zeros(nbars, dtype=np.float32)
                elif event.key == pygame.K_f:
                    show_ui = not show_ui
                elif event.key == pygame.K_r:
                    capture.reopen()
                    smooth = np.zeros(nbars, dtype=np.float32)
                elif event.key == pygame.K_c:
                    scheme_index = (scheme_index + 1) % len(COLOR_SCHEMES)

        cur_w, cur_h = screen.get_size()
        screen.fill((4, 4, 9))

        history = capture.history()
        rms = float(np.sqrt(np.mean(history * history)))
        db = compute_bands(history, capture.rate, nbars, window)
        gate, noise_floor = silence_gate(gate, rms, noise_floor, opened)
        if gate > 0.5:
            opened = True
        if gate > 0.3:
            gain = auto_gain(db, gain)
        target = db_to_frac(db, gain)
        if target.shape[0] != smooth.shape[0]:
            smooth = np.zeros(nbars, dtype=np.float32)
        smooth_levels(smooth, target)
        if gate < 0.35:
            smooth *= 0.70
        levels = smooth * gate

        bar_gap = 2
        total = nbars * bar_gap
        bar_w = (cur_w - total) // nbars
        if bar_w < 2:
            bar_gap = 1
            total = nbars * bar_gap
            bar_w = (cur_w - total) // nbars
        if bar_w < 1:
            bar_w = 1
        bar_stride = bar_w + bar_gap
        screen_rect = pygame.Rect(0, 0, cur_w, cur_h)
        for i in range(nbars):
            lev = float(levels[i])
            hgt = int(round(lev * cur_h))
            if hgt < 1:
                continue
            if hgt > cur_h:
                hgt = cur_h
            x = min(i * bar_stride, cur_w - bar_w)
            scheme = COLOR_SCHEMES[scheme_index]
            top, bottom = scheme_colors(lev, i, scheme, frame)
            cap_r = min(bar_w // 2, 6)
            draw_gradient_column(screen, x, bar_w, cur_h - hgt, hgt,
                                 top, bottom, True, cap_r, screen_rect)

        if show_ui:
            draw_ui(screen, clock.get_fps(), nbars, capture, gate, (cur_w, cur_h),
                    COLOR_SCHEMES[scheme_index]["name"])

        pygame.display.flip()
        clock.tick(TARGET_FPS)
        frame += 1

    capture.close()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()