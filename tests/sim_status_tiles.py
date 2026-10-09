# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Status tiles, against a hub it starts: the box row arranged by the owner
(hidden, ordered, two cells wide), the arrangement checked, and /status carrying the different
devices seen today and this week only while counting is on. python3 tests/sim_status_tiles.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
work = Path(tempfile.mkdtemp(prefix="status-tiles-"))
counts = work / "counts.json"
counts.write_text(json.dumps({"day": 4, "week": 9, "date": "2026-10-08", "week_of": "2026-W41", "at": 1}))
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "f" * 64
env = dict(os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=str(work), HUB_ETC_DIR=str(work), PORT=str(port), HUB_BIND="127.0.0.1",
           HUB_VISITORS_COUNTS=str(counts))
hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
try:
    for _ in range(50):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status"); break
        except OSError:
            time.sleep(0.1)
    def go(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {"Host": f"127.0.0.1:{port}", "X-Irate-Front": SECRET, "X-Irate-Admin": "1", "Content-Type": "application/json",
             "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse(); return r.status, r.read().decode()
    _, body = go("GET", "/admin/status-tiles")
    ids = [t["id"] for t in json.loads(body)["tiles"]]
    check("the box row's tiles listed", {"box-people", "box-qr", "box-system", "box-services"} <= set(ids), ids)
    _, home = go("GET", "/")
    check("by default: the join QR on the page, People before Memory and disk", 'id="qr-card"' in home and home.find('id="people-card"') < home.find('id="system-card"'))
    st, _ = go("POST", "/admin/status-tiles", {"state": {"hidden": ["box-qr"], "order": ["box-system", "box-people"], "double": ["box-people"]}})
    _, home = go("GET", "/")
    check("hidden: the QR gone", st == 200 and 'id="qr-card"' not in home)
    check("ordered: Memory and disk first", home.find('id="system-card"') < home.find('id="people-card"'))
    check("an older page's double: People wide (two cells), marked so", 'data-size="wide" class="service-card wide people-card" id="people-card"' in home, home[home.find('people-card') - 80:home.find('people-card') + 40])
    # F5: three sizes, single, wide (2x1) and large (2x2), for the box row and the apps row alike.
    st, body = go("POST", "/admin/status-tiles", {"state": {"order": [], "hidden": [], "size": {"box-system": "large", "box-people": "wide"}}})
    _, home = go("GET", "/")
    check("large: Memory and disk two by two, marked so", st == 200 and 'data-size="large" class="service-card large system-card"' in home, home[home.find('system-card') - 80:home.find('system-card') + 30])
    check("the state says each size", json.loads(body)["state"]["size"] == {"box-system": "large", "box-people": "wide"}
          and {t["id"]: t["size"] for t in json.loads(body)["tiles"]}.get("box-qr") == "single", body[:300])
    st, body = go("POST", "/admin/tiles", {"state": {"order": [], "size": {"draw": "large", "about": "wide"}}})
    _, home = go("GET", "/")
    i = home.find('<span class="name">Excalidraw</span>')
    check("an app's tile large", st == 200 and 'class="service-card large" data-size="large"' in home[home.rfind("<a ", 0, i):i], home[home.rfind("<a ", 0, i):i])
    # A tile's own icon: emoji, or up to four letters and digits drawn as text.
    st, body = go("POST", "/admin/tiles", {"state": {"order": [], "size": {}, "icon": {"draw": "DRAW", "about": "🦀"}}})
    _, home = go("GET", "/")
    i = home.find('<span class="name">Excalidraw</span>')
    check("an icon of letters: drawn as text", st == 200 and '<span class="icon icon-text">DRAW</span>' in home[home.rfind("<a ", 0, i):i], home[home.rfind("<a ", 0, i):i])
    i = home.find('<span class="name">About</span>')
    check("an icon of emoji: in place of the manifest's", '<span class="icon">🦀</span>' in home[home.rfind("<a ", 0, i):i])
    check("/admin/tiles says each tile's own", {t["id"]: t["own_icon"] for t in json.loads(body)["tiles"]}.get("draw") == "DRAW")
    for bad in ({"icon": {"draw": "DRAWS"}}, {"icon": {"draw": "a b"}}, {"icon": {"draw": "<b>"}}, {"icon": {"draw": "x🦀"}}, {"icon": {"draw": ""}}, {"icon": {"nothing": "AB"}}):
        st, _ = go("POST", "/admin/tiles", {"state": bad})
        check(f"icon refused: {str(bad)[:40]}", st == 400, st)
    for bad in ({"size": {"draw": "huge"}}, {"size": {"nothing": "wide"}}, {"size": ["draw"]}):
        st, _ = go("POST", "/admin/tiles", {"state": bad})
        check(f"apps row refused: {str(bad)[:40]}", st == 400, st)
    for bad in ({"hidden": ["box-nothing"]}, {"double": "box-people"}, {"size": {"box-qr": "huge"}}, "x"):
        st, _ = go("POST", "/admin/status-tiles", {"state": bad})
        check(f"refused: {str(bad)[:40]}", st == 400, st)
    _, body = go("GET", "/status")
    check("/status: the counts, while counting is on", json.loads(body).get("visitors") == {"day": 4, "week": 9, "date": "2026-10-08"}, body[:300])
    go("POST", "/admin/settings", {"visitor_counts": False})
    _, body = go("GET", "/status")
    check("off: no counts, though a file is there", "visitors" not in json.loads(body))
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
