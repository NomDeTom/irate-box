"""Bounded five-minute memory samples for the admin Overview."""

import json
import math
import os
import threading
import time
from pathlib import Path

from irate_box.hub import svchistory

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
FILE = STATE / "memory-history.json"
PROC = Path("/proc")
EVERY = 300
KEEP = 7 * 86400
LIMIT = KEEP // EVERY + 1


def memory():
    try:
        mem = {key: int(value.split()[0]) * 1024
               for key, value in (line.split(":", 1) for line in (PROC / "meminfo").read_text().splitlines())}
        return {"total": mem["MemTotal"], "available": mem["MemAvailable"],
                "swap_total": mem.get("SwapTotal", 0), "swap_free": mem.get("SwapFree", 0)}
    except (OSError, KeyError, ValueError, IndexError):
        return {}


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


def load():
    try:
        hist = json.loads(FILE.read_text())
        if not isinstance(hist, dict) or not isinstance(hist.get("samples"), list):
            return {"samples": []}
        hist["samples"] = [sample for sample in hist["samples"] if isinstance(sample, dict)
                           and all(isinstance(sample.get(key), (int, float)) and math.isfinite(sample[key])
                                   for key in ("at", "total", "available", "swap_total", "swap_free"))
                           and sample["total"] > 0 and 0 <= sample["available"] <= sample["total"]
                           and 0 <= sample["swap_free"] <= sample["swap_total"]][-LIMIT:]
        return hist
    except (OSError, ValueError):
        return {"samples": []}


def record(hist, mem, now):
    if not mem.get("total"):
        return False
    samples = hist.setdefault("samples", [])
    if samples and now < samples[-1]["at"] + EVERY:
        return False
    samples.append(dict(mem, at=int(now)))
    hist["samples"] = [sample for sample in samples if now - KEEP <= sample["at"] <= now][-LIMIT:]
    return True


def snapshot(now=None):
    now = time.time() if now is None else now
    hist = load()
    samples = [sample for sample in hist["samples"] if now - KEEP <= sample["at"] <= now]
    fresh = bool(samples) and now - samples[-1]["at"] <= EVERY * 2
    return {"now": int(now), "every": EVERY,
            "samples": samples, "processes": hist.get("processes", []) if fresh else [],
            "processes_at": samples[-1]["at"] if fresh else None}


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
        if not record(hist, memory(), now):
            return False
        hist["processes"] = processes()
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_name(FILE.name + ".tmp")
        tmp.write_text(json.dumps(hist, separators=(",", ":")))
        os.replace(tmp, FILE)
        return True

    def run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:
                print(f"memhistory: {exc}")
            self.stopping.wait(self.every - time.time() % self.every + 1)