"""A person's own settings (menu overhaul M12; proposed 5f), against a hub it starts: set only
through their own session, each as it allows, the safe choice the default; names online only of
those who said yes, and only to whom the owner allows (guests get the count alone); the admin's
list shows each person's choice, never their email. python3 tests/sim_prefs.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
state = tempfile.mkdtemp(prefix="prefs-")
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
    def go(method, path, body=None, cookie=None, admin=False, account=False):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}",
             "Sec-Fetch-Site": "same-origin", "X-Forwarded-For": "10.1.0.1"}
        if cookie:
            h["Cookie"] = cookie
        if admin:
            h.update({"X-Irate-Front": SECRET, "X-Irate-Admin": "1"})
        if account:
            h["X-Irate-Account"] = "1"
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse(); raw = r.read().decode()
        cookie_out = (r.getheader("Set-Cookie") or "").split(";")[0]
        try:
            return r.status, json.loads(raw), cookie_out
        except ValueError:
            return r.status, raw, cookie_out
    go("POST", "/admin/accounts", {"action": "settings", "signup": "open"}, admin=True)
    jar = {}
    for name in ("alice", "bob"):
        st, d, ck = go("POST", "/api/account", {"action": "signup", "name": name, "password": "correct horse " + name}, account=True)
        jar[name] = ck
        check(f"{name} signs up and is logged in", st == 200 and d.get("me", {}).get("name") == name and ck, (st, d))
    st, d, _ = go("GET", "/api/account", cookie=jar["alice"])
    check("their own settings, the safe defaults: not shown by name, no lock, no email",
          d.get("prefs") == {"show_online": False, "hue": None, "lock_default": False, "email": ""}, d.get("prefs"))
    st, d, _ = go("POST", "/api/account", {"action": "prefs", "prefs": {"show_online": True, "hue": 210, "email": "alice@example.org"}}, cookie=jar["alice"], account=True)
    check("alice chooses to be shown, a colour, an email", st == 200 and d["prefs"]["show_online"] is True and d["prefs"]["hue"] == 210, d)
    for bad in ({"show_online": "yes"}, {"hue": 400}, {"email": "not an address"}, {"role": "admin"}, {}):
        st, _, _ = go("POST", "/api/account", {"action": "prefs", "prefs": bad}, cookie=jar["alice"], account=True)
        check(f"refused: {bad}", st == 400, st)
    st, _, _ = go("POST", "/api/account", {"action": "prefs", "prefs": {"show_online": True}}, account=True)
    check("no session: nothing to set", st == 400, st)
    st, _, _ = go("POST", "/api/account", {"action": "prefs", "prefs": {"show_online": True}}, cookie=jar["bob"])
    check("only from the account page (its header)", st == 403, st)
    # Both around: alice shown, bob not.
    go("GET", "/messages", cookie=jar["bob"])
    _, s, _ = go("GET", "/status", cookie=jar["alice"])
    check("to a user: both counted, only alice named", s.get("signed_in") == 2 and s.get("names") == ["alice"], (s.get("signed_in"), s.get("names")))
    _, s, _ = go("GET", "/status")
    check("to a guest: the count only", s.get("signed_in") == 2 and "names" not in s, (s.get("signed_in"), s.get("names")))
    go("POST", "/admin/settings", {"names_to": "admin"}, admin=True)
    _, s, _ = go("GET", "/status", cookie=jar["bob"])
    check("names for the admin only: a user gets the count", "names" not in s and s.get("signed_in") == 2, s.get("names"))
    _, acc, _ = go("GET", "/admin/accounts", admin=True)
    listed = {a["name"]: a for a in acc.get("accounts", [])}
    check("the admin sees each choice", listed.get("alice", {}).get("shown_online") is True and listed.get("bob", {}).get("shown_online") is False, listed)
    check("  never an email", "alice@example.org" not in json.dumps(acc))
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
