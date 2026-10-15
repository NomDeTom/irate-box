#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The clock's sources, as the owner chooses them on the Clock page.

Two ways, the owner's choice:
  image    the time service the image came with (systemd-timesyncd on Armbian and Debian), its servers chosen:
           an NTP server on the local network, the internet's pool, or both. A drop-in of ours,
           /etc/systemd/timesyncd.conf.d/52irate-box.conf.
  chrony   chrony in its place (suggested): the same two, and GPS through gpsd (the GPS time toolkit), each
           on or off, each with its own state from `chronyc sources`. Our own files in chrony's sources.d and
           conf.d; the pool line of chrony.conf turned off only when the pool is not chosen. Installing chrony
           removes timesyncd (Debian's packages conflict); both come from Debian or, offline, the toolkits' cache.
           chrony can also serve the time to the hotspot's guests: an `allow` for the hotspot's network, and the
           hotspot firewall opens UDP 123 to guests while it does (firewall.services_here asks serving()).

The clock module (rtc.py) is not one of these: it sets the time at boot and keeps it, whichever service runs.

What the image had is kept in RECORD the first time anything is changed (its daemon, whether timesyncd was
installed and enabled, chrony.conf's text), and Restore (undo) puts all of it back: our files gone, chrony.conf as it
was, chrony removed and timesyncd installed again if this page swapped them, the GPS toolkit removed if this page
added it.

  irate-box clock status     what is chosen and how each source stands
  irate-box clock undo-all   put back what the image had (uninstall)
"""
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RECORD = ETC / "clock.json"
SYS = Path(os.environ.get("HUB_CLOCK_SYSROOT", "/"))
CHRONY_CONF = SYS / "etc/chrony/chrony.conf"
CHRONY_SOURCES = SYS / "etc/chrony/sources.d/52irate-box.sources"
CHRONY_D = SYS / "etc/chrony/conf.d/52irate-box.conf"
TIMESYNCD_D = SYS / "etc/systemd/timesyncd.conf.d/52irate-box.conf"
KIT_POOL = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits")) / "pool"
POOL = ("0.debian.pool.ntp.org", "1.debian.pool.ntp.org", "2.debian.pool.ntp.org", "3.debian.pool.ntp.org")
GPS_KIT = "gpstime"
# A server on the local network: an IPv4 or IPv6 address, or a host name.
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
# The page's choice: clock-sources:SERVICE:POOL:GPS:SERVE:LOCAL (LOCAL "-" for none), and clock-undo.
CHOICE_RE = re.compile(r"^clock-sources:(image|chrony):([01]):([01]):([01]):(-|[A-Za-z0-9][A-Za-z0-9.:-]{0,252})$")
STATE_WORDS = {"in-use": "In use", "answering": "Answering, not used", "not-answering": "Not answering",
               "not-found": "Not found", "unknown": "Not reported"}


def run(*cmd, timeout=120):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                              env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 1, "", str(exc))


def load():
    try:
        rec = json.loads(RECORD.read_text())
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(rec):
    safeio.write(RECORD, json.dumps(rec, indent=1), mode=0o600)


def _installed(pkg):
    out = run("dpkg-query", "-W", "-f", "${Status}", pkg).stdout
    return out.strip() == "install ok installed"


def _active(unit):
    return run("systemctl", "is-active", unit).stdout.strip() == "active"


def _enabled(unit):
    return run("systemctl", "is-enabled", unit).stdout.strip() == "enabled"


def image_daemon():
    """The time service running now: chrony, systemd-timesyncd, or None."""
    for unit, name in (("chrony.service", "chrony"), ("systemd-timesyncd.service", "systemd-timesyncd")):
        if _active(unit):
            return name
    return None


def _image_as_it_was():
    """What the image had, read before the first change: kept until Restore."""
    return {"date": time.strftime("%Y-%m-%d"), "daemon": image_daemon(),
            "chrony": _installed("chrony"), "timesyncd": _installed("systemd-timesyncd"),
            "timesyncd_enabled": _enabled("systemd-timesyncd.service"),
            "chrony_conf": CHRONY_CONF.read_text() if CHRONY_CONF.is_file() else None}


def parse(choice):
    """('chrony'|'image', pool, gps, serve, local or '') from the page's choice, or ValueError."""
    m = CHOICE_RE.match(choice or "")
    if not m:
        raise ValueError("a clock choice: clock-sources:image|chrony:POOL:GPS:SERVE:LOCAL")
    service, pool, gps, serve, local = m.group(1), m.group(2) == "1", m.group(3) == "1", m.group(4) == "1", m.group(5)
    local = "" if local == "-" else local
    if gps and service != "chrony":
        raise ValueError("GPS needs chrony: systemd-timesyncd takes no reference clock")
    if serve and service != "chrony":
        raise ValueError("Serving the time needs chrony: systemd-timesyncd only asks, it does not answer")
    if local and not _host_ok(local):
        raise ValueError(f"{local} is not an address or a host name")
    if not (pool or gps or local):
        raise ValueError("Select at least one source: with none, nothing sets the clock")
    return service, pool, gps, serve, local


def hotspot_net():
    """The hotspot's network, from the hub's address on it (apmode.ADDRESS, a /24)."""
    from irate_box.hub import apmode
    return str(ipaddress.ip_network(f"{apmode.ADDRESS}/24", strict=False))


def serving():
    """Is the box serving the time to the hotspot (chrony, by the owner's choice)? The firewall opens UDP 123 for it."""
    rec = load()
    return rec.get("service") == "chrony" and bool(rec.get("serve"))


def _firewall_refresh():
    """The hotspot firewall loaded again, if it is on, so UDP 123 opens or closes with serving()."""
    try:
        from irate_box.root import security
        fw = security.load_record().get("firewall")
        if fw:
            security._firewall_apply(fw)
            return True
    except Exception:  # noqa: BLE001 - the clock is set either way; the firewall's own finding says how it is
        return False
    return False


def _host_ok(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return bool(HOST_RE.match(host)) and ":" not in host and not host.startswith("-") and ".." not in host


# --- applying ----------------------------------------------------------------------------------------------

def _install(pkg, why):
    """A package from Debian, or offline from the toolkits' cache (its newest .deb there)."""
    if run("apt-get", "install", "-y", pkg, timeout=1200).returncode == 0:
        return f"{pkg} installed"
    debs = sorted(KIT_POOL.glob(f"{pkg}_*.deb")) if KIT_POOL.is_dir() else []
    if debs and run("apt-get", "install", "-y", str(debs[-1]), timeout=1200).returncode == 0:
        return f"{pkg} installed from the toolkits' cache"
    raise ValueError(f"{pkg} could not be installed: it needs the internet once, or {why}")


def timesyncd_text(pool, local):
    return ("# Written by irate-box's Clock page (/admin): the servers systemd-timesyncd asks. Change them there.\n[Time]\n"
            f"NTP={local}\nFallbackNTP={' '.join(POOL) if pool else ''}\n")


def chrony_sources_text(local):
    return ("# Written by irate-box's Clock page (/admin): a time server on the local network.\n"
            + (f"server {local} iburst\n" if local else ""))


def chrony_conf_text(gps, serve=False, net=None):
    lines = ["# Written by irate-box's Clock page (/admin). Change it there.",
             "# The kernel's clock kept in step, so other tools see the time as synchronised.", "rtcsync"]
    if serve:
        lines += ["# The time served to the hotspot's guests (the hotspot firewall opens UDP 123 for it).",
                  f"allow {net or hotspot_net()}"]
    if gps:
        lines += ["# GPS through gpsd: its time from the receiver's sentences (shared memory 0),",
                  "refclock SHM 0 refid GPS precision 1e-1 offset 0.0 delay 0.2"]
        if (SYS / "dev/pps0").exists():
            lines += ["# and its pulse per second where the receiver gives one, locked to them.",
                      "refclock PPS /dev/pps0 lock GPS refid PPS"]
    return "\n".join(lines) + "\n"


def without_pool(text):
    """chrony.conf with its pool and server lines turned off (commented, so Restore is exact either way)."""
    return "".join(f"#irate-box off: {l}" if re.match(r"^\s*(pool|server)\s", l) else l for l in text.splitlines(True))


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(path, text)


def _chrony_on(rec, pool, gps, serve, local, said):
    img = rec["image"]
    if not _installed("chrony"):
        said.append(_install("chrony", "the GPS time toolkit's cache, which holds it"))
        rec["installed_chrony"] = True
        if img.get("timesyncd") and not _installed("systemd-timesyncd"):
            rec["removed_timesyncd"] = True
    if gps and not rec.get("installed_gps_kit"):
        from irate_box.root import kits
        if GPS_KIT not in kits.installed_state():
            if not kits.manifest(GPS_KIT):
                raise ValueError("GPS needs the GPS time toolkit: refresh its cache on Toolkits while the box has internet")
            kits.install(GPS_KIT, None, log=lambda *_: None)
            rec["installed_gps_kit"] = True
            said.append("the GPS time toolkit installed (gpsd)")
    # The image's own pool line off only when the pool is not chosen; back as it was when it is.
    if img.get("chrony_conf") is None and CHRONY_CONF.is_file():
        img["chrony_conf"] = CHRONY_CONF.read_text()
    if img.get("chrony_conf") is not None:
        want = img["chrony_conf"] if pool else without_pool(img["chrony_conf"])
        if not CHRONY_CONF.is_file() or CHRONY_CONF.read_text() != want:
            _write(CHRONY_CONF, want)
    # With no chrony.conf at all (a chrony without Debian's file), the pool goes in our sources instead.
    _write(CHRONY_SOURCES, chrony_sources_text(local)
           + ("".join(f"pool {p} iburst\n" for p in POOL[2:3]) if pool and img.get("chrony_conf") is None else ""))
    _write(CHRONY_D, chrony_conf_text(gps, serve))
    # A toolkit install leaves the units it brought disabled: chrony is wanted here.
    run("systemctl", "enable", "chrony.service")
    run("systemctl", "restart", "chrony.service", timeout=120)
    said.append("chrony keeps the time: " + _sources_words(pool, gps, local)
                + (f", and serves it to the hotspot ({hotspot_net()})" if serve else ""))


def _chrony_off(rec, said):
    """Back from chrony to the image's own service: our files gone, chrony.conf as it was, the packages as they were."""
    img = rec.get("image") or {}
    for f in (CHRONY_SOURCES, CHRONY_D):
        f.unlink(missing_ok=True)
    if img.get("chrony_conf") is not None and CHRONY_CONF.is_file() and CHRONY_CONF.read_text() != img["chrony_conf"]:
        _write(CHRONY_CONF, img["chrony_conf"])
    if rec.pop("installed_gps_kit", False):
        from irate_box.root import kits
        try:
            kits.remove(GPS_KIT, log=lambda *_: None)
            said.append("the GPS time toolkit removed")
        except ValueError:
            pass
    if rec.pop("installed_chrony", False):
        run("apt-get", "remove", "-y", "chrony", timeout=1200)
        said.append("chrony removed")
    if rec.pop("removed_timesyncd", False):
        said.append(_install("systemd-timesyncd", "the System toolkit's cache, which holds it"))
    if img.get("daemon") == "chrony":
        run("systemctl", "restart", "chrony.service", timeout=120)


def _timesyncd(pool, local, said):
    _write(TIMESYNCD_D, timesyncd_text(pool, local))
    run("systemctl", "enable", "systemd-timesyncd.service")
    run("systemctl", "restart", "systemd-timesyncd.service", timeout=120)
    said.append("systemd-timesyncd asks " + _sources_words(pool, False, local))


def _sources_words(pool, gps, local):
    parts = (["GPS"] if gps else []) + ([f"the server {local} on the local network"] if local else []) \
        + (["the internet's pool"] if pool else [])
    return ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else "nothing"


def apply(choice):
    """The page's choice applied; what was done, in a sentence."""
    service, pool, gps, serve, local = parse(choice)
    rec = load()
    if "image" not in rec:
        rec["image"] = _image_as_it_was()
    said = []
    if service == "chrony":
        TIMESYNCD_D.unlink(missing_ok=True)
        _chrony_on(rec, pool, gps, serve, local, said)
    else:
        if rec.get("service") == "chrony" or rec.get("installed_chrony"):
            _chrony_off(rec, said)
        if not _installed("systemd-timesyncd"):
            said.append(_install("systemd-timesyncd", "the System toolkit's cache, which holds it"))
        _timesyncd(pool, local, said)
    was_serving = serving()
    rec.update(service=service, pool=pool, gps=gps, serve=serve, local=local, at=time.time())
    _save(rec)
    if serve != was_serving and _firewall_refresh():
        said.append("the hotspot firewall " + ("opens UDP 123 to guests" if serve else "closes UDP 123 again"))
    return ". ".join(s[0].upper() + s[1:] for s in said) + "."


def undo():
    """Everything back as the image had it."""
    rec = load()
    if "image" not in rec:
        raise ValueError("The clock's sources were not changed from this page")
    said = []
    _chrony_off(rec, said)
    TIMESYNCD_D.unlink(missing_ok=True)
    img = rec["image"]
    if _installed("systemd-timesyncd"):
        if img.get("timesyncd_enabled"):
            run("systemctl", "enable", "systemd-timesyncd.service")
        if img.get("daemon") == "systemd-timesyncd":
            run("systemctl", "restart", "systemd-timesyncd.service", timeout=120)
    was_serving = serving()
    RECORD.unlink(missing_ok=True)
    if was_serving and _firewall_refresh():
        said.append("the hotspot firewall closes UDP 123 again")
    said.append("the clock's sources put back as the image had them")
    return ". ".join(s[0].upper() + s[1:] for s in said) + "."


def undo_all():
    if "image" not in load():
        return ["the clock's sources: not changed from the Clock page"]
    try:
        return [undo()]
    except ValueError as exc:
        return [f"the clock's sources: {exc}"]


# --- how each source stands -------------------------------------------------------------------------------

def parse_chronyc(text):
    """`chronyc -n sources` as [{mode: server|refclock|peer, state, name}]; state: in-use (* or +), answering (- ~ x),
    not-answering (?)."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^([\^#=])([*+\-?x~])\s+(\S+)", line)
        if not m:
            continue
        mode = {"^": "server", "#": "refclock", "=": "peer"}[m.group(1)]
        state = "in-use" if m.group(2) in "*+" else "not-answering" if m.group(2) == "?" else "answering"
        out.append({"mode": mode, "state": state, "name": m.group(3)})
    return out


def _addresses(host):
    try:
        return {a[4][0] for a in socket.getaddrinfo(host, 123, proto=socket.IPPROTO_UDP)}
    except OSError:
        return {host}


def _best(states):
    for s in ("in-use", "answering", "not-answering"):
        if s in states:
            return s
    return "not-found"


def gps_device():
    """A receiver gpsd could read: a serial USB device or the UART gpsd is told to use."""
    devs = [p for pat in ("dev/ttyACM*", "dev/ttyUSB*", "dev/gps*") for p in SYS.glob(pat)]
    return bool(devs)


def source_states(rec, chronyc_text=None, timesync=None):
    """[{source: gps|local|pool, name, state}] for each chosen source."""
    out = []
    if rec.get("service") == "chrony":
        rows = parse_chronyc(chronyc_text if chronyc_text is not None else run("chronyc", "-n", "sources").stdout)
        local_ips = _addresses(rec["local"]) if rec.get("local") else set()
        if rec.get("gps"):
            refs = [r["state"] for r in rows if r["mode"] == "refclock" and r["name"] in ("GPS", "PPS")]
            state = _best(refs) if refs else "not-found"
            if state != "in-use" and not gps_device():
                state = "not-found"
            out.append({"source": "gps", "name": "GPS", "state": state})
        if rec.get("local"):
            states = [r["state"] for r in rows if r["mode"] == "server" and (r["name"] in local_ips or r["name"] == rec["local"])]
            out.append({"source": "local", "name": rec["local"], "state": _best(states)})
        if rec.get("pool"):
            states = [r["state"] for r in rows if r["mode"] == "server" and r["name"] not in local_ips and r["name"] != rec.get("local")]
            out.append({"source": "pool", "name": "the internet's pool", "state": _best(states)})
    elif rec.get("service") == "image":
        # timesyncd says only the server it uses now.
        if timesync is None:
            timesync = dict(l.split("=", 1) for l in run("timedatectl", "show-timesync", "-p", "ServerName", "-p",
                                                          "ServerAddress").stdout.splitlines() if "=" in l)
        name = timesync.get("ServerName", "")
        if rec.get("local"):
            out.append({"source": "local", "name": rec["local"], "state": "in-use" if name == rec["local"] else "unknown"})
        if rec.get("pool"):
            out.append({"source": "pool", "name": "the internet's pool",
                        "state": "in-use" if name.endswith("pool.ntp.org") else "unknown"})
    return out


def status():
    rec = load()
    return {"service": rec.get("service"), "pool": rec.get("pool"), "gps": rec.get("gps"), "serve": bool(rec.get("serve")),
            "local": rec.get("local") or "",
            "changed": "image" in rec, "daemon": image_daemon(), "chrony_installed": _installed("chrony"),
            "gps_device": gps_device(), "sources": source_states(rec) if rec.get("service") else []}


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "set" and len(argv) == 2:
        print(apply(argv[1]))
    elif cmd == "undo":
        print(undo())
    elif cmd == "undo-all":
        for line in undo_all():
            print(line)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
