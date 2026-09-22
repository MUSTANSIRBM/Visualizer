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
- **Now Playing detector** — shows the app that's actually making sound (via WASAPI peak + Windows SMTC media sessions).
- **Resizable window, dark title bar** (Windows), and an optional on-screen UI overlay with live stats.
- **Single-file core** — no UI framework, no runtime config, no external assets.

Display modes: **Bottom-Up** (default) and **Radial** spiral (press `M`).

| Key | Action |
|---|---|
| `M` | Cycle display mode (Bottom-Up / Radial) |
| `+` / `=` | More bars (up to 128) |
| `-` | Fewer bars (down to 8) |
| `F` | Toggle UI overlay |
| `R` | Reopen capture device |
| `C` | Cycle color schemes |

---

## Download & Install

### Windows

- **Download** — grab the latest installer from [GitHub Releases](https://github.com/MUSTANSIRBM/Visualizer/releases/latest/download/MyVisualizer-Setup.exe)
  (or [download the exe directly from the repo](https://github.com/MUSTANSIRBM/Visualizer/blob/main/windows/installer/MyVisualizer-Setup.exe)).
- **Install** — run `MyVisualizer-Setup.exe` and click through the wizard:
  1. It installs to `%LOCALAPPDATA%\Programs\MyVisualizer` (no admin needed).
  2. Start Menu entry is created; tick "desktop icon" if you want one.
  3. Launch from the Start Menu, desktop icon, or the "Run now" checkbox at the end.
- **Uninstall** — Windows Settings → Apps → MyVisualizer.
- **Build from source instead** — see [Getting Started](#getting-started) → Windows.

### Linux

No installer package yet — build and install from source:

1. Make sure **PipeWire or PulseAudio** is running. Optionally install `pulseaudio-utils` / `pipewire` for the primary capture path (a `sounddevice` loopback fallback is bundled).
2. Run once from source:

   ```bash
   cd linux
   ./run.sh        # creates .venv, installs deps, launches the app
   ```

3. Or build a standalone bundle and install it system-wide (current user):

   ```bash
   ./build.sh      # outputs dist/MyVisualizer/
   ./install.sh    # installs to ~/.local/share + app-menu entry + `myvisualizer` launcher
   ```

4. Launch from your app menu, or run `myvisualizer`.

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
    │   ├── source.py           # Now Playing detector
    │   ├── colors.txt          # color scheme definitions
    │   ├── icon.png            # app icon
    │   ├── make_icon.py        # regenerates the icon
    │   ├── requirements.txt    # runtime dependencies
    │   ├── run.sh              # run from source
    │   ├── build.sh            # PyInstaller build
    │   ├── MyVisualizer.spec   # PyInstaller config
    │   ├── install.sh          # install to ~/.local + menu entry
    │   └── APP.md              # in-depth Linux docs
└── windows/                ← Windows edition
    ├── visualizer.py       # entire app (capture, DSP, rendering)
    ├── source.py           # Now Playing detector (pycaw + winsdk SMTC)
    ├── colors.txt          # color scheme definitions
    ├── icon.ico / icon.png # app icons
    ├── make_icon.py        # regenerates the icons
    ├── test_bounds.py      # border-pixel guard (run before every build)
    ├── requirements.txt    # runtime dependencies
    ├── run.bat             # run from source (creates .venv, installs deps)
    ├── build.bat           # bounds test + PyInstaller build
    ├── MyVisualizer.spec   # PyInstaller config
    ├── install_script.iss  # Inno Setup installer script
    └── APP.md              # in-depth Windows docs
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
run.bat           :: creates .venv, installs deps, launches the app from source
```

Build a standalone bundle (runs the border-pixel guard first):

```bat
build.bat         :: outputs dist\MyVisualizer\
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
| `pycaw` / `comtypes` | Audio-session peak detection (Windows) |
| `winsdk` | Windows Media Transport Controls / SMTC (Windows) |
| `pyinstaller` | Bundling the app (build only) |

---

## Documentation

- [Linux deep-dive](linux/APP.md)
- [Windows deep-dive](windows/APP.md)