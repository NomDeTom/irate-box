# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Bounded five-minute memory and disk samples for the admin Overview."""

import json
import math
import os
import threading
import time
from pathlib import Path

from irate_box.hub import jsonlog, svchistory

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
FILE = STATE / "memory-history.json"
PROC = Path("/proc")
EVERY = 300
KEEP = 7 * 86400
LIMIT = KEEP // EVERY + 1


def memory():
    try:
        mem = {
            key: int(value.split()[0]) * 1024
            for key, value in (line.split(":", 1) for line in (PROC / "meminfo").read_text().splitlines())
        }
        return {
            "total": mem["MemTotal"],
            "available": mem["MemAvailable"],
            "swap_total": mem.get("SwapTotal", 0),
            "swap_free": mem.get("SwapFree", 0),
        }
    except (OSError, KeyError, ValueError, IndexError):
        return {}


def disk():
    """The hub's storage, as the disk meter reads it: the filesystem of the state folder."""
    try:
        st = os.statvfs(STATE)
    except OSError:
        return {}
    total = st.f_blocks * st.f_frsize
    return {"disk_total": total, "disk_free": min(st.f_bavail * st.f_frsize, total)} if total else {}


def _disk_ok(sample):
    """A sample's disk fields when they are sound; a sample from before they were kept, or with bad ones, keeps its memory."""
    t, f = sample.get("disk_total"), sample.get("disk_free")
    return (
        isinstance(t, (int, float))
        and isinstance(f, (int, float))
        and math.isfinite(t)
        and math.isfinite(f)
        and t > 0
        and 0 <= f <= t
    )


def processes():
    found = []
    page = os.sysconf("SC_PAGE_SIZE")
    for folder in PROC.glob("[0-9]*"):
        try:
            resident = int((folder / "statm").read_text().split()[1]) * page
            found.append({"pid": int(folder.name), "name": (folder / "comm").read_text().strip(), "resident": resident})
        except (OSError, ValueError, IndexError):
            continue
    return sorted(found, key=lambda entry: entry["resident"], reverse=True)[:5]


def _legacy(text):
    """The older form's samples (one JSON object holding them all), or None for lines of samples."""
    try:
        whole = json.loads(text)
    except ValueError:
        return None
    if isinstance(whole, dict) and "samples" in whole:
        return whole["samples"] if isinstance(whole["samples"], list) else []
    return None


def load():
    """The samples kept: a line each (jsonlog), or the older single JSON object."""
    try:
        text = FILE.read_text()
    except (OSError, ValueError):
        return {"samples": []}
    old = _legacy(text)
    samples = old if old is not None else jsonlog.read(FILE)
    kept = [
        sample
        for sample in samples
        if isinstance(sample, dict)
        and all(
            isinstance(sample.get(key), (int, float)) and math.isfinite(sample[key])
            for key in ("at", "total", "available", "swap_total", "swap_free")
        )
        and sample["total"] > 0
        and 0 <= sample["available"] <= sample["total"]
        and 0 <= sample["swap_free"] <= sample["swap_total"]
    ][-LIMIT:]
    return {
        "samples": [
            s if _disk_ok(s) else {k: v for k, v in s.items() if k not in ("disk_total", "disk_free")} for s in kept
        ]
    }


def record(hist, mem, now):
    if not mem.get("total"):
        return False
    samples = hist.setdefault("samples", [])
    if samples and now < samples[-1]["at"] + EVERY:
        return False
    samples.append(dict(mem, at=int(now)))
    hist["samples"] = [sample for sample in samples if now - KEEP <= sample["at"] <= now][-LIMIT:]
    return True


# The largest processes at the last sample: shown while fresh, kept in memory only (a table that is
# replaced every five minutes has no need to be on the card).
_processes = {"at": None, "list": []}


def snapshot(now=None):
    now = time.time() if now is None else now
    hist = load()
    samples = [sample for sample in hist["samples"] if now - KEEP <= sample["at"] <= now]
    fresh = bool(samples) and now - samples[-1]["at"] <= EVERY * 2
    shown = fresh and _processes["at"] == samples[-1]["at"]
    return {
        "now": int(now),
        "every": EVERY,
        "samples": samples,
        "processes": _processes["list"] if shown else [],
        "processes_at": samples[-1]["at"] if shown else None,
    }


class Sampler(threading.Thread):
    def __init__(self, trusted=svchistory.clock_trusted, every=EVERY):
        super().__init__(daemon=True, name="memhistory")
        self.trusted, self.every = trusted, every
        self.stopping = threading.Event()

    def tick(self, now=None):
        if not self.trusted():
            return False
        now = time.time() if now is None else now
        hist = load()
        mem = memory()
        if not record(hist, {**mem, **disk()} if mem else {}, now):
            return False
        sample = hist["samples"][-1]
        _processes.update(at=sample["at"], list=processes())
        # A line a sample; the whole file rewritten only from the older form or once it holds twice
        # what is kept.
        try:
            legacy = _legacy(FILE.read_text()) is not None
        except OSError:
            legacy = False
        if legacy or not jsonlog.append(FILE, [sample]) or jsonlog.count(FILE) > 2 * LIMIT:
            jsonlog.rewrite(FILE, hist["samples"])
        return True

    def run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - a bad tick must not end the sampling
                print(f"memhistory: {exc}")
            self.stopping.wait(self.every - time.time() % self.every + 1)
