# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A notebook per login: a SilverBullet of its own for each signed-in person, beside the shared notes.

Each runs only while its owner uses it. The hub keeps the list of the notebooks that should be
running (WANTED, in the hub's state); the root helper's irate-box-notebooks unit, started when
that file changes, starts and stops one irate-box-notebook@<name> unit per entry. The web server
asks the hub (/_irate/notebook) on every request to the notebooks' origin, which tells it whose
socket to use and marks the notebook as in use. A notebook unused for IDLE seconds, or whose owner
no longer has a session, comes off the list. The folders stay where they are when a notebook
stops, when the box goes back to communal notes, and when an account is removed.
"""

import json
import os
import re
import threading
import time
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
ROOT = STATE / "notebooks"
WANTED = ROOT / ".wanted.json"
SOCKETS = Path(os.environ.get("HUB_NOTEBOOK_SOCKETS", "/run/irate-box-notebooks"))
NAME_RE = re.compile(r"^[a-z0-9_-][a-z0-9._-]{2,31}$")
IDLE = 30 * 60
HARD_MAX = 10
PORTS = {
    "http": int(os.environ.get("HUB_NOTEBOOK_PORT", "8094")),
    "https": int(os.environ.get("HUB_NOTEBOOK_TLS_PORT", "8494")),
}
MODES = ("", "private", "communal")
MAX_CHOICES = (1, 2, 3, 4, 5, 6, 8, 10)
FREE_CHOICES = (48, 64, 96, 128, 192)

INDEX = """# My notebook

This notebook is yours alone: only you can open it, once signed in. The shared notes, which
everyone with notes access can read and change, are separate.

It runs while you use it and stops about half an hour after you last did, or when you sign out.
Your account page can download all of it as a zip.
"""


def key(name):
    """A login's notebook name, or None for a name that cannot be one (the accounts' names are
    lower-cased already; a leading dot would hide its folder)."""
    k = str(name or "").lower()
    return k if NAME_RE.match(k) else None


def mode(st, signup):
    """private or communal: the admin's choice, or by default private wherever people may sign in."""
    chosen = st.get("notebooks", "")
    if chosen in ("private", "communal"):
        return chosen
    return "communal" if signup == "off" else "private"


def socket_path(k):
    return SOCKETS / k / "sb.sock"


def available_mb():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


class Notebooks:
    def __init__(self, listening, live, settings, signup, web="nginx"):
        """listening(path): is a socket answering; live(k): does that login still have a session;
        settings(): the hub's settings; signup(): the accounts' sign-up setting."""
        self.listening, self.live, self.settings, self.signup = listening, live, settings, signup
        self.web = web
        self.lock = threading.Lock()
        self.used = {}
        # None running as the hub starts: any left from before stop, and open again when next asked.
        self.wanted = set()
        if WANTED.exists():
            try:
                self._write()
            except OSError as exc:
                print(f"notebooks: {exc}")

    def _write(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        tmp = WANTED.with_name(WANTED.name + ".tmp")
        tmp.write_text(json.dumps({"run": sorted(self.wanted)}))
        os.replace(tmp, WANTED)

    def mode(self):
        return mode(self.settings(), self.signup())

    def supported(self):
        return self.web == "nginx"

    def folder(self, k):
        return ROOT / k

    def _seed(self, k):
        d = self.folder(k)
        if not d.is_dir():
            d.mkdir(parents=True)
            (d / "index.md").write_text(INDEX)

    def gate(self, me):
        """The web server's question for a request to the notebooks' origin: 204 and whose
        notebook (running), 401 (not signed in), 403 (not running: the waiting page), 404 (off)."""
        if not me:
            return 401, None
        k = key(me.get("name"))
        if k is None or self.mode() != "private" or not self.supported():
            return 404, None
        with self.lock:
            if k in self.wanted and self.listening(str(socket_path(k))):
                self.used[k] = time.monotonic()
                return 204, k
        return 403, None

    def status(self, me):
        """For the account page and the waiting page: where this login's notebook stands."""
        if not me:
            return {"state": "signed-out"}
        k = key(me.get("name"))
        if not self.supported():
            return {"state": "unsupported"}
        if k is None or self.mode() != "private":
            return {"state": "off"}
        with self.lock:
            running = k in self.wanted
            ready = running and self.listening(str(socket_path(k)))
        return {
            "state": "ready" if ready else "starting" if running else "stopped",
            "running": len(self.wanted),
            "max": self.settings()["notebooks_max"],
            "ports": PORTS,
        }

    def open(self, me):
        """Asked to open: put it on the list if there is room (a count and the memory kept free)."""
        st = self.status(me)
        if st["state"] != "stopped":
            return st
        k = key(me.get("name"))
        conf = self.settings()
        with self.lock:
            if k not in self.wanted:
                if len(self.wanted) >= min(conf["notebooks_max"], HARD_MAX):
                    return dict(st, state="full")
                free = available_mb()
                if free is not None and free < conf["notebooks_keep_free_mb"]:
                    return dict(st, state="memory", free_mb=free, keep_mb=conf["notebooks_keep_free_mb"])
                self._seed(k)
                self.wanted.add(k)
                self.used[k] = time.monotonic()
                self._write()
        return self.status(me)

    def close(self, name):
        """Its owner signed out: stopped now, not after the idle time."""
        k = key(name)
        with self.lock:
            if k in self.wanted:
                self.wanted.discard(k)
                self.used.pop(k, None)
                self._write()

    def reap(self, now=None):
        """Off the list: unused for IDLE, no session left, or the box back to communal notes."""
        now = time.monotonic() if now is None else now
        private = self.mode() == "private" and self.supported()
        with self.lock:
            keep = {k for k in self.wanted if private and self.live(k) and now - self.used.setdefault(k, now) < IDLE}
            if keep != self.wanted:
                for k in self.wanted - keep:
                    self.used.pop(k, None)
                self.wanted = keep
                self._write()
                return True
        return False

    def view(self):
        """For /admin: who has one, and which are running."""
        names = sorted(p.name for p in ROOT.iterdir() if p.is_dir() and NAME_RE.match(p.name)) if ROOT.is_dir() else []
        with self.lock:
            return {
                "mode": self.mode(),
                "supported": self.supported(),
                "notebooks": names,
                "running": sorted(self.wanted),
                "free_mb": available_mb(),
            }


class Reaper(threading.Thread):
    def __init__(self, books, every=60):
        super().__init__(daemon=True, name="notebooks")
        self.books, self.every = books, every
        self.stopping = threading.Event()

    def run(self):
        while not self.stopping.wait(self.every):
            try:
                self.books.reap()
            except Exception as exc:
                print(f"notebooks: {exc}")
