# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The clock's sources (irate_box/root/clock.py), on a stand-in root folder with apt, dpkg and systemctl stood in:
the page's choice read and refused where it must be; the image's own service given its servers in a drop-in; chrony
in its place with its sources, the pool line of chrony.conf off and back; Restore putting all of it back, packages
included; chronyc's sources read into states; the doctor's finding per source; the hub and the doctor accepting the
same choices. python3 tests/sim_clock_sources.py"""
import json, os, re, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="clock-src-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc-hub"), HUB_CLOCK_SYSROOT=str(T / "root"), HUB_KITS_ROOT=str(T / "kits"),
                  HUB_STATE_DIR=str(T / "state"))
(T / "etc-hub").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.root import clock  # noqa: E402

fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

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
            pkgs.discard("systemd-timesyncd"); active.discard("systemd-timesyncd.service"); enabled.discard("systemd-timesyncd.service")
            conf = T / "root/etc/chrony/chrony.conf"; conf.parent.mkdir(parents=True, exist_ok=True)
            if not conf.exists():
                conf.write_text(DEBIAN_CONF)
        return R()
    if cmd[:3] == ["apt-get", "remove", "-y"]:
        pkgs.discard(cmd[3]); active.discard(f"{cmd[3]}.service")
        return R()
    return R()
clock.run = fake_run

# --- the page's choice --------------------------------------------------------------------------------------
check("a choice read: service, pool, GPS, a server", clock.parse("clock-sources:chrony:1:1:0:192.168.1.1") == ("chrony", True, True, False, "192.168.1.1"))
check("  '-' for no server", clock.parse("clock-sources:image:1:0:0:-") == ("image", True, False, False, ""))
for bad, why in (("clock-sources:image:1:1:0:-", "GPS needs chrony"), ("clock-sources:chrony:0:0:0:-", "at least one source"),
                 ("clock-sources:chrony:1:0:0:a..b", "not an address"), ("clock-sources:ntpd:1:0:0:-", "a clock choice"),
                 ("clock-sources:chrony:1:0:0:x;rm", "a clock choice"), ("clock-sources:image:1:0:1:-", "Serving the time needs chrony")):
    try:
        clock.parse(bad); check(f"refused: {bad}", False)
    except ValueError as exc:
        check(f"refused: {bad} ({why})", why in str(exc), exc)

# --- the image's own service, its servers chosen ----------------------------------------------------------
said = clock.apply("clock-sources:image:0:0:0:192.168.1.1")
drop = T / "root/etc/systemd/timesyncd.conf.d/52irate-box.conf"
check("the image's own: a drop-in naming the local server, no pool", drop.read_text().endswith("NTP=192.168.1.1\nFallbackNTP=\n")
      and "the server 192.168.1.1" in said, (drop.read_text(), said))
rec = clock.load()
check("  what the image had kept the first time (timesyncd, installed and enabled, no chrony)", rec["image"]["daemon"] == "systemd-timesyncd"
      and rec["image"]["timesyncd"] and rec["image"]["timesyncd_enabled"] and not rec["image"]["chrony"], rec["image"])
check("  timesyncd restarted to read it", ["systemctl", "restart", "systemd-timesyncd.service"] in calls)
clock.apply("clock-sources:image:1:0:0:-")
check("  the pool: Debian's four servers as the fallback", "FallbackNTP=0.debian.pool.ntp.org" in drop.read_text() and "NTP=\n" in drop.read_text())

# --- chrony in its place -----------------------------------------------------------------------------------
calls.clear()
said = clock.apply("clock-sources:chrony:0:0:0:192.168.1.1")
conf = T / "root/etc/chrony/chrony.conf"
src = T / "root/etc/chrony/sources.d/52irate-box.sources"
confd = T / "root/etc/chrony/conf.d/52irate-box.conf"
rec = clock.load()
check("chrony: installed, timesyncd gone (they conflict), both recorded for Restore", "chrony" in pkgs and "systemd-timesyncd" not in pkgs
      and rec.get("installed_chrony") and rec.get("removed_timesyncd"), rec)
check("  the drop-in of timesyncd gone", not drop.exists())
check("  the local server in our sources", src.read_text().endswith("server 192.168.1.1 iburst\n"))
check("  the pool not chosen: Debian's pool line off (commented, so Restore is exact)", "#irate-box off: pool 2.debian.pool.ntp.org" in conf.read_text()
      and "\npool " not in conf.read_text())
check("  chrony enabled and restarted (a toolkit install would leave it disabled)", ["systemctl", "enable", "chrony.service"] in calls
      and ["systemctl", "restart", "chrony.service"] in calls)
check("  said in a sentence", said.startswith("Chrony installed.") and "the server 192.168.1.1" in said, said)
clock.apply("clock-sources:chrony:1:0:0:-")
check("  the pool chosen again: chrony.conf as it came", conf.read_text() == DEBIAN_CONF and "server" not in src.read_text().split("\n", 1)[1])
check("  the image as it was is kept from the first change, not overwritten", clock.load()["image"]["daemon"] == "systemd-timesyncd")
check("chrony's own lines: rtcsync; no refclock without GPS", "rtcsync" in confd.read_text() and "refclock" not in confd.read_text())
check("  with GPS: gpsd's shared memory, and PPS where there is a pulse device", "refclock SHM 0 refid GPS" in clock.chrony_conf_text(True)
      and "PPS" not in clock.chrony_conf_text(True))
(T / "root/dev").mkdir(parents=True, exist_ok=True); (T / "root/dev/pps0").write_text("")
check("  …and its PPS locked to it", "refclock PPS /dev/pps0 lock GPS refid PPS" in clock.chrony_conf_text(True))

# GPS: the toolkit installed if it is cached; asked for its cache if not.
import types
fake_kits = types.SimpleNamespace(installed_state=lambda: {}, manifest=lambda k: None, install=None, remove=None)
sys.modules["irate_box.root.kits"] = fake_kits
import irate_box.root as root_pkg
root_pkg.kits = fake_kits
try:
    clock.apply("clock-sources:chrony:1:1:0:-"); check("GPS with no toolkit cached: refused", False)
except ValueError as exc:
    check("GPS with no toolkit cached: refused, saying where to get it", "GPS time toolkit" in str(exc), exc)
got = []
fake_kits.manifest = lambda k: {"id": k}
fake_kits.install = lambda k, hours, log=None: got.append(("install", k, hours))
fake_kits.remove = lambda k, log=None: got.append(("remove", k))
said = clock.apply("clock-sources:chrony:1:1:0:-")
check("  cached: installed, kept (no removal time), said", got == [("install", "gpstime", None)] and "GPS time toolkit installed" in said, (got, said))

# --- serving the time to the hotspot: chrony's allow, and the hotspot firewall's UDP 123 ---------------------
check("serving: chrony allows the hotspot's network", "allow 192.168.4.0/24" in clock.chrony_conf_text(False, True)
      and "allow" not in clock.chrony_conf_text(False, False))
refreshed = []
fake_security = types.SimpleNamespace(load_record=lambda: {"firewall": {"level": "apps", "services": ["mqtt"]}},
                                      _firewall_apply=lambda fw: refreshed.append(fw))
sys.modules["irate_box.root.security"] = fake_security
root_pkg.security = fake_security
said = clock.apply("clock-sources:chrony:1:1:1:-")
check("  turned on: the allow written, the firewall loaded again, said", "allow 192.168.4.0/24" in confd.read_text() and len(refreshed) == 1
      and "serves it to the hotspot (192.168.4.0/24)" in said and "opens UDP 123 to guests" in said, (refreshed, said))
from irate_box.root import firewall  # noqa: E402
check("  the firewall opens the time to guests while it serves (UDP 123), by the Clock page's switch", "time" in firewall.services_here([])
      and 123 in firewall.ports(firewall.services_here([]))[1])
clock.apply("clock-sources:chrony:1:1:0:-")
check("  turned off: the allow gone, the firewall loaded again, the time closed", "allow" not in confd.read_text() and len(refreshed) == 2
      and "time" not in firewall.services_here([]) and 123 not in firewall.ports(firewall.services_here([]))[1])
clock.apply("clock-sources:chrony:1:1:1:-")
n = len(refreshed)

# --- Restore: everything back as the image had it ---------------------------------------------------------
calls.clear()
said = clock.undo()
check("Restore: our files gone, chrony.conf as it came", not src.exists() and not confd.exists() and conf.read_text() == DEBIAN_CONF)
check("  the GPS toolkit removed (this page added it)", ("remove", "gpstime") in got)
check("  chrony removed and timesyncd installed again", "chrony" not in pkgs and "systemd-timesyncd" in pkgs, pkgs)
check("  timesyncd enabled and running, as the image had it", "systemd-timesyncd.service" in enabled
      and ["systemctl", "restart", "systemd-timesyncd.service"] in calls)
check("  the record gone, and said", not clock.RECORD.exists() and "as the image had them" in said, said)
check("  serving stopped: the firewall loaded again, UDP 123 closed", len(refreshed) == n + 1 and "closes UDP 123 again" in said
      and "time" not in firewall.services_here([]), said)
try:
    clock.undo(); check("Restore again: refused", False)
except ValueError as exc:
    check("Restore again: refused, nothing to put back", "not changed" in str(exc))
check("undo-all with nothing changed says so", clock.undo_all() == ["the clock's sources: not changed from the Clock page"])

# --- how each source stands ----------------------------------------------------------------------------------
CHRONYC = """MS Name/IP address         Stratum Poll Reach LastRx Last sample
===============================================================================
#* GPS                           0   4   377    12   +1234us[+1300us] +/-  200ms
^- 192.168.1.1                   2   6   377    35   -2ms[  -2ms] +/-   30ms
^? 162.159.200.1                 0   6     0     -     +0ns[   +0ns] +/-    0ns
^? 193.1.2.3                     0   6     0     -     +0ns[   +0ns] +/-    0ns
"""
rows = clock.parse_chronyc(CHRONYC)
check("chronyc read: the mode, the state and the name of each line", rows[0] == {"mode": "refclock", "state": "in-use", "name": "GPS"}
      and rows[1]["state"] == "answering" and rows[2]["state"] == "not-answering" and len(rows) == 4, rows)
rec = {"service": "chrony", "pool": True, "gps": True, "local": "192.168.1.1"}
st = {r["source"]: r["state"] for r in clock.source_states(rec, CHRONYC)}
check("each chosen source: GPS in use, the local server answering, the pool not answering", st == {"gps": "in-use", "local": "answering",
      "pool": "not-answering"}, st)
st = {r["source"]: r["state"] for r in clock.source_states(rec, "MS Name/IP address\n")}
check("  chosen, but nothing in chronyc: not found", st == {"gps": "not-found", "local": "not-found", "pool": "not-found"}, st)
st = {r["source"]: r["state"] for r in clock.source_states({"service": "image", "pool": True, "local": "192.168.1.1"}, timesync={"ServerName": "2.debian.pool.ntp.org"})}
check("timesyncd: the pool in use, the local server not reported (it says only the one it uses)", st == {"pool": "in-use", "local": "unknown"}, st)

# --- the doctor's findings, and both sides accepting the choices ---------------------------------------------
from irate_box.root import health  # noqa: E402
clock.load = lambda: rec
clock.source_states = lambda r: [{"source": "gps", "name": "GPS", "state": "in-use"}, {"source": "local", "name": "192.168.1.1", "state": "not-answering"},
                                 {"source": "pool", "name": "the internet's pool", "state": "not-found"}]
fs = {f["id"]: f for f in health.check_sources()}
check("the doctor: a finding per chosen source, on the Clock page (its id starts clock)", set(fs) == {"clock-source:gps", "clock-source:local", "clock-source:pool"})
check("  in use fine; chosen but not answering a problem; chosen but not found a warning, each with what to do",
      fs["clock-source:gps"]["status"] == "ok" and fs["clock-source:local"]["status"] == "problem" and fs["clock-source:pool"]["status"] == "warn"
      and "UDP port 123" in fs["clock-source:local"]["fix"] and fs["clock-source:gps"]["fix"] == "", fs)
from irate_box.hub import server  # noqa: E402
for c in ("clock-sources:chrony:1:1:1:192.168.1.1", "clock-sources:image:0:0:0:router.lan", "clock-undo"):
    check(f"accepted by the doctor and the hub: {c}", bool(health.CHOICE_RE.match(c)) and bool(server.HEALTH_CHOICE_RE.match(c)))
for c in ("clock-sources:chrony:1:1:0:$(reboot)", "clock-sources:chrony:2:0:0:-", "clock-undo:x"):
    check(f"refused by both: {c}", not health.CHOICE_RE.match(c) and not server.HEALTH_CHOICE_RE.match(c))

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
