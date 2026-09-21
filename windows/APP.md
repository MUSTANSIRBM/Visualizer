# MyVisualizer (Windows) — How the App Works

A real-time audio spectrum visualizer for Windows. It captures whatever the computer is playing (system output audio via WASAPI loopback), converts it to frequency bands with an FFT, and renders smooth, flat gradient bars in a Pygame window.

## Layout

| File | Purpose |
|---|---|
| `visualizer.py` | Entire app: audio capture, DSP, rendering, input handling |
| `colors.txt` | Color scheme definitions (hex), loaded at startup |
| `icon.ico` / `icon.png` | App icon (exe/taskbar + window icon) |
| `make_icon.py` | Regenerates the icon (multi-size PNG + ICO) |
| `build.bat` | Builds `dist\MyVisualizer\` with PyInstaller |
| `MyVisualizer.spec` | PyInstaller config (bundles `pyaudiowpatch`, `colors.txt`, `icon.png`) |
| `install_script.iss` | Inno Setup script that packages `dist\MyVisualizer\` into an installer (`installer\MyVisualizer-Setup.exe`) |

Single-file app — no UI framework, no config files, no external assets at runtime.

## Dependencies

- `pygame` — windowing, rendering, event loop
- `numpy` — ring buffer, FFT, vectorized math
- `pyaudiowpatch` — WASAPI audio loopback (captures *system output* audio, unlike microphone capture)
- `pyinstaller` (build only)

## Runtime Flow (`main()`)

```
main()
├─ enable_dpi_awareness()        # fix blurry scaling
├─ set_dark_title_bar()          # dark window title bar
├─ pygame.init()                 # 1280x720 resizable window
├─ window = np.hanning(FFT_N)    # FFT window function
├─ capture = AudioCapture()      # open WASAPI loopback stream
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

- Opens a **WASAPI loopback device** via `pyaudiowpatch` (`_default_loopback`): locates the WASAPI host API, takes its default output device, then matches a loopback device whose name contains the output device name.
- Sample rate comes from the device (`defaultSampleRate`), default 48000.
- Callback stream in a background thread: incoming `int16` frames are reshaped per-channel, downmixed to mono, normalized to `[-1, 1]`, and pushed into a fixed ring buffer (`ring`, size `FFT_N = 2048`).
- `history()` returns a copy of the ring under a lock (safe for the render thread).
- `reopen()` (key `R`) stops, closes, and recreates the stream — handy after device changes.
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
- Gate opens when RMS exceeds `max(floor * 1.5, FLOOR_MIN)`; opens fast, emits slowly.
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

## Build

`build.bat`:
1. Activates `.venv` (creates it if missing, installs `requirements.txt`).
2. Runs `PyInstaller --onedir --windowed --name MyVisualizer --collect-all pyaudiowpatch --add-data "colors.txt;." --add-data "icon.png;." --icon "icon.ico" visualizer.py`.
3. Output: `dist\MyVisualizer\` (exe + `_internal` deps).

Notes: the app uses **onedir** (not onefile) — onefile builds crash on this Python 3.14 setup, while onedir runs cleanly and starts faster. `--collect-all pyaudiowpatch` ships the DLLs that enable WASAPI loopback in the frozen exe. `console=False` means no terminal window. `--icon icon.ico` sets the exe icon; `icon.png` + `pygame.display.set_icon` set the runtime window/taskbar icon.

## Installer

`install_script.iss` (Inno Setup) packages `dist\MyVisualizer\` into `installer\MyVisualizer-Setup.exe`, which installs the app and a start-menu/desktop shortcut on the target machine.