import argparse
import ctypes
import json
import math
import os
import sys
import threading
import time

import numpy as np
import pygame
import pyaudiowpatch as pyaudio

from source import SourceMonitor

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
MIN_WIN_W = 360
MIN_WIN_H = 240

MODE_UP = 0
MODE_RADIAL = 1
MODES = ["Bottom-Up", "Radial"]

COLOR_SCHEMES = []


class DspState:
    def __init__(self, nbars):
        self.nbars = nbars
        self.smooth = np.zeros(nbars, dtype=np.float32)
        self.gate = 0.0
        self.opened = False
        self.noise_floor = 0.0
        self.gain = 0.0


def parse_hex(h):
    h = h.strip().lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def resource_path(name):
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def config_path():
    cached = getattr(config_path, "_cached", None)
    if cached:
        return cached
    base = os.environ.get("APPDATA")
    if base:
        path = os.path.join(base, "MyVisualizer")
    else:
        path = os.path.join(os.path.expanduser("~"), ".myvisualizer")
    os.makedirs(path, exist_ok=True)
    path = os.path.join(path, "config.json")
    config_path._cached = path
    return path


def load_config():
    default_cfg = {
        "bars": DEFAULT_BARS,
        "scheme": None,
        "show_ui": True,
        "width": 1280,
        "height": 720,
        "mode": 0,
    }
    cfg = dict(default_cfg)
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            cfg.update(stored)
    except FileNotFoundError:
        pass
    except Exception as exc:
        print(f"Failed to load config: {exc}")
    cfg.pop("fullscreen", None)
    cfg["bars"] = max(MIN_BARS, min(MAX_BARS, int(cfg.get("bars", DEFAULT_BARS))))
    cfg["show_ui"] = bool(cfg.get("show_ui", True))
    cfg["mode"] = max(0, min(len(MODES) - 1, int(cfg.get("mode", 0))))
    try:
        cfg["width"] = max(320, int(cfg.get("width")))
        cfg["height"] = max(240, int(cfg.get("height")))
    except Exception:
        cfg["width"], cfg["height"] = default_cfg["width"], default_cfg["height"]
    return cfg


def save_config(cfg):
    path = config_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, path)
    except Exception as exc:
        print(f"Failed to save config: {exc}")


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


def set_taskbar_identify():
    try:
        app_id = ctypes.create_unicode_buffer("MyVisualizer.AudioVisualizer.1")
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
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


def analyze(history, rate, nbars, window, st):
    rms = float(np.sqrt(np.mean(history * history)))
    db = compute_bands(history, rate, nbars, window)
    st.gate, st.noise_floor = silence_gate(st.gate, rms, st.noise_floor, st.opened)
    if st.gate > 0.5:
        st.opened = True
    if st.gate > 0.3:
        st.gain = auto_gain(db, st.gain)
    target = db_to_frac(db, st.gain)
    if target.shape[0] != st.smooth.shape[0]:
        st.smooth = np.zeros(nbars, dtype=np.float32)
    smooth_levels(st.smooth, target)
    if st.gate < 0.35:
        st.smooth *= 0.70
    return st.smooth * st.gate


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


def _gradient_surface(cb, ct, w, h):
    t = np.linspace(0.0, 1.0, h, dtype=np.float32)
    rows = np.stack(
        [cb[0] + (ct[0] - cb[0]) * t,
         cb[1] + (ct[1] - cb[1]) * t,
         cb[2] + (ct[2] - cb[2]) * t],
        axis=-1,
    ).astype(np.uint8)
    arr = np.ascontiguousarray(np.repeat(rows[:, None, :], w, axis=1).transpose(1, 0, 2))
    return pygame.surfarray.make_surface(arr)


def draw_gradient_column(surface, x, w, col_top, col_h, cb, ct):
    if col_h <= 0 or w <= 0:
        return
    surface.blit(_gradient_surface(cb, ct, w, col_h), (x, col_top))


def linear_bar_rects(nbars, i, hgt, size):
    cur_w, cur_h = size
    bar_gap = 2
    bar_w = (cur_w - nbars * bar_gap) // nbars
    if bar_w < 2:
        bar_gap = 1
        bar_w = (cur_w - nbars * bar_gap) // nbars
    if bar_w < 1:
        bar_w = 1
        bar_gap = 1
    bar_stride = bar_w + bar_gap
    hgt = max(0, min(hgt, int(cur_h * 0.92)))
    x = min(i * bar_stride, cur_w - bar_w)
    return [(x, cur_h - hgt, bar_w, hgt)]


