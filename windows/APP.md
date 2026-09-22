# MyVisualizer (Windows) — How the App Works

A real-time audio spectrum visualizer for Windows. It captures whatever the computer is playing (system output audio via WASAPI loopback), converts it to frequency bands with an FFT, and renders smooth, flat gradient bars in a Pygame window.

## Layout

| File | Purpose |
|---|---|
| `visualizer.py` | Entire app: audio capture, DSP, rendering, input handling |
| `source.py` | "Now playing" detector (WASAPI audio sessions → app, optional SMTC media title) |
| `colors.txt` | Color scheme definitions (hex), loaded at startup |
| `icon.ico` / `icon.png` | App icon (exe/taskbar + window icon) |
| `make_icon.py` | Regenerates the icon (multi-size PNG + ICO) |
| `test_bounds.py` | Border-pixel guard: verifies bars never draw outside the window |
| `build.bat` | Runs the bounds guard, then builds `dist\MyVisualizer\` with PyInstaller |
| `MyVisualizer.spec` | PyInstaller config (bundles `pyaudiowpatch`, `pycaw`, `winsdk`, `colors.txt`, `icon.png`) |
| `install_script.iss` | Inno Setup script that packages `dist\MyVisualizer\` into an installer (`installer\MyVisualizer-Setup.exe`) |

A single GUI app — no terminal mode, no UI framework, no external assets at runtime.

## Persisted Settings

The app remembers its settings between runs in a small JSON file:

- `%APPDATA%\MyVisualizer\config.json`

Stored fields: `bars`, `scheme` (by name), `show_ui`, `width`, `height`, `mode`. Values are clamped on load (`bars` 8–128, window size capped to the current desktop so it never opens off-screen), and the config is re-written atomically (temp file + rename) on every change and on quit. The window is always resizable and always starts windowed (no persisted fullscreen). Delete the file to reset to defaults.

## Now Playing Detector (`source.py`)

Every second, a background thread reports what's currently audible:

1. **WASAPI peak detection** (`pycaw`): enumerate the output sessions bound to the speakers and read each session's peak volume, so only apps that are *actually making sound* count (paused/silent ones are skipped, and the app's own process is ignored).
2. **SMTC media session** (`winsdk`): Windows' GlobalSystemMediaTransportControls session for the loudest session. When a site/app registers one (browsers, Spotify, VLC, …) this gives the real, up-to-date song/artist independently of windows.
3. **Window title fallback** (only when it's accurate): exact PID match against top-level windows. For one-window apps (VLC, Spotify desktop, foobar2000) it yields the current track/window title. For browsers, whose renderer processes own no top-level windows, it does **not** guess the active tab — it just reports the browser name.

The UI shows the app name (e.g. "Spotify" / "Opera") and a LIVE/SILENT/ERROR status. If nothing is audible it shows **Nothing playing**.

## Dependencies

- `pygame` — windowing, rendering, event loop
- `numpy` — ring buffer, FFT, vectorized math
- `pyaudiowpatch` — WASAPI audio loopback (captures *system output* audio, unlike microphone capture)
- `pycaw` / `comtypes` — WASAPI audio-session enumeration (for the Now Playing box)
- `winsdk` — Windows Media Transport Controls (SMTC) for accurate media titles
- `pyinstaller` (build only)

## Runtime Flow (`main()`)

```
main()
├─ enable_dpi_awareness()        # fix blurry scaling
├─ set_dark_title_bar()          # dark window title bar
├─ pygame.init()                 # resizable window (saved size)
├─ window = np.hanning(FFT_N)    # FFT window function
├─ capture = AudioCapture()      # open WASAPI loopback stream
└─ loop:
   ├─ handle events (keys, live resize, window close)
   ├─ history = capture.history()     # latest FFT_N audio samples
   ├─ levels = analyze(history,...)  # gate->auto-gain->smooth->levels in one step
   ├─ draw_bars(screen, mode,...)    # Bottom-Up / Radial / Mirror / Ring
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
- Bars stop ~8% short of the window edges so they never clip into the borders even at full volume (guaranteed by `test_bounds.py` before every build, now for all four modes).
- **Smooth per-pixel gradient** — `_gradient_surface` builds each column with one color per pixel row (via numpy + `pygame.surfarray`), so a single bar is one continuous gradient with *no visible color banding*.
- Colors come from `colors.txt`, not the code. Each line is `<name> <bottom_hex> <top_hex> [alt_bottom_hex alt_top_hex]`. Two-tone schemes (Neon, Sunset) alternate two gradients bar-by-bar and add a traveling brightness wave. Bar brightness scales with its level.

