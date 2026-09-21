# MyVisualizer

A real-time audio spectrum visualizer that captures whatever your computer is playing and renders smooth, flat gradient bars in a fully integrated Pygame window.

Runs on **Windows** (WASAPI loopback) and **Linux** (PulseAudio/PipeWire monitor), and ships as a self-contained desktop app for both platforms.

---

## Features

- **Listen to system audio, not the microphone** — captures the actual output of your speakers/headphones.
- **Log-spaced frequency bars** — 8 to 128 bars (default 40), each band covering a constant musical interval.
- **Flat bars with per-pixel gradients** — numpy-driven column rendering with zero visible color banding.
- **10 built-in color schemes** — single-gradient (Blue, Red, Violet, Emerald, Amber, Teal, Pink, Gold) and two-tone animated (Neon, Sunset), with bar brightness scaled to level.
- **Auto-gain with silence gate** — bars ride the music and drop flat during silence, no manual tuning.
- **Fullscreen, resizable window, dark title bar** (Windows), and an optional on-screen UI overlay with live stats.
- **Single-file core** — no UI framework, no runtime config, no external assets.

| Key | Action |
|---|---|
| `F11` | Toggle fullscreen |
| `+` / `=` | More bars (up to 128) |
| `-` | Fewer bars (down to 8) |
| `F` | Toggle UI overlay |
| `R` | Reopen capture device |
| `C` | Cycle color schemes |

---

## Screenshots

<!-- Add a screenshot here, e.g. <img src="screenshot.png" width="640"> -->

---

## How It's Made

The entire app is a single Python file per platform. Three stages run per frame at 60 FPS:

```
audio capture  →  DSP pipeline  →  rendering
```

### 1. Audio capture

The app captures **system output** audio, not microphone input:

- **Windows** — `pyaudiowpatch` opens the default output device's **WASAPI loopback**, downmixing to mono and normalizing to `[-1, 1]`.
- **Linux** — a `parec` / `pw-cat` subprocess streams the default sink's `.monitor` source; if neither tool is present, it falls back to a `sounddevice` loopback device.

Captured samples land in a fixed-size ring buffer (`FFT_N = 2048`), read safely by the render thread under a lock.

### 2. DSP pipeline

- **FFT bands** — a 2048-point FFT with a Hann window splits `30 Hz – 18 kHz` into log-spaced bands; each band's mean magnitude is converted to dB.
- **Auto-gain** — drives the peak band toward `-8 dB` (fast attack, slow release) so levels stay consistent across tracks.
- **Silence gate** — tracks a slowly adapting noise floor with hysteresis, multiplying bar levels to zero during silence.
- **Smoothing** — asymmetric 1-pole smoothing (fast rise `0.70`, slow fall `0.90`) makes bars snap up and trail down naturally.

### 3. Rendering

- Pygame window at `1280×720` (resizable), dark background `(4, 4, 9)`.
- Each bar is rendered as a per-pixel vertical gradient using `numpy` + `pygame.surfarray` — one color per pixel row for banding-free gradients.
- Color schemes are defined in `colors.txt`, not in code: `<name> <bottom_hex> <top_hex> [alt_bottom_hex alt_top_hex]`.

---

## Project Structure

```
.
├── README.md               ← this file
├── .gitignore
├── linux/                  ← Linux edition
│   ├── visualizer.py       # entire app (capture, DSP, rendering)
│   ├── colors.txt          # color scheme definitions
│   ├── icon.png            # app icon
│   ├── make_icon.py        # regenerates the icon
│   ├── requirements.txt    # runtime dependencies
│   ├── run.sh              # run from source
│   ├── build.sh            # PyInstaller build
│   ├── MyVisualizer.spec   # PyInstaller config
│   ├── install.sh          # install to ~/.local + menu entry
│   ├── APP.md              # in-depth Linux docs
│   └── INSTALLER.md        # installer docs
└── windows/                ← Windows edition
    ├── visualizer.py       # entire app (capture, DSP, rendering)
    ├── colors.txt          # color scheme definitions
    ├── icon.ico / icon.png # app icons
    ├── make_icon.py        # regenerates the icons
    ├── build.bat           # PyInstaller build
    ├── MyVisualizer.spec   # PyInstaller config
    ├── install_script.iss  # Inno Setup installer script
    ├── APP.md              # in-depth Windows docs
    └── INSTALLER.md        # installer docs
```

---

## Getting Started

### Linux

Requires a running **PipeWire or PulseAudio** daemon. Optionally install `pulseaudio-utils` / `pipewire` for the primary capture path (a `sounddevice` loopback fallback is bundled).

```bash
cd linux
./run.sh          # creates .venv, installs deps, launches the app
```

Build a standalone bundle:

```bash
./build.sh        # outputs dist/MyVisualizer/
```

Install system-wide (current user):

```bash
./install.sh      # requires a completed build; adds a menu entry + `myvisualizer` command
```

### Windows

```bat
cd windows
build.bat         // creates .venv, installs deps, builds dist\MyVisualizer\
```

Package an installer with Inno Setup using `install_script.iss` (produces `MyVisualizer-Setup.exe`).

---

## Dependencies

| Library | Purpose |
|---|---|
| `pygame` | Windowing, rendering, event loop |
| `numpy` | Ring buffer, FFT, vectorized math |
| `sounddevice` | Loopback capture fallback (Linux) |
| `pyaudiowpatch` | WASAPI loopback capture (Windows) |
| `pyinstaller` | Bundling the app (build only) |

---

## Documentation

- [Linux deep-dive](linux/APP.md)
- [Windows deep-dive](windows/APP.md)
- [Windows installer notes](windows/INSTALLER.md)

---

## License

All rights reserved. © 2026 MUSTANSIRBM.