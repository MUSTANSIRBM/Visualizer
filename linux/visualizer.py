import math
import os
import shutil
import subprocess
import sys
import threading
import time

import numpy as np
import pygame
import sounddevice as sd

IS_WINDOWS = sys.platform.startswith("win")
if IS_WINDOWS:
    import ctypes

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
_ft_ready = False
_ft_mod = None


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
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def set_dark_title_bar():
    if not IS_WINDOWS:
        return
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
        self.chunk = CHUNK
        self.fft_n = FFT_N
        self.lock = threading.Lock()
        self.ring = None
        self.rate = RATE_DEFAULT
        self.device_name = None
        self.error = None
        self._stop = threading.Event()
        self._proc = None
        self._sstream = None
        self._thread = None
        self._make_stream()

    def _default_loopback(self):
        """Pick a sounddevice (PortAudio) device that records the system output.

        Preference order:
          1. A visible ALSA monitor that matches the pactl default sink.
          2. Any device whose name contains '.monitor'.
          3. The generic 'pipewire' / 'default' PortAudio device (on PipeWire
             this captures the default sink's loopback).
        """
        try:
            out = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
        except Exception:
            out = ""
        monitors = []
        default_match = None
        for dev in sd.query_devices():
            name = dev["name"]
            if dev.get("max_input_channels", 0) <= 0:
                continue
            if ".monitor" in name:
                monitors.append(dev)
                if out and (name == out + ".monitor" or out in name):
                    default_match = dev
        if default_match:
            return default_match
        if monitors:
            return monitors[0]
        for dev in sd.query_devices():
            name = dev["name"].lower()
            if dev.get("max_input_channels", 0) > 0 and name in ("default", "pipewire", "sysdefault"):
                return dev
        return None

    @staticmethod
    def _monitor_name():
        """Name of the default sink's monitor source, e.g. '<sink>.monitor'."""
        try:
            out = subprocess.run(
                ["pactl", "get-default-sink"],
                capture_output=True, text=True, timeout=2,
            ).stdout.strip()
        except Exception:
            return None
        return (out + ".monitor") if out else None

    @staticmethod
    def _capture_tool():
        for tool in ("parec", "pw-cat"):
            if shutil.which(tool):
                return tool
        return None

    @staticmethod
    def _spawn_capture(tool, monitor, rate):
        if tool == "parec":
            return subprocess.Popen(
                ["parec", f"--device={monitor}", "--format=float32le",
                 "--channels=1", f"--rate={rate}", "--latency-msec=50", "--raw"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        return subprocess.Popen(
            ["pw-cat", "--record", "--raw", "--channels=1", "--format=f32",
             f"--rate={rate}", "--latency=50", f"--target={monitor}", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)

    def _push(self, mono):
        if self.ring is None:
            self.ring = np.zeros(self.fft_n, dtype=np.float32)
        if mono.size >= self.fft_n:
            self.ring[:] = mono[-self.fft_n:]
        elif mono.size > 0:
            shift = mono.size
            self.ring[:-shift] = self.ring[shift:]
            self.ring[-shift:] = mono

    def _reader_loop(self):
        proc = self._proc
        step = self.fft_n * 4
        while not self._stop.is_set() and proc is not None:
            try:
                buf = proc.stdout.read(step)
            except Exception:
                buf = b""
            if buf:
                mono = np.frombuffer(buf, dtype=np.float32)
                with self.lock:
                    self._push(mono)
            elif proc.poll() is not None:
                err = proc.stderr.read(200).decode("utf-8", "replace").strip()
                with self.lock:
                    self.error = err or "audio capture process exited"
                break
            else:
                time.sleep(0.005)

    def _make_stream_sd(self):
        try:
            dev = self._default_loopback()
            if dev is None:
                raise RuntimeError("No loopback capture device found")
            self.device_name = dev["name"]
            self.rate = int(dev.get("default_samplerate") or RATE_DEFAULT)
            channels = int(dev.get("max_input_channels") or 2)

            def callback(indata, frames, time_info, status):
                mono = indata.mean(axis=1).astype(np.float32)
                with self.lock:
                    self._push(mono)
                return None

            self._sstream = sd.InputStream(
                device=int(dev["index"]),
                channels=channels,
                samplerate=self.rate,
                dtype="float32",
                blocksize=self.chunk,
                callback=callback,
            )
            self.error = None
        except Exception as exc:
            self._sstream = None
            self.error = f"{type(exc).__name__}: {exc}"

    def _make_stream(self):
        self._stop.clear()
        try:
            monitor = self._monitor_name()
            tool = self._capture_tool()
            if monitor and tool:
                proc = self._spawn_capture(tool, monitor, self.rate)
                time.sleep(0.25)
                if proc.poll() is not None:
                    err = proc.stderr.read(200).decode("utf-8", "replace").strip()
                    self.error = err or f"{tool} could not start"
                    self.device_name = monitor
                    self._proc = None
                    return
                self._proc = proc
                self.device_name = monitor
                self.error = None
                self._thread = threading.Thread(target=self._reader_loop, daemon=True)
                self._thread.start()
                return
            raise RuntimeError("No PulseAudio/PipeWire monitor found")
        except Exception as exc:
            self._proc = None
            self.error = f"{type(exc).__name__}: {exc}"
            self._make_stream_sd()

    def reopen(self):
        self.close()
        self._make_stream()

    def history(self):
        with self.lock:
            if self.ring is None:
                return np.zeros(self.fft_n, dtype=np.float32)
            return self.ring.copy()

    def close(self):
        self._stop.set()
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        proc.kill()
            except Exception:
                pass
        stream, self._sstream = self._sstream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
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


def _get_freetype():
    global _ft_ready, _ft_mod
    if not _ft_ready:
        from pygame import _freetype
        _ft_mod = _freetype
        if not _ft_mod.was_init():
            _ft_mod.init()
        _ft_ready = True
    return _ft_mod


def draw_ui(surface, fps, nbars, capture, gate, size, scheme_name):
    ft = _get_freetype()
    font = ft.Font(None, 24)
    small = ft.Font(None, 20)

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
    surface.blit(font.render(f"Status: {status}", st_color)[0], (x, y)); y += 24
    surface.blit(small.render(f"Device: {dev}", (200, 210, 225))[0], (x, y)); y += 22
    surface.blit(small.render(f"FPS: {fps:.0f} | Bars: {nbars} | {RATE_DEFAULT // 1000}kHz | {scheme_name}", (200, 210, 225))[0], (x, y)); y += 22
    surface.blit(small.render(f"C color  +/- bars  R device  F11 fs  F UI", (140, 160, 200))[0], (x, y))
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
                if event.key == pygame.K_F11:
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
            draw_gradient_column(screen, x, bar_w, cur_h - hgt, hgt, top, bottom)

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