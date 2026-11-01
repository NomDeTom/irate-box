# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A board's setup hotspot (root/rkspot.py), offline: a hotspot root's own scan does not show refused; a page that is
not the wizard refused before the administrator's WiFi password is sent; the wizard's own page checked, the choices
posted once, the temporary connection deleted and the box's own network restored; the uplink watchdog held for the
step and its earlier hold put back. python3 tests/sim_rkspot.py"""

import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import rksetup, uplink
from irate_box.msg import Refused
from irate_box.root import rkspot

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except Refused as exc:
        return exc.message["id"]
    return None


choices = {
    "root_password": "Root16charsAbcd1",
    "user": "salty-parrot",
    "user_password": "User16charsAbcd2",
    "real_name": "Salty Parrot",
    "locale": "en_GB.UTF-8",
    "timezone": "Europe/London",
    "ssid": "GOODNET",
    "wifi_password": "eight888",
    "country": "GB",
}
recorded = json.loads((REPO / "tests/fixtures/armbian-web-config-setup.json").read_text())
check(
    "the wizard's recorded request: every field this box posts has the same name, and none is added",
    set(rkspot._body(choices)) == set(recorded),
    (sorted(rkspot._body(choices)), sorted(recorded)),
)
spots = [{"ssid": "armbiansetup-luckfox-lyra-zero-w", "host": "luckfox-lyra-zero-w", "signal": 60, "boards": 1}]
rksetup.hotspots = lambda: spots
check(
    "a hotspot root's own scan does not show refused, before anything is joined",
    refused(rkspot.setup, "armbiansetup-other", choices) == "rkspot.not-in-range",
)

settings = {"hold_until": 0}
uplink.load_settings = lambda: dict(settings)
uplink.save_settings = lambda s: settings.update(s)
ran, gets, posts = [], [], []


def run(*cmd, timeout=90):
    ran.append(cmd)

    class Done:
        returncode = 0
        if cmd[:1] == ("nmcli",) and "show" in cmd:
            stdout = "GOODNET:802-11-wireless\n"
        elif cmd[:1] == ("nmcli",) and "status" in cmd:
            stdout = "wlan0:wifi:connected\nwlan1:wifi:disconnected\n"
        else:
            stdout = ""
        stderr = ""

    return Done()


rkspot.run = run
check("a free WiFi radio is used for the step when there is one", rkspot.free_radio() == "wlan1")
rkspot._get = lambda path: (gets.append(path), (200, "<html><form>wifiSsid rootPass userName</form></html>"))[1]
rkspot._post = lambda body: (posts.append(body), (200, "ok"))[1]
got = rkspot.setup(spots[0]["ssid"], choices)
check(
    "the wizard's page checked, then one post: the network, the accounts and the place, no keys",
    gets == ["/"]
    and len(posts) == 1
    and posts[0]["wifiSsid"] == "GOODNET"
    and posts[0]["wifiPass"] == "eight888"
    and posts[0]["rootPass"] == "Root16charsAbcd1"
    and posts[0]["userName"] == "salty-parrot"
    and posts[0]["locale"] == "en_GB.UTF-8"
    and posts[0]["rootKey"] == posts[0]["userKey"] == "",
    (gets, posts),
)
names = [c for c in ran if c[:2] == ("nmcli", "con")]
check(
    "the temporary connection made, joined and deleted, and the box's own network brought back",
    any(c[:3] == ("nmcli", "con", "delete") and c[-1] == rkspot.CONNECTION for c in names)
    and any(c[:3] == ("nmcli", "con", "add") for c in names)
    and any(c[:3] == ("nmcli", "con", "up") and c[-1] == rkspot.CONNECTION for c in names)
    and any(c[:3] == ("nmcli", "con", "up") and c[-1] == "GOODNET" for c in names),
    names,
)
check("the watchdog held for the step, and its earlier hold put back", settings.get("hold_until") == 0, settings)
settings["hold_until"] = 123
rkspot.setup(spots[0]["ssid"], choices)
check("  a hold that was there before the step is put back exactly", settings.get("hold_until") == 123, settings)
settings["hold_until"] = 0

gets.clear()
posts.clear()
rkspot._get = lambda path: (gets.append(path), (200, "<html><h1>Somebody else</h1></html>"))[1]
check(
    "a hotspot that is not the wizard refused, and the administrator's WiFi password never sent",
    refused(rkspot.setup, spots[0]["ssid"], choices) == "rkspot.not-wizard" and gets == ["/"] and posts == [],
    (gets, posts),
)
check(
    "  the temporary connection still deleted and the box's network brought back",
    any(c[:3] == ("nmcli", "con", "delete") for c in ran[-4:]) and any(c[-1] == "GOODNET" for c in ran[-4:]),
    ran[-4:],
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
