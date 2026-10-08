"""Who sees what a person posts (menu overhaul M13; Tom, 2026-10-08), against a hub it starts:
alice chooses, per app, everyone here, signed-in people or only her; what bob (signed in) and a
guest read follows it in the shoutbox, the forum, saved work and the file drop, to the listings
and the reads; the admin's moderation still sees all; a choice that isn't one is refused.
python3 tests/sim_seen.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
state = tempfile.mkdtemp(prefix="seen-")
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "f" * 64
env = dict(os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=state, HUB_ETC_DIR=state, PORT=str(port), HUB_BIND="127.0.0.1")
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
    def go(method, path, body=None, cookie=None, admin=False, account=False, raw=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
        h.update(headers or {})
        if cookie:
            h["Cookie"] = cookie
        if admin:
            h.update({"X-Irate-Front": SECRET, "X-Irate-Admin": "1"})
        if account:
            h["X-Irate-Account"] = "1"
        c.request(method, path, body=raw if raw is not None else (json.dumps(body).encode() if body is not None else None), headers=h)
        r = c.getresponse(); text = r.read().decode(errors="replace")
        ck = (r.getheader("Set-Cookie") or "").split(";")[0]
        try:
            return r.status, json.loads(text), ck
        except ValueError:
            return r.status, text, ck
    go("POST", "/admin/accounts", {"action": "settings", "signup": "open"}, admin=True)
    jar = {}
    for name in ("alice", "bob"):
        _, _, jar[name] = go("POST", "/api/account", {"action": "signup", "name": name, "password": "correct horse " + name}, account=True)
    st, d, _ = go("POST", "/api/account", {"action": "prefs", "prefs": {"posts": {"shoutbox": "me", "board": "users", "saves": "me", "drop": "users"}}},
                  cookie=jar["alice"], account=True)
    check("alice chooses per app", st == 200 and d["prefs"]["posts"] == {"shoutbox": "me", "board": "users", "saves": "me", "drop": "users"}, d)
    st, _, _ = go("POST", "/api/account", {"action": "prefs", "prefs": {"posts": {"shoutbox": "nobody"}}}, cookie=jar["alice"], account=True)
    check("a choice that isn't one: refused", st == 400, st)
    go("POST", "/messages", {"text": "just a note to self"}, cookie=jar["alice"])
    go("POST", "/messages", {"name": "Moth", "text": "hello from a guest"})
    go("POST", "/board/threads", {"title": "Members' meet", "text": "Saturday"}, cookie=jar["alice"])
    go("POST", "/api/saves", {"kind": "excalidraw", "name": "alice's sketch", "state": {"elements": []}}, cookie=jar["alice"])
    go("POST", "/api/drop", raw=b"members only", cookie=jar["alice"], headers={"Content-Type": "application/octet-stream", "X-Drop-Name": "plan.txt", "Content-Length": "12"})
    def views(cookie=None):
        _, m, _ = go("GET", "/messages", cookie=cookie)
        _, b, _ = go("GET", "/board/threads", cookie=cookie)
        _, s, _ = go("GET", "/api/saves", cookie=cookie)
        _, f, _ = go("GET", "/api/drop", cookie=cookie)
        return ({x["text"] for x in m["messages"]}, {x["title"] for x in b["threads"]}, {x["name"] for x in s["saves"]}, {x["name"] for x in f["files"]})
    a, b, g = views(jar["alice"]), views(jar["bob"]), views()
    check("alice sees everything of hers", "just a note to self" in a[0] and "Members' meet" in a[1] and "alice's sketch" in a[2] and "plan.txt" in a[3], a)
    check("bob, signed in: the forum thread and the file, not her note or her sketch",
          "just a note to self" not in b[0] and "Members' meet" in b[1] and "alice's sketch" not in b[2] and "plan.txt" in b[3], b)
    check("a guest: none of hers, the guest's own message still there",
          "just a note to self" not in g[0] and "hello from a guest" in g[0] and not g[1] and "alice's sketch" not in g[2] and "plan.txt" not in g[3], g)
    _, threads, _ = go("GET", "/board/threads", cookie=jar["alice"])
    tid = next(t["id"] for t in threads["threads"] if t["title"] == "Members' meet")
    st, _, _ = go("GET", f"/board/thread/{tid}")
    check("the thread itself: not found for a guest", st == 404, st)
    _, saves, _ = go("GET", "/api/saves", cookie=jar["alice"])
    sid = next(x["id"] for x in saves["saves"] if x["name"] == "alice's sketch")
    st, _, _ = go("GET", f"/api/saves/{sid}", cookie=jar["bob"])
    check("her sketch read directly by bob: not found", st == 404, st)
    _, files, _ = go("GET", "/api/drop", cookie=jar["alice"])
    fid = next(x["id"] for x in files["files"] if x["name"] == "plan.txt")
    st, _, _ = go("GET", f"/api/drop/{fid}")
    check("the file downloaded by a guest: not found", st == 404, st)
    st, _, _ = go("GET", f"/api/drop/{fid}", cookie=jar["bob"])
    check("by bob, signed in: there", st == 200, st)
    _, mod, _ = go("GET", "/admin/moderation", admin=True)
    check("the admin's moderation sees all", any(m["text"] == "just a note to self" for m in mod["messages"]))
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
