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

# git over HTTP (stage 4): an account by its basic-auth credentials.
import base64  # noqa: E402
basic = lambda n, pw: "Basic " + base64.b64encode(f"{n}:{pw}".encode()).decode()  # noqa: E731
A.set_settings(signup="apply")
check("check_basic: an account's name and password, its role", A.check_basic(basic("carol", "password6"), "10.9.9.9") == {"name": "carol", "role": "admin"})
t0 = time.time(); A.check_basic(basic("carol", "password6")); quick = time.time() - t0
check("  a wrong password, the box's own 'admin', garbage, a cookie-less nothing: none", A.check_basic(basic("carol", "nope-nope"), "10.9.9.9") is None
      and A.check_basic(basic("admin", "whatever"), "") is None and A.check_basic("Basic !!!", "") is None and A.check_basic("", "") is None)
check("  a right one remembered for a minute (git asks several times a push)", quick < 0.01, quick)
A._fails.clear()

# Users mode for apps (stage 2): access.py, and the root helper's files for nginx.
from irate_box.hub import access  # noqa: E402
st_ = access.clean({"draw": "users", "flasher": "users", "term": "users", "my-addon": "users", "wiki": "users"})
check("users mode: a built-in app may be for users; not the flasher (its page is cross-site), the shell, nor a local add-on",
      st_["draw"] == "users" and st_["wiki"] == "users" and st_["flasher"] == "public" and st_["term"] == "public" and "my-addon" not in st_, st_)
conf = access.nginx_conf(st_)
check("  nginx: no basic auth for it (the gate asks the hub instead)", "set $irate_box_auth_draw off;" in conf)
gates = access.nginx_gates(st_)
check("  a gate for each app for users, and the admin's", sorted(gates) == ["gate-admin.conf", "gate-draw.conf", "gate-wiki.conf"]
      and "auth_request /_irate_user;" in gates["gate-draw.conf"] and "error_page 401 = @irate_box_login;" in gates["gate-draw.conf"], gates)
check("  Caddy: forward_auth to the hub's check, which redirects", "forward_auth 127.0.0.1:8000" in access.caddy_snippets(st_, "HASH")["draw.caddy"]
      and "uri /_irate/user?redirect=1" in access.caddy_snippets(st_, "HASH")["draw.caddy"])
sn = access.caddy_snippets(access.clean({"tools": "private"}), "HASH")
check("  Caddy's admin gate: the hub asked (soft), its answer copied onto the request; a private app: the session, else the login",
      "uri /_irate/admin?soft=1" in sn["admin-gate.caddy"] and "copy_headers X-Irate-Session" in sn["admin-gate.caddy"]
      and sn["tools.caddy"].index("request_header -X-Irate-Session") < sn["tools.caddy"].index("forward_auth")
      < sn["tools.caddy"].index("basic_auth @irate_box_basic {\n\t\tadmin HASH") and "not header X-Irate-Session admin" in sn["tools.caddy"], sn["tools.caddy"])
sn = access.caddy_snippets(access.clean({"tools": "private"}), "HASH", admin_login=False)
check("  with the box's own login off: a hard check (anyone else sent to log in), no login asked",
      "uri /_irate/admin?redirect=1" in sn["admin-gate.caddy"] and "redirect=1" in sn["tools.caddy"] and "basic_auth" not in sn["tools.caddy"], sn)
cf = (REPO / "config" / "Caddyfile").read_text()
check("  the Caddyfile: /admin, the shell, Syncthing and /git-private take the gate before their own login, which stays (no gate file: the login alone); git asks the hub",
      cf.count("import {$HUB_ACCESS_DIR:/etc/caddy/irate-box-access}/admin-gate.caddy*") == 4
      and cf.count("basic_auth @irate_box_basic {") == 6 and "try_files /guest-push" not in cf
      and all(cf.index("admin-gate.caddy*", cf.index(r)) < cf.index("basic_auth @irate_box_basic", cf.index(r)) < cf.index("reverse_proxy", cf.index(r))
              for r in ("handle_path /sync/* {", "handle /term/* {")))
g2 = access.nginx_gates(access.clean({"tools": "private", "term": "private"}))
check("admin gate: the box's own login or an admin's session; a private app gets it, the shell needs none of its own",
      g2["gate-admin.conf"].endswith("satisfy any;\nauth_request /_irate_admin;\n") and g2["gate-tools.conf"] == g2["gate-admin.conf"] and "gate-term.conf" not in g2, g2)
