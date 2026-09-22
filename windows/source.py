import asyncio
import ctypes
import os
import re
import threading
from ctypes import wintypes

try:
    from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

    _PYCAW_OK = True
except Exception:
    _PYCAW_OK = False

try:
    from winsdk.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager,
        GlobalSystemMediaTransportControlsSessionPlaybackStatus,
    )

    _SMTC_OK = True
except Exception:
    _SMTC_OK = False

_FRIENDLY = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "brave.exe": "Brave",
    "opera.exe": "Opera",
    "spotify.exe": "Spotify",
    "vlc.exe": "VLC",
    "mpv.exe": "MPV",
    "foobar2000.exe": "foobar2000",
    "itunes.exe": "iTunes",
    "tidal.exe": "TIDAL",
    "wmplayer.exe": "Windows Media Player",
    "audacity.exe": "Audacity",
    "discord.exe": "Discord",
    "steam.exe": "Steam",
    "stremio.exe": "Stremio",
}

_AUMID = {
    "operasoftware.operawebbrowser": "Opera",
    "opera gx": "Opera GX",
    "google chrome": "Chrome",
    "chrome.exe": "Chrome",
    "microsoft edge": "Edge",
    "msedge.exe": "Edge",
    "mozilla firefox": "Firefox",
    "mozilla.firefox": "Firefox",
    "firefox.exe": "Firefox",
    "brave": "Brave",
    "spotify": "Spotify",
    "spotify.exe": "Spotify",
    "vlc": "VLC",
    "vlc.exe": "VLC",
    "mpv": "MPV",
    "foobar2000": "foobar2000",
    "itunes.exe": "iTunes",
    "tidal.exe": "TIDAL",
    "windows media player": "Windows Media Player",
    "wmplayer.exe": "Windows Media Player",
    "discord.exe": "Discord",
    "steam.exe": "Steam",
    "stremio.exe": "Stremio",
}

_SELF = {"myvisualizer.exe", "python.exe", "pwsh.exe"}
_PEAK_MIN = 0.004


def _aumid_lookup(low):
    if low in _AUMID:
        return _AUMID[low]
    parts = low.split(".")
    for i in range(len(parts) - 1, 1, -1):
        cand = ".".join(parts[:i])
        if cand in _AUMID:
            return _AUMID[cand]
    return None


def friendly(exe):
    if not exe:
        return None
    low = str(exe).strip().lower()
    hit = _aumid_lookup(low)
    if hit:
        return hit
    base = os.path.basename(exe).lower()
    if base in _AUMID:
        return _AUMID[base]
    if base in _FRIENDLY:
        return _FRIENDLY[base]
    root = os.path.splitext(base)[0]
    if not root:
        return None
    return root[:1].upper() + root[1:]


_BRANDS = (
    " - google chrome", " - microsoft edge", " - mozilla firefox", " - brave",
    " - opera", " - opera gx", " - vivaldi", " - waterfox",
)

_BROWSERS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe",
    "opera gx.exe", "vivaldi.exe", "waterfox.exe", "seamonkey.exe",
    "iexplore.exe", "360chrome.exe", "centbrowser.exe", "slimjet.exe",
}


def _clean_title(title, friendly_name):
    title = title.strip()
    title = re.sub(r"^\(\d+\)\s*", "", title)
    if friendly_name:
        mark = " - " + friendly_name.lower()
        if title.lower().endswith(mark):
            title = title[: -len(mark)].rstrip()
    return title.strip()


