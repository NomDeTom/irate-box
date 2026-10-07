# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Accounts (next-work plan step 16, accounts-plan stage 1): the store offline (names, scrypt,
sessions, one-time codes, the limits, the admin's levels), then a hub on a spare port: the cookie's
flags, the HTTP stance, a page elsewhere refused. python3 tests/sim_accounts.py"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="accounts-"))
os.environ["HUB_STATE_DIR"] = str(T / "state")
sys.path.insert(0, str(REPO))
from irate_box.hub import accounts as A  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def refused(fn, *a, kind=A.AccountError, says=""):
    try:
        fn(*a)
    except kind as exc:
        return says in str(exc)
    return False


# Off by default: nothing changes on a box until its owner wants it.
check("off by default, HTTP with a warning", A.settings() == {"signup": "off", "http": "warning"})
check("  no sign-ups while off", refused(A.signup, "alice", "password1", says="no sign-ups"))
A.set_settings(signup="open")
check("open: an account usable at once", A.signup("Alice", "password1", "10.0.0.2") == "user")
check("  names: taken (any case), reserved, malformed", refused(A.signup, "ALICE", "password1", says="taken")
      and refused(A.signup, "admin", "password1", says="kept") and refused(A.signup, "a b", "password1") and refused(A.signup, "ab", "password1"))
check("  a short password refused", refused(A.signup, "bob", "short"))
raw = json.loads((T / "state" / "accounts.json").read_text())
h = raw["accounts"]["alice"]["hash"]
check("stored: scrypt with its salt, never the password; the file 600", h.startswith("scrypt$16384$8$1$") and "password1" not in json.dumps(raw)
      and (T / "state" / "accounts.json").stat().st_mode & 0o777 == 0o600, h)
t0 = time.time(); token, me = A.login("alice", "password1", "10.0.0.2"); dt = time.time() - t0
check("login: by name in any case; a session", me["name"] == "Alice" and A.session(token)["name"] == "Alice", me)
print(f"note: one scrypt check took {dt * 1000:.0f} ms here")
check("  the cookie itself never stored", token not in (T / "state" / "accounts.json").read_text())
check("  a wrong password, an unknown name: the same answer", refused(A.login, "alice", "nope-nope", "10.0.0.3", says="don't match")
      and refused(A.login, "nobody", "password1", "10.0.0.3", says="don't match"))
for _ in range(9):
    refused(A.login, "alice", "wrong-wrong", "10.0.0.9")
check("  10 failures in a quarter hour: a wait, even with the right password", refused(A.login, "alice", "password1", "10.0.0.8", kind=A.Wait))
A._fails.clear()
check("  a session that isn't one: none", A.session("x" * 43) is None and A.session(None) is None)
A.set_settings(signup="apply")
check("apply: the account waits", A.signup("bob", "password2", "10.0.0.4") == "asked" and refused(A.login, "bob", "password2", "", says="waits"))
A.change("bob", "accept")
tb, _ = A.login("bob", "password2")
check("  accepted: it logs in", A.session(tb)["state"] == "user")
check("  sign-ups from one address limited", all(A.signup(f"x{i}x", "password9", "10.0.0.7") for i in range(5))
      and refused(A.signup, "x6x", "password9", "10.0.0.7", kind=A.Wait))
A.change("bob", "disable")
check("disable: its sessions end at once, and it can't log in", A.session(tb) is None and refused(A.login, "bob", "password2", "", says="switched off"))
A.change("bob", "enable")
A.set_settings(signup="assigned")
code = A.make("carol", "admin")
check("assigned: no sign-ups; an admin's account with a one-time code", refused(A.signup, "dave", "password3", says="admin makes")
      and len(code) == 19 and code.count("-") == 3 and refused(A.login, "carol", "anything1", "", says="don't match"))
check("  the code sets the password (dashes and case don't matter), once", A.use_code(code.lower().replace("-", " "), "password4") == "carol"
      and A.login("carol", "password4")[1]["role"] == "admin" and refused(A.use_code, code, "password5", says="not one"))
tc, _ = A.login("carol", "password4")
code = A.reset("carol")
check("reset: a new code, and the account's sessions end", A.session(tc) is None and A.use_code(code, "password6") == "carol")
ta, _ = A.login("alice", "password1")
ta2, _ = A.login("alice", "password1")
A.change_password(ta, "password1", "password7")
check("change password: the old one wrong refused; done, its other sessions end", A.session(ta) and A.session(ta2) is None
      and refused(A.change_password, ta, "password1", "password8", says="not right"))