check("  with the box's own login off: anyone without an admin session sent to log in",
      "error_page 401 = @irate_box_login;" in access.nginx_gates({}, admin_login=False)["gate-admin.conf"])
site = (REPO / "config" / "irate-box.nginx").read_text()
check("  the site: /admin/, /term/ and /sync/ include it; each server block can ask",
      all(site.index("include @ACCESS@.d/gate-admin.conf*;", site.index(loc)) < site.index("\n\t}\n", site.index(loc)) for loc in ("location /admin/ {", "location /term/ {", "location /sync/ {"))
      and site.count("location = /_irate_admin") == 4)
import re as _re  # noqa: E402
gated = set(_re.findall(r"auth_basic \$irate_box_auth_(\w+);", site))
check("  the site: every app location with a login has its gate include",
      all(f"auth_basic $irate_box_auth_{i};\n" + "\t" * 2 + f"include @ACCESS@.d/gate-{i}.conf*;" in site for i in gated)
      and site.count("location = /_irate_user") == 4 and site.count("location @irate_box_login") == 4, sorted(gated))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_WEB_SERVER="nginx", HUB_NGINX_LOGINS=str(T / "etc" / "htpasswd"))
(T / "etc").mkdir()
from irate_box.root import hub_control as H  # noqa: E402
H._access_files(st_)
gd = T / "etc" / "nginx-access.conf.d"
check("helper: the gates written beside the access include", sorted(p.name for p in gd.iterdir()) == ["gate-admin.conf", "gate-draw.conf", "gate-wiki.conf"])
H._access_files(access.clean({"draw": "users"}))
check("  and the one no longer for users removed", sorted(p.name for p in gd.iterdir()) == ["gate-admin.conf", "gate-draw.conf"])
try:
    H.access_set({"app": "flasher", "mode": "users"}); check("  the flasher for users: refused", False)
except ValueError as exc:
    check("  the flasher for users: refused", "not for users" in str(exc), exc)

# The box's own admin login switched off and on (stage 3).
(T / "etc" / "htpasswd").write_text("admin:$6$hash\n")
H.run = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")   # no systemctl here
H.CONTROL = T / "control"; H.CONTROL.mkdir(exist_ok=True); H.ADMIN_LOGIN_STATE = H.CONTROL / "admin-login.json"
H.STATE = T / "state"
A.set_settings(signup="apply")
try:
    H.admin_login({"on": False}); check("off: refused until an admin account has logged in over HTTPS", False)
except ValueError as exc:
    check("off: refused until an admin account has logged in over HTTPS", "over HTTPS" in str(exc), exc)
A.login("carol", "password6", https=True)
out = H.admin_login({"on": False})
check("  then off: the login file emptied, its hash kept aside, the gates send people to log in, the hub told",
      "admin:" not in (T / "etc" / "htpasswd").read_text() and (T / "etc" / "admin-login.off").read_text() == "admin:$6$hash\n"
      and "error_page 401" in (gd / "gate-admin.conf").read_text() and json.loads(H.ADMIN_LOGIN_STATE.read_text()) == {"on": False}
      and "carol" in out, out)
check("  while it is off: the last admin account can't be switched off, made a user or deleted; sign-up can't go off",
      refused(A.change, "carol", "user", True, says="last admin") and refused(A.change, "carol", "delete", True, says="last admin")
      and refused(A.set_settings, "off", None, True, says="switch it on first"))
H.admin_login({"on": True})
check("on again: the login as it was, the gates ask for it again", (T / "etc" / "htpasswd").read_text() == "admin:$6$hash\n"
      and not (T / "etc" / "admin-login.off").exists() and "error_page" not in (gd / "gate-admin.conf").read_text())
H.admin_login({"on": False})
H.set_login("a-new-password", keep=False)
check("  a new password (reset-password at the console): on again", (T / "etc" / "htpasswd").read_text().startswith("admin:$6$")
      and not (T / "etc" / "admin-login.off").exists() and json.loads(H.ADMIN_LOGIN_STATE.read_text()) == {"on": True})

