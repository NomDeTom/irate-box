#!/usr/bin/env python3
"""Irate-Box hub server: shoutbox, board, blob store, status, captive-portal target.

Serves static/ itself so a bare `python3 server.py` works; behind Caddy the assets are
served by file_server and only `/` and the API reach this process."""

import json
import os
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import board
import hubclock
import store

STATIC = Path(__file__).parent / "static"
# Mutable state lives outside the code directory so packaging can point it at
# /var/lib/hub and `apt purge` cannot eat anyone's messages.
STATE_DIR = Path(os.environ.get("HUB_STATE_DIR", Path(__file__).parent))
DATA_FILE = STATE_DIR / "messages.json"
MAX_MESSAGES = 200
# Shoutbox entries expire against powered-on time, not the wall clock, which does not
# exist on this hardware. See hubclock.py.
SHOUT_TTL = 24 * 3600
PORT = int(os.environ.get("PORT", 8000))
BIND = os.environ.get("HUB_BIND", "0.0.0.0")  # 127.0.0.1 when Caddy is in front
# Where captive-portal probes are sent. Unset, the redirect is relative and lands on
# whatever address the probe arrived at -- fine on localhost, and on the AP every name
# resolves to the hub anyway. The Pi's unit sets the real origin, http://192.168.4.1/,
# so the sign-in sheet shows an address a guest can type again later.
HUB_URL = os.environ.get("HUB_URL", "/")

CLOCK = hubclock.HubClock(STATE_DIR / "clock.json")
BOARD = board.Board(STATE_DIR / "board.json", CLOCK)
# Excalidraw's storage API and the saved-work gallery. See store.py.
STORE = store.Store(STATE_DIR / "store", CLOCK)

# Hosts and paths used by OSes to detect captive portals.
# We redirect them to the hub, which triggers the "sign in to network" popup.
CAPTIVE_HOSTS = {
    "captive.apple.com",
    "www.apple.com",
    "connectivitycheck.gstatic.com",
    "clients3.google.com",
    "www.msftconnecttest.com",
    "www.msftncsi.com",
    "detectportal.firefox.com",
    "nmcheck.gnome.org",
}

CAPTIVE_PATHS = {
    "/hotspot-detect.html",       # iOS / macOS
    "/library/test/success.html", # older iOS
    "/generate_204",              # Android
    "/connecttest.txt",           # Windows
    "/ncsi.txt",                  # Windows (legacy)
    "/canonical.html",            # Firefox
    "/check_network_status.txt",  # GNOME
}

lock = threading.Lock()


def load_messages():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except ValueError:
            return []  # torn write from a power cut
    return []


def live_messages(now):
    """Messages that have not aged out. Reads do not rewrite the file."""
    return [m for m in load_messages() if now - m.get("created", now) <= SHOUT_TTL]


# What sits behind Caddy, keyed by the path the tile links to. Interim until the
# apps.d/ manifests in the plan replace this table. `port` is a loopback listener to
# probe; `static` means Caddy serves files itself, so it is up whenever Caddy is.
SERVICES = [
    {"path": "/draw/", "static": True},
    {"path": "/serial/", "port": 8080},
    {"path": "/wiki/", "port": 8081},
    {"path": "/mermaid/", "static": True},
    {"path": "/tools/", "static": True},
    {"path": "/term/", "port": 7681},
]
STATUS_CACHE_S = 5  # one probe sweep per this many seconds, shared by every client
_status_cache = {"at": 0.0, "ports": {}}

# --- who is here -------------------------------------------------------------
# "Online" is distinct client addresses seen in the last ONLINE_WINDOW seconds of
# powered-on time. Every open page already polls /status and /messages, so this needs
# no heartbeat of its own -- ordinary traffic is the signal. Addresses are held in
# memory only: never written to disk, never served individually, and gone on restart.
# The page is only ever told how many.
ONLINE_WINDOW = 90
# Optional second number: devices that joined the WiFi at all, whether or not anyone
# opened the page. That is the PirateBox station counter, and dnsmasq already keeps it.
LEASES = Path(os.environ.get("HUB_LEASES", "/var/lib/misc/dnsmasq.leases"))
_seen = {}
_seen_lock = threading.Lock()


