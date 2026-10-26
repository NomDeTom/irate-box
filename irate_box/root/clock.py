#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The clock's sources, as the owner chooses them on the Clock page.

Two ways, the owner's choice:
  image    the time service the image came with (systemd-timesyncd on Armbian and Debian), its servers chosen:
           an NTP server on the local network, the internet's pool, or both. A drop-in of ours,
           /etc/systemd/timesyncd.conf.d/52irate-box.conf.
  chrony   chrony in its place (recommended): the same two, and GPS through gpsd (the GPS time toolkit), each
           on or off, each with its own state from `chronyc sources`. Our own files in chrony's sources.d and
           conf.d; the pool line of chrony.conf turned off only when the pool is not chosen. Installing chrony
           removes timesyncd (Debian's packages conflict); both come from Debian or, offline, the toolkits' cache.
           chrony can also serve the time to the hotspot's guests: an `allow` for the hotspot's network, and the
           hotspot firewall opens UDP 123 to guests while it does (firewall.services_here asks serving()).

The clock module (rtc.py) sets the time at boot whichever service runs. With chrony it is a source as well: chrony
has no RTC driver of its own (Debian's 4.6 has PPS, SOCK, SHM and PHC), so irate-box-rtc-feed.service (`clock feed`)
reads the module every 4 s, by its kernel driver or irate-box's own, and sends chrony the offset over the SOCK
driver's socket: chrony polls it every 16 s, so each poll has several samples. Its delay is large, so chrony uses it only while GPS and the network are silent.

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
import socket
import subprocess
import sys
import time
from pathlib import Path

from irate_box.msg import M, Refused, render, sentences
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
# The page's choice: clock-sources:SERVICE:POOL:GPS:MODULE:SERVE:LOCAL (LOCAL "-" for none), and clock-undo.
CHOICE_RE = re.compile(
    r"^clock-sources:(image|chrony):([01]):([01]):([01]):([01]):(-|[A-Za-z0-9][A-Za-z0-9.:-]{0,252})"
    r"(?::((?:gps|local|pool|module)(?:,(?:gps|local|pool|module)){0,3}))?$"
)
# The sources in the owner's order, most trusted first (the Clock page's list). With chrony the first one chosen is
# preferred while it answers well, the others backing it up as chrony judges them; with the image's own service the
# servers are asked in this order.
ORDER = ("gps", "local", "pool", "module")
# The clock module fed to chrony: the socket chronyd makes, and the service that writes to it.
FEED_SOCK = Path(os.environ.get("HUB_CLOCK_FEED_SOCK", "/run/chrony.irate-box-rtc.sock"))
FEED_UNIT = "irate-box-rtc-feed.service"
UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
CODE = Path(__file__).resolve().parents[2]
FEED_EVERY = 4
SOCK_MAGIC = 0x534F434B
STATE_WORDS = {
    "in-use": "In use",
    "answering": "Answering, not used",
    "not-answering": "Not answering",
    "not-found": "Not found",
    "unknown": "Not reported",
}


def run(*cmd, timeout=120):
    try:
        return subprocess.run(  # noqa: TID251
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"),
        )
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
    return {
        "date": time.strftime("%Y-%m-%d"),
        "daemon": image_daemon(),
        "chrony": _installed("chrony"),
        "timesyncd": _installed("systemd-timesyncd"),
        "timesyncd_enabled": _enabled("systemd-timesyncd.service"),
        "chrony_conf": CHRONY_CONF.read_text() if CHRONY_CONF.is_file() else None,
    }


def module_ready():
    """Is a clock module set up (rtc.py's config)?"""
    try:
        from irate_box.root import rtc

        return bool(rtc.load_config())
    except Exception:  # noqa: BLE001
        return False


def hardware():
    """The box's hardware clock, the one that keeps the time while it is off, for the Clock page: a clock module on
    I2C that rtc.py set up ({kind: module, chip, bus, addr, by}), the board's own (the kernel's /dev/rtc0: {kind:
    board, name}), or none ({kind: none})."""
    try:
        from irate_box.root import rtc

        cfg = rtc.load_config()
    except Exception:  # noqa: BLE001
        cfg = None
    if cfg:
        return {"kind": "module", "chip": cfg["label"], "bus": cfg["bus"], "addr": cfg["addr"], "by": cfg.get("by")}
    try:
        name = (SYS / "sys/class/rtc/rtc0/name").read_text().strip()
    except OSError:
        name = None
    return {"kind": "board", "name": name} if name else {"kind": "none"}


def parse(choice):
    """('chrony'|'image', pool, gps, module, serve, local or '') from the page's choice, or ValueError."""
    m = CHOICE_RE.match(choice or "")
    if not m:
        raise Refused(
            M("clock-sources.not-a-choice", "This is not a choice of clock sources that the Clock page makes.")
        )
    service, pool, gps, module, serve, local = (
        m.group(1),
        m.group(2) == "1",
        m.group(3) == "1",
        m.group(4) == "1",
        m.group(5) == "1",
        m.group(6),
    )
    local = "" if local == "-" else local
    if gps and service != "chrony":
        raise Refused(
            M("clock-sources.gps-needs-chrony", "GPS needs chrony. systemd-timesyncd takes no reference clock.")
        )
    if serve and service != "chrony":
        raise Refused(
            M(
                "clock-sources.serve-needs-chrony",
                "Serving the time needs chrony. systemd-timesyncd only asks for the time, and does not answer.",
            )
        )
    if module and service != "chrony":
        raise Refused(
            M(
                "clock-sources.module-needs-chrony",
                "The hardware clock as a source needs chrony. With the image's own service, "
                "it sets the time at boot only.",
            )
        )
    if module and not module_ready():
        raise Refused(M("clock-sources.no-module", "No clock module is set up. Look for one on the Clock page first."))
    if local and not _host_ok(local):
        raise Refused(M("clock-sources.bad-host", "{host} is not an address or a host name.", host=local))
    if not (pool or gps or module or local):
        raise Refused(M("clock-sources.none", "Select at least one source. With none, nothing sets the clock."))
    return service, pool, gps, module, serve, local


def parse_order(choice):
    """The sources' order from the page's choice (its last field), each source once, any left out after in the
    default order; the default when the choice has none."""
    m = CHOICE_RE.match(choice or "")
    given = [s for s in (m.group(7) or "").split(",") if s] if m else []
    seen = list(dict.fromkeys(given))
    return seen + [s for s in ORDER if s not in seen]


def first_chosen(order, pool, gps, local, module):
    """The first source in the order that is chosen: the one chrony prefers."""
    on = {"gps": gps, "local": bool(local), "pool": pool, "module": module}
    return next((s for s in order if on[s]), None)


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

# Where a package comes from offline: the toolkit whose cache holds it.
FROM_GPS_KIT = M(
    "clock-sources.install.failed.gps-kit",
    "The box could not install {package}. Connect the box to the internet "
    "once, or refresh the cache of the GPS time toolkit on Toolkits.",
    package="chrony",
)
FROM_SYSTEM_KIT = M(
    "clock-sources.install.failed.system-kit",
    "The box could not install {package}. Connect the box to the internet "
    "once, or refresh the cache of the System toolkit on Toolkits.",
    package="systemd-timesyncd",
)


def _install(pkg, failed):
    """A package from Debian, or offline from the toolkits' cache (its newest .deb there); failed: the refusal if neither."""
    if run("apt-get", "install", "-y", pkg, timeout=1200).returncode == 0:
        return M("clock-sources.installed", "The box installed {package}.", package=pkg)
    debs = sorted(KIT_POOL.glob(f"{pkg}_*.deb")) if KIT_POOL.is_dir() else []
    if debs and run("apt-get", "install", "-y", str(debs[-1]), timeout=1200).returncode == 0:
        return M("clock-sources.installed-cache", "The box installed {package} from the toolkits' cache.", package=pkg)
    raise Refused(failed)


def timesyncd_text(pool, local, order=ORDER):
    """systemd-timesyncd asks NTP='s servers in turn: the local server and the pool in the owner's order."""
    servers = [
        x for s in order for x in ([local] if s == "local" and local else list(POOL) if s == "pool" and pool else [])
    ]
    return (
        "# Written by irate-box's Clock page (/admin): the servers systemd-timesyncd asks, in the order chosen there."
        f"\n[Time]\nNTP={' '.join(servers)}\nFallbackNTP=\n"
    )


def chrony_sources_text(local, pool=False, prefer=None):
    """The local server and the pool, ours (the image's own pool line is turned off while chrony is ours), the
    preferred one marked."""
    lines = [
        "# Written by irate-box's Clock page (/admin): the time servers chosen there, the first in its order preferred."
    ]
    if local:
        lines.append(f"server {local} iburst" + (" prefer" if prefer == "local" else ""))
    if pool:
        lines.append(f"pool {POOL[2]} iburst" + (" prefer" if prefer == "pool" else ""))
    return "\n".join(lines) + "\n"


def chrony_conf_text(gps, serve=False, net=None, module=False, prefer=None):
    lines = [
        "# Written by irate-box's Clock page (/admin). Change it there.",
        "# The kernel's clock kept in step, so other tools see the time as synchronised.",
        "rtcsync",
    ]
    if module:
        lines += [
            "# The clock module, fed by irate-box-rtc-feed.service over this socket: one-second steps and a large",
            "# delay, so GPS and the network win whenever they answer; and stratum 10, so a box keeping its time from",
            "# its module alone serves it as stratum 11, below any network or GPS source, never as a reference.",
            f"refclock SOCK {FEED_SOCK} refid RTC precision 1 delay 2 stratum 10"
            + (" prefer" if prefer == "module" else ""),
        ]
    if serve:
        lines += [
            "# The time served to the hotspot's guests (the hotspot firewall opens UDP 123 for it).",
            f"allow {net or hotspot_net()}",
        ]
    if gps:
        lines += [
            "# GPS through gpsd: its time from the receiver's sentences (shared memory 0),",
            "refclock SHM 0 refid GPS precision 1e-1 offset 0.0 delay 0.2" + (" prefer" if prefer == "gps" else ""),
        ]
        if (SYS / "dev/pps0").exists():
            lines += [
                "# and its pulse per second where the receiver gives one, locked to them.",
                "refclock PPS /dev/pps0 lock GPS refid PPS",
            ]
    return "\n".join(lines) + "\n"


def without_pool(text):
    """chrony.conf with its pool and server lines turned off (commented, so Restore is exact either way)."""
    return "".join(
        f"#irate-box off: {ln}" if re.match(r"^\s*(pool|server)\s", ln) else ln for ln in text.splitlines(True)
    )


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    safeio.write(path, text)


def _chrony_on(rec, pool, gps, module, serve, local, said, order=ORDER):
    img = rec["image"]
    if not _installed("chrony"):
        said.append(_install("chrony", FROM_GPS_KIT))
        rec["installed_chrony"] = True
        if img.get("timesyncd") and not _installed("systemd-timesyncd"):
            rec["removed_timesyncd"] = True
    if gps and not rec.get("installed_gps_kit"):
        from irate_box.root import kits

        if GPS_KIT not in kits.installed_state():
            if not kits.manifest(GPS_KIT):
                raise Refused(
                    M(
                        "clock-sources.gps-kit-missing",
                        "GPS needs the GPS time toolkit. Refresh its cache on Toolkits while the box "
                        "has internet access.",
                    )
                )
            kits.install(GPS_KIT, None, log=lambda *_: None)
            rec["installed_gps_kit"] = True
            said.append(M("clock-sources.gps-kit-installed", "The box installed the GPS time toolkit (gpsd)."))
    # The image's own pool and server lines off while chrony is ours: the pool, when chosen, is our own line, so it
    # can be the preferred one. Restore puts chrony.conf back as it came.
    if img.get("chrony_conf") is None and CHRONY_CONF.is_file():
        img["chrony_conf"] = CHRONY_CONF.read_text()
    if img.get("chrony_conf") is not None:
        want = without_pool(img["chrony_conf"])
        if not CHRONY_CONF.is_file() or CHRONY_CONF.read_text() != want:
            _write(CHRONY_CONF, want)
    prefer = first_chosen(order, pool, gps, local, module)
    _write(CHRONY_SOURCES, chrony_sources_text(local, pool, prefer))
    _write(CHRONY_D, chrony_conf_text(gps, serve, module=module, prefer=prefer))
    # A toolkit install leaves the units it brought disabled: chrony is wanted here.
    run("systemctl", "enable", "chrony.service")
    run("systemctl", "restart", "chrony.service", timeout=120)
    _feed(module)
    sources = _sources(pool, gps, local, module, order)
    said.append(
        M(
            "clock-sources.chrony-serves",
            "chrony now keeps the time from {sources}. It also serves the time to the hotspot ({net}).",
            sources=sources,
            net=hotspot_net(),
        )
        if serve
        else M("clock-sources.chrony", "chrony now keeps the time from {sources}.", sources=sources)
    )


def _chrony_off(rec, said):
    """Back from chrony to the image's own service: our files gone, chrony.conf as it was, the packages as they were."""
    img = rec.get("image") or {}
    _feed(False)
    for f in (CHRONY_SOURCES, CHRONY_D):
        f.unlink(missing_ok=True)
    if img.get("chrony_conf") is not None and CHRONY_CONF.is_file() and CHRONY_CONF.read_text() != img["chrony_conf"]:
        _write(CHRONY_CONF, img["chrony_conf"])
    if rec.pop("installed_gps_kit", False):
        from irate_box.root import kits

        try:
            kits.remove(GPS_KIT, log=lambda *_: None)
            said.append(M("clock-sources.gps-kit-removed", "The box removed the GPS time toolkit."))
        except ValueError:
            pass
    if rec.pop("installed_chrony", False):
        run("apt-get", "remove", "-y", "chrony", timeout=1200)
        said.append(M("clock-sources.chrony-removed", "The box removed chrony."))
    if rec.pop("removed_timesyncd", False):
        said.append(_install("systemd-timesyncd", FROM_SYSTEM_KIT))
    if img.get("daemon") == "chrony":
        run("systemctl", "restart", "chrony.service", timeout=120)


def _timesyncd(pool, local, said, order=ORDER):
    _write(TIMESYNCD_D, timesyncd_text(pool, local, order))
    run("systemctl", "enable", "systemd-timesyncd.service")
    run("systemctl", "restart", "systemd-timesyncd.service", timeout=120)
    said.append(
        M(
            "clock-sources.timesyncd",
            "systemd-timesyncd now gets the time from {sources}.",
            sources=_sources(pool, False, local, order=order),
        )
    )


def _sources(pool, gps, local, module=False, order=ORDER):
    """The sources chosen, as a list in the owner's order (parse lets no choice have none)."""
    named = {
        "gps": M("clock-sources.gps", "GPS") if gps else None,
        "module": M("clock-sources.module", "the hardware clock") if module else None,
        "local": M("clock-sources.local", "the server {host} on the local network", host=local) if local else None,
        "pool": M("clock-sources.pool", "the internet's pool") if pool else None,
    }
    return [named[s] for s in order if named[s]]


FIREWALL_OPENS = M("clock-sources.firewall-opens", "The hotspot firewall now opens UDP port 123 to guests.")
FIREWALL_CLOSES = M("clock-sources.firewall-closes", "The hotspot firewall closes UDP port 123 again.")


def apply(choice):
    """The page's choice applied; what was done, a sentence a step."""
    service, pool, gps, module, serve, local = parse(choice)
    order = parse_order(choice)
    rec = load()
    if "image" not in rec:
        rec["image"] = _image_as_it_was()
    said = []
    if service == "chrony":
        TIMESYNCD_D.unlink(missing_ok=True)
        _chrony_on(rec, pool, gps, module, serve, local, said, order)
    else:
        if rec.get("service") == "chrony" or rec.get("installed_chrony"):
            _chrony_off(rec, said)
        if not _installed("systemd-timesyncd"):
            said.append(_install("systemd-timesyncd", FROM_SYSTEM_KIT))
        _timesyncd(pool, local, said, order)
    was_serving = serving()
    rec.update(
        service=service, pool=pool, gps=gps, module=module, serve=serve, local=local, order=order, at=time.time()
    )
    _save(rec)
    if serve != was_serving and _firewall_refresh():
        said.append(FIREWALL_OPENS if serve else FIREWALL_CLOSES)
    return sentences(*said)


def undo():
    """Everything back as the image had it."""
    rec = load()
    if "image" not in rec:
        raise Refused(
            M(
                "clock-sources.undo.nothing",
                "The clock's sources were not changed from the Clock page. There is nothing to restore.",
            )
        )
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
        said.append(FIREWALL_CLOSES)
    said.append(M("clock-sources.restored", "The clock's sources are back as the image had them."))
    return sentences(*said)


def undo_all():
    if "image" not in load():
        return ["the clock's sources: not changed from the Clock page"]
    try:
        return [render(undo())]  # the uninstaller's terminal
    except ValueError as exc:
        return [f"the clock's sources: {exc}"]


# --- the clock module, fed to chrony ------------------------------------------------------------------------


def feed_unit_text():
    return (
        f"[Unit]\nDescription=Irate-Box: the clock module's time fed to chrony (the Clock page's choice)\n"
        f"After=chrony.service\nWants=chrony.service\n\n[Service]\nExecStart={CODE}/irate-box clock feed\n"
        f"Restart=always\nRestartSec=30\n\n[Install]\nWantedBy=multi-user.target\n"
    )


def _feed(on):
    """The feeding service on (written, enabled, started) or off (stopped, disabled, its unit gone)."""
    unit = UNIT_DIR / FEED_UNIT
    if on:
        _write(unit, feed_unit_text())
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", FEED_UNIT)
        run("systemctl", "restart", FEED_UNIT)
    elif unit.exists():
        run("systemctl", "disable", "--now", FEED_UNIT)
        unit.unlink(missing_ok=True)
        run("systemctl", "daemon-reload")


def sock_layout():
    """The byte layout of chrony's SOCK sample (refclock_sock.c: a struct timeval, a double offset, pulse, leap,
    padding, the magic), as this system's chronyd was built: time_t 64 bits on 64-bit systems, and on 32-bit ARM from
    Debian 13 on (its 64-bit time_t transition); 32 bits before. To be confirmed with a module on a board."""
    import struct

    if struct.calcsize("P") == 8:
        return "<qqdiiii"
    try:
        release = dict(ln.split("=", 1) for ln in (SYS / "etc/os-release").read_text().splitlines() if "=" in ln)
        if release.get("ID") == "debian" or "debian" in release.get("ID_LIKE", ""):
            if int(release.get("VERSION_ID", "0").strip('"').split(".")[0] or 0) >= 13:
                return "<qqdiiii"
    except (OSError, ValueError):
        pass
    return "<iidiiii"


def sample(now, offset, layout=None):
    """One SOCK sample: the time it was taken and the module's offset from the system clock."""
    import struct

    return struct.pack(
        layout or sock_layout(), int(now), int(round((now % 1) * 1e6)) % 1000000, float(offset), 0, 0, 0, SOCK_MAGIC
    )


def feed(every=FEED_EVERY, once=False):
    """The module read every `every` s and its offset sent to chronyd. The module counts whole seconds: read at an
    unknown point within one, it is on average half a second behind, which is added back."""
    from irate_box.root import rtc

    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    layout = sock_layout()
    while True:
        cfg = rtc.load_config()
        if cfg:
            try:
                epoch, _why, _notes = rtc.read_module(cfg)
            except Exception:  # noqa: BLE001 - a module that does not answer this time may next time
                epoch = None
            if epoch:
                now = time.time()
                try:
                    s.sendto(sample(now, epoch + 0.5 - now, layout), str(FEED_SOCK))
                except OSError:
                    pass  # chronyd not up yet: it makes the socket when it starts
        if once:
            return
        time.sleep(every)


# --- how each source stands -------------------------------------------------------------------------------


# chronyc's mark for a source, as what it is doing: * the one setting the time, + combined with it, - answering
# but left out, x disagreeing with the others, ~ too variable to use, ? not answering.
MARKS = {"*": "selected", "+": "combined", "-": "spare", "x": "disagrees", "~": "unsteady", "?": "silent"}


def parse_chronyc(text):
    """`chronyc -n sources` as [{mode: server|refclock|peer, state, mark, name, ago, offset}]; state: in-use (* or +),
    answering (- ~ x), not-answering (?); mark: MARKS; ago: seconds since its last sample (None if never); offset:
    its last sample's offset from the system clock, in seconds."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^([\^#=])([*+\-?x~])\s+(\S+)(?:\s+\d+\s+\d+\s+\d+\s+(\S+)\s+([+-]?[\d.]+)(ns|us|ms|s))?", line)
        if not m:
            continue
        mode = {"^": "server", "#": "refclock", "=": "peer"}[m.group(1)]
        state = "in-use" if m.group(2) in "*+" else "not-answering" if m.group(2) == "?" else "answering"
        row = {"mode": mode, "state": state, "mark": MARKS[m.group(2)], "name": m.group(3), "ago": None, "offset": None}
        if m.group(4) and m.group(4) != "-" and m.group(2) != "?":
            row["ago"] = _ago(m.group(4))
            row["offset"] = float(m.group(5)) * {"ns": 1e-9, "us": 1e-6, "ms": 1e-3, "s": 1.0}[m.group(6)]
        out.append(row)
    return out


def _ago(text):
    """chronyc's LastRx: seconds, or with m, h, d or y."""
    m = re.match(r"^(\d+)([mhdy]?)$", text)
    return int(m.group(1)) * {"": 1, "m": 60, "h": 3600, "d": 86400, "y": 31536000}[m.group(2)] if m else None


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


def _best_row(rows):
    """Of a source's lines in chronyc (a pool has several), the one that speaks for it: selected, combined, spare,
    disagreeing, unsteady, then silent."""
    rank = ["selected", "combined", "spare", "disagrees", "unsteady", "silent"]
    return min(rows, key=lambda r: rank.index(r["mark"])) if rows else None


def _with(entry, row):
    """A source's entry with its line's mark, last sample's age and offset (or none, when chronyc has no line)."""
    entry.update(mark=row["mark"], ago=row["ago"], offset=row["offset"]) if row else entry.update(mark=None)
    return entry


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
            refs = [r for r in rows if r["mode"] == "refclock" and r["name"] in ("GPS", "PPS")]  # not RTC: the module
            state = _best([r["state"] for r in refs]) if refs else "not-found"
            if state != "in-use" and not gps_device():
                state, refs = "not-found", []
            out.append(_with({"source": "gps", "name": "GPS", "state": state}, _best_row(refs)))
        if rec.get("module"):
            refs = [r for r in rows if r["mode"] == "refclock" and r["name"] == "RTC"]
            out.append(
                _with(
                    {
                        "source": "module",
                        "name": "the hardware clock",
                        "state": _best([r["state"] for r in refs]) if refs else "not-found",
                    },
                    _best_row(refs),
                )
            )
        if rec.get("local"):
            mine = [r for r in rows if r["mode"] == "server" and (r["name"] in local_ips or r["name"] == rec["local"])]
            out.append(
                _with(
                    {"source": "local", "name": rec["local"], "state": _best([r["state"] for r in mine])},
                    _best_row(mine),
                )
            )
        if rec.get("pool"):
            pool = [
                r
                for r in rows
                if r["mode"] == "server" and r["name"] not in local_ips and r["name"] != rec.get("local")
            ]
            out.append(
                _with(
                    {"source": "pool", "name": "the internet's pool", "state": _best([r["state"] for r in pool])},
                    _best_row(pool),
                )
            )
        order = rec.get("order") or ORDER
        out.sort(key=lambda e: order.index(e["source"]) if e["source"] in order else len(order))
    elif rec.get("service") == "image":
        # timesyncd says only the server it uses now.
        if timesync is None:
            timesync = dict(
                ln.split("=", 1)
                for ln in run(
                    "timedatectl", "show-timesync", "-p", "ServerName", "-p", "ServerAddress"
                ).stdout.splitlines()
                if "=" in ln
            )
        name = timesync.get("ServerName", "")
        if rec.get("local"):
            out.append(
                {
                    "source": "local",
                    "name": rec["local"],
                    "state": "in-use" if name == rec["local"] else "unknown",
                    "mark": "selected" if name == rec["local"] else "standby",
                }
            )
        if rec.get("pool"):
            out.append(
                {
                    "source": "pool",
                    "name": "the internet's pool",
                    "state": "in-use" if name in POOL else "unknown",
                    "mark": "selected" if name in POOL else "standby",
                }
            )  # the names given it (POOL)
        order = rec.get("order") or ORDER
        out.sort(key=lambda e: order.index(e["source"]))
    return out


def tracking(text=None):
    """What the box keeps its time from now, as it would serve it (`chronyc -n tracking`): {ref: GPS, RTC, PPS or a
    server's address, stratum, synced}. Not synchronised (no source answering, or chrony only starting): stratum 16, and
    chrony serves nothing a client accepts: there is no `local` line to make up a time. None without chrony."""
    if text is None:
        r = run("chronyc", "-n", "tracking")
        if r.returncode != 0:
            return None
        text = r.stdout
    ref = re.search(r"^Reference ID\s*:\s*\S+\s*\(([^)]*)\)", text, re.M)
    stratum = re.search(r"^Stratum\s*:\s*(\d+)", text, re.M)
    leap = re.search(r"^Leap status\s*:\s*(.+)$", text, re.M)
    if not stratum:
        return None
    synced = bool(leap) and leap.group(1).strip() != "Not synchronised" and int(stratum.group(1)) < 16
    return {"ref": ref.group(1) if ref else "", "stratum": int(stratum.group(1)), "synced": synced}


def status():
    rec = load()
    return {
        "service": rec.get("service"),
        "pool": rec.get("pool"),
        "gps": rec.get("gps"),
        "serve": bool(rec.get("serve")),
        "module": bool(rec.get("module")),
        "module_ready": module_ready(),
        "hardware": hardware(),
        "order": rec.get("order") or list(ORDER),
        "local": rec.get("local") or "",
        "changed": "image" in rec,
        "daemon": image_daemon(),
        "chrony_installed": _installed("chrony"),
        "gps_device": gps_device(),
        "sources": source_states(rec) if rec.get("service") else [],
        "tracking": tracking() if rec.get("service") == "chrony" or image_daemon() == "chrony" else None,
    }


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "status":
        print(json.dumps(status(), indent=1))
    elif cmd == "set" and len(argv) == 2:
        print(apply(argv[1]))
    elif cmd == "undo":
        print(undo())
    elif cmd == "feed":
        feed()
    elif cmd == "undo-all":
        for line in undo_all():
            print(line)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
