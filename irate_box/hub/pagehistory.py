# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The use of the pages the web server serves itself (Excalidraw, Mermaid, the calculators, the serial
terminal, the flashers), which have no service of their own to sample: their heatmap shows an hour as
blank (not used), worked (used, every load answered) or failed (a load got an error), from nginx's log of
page loads (/run/irate-box/pagelog/pages.log, in RAM: "<time> <status> <path>" a line, nothing about who asked).

The hub reads what is new every five minutes (the file emptied by logrotate past 1 MB: read from the start
again when it shrinks) and keeps hourly counts for 72 days:

  $HUB_STATE_DIR/page-history.json
  {"offset": n, "inode": n, "pages": {"/draw/": {"first": <hour>, "u": [loads...], "e": [failed...]}}}

A load failed when its status was 5xx (the server) or 404 (the page's files missing). Stdlib only.
"""

import json
import os
import threading
import time
from pathlib import Path

from irate_box.hub import svchistory

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
# In RAM (/run, made at each boot by install.sh's tmpfiles rule): a reboot starts it empty, and the hub, which keeps
# its place by offset and inode, starts again from its start.
LOG = Path(os.environ.get("HUB_PAGELOG", "/run/irate-box/pagelog/pages.log"))
FILE = STATE / "page-history.json"
HOUR = 3600
KEEP = 72 * 24
MAX_READ = 2_000_000  # at most this much of the log in one look


def failed(status):
    return status >= 500 or status == 404


def page_of(uri, paths):
    """The page a path belongs to: the longest of paths it starts with, or None."""
    best = None
    for p in paths:
        if uri.startswith(p) and (best is None or len(p) > len(best)):
            best = p
    return best


def load():
    try:
        h = json.loads(FILE.read_text())
        return h if isinstance(h, dict) else {}
    except (OSError, ValueError):
        return {}


def add(hist, path, at, bad):
    """One page load into hist (in place)."""
    idx = int(at // HOUR)
    pages = hist.setdefault("pages", {})
    e = pages.get(path)
    if not isinstance(e, dict) or not isinstance(e.get("first"), int):
        e = pages[path] = {"first": idx, "u": [0], "e": [0]}
    last = e["first"] + len(e["u"]) - 1
    if idx < e["first"]:
        return  # older than kept: the clock was wrong then
    if idx > last:
        gap = idx - last
        e["u"] += [0] * gap
        e["e"] += [0] * gap
    i = idx - e["first"]
    e["u"][i] += 1
    e["e"][i] += int(bad)
    if len(e["u"]) > KEEP:
        cut = len(e["u"]) - KEEP
        e["first"] += cut
        e["u"], e["e"] = e["u"][cut:], e["e"][cut:]


def read_new(hist, paths, log=LOG):
    """The log's new lines into hist (in place); returns how many page loads were counted."""
    try:
        st = log.stat()
    except OSError:
        return 0
    off = hist.get("offset", 0) if hist.get("inode") == st.st_ino else 0
    if st.st_size < off:
        off = 0  # emptied by logrotate: from the start again
    n = 0
    with open(log, "rb") as fh:
        fh.seek(off)
        data = fh.read(MAX_READ)
    end = data.rfind(b"\n") + 1  # a line half-written stays for the next look
    for line in data[:end].decode("utf-8", "replace").splitlines():
        parts = line.split(" ", 2)
        if len(parts) != 3:
            continue
        try:
            at, status = float(parts[0]), int(parts[1])
        except ValueError:
            continue
        page = page_of(parts[2], paths)
        if page:
            add(hist, page, at, failed(status))
            n += 1
    hist["offset"], hist["inode"] = off + end, st.st_ino
    return n


def _cell(e, start, end):
    """{up, n, uses, errors} for hours [start, end), or None when the page wasn't loaded then."""
    u = er = 0
    for h in range(start, end):
        i = h - e["first"]
        if 0 <= i < len(e["u"]):
            u += e["u"][i]
            er += e["e"][i]
    if not u:
        return None
    return {"up": round((u - er) / u, 3), "n": u, "uses": u, "errors": er}


def summarize(hist, now=None):
    """Per page: the last 72 hours by hour and the last 72 days by day (svchistory's columns), and the
    hours' loads and failures."""
    now = time.time() if now is None else now
    base = svchistory.summarize({}, now)
    today = svchistory._midnight(now)
    anchor = int(now // HOUR)
    days = []
    for back in range(71, -1, -1):
        s = svchistory._midnight(today - back * 86400 + 43200)
        days.append((s, svchistory._midnight(s + 36 * 3600)))
    out = {k: base[k] for k in ("month_days", "hour_cols", "hour_full")}
    out["pages"] = {}
    for path, e in sorted((hist or {}).get("pages", {}).items()):
        if not isinstance(e, dict) or not isinstance(e.get("first"), int):
            continue
        hours = [_cell(e, anchor - 71 + i, anchor - 71 + i + 1) for i in range(72)]
        month = [_cell(e, int(s // HOUR), int(t // HOUR)) for s, t in days]
        seen = [c for c in hours if c]
        out["pages"][path] = {
            "hours": hours,
            "month": month,
            "summary": {"uses": sum(c["uses"] for c in seen), "errors": sum(c["errors"] for c in seen)},
        }
    return out


class Sampler(threading.Thread):
    """Every five minutes: the log's new page loads counted, for the pages paths() names."""

    def __init__(self, paths, every=svchistory.EVERY):
        super().__init__(daemon=True, name="pagehistory")
        self.paths, self.every = paths, every
        self.stopping = threading.Event()

    def tick(self):
        hist = load()
        before = (hist.get("offset"), hist.get("inode"))
        read_new(hist, self.paths())
        if (hist.get("offset"), hist.get("inode")) == before:
            return False
        tmp = FILE.with_name(FILE.name + ".tmp")
        tmp.write_text(json.dumps(hist, separators=(",", ":")))
        os.replace(tmp, FILE)
        return True

    def run(self):
        while not self.stopping.wait(self.every - time.time() % self.every + 2):
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - a bad look must not end the counting
                print(f"pagehistory: {exc}")
