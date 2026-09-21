# MyVisualizer — Installer Plan

Goal: make MyVisualizer feel like a real app you download from a website — a single `MyVisualizer-Setup.exe` that runs a normal install wizard and lets the user pick where it goes.

## Download & install flow (as the user sees it)

```
User visits site -> clicks Download -> gets MyVisualizer-Setup.exe
        -> runs setup -> wizard:
            welcome -> select install location -> (optional) desktop shortcut
            -> ready to install -> install -> checkbox "run now" -> finish
        -> app appears in Start Menu, works offline
        -> uninstall via Windows Settings > Apps (or Start Menu entry)
```

## How it works

- **`visualizer.py`** is built into a standalone app folder `dist\MyVisualizer\` (PyInstaller **onedir**: exe + `_internal`, bundling `pyaudiowpatch`, `colors.txt`, `icon.png`). Onefile was abandoned — it crashes instantly on this Python 3.14/Windows setup.
- **`install_script.iss`** is an Inno Setup 6 script that packages that folder into an installer.
- The installer uses the standard **Modern** wizard style: language, destination selection (defaults to `%LOCALAPPDATA%\Programs\MyVisualizer`, no admin needed), Start Menu group, optional desktop icon, and a "Run now" checkbox at the end.
- **Per-user install** (`PrivilegesRequired=lowest`) — no UAC prompt, clean uninstall entry in Settings > Apps.

## Build steps (dev, from the project folder)

1. Rebuild the app: `build.bat`  → produces `dist\MyVisualizer\` (exe + `_internal`)
2. Compile installer:
   `"C:\Users\Mustansir_BM\AppData\Local\Programs\Inno Setup 6\ISCC.exe" install_script.iss`
3. Output: `installer\MyVisualizer-Setup.exe`

## Files

| File | Purpose |
|---|---|
| `install_script.iss` | Inno Setup script (wizard, shortcuts, uninstaller) |
| `installer\MyVisualizer-Setup.exe` | The distributable installer |
| `dist\MyVisualizer\` | The packaged app folder the installer installs |

## Notes / next steps

- Hosting: any static site or GitHub Releases can serve `MyVisualizer-Setup.exe`.
- Splash/branding image in the wizard welcome page (WizardImageFile) — polished later.
- Version bump = change `MyAppVersion` in the `.iss` and rebuild.