# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What the box writes to its SD card, kept as a history for the Box doctor.

Once an hour, one line appended to card-history.jsonl: the card's bytes written since boot (the kernel's count
for the whole device under /), each service's bytes written to it since it started (cgroup v2 io.stat; needs
per-service I/O accounting, which the Box doctor offers to switch on), the hour's lowest available memory
(sampled every five minutes, in memory), and the write settings in force. Counts since boot, not differences:
the next line of the same boot gives the hour's writes, so a restart of the hub loses nothing. 30 days kept,
about 400 bytes a line. summarize() turns the lines into what the Box doctor shows: writes per day, the services
that write most, memory, and the writes before and after each change of a setting."""

import os
import threading
import time
from pathlib import Path

from irate_box.hub import jsonlog, svchistory

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
FILE = STATE / "card-history.jsonl"
PROC = Path(os.environ.get("HUB_PROC", "/proc"))
SYS = Path(os.environ.get("HUB_SYS", "/sys"))
CGROUP = SYS / "fs" / "cgroup"
EVERY = 3600
SAMPLE = 300
KEEP = 30 * 86400
LIMIT = KEEP // EVERY + 1
UNITS_KEPT = 12  # the services that wrote most since boot, on each line; the rest are "other"


def card():
    """(the whole device under / as "name", its "major:minor"), or None: a partition's parent is the card."""
    try:
        source = next(
            line.split()[-2]
            for line in (PROC / "self" / "mountinfo").read_text().splitlines()
            if line.split()[4] == "/"
        )
        block = (SYS / "class" / "block" / Path(source).name).resolve()
        if (block / "partition").exists():
            block = block.parent
        return block.name, (block / "dev").read_text().strip()
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def size(name):
    """The device's size in bytes, or None."""
    try:
        return int((SYS / "class" / "block" / name / "size").read_text()) * 512
    except (OSError, ValueError):
        return None


def written(name):
    """The device's bytes written since boot (its stat's sectors written), or None."""
    try:
        return int((SYS / "class" / "block" / name / "stat").read_text().split()[6]) * 512
    except (OSError, ValueError, IndexError):
        return None


def units(majmin):
    """{service: bytes written to the device since it started} from cgroup io.stat; {} without I/O accounting."""
    out = {}
    for stat in [*CGROUP.glob("system.slice/*.service/io.stat"), *CGROUP.glob("system.slice/*/*.service/io.stat")]:
        try:
            for line in stat.read_text().splitlines():
                fields = line.split()
                if fields and fields[0] == majmin:
                    w = int(next(f for f in fields[1:] if f.startswith("wbytes=")).split("=", 1)[1])
                    if w:
                        out[stat.parent.name] = w
        except (OSError, ValueError, StopIteration):
            continue
    return out


def accounting():
    """Whether per-service I/O accounting is on (the io controller handed to the services)."""
    try:
        return "io" in (CGROUP / "system.slice" / "cgroup.subtree_control").read_text().split()
    except OSError:
        return False


def memory_controller():
    """Whether the kernel has the memory controller: without it, a service's ordinary (buffered) writes are not
    counted as its own, only the kernel's (Raspberry Pi kernels leave it off unless cgroup_enable=memory)."""
    try:
        return "memory" in (CGROUP / "cgroup.controllers").read_text().split()
    except OSError:
        return False


