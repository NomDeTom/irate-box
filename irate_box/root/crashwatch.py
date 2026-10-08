#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Crash watch: what the box was doing when it stopped, and acting before a failing radio takes it
down (Tom, 2026-10-08, after the Lyra hung twice that day: "set a watchdog on it to spot next time it
happens and try to capture something to flash"; "Crash watch needs to go into the doctors, and I want
it to have a pre-emption mode for when e.g. the WiFi cuts out unexpectedly, indicating a hardware
failure that might need a restart").

irate-box-crashwatch.service runs this as root (`crashwatch.py run`), always: small, one process.

  Snapshots   every 30 s, load, memory, the heaviest processes, temperature, the network links and
              the kernel's new lines, appended to STATE/crashwatch/snap.log on the box's own storage
              and synced (on Armbian images /var/log is a RAM disk, so the journal alone loses a
              hang). Rotated at 4 MB. A driver flooding the kernel log (the AIC8800's "4addr" hex
              dumps, ~10 a second) is counted, not copied. On by default; off on the box doctor.
  A crash     each start of the service notes the boot; a stop (a shutdown) marks it clean. A new
              boot with no clean mark is a box that stopped without shutting down (a hang, a crash
              or the power going): its last snapshots and the previous boot's journal tail are filed
              under STATE/crashwatch/crashes/<time>/ for the box doctor (the last 30 kept).
  Pre-emption the WiFi radio watched for a hardware or driver failure, told apart from an access
              point going away (that is uplink.py's): its interface or USB device gone, or a burst
              of driver errors. As far as the owner chose (box doctor):
                off      nothing
                warn     a fuller snapshot and the doctor's finding (the default)
                radio    ... and the radio reset at once: its USB device unbound and bound again (its
                         hub's, when the device itself is gone), or its driver reloaded
                reboot   ... and the box restarted if the radio isn't back within 3 minutes
              With uplink.py's guards: the owner's Hold, guests on the hotspot (unless the uplink's
              settings say to ignore them), no reboot while a build or an update runs, within
              15 minutes of boot, or past the uplink's daily cap (one shared history).
  A hang      the kernel itself freezing, which nothing in user space can act on. Two choices, off by
              default, each the owner's (box doctor): reboot on a kernel panic, oops or lockup
              (kernel.panic and its kin); and a watchdog (the board's own, or the kernel's softdog
              where it has none) that systemd keeps fed, so a frozen system restarts by itself.

    crashwatch.py run               the watcher
    crashwatch.py boot | shutdown   the service's start and stop marks (ExecStartPre, ExecStopPost)
    crashwatch.py status            what it knows, as JSON
    crashwatch.py set KEY VALUE     snapshots on|off, preempt off|warn|radio|reboot,
                                    panic on|off, watchdog on|off
    crashwatch.py undo-all          for uninstall.sh: the hang settings taken away

Settings in /etc/hub/crashwatch.json (root's). Stdlib only, besides irate_box.hub (uplink, netinv)
when acting on the radio.
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

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
SETTINGS = ETC / "crashwatch.json"
DIR = STATE / "crashwatch"
SNAP = DIR / "snap.log"
RUN = DIR / "run.json"               # this boot: its id, when it started, whether it stopped cleanly
CRASHES = DIR / "crashes"
INDEX = DIR / "crashes.json"
EVENTS = DIR / "events.json"         # the radio's failures and what was done, newest last
STATUS = DIR / "status.json"         # for the box doctor: the radios watched, the floods, the last look
PROC = Path(os.environ.get("HUB_PROC", "/proc"))
SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
KMSG = os.environ.get("HUB_KMSG", "/dev/kmsg")
SYSCTL = Path(os.environ.get("HUB_SYSCTL_DIR", "/etc/sysctl.d")) / "90-irate-box-hang.conf"
MODULES = Path(os.environ.get("HUB_MODULES_LOAD", "/etc/modules-load.d")) / "irate-box-softdog.conf"
SYSTEMD_CONF = Path(os.environ.get("HUB_SYSTEMD_CONF_DIR", "/etc/systemd/system.conf.d")) / "90-irate-box-watchdog.conf"

TICK = 30
SNAP_MAX = 4_000_000
KEEP_CRASHES = 30
PREEMPT = ("off", "warn", "radio", "reboot")
DEFAULT = {"snapshots": True, "preempt": "warn", "panic": False, "watchdog": False}
RADIO_WAIT = 180          # seconds for a reset radio to come back before a reboot
RADIO_GAP = 900           # no second radio reset within this
BOOT_CALM = 900           # no reboot within this of boot
FLOOD = 30                # kernel lines of one kind in one look (30 s, so one a second) that make a flood
ERR_BURST = 5             # driver errors within two looks that make a failure
# A radio in trouble, as the kernel says it (the AIC8800's own words, and the USB core's).
RADIO_ERR = re.compile(r"USB disconnect|tx timeout|cmd (?:tx )?time ?out|firmware (?:crash|error|fail)|fw (?:crash|error)"
                       r"|failed to (?:send|transmit|xmit)|error -(?:71|110|19)\b|descriptor read.*error|reset (?:high|full)-speed USB",
                       re.I)
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
    if raw.get("preempt") in PREEMPT:
        out["preempt"] = raw["preempt"]
    return out


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
            except OSError as exc:          # EPIPE: lines overwritten before we read them
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
    counts = Counter(kind(l) for l in lines)
    flood = {k: n for k, n in counts.items() if n >= limit}
    words = {k: next((w for w in k.split() if "#" not in w), None) for k in flood}
    keep = []
    for l in lines:
        k = kind(l)
        if k not in flood:
            hit = next((f for f, w in words.items() if w and f" {w} " in f" {l.split('] ', 1)[-1]} "), None)
            if not hit:
                keep.append(l)
                continue
            k = hit
        flood[k] = flood.get(k, 0) + (0 if counts[kind(l)] >= limit else 1)
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
    lines = [f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} up {uptime():.0f}s load {' '.join(load[:3])} boot {boot_id()[:8]}",
             "  mem: " + "  ".join(f"{k} {m[k]} MB" for k in ("MemAvailable", "MemFree", "SwapFree", "Slab", "Committed_AS") if k in m),
             f"  temp {temperature()}C  procs {len(list(PROC.glob('[0-9]*')))}",
             *[f"  {kb:>7} KB {name}" for kb, name in heaviest()],
             "  links: " + ", ".join(links()),
             *[f"  wifi {l.strip()}" for l in _read(PROC / "net/wireless").splitlines()[2:]],
             *[f"  kernel: {n} lines like \"{k}\"" for k, n in flood.items()],
             *[f"  k {l}" for l in kernel[-40:]]]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def append_snapshot(text):
    DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR, 0o700)
    if SNAP.exists() and SNAP.stat().st_size > SNAP_MAX:
        os.replace(SNAP, SNAP.with_name("snap.log.1"))
    fd = os.open(SNAP, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, text.encode())
        os.fdatasync(fd)
    finally:
        os.close(fd)


# --- a crash, at boot ----------------------------------------------------------------------------

def journal_tail(n=300):
    try:
        r = subprocess.run(["journalctl", "-b", "-1", "--no-pager", "-o", "short-monotonic", "-n", str(n * 4)],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return ""
    keep, flood = floods(r.stdout.splitlines(), limit=40)
    return "\n".join([f"({n} lines like \"{k}\" left out)" for k, n in flood.items()] + keep[-n:])


def snapshots_tail(nbytes=200_000):
    text = _read(SNAP.with_name("snap.log.1")) + _read(SNAP)
    return text[-nbytes:]


def last_snapshot(text):
    i = text.rfind("=== ")
    return text[i:] if i >= 0 else ""


def boot(now=None):
    """At the service's start: a previous boot that never stopped cleanly is filed as a crash."""
    now = now or time.time()
    DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DIR, 0o700)
    cur, prev = boot_id(), _json(RUN, None)
    filed = None
    if prev and prev.get("boot") and prev["boot"] != cur and not prev.get("clean"):
        stamp = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(now))
        d = CRASHES / stamp
        d.mkdir(parents=True, exist_ok=True)
        snaps = snapshots_tail()
        (d / "snapshots.log").write_text(snaps)
        (d / "journal-previous-boot.log").write_text(journal_tail())
        events = [e for e in _json(EVENTS, []) if e.get("boot") == prev["boot"]]
        filed = {"dir": stamp, "boot": prev["boot"], "started": prev.get("at"), "detected": now,
                 "last": last_snapshot(snaps)[:1500], "events": events[-5:]}
        (d / "info.json").write_text(json.dumps(filed, indent=1))
        index = _json(INDEX, []) + [filed]
        for old in index[:-KEEP_CRASHES]:
            shutil.rmtree(CRASHES / old.get("dir", "-"), ignore_errors=True)
        _write(INDEX, index[-KEEP_CRASHES:])
    if not prev or prev.get("boot") != cur:
        _write(RUN, {"boot": cur, "at": now, "clean": False})
    else:
        _write(RUN, dict(prev, clean=False))
    return filed


def shutdown():
    rec = _json(RUN, {})
    if rec.get("boot") == boot_id():
        _write(RUN, dict(rec, clean=True))


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
        names = {n for n in (iface, r.get("driver"), r.get("port") and f"{r['port']}:") if n}
        if (r.get("driver") or "").startswith("aic"):
            names |= {"aicwf", "aicbsp", "rwnx"}      # the AIC8800 driver's own prefixes
        errs = [l for l in kernel if RADIO_ERR.search(l) and any(n in l for n in names)]
        out[iface] = (why, errs)
    return out


class Preempt:
    """What to do about a failing radio, as far as the owner chose. act(now, states) returns the
    events of this look; the system's side (resetting, rebooting, the guards) is passed in."""
    def __init__(self, level, reset, reboot, guards, log):
        self.level, self.reset, self.reboot, self.guards, self.log = level, reset, reboot, guards, log
        self.recent = {}        # iface: deque of (time, error count)
        self.failed = {}        # iface: since
        self.last_reset = float("-inf")
        self.reset_at = {}      # iface: when it was reset in this failure

    def act(self, now, states):
        events = []
        for iface, (gone, errs) in states.items():
            q = self.recent.setdefault(iface, deque(maxlen=2))
            q.append((now, len(errs)))
            burst = sum(n for _, n in q) >= ERR_BURST
            bad = bool(gone) or burst
            if not bad:
                if iface in self.failed:
                    events.append(self.log(now, iface, "recovered", "the radio is back"))
                    self.failed.pop(iface)
                    self.reset_at.pop(iface, None)
                continue
            why = "; ".join(gone) or f"{sum(n for _, n in q)} driver errors in a minute: {errs[-1][:160] if errs else ''}"
            if iface not in self.failed:
                self.failed[iface] = now
                events.append(self.log(now, iface, "failed", why, snapshot=True))
            if self.level in ("radio", "reboot") and iface not in self.reset_at:
                held = self.guards("radio", now)
                if held:
                    events.append(self.log(now, iface, "held", f"radio reset held: {held}"))
                    self.reset_at[iface] = now          # once per failure: said, not repeated
                elif now - self.last_reset < RADIO_GAP:
                    pass
                else:
                    self.last_reset = self.reset_at[iface] = now
                    events.append(self.log(now, iface, "reset", self.reset(iface)))
            elif self.level == "reboot" and iface in self.reset_at and now - self.reset_at[iface] >= RADIO_WAIT:
                held = self.guards("reboot", now)
                if held:
                    if not any(e.get("kind") == "held" for e in events):
                        events.append(self.log(now, iface, "held", f"reboot held: {held}"))
                    self.reset_at[iface] = now          # look again after another wait
                else:
                    events.append(self.log(now, iface, "reboot", "the radio did not come back: rebooting"))
                    self.reboot()
        return events


def reset_radio(iface, known):
    """Unbind and bind its USB device again (its parent hub's when the device has gone), or reload
    its driver."""
    r = known.get(iface, {})
    drv = SYS / "bus/usb/drivers/usb"
    port = r.get("port")
    if port and re.fullmatch(r"[0-9]+-[0-9.]+", port):
        target = port if (SYS / "bus/usb/devices" / port).exists() else port.rsplit(".", 1)[0] if "." in port else None
        if target and (SYS / "bus/usb/devices" / target).exists():
            try:
                (drv / "unbind").write_text(target)
                time.sleep(3)
                (drv / "bind").write_text(target)
                return f"USB {target} unbound and bound again" + ("" if target == port else f" (the hub the radio hangs off: {port} had gone)")
            except OSError as exc:
                return f"USB {target}: {exc}"
    mod = r.get("driver")
    if mod and re.fullmatch(r"[A-Za-z0-9_-]+", mod):
        subprocess.run(["modprobe", "-r", mod], capture_output=True, timeout=60)
        time.sleep(2)
        p = subprocess.run(["modprobe", mod], capture_output=True, text=True, timeout=60)
        return f"{mod} reloaded" if p.returncode == 0 else f"modprobe {mod}: {p.stderr.strip()[:160]}"
    return "no way to reset this radio here"


def guards(step, now):
    """uplink.py's guards, shared: the owner's Hold, guests on the hotspot, a busy box, the reboot cap."""
    from irate_box.hub import uplink
    chosen = uplink.load_settings()
    eff = uplink.effective(chosen)
    if chosen.get("hold_until", 0) > now:
        return "the owner holds repairs (Network → Hold)"
    if eff.get("guests") == "protect":
        try:
            n = uplink.guests()
        except Exception:  # noqa: BLE001 - a radio that has failed may not answer iw at all
            n = 0
        if n:
            return f"{n} guest{'s are' if n != 1 else ' is'} on the hotspot"
    if step == "reboot":
        if uptime() < BOOT_CALM:
            return "the box started less than 15 minutes ago"
        busy = uplink.busy()
        if busy:
            return f"{busy} is running"
        day = [t for t in _json(uplink.REBOOTS, []) if t > now - 86400]
        if len(day) >= eff.get("reboots_per_day", 2):
            return f"it has rebooted {len(day)} times today already"
    return None


def reboot_now():
    from irate_box.hub import uplink
    from irate_box.root import safeio
    hist = [t for t in _json(uplink.REBOOTS, []) if t > time.time() - 86400] + [time.time()]
    safeio.write(uplink.REBOOTS, json.dumps(hist), 0o600)
    os.sync()
    subprocess.Popen(["systemctl", "reboot"])


# --- the hang settings ---------------------------------------------------------------------------

def watchdog_device():
    """'hardware' when the board has a watchdog of its own, 'softdog' when the kernel can give one,
    else None."""
    if any((SYS / "class/watchdog").glob("watchdog*")) and not _softdog_loaded():
        return "hardware"
    r = subprocess.run(["modinfo", "softdog"], capture_output=True, text=True)
    return "softdog" if r.returncode == 0 or _softdog_loaded() else None


def _softdog_loaded():
    return (SYS / "module/softdog").exists()


def set_panic(on):
    if on:
        from irate_box.root import safeio
        SYSCTL.parent.mkdir(parents=True, exist_ok=True)
        safeio.write(SYSCTL, "# Written by irate-box (root/crashwatch.py): restart on a kernel panic, oops or lockup.\n"
                     + "".join(f"{k} = {v}\n" for k, v in HANG_SYSCTL.items()))
        for k, v in HANG_SYSCTL.items():
            subprocess.run(["sysctl", "-q", "-w", f"{k}={v}"], capture_output=True)
        return "the box restarts 10 s after a kernel panic, oops or lockup"
    SYSCTL.unlink(missing_ok=True)
    for k in HANG_SYSCTL:
        subprocess.run(["sysctl", "-q", "-w", f"{k}=0"], capture_output=True)
    return "a kernel panic or lockup no longer restarts the box"


def set_watchdog(on):
    from irate_box.root import safeio
    if on:
        kind = watchdog_device()
        if not kind:
            raise ValueError("this kernel has no watchdog: neither the board's own nor softdog")
        if kind == "softdog":
            MODULES.parent.mkdir(parents=True, exist_ok=True)
            safeio.write(MODULES, "# Written by irate-box (root/crashwatch.py): a watchdog where the board has none.\nsoftdog\n")
            p = subprocess.run(["modprobe", "softdog"], capture_output=True, text=True)
            if p.returncode:
                MODULES.unlink(missing_ok=True)
                raise ValueError(f"modprobe softdog: {p.stderr.strip()[:160]}")
        SYSTEMD_CONF.parent.mkdir(parents=True, exist_ok=True)
        safeio.write(SYSTEMD_CONF, "# Written by irate-box (root/crashwatch.py): systemd feeds the watchdog; a frozen\n"
                     "# system stops feeding it and the box restarts.\n[Manager]\nRuntimeWatchdogSec=60\nRebootWatchdogSec=5min\n")
        subprocess.run(["systemctl", "daemon-reexec"], capture_output=True, timeout=120)
        which = "the board's own" if kind == "hardware" else "the kernel's softdog"
        return f"a watchdog ({which}) restarts the box if it freezes for a minute"
    SYSTEMD_CONF.unlink(missing_ok=True)
    subprocess.run(["systemctl", "daemon-reexec"], capture_output=True, timeout=120)
    if MODULES.exists():
        MODULES.unlink()
        subprocess.run(["modprobe", "-r", "softdog"], capture_output=True)
    return "no watchdog: a frozen box waits for someone to restart it"


def set_option(key, value):
    """One setting, as the box doctor's switches give it. Returns what is now so."""
    s = load_settings()
    if key == "snapshots" and value in ("on", "off"):
        s["snapshots"] = value == "on"
        msg = "snapshots to the box's storage every 30 s" if s["snapshots"] else "no snapshots: a crash leaves only the journal's last sync"
    elif key == "preempt" and value in PREEMPT:
        s["preempt"] = value
        msg = {"off": "the radio is not watched", "warn": "a failing radio is noted and said, nothing more",
               "radio": "a failing radio is reset at once", "reboot": "a failing radio is reset, and the box restarted if it doesn't come back"}[value]
    elif key == "panic" and value in ("on", "off"):
        msg = set_panic(value == "on")
        s["panic"] = value == "on"
    elif key == "watchdog" and value in ("on", "off"):
        msg = set_watchdog(value == "on")
        s["watchdog"] = value == "on"
    else:
        raise ValueError(f"{key} {value}: not a crash watch setting")
    save_settings(s)
    return msg


def undo_all():
    done = []
    s = load_settings()
    if s["panic"] or SYSCTL.exists():
        done.append(set_panic(False))
    if s["watchdog"] or SYSTEMD_CONF.exists():
        done.append(set_watchdog(False))
    return done


# --- running ---------------------------------------------------------------------------------

def status():
    s = load_settings()
    st = _json(STATUS, {})
    return {"settings": s, "running": st, "crashes": _json(INDEX, []), "events": _json(EVENTS, [])[-20:],
            "watchdog_device": None if os.geteuid() and not os.environ.get("HUB_SYSFS") else watchdog_device()}


def run(once=False):
    kmsg = Kmsg()
    known = radios()
    events = deque(_json(EVENTS, [])[-50:], maxlen=50)
    floods_hour = Counter()
    hour_start = time.time()
    status_at = 0.0
    pending = {"snap": None}

    def log(now, iface, kind, text, snapshot=False):
        e = {"at": now, "boot": boot_id(), "iface": iface, "kind": kind, "text": text}
        events.append(e)
        _write(EVENTS, list(events))
        if snapshot:
            pending["snap"] = f"  !! {iface}: {text}"
        return e

    settings = load_settings()
    pre = Preempt(settings["preempt"], lambda i: reset_radio(i, known), reboot_now, guards, log)
    mtime = 0.0
    while True:
        now = time.time()
        try:
            m = SETTINGS.stat().st_mtime
        except OSError:
            m = 0.0
        if m != mtime:
            mtime, settings = m, load_settings()
            pre.level = settings["preempt"]
        lines = kmsg.read()
        keep, flood = floods(lines)
        floods_hour.update(flood)
        if settings["preempt"] != "off":
            now_known = radios()
            for i, r in now_known.items():
                known[i] = r          # a radio that came back, or a new one, watched from now on
            pre.act(now, radio_state(known, lines))
        extra = pending.pop("snap", None) or ""
        pending["snap"] = None
        if settings["snapshots"] or extra:
            if extra:
                extra += "\n  last kernel lines:\n" + "\n".join(f"    {l}" for l in lines[-60:])
            try:
                append_snapshot(snapshot(keep, flood, extra))
            except OSError as exc:
                print(f"crashwatch: snapshot not written: {exc}", file=sys.stderr)
        # For the doctor every 5 minutes: the radios, and the floods counted over the hour so far.
        if now - status_at >= 300 or once:
            status_at = now
            _write(STATUS, {"at": now, "boot": boot_id(), "radios": known, "floods": dict(floods_hour.most_common(5)),
                            "flood_window": max(now - hour_start, TICK)})
        if now - hour_start >= 3600:
            floods_hour.clear()
            hour_start = now
        if once:
            return
        time.sleep(TICK)


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "run":
        run()
    elif cmd == "boot":
        f = boot()
        print(f"crashwatch: the previous boot stopped without shutting down; filed as {f['dir']}" if f else "crashwatch: boot noted")
    elif cmd == "shutdown":
        shutdown()
    elif cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "set" and len(argv) == 3:
        print(set_option(argv[1], argv[2]))
    elif cmd == "undo-all":
        print("\n".join(undo_all()) or "nothing to undo")
    else:
        print(__doc__.split("    crashwatch.py run")[0].strip().splitlines()[0])
        print("usage: crashwatch.py run | boot | shutdown | status | set KEY VALUE | undo-all")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
