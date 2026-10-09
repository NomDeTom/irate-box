# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A service declared in its manifest (the network block, hub/services.py) reaches the firewall's floor, the
Security page, the security doctor's cross-reference and the box doctor with no code of its own: a made-up
service (a TAK server: TCP 8087, UDP 6969) in a manifests folder of the test's own. And a service nobody
declared: the box doctor's options for it. python3 tests/sim_declared_services.py"""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="declared-"))
APPS = T / "apps.d"
shutil.copytree(REPO / "apps.d", APPS)
TAK = {"id": "tak", "order": 66,
       "tile": {"icon": "🗺️", "name": "TAK server", "desc": "Maps and positions for TAK apps", "href": "/tak.html"},
       "status": {"path": "/tak.html", "name": "TAK server", "port": 8443, "unit": "takserver.service"},
       "network": {"listen": [{"proto": "tcp", "port": 8087}, {"proto": "udp", "port": 6969}],
                   "present": "/etc/takserver/irate-box.conf", "hotspot": "open", "risk": "warn",
                   "says": "TAK apps send their positions here; anyone on the network can, with no login."}}
(APPS / "66-tak.json").write_text(json.dumps(TAK))
os.environ.update(HUB_APPS_D=str(APPS), HUB_PRESENT_ROOT=str(T / "present"), HUB_STATE_DIR=str(T / "state"),
                  HUB_ETC_DIR=str(T / "etc"), HUB_CONTROL_DIR=str(T / "control"))
for d in ("state", "etc", "control"):
    (T / d).mkdir()
sys.path.insert(0, str(REPO))
from irate_box.hub import manifests, services  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

# --- the manifest ------------------------------------------------------------------------------
check("the made-up service's manifest is accepted", any(m["id"] == "tak" for m in manifests.load()))
for bad, why in ((dict(TAK["network"], listen=[]), "no ports"), (dict(TAK["network"], listen=[{"proto": "sctp", "port": 1}]), "a proto"),
                 (dict(TAK["network"], listen=[{"proto": "tcp", "port": 70000}]), "a port out of range"),
                 (dict(TAK["network"], present="etc/x"), "a relative present path"), (dict(TAK["network"], present="/etc/../x"), "a .. in it"),
                 (dict(TAK["network"], hotspot="maybe"), "hotspot"), (dict(TAK["network"], risk="high"), "risk"),
                 (dict(TAK["network"], says="x" * 400), "says too long")):
    try:
        manifests._check(dict(TAK, network=bad), "66-tak.json"); check(f"network refused: {why}", False)
    except manifests.ManifestError:
        check(f"network refused: {why}", True)
try:
    manifests._check(dict(TAK, status={"path": "/tak.html", "name": "TAK server", "port": 8443}), "66-tak.json")
    check("network without status.unit: refused", False)
except manifests.ManifestError:
    check("network without status.unit: refused", True)
tak = services.by_id("tak")
check("declared: name, unit, ports, hotspot, risk, words", tak and tak["unit"] == "takserver.service" and tak["listen"] == [("tcp", 8087), ("udp", 6969)]
      and tak["hotspot"] == "open" and tak["risk"] == "warn", tak)
check("  found by port and by unit", services.by_port("udp", 6969)["id"] == "tak" and services.by_unit("takserver.service")["id"] == "tak")
check("  not installed until its present file is there", not services.installed(tak))
(T / "present/etc/takserver").mkdir(parents=True); (T / "present/etc/takserver/irate-box.conf").write_text("x")
check("  installed once it is", services.installed(tak))

# --- the firewall and the Security page ----------------------------------------------------------
from irate_box.root import firewall, security, secdoctor_xref as xref  # noqa: E402
check("the floor: its ports by its id", firewall.service_ports("tak") == ((8087,), (6969,)))
check("  where installed, among the services a floor opens", "tak" in firewall.services_here(["tak"]) and "tak" in security.floor_default())
tcp, udp = firewall.ports(["tak"], "apps")
check("  in the floor's ports", 8087 in tcp and 6969 in udp, (tcp, udp))
lf = security.listener_findings([{"proto": "tcp", "port": 8087, "addr": "0.0.0.0", "unit": "takserver.service", "pid": 7,
                                  "process": "java", "name": "TAK server"}], {})[0]