def draw_bars(surface, mode, levels, scheme, frame):
    cur_w, cur_h = surface.get_size()
    nbars = levels.shape[0]

    if mode == MODE_UP:
        for i in range(nbars):
            lev = float(levels[i])
            hgt = int(round(lev * cur_h))
            if hgt < 1:
                continue
            top, bottom = scheme_colors(lev, i, scheme, frame)
            for x, col_top, bw, hh in linear_bar_rects(nbars, i, hgt, (cur_w, cur_h)):
                draw_gradient_column(surface, x, bw, col_top, hh, top, bottom)

    else:  # MODE_RADIAL
        cx, cy = cur_w // 2, cur_h // 2
        base_r = max(24.0, min(cur_w, cur_h) * 0.14)
        max_r = max(base_r + 12.0, min(cur_w, cur_h) * 0.48)
        step = 2.0 * math.pi / nbars
        pygame.draw.circle(surface, (66, 66, 96), (cx, cy), int(base_r), 2)
        for i in range(nbars):
            lev = float(levels[i])
            if lev <= 0.02:
                continue
            length = (max_r - base_r) * lev
            ang = -math.pi / 2 + i * step
            half = step * 0.38
            a1, a2 = ang - half, ang + half
            bx1 = cx + math.cos(a1) * base_r
            by1 = cy + math.sin(a1) * base_r
            bx2 = cx + math.cos(a2) * base_r
            by2 = cy + math.sin(a2) * base_r
            tx1 = cx + math.cos(a1) * (base_r + length)
            ty1 = cy + math.sin(a1) * (base_r + length)
            tx2 = cx + math.cos(a2) * (base_r + length)
            ty2 = cy + math.sin(a2) * (base_r + length)
            top, _ = scheme_colors(lev, i, scheme, frame)
            pygame.draw.polygon(
                surface, top, [(bx1, by1), (bx2, by2), (tx2, ty2), (tx1, ty1)]
            )
            mx, my = (tx1 + tx2) / 2.0, (ty1 + ty2) / 2.0
            tip_w = math.hypot(tx2 - tx1, ty2 - ty1)
            pygame.draw.circle(surface, top, (int(mx), int(my)), max(1, int(tip_w / 2)))
        pygame.draw.circle(surface, (8, 8, 18), (cx, cy), int(base_r * 0.82))


def _trunc(text, font, max_w):
    if font.size(text)[0] <= max_w:
        return text
    while text and font.size(text + "\u2026")[0] > max_w:
        text = text[:-1]
    return text + "\u2026"