def window_title_for_session(pid, exe):
    if not exe:
        return ""
    import psutil

    exe_base = os.path.basename(exe).lower()
    fg = wintypes.HWND(ctypes.windll.user32.GetForegroundWindow())
    found = []

    def callback(hwnd, _lp):
        if not ctypes.windll.user32.IsWindowVisible(hwnd):
            return True
        wnd_pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(wnd_pid))
        cur = wnd_pid.value
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title:
            return True
        area = 0
        rect = wintypes.RECT()
        if ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            area = max(0, rect.right - rect.left) * max(0, rect.bottom - rect.top)
        try:
            wname = os.path.basename(psutil.Process(cur).name()).lower()
        except Exception:
            wname = ""
        found.append((hwnd == fg, title, area, wname, cur))
        return True

    Proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    ctypes.windll.user32.EnumWindows(Proc(callback), 0)
    if not found:
        return ""
    if pid:
        exact = [f for f in found if f[4] == pid]
        if exact:
            exact.sort(key=lambda t: (not t[0], -t[2]))
            return exact[0][1]
    same = [f for f in found if f[3] == exe_base]
    if not same:
        return ""
    if len(same) == 1:
        return same[0][1]
    if exe_base in _BROWSERS:
        return ""
    same.sort(key=lambda t: (not t[0], -t[2]))
    return same[0][1]


def _best_session():
    if not _PYCAW_OK:
        return (None, None, 0.0)
    best = None
    best_peak = 0.0
    for s in AudioUtilities.GetAllSessions():
        name = None
        pid = None
        proc = s.Process
        if proc is not None:
            try:
                pid = proc.pid
                name = proc.name()
            except Exception:
                pid = None
                name = None
        if not name:
            continue
        if os.path.basename(name).lower() in _SELF:
            continue
        try:
            meter = s._ctl.QueryInterface(IAudioMeterInformation)
            peak = float(meter.GetPeakValue())
        except Exception:
            peak = 0.0
        if peak > best_peak:
            best_peak = peak
            best = (name, pid)
    if not best or best_peak < _PEAK_MIN:
        return (None, None, 0.0)
    return (best[0], best[1], best_peak)


def _smtc_snapshot():
    if not _SMTC_OK:
        return None

    async def _collect():
        mgr = await GlobalSystemMediaTransportControlsSessionManager.request_async()
        best = None
        for s in mgr.get_sessions():
            props = None
            try:
                props = await s.try_get_media_properties_async()
            except Exception:
                pass
            title = (props.title or "").strip() if props else ""
            if not title:
                continue
            artist = (props.artist or "").strip() if props else ""
            status = None
            playing = False
            try:
                status = s.get_playback_info().playback_status
                playing = bool(
                    status is not None and status == GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING
                )
            except Exception:
                pass
            if not playing and status is not None:
                playing = "PLAY" in str(status).upper()
            item = (s.source_app_user_model_id, title, artist)
            if playing:
                return ("playing",) + item
            if best is None:
                best = ("paused",) + item
        return best

    try:
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(asyncio.wait_for(_collect(), timeout=1.5))
        finally:
            loop.close()
        return result
    except Exception:
        return None


def _coinit_mta():
    try:
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except Exception:
        pass


def _snapshot():
    peak_session = _best_session()
    name, pid, peak = peak_session
    smtc = _smtc_snapshot()
    if smtc:
        _, sapp, stitle, sartist = smtc
        app = friendly(sapp) or (friendly(name) if name else None)
        if sartist and sartist.lower() not in stitle.lower():
            title = f"{sartist} - {stitle}"
        else:
            title = stitle
        return (app, title, peak)
    if not name:
        return (None, None, 0.0)
    fn = friendly(name)
    if fn is None:
        return (None, None, 0.0)
    t = window_title_for_session(pid, name)
    return (fn, _clean_title(t, fn), peak)


class SourceMonitor:
    def __init__(self, interval=1.0):
        self._interval = interval
        self._lock = threading.Lock()
        self._info = (None, None, 0.0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        _coinit_mta()
        while not self._stop.is_set():
            try:
                info = _snapshot()
            except Exception:
                info = (None, None, 0.0)
            with self._lock:
                self._info = info
            self._stop.wait(self._interval)

    def snapshot(self):
        with self._lock:
            return self._info

    def close(self):
        self._stop.set()