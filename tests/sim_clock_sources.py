# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The clock's sources (irate_box/root/clock.py), on a stand-in root folder with apt, dpkg and systemctl stood in:
the page's choice read and refused where it must be; the image's own service given its servers in a drop-in; chrony
in its place with its sources, the pool line of chrony.conf off and back; Restore putting all of it back, packages
included; chronyc's sources read into states; the doctor's finding per source; the hub and the doctor accepting the
same choices. python3 tests/sim_clock_sources.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="clock-src-"))
os.environ.update(
    HUB_ETC_DIR=str(T / "etc-hub"),
    HUB_CLOCK_SYSROOT=str(T / "root"),
    HUB_KITS_ROOT=str(T / "kits"),
    HUB_STATE_DIR=str(T / "state"),
)
(T / "etc-hub").mkdir()
sys.path.insert(0, str(REPO))
from irate_box import msg
from irate_box.root import clock

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# The stand-in system: which packages are installed and which units run, as apt and systemctl would change them.
DEBIAN_CONF = "# Debian's chrony.conf\npool 2.debian.pool.ntp.org iburst\nsourcedir /run/chrony-dhcp\nrtcsync\n"
pkgs = {"systemd-timesyncd"}
active = {"systemd-timesyncd.service"}
enabled = {"systemd-timesyncd.service"}
calls = []