def mem_available():
    try:
        for line in (PROC / "meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def boot_id():
    try:
        return (PROC / "sys" / "kernel" / "random" / "boot_id").read_text().strip()[:8]
    except OSError:
        return ""


def sample(settings, mem_min, now):
    """One line: what was written since boot, by the card and by each service, and the hour's memory."""
    found = card()
    if not found:
        return None
    total = written(found[0])
    if total is None:
        return None
    by_unit = units(found[1])
    top = dict(sorted(by_unit.items(), key=lambda kv: -kv[1])[:UNITS_KEPT])
    return {
        "at": int(now),
        "boot": boot_id(),
        "card": total,
        "units": top,
        "other": sum(by_unit.values()) - sum(top.values()),
        "accounting": accounting() and memory_controller(),
        "mem_min": mem_min,
        "settings": settings,
    }


def _hours(rows):
    """Each line's hour: bytes written since the line before (the same boot), by the card and by service.
    The first line of a boot counts everything since that boot."""
    out, prev = [], None
    for r in rows:
        same = prev is not None and prev.get("boot") == r.get("boot") and r["card"] >= prev["card"]
        card_bytes = r["card"] - prev["card"] if same else r["card"]
        by = {}
        for u, w in (r.get("units") or {}).items():
            before = (prev.get("units") or {}).get(u, 0) if same else 0
            by[u] = w - before if w >= before else w  # a service restarted: its count began again
        out.append(
            {
                "at": r["at"],
                "card": card_bytes,
                "units": by,
                "mem_min": r.get("mem_min"),
                "settings": r.get("settings") or {},
                "span": r["at"] - prev["at"] if same else None,
            }
        )
        prev = r
    return out


def summarize(rows, now=None):
    """For the Box doctor: bytes written per day (30 days), the services that wrote most in the last 7 days,
    memory, the rate over the last 24 hours, and each change of a setting with the daily writes on either side
    (once 24 hours of each exist)."""
    now = time.time() if now is None else now
    hours = [h for h in _hours(rows) if now - KEEP <= h["at"] <= now]
    days = {}
    for h in hours:
        day = time.strftime("%Y-%m-%d", time.localtime(h["at"] - 1))
        d = days.setdefault(day, {"day": day, "card": 0, "mem_min": None})
        d["card"] += h["card"]
        if h["mem_min"] is not None:
            d["mem_min"] = h["mem_min"] if d["mem_min"] is None else min(d["mem_min"], h["mem_min"])
    week = {}
    for h in hours:
        if h["at"] >= now - 7 * 86400:
            for u, w in h["units"].items():
                week[u] = week.get(u, 0) + w
    last_day = [h for h in hours if h["at"] >= now - 86400]
    changes, before = [], None
    for i, h in enumerate(hours):
        if before is not None and h["settings"] != before:
            changes.append(
                {
                    "at": h["at"],
                    "from": before,
                    "to": h["settings"],
                    "before": _per_day(hours[:i], h["at"] - 86400, h["at"]),
                    "after": _per_day(hours[i:], h["at"], h["at"] + 86400),
                }
            )
        before = h["settings"]
    latest = rows[-1] if rows else {}
    return {
        "days": list(days.values()),
        "week_units": sorted(([u, w] for u, w in week.items()), key=lambda uw: -uw[1])[:8],
        "last_day": sum(h["card"] for h in last_day) if last_day else None,
        "last_day_hours": len(last_day),
        "changes": changes[-10:],
        "accounting": bool(latest.get("accounting")),
        "settings": latest.get("settings") or {},
    }


def _per_day(hours, start, end):
    """Bytes a day over the hours from start to end, or None until the whole window is covered."""
    inside = [h for h in hours if start < h["at"] <= end and h["span"]]
    covered = sum(h["span"] for h in inside)
    if covered < 0.9 * (end - start):
        return None
    return int(sum(h["card"] for h in inside) * 86400 / covered)


def load():
    return [r for r in jsonlog.read(FILE) if isinstance(r.get("at"), (int, float)) and isinstance(r.get("card"), int)]


_cache = {"mtime": None, "rows": []}


def snapshot(now=None):
    """summarize() of the file, read again only when it has changed (a line an hour)."""
    try:
        mtime = FILE.stat().st_mtime_ns
    except OSError:
        mtime = None
    if mtime != _cache["mtime"]:
        _cache.update(mtime=mtime, rows=load())
    found = card()
    return dict(summarize(_cache["rows"], now), card_size=size(found[0]) if found else None)


class Sampler(threading.Thread):
    """Memory every SAMPLE seconds (its lowest kept for the hour), and a line every EVERY seconds."""

    def __init__(self, settings, trusted=svchistory.clock_trusted, every=EVERY):
        super().__init__(daemon=True, name="cardhistory")
        self.settings, self.trusted, self.every = settings, trusted, every
        self.mem_min, self.last_at = None, None
        self.stopping = threading.Event()

    def tick(self, now=None):
        now = time.time() if now is None else now
        mem = mem_available()
        if mem is not None:
            self.mem_min = mem if self.mem_min is None else min(self.mem_min, mem)
        if self.last_at is None:
            rows = load()
            self.last_at = rows[-1]["at"] if rows else 0
        if not self.trusted() or now < self.last_at + self.every:
            return False
        line = sample(self.settings(), self.mem_min, now)
        if not line:
            return False
        self.mem_min, self.last_at = None, line["at"]
        if not jsonlog.append(FILE, [line]) or jsonlog.count(FILE) > 2 * LIMIT:
            jsonlog.rewrite(FILE, [*[r for r in load() if now - KEEP <= r["at"]], line][-LIMIT:])
        return True

    def run(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - a bad tick must not end the sampling
                print(f"cardhistory: {exc}")
            self.stopping.wait(SAMPLE)