check("the Security page: its listener rated and said as its manifest says, no stop offered (an add-on)", lf["status"] == "warn"
      and "TAK apps send their positions here" in lf["detail"] and not lf.get("actions"), lf)

# --- the security doctor's cross-reference --------------------------------------------------------
check("the doctor's add-on finding and the page's port: the same service", xref.about("doctor", "addon-tak") == {"kind": "service", "key": "tcp/8087"}
      and xref.about("security-page", "page-port-tcp-8087") == {"kind": "service", "key": "tcp/8087"})
do = xref.do_for("security-page", "page-port-udp-6969")
check("  put right on Add-ons, in its own words", do and do["go"] == "addons" and "TAK apps send their positions here" in do["say"], do)

# --- the box doctor ----------------------------------------------------------------------------------
from irate_box.root import health  # noqa: E402
check("the box doctor: a declared service's unit is the hub's to restart", health.ours("takserver.service") and not health.ours("homeassistant.service"))
(T / "etc" / "install-options").write_text("--with-tak\n")
calls = []
def fake(*cmd, timeout=60, **kw):
    calls.append(cmd)
    if cmd[:2] == ("systemctl", "--failed"):
        return subprocess.CompletedProcess(cmd, 0, "homeassistant.service loaded failed failed Home Assistant\nirate-box.service loaded failed failed hub\n", "")
    if cmd[:2] == ("systemctl", "show"):
        return subprocess.CompletedProcess(cmd, 0, "LoadState=loaded\nActiveState=active\nSubState=running\nResult=success\nUnitFileState=enabled\n", "")
    return subprocess.CompletedProcess(cmd, 0, "", "")
health.run = fake
TAK["addon"] = {"option": "--with-tak", "title": "TAK", "summary": "x"}
out = health.check_units()
other = [f for f in out if f["id"].startswith("other-failed:")]
check("  a failed service of the owner's: its own finding, why, and Start it again (asked first)",
      [f["id"] for f in other] == ["other-failed:homeassistant.service"] and "journalctl -u homeassistant.service" in other[0]["fix"]
      and other[0]["actions"][0]["choice"] == "other-restart:homeassistant.service" and other[0]["actions"][0].get("confirm"), other)
check("  the hub's own failed unit is not among them", not any("irate-box.service" in f["id"] for f in other))
calls.clear()
said = health.fix("other-restart:homeassistant.service")
check("  Start it again: its failed mark cleared, then started", ("systemctl", "reset-failed", "homeassistant.service") in calls
      and ("systemctl", "start", "homeassistant.service") in calls and "started" in said, (calls, said))
for bad in ("other-restart:sshd.service", "other-restart:irate-box.service", "other-restart:x; reboot"):
    try:
        health.fix(bad); check(f"{bad}: refused", False)
    except ValueError:
        check(f"{bad}: refused", True)

# --- the manifest format a manifest was written for ("api") ------------------------------------------------
cat = json.loads(sorted((REPO / "addons").glob("*.json"))[0].read_text())
manifests.check_local(dict(cat, api=manifests.API))
check("a catalogue add-on at this hub's format: accepted, and with api left out (1)", True)
for api, why in ((manifests.API + 1, "update the hub"), (manifests.API_OLDEST - 1, "update the add-on"), ("1", "an integer")):
    try:
        manifests.check_local(dict(cat, api=api)); check(f"api {api!r}: refused", False)
    except manifests.ManifestError as exc:
        check(f"api {api!r}: refused, saying what to do ({why})", why in str(exc), str(exc))
local = T / "state" / "apps.d"; local.mkdir(parents=True)
(local / f"{cat['id']}.json").write_text(json.dumps(dict(cat, api=manifests.API + 1)))
got, errors = manifests.load_local(local)
check("  in the local folder: listed with the reason, not loaded, nothing else stopped", not got and "update the hub" in errors.get(f"{cat['id']}.json", ""), errors)
shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
