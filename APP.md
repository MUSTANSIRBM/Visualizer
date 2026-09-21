# MyVisualizer — How the App Works

A real-time audio spectrum visualizer for Windows. Captures what the computer is playing (system audio), converts it to frequency bands via FFT, and renders animated bars on a Pygame window.

## Layout

| File | Purpose |
|---|---|
| `visualizer.py` | Entire app: audio capture, DSP, rendering, input handling |
| `colors.txt` | Color scheme definitions (hex), loaded at startup |
| `icon.ico` / `icon.png` | App icon (exe/taskbar + window icon) |
| `make_icon.py` | Regenerates the icon (multi-size PNG + ICO) |
| `build.bat` | Builds `dist/MyVisualizer.exe` with PyInstaller |
| `MyVisualizer.spec` | PyInstaller config (bundles `pyaudiowpatch` + `colors.txt`) |
| `dist/` | Built app folder (`MyVisualizer\` = exe + `_internal`) |

Single-file app. No UI framework, no config files, no external assets.

## Dependencies

- `pygame` — windowing, rendering, event loop
- `numpy` — ring buffer, FFT, vectorized math
- `pyaudiowpatch` — WASAPI audio loopback (captures *system output* audio, unlike normal microphone capture)
- `pyinstaller` (build only)

## Runtime Flow (`main()`)

```
main()
├─ enable_dpi_awareness()        # fix blurry scaling
├─ pygame.init()                 # 1280x720 resizable window
├─ window = np.hanning(FFT_N)    # FFT window function
├─ capture = AudioCapture()      # open WASAPI loopback stream
└─ loop:
   ├─ handle events (keys, quit, resize)
   ├─ history = capture.history()     # latest FFT_N audio samples
   ├─ rms = sqrt(mean(history^2))     # loudness estimate
   ├─ db = compute_bands(history, ...)# FFT -> log-spaced dB bands
   ├─ gate, floor = silence_gate(...) # open/close voice-activity gate
   ├─ gain = auto_gain(db, gain)      # normalize to DB_HEAD
   ├─ target = db_to_frac(db, gain)   # dB -> 0..1 bar target
   ├─ smooth_levels(smooth, target)   # attack/release smoothing
   ├─ levels = smooth * gate          # silence suppression
   ├─ draw bars (gradient columns)
   ├─ draw UI overlay (if shown)
   └─ flip, tick(60)
```

## Audio Capture (`AudioCapture`)

- Opens a **WASAPI loopback device** via `pyaudiowpatch` (`_default_loopback`): finds the host API, takes the default output device, then matches a loopback device by name.
- Sample rate taken from the device (`defaultSampleRate`), default 48000.
- Callback stream in a background thread: incoming `int16` frames are reshaped per-channel, downmixed to mono, normalized to `[-1, 1]`, and pushed into a fixed ring buffer (`ring`, size `FFT_N = 2048`).
- `history()` returns a copy of the ring under a lock (safe for the render thread).
- `reopen()` (key `R`) closes and recreates the stream — useful after device changes.
- Failures are stored in `self.error` and surfaced in the UI as an error status.

## DSP Pipeline

### Frequency bands (`compute_bands`)
- Computes `abs(rfft(history * hanning))`.
- Frequency range: `MIN_FREQ=30Hz` to `min(MAX_FREQ=18kHz, rate*0.45)` (Nyquist-ish).
- Bins are **log-spaced** edges (`np.linspace(log(lo), log(hi), nbars+1)`) so each band covers a constant musical interval — classic for audio bars.
- Each band = mean magnitude over its FFT bins, normalized by `FFT_N/2`, converted to dB (`20*log10`).

### Auto-gain (`auto_gain`)
- Drives the peak band toward `DB_HEAD = -8dB`.
- Fast attack (`GAIN_ATTACK 0.08`) when below target, slow release (`0.015`) when above — so the level rides the music without pumping on quiet gaps.

### Silence gate (`silence_gate`)
- Tracks a slowly adapting noise floor.
- Gate opens when RMS exceeds `max(floor * 1.5, FLOOR_MIN)`; opens fast, decays slowly.
- While open, adapts the floor to background noise so the gate stays open during quiet bits of a track (hysteresis).
- Final bar levels are multiplied by `gate`, so silence -> flat bars.

### Smoothing (`smooth_levels`)
- Asymmetric 1-pole smoothing per bar: rise `0.70` (fast attack), fall `0.90` (slow decay) — bars jump up and trail down.
- Odds are `levels = smooth * gate`, with extra 0.70 damping when gate is shutting.

## Rendering

- Dark background `(4,4,9)`.
- `nbars` default 40 (range 8–128), gap 2px; bar width computed to fit the window.
- Each bar is a vertical stack of up to `GRAD_STEPS=24` rectangles (stop when step < 12px), 2-color gradient from top (`ct`, t=1) to bottom (`cb`, t=0).
- Colors come from `colors.txt`, not the code. Each line is `<name> <bottom_hex> <top_hex> [alt_bottom_hex alt_top_hex]`. Two-tone schemes (like Police) alternate gradients per bar and add a traveling brightness wave (siren feel). Bar brightness scales with the bar's level (`frac`).
- Rounded cap on the top segment of each bar.

### UI overlay (`draw_ui`)
- Semi-transparent box top-left showing: status (`LIVE`/`SILENT`/`ERROR`), device name, FPS, bar count, sample rate, color scheme, and control hints.

## Controls

| Key | Action |
|---|---|
| `Esc` | Quit |
| `F11` | Toggle fullscreen (remembers window size) |
| `+` / `=` | More bars (+4, max 128) |
| `-` | Fewer bars (−4, min 8) |
| `F` | Toggle UI overlay |
| `R` | Reopen capture device |
| `C` | Cycle color scheme (Blue ↔ Red ↔ Police, from `colors.txt`) |

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
| `GRAD_STEPS` | gradient band resolution |
| `colors.txt` | defines schemes: name, bottom/top hex (and optional alt pair); `C` cycles |

## Build

`build.bat`:
1. Activates `.venv`
2. Runs `PyInstaller --onedir --windowed --name MyVisualizer --collect-all pyaudiowpatch --add-data "colors.txt;." --add-data "icon.png;." --icon "icon.ico" visualizer.py`
3. Output: `dist\MyVisualizer\` (exe + `_internal` deps)

Underscores: the app uses **onedir** (not onefile) — PyInstaller 6.22.3 onefile builds crash instantly on this Python 3.14/Windows setup, while onedir runs cleanly (and starts faster). The installer and any distribution ship the whole `dist\MyVisualizer\` folder. `--collect-all pyaudiowpatch` is required to ship the DLLs that enable WASAPI loopback in the frozen exe. `console=False` in the spec means no terminal window (the app is windowed-only). `--icon icon.ico` sets the exe's icon; `icon.png` + `pygame.display.set_icon` set the runtime window/taskbar icon.