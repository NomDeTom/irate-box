# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Status tiles, against a hub it starts: the box row arranged by the owner
(hidden, ordered, two cells wide), the arrangement checked, and /status carrying the different
devices seen today and this week only while counting is on; who sees each tile (everyone, signed-in users or
administrators), and /status an allow-list that follows it, with no settings in it.
python3 tests/sim_status_tiles.py"""

import hashlib
import hmac
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from freeport import free_port

REPO = Path(__file__).resolve().parents[1]
work = Path(tempfile.mkdtemp(prefix="status-tiles-"))
counts = work / "counts.json"
counts.write_text(json.dumps({"day": 4, "week": 9, "date": "2026-10-08", "week_of": "2026-W41", "at": 1}))
port = free_port()
SECRET = "f" * 64
env = dict(
    os.environ,
    HUB_FRONT_SECRET=SECRET,
    HUB_STATE_DIR=str(work),
    HUB_ETC_DIR=str(work),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
    HUB_VISITORS_COUNTS=str(counts),
)
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


try:
    for _ in range(300):  # up to 30 s: a busy CI runner can take longer than 5 s to start the hub
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)

    def go(method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {
            **(headers or {}),
            "Host": f"127.0.0.1:{port}",
            "X-Irate-Front": SECRET,
            "X-Irate-Admin": "1",
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
            "Sec-Fetch-Site": "same-origin",
        }
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        return r.status, r.read().decode()

    _, body = go("GET", "/admin/status-tiles")
    ids = [t["id"] for t in json.loads(body)["tiles"]]
    check("the box row's tiles listed", {"box-people", "box-qr", "box-system", "box-services"} <= set(ids), ids)
    _, body = go("GET", "/admin/box")
    box = json.loads(body)
    check("Overview exposes bounded memory history", isinstance(box.get("memory_history", {}).get("samples"), list))
    check(
        "Overview reports current boot uptime separately from accumulated time",
        box["system"].get("boot_uptime", -1) >= 0 and box["uptime"] + 1 >= box["system"]["boot_uptime"],
        box["system"],
    )
    _, home = go("GET", "/")
    check(
        "by default: the join QR on the page, People before Memory and disk",
        'id="qr-card"' in home and home.find('id="people-card"') < home.find('id="system-card"'),
    )
    st, _ = go(
        "POST",
        "/admin/status-tiles",
        {"state": {"hidden": ["box-qr"], "order": ["box-system", "box-people"], "double": ["box-people"]}},
    )
    _, home = go("GET", "/")
    check("hidden: the QR gone", st == 200 and 'id="qr-card"' not in home)
    check("ordered: Memory and disk first", home.find('id="system-card"') < home.find('id="people-card"'))
    check(
        "an older page's double: People wide (two cells), marked so",
        'data-size="wide" class="service-card wide people-card" id="people-card"' in home,
        home[home.find("people-card") - 80 : home.find("people-card") + 40],
    )
    # Three sizes, single, wide (2x1) and large (2x2), for the box row and the apps row alike.
    st, body = go(
        "POST",
        "/admin/status-tiles",
        {"state": {"order": [], "hidden": [], "size": {"box-system": "large", "box-people": "wide"}}},
    )
    _, home = go("GET", "/")
    check(
        "large: Memory and disk two by two, marked so",
        st == 200 and 'data-size="large" class="service-card large system-card"' in home,
        home[home.find("system-card") - 80 : home.find("system-card") + 30],
    )
    check(
        "the state says each size",
        json.loads(body)["state"]["size"] == {"box-system": "large", "box-people": "wide"}
        and {t["id"]: t["size"] for t in json.loads(body)["tiles"]}.get("box-qr") == "single",
        body[:300],
    )
    st, body = go("POST", "/admin/tiles", {"state": {"order": [], "size": {"draw": "large", "about": "wide"}}})
    _, home = go("GET", "/")
    i = home.find('<span class="name">Excalidraw</span>')
    check(
        "an app's tile large",
        st == 200 and 'class="service-card large" data-size="large"' in home[home.rfind("<a ", 0, i) : i],
        home[home.rfind("<a ", 0, i) : i],
    )
    # A tile's own icon: emoji, or up to four letters and digits drawn as text.
    st, body = go("POST", "/admin/tiles", {"state": {"order": [], "size": {}, "icon": {"draw": "DRAW", "about": "🦀"}}})
    _, home = go("GET", "/")
    i = home.find('<span class="name">Excalidraw</span>')
    check(
        "an icon of letters: drawn as text",
        st == 200 and '<span class="icon icon-text">DRAW</span>' in home[home.rfind("<a ", 0, i) : i],
        home[home.rfind("<a ", 0, i) : i],
    )
    i = home.find('<span class="name">About</span>')
    check(
        "an icon of emoji: in place of the manifest's",
        '<span class="icon">🦀</span>' in home[home.rfind("<a ", 0, i) : i],
    )
    check(
        "/admin/tiles says each tile's own",
        {t["id"]: t["own_icon"] for t in json.loads(body)["tiles"]}.get("draw") == "DRAW",
    )
    for bad in (
        {"icon": {"draw": "DRAWS"}},
        {"icon": {"draw": "a b"}},
        {"icon": {"draw": "<b>"}},
        {"icon": {"draw": "x🦀"}},
        {"icon": {"draw": ""}},
        {"icon": {"nothing": "AB"}},
    ):
        st, _ = go("POST", "/admin/tiles", {"state": bad})
        check(f"icon refused: {str(bad)[:40]}", st == 400, st)
    for bad in ({"size": {"draw": "huge"}}, {"size": {"nothing": "wide"}}, {"size": ["draw"]}):
        st, _ = go("POST", "/admin/tiles", {"state": bad})
        check(f"apps row refused: {str(bad)[:40]}", st == 400, st)
    for bad in ({"hidden": ["box-nothing"]}, {"double": "box-people"}, {"size": {"box-qr": "huge"}}, "x"):
        st, _ = go("POST", "/admin/status-tiles", {"state": bad})
        check(f"refused: {str(bad)[:40]}", st == 400, st)
    _, body = go("GET", "/status")
    check(
        "/status: the counts, while counting is on",
        json.loads(body).get("visitors") == {"day": 4, "week": 9, "date": "2026-10-08"},
        body[:300],
    )
    go("POST", "/admin/settings", {"visitor_counts": False})
    _, body = go("GET", "/status")
    check("off: no counts, though a file is there", "visitors" not in json.loads(body))

    # Who sees a tile, and its part of /status.
    guest = json.loads(go("GET", "/status")[1])
    check(
        "/status to a guest, by default: the tiles' parts, and none of the settings",
        {"system", "uptime", "online", "signed_in", "services", "version", "proxied"} <= set(guest)
        and "settings" not in guest,
        sorted(guest),
    )
    box_names = {"Hub server", "Remote access (Tailscale)"}
    check("  the box's own services, with the Services tile", box_names <= {x["name"] for x in guest["services"]})
    st, body = go(
        "POST",
        "/admin/status-tiles",
        {
            "state": {
                "order": [],
                "hidden": ["box-services"],
                "size": {},
                "seen_by": {"box-system": "admin", "box-people": "users", "box-qr": "guests"},
            }
        },
    )
    check(
        "who sees each tile: kept, everyone not stored",
        st == 200 and json.loads(body)["state"]["seen_by"] == {"box-system": "admin", "box-people": "users"},
        body[:300],
    )
    guest = json.loads(go("GET", "/status")[1])
    check(
        "a guest: no memory and disk (administrators), no people (signed-in users)",
        not {"system", "uptime", "online", "signed_in", "visitors", "joined", "names"} & set(guest),
        sorted(guest),
    )
    check(
        "  no box services (the Services tile hidden), the apps' still there for the tiles",
        not box_names & {x["name"] for x in guest["services"]} and guest["services"],
        [x["name"] for x in guest["services"]],
    )
    _, home = go("GET", "/")
    check(
        "  and the tiles themselves gone from the page",
        'id="system-card"' not in home and 'id="people-card"' not in home,
    )
    # The administrator lately at /admin (the box's own login): everything.
    go("GET", "/", headers={"Cookie": "irate_admin_seen=9999999999.x"})  # makes the key, if not made yet
    key = (work / "admin-seen.key").read_bytes()
    until = str(int(time.time()) + 3600)
    seen = f"irate_admin_seen={until}.{hmac.new(key, until.encode(), hashlib.sha256).hexdigest()}"
    admin = json.loads(go("GET", "/status", headers={"Cookie": seen})[1])
    check(
        "the administrator: every part, the box services too",
        {"system", "online", "signed_in"} <= set(admin) and box_names <= {x["name"] for x in admin["services"]},
        sorted(admin),
    )
    for bad in ({"seen_by": {"box-system": "everyone"}}, {"seen_by": {"box-nothing": "admin"}}, {"seen_by": ["x"]}):
        st, _ = go("POST", "/admin/status-tiles", {"state": bad})
        check(f"refused: {str(bad)[:40]}", st == 400, st)
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