# The same under Caddy: its hashes swapped for an unknown one and back; the gate hard while off.
CF = T / "Caddyfile"
REAL = "$2a$14$" + "r" * 53
CF.write_text(f":80 {{\n\thandle /admin/* {{\n\t\tbasic_auth @irate_box_basic {{\n\t\t\tadmin {REAL}\n\t\t}}\n\t}}\n"
              f"\thandle /term/* {{\n\t\tbasic_auth {{\n\t\t\tadmin {REAL}\n\t\t}}\n\t}}\n}}\n")
caddy_calls = []
def caddy_run(*a, **k):
    caddy_calls.append(a)
    if a[:2] == ("caddy", "hash-password"):
        return subprocess.CompletedProcess(a, 0, "$2a$14$" + "u" * 53 + "\n", "")
    if a[:2] == ("caddy", "version"):
        return subprocess.CompletedProcess(a, 0, "v2.6.2\n", "")
    return subprocess.CompletedProcess(a, 0, "", "")
H.run, H.WEB_SERVER, H.CADDYFILE, H.CADDY_SITE = caddy_run, "caddy", CF, T / "no-site.caddy"
H.CADDY_ACCESS = T / "caddy-access"
popen = H.subprocess.Popen; H.subprocess.Popen = lambda *a, **k: caddy_calls.append(a[0])
try:
    out = H.admin_login({"on": False})
    gate = (H.CADDY_ACCESS / "admin-gate.caddy").read_text()
    check("Caddy, off: every hash swapped for one nobody knows, the real one kept aside, the gate hard, Caddy restarted, the hub told",
          REAL not in CF.read_text() and CF.read_text().count("$2a$14$" + "u" * 53) == 2
          and (T / "etc" / "admin-login.off").read_text().strip() == REAL and "redirect=1" in gate
          and any(c[-2:] == ["restart", "caddy"] for c in caddy_calls if isinstance(c, list))
          and json.loads(H.ADMIN_LOGIN_STATE.read_text()) == {"on": False} and "carol" in out, (out, CF.read_text()))
    H.admin_login({"on": True})
    check("  on again: the real hash back everywhere, the gate soft", CF.read_text().count(REAL) == 2
          and not (T / "etc" / "admin-login.off").exists() and "soft=1" in (H.CADDY_ACCESS / "admin-gate.caddy").read_text())
    H.admin_login({"on": False})
    H.set_login("another-password", keep=False)
    check("  a new password while off: on again, the new hash everywhere", not (T / "etc" / "admin-login.off").exists()
          and CF.read_text().count("$2a$14$" + "u" * 53) == 2 and "soft=1" in (H.CADDY_ACCESS / "admin-gate.caddy").read_text())
