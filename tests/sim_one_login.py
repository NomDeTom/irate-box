# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One login, through the standard user login: admin accounts sign in on the standard page even with sign-up off. The box's first
use: its admin pages free to anyone on the network, the home page saying so, until the owner makes the admin account
(over HTTPS, or over HTTP only by choice), which signs them in and turns the box's own login off (no password made for
scripts); after it, an admin's password changes only over HTTPS. Once an admin account can sign in, the admin's routes
send a browser to that sign-in (a script's login still passes); the header shows Admin only to a signed-in admin.
python3 tests/sim_one_login.py"""
import json, os, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="onelogin-"))
os.environ.update(HUB_STATE_DIR=str(T / "unit"))
(T / "unit").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.hub import accounts as A, access  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def refused(fn, *a, says=""):
    try:
        fn(*a)
        return False
    except A.AccountError as exc:
        return says in str(exc)
check("sign-up off by default, and no admin ready on a new box", A.settings()["signup"] == "off" and not A.admin_ready())
tok, me = A.claim_admin("alice", "password-one", "10.0.0.1")
check("first use: the owner's admin account made and signed in", me["role"] == "admin" and A.session(tok)["name"] == "alice" and A.admin_ready())
check("  admins sign in with sign-up off", A.login("alice", "password-one")[1]["name"] == "alice")
A.make("ann", "user")
data = A._load(); data["accounts"]["ann"]["hash"] = A.hash_password("password-two"); A._save(data)
check("  a user's account does not, with sign-up off", refused(A.login, "ann", "password-two", says="no accounts"))
check("  a wrong admin password is refused, as always", refused(A.login, "alice", "wrong-one", says="don't match"))
tok2, me2 = A.claim_admin("Ann", "password-three")
check("the console's reset and first use again: an existing name made admin with the new password", me2["role"] == "admin"
      and A.login("ann", "password-three")[1]["role"] == "admin")
check("  the name rules hold ('admin' is kept for the box's own login)", refused(A.claim_admin, "admin", "password-four", says="kept"))
code = A.make("bob", "admin")
check("an admin's one-time code over plain HTTP: refused, and the code kept", refused(A.use_code, code, "password-five", "", False, says="HTTPS"))
check("  over HTTPS: the password set with it", A.use_code(code, "password-five", "", True) == "bob" and A.login("bob", "password-five")[1]["role"] == "admin")
A.set_admin("dave", "password-seven", by="the console")
check("the console's set-admin: an admin account, no session made", A._load()["accounts"]["dave"]["role"] == "admin"
      and A._load()["accounts"]["dave"]["by"] == "the console" and not any(v.get("name") == "dave" for v in A._load()["sessions"].values()))
gate = lambda **k: access.nginx_gates({}, **k)["gate-admin.conf"]  # noqa: E731
check("nginx: no admin ready, the box's own login prompt; ready, a browser sent to sign in; off, the same",
      "error_page" not in gate(admin_login=True) and "error_page 401 = @irate_box_login" in gate(admin_login=True, accounts_ready=True)
      and "error_page 401 = @irate_box_login" in gate(admin_login=False) and "satisfy any" in gate(admin_login=True, accounts_ready=True))
cg = access.caddy_admin_gate
check("Caddy: soft with no admin ready; redirect with a script's login passed on when ready; a hard redirect with the login off",
      "soft=1" in cg(True, False) and "redirect=1&basic=1" in cg(True, True) and "uri /_irate/admin?redirect=1\n" in cg(False, True))

# The hub: first use over the page, the header, and Caddy's question.
st = T / "hub"; st.mkdir()
(st / "unclaimed").write_text("no admin password chosen yet\n")
s_ = socket.socket(); s_.bind(("127.0.0.1", 0)); port = s_.getsockname()[1]; s_.close()
env = dict(os.environ, HUB_STATE_DIR=str(st), HUB_ETC_DIR=str(st), PORT=str(port), HUB_BIND="127.0.0.1", HUB_UNCLAIMED_FILE=str(st / "unclaimed"))
hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(100):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1); break
    except OSError:
        time.sleep(0.1)
def req(path, body=None, headers=None, raw=False):
    h = {"X-Forwarded-For": "10.1.1.1", "Host": f"127.0.0.1:{port}"}
    if body is not None:
        h.update({"Content-Type": "application/json", "X-Irate-Admin": "1"})
    h.update(headers or {})
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(), headers=h)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(r, timeout=10) as resp:
            data = resp.read()
            return resp.status, (data.decode() if raw else json.loads(data or b"{}")), resp.headers
    except urllib.error.HTTPError as e:
        data = e.read()
        return e.code, (data.decode() if raw else json.loads(data or b"{}")), e.headers
HTTPS = {"X-Forwarded-Proto": "https"}
def queued():
    return [json.loads(p.read_text()) for p in st.rglob("requests/*.json")]
try:
    code, page, _ = req("/", raw=True)
    check("the first use, on the home page: Sign in, no Admin button; a note that the box is not set up, to /admin/setup", code == 200
          and "👤</span> Sign in" in page and "⚙️</span> Admin" not in page and 'class="first-use-note"' in page
          and 'href="/admin/setup"' in page and "set-admin NAME" in page, page[:600])
    code, page, _ = req("/admin/", raw=True)
    check("  the admin pages themselves, free, with their banner", code == 200 and 'id="first-use"' in page and "admin-main" in page, code)
    code, page, _ = req("/admin/setup", raw=True)
    check("  the set-up page at /admin/setup, its HTTP warning and the console's way", code == 200 and 'id="setup-http"' in page
          and "set-admin NAME" in page, code)
    code, d, _ = req("/admin/password", {"password": "pw-for-scripts"})
    check("  the box's own login not set from the free pages", code == 403 and "first use" in d.get("error", ""), (code, d))
    code, d, _ = req("/admin/accounts", {"action": "make", "name": "mallory", "role": "admin"})
    check("  nor an admin made on Accounts (the first admin is the set-up page's)", code == 403, (code, d))
    code, d, _ = req("/admin/setup", {"name": "owner", "password": "pw-of-the-owner"})
    check("the first password over plain HTTP, not chosen: refused, said why", code == 403 and d.get("https") is False and "HTTP" in d.get("error", ""), (code, d))
    code, d, h = req("/admin/setup", {"name": "owner", "password": "pw-of-the-owner", "over_http": True})
    cookie = (h.get("Set-Cookie") or "").split(";")[0]
    reqs = queued()
    check("  chosen: the admin account made, signed in; the first use ended with no password made for scripts; the gates follow",
          code == 202 and cookie.startswith("irate_session=") and any(r.get("action") == "claim" for r in reqs)
          and not any(r.get("action") == "password" for r in reqs) and any(r.get("action") == "admin-gate" for r in reqs),
          (code, d, [r.get("action") for r in reqs]))
    (st / "unclaimed").unlink()  # what the root helper's claim does
    acct = {"X-Irate-Account": "1", "Cookie": cookie}
    code, d, _ = req("/api/account", {"action": "password", "old": "pw-of-the-owner", "new": "pw-of-the-owner-2"}, headers=acct)
    check("after it, an admin's password over plain HTTP: refused", code == 400 and "HTTPS" in d.get("error", ""), (code, d))
    code, d, _ = req("/api/account", {"action": "password", "old": "pw-of-the-owner", "new": "pw-of-the-owner-2"}, headers=dict(acct, **HTTPS))
    check("  over HTTPS: changed", code == 200 and d.get("changed") is True, (code, d))
    code, d, _ = req("/admin/password", {"password": "pw-for-scripts"}, headers={"Cookie": cookie})
    check("  the box's own login over plain HTTP: refused (the console's script-login, or HTTPS)", code == 403 and d.get("https") is False, (code, d))
    code, page, _ = req("/", raw=True, headers={"Cookie": cookie})
    check("  the header for the admin: My account and Admin", "My account" in page and 'href="/admin/"' in page)
    code, page, _ = req("/", raw=True)
    check("  for a guest now: Sign in, no Admin, no first-use note", "👤</span> Sign in" in page and 'href="/admin/"' not in page
          and "first-use-note" not in page)
    code, _, _ = req("/_irate/admin?redirect=1&basic=1", headers={"Authorization": "Basic YWRtaW46eA=="}, raw=True)
    check("Caddy's question: a request carrying a login of its own passes on to basic_auth (which checks it)", code == 204, code)
    code, _, h = req("/_irate/admin?redirect=1&basic=1", headers={"X-Forwarded-Uri": "/admin/"}, raw=True)
    check("  anyone else without an admin session is sent to sign in, coming back to /admin", code == 302
          and h.get("Location") == "/account.html?next=/admin/", (code, h.get("Location")))
    code, _, h = req("/_irate/admin?redirect=1&basic=1", headers={"Cookie": cookie}, raw=True)
    check("  the admin's session: in", code == 204 and h.get("X-Irate-Session") == "admin")
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
