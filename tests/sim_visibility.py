"""Who sees an app's tile, apart from who opens it (menu overhaul M5; checklist 4a), against a hub
it starts: auto follows access; a users-only or private app shown to everyone carries a lock; off
is never shown; hidden takes the tile away; only real apps and values taken; /admin/access says
what was chosen. python3 tests/sim_visibility.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
state = Path(tempfile.mkdtemp(prefix="visibility-"))
(state / "control").mkdir()
# The root helper's copy of the access choices, as it leaves it in the control folder.
(state / "control" / "access.json").write_text(json.dumps({"notes": "users", "git": "private", "wiki": "off"}))
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "f" * 64
env = dict(os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=str(state), HUB_ETC_DIR=str(state), PORT=str(port), HUB_BIND="127.0.0.1")
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
    def tile(page, name):
        i = page.find(f'<span class="name">{name}</span>')
        return None if i < 0 else page[page.rfind("<a ", 0, i):page.find("</a>", i)]
    _, home = go("GET", "/")
    check("auto: a users-only app has no tile for a guest", tile(home, "Notes") is None)
    check("auto: a private one neither", tile(home, "Git") is None)
    st, _ = go("POST", "/admin/visibility", {"app": "notes", "visible": "guests"})
    go("POST", "/admin/visibility", {"app": "git", "visible": "guests"})
    go("POST", "/admin/visibility", {"app": "wiki", "visible": "guests"})
    _, home = go("GET", "/")
    t = tile(home, "Notes")
    check("shown to everyone: the tile, with a lock", st == 200 and t and "locked" in t and "sign in to open" in t, t)
    t = tile(home, "Git")
    check("a private app shown to everyone: locked too", t and "locked" in t, t)
    check("an app that is off: never shown", tile(home, "Kiwix") is None)
    t = tile(home, "Excalidraw")
    check("a public app: no lock", t and "locked" not in t, t)
    go("POST", "/admin/visibility", {"app": "draw", "visible": "hidden"})
    _, home = go("GET", "/")
    check("hidden: no tile, even for a public app", tile(home, "Excalidraw") is None)
    for bad in ({"app": "draw", "visible": "admins"}, {"app": "nonsense", "visible": "guests"}, {"app": "draw"}):
        st, _ = go("POST", "/admin/visibility", bad)
        check(f"refused: {bad}", st == 400, st)
    _, body = go("GET", "/admin/access")
    vis = {a["id"]: a["visible"] for a in json.loads(body)["apps"]}
    check("/admin/access says what was chosen", vis.get("notes") == "guests" and vis.get("draw") == "hidden" and vis.get("mermaid") == "auto", vis)
    go("POST", "/admin/visibility", {"app": "draw", "visible": "auto"})
    _, home = go("GET", "/")
    check("back to auto: as its access again", tile(home, "Excalidraw") is not None)
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
