import json
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

from source import SourceMonitor

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
TUI_FPS = 30
MIN_WIN_W = 360
MIN_WIN_H = 240

MODE_UP = 0
MODE_MIRROR = 1
MODE_RADIAL = 2
MODES = ["Bottom-Up", "Mirror", "Radial"]

COLOR_SCHEMES = []
_TUI_TTY = {"restore": None}
_BLACK = (0, 0, 0)
_ft_ready = False
_ft_mod = None


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
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        path = os.path.join(base, "MyVisualizer")
    else:
        path = os.path.join(os.path.expanduser("~"), ".config", "MyVisualizer")
    os.makedirs(path, exist_ok=True)
    path = os.path.join(path, "config.json")
    config_path._cached = path
    return path


def load_config():
    default_cfg = {
        "bars": DEFAULT_BARS,
        "scheme": None,
        "show_ui": True,
        "fullscreen": False,
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
    cfg["bars"] = max(MIN_BARS, min(MAX_BARS, int(cfg.get("bars", DEFAULT_BARS))))
    cfg["show_ui"] = bool(cfg.get("show_ui", True))
    cfg["fullscreen"] = bool(cfg.get("fullscreen", False))
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


def draw_bars(surface, mode, levels, scheme, frame):
    cur_w, cur_h = surface.get_size()
    nbars = levels.shape[0]

    if mode in (MODE_UP, MODE_MIRROR):
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
        cy = cur_h // 2
        max_h = int(cur_h * 0.92)
        max_uh = int(cy * 0.92)
        max_dh = int((cur_h - cy) * 0.92)
        for i in range(nbars):
            lev = float(levels[i])
            hgt = int(round(lev * cur_h))
            if hgt < 1:
                continue
            if hgt > max_h:
                hgt = max_h
            x = min(i * bar_stride, cur_w - bar_w)
            top, bottom = scheme_colors(lev, i, scheme, frame)
            if mode == MODE_UP:
                draw_gradient_column(surface, x, bar_w, cur_h - hgt, hgt, top, bottom)
            else:
                uh = min(hgt, max_uh)
                dh = min(hgt, max_dh)
                if uh > 0:
                    draw_gradient_column(surface, x, bar_w, cy - uh, uh, top, bottom)
                if dh > 0:
                    draw_gradient_column(surface, x, bar_w, cy, dh, bottom, top)

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


def _get_freetype():
    global _ft_ready, _ft_mod
    if not _ft_ready:
        from pygame import _freetype
        _ft_mod = _freetype
        if not _ft_mod.was_init():
            _ft_mod.init()
        _ft_ready = True
    return _ft_mod


def _trunc(text, font, max_w):
    if font.get_rect(text).width <= max_w:
        return text
    while text and font.get_rect(text + "\u2026").width > max_w:
        text = text[:-1]
    return text + "\u2026"


def draw_ui(surface, fps, nbars, capture, gate, size, scheme_name, mode_name,
            mode, source_snap):
    ft = _get_freetype()
    font = ft.Font(None, 24)
    small = ft.Font(None, 19)

    if capture.error is not None:
        status, st_color = f"ERROR: {capture.error[:40]}", (255, 90, 90)
    elif gate < 0.5:
        status, st_color = "SILENT", (255, 200, 60)
    else:
        status, st_color = "LIVE", (90, 255, 90)

    src_name, src_title, _ = source_snap
    cur_w, cur_h = size
    accent = (210, 220, 235)
    dim = (140, 155, 185)

    if mode == MODE_RADIAL:
        cx, cy = cur_w // 2, cur_h // 2
        base_r = max(24.0, min(cur_w, cur_h) * 0.14)
        inner_r = base_r * 0.82
        max_w = max(60, int(inner_r * 1.45))
        line_h = 19
        lines = []
        if src_name:
            peak = "  playing" if st_color == (90, 255, 90) else "  " + status.lower()
            lines.append((font, f"{src_name}{peak}", st_color))
            lines.append((small, _trunc(src_title or "", small, max_w), accent))
            if src_title:
                lines.append((small, status.lower(), dim))
        else:
            lines.append((font, "Nothing playing", dim))
            lines.append((small, status.lower(), dim))
        lines.append((small, f"{int(fps)} fps \u2022 {nbars} bars", accent))
        lines.append((small, f"{scheme_name} \u2022 {mode_name}", dim))
        lines.append((small,
                      "C col  M mode  +/- bars  F11 fs  F UI",
                      (108, 122, 152)))
        n = len(lines)
        hpad, vpad = 16, 8
        bbox_w = max(max_w + hpad * 2, 250)
        bbox_h = n * line_h + vpad * 2
        box = pygame.Surface((bbox_w, bbox_h), pygame.SRCALPHA)
        pygame.draw.rect(box, (0, 0, 0, 172), (0, 0, bbox_w, bbox_h),
                         border_radius=10)
        pygame.draw.rect(box, (90, 140, 200, 110), (0, 0, bbox_w, bbox_h),
                         width=1, border_radius=10)
        surface.blit(box, (cx - bbox_w // 2, cy - bbox_h // 2))
        y = cy - bbox_h // 2 + vpad
        for f, text, color in lines:
            w = f.get_rect(text).width
            x = cx - w // 2
            surface.blit(f.render(text, color)[0], (x, y))
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
        surface.blit(font.render(src_name, st_color)[0], (x, y)); y += gap_line
        surface.blit(small.render(_trunc(src_title or "playing\u2026", small, box_w - 28),
                                  accent)[0], (x, y)); y += gap_line
        surface.blit(small.render(f"now playing \u00b7 {status.lower()} \u2022 {scheme_name} \u2022 {mode_name}",
                                  dim)[0], (x, y)); y += gap_line
    else:
        surface.blit(font.render("Nothing playing", dim)[0], (x, y)); y += gap_line
        surface.blit(small.render(f"{status.lower()} \u2022 {scheme_name} \u2022 {mode_name}",
                                  dim)[0], (x, y)); y += gap_line
    surface.blit(small.render(f"{fps:.0f} fps \u2022 {nbars} bars \u2022 C col  M mode  +/- bars  F11 fs  F UI",
                              (120, 135, 165))[0], (x, y))


def _tui_write(s):
    try:
        sys.stdout.write(s)
        sys.stdout.flush()
    except Exception:
        pass


def _tui_size():
    try:
        size = shutil.get_terminal_size()
        return max(20, size.columns), max(8, size.lines)
    except Exception:
        return 80, 24


def _tui_enable():
    if os.name != "nt":
        return
    try:
        import ctypes
        h = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass


def _tui_init_raw():
    if os.name == "nt":
        return
    try:
        import termios
        import tty
        _TUI_TTY["restore"] = termios.tcgetattr(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
    except Exception:
        _TUI_TTY["restore"] = None


def _tui_restore_raw():
    if _TUI_TTY["restore"] is not None:
        try:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _TUI_TTY["restore"])
        except Exception:
            pass


def _tui_get_key():
    if os.name == "nt":
        try:
            import msvcrt
            if not msvcrt.kbhit():
                return None
            ch = msvcrt.getwch()
            if ch in ("\x03", "\x1a"):
                return "q"
            return ch.lower()
        except Exception:
            return None
    try:
        import select
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1).lower()
        return None
    except Exception:
        return None


def _tui_fg(c):
    return f"\x1b[38;2;{c[0]};{c[1]};{c[2]}m"


def _tui_bg(c):
    return f"\x1b[48;2;{c[0]};{c[1]};{c[2]}m"


def _tui_px(bottom, top, idx, lit):
    f = idx / max(lit - 1, 1)
    return (
        int(bottom[0] + (top[0] - bottom[0]) * f),
        int(bottom[1] + (top[1] - bottom[1]) * f),
        int(bottom[2] + (top[2] - bottom[2]) * f),
    )


def _tui_frame(cols, rows, levels, scheme, frame, capture, gate, fps, source_snap=()):
    nbars = levels.shape[0]
    row_h = max(1, rows - 1)
    maxh = max(2, row_h * 2)
    bar_w = 1
    lines = []
    for line_no in range(row_h):
        up_px = 2 * (row_h - 1 - line_no)
        cells = []
        for i in range(nbars):
            lev = float(levels[i])
            lit = max(0, min(maxh, int(round(lev * maxh))))
            top, bottom = scheme_colors(lev, i, scheme, frame)
            uc = _tui_px(bottom, top, up_px, lit) if up_px < lit else _BLACK
            lc = _tui_px(bottom, top, up_px + 1, lit) if (up_px + 1) < lit else _BLACK
            if uc != _BLACK and lc != _BLACK:
                cell = "\u2588" * bar_w
            elif uc != _BLACK:
                cell = "\u2580" * bar_w
            elif lc != _BLACK:
                cell = "\u2584" * bar_w
            else:
                cell = " " * bar_w
            cells.append(_tui_fg(uc) + _tui_bg(lc) + cell + "\x1b[0m ")
        lines.append("".join(cells))
    if capture.error is not None:
        status = "ERROR"
    elif gate < 0.5:
        status = "SILENT"
    else:
        status = "LIVE"
    src_name, src_title, _ = source_snap
    now = ""
    if src_name:
        now = f" | Now: {src_name}"
        if src_title:
            now += f" \u2013 {src_title}"
    footer = (f"MyVisualizer TUI | {scheme['name']} | {status} | {fps:.0f} fps | "
              f"q/Esc quit  C color")
    footer = (footer + now + " " * 4)[:cols]
    footer = footer.ljust(cols)
    out = "\x1b[H" + "\n".join(lines) + "\x1b[0m\n" + footer + "\x1b[0m"
    return out


def main_terminal():
    global COLOR_SCHEMES
    if not sys.stdout.isatty():
        print("Terminal mode requires an interactive terminal (a real TTY).",
              file=sys.stderr)
        sys.exit(1)
    if not COLOR_SCHEMES:
        try:
            COLOR_SCHEMES = load_color_schemes(resource_path("colors.txt"))
        except Exception as exc:
            print(f"Failed to load colors.txt: {exc}")
            sys.exit(1)
    cfg = load_config()
    names = [s["name"] for s in COLOR_SCHEMES]
    scheme_index = names.index(cfg.get("scheme")) if cfg.get("scheme") in names else 0

    capture = AudioCapture()
    source_mon = SourceMonitor()
    window = np.hanning(FFT_N).astype(np.float32)
    st = DspState(MIN_BARS)

    _tui_enable()
    _tui_init_raw()
    try:
        _tui_write("\x1b[?1049h\x1b[?25l")
        cols, rows = _tui_size()
        nbars = max(MIN_BARS, min(MAX_BARS, (cols - 1) // 2))
        st = DspState(nbars)
        last_size = (cols, rows)
        cleared = True
        clock = time.time()
        frame = 0
        while True:
            key = _tui_get_key()
            if key in ("q", "\x1b", "x"):
                break
            if key == "c":
                scheme_index = (scheme_index + 1) % len(COLOR_SCHEMES)
                cfg["scheme"] = COLOR_SCHEMES[scheme_index]["name"]
                save_config(cfg)
            cur = _tui_size()
            if cur != last_size:
                cols, rows = cur
                nbars = max(MIN_BARS, min(MAX_BARS, (cols - 1) // 2))
                st = DspState(nbars)
                last_size = cur
                cleared = True
            history = capture.history()
            levels = analyze(history, capture.rate, nbars, window, st)
            payload = _tui_frame(cols, rows, levels, COLOR_SCHEMES[scheme_index],
                                 frame, capture, st.gate, TUI_FPS,
                                 source_mon.snapshot())
            _tui_write(("\x1b[2J" if cleared else "") + payload)
            cleared = False
            frame += 1
            target = 1.0 / TUI_FPS
            dt = time.time() - clock
            if dt < target:
                time.sleep(target - dt)
            clock = time.time()
    finally:
        _tui_write("\x1b[0m\x1b[?25h\x1b[?1049l")
        _tui_restore_raw()
        capture.close()
        source_mon.close()


def main():
    global COLOR_SCHEMES
    try:
        COLOR_SCHEMES = load_color_schemes(resource_path("colors.txt"))
    except Exception as exc:
        print(f"Failed to load colors.txt: {exc}")
        sys.exit(1)
    if "--terminal" in sys.argv or "--tui" in sys.argv:
        main_terminal()
        return
    if "--gui" not in sys.argv:
        try:
            tty = sys.stdout.isatty()
        except Exception:
            tty = False
        if tty:
            main_terminal()
            return

    enable_dpi_awareness()
    pygame.init()
    pygame.key.set_repeat(400, 40)

    cfg = load_config()
    nbars = cfg["bars"]
    scheme_name = cfg.get("scheme")
    scheme_names = [s["name"] for s in COLOR_SCHEMES]
    scheme_index = scheme_names.index(scheme_name) if scheme_name in scheme_names else 0
    show_ui = cfg["show_ui"]
    fullscreen = False
    mode = cfg["mode"]
    width, height = cfg["width"], cfg["height"]

    try:
        pygame.display.set_icon(pygame.image.load(resource_path("icon.png")))
    except Exception:
        pass
    pygame.display.set_caption("Audio Visualizer")
    if fullscreen:
        dw, dh = pygame.display.get_desktop_sizes()[0]
        screen = pygame.display.set_mode((dw, dh), pygame.NOFRAME)
        try:
            pygame.display.set_window_position((0, 0))
        except Exception:
            pass
    else:
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
    last_f11 = 0.0

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
                if not fullscreen and (ew, eh) != screen.get_size():
                    window_size = (ew, eh)
                    screen = pygame.display.set_mode((ew, eh), pygame.RESIZABLE)
                    set_dark_title_bar()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_F11:
                    now = time.monotonic()
                    if now - last_f11 < 0.5:
                        continue
                    last_f11 = now
                    if not fullscreen:
                        window_size = screen.get_size()
                    fullscreen = not fullscreen
                    if fullscreen:
                        dw, dh = pygame.display.get_desktop_sizes()[0]
                        screen = pygame.display.set_mode((dw, dh), pygame.NOFRAME)
                        try:
                            pygame.display.set_window_position((0, 0))
                        except Exception:
                            pass
                    else:
                        dw, dh = pygame.display.get_desktop_sizes()[0]
                        ww, wh = window_size
                        screen = pygame.display.set_mode(
                            (min(ww, dw), min(wh, dh)), pygame.RESIZABLE)
                        set_dark_title_bar()
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
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

        cur_w, cur_h = screen.get_size()
        screen.fill((4, 4, 9))

        history = capture.history()
        levels = analyze(history, capture.rate, nbars, window, st)
        draw_bars(screen, mode, levels, COLOR_SCHEMES[scheme_index], frame)

        if show_ui:
            draw_ui(screen, clock.get_fps(), nbars, capture, st.gate, (cur_w, cur_h),
                    COLOR_SCHEMES[scheme_index]["name"], MODES[mode], mode,
                    source_mon.snapshot())

        pygame.display.flip()
        clock.tick(TARGET_FPS)
        frame += 1

    cfg["bars"] = nbars
    cfg["scheme"] = COLOR_SCHEMES[scheme_index]["name"]
    cfg["show_ui"] = show_ui
    cfg["fullscreen"] = fullscreen
    cfg["mode"] = mode
    if not fullscreen:
        cfg["width"], cfg["height"] = screen.get_size()
    save_config(cfg)
    capture.close()
    source_mon.close()
    pygame.quit()
    sys.exit(0)


if __name__ == "__main__":
    main()