A.logout(ta)
check("logout: the session ends", A.session(ta) is None)
tb, _ = A.login("bob", "password2")
A.set_settings(signup="off")
check("off again: no logins, no sessions count, no codes", A.session(tb) is None and refused(A.login, "bob", "password2", says="no accounts")
      and refused(A.use_code, "ABCD-EFGH-JKLM-NPQR", "password9", says="no accounts"))
A.set_settings(signup="apply")
check("  on again: the sessions count again", A.session(tb) is not None)
A.change("x0x", "delete")
check("delete: gone; the list shows no hash", "x0x" not in [a["name"] for a in A.listing()] and all("hash" not in a for a in A.listing()))
check("counts", A.counts() == {"asked": 4, "user": 3, "disabled": 0, "admins": 1}, A.counts())
check("bad settings refused", refused(A.set_settings, "everyone") and refused(A.set_settings, None, "sometimes"))

# Users mode for apps (stage 2): access.py, and the root helper's files for nginx.
from irate_box.hub import access  # noqa: E402
st_ = access.clean({"draw": "users", "flasher": "users", "term": "users", "my-addon": "users", "wiki": "users"})
check("users mode: a built-in app may be for users; not the flasher (its page is cross-site), the shell, nor a local add-on",
      st_["draw"] == "users" and st_["wiki"] == "users" and st_["flasher"] == "public" and st_["term"] == "public" and "my-addon" not in st_, st_)
conf = access.nginx_conf(st_)
check("  nginx: no basic auth for it (the gate asks the hub instead)", "set $irate_box_auth_draw off;" in conf)
gates = access.nginx_gates(st_)
check("  a gate for each app for users, and only those", sorted(gates) == ["gate-draw.conf", "gate-wiki.conf"]
      and "auth_request /_irate_user;" in gates["gate-draw.conf"] and "error_page 401 = @irate_box_login;" in gates["gate-draw.conf"], gates)
check("  Caddy: forward_auth to the hub's check, which redirects", "forward_auth 127.0.0.1:8000" in access.caddy_snippets(st_, "HASH")["draw.caddy"]
      and "uri /_irate/user?redirect=1" in access.caddy_snippets(st_, "HASH")["draw.caddy"])
site = (REPO / "config" / "irate-box.nginx").read_text()
import re as _re  # noqa: E402
gated = set(_re.findall(r"auth_basic \$irate_box_auth_(\w+);", site))
check("  the site: every app location with a login has its gate include",
      all(f"auth_basic $irate_box_auth_{i};\n" + "\t" * 2 + f"include @ACCESS@.d/gate-{i}.conf*;" in site for i in gated)
      and site.count("location = /_irate_user") == 4 and site.count("location @irate_box_login") == 4, sorted(gated))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_WEB_SERVER="nginx")
(T / "etc").mkdir()
from irate_box.root import hub_control as H  # noqa: E402
H._access_files(st_)
gd = T / "etc" / "nginx-access.conf.d"
check("helper: the gates written beside the access include", sorted(p.name for p in gd.iterdir()) == ["gate-draw.conf", "gate-wiki.conf"])
H._access_files(access.clean({"draw": "users"}))
check("  and the one no longer for users removed", sorted(p.name for p in gd.iterdir()) == ["gate-draw.conf"])
try:
    H.access_set({"app": "flasher", "mode": "users"}); check("  the flasher for users: refused", False)
except ValueError as exc:
    check("  the flasher for users: refused", "not for users" in str(exc), exc)

# The hub.
st = T / "hub"
st.mkdir()
s_ = socket.socket(); s_.bind(("127.0.0.1", 0)); port = s_.getsockname()[1]; s_.close()
env = dict(os.environ, HUB_STATE_DIR=str(st), HUB_ETC_DIR=str(st), PORT=str(port), HUB_BIND="127.0.0.1")
hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(100):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1); break
    except OSError:
        time.sleep(0.1)


def req(path, body=None, headers=None, https=False):
    h = {"X-Forwarded-For": "10.1.1.1", "X-Forwarded-Proto": "https" if https else "http", "Host": f"127.0.0.1:{port}"}
    if body is not None:
        h.update({"Content-Type": "application/json", "X-Irate-Account": "1"})
    h.update(headers or {})
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}"), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers


try:
    code, d, _ = req("/api/account")
    check("hub: the page's view, off and nobody", code == 200 and d == {"signup": "off", "http": "warning", "https": False, "me": None}, d)
    code, d, _ = req("/admin/accounts", {"action": "settings", "signup": "open", "http": "prevented"}, {"X-Irate-Admin": "1"})
    check("  the admin sets the levels", code == 200 and d["settings"] == {"signup": "open", "http": "prevented"}, d)
    code, d, _ = req("/api/account", {"action": "signup", "name": "erin", "password": "password1"})
    check("  prevented: no password over plain HTTP", code == 403 and "HTTPS" in d["error"], d)
    code, d, h = req("/api/account", {"action": "signup", "name": "erin", "password": "password1"}, https=True)
    cookie = h.get("Set-Cookie", "")
    check("  over HTTPS: made and logged in; the cookie HttpOnly, SameSite=Strict, Secure", code == 200 and d["me"]["name"] == "erin"
          and all(f in cookie for f in ("irate_session=", "HttpOnly", "SameSite=Strict", "Secure", "Path=/")), (code, d, cookie))
    tok = cookie.split(";")[0]
    code, d, _ = req("/api/account", headers={"Cookie": tok}, https=True)
    check("  the cookie says who", d["me"]["name"] == "erin" and d["https"] is True, d)
    code, d, _ = req("/api/account", {"action": "login", "name": "erin", "password": "password1"}, {"X-Irate-Account": ""}, https=True)
    check("  without the page's header: refused", code == 403, d)
    code, d, _ = req("/api/account", {"action": "login", "name": "erin", "password": "password1"}, {"Origin": "http://elsewhere.example"}, https=True)
    check("  from another site's page: refused", code == 403, d)
    code, d, h = req("/api/account", {"action": "logout"}, {"Cookie": tok}, https=True)
    check("  logout: the cookie cleared, the session gone", "Max-Age=0" in h.get("Set-Cookie", "") and req("/api/account", headers={"Cookie": tok})[1]["me"] is None)
    req("/admin/accounts", {"action": "settings", "http": "warning"}, {"X-Irate-Admin": "1"})
    code, d, h = req("/api/account", {"action": "login", "name": "erin", "password": "password1"})
    check("  warning: allowed over HTTP, the cookie not Secure there", code == 200 and "Secure" not in h.get("Set-Cookie", ""), h.get("Set-Cookie"))
    code, d, _ = req("/admin/accounts", {"action": "make", "name": "frank"}, {"X-Irate-Admin": "1"})
    check("  the admin makes one: its code given once", code == 200 and len(d.get("code", "")) == 19 and any(a["name"] == "frank" and not a["password_set"] for a in d["accounts"]), d)
    code, d, _ = req("/admin/accounts", {"action": "make", "name": "frank"})
    check("  an /admin change without the admin page's header: refused", code == 403)
    # Users mode: the front's check, and the home page.
    code, d, _ = req("/_irate/user")
    check("  the front's check: a guest, 401", code == 401)
    code, d, h = req("/api/account", {"action": "login", "name": "erin", "password": "password1"})
    tok = h.get("Set-Cookie", "").split(";")[0]
    code, d, _ = req("/_irate/user", headers={"Cookie": tok})
    check("  logged in: 204", code == 204, code)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    r = urllib.request.Request(f"http://127.0.0.1:{port}/_irate/user?redirect=1", headers={"X-Forwarded-Uri": "/draw/?x=1&y=2"})
    try:
        urllib.request.build_opener(NoRedirect).open(r, timeout=5); loc = None
    except urllib.error.HTTPError as e:
        loc = (e.code, e.headers.get("Location"))
    check("  Caddy's (?redirect=1): to the account page, back to where they were going", loc == (302, "/account.html?next=/draw/%3Fx%3D1%26y%3D2"), loc)
    r = urllib.request.Request(f"http://127.0.0.1:{port}/_irate/user?redirect=1", headers={"X-Forwarded-Uri": "//elsewhere.example/"})
    try:
        urllib.request.build_opener(NoRedirect).open(r, timeout=5); loc = None
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location")
    check("  never back to another site", loc == "/account.html?next=/", loc)
    (st / "control").mkdir(exist_ok=True)
    (st / "control" / "access.json").write_text(json.dumps({"draw": "users"}))
    def home(cookie=None):
        r = urllib.request.Request(f"http://127.0.0.1:{port}/", headers={"Cookie": cookie} if cookie else {})
        return urllib.request.urlopen(r, timeout=5).read().decode()
    check("  the home page: an app for users shown to a user, not to a guest", "/app.html#/draw/" in home(tok) and "/app.html#/draw/" not in home())
finally:
    hub.terminate()
    hub.wait()
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
