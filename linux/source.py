import subprocess
import threading
import time

_FRIENDLY = {
    "chrome": "Chrome",
    "chromium": "Chromium",
    "microsoft-edge": "Edge",
    "firefox": "Firefox",
    "brave": "Brave",
    "opera": "Opera",
    "spotify": "Spotify",
    "vlc": "VLC",
    "mpv": "MPV",
    "rhythmbox": "Rhythmbox",
    "audacious": "Audacious",
    "foobar2000": "foobar2000",
    "strawberry": "Strawberry",
}

_SELF = {"myvisualizer", "myvisualizertui", "python3", "python"}


def friendly(binary):
    if not binary:
        return None
    b = binary.lower()
    if b in _FRIENDLY:
        return _FRIENDLY[b]
    return b[:1].upper() + b[1:]


def _run(cmd, timeout=1.0):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout).stdout
        return out
    except Exception:
        return ""


def _pactl_session():
    data = _run(["pactl", "list", "sink-inputs"])
    if not data:
        return None
    found = []
    for raw in data.split("Sink Input #")[1:]:
        props = {}
        for line in raw.splitlines():
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"')
            props[key] = val
        state = props.get("State") or "_"
        binary = props.get("application.process.binary", "")
        if binary in _SELF:
            continue
        sample = props.get("application.name") or ""
        media = props.get("media.title") or props.get("media.name") or ""
        name = friendly(binary)
        if not name:
            name = sample or binary
        if not name:
            continue
        found.append((name, media or sample, state))
    if not found:
        return None
    found.sort(key=lambda t: 1 if t[2] == "RUNNING" else 0)
    name, title, _ = found[0]
    return (name, title, 1.0) if name else None


def _pw_dump_session():
    data = _run(["pw-dump"])
    if not data:
        return None
    import json

    try:
        nodes = json.loads(data)
    except Exception:
        return None
    best = None
    for node in nodes:
        if node.get("type") not in ("PipeWire:Interface:Node",):
            continue
        info = node.get("info") or {}
        props = info.get("props") or {}
        binary = props.get("application.process.binary", "")
        if binary in _SELF:
            continue
        media = props.get("media.title") or props.get("media.name") or ""
        app = props.get("application.name") or ""
        name = friendly(binary) or app or binary
        if name:
            best = (name, media, 1.0)
            return best
    return None


def _linux_session():
    return _pactl_session() or _pw_dump_session()


class SourceMonitor:
    def __init__(self, interval=1.0):
        self._interval = interval
        self._lock = threading.Lock()
        self._info = (None, None, 0.0)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                info = _linux_session()
            except Exception:
                info = None
            with self._lock:
                self._info = info if info else (None, None, 0.0)
            self._stop.wait(self._interval)

    def snapshot(self):
        with self._lock:
            return self._info

    def close(self):
        self._stop.set()