### Color schemes (press `C` to cycle)

Blue, Red, Violet, Emerald, Amber, Teal, Pink, Gold — single dark-themed gradients; Neon and Sunset — two-tone (alternating + wave).

### Display modes (press `M` to cycle)

| # | Mode | Look |
|---|---|---|
| 0 | **Bottom-Up** | bars rise from the bottom edge (default) |
| 1 | **Radial** | bars fan out around a central circle, radiating from its border, with rounded tips |
| 2 | **Mirror** | each bar grows from the horizontal center line both upward and downward — a symmetric butterfly around the middle of the screen |
| 3 | **Ring** | bars are drawn as annular segments between an inner circle and a fixed outer ring; the ring "swells" with the music |

The active mode is saved to the config file and restored on next launch.

### UI overlay (`draw_ui`)
- Box modes (Bottom-Up, Mirror): small semi-transparent box top-left.
- Circular modes (Radial, Ring): a **circular info bubble centered inside the spiral/ring's inner circle** — text is clipped to the circle so it never pokes outside.
- Shows the Now Playing source (app name), status, FPS, bars, scheme, mode, and control hints.

## Controls

| Key | Action |
|---|---|
| Close window (X) | Quit |
| `+` / `=` / Numpad `+` | More bars (+4, max 128) |
| `-` / Numpad `-` | Fewer bars (−4, min 8) |
| `M` | Cycle display mode (Bottom-Up / Radial / Mirror / Ring) |
| `F` | Toggle UI overlay |
| `R` | Reopen capture device |
| `C` | Cycle color scheme (all schemes in `colors.txt`) |

Note: `Esc` intentionally does not quit — use the window close button. Keys auto-repeat while held (delay 400ms, repeat 40ms), so holding `+`/`-` continuously adjusts bar count.

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

## CLI Flags

- `--verbose` — print per-second status lines to stdout (useful when running from source).
- `--bars N` — override the saved bar count for this session (8–128).

## Build

`build.bat`:
1. Activates `.venv` (creates it if missing, installs `requirements.txt`).
2. Runs `test_bounds.py` (border-pixel guard) and **aborts if it fails**.
3. Builds the app: `PyInstaller --onedir --windowed --name MyVisualizer --collect-all pyaudiowpatch --collect-all comtypes --collect-all pycaw --collect-all winsdk --add-data "colors.txt;." --add-data "icon.png;." --icon "icon.ico" visualizer.py`.
4. Output: `dist\MyVisualizer\` (exe + `_internal` deps).

Notes: the app uses **onedir** (not onefile) — onefile builds crash on this Python setup, while onedir runs cleanly and starts faster. `--collect-all pyaudiowpatch` ships the DLLs that enable WASAPI loopback in the frozen exe, `--collect-all comtypes` ships the COM type-library support needed by pycaw, and `--collect-all winsdk` bundles the SMTC WinRT projection. `--icon icon.ico` sets the exe icon; `icon.png` + `pygame.display.set_icon` set the runtime window/taskbar icon.

## Installer

`install_script.iss` (Inno Setup) packages `dist\MyVisualizer\` into `installer\MyVisualizer-Setup.exe`, which installs the single GUI app and a start-menu/desktop shortcut. There is no terminal integration and no PATH modification.

### Updating an existing install

Because the `AppId` and install directory (`{localappdata}\Programs\MyVisualizer`) never change, re-running the same `MyVisualizer-Setup.exe` **updates the app in place**:

- `AppMutex=MyVisualizer_Mutex` + `CloseApplications` — the app holds the `MyVisualizer_Mutex` named mutex for its whole lifetime, so the installer detects a running copy and auto-closes it before overwriting (and relaunches it when done, via `RestartApplications`). No manual "close the app first" step needed.
- All files use `ignoreversion` so the new build overwrites the old one.
- User settings are unaffected: they live in `%APPDATA%\MyVisualizer\config.json` (plus the `colors.txt` shipped with the app), never in the install folder, so the color scheme, bar count, window size, and mode all survive an update.