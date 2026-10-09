# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""/admin's app pages (menu overhaul M4): /admin/apps behind the admin gate, each app with the
/admin sections its manifest owns, in the hub's order; a manifest's admin part checked; every
section a manifest names is one admin.html has. python3 tests/sim_admin_apps.py"""
import http.client, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import manifests  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

ms = manifests.load()
apps = manifests.admin_apps(ms)
by = {a["id"]: a for a in apps}
check("Kiwix owns Books", by.get("wiki", {}).get("sections") == ["books"], by.get("wiki"))
check("Git owns its repositories and mirrors", by.get("git", {}).get("sections") == ["git", "mirrors"], by.get("git"))
check("the Factory is named by its admin part", by.get("box-factory", {}).get("name") == "Firmware Factory", by.get("box-factory"))
check("the folders are marked", all(by[i]["folder"] for i in ("meshtastic", "tools-rf", "tools-general", "tools-electronics") if i in by))
check("box widgets without an admin part are left out", "box-people" not in by and "box-qr" not in by)
check("the shoutbox and the board: apps drawn on the hub page, each with its width (M9)",
      by.get("shoutbox", {}).get("page") is True and by.get("board", {}).get("width") == "board_width" and by["shoutbox"]["sections"] == ["shoutbox-settings", "shoutbox-mod"])
check("the drop's files on the drop's own page", by.get("drop", {}).get("sections") == ["drop-mod"])
check("in the hub's order", [a["id"] for a in apps] == [m["id"] for m in sorted(ms, key=lambda m: m["order"]) if m["id"] in by])
html = (REPO / "web" / "admin.html").read_text(encoding="utf-8")
ids = set(re.findall(r'<section class="admin-pane" id="([a-z0-9-]+)"', html))
named = [s for a in apps for s in a["sections"]]
check("every section a manifest names is in admin.html", set(named) <= ids, set(named) - ids)
check("no section owned twice", len(named) == len(set(named)), named)
for bad in ({"sections": "books"}, {"sections": ["Books!"]}, {"title": 3}):
    try:
        manifests._check({"id": "x", "order": 1, "admin": bad}, "test")
        check(f"a bad admin part refused: {bad}", False)
    except manifests.ManifestError:
        check(f"a bad admin part refused: {bad}", True)

state = tempfile.mkdtemp(prefix="admin-apps-")
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "f" * 64
env = dict(os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=state, HUB_ETC_DIR=state, PORT=str(port), HUB_BIND="127.0.0.1")
hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status"); break
        except OSError:
            time.sleep(0.1)
    def get(path, headers):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **headers}); r = c.getresponse(); return r.status, r.read()
    st, _ = get("/admin/apps", {})
    check("/admin/apps refused without the front's header", st == 403, st)
    st, body = get("/admin/apps", {"X-Irate-Front": SECRET})
    got = json.loads(body)["apps"] if st == 200 else []
    check("/admin/apps answers the admin page", [{k: v for k, v in x.items() if k not in ("switch", "seen")} for x in got] == apps, body[:200])
    seen = {x["id"]: x.get("seen") for x in got}
    # F7: the Firmware Factory's own page, under /admin's gate.
    st, page = get("/admin/factory.html", {"X-Irate-Front": SECRET})
    check("the Factory's own page served at /admin/factory.html", st == 200 and b"/factory.js" in page and b"factory-form" in page, st)
    st, _ = get("/admin/factory.html", {})
    check("  refused without the front's header, as /admin is", st == 403, st)
    st, page = get("/admin/mesh.html", {"X-Irate-Front": SECRET})
    check("Mesh's Heard served at /admin/mesh.html (item 9)", st == 200 and b"/mesh-heard.js" in page, st)
    st, _ = get("/admin/mesh.html", {})
    check("  refused without the front's header, as /admin is", st == 403, st)
    check("/admin/apps: who sees it alone for About and the folders, not a switched app (F3)", seen.get("about") is True and seen.get("tools-rf") is True and seen.get("draw") is False, seen)
    sw = {x["id"]: x["switch"] for x in got}
    check("  with whether each app's access can be set (M6)", sw.get("git") is True and sw.get("wiki") is True and sw.get("meshtastic") is False and sw.get("box-factory") is False, sw)
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
