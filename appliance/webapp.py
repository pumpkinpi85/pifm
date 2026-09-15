"""Lightweight HTTP API + static dashboard (stdlib only)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

if TYPE_CHECKING:
    from .controller import Controller
    from .events import EventLog
    from .library import Library

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def _json_bytes(obj: Any, code: int = 200) -> Tuple[int, bytes, str]:
    return code, json.dumps(obj).encode("utf-8"), "application/json"


def _system_health() -> Dict[str, Any]:
    out = {
        "temp_c": None,
        "loadavg": None,
        "mem": None,
        "disk_free_gb": None,
        "hostname": None,
        "ethernet": None,
    }
    try:
        out["hostname"] = open("/etc/hostname").read().strip()
    except OSError:
        out["hostname"] = os.uname().nodename
    try:
        out["loadavg"] = os.getloadavg()
    except OSError:
        pass
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            out["temp_c"] = round(int(fh.read().strip()) / 1000.0, 1)
    except OSError:
        pass
    try:
        # fallback vcgencmd not required
        pass
    except Exception:
        pass
    try:
        import shutil as sh

        usage = sh.disk_usage("/")
        out["disk_free_gb"] = round(usage.free / (1024**3), 2)
        out["disk_total_gb"] = round(usage.total / (1024**3), 2)
    except OSError:
        pass
    try:
        # Prefer configured/common interfaces without hard-depending on one host.
        ifaces = ["eth0", "wlan0", "en0"]
        out["ethernet"] = False
        for name in ifaces:
            p = Path("/sys/class/net/{}".format(name))
            if p.exists():
                out["ethernet"] = True
                out["ethernet_iface"] = name
                try:
                    out["ethernet_operstate"] = (p / "operstate").read_text().strip()
                except OSError:
                    out["ethernet_operstate"] = None
                break
    except OSError:
        out["ethernet"] = False
    try:
        # mem available
        info = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                if ":" in line:
                    k, v = line.split(":", 1)
                    info[k.strip()] = v.strip()
        out["mem"] = {
            "total_kb": int(info.get("MemTotal", "0").split()[0]),
            "available_kb": int(info.get("MemAvailable", "0").split()[0]),
        }
    except OSError:
        pass
    return out


class Handler(BaseHTTPRequestHandler):
    controller = None  # type: Optional[Controller]
    library = None  # type: Optional[Library]
    events = None  # type: Optional[EventLog]
    gpio = None

    def log_message(self, fmt: str, *args: Any) -> None:
        # quieter
        return

    def _cors(self) -> None:
        self.send_header("Cache-Control", "no-store")

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("JSON object required")
        return data

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/status":
                assert self.controller
                st = self.controller.status()
                st["health"] = _system_health()
                code, body, ct = _json_bytes(st)
                return self._send(code, body, ct)
            if path == "/api/readiness":
                assert self.controller
                code, body, ct = _json_bytes(
                    self.controller.broadcast_checklist()
                )
                return self._send(code, body, ct)
            if path == "/api/events":
                assert self.events
                code, body, ct = _json_bytes({"events": self.events.recent(80)})
                return self._send(code, body, ct)
            if path == "/api/events/stream":
                return self._sse_stream()
            if path == "/api/queue":
                assert self.controller
                code, body, ct = _json_bytes({"queue": self.controller.queue_snapshot()})
                return self._send(code, body, ct)
            if path == "/api/config":
                assert self.controller
                code, body, ct = _json_bytes(self.controller.config.as_dict())
                return self._send(code, body, ct)
            if path == "/api/library":
                assert self.library
                qs = parse_qs(parsed.query)
                q = (qs.get("q") or [""])[0]
                code, body, ct = _json_bytes({"tracks": self.library.search(q)})
                return self._send(code, body, ct)
            if path == "/api/playlists":
                assert self.library
                code, body, ct = _json_bytes({"playlists": self.library.list_playlists()})
                return self._send(code, body, ct)
            if path.startswith("/api/playlists/"):
                assert self.library
                pid = path.split("/")[3]
                code, body, ct = _json_bytes(self.library.load_playlist(pid))
                return self._send(code, body, ct)
            # static
            return self._static(path)
        except FileNotFoundError as exc:
            code, body, ct = _json_bytes({"error": str(exc)}, 404)
            return self._send(code, body, ct)
        except Exception as exc:  # noqa: BLE001
            code, body, ct = _json_bytes(
                {"error": str(exc), "trace": traceback.format_exc()[-500:]}, 500
            )
            return self._send(code, body, ct)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            assert self.controller and self.library and self.events
            data = {}
            if self.headers.get("Content-Type", "").startswith("application/json"):
                data = self._read_json()

            if path == "/api/tx/on":
                # Return immediately with STARTING; completion is backgrounded.
                st = self.controller.go_on_air(wait=False)
            elif path == "/api/tx/off":
                st = self.controller.tx_off()
            elif path == "/api/play":
                st = self.controller.play(wait=False)
            elif path == "/api/pause":
                st = self.controller.pause()
            elif path == "/api/stop":
                st = self.controller.stop_playback()
            elif path == "/api/next":
                st = self.controller.next_track(wait=False)
            elif path == "/api/prev":
                st = self.controller.prev_track(wait=False)
            elif path == "/api/fault/clear":
                st = self.controller.clear_fault()
            elif path == "/api/rfquiet":
                st = self.controller.rf_quiet(confirmed=bool(data.get("confirmed")))
            elif path == "/api/rfquiet/restore":
                st = self.controller.rf_quiet_restore()
            elif path == "/api/config":
                st = {"config": self.controller.update_config(data)}
            elif path == "/api/library/reindex":
                n = self.library.reindex()
                self.events.emit("library_import", "reindex {}".format(n))
                st = {"indexed": n}
            elif path == "/api/playlists":
                st = self.library.create_playlist(str(data.get("name") or "playlist"))
            elif path.startswith("/api/playlists/") and path.endswith("/tracks"):
                pid = path.split("/")[3]
                pl = self.library.load_playlist(pid)
                tid = str(data.get("track_id") or "")
                if not tid:
                    raise ValueError("track_id required")
                tracks = list(pl.get("tracks") or [])
                if tid not in tracks:
                    tracks.append(tid)
                pl["tracks"] = tracks
                st = self.library.save_playlist(pid, pl)
            elif path.startswith("/api/playlists/") and path.endswith("/reorder"):
                pid = path.split("/")[3]
                order = data.get("tracks") or []
                if not isinstance(order, list):
                    raise ValueError("tracks must be a list")
                pl = self.library.load_playlist(pid)
                pl["tracks"] = [str(x) for x in order]
                st = self.library.save_playlist(pid, pl)
            elif path == "/api/upload":
                st = self._upload()
            else:
                code, body, ct = _json_bytes({"error": "not found"}, 404)
                return self._send(code, body, ct)

            code, body, ct = _json_bytes(st)
            return self._send(code, body, ct)
        except Exception as exc:  # noqa: BLE001
            code, body, ct = _json_bytes({"error": str(exc)}, 400)
            return self._send(code, body, ct)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            assert self.library
            data = self._read_json()
            if path.startswith("/api/playlists/") and not path.endswith("/tracks"):
                pid = path.split("/")[3]
                st = self.library.save_playlist(pid, data)
                code, body, ct = _json_bytes(st)
                return self._send(code, body, ct)
            code, body, ct = _json_bytes({"error": "not found"}, 404)
            return self._send(code, body, ct)
        except Exception as exc:  # noqa: BLE001
            code, body, ct = _json_bytes({"error": str(exc)}, 400)
            return self._send(code, body, ct)

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            assert self.library
            if path.startswith("/api/playlists/"):
                parts = path.strip("/").split("/")
                # api/playlists/{id} or api/playlists/{id}/tracks/{tid}
                if len(parts) == 3 and parts[0] == "api" and parts[1] == "playlists":
                    self.library.delete_playlist(parts[2])
                    code, body, ct = _json_bytes({"ok": True})
                    return self._send(code, body, ct)
                if (
                    len(parts) == 5
                    and parts[0] == "api"
                    and parts[1] == "playlists"
                    and parts[3] == "tracks"
                ):
                    pid, tid = parts[2], parts[4]
                    pl = self.library.load_playlist(pid)
                    pl["tracks"] = [t for t in pl.get("tracks") or [] if t != tid]
                    st = self.library.save_playlist(pid, pl)
                    code, body, ct = _json_bytes(st)
                    return self._send(code, body, ct)
            code, body, ct = _json_bytes({"error": "not found"}, 404)
            return self._send(code, body, ct)
        except Exception as exc:  # noqa: BLE001
            code, body, ct = _json_bytes({"error": str(exc)}, 400)
            return self._send(code, body, ct)

    def _sse_stream(self) -> None:
        """Push status snapshots on EventLog changes; keepalive every ~15s."""
        assert self.controller and self.events
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def _write(obj: Dict[str, Any]) -> None:
            payload = json.dumps(obj, default=str)
            self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
            self.wfile.flush()

        try:
            st = self.controller.status()
            st["health"] = _system_health()
            _write({"type": "status", "status": st, "seq": self.events.seq})
            last_seq = self.events.seq
            while True:
                new_seq = self.events.wait(after_seq=last_seq, timeout=15.0)
                if new_seq > last_seq:
                    last_seq = new_seq
                    st = self.controller.status()
                    st["health"] = _system_health()
                    _write(
                        {
                            "type": "status",
                            "status": st,
                            "seq": last_seq,
                            "event": (self.events.recent(1) or [None])[-1],
                        }
                    )
                else:
                    _write({"type": "ping", "ts": time.time(), "seq": last_seq})
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _upload(self) -> Dict[str, Any]:
        assert self.library and self.events
        # multipart is heavy; accept raw body with X-Filename header for light clients
        filename = self.headers.get("X-Filename") or "upload.bin"
        filename = os.path.basename(filename)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 80 * 1024 * 1024:
            raise ValueError("invalid upload size")
        dest = self.library.library_dir / filename
        tmp = dest.with_suffix(dest.suffix + ".partial")
        remaining = length
        with open(tmp, "wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        os.replace(str(tmp), str(dest))
        n = self.library.reindex()
        self.events.emit("library_import", "uploaded {}".format(filename), indexed=n)
        return {"ok": True, "filename": filename, "indexed": n}

    def _static(self, path: str) -> None:
        if path == "/" or path == "":
            path = "/index.html"
        rel = path.lstrip("/")
        file_path = (STATIC_DIR / rel).resolve()
        if not str(file_path).startswith(str(STATIC_DIR.resolve())):
            return self._send(403, b"forbidden", "text/plain")
        if not file_path.is_file():
            return self._send(404, b"not found", "text/plain")
        data = file_path.read_bytes()
        ctype = "text/plain"
        if file_path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif file_path.suffix == ".css":
            ctype = "text/css"
        elif file_path.suffix == ".js":
            ctype = "application/javascript"
        elif file_path.suffix == ".svg":
            ctype = "image/svg+xml"
        return self._send(200, data, ctype)


# ThreadingHTTPServer exists on 3.7+ as of 3.7 — actually ThreadingHTTPServer was added in 3.7.
# Confirm: yes Python 3.7 has http.server.ThreadingHTTPServer.

def serve(
    host: str,
    port: int,
    controller: "Controller",
    library: "Library",
    events: "EventLog",
    gpio: Any = None,
) -> ThreadingHTTPServer:
    Handler.controller = controller
    Handler.library = library
    Handler.events = events
    Handler.gpio = gpio
    httpd = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
