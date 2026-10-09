# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The services' uptime, for the folded grid under /admin → Overview's services.
The hub samples every service's state every five minutes (one
`systemctl show`, no root) and keeps hourly buckets for 72 days.

Per unit, one three-character group per hour, from the hour numbered `first` (Unix time // 3600):
samples up and samples taken (a hex digit each, at most 12 in an hour), and how many times it
started in that hour (0-9: a start is a new InvocationID; a reboot starts them all). "000" is an
hour with no samples. About 2.5 KB per service:

  $HUB_STATE_DIR/service-history.json
  {"units": {"kiwix.service": {"first": 497592, "s": "cc0cc0bc1..."}}, "ids": {"kiwix.service": "<InvocationID>"}}

Only sampled while the clock is trusted (network time, or set by the owner: rtc.py's mark in
/run/irate-box), as the network history is. Stdlib only.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
FILE = STATE / "service-history.json"
RUN = Path(os.environ.get("HUB_RUN_DIR", "/run/irate-box"))
EVERY = 300
HOUR = 3600
KEEP = 72 * 24


def clock_trusted():
    if (RUN / "clock-trusted").exists():
        return True
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=5)
        return out.stdout.strip() == "yes"
    except (OSError, subprocess.SubprocessError):
        return False


def look(units):
    """{unit: (active?, InvocationID)} for the installed ones, from one `systemctl show`."""
    try:
        out = subprocess.run(["systemctl", "show", "--property=Id,LoadState,ActiveState,InvocationID", *units],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for block in out.strip().split("\n\n"):
        f = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        if f.get("Id") and f.get("LoadState") not in ("not-found", "", None):
            found[f["Id"]] = (f.get("ActiveState") == "active", f.get("InvocationID", ""))
    return found


def record(hist, states, now):
    """One sample of every unit's state into hist (in place). states: {unit: (active?, id)}."""
    idx = int(now // HOUR)
    units, ids = hist.setdefault("units", {}), hist.setdefault("ids", {})
    for unit, (active, inv) in states.items():
        started = int(bool(inv) and unit in ids and ids[unit] != inv)
        if inv:
            ids[unit] = inv
        e = units.get(unit)
        if not e or not e.get("s"):
            e = units[unit] = {"first": idx, "s": "000"}
        last = e["first"] + len(e["s"]) // 3 - 1
        if idx < last:
            continue                    # the clock went back: the hours ahead stand
        if idx - last > KEEP:
            e["first"], e["s"], last = idx, "000", idx
        elif idx > last:
            e["s"] += "000" * (idx - last)
            last = idx
        up, n, r = int(e["s"][-3], 16), int(e["s"][-2], 16), int(e["s"][-1])
        e["s"] = e["s"][:-3] + f"{min(15, up + active):x}{min(15, n + 1):x}{min(9, r + started)}"
        if len(e["s"]) > 3 * KEEP:
            cut = len(e["s"]) // 3 - KEEP
            e["first"] += cut
            e["s"] = e["s"][3 * cut:]
    return hist


def _hours(e, start, end):
    """[(up, n, restarts)] for hours [start, end), (0, 0, 0) where nothing was sampled."""
    out = []
    for h in range(start, end):
        i = h - e["first"]
        g = e["s"][3 * i:3 * i + 3] if 0 <= i < len(e["s"]) // 3 else "000"
        out.append((int(g[0], 16), int(g[1], 16), int(g[2])))
    return out


def _cell(hours):
    up, n, r = (sum(x[k] for x in hours) for k in range(3))
    return {"up": round(up / n, 3), "n": n, "restarts": r} if n else None


def _midnight(t):
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def summarize(hist, now=None):
    """Per unit: the last 72 hours by hour (oldest first) and the last 72 by day, in the box's
    local time (githubstatus.com's format), with the days' labels; and those hours' share up and
    restarts."""
    now = time.time() if now is None else now
    today = _midnight(now)
    anchor = int(now // HOUR)                 # the hour now in progress
    days = []
    for back in range(71, -1, -1):
        s = _midnight(today - back * 86400 + 43200)
        days.append((s, _midnight(s + 36 * 3600)))
    label = lambda s: {"date": time.strftime("%Y-%m-%d", time.localtime(s)), "label": time.strftime("%a %d", time.localtime(s))}  # noqa: E731
    hour_at = lambda i: (anchor - 71 + i) * HOUR  # noqa: E731
    hour_cols = [time.strftime("%H:00", time.localtime(hour_at(i))) if i % 12 == 0 else "" for i in range(72)]
    hour_full = [time.strftime("%a %d %H:00", time.localtime(hour_at(i)))
                 + "–" + time.strftime("%H:00", time.localtime(hour_at(i) + HOUR)) for i in range(72)]
    out = {"month_days": [label(s) for s, _ in days], "hour_cols": hour_cols, "hour_full": hour_full, "units": {}}
    for unit, e in sorted((hist or {}).get("units", {}).items()):
        if not isinstance(e, dict) or not isinstance(e.get("first"), int) or not isinstance(e.get("s"), str):
            continue
        hours = [_cell(_hours(e, anchor - 71 + i, anchor - 71 + i + 1)) for i in range(72)]
        month = [_cell(_hours(e, int(s // HOUR), int(t // HOUR))) for s, t in days]
        seen = [c for c in hours if c]
        n = sum(c["n"] for c in seen)
        out["units"][unit] = {"hours": hours, "month": month, "summary": {
            "up": round(sum(c["up"] * c["n"] for c in seen) / n, 4) if n else None,
            "restarts": sum(c["restarts"] for c in seen)}}
    return out


def load():
    try:
        h = json.loads(FILE.read_text())
        return h if isinstance(h, dict) else {}
    except (OSError, ValueError):
        return {}


class Sampler(threading.Thread):
    """Every five minutes, on the five minutes: every unit's state, while the clock is trusted."""

    def __init__(self, units, trusted=clock_trusted, every=EVERY):
        super().__init__(daemon=True, name="svchistory")
        self.units, self.trusted, self.every = units, trusted, every
        self.stopping = threading.Event()

    def tick(self, now=None):
        now = time.time() if now is None else now
        if not self.trusted():
            return False
        states = look(self.units())
        if not states:
            return False
        hist = record(load(), states, now)
        tmp = FILE.with_name(FILE.name + ".tmp")
        tmp.write_text(json.dumps(hist, separators=(",", ":")))
        os.replace(tmp, FILE)
        return True

    def run(self):
        while not self.stopping.wait(self.every - time.time() % self.every + 1):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - a bad sample must not end the sampling
                print(f"svchistory: {exc}")