def draw_ui(surface, fps, nbars, capture, gate, size, scheme_name, mode_name,
            mode, source_snap):
    font = pygame.font.Font(None, 24)
    small = pygame.font.Font(None, 19)

    if capture.error is not None:
        status, st_color = f"ERROR: {capture.error[:40]}", (255, 90, 90)
    elif gate < 0.5:
        status, st_color = "SILENT", (255, 200, 60)
    else:
        status, st_color = "LIVE", (90, 255, 90)

    src_name, _, _ = source_snap
    cur_w, cur_h = size
    accent = (210, 220, 235)
    dim = (140, 155, 185)

    if mode == MODE_RADIAL:
        cx, cy = cur_w // 2, cur_h // 2
        base_r = max(24.0, min(cur_w, cur_h) * 0.14)
        small = pygame.font.Font(None, 19)
        head_f = pygame.font.Font(None, 24)
        tiny = pygame.font.Font(None, 15)
        R = max(36, int(base_r * 0.97))
        line_h = 17
        lines = []
        if src_name:
            peak = "  playing" if st_color == (90, 255, 90) else "  " + status.lower()
            lines.append((head_f, f"{src_name}{peak}", st_color))
        else:
            lines.append((head_f, "Nothing playing", dim))
        lines.append((small, f"{status.lower()} \u2022 {scheme_name} \u2022 {mode_name}", dim))
        lines.append((small, f"{int(fps)} fps \u2022 {nbars} bars", accent))
        lines.append((tiny, "C col  M mode  +/- bars  F UI", (108, 122, 152)))
        n = len(lines)
        bubble = pygame.Surface((R * 2, R * 2), pygame.SRCALPHA)
        pygame.draw.circle(bubble, (0, 0, 0, 168), (R, R), R)
        pygame.draw.circle(bubble, (90, 140, 200, 110), (R, R), R, 1)
        surface.blit(bubble, (cx - R, cy - R))
        y = cy - (n * line_h) // 2
        for f, text, color in lines:
            dy = (y + line_h // 2) - cy
            chord = int(2.0 * math.sqrt(max(1.0, R * R - dy * dy))) - 14
            text = _trunc(text or "", f, max(40, chord))
            w = f.size(text)[0]
            surface.blit(f.render(text, True, color), (cx - w // 2, y))
            y += line_h
        return

    pad = 12
    gap_line = 20
    box_w = 360
    box_h = 112
    bg = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 160))
    surface.blit(bg, (pad, pad))

    x = pad + 14
    y = pad + 10
    if src_name:
        surface.blit(font.render(src_name, True, st_color), (x, y)); y += gap_line
        surface.blit(small.render(f"now playing \u00b7 {status.lower()} \u2022 {scheme_name} \u2022 {mode_name}",
                                  True, dim), (x, y)); y += gap_line
    else:
        surface.blit(font.render("Nothing playing", True, dim), (x, y)); y += gap_line
        surface.blit(small.render(f"{status.lower()} \u2022 {scheme_name} \u2022 {mode_name}",
                                  True, dim), (x, y)); y += gap_line
    surface.blit(small.render(f"{fps:.0f} fps \u2022 {nbars} bars \u2022 C col  M mode  +/- bars  F UI",
                              True, (120, 135, 165)), (x, y))


def main():
    global COLOR_SCHEMES
    parser = argparse.ArgumentParser(
        description="MyVisualizer - real-time audio spectrum visualizer.")
    parser.add_argument("--verbose", action="store_true",
                        help="print live status lines to stdout")
    parser.add_argument("--bars", type=int, default=None,
                        help=f"initial number of bars ({MIN_BARS}-{MAX_BARS}); overrides saved setting")
    args = parser.parse_args()

    try:
        COLOR_SCHEMES = load_color_schemes(resource_path("colors.txt"))
    except Exception as exc:
        print(f"Failed to load colors.txt: {exc}")
        sys.exit(1)

    verbose = args.verbose
    enable_dpi_awareness()
    pygame.init()
    pygame.key.set_repeat(400, 40)

    cfg = load_config()
    nbars = max(MIN_BARS, min(MAX_BARS, args.bars if args.bars is not None else cfg["bars"]))
    scheme_name = cfg.get("scheme")
    scheme_names = [s["name"] for s in COLOR_SCHEMES]
    scheme_index = scheme_names.index(scheme_name) if scheme_name in scheme_names else 0
    show_ui = cfg["show_ui"]
    mode = cfg["mode"]
    width, height = cfg["width"], cfg["height"]

    set_taskbar_identify()
    try:
        pygame.display.set_icon(pygame.image.load(resource_path("icon.png")))
    except Exception:
        pass
    pygame.display.set_caption("Audio Visualizer")
    dw, dh = pygame.display.get_desktop_sizes()[0]
    width = min(width, dw)
    height = min(height, dh)
    screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
    set_dark_title_bar()
    window_size = screen.get_size()
    clock = pygame.time.Clock()

    window = np.hanning(FFT_N).astype(np.float32)
    capture = AudioCapture()
    source_mon = SourceMonitor()

    running = True
    st = DspState(nbars)
    frame = 0
    pending_size = None

    def persist(key, value):
        cfg[key] = value
        save_config(cfg)

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type in (pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED):
                if event.type == pygame.VIDEORESIZE:
                    ew, eh = event.w, event.h
                else:
                    ew, eh = event.x, event.y
                ew = max(ew, MIN_WIN_W)
                eh = max(eh, MIN_WIN_H)
                if (ew, eh) != screen.get_size():
                    pending_size = (ew, eh)
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    nbars = min(nbars + 4, MAX_BARS)
                    st = DspState(nbars)
                    persist("bars", nbars)
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    nbars = max(nbars - 4, MIN_BARS)
                    st = DspState(nbars)
                    persist("bars", nbars)
                elif event.key == pygame.K_m:
                    mode = (mode + 1) % len(MODES)
                    persist("mode", mode)
                elif event.key == pygame.K_f:
                    show_ui = not show_ui
                    persist("show_ui", show_ui)
                elif event.key == pygame.K_r:
                    capture.reopen()
                    st = DspState(nbars)
                elif event.key == pygame.K_c:
                    scheme_index = (scheme_index + 1) % len(COLOR_SCHEMES)
                    persist("scheme", COLOR_SCHEMES[scheme_index]["name"])

        if pending_size is not None:
            window_size = pending_size
            screen = pygame.display.set_mode(pending_size, pygame.RESIZABLE)
            pending_size = None

        cur_w, cur_h = screen.get_size()
        screen.fill((4, 4, 9))

        history = capture.history()
        levels = analyze(history, capture.rate, nbars, window, st)
        draw_bars(screen, mode, levels, COLOR_SCHEMES[scheme_index], frame)

        if show_ui:
            draw_ui(screen, clock.get_fps(), nbars, capture, st.gate, (cur_w, cur_h),
                    COLOR_SCHEMES[scheme_index]["name"], MODES[mode], mode,
                    source_mon.snapshot())

        if verbose and frame % 60 == 0:
            status = "ERROR" if capture.error else ("SILENT" if st.gate < 0.5 else "LIVE")
            err = f" [{capture.error}]" if capture.error else ""
            print(f"[{status}] fps={clock.get_fps():5.1f} bars={nbars:3d} "
                  f"mode={MODES[mode]} "
                  f"src={capture.device_name} scheme={COLOR_SCHEMES[scheme_index]['name']}{err}", flush=True)

        pygame.display.flip()
        clock.tick(TARGET_FPS)
        frame += 1

    cfg["bars"] = nbars
    cfg["scheme"] = COLOR_SCHEMES[scheme_index]["name"]
    cfg["show_ui"] = show_ui
    cfg["mode"] = mode
    cfg["width"], cfg["height"] = screen.get_size()
    save_config(cfg)
    capture.close()
    source_mon.close()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()