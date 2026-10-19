#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Crash watch: what the box was doing when it stopped, kept where a reboot doesn't lose it.

irate-box-crashwatch.service runs this as root (`crashwatch.py run`), always: small, one process.

  Snapshots   every 30 s, load, memory, the heaviest processes, temperature, the network links and
              the kernel's new lines, appended to ROOT_STATE/crashwatch/snap.log on the box's own storage
              and synced (on Armbian images /var/log is a RAM disk, so the journal alone loses a
              hang). Rotated at 4 MB. A driver flooding the kernel log (the AIC8800's "4addr" hex
              dumps, ~10 a second) is counted, not copied. On by default; off on the box doctor.
  A crash     each start of the service notes the boot; a stop (a shutdown) marks it clean. A new
              boot with no clean mark is a box that stopped without shutting down (a hang, a crash
              or the power going): its last snapshots and the previous boot's journal tail are filed
              under ROOT_STATE/crashwatch/crashes/<time>/ for the box doctor (the last 30 kept).
  A radio     the WiFi radio watched for a hardware or driver failure: its interface or USB device
              gone, or a burst of driver errors (the AIC8800's own "cmd queue crashed" among them),
              and its driver resetting the radio itself. Each failure is filed as it happens
              (crashes/<time>-radio-<iface>/: the ten minutes before it and the kernel's lines), as
              the box may never stop, and noted in events.json. Crash watch never acts on the radio:
              the uplink watchdog (uplink.py) reads these as evidence and does the repairing.
  The record  what it notes, and why each boot happened (the uplink watchdog's reboot, a clean stop,
              or stopping without shutting down), goes into the watchers' one record too
              (hub/watchlog.py), beside the uplink watchdog's; a filed failure or crash carries its
              lines (watchers.log).
  A hang      the kernel itself freezing, which nothing in user space can act on. Two choices, off by
              default, each the owner's (box doctor): reboot on a kernel panic, oops or lockup
              (kernel.panic and its kin); and a watchdog (the board's own, or the kernel's softdog
              where it has none) that systemd keeps fed, so a frozen system restarts by itself.

    crashwatch.py run               the watcher
    crashwatch.py boot | shutdown   the service's start and stop marks (ExecStartPre, ExecStopPost)
    crashwatch.py status            what it knows, as JSON
    crashwatch.py set KEY VALUE     snapshots on|off, panic on|off, watchdog on|off
    crashwatch.py undo-all          for uninstall.sh: the hang settings taken away

Settings in /etc/hub/crashwatch.json (root's). Stdlib only.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, deque
from pathlib import Path

from irate_box.msg import M, Refused, duration, render
from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
SETTINGS = ETC / "crashwatch.json"
# Root's own folder beside $STATE: the hub owns $STATE, so it could swap a folder of root's there
# for a link, and root's writes would land where it pointed.
DIR = safeio.ROOT_STATE / "crashwatch"
OLD_DIR = STATE / "crashwatch"  # before 2026-10; migrate() moves its files over
SNAP = DIR / "snap.log"
RUN = DIR / "run.json"  # this boot: its id, when it started, whether it stopped cleanly
CRASHES = DIR / "crashes"
INDEX = DIR / "crashes.json"
INCIDENTS = DIR / "incidents.json"  # a radio's failures, filed as they happen (the box may never stop)
EVENTS = DIR / "events.json"  # the radio's failures, its driver's own resets, its recoveries, newest last
STATUS = DIR / "status.json"  # for the box doctor: the radios watched, the floods, the last look
PROC = Path(os.environ.get("HUB_PROC", "/proc"))
SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
KMSG = os.environ.get("HUB_KMSG", "/dev/kmsg")
SYSCTL = Path(os.environ.get("HUB_SYSCTL_DIR", "/etc/sysctl.d")) / "90-irate-box-hang.conf"
MODULES = Path(os.environ.get("HUB_MODULES_LOAD", "/etc/modules-load.d")) / "irate-box-softdog.conf"
SYSTEMD_CONF = Path(os.environ.get("HUB_SYSTEMD_CONF_DIR", "/etc/systemd/system.conf.d")) / "90-irate-box-watchdog.conf"
DT = Path(os.environ.get("HUB_DEVICE_TREE", "/proc/device-tree"))
BOOT = Path(os.environ.get("HUB_BOOT_DIR", "/boot"))
OVERLAY_NAME = "irate-box-watchdog"

TICK = 30
SNAP_MAX = 4_000_000
KEEP_CRASHES = 30
WATCHDOG_SEC = 60  # RuntimeWatchdogSec: how long a frozen system can stop feeding the watchdog
DEFAULT = {"snapshots": True, "panic": False, "watchdog": False}
FLOOD = 30  # kernel lines of one kind in one look (30 s, so one a second) that make a flood
ERR_BURST = 5  # driver errors within two looks that make a failure
# A radio in trouble, as the kernel says it (the AIC8800's own words, and the USB core's).
RADIO_ERR = re.compile(
    r"USB disconnect|tx timeout|cmd (?:tx )?time ?out|firmware (?:crash|error|fail)|fw (?:crash|error)"
    r"|failed to (?:send|transmit|xmit)|error -(?:71|110|19)\b|descriptor read.*error|reset (?:high|full)-speed USB",
    re.I,
)
# The AIC8800 driver's own words when its firmware wedges, which name no interface or driver (a
# burst of "check cmdqueue empty", "cmd_mgr_queue cmd timed-out", then "cmd queue crashed"; none of
# them seen in normal running). Counted for a radio with an aic driver.
AIC_ERR = re.compile(r"cmd queue crashed|cmd_mgr_queue cmd timed-out|check cmdqueue empty", re.I)
# The AIC8800 driver, patched to mend itself (aic8800_fdrv recover_on_timeout): on a command its firmware never
# answers it resets the USB device, which loads the firmware afresh. Its interfaces go and come back by themselves
# within seconds. Within DRIVER_GRACE of that line, a radio gone or the USB core's reset lines are that reset at
# work, not a failure of its own. The driver counts its resets in sysfs too
# (radio.driver_reset), as a flood in the kernel's small log can overwrite the line before a look reads it.
DRIVER_RESET = re.compile(r"aic8800: firmware not answering: resetting the device")
DRIVER_GRACE = 90
HANG_SYSCTL = {"kernel.panic": "10", "kernel.panic_on_oops": "1", "kernel.softlockup_panic": "1"}


# --- settings ----------------------------------------------------------------------------------


def load_settings():
    try:
        raw = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        raw = {}
    out = dict(DEFAULT)
    for k in ("snapshots", "panic", "watchdog"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    return out


def carry_over():
    """Crash watch once reset a failing radio and rebooted the box itself ("preempt" radio or reboot);
    that is the uplink watchdog's now, as its choice for a failed radio (on_wedge). An owner's choice
    is carried over, never lowered, and the old setting dropped. Returns what was done, or None."""
    try:
        raw = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return None
    old = raw.get("preempt") if isinstance(raw, dict) else None
    if old is None:
        return None
    said = None
    if old in ("radio", "reboot"):
        from irate_box.hub import uplink

        chosen = uplink.load_settings()
        order = uplink.ON_WEDGE
        if order.index(old) > order.index(chosen.get("on_wedge", "ladder")):
            chosen["on_wedge"] = old
            uplink.save_settings(chosen)
            said = f"the uplink watchdog now does what crash watch did for a failed radio (on_wedge {old})"
    save_settings(load_settings())
    return said or "crash watch's old radio setting dropped"


def save_settings(s):
    from irate_box.root import safeio

    safeio.write(SETTINGS, json.dumps(s, indent=1))


def _read(p, default=""):
    try:
        return Path(p).read_text()
    except OSError:
        return default


def _json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def _write(p, data):
    from irate_box.root import safeio

    p.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(p, json.dumps(data))


def boot_id():
    return _read(PROC / "sys/kernel/random/boot_id").strip()


def uptime():
    try:
        return float(_read(PROC / "uptime").split()[0])
    except (IndexError, ValueError):
        return 1e9


# --- the kernel's lines --------------------------------------------------------------------------


class Kmsg:
    """New kernel lines since the last look, from /dev/kmsg (non-blocking; starts at the end)."""

    def __init__(self, path=KMSG):
        self.fd = None
        try:
            self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            os.lseek(self.fd, 0, os.SEEK_END)
        except OSError:
            self.fd = None

    def read(self):
        out = []
        while self.fd is not None:
            try:
                rec = os.read(self.fd, 8192).decode(errors="replace")
            except BlockingIOError:
                break
            except OSError as exc:  # EPIPE: lines overwritten before we read them
                if getattr(exc, "errno", 0) == 32:
                    continue
                break
            if not rec:
                break
            head, _, msg = rec.partition(";")
            parts = head.split(",")
            secs = int(parts[2]) / 1e6 if len(parts) > 2 and parts[2].isdigit() else 0.0
            out.append(f"[{secs:.1f}] {msg.splitlines()[0] if msg else ''}")
        return out


SHAPE = re.compile(r"\b(?:[0-9a-f]{2}\s+){4,}[0-9a-f]{2}\b|0x[0-9a-f]+|\b\d+\b", re.I)


def shape(line):
    """A kernel line with its numbers and hex runs taken out: lines of one kind share a shape."""
    return SHAPE.sub("#", line.split("] ", 1)[-1])[:80]


def kind(line):
    """What a kernel line is, for counting: the first two words of its shape ("4addr #" for every line
    of the AIC8800's hex dumps, "usb #-#.#:" for its USB core's)."""
    return " ".join(shape(line).split()[:2])


def floods(lines, limit=FLOOD):
    """(the lines worth keeping, {kind: count} of the kinds that flooded). Once a kind floods, the
    lines naming its word go with it ("usb 1-1.1: 4addr Frame received" with the "4addr" dump)."""
    counts = Counter(kind(ln) for ln in lines)
    flood = {k: n for k, n in counts.items() if n >= limit}
    words = {k: next((w for w in k.split() if "#" not in w), None) for k in flood}
    keep = []
    for ln in lines:
        k = kind(ln)
        if k not in flood:
            hit = next((f for f, w in words.items() if w and f" {w} " in f" {ln.split('] ', 1)[-1]} "), None)
            if not hit:
                keep.append(ln)
                continue
            k = hit
        flood[k] = flood.get(k, 0) + (0 if counts[kind(ln)] >= limit else 1)
    return keep, flood


# --- a snapshot --------------------------------------------------------------------------------


def meminfo():
    out = {}
    for line in _read(PROC / "meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.strip().split() and v.strip().split()[0].isdigit():
            out[k] = int(v.split()[0]) // 1024
    return out


def temperature():
    temps = []
    for z in sorted((SYS / "class/thermal").glob("thermal_zone*")):
        t = _read(z / "temp").strip()
        if t.lstrip("-").isdigit():
            temps.append(int(t) // 1000)
    return max(temps) if temps else None


def heaviest(n=5):
    rows, page_kb = [], os.sysconf("SC_PAGE_SIZE") // 1024
    for d in PROC.glob("[0-9]*"):
        try:
            rss = int(_read(d / "statm").split()[1]) * page_kb
            rows.append((rss, _read(d / "comm").strip()))
        except (IndexError, ValueError):
            continue
    return sorted(rows, reverse=True)[:n]


def links():
    out = []
    for i in sorted((SYS / "class/net").glob("*")):
        if i.name != "lo":
            out.append(f"{i.name} {_read(i / 'operstate').strip() or '?'}")
    return out


def snapshot(kernel, flood, extra=""):
    m = meminfo()
    load = _read(PROC / "loadavg").split()
    lines = [
        f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} up {uptime():.0f}s load {' '.join(load[:3])} boot {boot_id()[:8]}",
        "  mem: "
        + "  ".join(
            f"{k} {m[k]} MB" for k in ("MemAvailable", "MemFree", "SwapFree", "Slab", "Committed_AS") if k in m
        ),
        f"  temp {temperature()}C  procs {len(list(PROC.glob('[0-9]*')))}",
        *[f"  {kb:>7} KB {name}" for kb, name in heaviest()],
        "  links: " + ", ".join(links()),
        *[f"  wifi {ln.strip()}" for ln in _read(PROC / "net/wireless").splitlines()[2:]],
        *[f'  kernel: {n} lines like "{k}"' for k, n in flood.items()],
        *[f"  k {ln}" for ln in kernel[-40:]],
    ]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def append_snapshot(text, sync=True):
    safeio.root_dir("crashwatch")
    if SNAP.exists() and SNAP.stat().st_size > SNAP_MAX:
        os.replace(SNAP, SNAP.with_name("snap.log.1"))
    fd = os.open(SNAP, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, text.encode())
        if sync:
            os.fdatasync(fd)
    finally:
        os.close(fd)


# The owner's write-intensity ladder (the hub's setting, Recovery and monitoring): how snapshots reach the card.
# everything, thrifty: each one written and synced; gentle: written, left to the filesystem's own commit;
# saver: held in memory, written every SAVER_EVERY and at once when something goes wrong.
CARD_STOPS = ("everything", "thrifty", "gentle", "saver")
SAVER_EVERY = 900


def card_writes():
    """The ladder's stop, from the hub's settings (the hub's file, read as data, never through a link)."""
    try:
        from irate_box.root import safeio

        value = json.loads(safeio.read_request(STATE / "settings.json")).get("card_writes")
    except (OSError, ValueError, AttributeError, ImportError):
        return "thrifty"
    return value if value in CARD_STOPS else "thrifty"


class SnapshotKeeper:
    """Snapshots to the card as the ladder says. trouble: something went wrong (a radio, a flood): written now."""

    def __init__(self, write=append_snapshot, clock=time.time):
        self.write, self.clock, self.held, self.since = write, clock, [], None

    def add(self, text, stop, trouble=False):
        if stop != "saver":
            self.flush()
            self.write(text, sync=stop != "gentle")
            return
        self.held.append(text)
        self.since = self.since or self.clock()
        if trouble or self.clock() - self.since >= SAVER_EVERY:
            self.flush()

    def flush(self):
        if self.held:
            self.write("".join(self.held), sync=True)
            self.held, self.since = [], None


# --- a crash, at boot ----------------------------------------------------------------------------


def journal_tail(n=300):
    try:
        r = subprocess.run(  # noqa: TID251
            ["journalctl", "-b", "-1", "--no-pager", "-o", "short-monotonic", "-n", str(n * 4)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    keep, flood = floods(r.stdout.splitlines(), limit=40)
    return "\n".join([f'({n} lines like "{k}" left out)' for k, n in flood.items()] + keep[-n:])


def snapshots_tail(nbytes=200_000, since=None):
    """The last snapshots: nbytes of them, or, if it reaches further back, from the snapshot taken
    at or just before `since` (epoch; a boot with a radio failure: a few minutes before it), at
    most 2 MB."""
    text = _read(SNAP.with_name("snap.log.1")) + _read(SNAP)
    if since:
        stamp = time.strftime("=== %Y-%m-%d %H:%M:%S", time.localtime(since))
        heads = [m for m in re.finditer(r"^=== \d{4}-\d\d-\d\d \d\d:\d\d:\d\d", text, re.M)]
        before = [m.start() for m in heads if m.group(0) <= stamp]
        i = before[-1] if before else (heads[0].start() if heads else len(text))
        nbytes = min(max(nbytes, len(text) - i), 2_000_000)
    return text[-nbytes:] if nbytes else ""


RADIO_JOIN = 120  # seconds within which a second interface of the same radio failing is the same incident


def file_radio(now, iface, text, kernel, radio=None):
    """A radio's failure, filed when it happens with what led to it (the last ten minutes'
    snapshots and this look's kernel lines), for the box doctor: a radio can wedge while the box
    runs on, so no crash is ever filed for it. `radio` is its USB port (radios()): another interface
    of the same radio failing soon after (a hotspot beside the uplink) joins that incident."""
    index = _json(INCIDENTS, [])
    safeio.root_dir("crashwatch")
    last = index[-1] if index else None
    if (
        radio
        and last
        and last.get("radio") == radio
        and last.get("boot") == boot_id()
        and now - last.get("at", 0) < RADIO_JOIN
        and iface not in last.get("ifaces", [last.get("iface")])
    ):
        last["ifaces"] = last.get("ifaces", [last.get("iface")]) + [iface]
        last["text"] = f"{last['text']}; {iface}: {text}"
        d = CRASHES / last["dir"]
        if d.is_dir():
            (d / "info.json").write_text(json.dumps(last, indent=1))
            with open(d / "kernel.log", "a") as fh:
                fh.write("\n".join(kernel[-300:]) + "\n")
        _write(INCIDENTS, index)
        return last
    stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(now))
    d = CRASHES / f"{stamp}-radio-{re.sub(r'[^A-Za-z0-9_.-]', '_', iface)}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "snapshots.log").write_text(snapshots_tail(0, since=now - 600))
    (d / "kernel.log").write_text("\n".join(kernel[-300:]) + "\n")
    _file_watchers(d, since=now - 600)
    rec = {
        "dir": d.name,
        "kind": "radio",
        "iface": iface,
        "ifaces": [iface],
        "radio": radio,
        "at": now,
        "boot": boot_id(),
        "text": text,
    }
    (d / "info.json").write_text(json.dumps(rec, indent=1))
    index = index + [rec]
    for old in index[:-KEEP_CRASHES]:
        shutil.rmtree(CRASHES / old.get("dir", "-"), ignore_errors=True)
    _write(INCIDENTS, index[-KEEP_CRASHES:])
    return rec


def _file_watchers(d, since=0.0, boot=None):
    """What the watchers saw and did (watchlog), beside a filed failure or crash."""
    from irate_box.hub import watchlog

    lines = [watchlog.line(e) for e in watchlog.read(since=since, boot=boot)]
    if lines:
        (d / "watchers.log").write_text("\n".join(lines[-500:]) + "\n")


def boot_reason(prev, now=None):
    """Why this boot happened, in words, from the last one's record (RUN) and the watchers' record:
    it stopped without shutting down (a hang, a crash or the power going: which, the box can't tell
    afterwards; the hardware watchdog restarts a hang by itself when it is on); or it shut down
    cleanly, after the uplink watchdog's reboot, or otherwise (by hand, an update, the system)."""
    from irate_box.hub import watchlog

    if not prev or not prev.get("boot"):
        return "the first boot crash watch has seen"
    if not prev.get("clean"):
        s = load_settings()
        return (
            "unclean shutdown: the box stopped without shutting down. The cause can be a hang, a crash or a power loss, and the box cannot tell which"
            + (f". The watchdog restarts a frozen box after {WATCHDOG_SEC} s" if s.get("watchdog") else "")
        )
    stopped = prev.get("stopped") or 0
    asked = [
        e
        for e in watchlog.read(boot=prev["boot"], kinds={"repair"})
        if e.get("step") == "reboot" and (not stopped or 0 <= stopped - e.get("at", 0) <= 600)
    ]
    if asked:
        return f"the uplink watchdog rebooted it: {asked[-1]['text']}"
    return "it was shut down or restarted cleanly (by hand, or by the system)"


def last_snapshot(text):
    i = text.rfind("=== ")
    return text[i:] if i >= 0 else ""


def boot(now=None):
    """At the service's start: a previous boot that never stopped cleanly is filed as a crash."""
    now = now or time.time()
    safeio.root_dir("crashwatch")
    cur, prev = boot_id(), _json(RUN, None)
    filed = None
    if prev and prev.get("boot") and prev["boot"] != cur and not prev.get("clean"):
        stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(now))
        d = CRASHES / stamp
        d.mkdir(parents=True, exist_ok=True)
        events = [e for e in _json(EVENTS, []) if e.get("boot") == prev["boot"]]
        # Back to a few minutes before that boot's first radio failure, if it had one.
        first = min((e["at"] for e in events if e.get("kind") == "failed"), default=None)
        snaps = snapshots_tail(since=first - 300 if first else None)
        (d / "snapshots.log").write_text(snaps)
        (d / "journal-previous-boot.log").write_text(journal_tail())
        _file_watchers(d, boot=prev["boot"])
        filed = {
            "dir": stamp,
            "boot": prev["boot"],
            "started": prev.get("at"),
            "detected": now,
            "last": last_snapshot(snaps)[:1500],
            "events": events[-5:],
        }
        (d / "info.json").write_text(json.dumps(filed, indent=1))
        index = _json(INDEX, []) + [filed]
        for old in index[:-KEEP_CRASHES]:
            shutil.rmtree(CRASHES / old.get("dir", "-"), ignore_errors=True)
        _write(INDEX, index[-KEEP_CRASHES:])
    if not prev or prev.get("boot") != cur:
        from irate_box.hub import watchlog

        watchlog.add("box", "boot", f"Started: {boot_reason(prev, now)}.", at=now, filed=filed and filed["dir"])
        _write(RUN, {"boot": cur, "at": now, "clean": False})
    else:
        _write(RUN, dict(prev, clean=False))
    return filed


def shutdown():
    rec = _json(RUN, {})
    if rec.get("boot") == boot_id():
        _write(RUN, dict(rec, clean=True, stopped=time.time()))


# --- the radio ---------------------------------------------------------------------------------


def radios():
    """{iface: {driver, port, product}} for the WiFi interfaces on the box now."""
    from irate_box.hub import netinv

    out = {}
    for i in sorted((SYS / "class/net").glob("*")):
        if (i / "wireless").exists() or (i / "phy80211").exists():
            f = netinv.device_facts(i.name)
            usb = f.get("usb") or {}
            out[i.name] = {"driver": f.get("driver"), "port": usb.get("port"), "product": usb.get("product")}
    return out


def radio_state(known, kernel):
    """Each known radio's state from this look: {iface: (failed?, why)}. A failure is the radio
    itself: its interface or USB device gone, or its driver's errors; not a link going down."""
    out = {}
    for iface, r in known.items():
        why = []
        if not (SYS / "class/net" / iface).exists():
            why.append(f"{iface} is gone")
        if r.get("port") and not (SYS / "bus/usb/devices" / r["port"]).exists():
            why.append(f"its USB device {r['port']} ({r.get('product') or 'the radio'}) left the bus")
        names = {n.lower() for n in (iface, r.get("driver"), r.get("port") and f"{r['port']}:") if n}
        aic = (r.get("driver") or "").startswith("aic")
        if aic:
            names |= {"aicwf", "aicbsp", "rwnx"}  # the AIC8800 driver's own prefixes ("AICWFDBG(LOGERROR)" too)
        errs = [
            ln
            for ln in kernel
            if (RADIO_ERR.search(ln) and any(n in ln.lower() for n in names)) or (aic and AIC_ERR.search(ln))
        ]
        out[iface] = (why, errs)
    return out


class RadioWatch:
    """A failing radio noticed and noted, never acted on (the uplink watchdog repairs). act(now,
    states) returns the events of this look."""

    def __init__(self, log):
        self.log = log
        self.driver_reset = None  # when the driver last reset the radio itself
        self.own_seen = None  # the driver's own count of its resets, as last read (radio.driver_reset)
        self.recent = {}  # iface: deque of (time, error count)
        self.failed = {}  # iface: since

    def act(self, now, states, kernel=(), own=(None, None)):
        """own: (marker, age) of the driver's last reset of its own device (radio.driver_reset), which
        a busy kernel log may have overwritten before this look read it."""
        events = []
        said = next((ln for ln in kernel if DRIVER_RESET.search(ln)), None)
        marker, age = own
        fresh = marker is not None and marker != self.own_seen and age is not None and age < DRIVER_GRACE
        if marker is not None:
            self.own_seen = marker
        if said or fresh:
            self.driver_reset = now - age if fresh and not said else now
            events.append(
                self.log(
                    now, "radio", "driver-reset", "the radio's driver reset it itself (its firmware stopped answering)"
                )
            )
        if self.driver_reset is not None and now - self.driver_reset < DRIVER_GRACE:
            return events  # its own reset at work: the interfaces come back by themselves, or don't
        for iface, (gone, errs) in states.items():
            q = self.recent.setdefault(iface, deque(maxlen=2))
            q.append((now, len(errs)))
            burst = sum(n for _, n in q) >= ERR_BURST
            if not (gone or burst):
                if iface in self.failed:
                    events.append(self.log(now, iface, "recovered", "the radio is back"))
                    self.failed.pop(iface)
                continue
            if iface not in self.failed:
                self.failed[iface] = now
                why = (
                    "; ".join(gone)
                    or f"{sum(n for _, n in q)} driver errors in a minute: {errs[-1][:160] if errs else ''}"
                )
                events.append(self.log(now, iface, "failed", why, snapshot=True))
        return events


# --- the hang settings ---------------------------------------------------------------------------


def watchdog_device():
    """'hardware' when the board has a watchdog of its own, 'softdog' when the kernel can give one,
    'switched-off' when the board has one its device tree leaves disabled and Armbian's user overlays can
    switch on, else None."""
    if any((SYS / "class/watchdog").glob("watchdog*")) and not _softdog_loaded():
        return "hardware"
    r = subprocess.run(["modinfo", "softdog"], capture_output=True, text=True)  # noqa: TID251
    if r.returncode == 0 or _softdog_loaded():
        return "softdog"
    if _dt_watchdog() and (BOOT / "armbianEnv.txt").is_file() and shutil.which("dtc"):
        return "switched-off"
    return None


def _dt_watchdog():
    """The device tree's first watchdog node left disabled, as its path from the root ("/watchdog@ff260000"), or None."""
    for node in sorted(DT.glob("watchdog@*")) if DT.is_dir() else []:
        try:
            if (node / "status").read_bytes().rstrip(b"\0") == b"disabled":
                return "/" + node.name
        except OSError:
            continue
    return None


def _boot_env_overlays(add=None, remove=None):
    """Armbian's armbianEnv.txt: the user_overlays line with `add` put in or `remove` taken out. Returns the list."""
    env = BOOT / "armbianEnv.txt"
    lines = env.read_text().splitlines()
    have, at = [], None
    for i, line in enumerate(lines):
        if line.startswith("user_overlays="):
            have, at = line.split("=", 1)[1].split(), i
    names = [n for n in have if n != remove] + ([add] if add and add not in have else [])
    if names != have:
        from irate_box.root import safeio

        new = "user_overlays=" + " ".join(names)
        if at is None:
            lines.append(new)
        elif names:
            lines[at] = new
        else:
            del lines[at]
        safeio.write(env, "\n".join(lines) + "\n")
    return names


def _board_watchdog(on):
    """The board's own watchdog switched on or off in the device tree, from the next boot: an Armbian user overlay."""
    dtbo = BOOT / "overlay-user" / f"{OVERLAY_NAME}.dtbo"
    if not on:
        dtbo.unlink(missing_ok=True)
        if (BOOT / "armbianEnv.txt").is_file():
            _boot_env_overlays(remove=OVERLAY_NAME)
        return
    node = _dt_watchdog()
    if not node:
        raise Refused(M("crashwatch-set.no-dt-watchdog", "The device tree has no disabled watchdog to enable."))
    src = (
        '/dts-v1/;\n/plugin/;\n/ {\n\tfragment@0 {\n\t\ttarget-path = "%s";\n'
        '\t\t__overlay__ { status = "okay"; };\n\t};\n};\n' % node
    )
    dtbo.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(  # noqa: TID251
        ["dtc", "-@", "-q", "-I", "dts", "-O", "dtb", "-o", str(dtbo), "-"], input=src, capture_output=True, text=True
    )
    if r.returncode:
        dtbo.unlink(missing_ok=True)
        raise Refused(
            M(
                "crashwatch-set.dtc-failed",
                "The device tree compiler (dtc) failed: {detail}",
                detail=r.stderr.strip()[:160],
            )
        )
    _boot_env_overlays(add=OVERLAY_NAME)


def board_watchdog_pending():
    """True when the overlay is in place but the watchdog hasn't appeared yet: it waits for a restart."""
    return (BOOT / "overlay-user" / f"{OVERLAY_NAME}.dtbo").exists() and not any(
        (SYS / "class/watchdog").glob("watchdog*")
    )


def _softdog_loaded():
    return (SYS / "module/softdog").exists()


def set_panic(on):
    if on:
        from irate_box.root import safeio

        SYSCTL.parent.mkdir(parents=True, exist_ok=True)
        safeio.write(
            SYSCTL,
            "# Written by irate-box (root/crashwatch.py): restart on a kernel panic, oops or lockup.\n"
            + "".join(f"{k} = {v}\n" for k, v in HANG_SYSCTL.items()),
        )
        for k, v in HANG_SYSCTL.items():
            subprocess.run(["sysctl", "-q", "-w", f"{k}={v}"], capture_output=True)  # noqa: TID251
        return M(
            "crashwatch-set.panic.on",
            "Restart after kernel fault enabled: the box restarts {wait} after a kernel panic, oops or lockup.",
            wait=duration(int(HANG_SYSCTL["kernel.panic"])),
        )
    SYSCTL.unlink(missing_ok=True)
    for k in HANG_SYSCTL:
        subprocess.run(["sysctl", "-q", "-w", f"{k}=0"], capture_output=True)  # noqa: TID251
    return M(
        "crashwatch-set.panic.off",
        "Restart after kernel fault disabled: a kernel panic or lockup no longer restarts the box.",
    )


def set_watchdog(on):
    from irate_box.root import safeio

    if on:
        kind = watchdog_device()
        if not kind:
            raise Refused(
                M("crashwatch-set.no-watchdog", "This kernel has no watchdog: neither the board's own nor softdog.")
            )
        if kind == "switched-off":
            _board_watchdog(True)
        if kind == "softdog":
            MODULES.parent.mkdir(parents=True, exist_ok=True)
            safeio.write(
                MODULES, "# Written by irate-box (root/crashwatch.py): a watchdog where the board has none.\nsoftdog\n"
            )
            p = subprocess.run(["modprobe", "softdog"], capture_output=True, text=True)  # noqa: TID251
            if p.returncode:
                MODULES.unlink(missing_ok=True)
                raise Refused(
                    M(
                        "crashwatch-set.softdog-failed",
                        "The kernel did not load softdog: {detail}",
                        detail=p.stderr.strip()[:160],
                    )
                )
        SYSTEMD_CONF.parent.mkdir(parents=True, exist_ok=True)
        safeio.write(
            SYSTEMD_CONF,
            "# Written by irate-box (root/crashwatch.py): systemd feeds the watchdog; a frozen\n"
            f"# system stops feeding it and the box restarts.\n[Manager]\nRuntimeWatchdogSec={WATCHDOG_SEC}\nRebootWatchdogSec=5min\n",
        )
        subprocess.run(["systemctl", "daemon-reexec"], capture_output=True, timeout=120)  # noqa: TID251
        wait = duration(WATCHDOG_SEC)
        if kind == "switched-off":
            return M(
                "crashwatch-set.watchdog.after-restart",
                "Watchdog enabled after restart: the board's own watchdog starts at the next "
                "restart. Then it restarts the box if the system freezes for {wait}.",
                wait=wait,
            )
        if kind == "hardware":
            return M(
                "crashwatch-set.watchdog.board",
                "Watchdog enabled: the board's own watchdog restarts the box if the system freezes for {wait}.",
                wait=wait,
            )
        return M(
            "crashwatch-set.watchdog.softdog",
            "Watchdog enabled: the kernel's softdog restarts the box if the system freezes for {wait}.",
            wait=wait,
        )
    SYSTEMD_CONF.unlink(missing_ok=True)
    _board_watchdog(False)
    subprocess.run(["systemctl", "daemon-reexec"], capture_output=True, timeout=120)  # noqa: TID251
    if MODULES.exists():
        MODULES.unlink()
        subprocess.run(["modprobe", "-r", "softdog"], capture_output=True)  # noqa: TID251
    return M("crashwatch-set.watchdog.off", "Watchdog disabled: a frozen box waits for an administrator to restart it.")


def set_option(key, value):
    """One setting, as the box doctor's switches give it. Returns what is now so."""
    s = load_settings()
    if key == "snapshots" and value in ("on", "off"):
        s["snapshots"] = value == "on"
        msg = (
            M(
                "crashwatch-set.snapshots.on",
                "Crash snapshots enabled: the box saves a snapshot to its storage every {every}.",
                every=duration(TICK),
            )
            if s["snapshots"]
            else M(
                "crashwatch-set.snapshots.off",
                "Crash snapshots disabled: after a crash, only what the journal last wrote is kept.",
            )
        )
    elif key == "panic" and value in ("on", "off"):
        msg = set_panic(value == "on")
        s["panic"] = value == "on"
    elif key == "watchdog" and value in ("on", "off"):
        msg = set_watchdog(value == "on")
        s["watchdog"] = value == "on"
    else:
        raise Refused(M("crashwatch-set.unknown", "Crash watch has no setting {key} {value}.", key=key, value=value))
    save_settings(s)
    return msg


def undo_all():
    done = []
    s = load_settings()
    if s["panic"] or SYSCTL.exists():
        done.append(render(set_panic(False)))  # for uninstall.sh's terminal
    if s["watchdog"] or SYSTEMD_CONF.exists() or (BOOT / "overlay-user" / f"{OVERLAY_NAME}.dtbo").exists():
        done.append(render(set_watchdog(False)))
    return done


# --- running ---------------------------------------------------------------------------------


def status():
    s = load_settings()
    st = _json(STATUS, {})
    return {
        "settings": s,
        "running": st,
        "crashes": _json(INDEX, []),
        "incidents": _json(INCIDENTS, []),
        "events": _json(EVENTS, [])[-20:],
        "watchdog_device": None if os.geteuid() and not os.environ.get("HUB_SYSFS") else watchdog_device(),
        "watchdog_pending": board_watchdog_pending(),
    }


def run(once=False):
    kmsg = Kmsg()
    known = radios()
    events = deque(_json(EVENTS, [])[-50:], maxlen=50)
    floods_hour = Counter()
    hour_start = time.time()
    status_at = 0.0
    pending = {"snap": None, "lines": []}

    def log(now, iface, kind, text, snapshot=False):
        e = {"at": now, "boot": boot_id(), "iface": iface, "kind": kind, "text": text}
        if kind in (
            "failed",
            "driver-reset",
        ):  # filed with the kernel's lines: what failed, or what the driver timed out on
            try:
                e["filed"] = file_radio(now, iface, text, pending["lines"], (known.get(iface) or {}).get("port"))["dir"]
            except OSError as exc:
                print(f"crashwatch: radio failure not filed: {exc}", file=sys.stderr)
        events.append(e)
        _write(EVENTS, list(events))
        from irate_box.hub import watchlog

        watchlog.add(
            "driver" if kind == "driver-reset" else "crashwatch",
            kind,
            text,
            None if iface == "radio" else iface,
            at=now,
            filed=e.get("filed"),
        )
        if snapshot:
            pending["snap"] = f"  !! {iface}: {text}"
        return e

    settings = load_settings()
    from irate_box.root import radio

    try:
        said = carry_over()
        if said:
            print(f"crashwatch: {said}", file=sys.stderr)
    except (OSError, ValueError) as exc:
        print(f"crashwatch: the old radio setting not carried over: {exc}", file=sys.stderr)
    watch = RadioWatch(log)
    keeper = SnapshotKeeper()
    # Stopped (a shutdown, a restart): what the saver stop holds goes to the card first.
    import signal

    def _stop(*_):
        keeper.flush()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _stop)
    mtime = 0.0
    while True:
        now = time.time()
        try:
            m = SETTINGS.stat().st_mtime
        except OSError:
            m = 0.0
        if m != mtime:
            mtime, settings = m, load_settings()
        lines = kmsg.read()
        pending["lines"] = lines
        keep, flood = floods(lines)
        floods_hour.update(flood)
        for i, r in radios().items():
            known[i] = r  # a radio that came back, or a new one, watched from now on
        watch.act(now, radio_state(known, lines), lines, radio.driver_reset())
        extra = pending.pop("snap", None) or ""
        pending["snap"] = None
        if settings["snapshots"] or extra:
            if extra:
                extra += "\n  last kernel lines:\n" + "\n".join(f"    {ln}" for ln in lines[-60:])
            try:
                keeper.add(snapshot(keep, flood, extra), card_writes(), trouble=bool(extra or flood))
            except OSError as exc:
                print(f"crashwatch: snapshot not written: {exc}", file=sys.stderr)
        # For the doctor every 5 minutes: the radios, and the floods counted over the hour so far.
        if now - status_at >= 300 or once:
            status_at = now
            _write(
                STATUS,
                {
                    "at": now,
                    "boot": boot_id(),
                    "radios": known,
                    "floods": dict(floods_hour.most_common(5)),
                    "flood_window": max(now - hour_start, TICK),
                },
            )
        if now - hour_start >= 3600:
            floods_hour.clear()
            hour_start = now
        if once:
            return
        time.sleep(TICK)


def migrate():
    """The files of a watch from before 2026-10, in $STATE/crashwatch, moved to DIR. Only a folder
    that is root's, 0700 and not a link is taken, and each entry moves through the folders' fds, so
    a folder the hub swapped in is left alone. Run by install.sh, outside the unit's sandbox (a
    rename between two of its mounts fails)."""
    try:
        old = safeio._dir_fd(OLD_DIR)
    except FileNotFoundError:
        return "nothing to move"
    except safeio.LinkRefused:
        return f"{OLD_DIR} is a link, not crash watch's folder: left as it is"
    try:
        st = os.fstat(old)
        if st.st_uid != os.geteuid() or st.st_mode & 0o777 != 0o700:
            return f"{OLD_DIR} is not crash watch's folder: left as it is"
        safeio.root_dir("crashwatch")
        new = os.open(DIR, safeio.DIR_FLAGS)
        moved = 0
        try:
            for name in os.listdir(old):
                try:
                    os.stat(name, dir_fd=new, follow_symlinks=False)
                    continue  # the new folder's own is newer
                except FileNotFoundError:
                    pass
                os.rename(name, name, src_dir_fd=old, dst_dir_fd=new)
                moved += 1
        finally:
            os.close(new)
    finally:
        os.close(old)
    try:
        os.rmdir(OLD_DIR)
    except OSError:
        pass
    return f"moved {moved} to {DIR}"


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "run":
        run()
    elif cmd == "boot":
        f = boot()
        print(
            f"crashwatch: the previous boot stopped without shutting down; filed as {f['dir']}"
            if f
            else "crashwatch: boot noted"
        )
    elif cmd == "shutdown":
        shutdown()
    elif cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "set" and len(argv) == 3:
        print(set_option(argv[1], argv[2]))
    elif cmd == "undo-all":
        print("\n".join(undo_all()) or "nothing to undo")
    elif cmd == "migrate":
        print(f"crashwatch: {migrate()}")
    else:
        print(__doc__.split("    crashwatch.py run")[0].strip().splitlines()[0])
        print("usage: crashwatch.py run | boot | shutdown | status | set KEY VALUE | undo-all | migrate")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