class R:
    def __init__(self, rc=0, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


def fake_run(*cmd, timeout=120):
    cmd = list(cmd)
    calls.append(list(cmd))
    if cmd[0] == "dpkg-query":
        return R(0 if cmd[-1] in pkgs else 1, "install ok installed" if cmd[-1] in pkgs else "")
    if cmd[0] == "systemctl":
        verb, unit = cmd[1], cmd[-1]
        if verb == "is-active":
            return R(0, "active" if unit in active else "inactive")
        if verb == "is-enabled":
            return R(0, "enabled" if unit in enabled else "disabled")
        if verb == "enable":
            enabled.add(unit)
        if verb == "restart":
            active.add(unit)
        return R()
    if cmd[:3] == ["apt-get", "install", "-y"]:
        name = Path(cmd[3]).name.split("_")[0]
        pkgs.add(name)
        if name == "chrony":
            # Debian's packages conflict: chrony in, timesyncd out, chrony's own chrony.conf with it.
            pkgs.discard("systemd-timesyncd")
            active.discard("systemd-timesyncd.service")
            enabled.discard("systemd-timesyncd.service")
            conf = T / "root/etc/chrony/chrony.conf"
            conf.parent.mkdir(parents=True, exist_ok=True)
            if not conf.exists():
                conf.write_text(DEBIAN_CONF)
        return R()
    if cmd[:3] == ["apt-get", "remove", "-y"]:
        pkgs.discard(cmd[3])
        active.discard(f"{cmd[3]}.service")
        return R()
    return R()


clock.run = fake_run

# --- the page's choice --------------------------------------------------------------------------------------
check(
    "a choice read: service, pool, GPS, a server",
    clock.parse("clock-sources:chrony:1:1:0:0:192.168.1.1") == ("chrony", True, True, False, False, "192.168.1.1"),
)
check("  '-' for no server", clock.parse("clock-sources:image:1:0:0:0:-") == ("image", True, False, False, False, ""))
for bad, why in (
    ("clock-sources:image:1:1:0:0:-", "gps-needs-chrony"),
    ("clock-sources:chrony:0:0:0:0:-", "none"),
    ("clock-sources:chrony:1:0:0:0:a..b", "bad-host"),
    ("clock-sources:ntpd:1:0:0:0:-", "not-a-choice"),
    ("clock-sources:chrony:1:0:0:0:x;rm", "not-a-choice"),
    ("clock-sources:image:1:0:0:1:-", "serve-needs-chrony"),
    ("clock-sources:image:1:0:1:0:-", "module-needs-chrony"),
    ("clock-sources:chrony:1:0:1:0:-", "no-module"),
):
    try:
        clock.parse(bad)
        check(f"refused: {bad}", False)
    except ValueError as exc:
        check(f"refused: {bad} ({why})", exc.message["id"] == f"clock-sources.{why}", exc)

# --- the image's own service, its servers chosen ----------------------------------------------------------
said = clock.apply("clock-sources:image:0:0:0:0:192.168.1.1")
drop = T / "root/etc/systemd/timesyncd.conf.d/52irate-box.conf"
check(
    "the image's own: a drop-in naming the local server, no pool",
    drop.read_text().endswith("NTP=192.168.1.1\nFallbackNTP=\n")
    and msg.args_of(said, "clock-sources.local") == {"host": "192.168.1.1"}
    and "clock-sources.timesyncd" in msg.ids(said),
    (drop.read_text(), said),
)
rec = clock.load()
check(
    "  what the image had kept the first time (timesyncd, installed and enabled, no chrony)",
    rec["image"]["daemon"] == "systemd-timesyncd"
    and rec["image"]["timesyncd"]
    and rec["image"]["timesyncd_enabled"]
    and not rec["image"]["chrony"],
    rec["image"],
)
check("  timesyncd restarted to read it", ["systemctl", "restart", "systemd-timesyncd.service"] in calls)
clock.apply("clock-sources:image:1:0:0:0:-")
check(
    "  the pool: Debian's four servers, asked in turn",
    "NTP=0.debian.pool.ntp.org 1.debian.pool.ntp.org" in drop.read_text() and "FallbackNTP=\n" in drop.read_text(),
)
clock.apply("clock-sources:image:1:0:0:0:192.168.1.1:pool,local")
check(
    "  the owner's order: the pool first, then the local server",
    "NTP=0.debian.pool.ntp.org 1.debian.pool.ntp.org 2.debian.pool.ntp.org 3.debian.pool.ntp.org 192.168.1.1\n"
    in drop.read_text(),
    drop.read_text(),
)
clock.apply("clock-sources:image:1:0:0:0:192.168.1.1")
check(
    "  no order given: the local server first (the default order)",
    "NTP=192.168.1.1 0.debian.pool.ntp.org" in drop.read_text(),
    drop.read_text(),
)
check(
    "the order read from a choice: each source once, any left out after, in the default order",
    clock.parse_order("clock-sources:chrony:1:1:0:0:-:pool,gps,pool") == ["pool", "gps", "local", "module"]
    and clock.parse_order("clock-sources:chrony:1:1:0:0:-") == list(clock.ORDER),
)

# --- chrony in its place -----------------------------------------------------------------------------------
calls.clear()
said = clock.apply("clock-sources:chrony:0:0:0:0:192.168.1.1")
conf = T / "root/etc/chrony/chrony.conf"
src = T / "root/etc/chrony/sources.d/52irate-box.sources"
confd = T / "root/etc/chrony/conf.d/52irate-box.conf"
rec = clock.load()
check(
    "chrony: installed, timesyncd gone (they conflict), both recorded for Restore",
    "chrony" in pkgs
    and "systemd-timesyncd" not in pkgs
    and rec.get("installed_chrony")
    and rec.get("removed_timesyncd"),
    rec,
)
check("  the drop-in of timesyncd gone", not drop.exists())
check(
    "  the local server in our sources, preferred (the only one chosen)",
    src.read_text().endswith("server 192.168.1.1 iburst prefer\n"),
    src.read_text(),
)
check(
    "  the pool not chosen: Debian's pool line off (commented, so Restore is exact)",
    "#irate-box off: pool 2.debian.pool.ntp.org" in conf.read_text() and "\npool " not in conf.read_text(),
)
check(
    "  chrony enabled and restarted (a toolkit install would leave it disabled)",
    ["systemctl", "enable", "chrony.service"] in calls and ["systemctl", "restart", "chrony.service"] in calls,
)
check(
    "  said a step a sentence: installed, then what keeps the time",
    msg.ids(said)[1:3] == ["clock-sources.installed", "clock-sources.chrony"]
    and msg.args_of(said, "clock-sources.installed") == {"package": "chrony"}
    and msg.args_of(said, "clock-sources.local") == {"host": "192.168.1.1"},
    said,
)
clock.apply("clock-sources:chrony:1:0:0:0:-")
check(
    "  the pool chosen again: our own pool line (so it can be preferred), Debian's still off for Restore",
    "pool 2.debian.pool.ntp.org iburst prefer" in src.read_text()
    and "server" not in src.read_text().split("\n", 1)[1]
    and "#irate-box off: pool" in conf.read_text(),
    (src.read_text(), conf.read_text()),
)
clock.apply("clock-sources:chrony:1:0:0:0:192.168.1.1:pool,local")
check(
    "  the owner's order: the pool first, so the pool preferred and the local server not",
    "pool 2.debian.pool.ntp.org iburst prefer" in src.read_text() and "server 192.168.1.1 iburst\n" in src.read_text(),
    src.read_text(),
)
check(
    "  the image as it was is kept from the first change, not overwritten",
    clock.load()["image"]["daemon"] == "systemd-timesyncd",
)
check(
    "chrony's own lines: rtcsync; no refclock without GPS",
    "rtcsync" in confd.read_text() and "refclock" not in confd.read_text(),
)
check(
    "  with GPS: gpsd's shared memory, and PPS where there is a pulse device",
    "refclock SHM 0 refid GPS" in clock.chrony_conf_text(True) and "PPS" not in clock.chrony_conf_text(True),
)
(T / "root/dev").mkdir(parents=True, exist_ok=True)
(T / "root/dev/pps0").write_text("")
check("  …and its PPS locked to it", "refclock PPS /dev/pps0 lock GPS refid PPS" in clock.chrony_conf_text(True))

# GPS: the toolkit installed if it is cached; asked for its cache if not.
import types

fake_kits = types.SimpleNamespace(installed_state=lambda: {}, manifest=lambda k: None, install=None, remove=None)
sys.modules["irate_box.root.kits"] = fake_kits
import irate_box.root as root_pkg

root_pkg.kits = fake_kits
try:
    clock.apply("clock-sources:chrony:1:1:0:0:-")
    check("GPS with no toolkit cached: refused", False)
except ValueError as exc:
    check(
        "GPS with no toolkit cached: refused, saying where to get it",
        exc.message["id"] == "clock-sources.gps-kit-missing",
        exc,
    )
got = []
fake_kits.manifest = lambda k: {"id": k}
fake_kits.install = lambda k, hours, log=None: got.append(("install", k, hours))
fake_kits.remove = lambda k, log=None: got.append(("remove", k))
said = clock.apply("clock-sources:chrony:1:1:0:0:-")
check(
    "  cached: installed, kept (no removal time), said",
    got == [("install", "gpstime", None)] and "clock-sources.gps-kit-installed" in msg.ids(said),
    (got, said),
)

# --- serving the time to the hotspot: chrony's allow, and the hotspot firewall's UDP 123 ---------------------
check(
    "serving: chrony allows the hotspot's network",
    "allow 192.168.4.0/24" in clock.chrony_conf_text(False, True)
    and "allow" not in clock.chrony_conf_text(False, False),
)
refreshed = []
fake_security = types.SimpleNamespace(
    load_record=lambda: {"firewall": {"level": "apps", "services": ["mqtt"]}},
    _firewall_apply=lambda fw: refreshed.append(fw),
)
sys.modules["irate_box.root.security"] = fake_security
root_pkg.security = fake_security
said = clock.apply("clock-sources:chrony:1:1:0:1:-")
check(
    "  turned on: the allow written, the firewall loaded again, said",
    "allow 192.168.4.0/24" in confd.read_text()
    and len(refreshed) == 1
    and msg.args_of(said, "clock-sources.chrony-serves")["net"] == "192.168.4.0/24"
    and "clock-sources.firewall-opens" in msg.ids(said),
    (refreshed, said),
)
from irate_box.root import firewall

check(
    "  the firewall opens the time to guests while it serves (UDP 123), by the Clock page's switch",
    "time" in firewall.services_here([]) and 123 in firewall.ports(firewall.services_here([]))[1],
)
clock.apply("clock-sources:chrony:1:1:0:0:-")
check(
    "  turned off: the allow gone, the firewall loaded again, the time closed",
    "allow" not in confd.read_text()
    and len(refreshed) == 2
    and "time" not in firewall.services_here([])
    and 123 not in firewall.ports(firewall.services_here([]))[1],
)
clock.apply("clock-sources:chrony:1:1:0:1:-")
n = len(refreshed)

# --- the clock module, fed to chrony over its SOCK socket ------------------------------------------------------
os.environ["HUB_UNIT_DIR"] = str(T / "units")
clock.UNIT_DIR = T / "units"
clock.module_ready = lambda: True
calls.clear()
said = clock.apply("clock-sources:chrony:1:1:1:1:-")
unit = T / "units" / clock.FEED_UNIT
check(
    "the module: chrony's SOCK refclock (refid RTC, a large delay), the feed service written and started",
    f"refclock SOCK {clock.FEED_SOCK} refid RTC precision 1 delay 2" in confd.read_text()
    and unit.exists()
    and "irate-box clock feed" in unit.read_text()
    and ["systemctl", "enable", "--now", clock.FEED_UNIT] in calls
    and "clock-sources.module" in msg.ids(said),
    (said, unit.exists()),
)
import struct

smp = clock.sample(1792040000.25, -0.5, "<qqdiiii")
check(
    "a SOCK sample: 40 bytes with a 64-bit time_t, the time, the offset and chrony's magic last",
    len(smp) == 40 and struct.unpack("<qqdiiii", smp) == (1792040000, 250000, -0.5, 0, 0, 0, 0x534F434B),
)
check("  32 bytes with a 32-bit one", len(clock.sample(1792040000.25, -0.5, "<iidiiii")) == 32)
(T / "root/etc/os-release").write_text('ID=debian\nVERSION_ID="13"\n')
check(
    "  the layout: 64-bit time_t on Debian 13 (its 32-bit ARM moved to it) or a 64-bit system",
    clock.sock_layout() == "<qqdiiii",
)
(T / "root/etc/os-release").write_text('ID=debian\nVERSION_ID="12"\n')
check(
    "  …and 32-bit before, on a 32-bit system",
    clock.sock_layout() == ("<qqdiiii" if struct.calcsize("P") == 8 else "<iidiiii"),
)
sent = []


class FakeSock:
    def sendto(self, data, path):
        sent.append((data, path))


real_socket = clock.socket.socket
clock.socket.socket = lambda *a: FakeSock()
fake_rtc = types.SimpleNamespace(
    load_config=lambda: {"chip": "ds3231", "mode": "user"}, read_module=lambda cfg: (1792040000, None, [])
)
sys.modules["irate_box.root.rtc"] = fake_rtc
real_rtc = root_pkg.rtc
root_pkg.rtc = fake_rtc
clock.feed(once=True)
root_pkg.rtc = real_rtc
sys.modules["irate_box.root.rtc"] = real_rtc
clock.socket.socket = real_socket
check(
    "the feed: the module read once and its offset sent to chrony's socket (half a second added back)",
    len(sent) == 1
    and sent[0][1] == str(clock.FEED_SOCK)
    and abs(
        struct.unpack(clock.sock_layout(), sent[0][0])[2]
        - (
            1792040000.5
            - struct.unpack(clock.sock_layout(), sent[0][0])[0]
            - struct.unpack(clock.sock_layout(), sent[0][0])[1] / 1e6
        )
    )
    < 1e-3,
    sent,
)
st = {
    r["source"]: r["state"]
    for r in clock.source_states({"service": "chrony", "module": True}, "#? RTC  0 4 0 - +0ns[+0ns] +/- 0ns\n")
}
check("  its state from chronyc's RTC line (not GPS's)", st == {"module": "not-answering"}, st)
from irate_box.root import rtc as real_rtc_mod

real_rtc_mod.ntp_synced = lambda: True
real_rtc_mod.chrony_reference = lambda: "RTC"
check("rtc: the module's own time not written back to it as if it were network time", real_rtc_mod.trusted() is None)
real_rtc_mod.chrony_reference = lambda: "GPS"
check("  network or GPS time still is", real_rtc_mod.trusted() == "network time")
clock.apply("clock-sources:chrony:1:1:0:1:-")
check(
    "the module off again: the feed service stopped and its unit gone",
    not unit.exists() and ["systemctl", "disable", "--now", clock.FEED_UNIT] in calls,
)
clock.apply("clock-sources:chrony:1:1:0:1:-")

# --- Restore: everything back as the image had it ---------------------------------------------------------
calls.clear()
said = clock.undo()
check(
    "Restore: our files gone, chrony.conf as it came",
    not src.exists() and not confd.exists() and conf.read_text() == DEBIAN_CONF,
)
check("  the GPS toolkit removed (this page added it)", ("remove", "gpstime") in got)
check("  chrony removed and timesyncd installed again", "chrony" not in pkgs and "systemd-timesyncd" in pkgs, pkgs)
check(
    "  timesyncd enabled and running, as the image had it",
    "systemd-timesyncd.service" in enabled and ["systemctl", "restart", "systemd-timesyncd.service"] in calls,
)
check("  the record gone, and said", not clock.RECORD.exists() and "clock-sources.restored" in msg.ids(said), said)
check(
    "  serving stopped: the firewall loaded again, UDP 123 closed",
    len(refreshed) == n + 1
    and "clock-sources.firewall-closes" in msg.ids(said)
    and "time" not in firewall.services_here([]),
    said,
)
try:
    clock.undo()
    check("Restore again: refused", False)
except ValueError as exc:
    check("Restore again: refused, nothing to put back", exc.message["id"] == "clock-sources.undo.nothing")
check(
    "undo-all with nothing changed says so",
    clock.undo_all() == ["the clock's sources: not changed from the Clock page"],
)

# --- how each source stands ----------------------------------------------------------------------------------
CHRONYC = """MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#* GPS                           0   4   377    12   +1234us[+1300us] +/-  200ms
^- 192.168.1.1                   2   6   377    35   -2ms[  -2ms] +/-   30ms
^? 162.159.200.1                 0   6     0     -     +0ns[   +0ns] +/-    0ns
^? 193.1.2.3                     0   6     0     -     +0ns[   +0ns] +/-    0ns
"""
rows = clock.parse_chronyc(CHRONYC)
check(
    "chronyc read: the mode, the state and the name of each line",
    {k: rows[0][k] for k in ("mode", "state", "name", "mark", "ago")}
    == {"mode": "refclock", "state": "in-use", "name": "GPS", "mark": "selected", "ago": 12}
    and abs(rows[0]["offset"] - 0.001234) < 1e-9
    and rows[1]["state"] == "answering"
    and (rows[1]["mark"], rows[1]["ago"], rows[1]["offset"]) == ("spare", 35, -0.002)
    and (rows[2]["mark"], rows[2]["ago"], rows[2]["offset"]) == ("silent", None, None)
    and rows[2]["state"] == "not-answering"
    and len(rows) == 4,
    rows,
)
rec = {"service": "chrony", "pool": True, "gps": True, "local": "192.168.1.1"}
st = {r["source"]: r["state"] for r in clock.source_states(rec, CHRONYC)}
check(
    "each chosen source: GPS in use, the local server answering, the pool not answering",
    st == {"gps": "in-use", "local": "answering", "pool": "not-answering"},
    st,
)
st = {r["source"]: r["state"] for r in clock.source_states(rec, "MS Name/IP address\n")}
check(
    "  chosen, but nothing in chronyc: not found",
    st == {"gps": "not-found", "local": "not-found", "pool": "not-found"},
    st,
)
st = {
    r["source"]: r["state"]
    for r in clock.source_states(
        {"service": "image", "pool": True, "local": "192.168.1.1"}, timesync={"ServerName": "2.debian.pool.ntp.org"}
    )
}
check(
    "timesyncd: the pool in use, the local server not reported (it says only the one it uses)",
    st == {"pool": "in-use", "local": "unknown"},
    st,
)

# --- what the box serves, and from where -------------------------------------------------------------------------
check(
    "the hardware clock as chrony's source: stratum 10, so a box on it alone serves stratum 11, never as a reference",
    "refid RTC precision 1 delay 2 stratum 10" in clock.chrony_conf_text(False, module=True),
    clock.chrony_conf_text(False, module=True),
)
check(
    "  no `local` line: a box with no source answering serves nothing a device accepts",
    "local" not in clock.chrony_conf_text(True, True, "192.168.4.0/24", True),
)
TRACK = "Reference ID    : {} ({})\nStratum         : {}\nLeap status     : {}\n"
check(
    "tracking: a server's address, its stratum, synchronised",
    clock.tracking(TRACK.format("5A9B0C0D", "90.155.12.13", 2, "Normal"))
    == {"ref": "90.155.12.13", "stratum": 2, "synced": True},
)
check(
    "  the hardware clock alone: stratum 11",
    clock.tracking(TRACK.format("52544300", "RTC", 11, "Normal")) == {"ref": "RTC", "stratum": 11, "synced": True},
)
check(
    "  not synchronised: said so",
    clock.tracking(TRACK.format("00000000", "", 0, "Not synchronised"))["synced"] is False,
)

# --- the doctor's findings, and both sides accepting the choices ---------------------------------------------
from irate_box.root import health

clock.load = lambda: rec
clock.source_states = lambda r: [
    {"source": "gps", "name": "GPS", "state": "in-use"},
    {"source": "local", "name": "192.168.1.1", "state": "not-answering"},
    {"source": "pool", "name": "the internet's pool", "state": "not-found"},
]
fs = {f["id"]: f for f in health.check_sources()}
check(
    "the doctor: a finding per chosen source, on the Clock page (its id starts clock)",
    set(fs) == {"clock-source:gps", "clock-source:local", "clock-source:pool"},
)
check(
    "  in use fine; chosen but not answering a problem; chosen but not found a warning, each with what to do",
    fs["clock-source:gps"]["status"] == "ok"
    and fs["clock-source:local"]["status"] == "problem"
    and fs["clock-source:pool"]["status"] == "warn"
    and fs["clock-source:local"]["fix"]["id"] == "clock-source.local.fix"
    and fs["clock-source:gps"]["fix"] is None,
    fs,
)
from irate_box.hub import server

for c in ("clock-sources:chrony:1:1:1:1:192.168.1.1", "clock-sources:image:0:0:0:0:router.lan", "clock-undo"):
    check(
        f"accepted by the doctor and the hub: {c}",
        bool(health.CHOICE_RE.match(c)) and bool(server.HEALTH_CHOICE_RE.match(c)),
    )
for c in ("clock-sources:chrony:1:1:0:0:$(reboot)", "clock-sources:chrony:2:0:0:0:-", "clock-undo:x"):
    check(f"refused by both: {c}", not health.CHOICE_RE.match(c) and not server.HEALTH_CHOICE_RE.match(c))

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