finally:
    H.subprocess.Popen, H.WEB_SERVER = popen, "nginx"

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
    code, _, _ = req("/_irate/admin", headers={"Cookie": tok})
    check("  the admin check: a user's session, 401", code == 401, code)
    code, d, _ = req("/admin/accounts", {"action": "make", "name": "gina", "role": "admin"}, {"X-Irate-Admin": "1"})
    req("/api/account", {"action": "code", "code": d["code"], "password": "password9"})
    _, _, h = req("/api/account", {"action": "login", "name": "gina", "password": "password9"}, https=True)
    code, _, _ = req("/_irate/admin", headers={"Cookie": h.get("Set-Cookie", "").split(";")[0]})
    _, d, _ = req("/admin/accounts")
    r = urllib.request.Request(f"http://127.0.0.1:{port}/_irate/admin", data=b"", method="POST",
                               headers={"Cookie": h.get("Set-Cookie", "").split(";")[0], "Content-Length": "40"})
    t0 = time.time()
    try:
        pc = urllib.request.urlopen(r, timeout=5).status
    except (urllib.error.HTTPError, OSError) as e:
        pc = getattr(e, "code", str(e))
    check("  asked as a POST whose body never comes (a gated POST): answered at once, not waited on", pc == 204 and time.time() - t0 < 2, pc)
    import http.client  # noqa: E402
    hc = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    hc.request("GET", "/_irate/admin", headers={"Connection": "close"})
    rr = hc.getresponse()
    check("  a request that asks to close: the answer says it closes (the web server must not reuse it)",
          (rr.getheader("Connection") or "").lower() == "close", rr.getheaders())
    hc.close()
    check("  an admin account's: 204; the Accounts page knows it logged in over HTTPS", code == 204 and d["admin_login"]["on"] is True
          and d["admin_login"]["https_admins"] == ["gina"], (code, d.get("admin_login")))
    gtok = h.get("Set-Cookie", "").split(";")[0]
    def caddy_ask(q, cookie=None):
        r = urllib.request.Request(f"http://127.0.0.1:{port}/_irate/admin?{q}", headers=dict({"X-Forwarded-Uri": "/term/"}, **({"Cookie": cookie} if cookie else {})))
        try:
            with urllib.request.build_opener(NoRedirect).open(r, timeout=5) as resp:
                return resp.status, resp.headers.get("X-Irate-Session") or None, None
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("X-Irate-Session") or None, e.headers.get("Location")
    check("Caddy's admin gate, the own login on (soft): an admin's session says so; a user's and a guest's pass on to the login",
          caddy_ask("soft=1", gtok) == (204, "admin", None) and caddy_ask("soft=1", tok) == (204, None, None)
          and caddy_ask("soft=1") == (204, None, None), (caddy_ask("soft=1", gtok), caddy_ask("soft=1", tok)))
    check("  the own login off (redirect): an admin's session in; anyone else sent to log in, back to where they were going",
          caddy_ask("redirect=1", gtok) == (204, "admin", None) and caddy_ask("redirect=1", tok) == (302, None, "/account.html?next=/term/"),
          (caddy_ask("redirect=1", gtok), caddy_ask("redirect=1", tok)))
    # The shoutbox and forum (stage 5): who may post, and the users' marks.
    utok = req("/api/account", {"action": "login", "name": "erin", "password": "password1"})[2].get("Set-Cookie", "").split(";")[0]
    code, d, _ = req("/messages", {"name": "Erin", "text": "hello"}, {"X-Irate-Account": ""})
    check("shoutbox: a guest can't post under an account's name (any case)", code == 403 and "account's name" in d["error"], d)
    code, d, _ = req("/messages", {"name": "Someone Else", "text": "as erin"}, {"Cookie": utok})
    check("  a user posts under their account's name, marked as theirs", code == 201 and d["name"] == "erin" and d["account"] == "erin", d)
    code, d, _ = req("/messages", {"name": "Salty Parrot", "text": "ahoy"})
    check("  a guest under any other name, unmarked", code == 201 and "account" not in d, d)
    code, d, _ = req("/messages", headers={"Cookie": utok})
    check("  the page told who may post, the marks, and who this is", d["posting"] == {"who": "guests", "marks": True, "me": "erin"}, d.get("posting"))
    req("/admin/settings", {"shout_who": "users", "board_who": "off", "board_marks": False, "shout_marks": "sometimes"}, {"X-Irate-Admin": "1"})
    code, d, _ = req("/messages", {"name": "Salty Parrot", "text": "ahoy"})
    check("users only: a guest refused, told to log in", code == 403 and d.get("login") is True, d)
    code, d, _ = req("/messages", {"name": "x", "text": "still here"}, {"Cookie": utok})
    check("  a user posts", code == 201)
    code, d, _ = req("/board/threads", {"name": "erin", "title": "t", "text": "x"}, {"Cookie": utok})
    check("forum off: no one posts, users neither", code == 403 and "closed" in d["error"], d)
    code, d, _ = req("/board/threads")
    check("  marks off told to the page; a setting that isn't one left as it was", d["posting"]["who"] == "off" and d["posting"]["marks"] is False
          and req("/messages")[1]["posting"]["marks"] is True, d["posting"])
    req("/admin/settings", {"board_who": "guests"}, {"X-Irate-Admin": "1"})
    code, d, _ = req("/board/threads", {"name": "anyone", "title": "t", "text": "x"}, {"Cookie": utok})
    tid = d["thread"]["id"]
    code2, d2, _ = req(f"/board/thread/{tid}", {"name": "ERIN", "text": "me too"})
    code3, d3, _ = req(f"/board/thread/{tid}", {"name": "Bosun", "text": "aye"})
    th = req(f"/board/thread/{tid}")[1]
    check("forum: a user's thread under their name and marked; a guest can't reply as them; a guest's reply unmarked",
          d["thread"]["author"] == "erin" and d["thread"]["posts"][0]["account"] == "erin" and code2 == 403 and code3 == 201
          and "account" not in d3["post"] and req("/board/threads")[1]["threads"][0]["account"] == "erin" and th["posting"]["who"] == "guests", (d, d2, d3))
finally:
    hub.terminate()
    hub.wait()
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
