# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Services' throwaway paths kept in RAM (root/servicewrites.py, the manifests' writes block, Box doctor's repairs),
offline: the drop-in for a unit (a RuntimeDirectory of its own bound over each path), written once, rewritten only
when it changes, removed when nothing declares it; the unit restarted only when running; the owner's choices kept
in service-ram.json, a path under /run, with .. or relative refused; the manifests' writes block checked; Box
doctor's Keep in RAM for a heavy writer's path, and its undo. python3 tests/sim_servicewrites.py"""

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="servicewrites-"))
(T / "etc").mkdir()
(T / "units").mkdir()
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_SYSTEMD_UNITS=str(T / "units"), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.hub import manifests
from irate_box.msg import Refused, render
from irate_box.root import servicewrites as sw

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


ran = []
active = {"ews-update.service": "inactive", "room.service": "active"}


def run(*cmd, timeout=120):
    ran.append(cmd)
    out = ""
    if cmd[:2] == ("systemctl", "is-active"):
        out = active.get(cmd[2], "inactive") + "\n"
    if cmd[:2] == ("systemctl", "show") and "BindPaths" in cmd:
        out = "/run/irate-box-ram/ews-update/0:/opt/ews/app/data/cache\n" if cmd[2] == "ews-update.service" else ""
    return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()


sw.run = run
room = {
    "id": "room",
    "status": {"unit": "room.service"},
    "writes": {"ram": ["/var/cache/room"], "card": ["/var/lib/room"]},
}

text = sw.dropin_text("ews-update.service", ["/opt/ews/app/data/cache", "/opt/ews/web/data"])
check(
    "the drop-in: a RuntimeDirectory of the unit's own, kept between runs, bound over each path",
    "RuntimeDirectory=irate-box-ram/ews-update/0 irate-box-ram/ews-update/1" in text
    and "RuntimeDirectoryPreserve=yes" in text
    and "BindPaths=/run/irate-box-ram/ews-update/0:/opt/ews/app/data/cache" in text
    and "BindPaths=/run/irate-box-ram/ews-update/1:/opt/ews/web/data" in text,
    text,
)
ran.clear()
done = sw.apply([room])
dropin = T / "units" / "room.service.d" / sw.DROPIN
check(
    "a manifest's ram path: its unit's drop-in written, systemd reloaded, the running unit restarted",
    done == [("room.service", "set")]
    and dropin.exists()
    and ("systemctl", "daemon-reload") in ran
    and ("systemctl", "try-restart", "room.service") in ran,
    (done, ran),
)
ran.clear()
check("  applied again unchanged: nothing written, nothing restarted", sw.apply([room]) == [] and not ran, ran)

said = sw.set_owner("ews-update.service", "/opt/ews/app/data/cache", on=True)
check(
    "the owner's choice for a service with no manifest: kept, applied, said",
    json.loads(sw.OWNER.read_text()) == {"ews-update.service": ["/opt/ews/app/data/cache"]}
    and (T / "units" / "ews-update.service.d" / sw.DROPIN).exists()
    and said["id"] == "service-ram.on"
    and ("systemctl", "try-restart", "ews-update.service") not in ran,  # not running: not started
    (said, ran),
)
check(
    "  in effect when the unit's own BindPaths show it", sw.in_effect("ews-update.service", ["/opt/ews/app/data/cache"])
)
for bad in ("/run/ews", "/opt/../etc", "relative/path", "/", "/opt//x", "/proc/x"):
    try:
        sw.set_owner("ews-update.service", bad)
        check(f"  {bad} refused", False)
    except Refused:
        check(f"  {bad} refused", True)
sw.set_owner("ews-update.service", "/opt/ews/app/data/cache", on=False)
sw.apply([])
check(
    "back on the card: its drop-in removed (nothing declares it), the owner's file without it",
    not (T / "units" / "ews-update.service.d" / sw.DROPIN).exists()
    and not dropin.exists()
    and json.loads(sw.OWNER.read_text()) == {},
)

# The manifests' writes block.
base = {"id": "room", "order": 90, "status": {"path": None, "name": "Room", "port": 3002, "unit": "room.service"}}
manifests._check(dict(base, writes={"ram": ["/var/cache/room"], "card": ["/var/lib/room"]}), "ok.json")
check("a manifest's writes block: ram and card paths of its unit's", True)
for bad, why in (
    ({"ram": ["relative"]}, "a relative path"),
    ({"ram": ["/run/room"]}, "a path under /run"),
    ({"ram": ["/var/../etc"]}, "a path with .."),
    ({"other": []}, "a key it does not know"),
):
    try:
        manifests._check(dict(base, writes=bad), "bad.json")
        check(f"  {why}: refused", False)
    except manifests.ManifestError:
        check(f"  {why}: refused", True)
try:
    manifests._check(
        {**{k: v for k, v in base.items() if k != "status"}, "writes": {"ram": ["/var/cache/x"]}}, "nounit.json"
    )
    check("  without status.unit: refused", False)
except manifests.ManifestError:
    check("  without status.unit: refused", True)

# Box doctor's repairs: Keep in RAM for a heavy writer's path (recomputed from its unit, never taken from the page),
# and its undo.
from irate_box.root import health

health._unit_writes = lambda unit, card: (True, ["/opt/ews/app/data", "/opt/ews/web/data"])
health.cardhistory_card = None
said = health.fix("card-ram:ews-update.service:1")
check(
    "Keep in RAM: the unit's second card path, as its unit says now",
    json.loads(sw.OWNER.read_text()) == {"ews-update.service": ["/opt/ews/web/data"]} and "is in RAM" in render(said),
    sw.OWNER.read_text(),
)
said = health.fix("card-ram-off:ews-update.service:0")
check("  and back on the card", json.loads(sw.OWNER.read_text()) == {} and "on the SD card again" in render(said))
try:
    health.fix("card-ram:ews-update.service:7")
    check("  a path the unit no longer has: refused", False)
except Refused:
    check("  a path the unit no longer has: refused", True)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
