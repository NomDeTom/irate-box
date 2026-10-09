# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
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
    # The shoutbox and the board (M9): drawn on the hub page; who sees each tab is theirs too.
    _, home = go("GET", "/")
    check("both tabs on the page by default", 'data-tab="shout"' in home and 'data-tab="board"' in home and "data-off" not in home)
    go("POST", "/admin/visibility", {"app": "shoutbox", "visible": "hidden"})
    go("POST", "/admin/visibility", {"app": "board", "visible": "users"})
    _, home = go("GET", "/")
    check("the shoutbox hidden: its tab gone, its pane marked off", 'data-tab="shout"' not in home and '<div id="tab-shout" data-off hidden>' in home)
    check("the board for users: gone for a guest too", 'data-tab="board"' not in home and '<div id="tab-board" data-off hidden>' in home)
    _, body = go("GET", "/admin/access")
    check("/admin/access says so", json.loads(body).get("pages") == {"shoutbox": "hidden", "board": "users"}, body[-120:])
    go("POST", "/admin/visibility", {"app": "shoutbox", "visible": "auto"})
    go("POST", "/admin/visibility", {"app": "board", "visible": "auto"})
    go("POST", "/admin/visibility", {"app": "draw", "visible": "auto"})
    _, home = go("GET", "/")
    check("back to auto: as its access again", tile(home, "Excalidraw") is not None)
    # F3: the tiles with no switch (the folders, About) have a "who sees it" of their own.
    _, body = go("GET", "/admin/access")
    seen = json.loads(body).get("seen", {})
    check("seen-only tiles listed: About and the folders", "about" in seen and "tools-general" in seen and "notes" not in seen, seen)
    check("a folder's tile there by default", tile(home, "Calculators") is not None)
    st1, _ = go("POST", "/admin/visibility", {"app": "tools-general", "visible": "hidden"})
    st2, _ = go("POST", "/admin/visibility", {"app": "about", "visible": "users"})
    _, home = go("GET", "/")
    check("a folder hidden: no tile", st1 == 200 and tile(home, "Calculators") is None)
    check("About for users: no tile for a guest", st2 == 200 and tile(home, "About") is None)
    st, page = go("GET", "/tools-general.html")
    check("a hidden folder's list still opens at its address", st == 200, st)
    go("POST", "/admin/visibility", {"app": "tools-general", "visible": "auto"})
    go("POST", "/admin/visibility", {"app": "about", "visible": "auto"})
    # The sign-in offer (F3, setup decision "sign-in-offer"): a users-only app at "as its access".
    go("POST", "/admin/visibility", {"app": "notes", "visible": "auto"})
    go("POST", "/admin/visibility", {"app": "git", "visible": "auto"})
    _, home = go("GET", "/")
    check("offer off (the default): a users-only app has no tile for a guest", tile(home, "Notes") is None)
    st, _ = go("POST", "/admin/settings", {"sign_in_offer": True})
    _, home = go("GET", "/")
    t = tile(home, "Notes")
    check("offer on: the tile shows to a guest, with a lock", st == 200 and t and "locked" in t, t)
    check("offer on: a private app still has no tile", tile(home, "Git") is None)
    # The global override of what a seen-but-unopenable tile does (Tom, 2026-10-09).
    st, _ = go("POST", "/admin/settings", {"locked_all": "grey"})
    _, home = go("GET", "/")
    t = tile(home, "Notes")
    check("override: every locked tile greyed, over the per-app choice", st == 200 and t and "greyed" in t, t)
    st, _ = go("POST", "/admin/settings", {"locked_all": "nonsense"})
    check("  refused: an unknown way", st in (200, 400) and json.loads(go("GET", "/admin/settings")[1])["locked_all"] == "grey")
    go("POST", "/admin/settings", {"locked_all": ""})
    _, home = go("GET", "/")
    check("  cleared: back to each app's own choice", "greyed" not in (tile(home, "Notes") or ""))
    go("POST", "/admin/settings", {"sign_in_offer": False})
    # The apps row's order (F3): kept by the hub; the rest after the ones named, as before.
    _, home = go("GET", "/")
    check("before: Excalidraw ahead of About", 0 <= home.find(">Excalidraw<") < home.find(">About<"))
    st, body = go("POST", "/admin/tiles", {"state": {"order": ["about", "draw"]}})
    _, home = go("GET", "/")
    check("an order chosen: About first, then Excalidraw", st == 200 and 0 <= home.find(">About<") < home.find(">Excalidraw<"), body[:200])
    check("/admin/tiles says it", json.loads(go("GET", "/admin/tiles")[1])["state"]["order"] == ["about", "draw"])
    st, _ = go("POST", "/admin/tiles", {"state": {"order": ["about", "nonsense"]}})
    check("refused: an order naming no tile", st == 400, st)
    # The admin level (Tom, 2026-10-08: the mock's four chips): a tile only an admin account sees.
    def visit(path, cookie="", body=None, account=False):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
        if cookie:
            h["Cookie"] = cookie
        if account:
            h["X-Irate-Account"] = "1"
        c.request("POST" if body is not None else "GET", path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse(); raw = r.read().decode()
        return raw, (r.getheader("Set-Cookie") or "").split(";")[0]
    go("POST", "/admin/accounts", {"action": "settings", "signup": "open"})
    jar = {}
    for name, role in (("ada", "admin"), ("bea", "user")):
        _, body = go("POST", "/admin/accounts", {"action": "make", "name": name, "role": role})
        visit("/api/account", body={"action": "code", "code": json.loads(body)["code"], "password": "correct horse " + name}, account=True)
        _, jar[name] = visit("/api/account", body={"action": "login", "name": name, "password": "correct horse " + name}, account=True)
    st, _ = go("POST", "/admin/visibility", {"app": "draw", "visible": "admin"})
    check("visible admin taken", st == 200, st)
    check("admin: no tile for a guest", tile(visit("/")[0], "Excalidraw") is None)
    check("admin: no tile for a user", jar["bea"] and tile(visit("/", jar["bea"])[0], "Excalidraw") is None)
    check("admin: the tile for an admin account", jar["ada"] and tile(visit("/", jar["ada"])[0], "Excalidraw") is not None, jar)
    t = tile(visit("/", jar["ada"])[0], "Excalidraw") or ""
    check("admin: the admin's tile carries its pill", "admin-pill" in t and "admin-only" in t, t)
    t = tile(visit("/", jar["ada"])[0], "Notes") or ""
    check("  a tile others see too has none", t and "admin-pill" not in t, t)
    # The box's own admin login (Tom, 2026-10-08): seen at /admin, a signed cookie; the home page then
    # shows the admin's tiles even with no account at all.
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", "/admin/", headers={"Host": f"127.0.0.1:{port}", "X-Irate-Front": SECRET})
    r = c.getresponse(); r.read()
    seen = (r.getheader("Set-Cookie") or "").split(";")[0]
    check("/admin leaves the admin-seen cookie, HttpOnly, SameSite=Strict", seen.startswith("irate_admin_seen=")
          and "HttpOnly" in (r.getheader("Set-Cookie") or "") and "SameSite=Strict" in (r.getheader("Set-Cookie") or ""), r.getheader("Set-Cookie"))
    t = tile(visit("/", seen)[0], "Excalidraw") or ""
    check("the admin seen at /admin: the admin's tile, with its pill, no account needed", "admin-pill" in t, t)
    forged = "irate_admin_seen=" + seen.split("=", 1)[1].split(".")[0] + "." + "0" * 64
    check("  a forged one: no", tile(visit("/", forged)[0], "Excalidraw") is None)
    check("  an old one: no", tile(visit("/", "irate_admin_seen=1." + "0" * 64)[0], "Excalidraw") is None)
    go("POST", "/admin/visibility", {"app": "draw", "visible": "auto"})
    go("POST", "/admin/visibility", {"app": "git", "visible": "auto"})
    check("auto, private: no tile for a user, the tile for an admin account", tile(visit("/", jar["bea"])[0], "Git") is None
          and tile(visit("/", jar["ada"])[0], "Git") is not None)
    # What a locked tile does (Tom, 2026-10-08): sign in, sign up, a padlock, greyed.
    def card(page, name):
        i = page.find(f'<span class="name">{name}</span>')
        if i < 0:
            return None
        start = max(page.rfind("<a ", 0, i), page.rfind("<div ", 0, i))
        end = min(x for x in (page.find("</a>", i), page.find("</div>", i)) if x >= 0)
        return page[start:end]
    go("POST", "/admin/visibility", {"app": "notes", "visible": "guests"})  # notes: for users, seen by everyone
    t = card(visit("/")[0], "Notes") or ""
    check("locked, sign in (the default): to the sign-in page, back to the app after", 'href="/account.html?next=' in t and "sign in to open" in t, t)
    st, _ = go("POST", "/admin/visibility", {"app": "notes", "locked": "signup"})
    t = card(visit("/")[0], "Notes") or ""
    check("sign up, with accounts open: to the sign-up form", st == 200 and "#signup" in t and "sign up to open" in t, t)
    go("POST", "/admin/accounts", {"action": "settings", "signup": "off"})
    t = card(visit("/")[0], "Notes") or ""
    check("  with no accounts to make: sign in instead", "#signup" not in t and "sign in to open" in t, t)
    go("POST", "/admin/accounts", {"action": "settings", "signup": "open"})
    go("POST", "/admin/visibility", {"app": "notes", "locked": "padlock"})
    t = card(visit("/")[0], "Notes") or ""
    check("padlock: a padlock, and nothing to follow", t.startswith("<div ") and "padlock" in t and "href" not in t and "🔒" in t, t)
    go("POST", "/admin/visibility", {"app": "notes", "locked": "grey"})
    t = card(visit("/")[0], "Notes") or ""
    check("greyed: greyed out, nothing to follow, no padlock", t.startswith("<div ") and "greyed" in t and "href" not in t and "🔒" not in t, t)
    t = card(visit("/", jar["bea"])[0], "Notes") or ""
    check("  a user, who may open it, gets the app as ever", t.startswith("<a ") and "greyed" not in t and "/notes" in t, t)
    st, _ = go("POST", "/admin/visibility", {"app": "notes", "locked": "ajar"})
    check("refused: a way not offered", st == 400, st)
    check("/admin/access says each app's way", json.loads(go("GET", "/admin/access")[1]).get("locked_as") == {"notes": "grey"})
    go("POST", "/admin/visibility", {"app": "notes", "locked": "signin"})
    st, body = go("GET", "/menus.json")
    check("/menus.json still answers", st == 200 and "tools-general" in json.loads(body), body[:120])
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