def note_client(handler):
    """Record that this address is around. Behind Caddy every request arrives from
    loopback, so the forwarded address is what distinguishes one guest from another."""
    fwd = handler.headers.get("X-Forwarded-For", "")
    addr = fwd.split(",")[0].strip() if fwd.strip() else handler.client_address[0]
    if not addr:
        return
    with _seen_lock:
        _seen[addr] = CLOCK.ticks()


# --- operator settings -------------------------------------------------------
# Options the person who owns the box sets, not the guests. Only booleans listed in
# DEFAULTS are accepted, so a malformed or hand-edited file cannot introduce keys.
# The gate is Caddy's basic_auth on /admin/*, the same treatment /term/ gets -- run
# the hub bare, with no Caddy in front, and these are as open as every other hub API.
SETTINGS_FILE = STATE_DIR / "settings.json"
DEFAULT_SETTINGS = {
    # The ttyd card is shown like any other service -- greyed while its unit is off --
    # unless the operator would rather guests were not shown the escape hatch at all.
    "show_term_card": True,
}
_settings_lock = threading.Lock()


def load_settings():
    data = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE) as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return data
    if isinstance(stored, dict):
        for key in DEFAULT_SETTINGS:
            if isinstance(stored.get(key), bool):
                data[key] = stored[key]
    return data


def save_settings(data):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.parent / (SETTINGS_FILE.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, SETTINGS_FILE)


# Held in memory so a page full of /status pollers costs no disk reads.
_settings = load_settings()


def settings_snapshot():
    with _settings_lock:
        return dict(_settings)


def online_count(now):
    with _seen_lock:
        for addr, seen in list(_seen.items()):
            if now - seen > ONLINE_WINDOW:
                del _seen[addr]
        return len(_seen)


def joined_count():
    """Devices with a DHCP lease, or None where there is no dnsmasq (i.e. in dev).
    Lease expiry is a wall-clock timestamp, which is fiction on a board with no RTC,
    so the lines are counted rather than filtered -- dnsmasq prunes the file itself."""
    try:
        with open(LEASES) as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return None


def port_listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def service_status(proxied):
    """Per-service up/down. Port probes are cached so a page full of pollers costs
    one sweep per STATUS_CACHE_S, not one per request."""
    now = time.monotonic()
    if now - _status_cache["at"] > STATUS_CACHE_S:
        _status_cache["ports"] = {
            svc["port"]: port_listening(svc["port"]) for svc in SERVICES if "port" in svc
        }
        _status_cache["at"] = now
    out = []
    for svc in SERVICES:
        if svc.get("static"):
            up = proxied
        else:
            up = proxied and _status_cache["ports"].get(svc["port"], False)
        out.append({"path": svc["path"], "up": up})
    return out


def save_messages(msgs):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_FILE.parent / (DATA_FILE.name + ".tmp")
    with open(tmp, "w") as fh:
        json.dump(msgs, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, DATA_FILE)


def thread_id(path):
    """Extract N from /board/thread/N, or None if the path is not one."""
    prefix = "/board/thread/"
    if not path.startswith(prefix):
        return None
    try:
        return int(path[len(prefix):].strip("/"))
    except ValueError:
        return None


MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    # Socket timeout per request. A phone that stalls mid-upload releases its thread
    # instead of pinning it; Caddy in front already shields the listener itself.
    timeout = 30
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # quiet

    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_empty(self, code):
        # Under HTTP/1.1 keep-alive every response needs a length, or the client
        # waits for a body that never comes.
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _is_captive_probe(self):
        host = self.headers.get("Host", "").split(":")[0]
        path = self.path.split("?")[0]
        return host in CAPTIVE_HOSTS or path in CAPTIVE_PATHS

    def _redirect_to_hub(self):
        self.send_response(302)
        self.send_header("Location", HUB_URL)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        note_client(self)
        if self._is_captive_probe():
            self._redirect_to_hub()
            return

        path = self.path.split("?")[0]

        if store.handle(self, "GET", path, STORE):
            return

        if path == "/messages":
            now = CLOCK.ticks()
            with lock:
                msgs = live_messages(now)
            self.send_json(200, {"now": now, "ttl": SHOUT_TTL, "messages": msgs})
            return

        if path == "/board/threads":
            self.send_json(200, BOARD.list_threads())
            return

        if path == "/admin/settings":
            self.send_json(200, settings_snapshot())
            return

        if path in ("/admin", "/admin/"):
            path = "/admin.html"

        if path == "/status":
            # Caddy's reverse_proxy adds X-Forwarded-For; a direct hit has none.
            proxied = "X-Forwarded-For" in self.headers
            now = CLOCK.ticks()
            payload = {
                "proxied": proxied,
                "services": service_status(proxied),
                "uptime": now,
                "online": online_count(now),
                "settings": settings_snapshot(),
            }
            joined = joined_count()
            if joined is not None:
                payload["joined"] = joined
            self.send_json(200, payload)
            return

        tid = thread_id(path)
        if tid is not None:
            result = BOARD.get_thread(tid)
            if result is None:
                self.send_json(404, {"error": "no such thread"})
            else:
                self.send_json(200, result)
            return

        if path == "/":
            path = "/index.html"
        file_path = STATIC / path.lstrip("/")

        if not file_path.resolve().is_relative_to(STATIC.resolve()):
            self.send_empty(403)
            return

        if file_path.is_file():
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", MIME.get(file_path.suffix, "application/octet-stream"))
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_empty(404)

    def _read_payload(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return None

    def do_POST(self):
        note_client(self)
        path = self.path.split("?")[0]

        # Delegated before the body is read: the store takes raw bytes, and the
        # Excalidraw frontend sends no Content-Type for JSON to be parsed from.
        if store.handle(self, "POST", path, STORE):
            return

        payload = self._read_payload()
        if payload is None:
            self.send_json(400, {"error": "bad request"})
            return

        if path == "/admin/settings":
            with _settings_lock:
                for key in DEFAULT_SETTINGS:
                    if isinstance(payload.get(key), bool):
                        _settings[key] = payload[key]
                current = dict(_settings)
                save_settings(current)
            self.send_json(200, current)
            return

        if path == "/messages":
            self._post_message(payload)
            return

        if path == "/board/threads":
            try:
                result = BOARD.create_thread(payload.get("name"),
                                             payload.get("title"),
                                             payload.get("text"),
                                             payload.get("hue"))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(201, result)
            return

        tid = thread_id(path)
        if tid is not None:
            try:
                result = BOARD.reply(tid, payload.get("name"), payload.get("text"),
                                     payload.get("hue"))
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            if result is None:
                self.send_json(404, {"error": "no such thread"})
            else:
                self.send_json(201, result)
            return

        self.send_empty(404)

    def do_PUT(self):
        if not store.handle(self, "PUT", self.path.split("?")[0], STORE):
            self.send_empty(404)

    def do_DELETE(self):
        if not store.handle(self, "DELETE", self.path.split("?")[0], STORE):
            self.send_empty(404)

    def do_PATCH(self):
        if not store.handle(self, "PATCH", self.path.split("?")[0], STORE):
            self.send_empty(404)

    def do_OPTIONS(self):
        if not store.handle(self, "OPTIONS", self.path.split("?")[0], STORE):
            self.send_empty(404)

    def _post_message(self, payload):
        name = str(payload.get("name", "")).strip()[:32]
        text = str(payload.get("text", "")).strip()[:200]
        if not name or not text:
            self.send_json(400, {"error": "name and text required"})
            return

        # Optional author colour, stored as a hue only (0-359) -- the client's palette
        # picks saturation and lightness. Absent means "derive it from the name", so a
        # guest who renames re-colours instead of carrying a stale hue around.
        hue = payload.get("hue")
        if hue is not None:
            try:
                hue = int(hue) % 360
            except (TypeError, ValueError):
                hue = None

        now = CLOCK.ticks()
        entry = {
            "name": name,
            "text": text,
            "created": now,
            # Kept for debugging only. On a box with no RTC this is fiction; nothing
            # reads it, and nothing should.
            "time": datetime.now(timezone.utc).isoformat(),
        }
        if hue is not None:
            entry["hue"] = hue
        with lock:
            msgs = live_messages(now)
            msgs.append(entry)
            if len(msgs) > MAX_MESSAGES:
                msgs = msgs[-MAX_MESSAGES:]
            save_messages(msgs)

        self.send_json(201, entry)


if __name__ == "__main__":
    CLOCK.start()
    # One thread per request: a 50 MB paste into the blob store must not freeze
    # everyone else's shoutbox poll. State is guarded by `lock` and the store's own.
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    print(f"Hub running at http://{BIND}:{PORT}")
    print(f"Uptime clock at {hubclock.format_age(CLOCK.ticks())} cumulative")
    try:
        server.serve_forever()
    finally:
        CLOCK.stop()
