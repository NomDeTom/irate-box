#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One record of what the box's watchers saw and did, in time order: the uplink watchdog (its
outages, their causes, each step taken, held or skipped, a stall), crash watch (a radio failing and
coming back, its driver resetting it), and the box itself (why each boot happened). Each keeps its
own files for its own pages too; this is where they meet, for the box doctor and crash folders.

One JSON object a line in $HUB_STATE_DIR/control/watchers.jsonl: {at, boot, by, kind, iface, text}.
Written by root's watchers only, each line under a lock; once it passes MAX_BYTES, trimmed to the last
KEEP_DAYS and to half of MAX_BYTES, so the next rewrite comes only after as much again is added. Stdlib only.
"""

import fcntl
import json
import os
import time
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
LOG = STATE / "control" / "watchers.jsonl"
PROC = Path(os.environ.get("HUB_PROC", "/proc"))
KEEP_DAYS = 14
MAX_BYTES = 2_000_000
# Who wrote it, in words for the pages.
BY = {"watchdog": "the uplink watchdog", "crashwatch": "crash watch", "driver": "the radio's driver", "box": "the box"}
# What is worth an owner's look, in the doctor's summary (the rest stays in the file).
NOTABLE = {
    "down",
    "up",
    "cause",
    "wedged",
    "repair",
    "held",
    "skip",
    "stalled",
    "failed",
    "recovered",
    "driver-reset",
    "boot",
}


def boot_id():
    try:
        return (PROC / "sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return ""


def add(by, kind, text, iface=None, at=None, **extra):
    """One line, appended under a lock. Never raises: a watcher goes on whatever happens here."""
    e = {
        "at": time.time() if at is None else at,
        "boot": boot_id(),
        "by": by,
        "kind": kind,
        "iface": iface,
        "text": str(text)[:400],
    }
    e.update({k: v for k, v in extra.items() if v is not None})
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(e) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            if fh.tell() > MAX_BYTES:
                _trim()
    except OSError:
        pass
    return e


def _trim():
    """Keep the last KEEP_DAYS, at most half of MAX_BYTES (called with the lock held). Trimmed to the days
    alone, a box with 14 busy days stayed past MAX_BYTES and rewrote the whole file for every line."""
    since = time.time() - KEEP_DAYS * 86400
    kept = [line for line in LOG.read_text(encoding="utf-8").splitlines() if _at(line) >= since][-20000:]
    start = max(len(kept) - 1, 0)  # the newest line always
    size = len(kept[start]) + 1 if kept else 0
    while start and size + len(kept[start - 1]) + 1 <= MAX_BYTES // 2:
        start -= 1
        size += len(kept[start]) + 1
    tmp = LOG.with_name(LOG.name + ".tmp")
    tmp.write_text("".join(f"{line}\n" for line in kept[start:]), encoding="utf-8")
    os.replace(tmp, LOG)


def _at(line):
    try:
        return float(json.loads(line).get("at", 0))
    except (ValueError, AttributeError, TypeError):
        return 0.0


def read(since=0.0, until=None, boot=None, kinds=None):
    """The lines from `since` (to `until`), of one boot or all, of some kinds or all, oldest first."""
    try:
        lines = LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict) or e.get("at", 0) < since or (until is not None and e.get("at", 0) > until):
            continue
        if (boot and e.get("boot") != boot) or (kinds and e.get("kind") not in kinds):
            continue
        out.append(e)
    return out


def line(e):
    """One event as a line of text: time, who, what."""
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e.get("at", 0)))
    on = f" {e['iface']}" if e.get("iface") else ""
    return f"{when} {BY.get(e.get('by'), e.get('by'))}{on} {e.get('kind')}: {e.get('text')}"
