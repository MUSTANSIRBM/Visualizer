# MyVisualizer (Linux) — How the App Works

A real-time audio spectrum visualizer for Linux. It captures whatever the computer is playing (system output audio via a PulseAudio/PipeWire monitor or a loopback device), converts it to frequency bands with an FFT, and renders smooth, flat gradient bars in a Pygame window.

## Layout

| File | Purpose |
|---|---|
| `visualizer.py` | Entire app: audio capture, DSP, rendering, input handling |
| `colors.txt` | Color scheme definitions (hex), loaded at startup |
| `icon.png` | App icon (window + installer/desktop icon) |
| `make_icon.py` | Regenerates the icon (PNG) |
| `requirements.txt` | Runtime dependencies (`numpy`, `pygame`, `sounddevice`) |
| `run.sh` | Creates `.venv` if needed and runs the app from source |
| `build.sh` | Builds `dist/MyVisualizer/` with PyInstaller |
| `MyVisualizer.spec` | PyInstaller config (bundles `sounddevice`, `colors.txt`, `icon.png`) |
| `install.sh` | Installs the built app to `~/.local/` with a launcher + desktop entry |

Single-file app — no UI framework, no config files, no external assets at runtime.

## System Requirements

- A running **PipeWire or PulseAudio** daemon (almost every modern desktop has one).
- Optional command-line tools (used for the primary capture path):
  - `parec` (from `pulseaudio-utils`)
  - `pw-cat` (from `pipewire`)
  - `pactl` (from `pulseaudio-utils` or part of PipeWire's `pipewire-pulse`)
  If none are present, the app falls back to recording a loopback device via the `sounddevice` library.

## Dependencies

- `pygame` — windowing, rendering, event loop
- `numpy` — ring buffer, FFT, vectorized math
- `sounddevice` — PortAudio bindings, used as loopback capture fallback
- `pyinstaller` (build only)

## Runtime Flow (`main()`)

```
main()
├─ pygame.init()                 # 1280x720 resizable window
├─ window = np.hanning(FFT_N)    # FFT window function
├─ capture = AudioCapture()      # open monitor/loopback stream
└─ loop:
   ├─ handle events (keys, F11, resize, window close)
   ├─ history = capture.history()     # latest FFT_N audio samples
   ├─ rms = sqrt(mean(history^2))     # loudness estimate
   ├─ db = compute_bands(history, ...)# FFT -> log-spaced dB bands
   ├─ gate, floor = silence_gate(...) # open/close silence gate
   ├─ gain = auto_gain(db, gain)      # normalize to DB_HEAD
   ├─ target = db_to_frac(db, gain)   # dB -> 0..1 bar target
   ├─ smooth_levels(smooth, target)   # attack/release smoothing
   ├─ levels = smooth * gate          # silence suppression
   ├─ draw bars (flat gradient columns)
   ├─ draw UI overlay (if shown)
   └─ flip, tick(60)
```

## Audio Capture (`AudioCapture`)

Capture works in two layers:

### Primary path — sink monitor via `parec` / `pw-cat`
1. `_monitor_name()` asks `pactl get-default-sink` and appends `.monitor`, e.g. `alsa_output.pci-0000_00_1f.3.analog-stereo.monitor` — the capture source for the computer's speakers.
2. `_capture_tool()` prefers `parec`, else `pw-cat`.
3. `_spawn_capture()` launches the tool to stream **raw float32 mono** at the app's sample rate into a pipe:
   - `parec --device=<monitor> --format=float32le --channels=1 --rate=<rate> ... --raw`
   - `pw-cat --record --raw --channels=1 --format=f32 --rate=<rate> --target=<monitor> -`
4. A reader thread (`_reader_loop`) consumes the pipe, decodes float32 samples, and pushes them into a fixed ring buffer (`ring`, size `FFT_N = 2048`) under a lock.

### Fallback — loopback device via `sounddevice`
If no monitor + tool is available (or the process dies), `_make_stream_sd()` picks a recording device:
1. An `alsa_input...monitor` device that matches the `pactl` default sink,
2. otherwise any device whose name contains `.monitor`,
3. otherwise the generic `pipewire` / `default` PortAudio device (which loopbacks the default sink on PipeWire).

It opens a `sounddevice.InputStream` whose callback downmixes to mono and pushes into the ring buffer.

- `history()` returns a copy of the ring under a lock (safe for the render thread).
- `reopen()` (key `R`) tears down and rebuilds the capture path (both layers).
- Failures are stored in `self.error` and surfaced in the UI as an error status.

## DSP Pipeline

### Frequency bands (`compute_bands`)
- Computes `abs(rfft(history * hanning))`.
- Frequency range: `MIN_FREQ=30Hz` to `min(MAX_FREQ=18kHz, rate*0.45)` (Nyquist-ish).
- Bins are **log-spaced** edges (`np.linspace(log(lo), log(hi), nbars+1)`) so each band covers a constant musical interval — classic for audio bars.
- Each band = mean magnitude over its FFT bins, normalized by `FFT_N/2`, converted to dB (`20*log10`).

### Auto-gain (`auto_gain`)
- Drives the peak band toward `DB_HEAD = -8dB`.
- Fast attack (`GAIN_ATTACK 0.08`) when below target, slow release (`0.015`) above — the level rides the music without pumping on quiet gaps.

### Silence gate (`silence_gate`)
- Tracks a slowly adapting noise floor.
- Gate opens when RMS exceeds `max(floor * 1.5, FLOOR_MIN)`; opens fast, decays slowly.
- While open it keeps adapting the floor so the gate stays open through quiet parts of a track (hysteresis).
- Final bar levels are multiplied by `gate`, so silence → flat bars.

### Smoothing (`smooth_levels`)
- Asymmetric 1-pole smoothing per bar: rise `0.70` (fast attack), fall `0.90` (slow decay) — bars snap up and trail down.
- `levels = smooth * gate`, with extra 0.70 damping while the gate is shutting.

## Rendering

- Dark background `(4, 4, 9)`.
- `nbars` default 40 (range 8–128), 2px gap; bar width is computed to fill the window.
- **Flat bars** — each bar is a plain rectangle from the bottom of the window to its level (no rounded caps).
- **Smooth per-pixel gradient** — `_gradient_surface` builds each column with one color per pixel row (via numpy + `pygame.surfarray`), so a single bar is one continuous gradient with *no visible color banding*.
- Colors come from `colors.txt`, not the code. Each line is `<name> <bottom_hex> <top_hex> [alt_bottom_hex alt_top_hex]`. Two-tone schemes (Neon, Sunset) alternate two gradients bar-by-bar and add a traveling brightness wave. Bar brightness scales with its level.

### Color schemes (press `C` to cycle)

Blue, Red, Violet, Emerald, Amber, Teal, Pink, Gold — single dark-themed gradients; Neon and Sunset — two-tone (alternating + wave).

### UI overlay (`draw_ui`)
- Semi-transparent box top-left showing: status (`LIVE` / `SILENT` / `ERROR`), device name, FPS, bar count, sample rate, color scheme, and control hints.

## Controls

| Key | Action |
|---|---|
| Close window (X) | Quit |
| `F11` | Toggle fullscreen (remembers window size) |
| `+` / `=` | More bars (+4, max 128) |
| `-` | Fewer bars (−4, min 8) |
| `F` | Toggle UI overlay |
| `R` | Reopen capture device |
| `C` | Cycle color scheme (all schemes in `colors.txt`) |

Note: `Esc` intentionally does not quit — use the window close button.

## Key Tunables (top of `visualizer.py`)

| Constant | Meaning |
|---|---|
| `RATE_DEFAULT` / `CHUNK` / `FFT_N` | 48kHz, 1024 frames, 2048-pt FFT |
| `DB_FLOOR` / `DB_HEAD` | dB mapping range for bars (−70 .. −8) |
| `GAIN_EXP` (1.1) | curve gamma between dB and fraction |
| `GAIN_ATTACK/RELEASE` | auto-gain smoothing |
| `RISE` / `FALL` | bar response (fast up, slow down) |
| `FLOOR_RATIO` / `FLOOR_MIN` | silence gate threshold vs noise floor |
| `MIN/MAX_FREQ` | band edge limits |
| `MIN/MAX/DEFAULT_BARS` | bar count limits |

## Run, Build, Install

### Run from source

```bash
./run.sh
```

Creates `.venv` on first run, installs `requirements.txt`, and launches `visualizer.py`.

### Build a standalone bundle

```bash
./build.sh
```

Creates/uses `.venv`, installs PyInstaller, and runs:

```
pyinstaller --onedir --windowed --name MyVisualizer --collect-all sounddevice \
    --add-data "colors.txt:." --add-data "icon.png:." --icon icon.png visualizer.py
```

Output: `dist/MyVisualizer/` (launcher + `_internal` deps). `--collect-all sounddevice` ships the PortAudio libraries needed for loopback capture in the frozen app.

### Install system-wide (current user)

```bash
./install.sh
```

Requires a completed `./build.sh` (`dist/MyVisualizer/MyVisualizer`). It:

1. Copies the bundle to `~/.local/share/MyVisualizer/`.
2. Installs a launcher script `~/.local/bin/myvisualizer`.
3. Installs the icon to `~/.local/share/icons/hicolor/512x512/apps/myvisualizer.png`.
4. Adds a desktop entry `myvisualizer.desktop` so the app appears in the application menu.

Launch it with `myvisualizer` or from the app menu.