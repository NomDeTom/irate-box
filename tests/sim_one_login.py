# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One login (Tom, 2026-10-09: "get rid of the admin-specific login, and have them log in through the standard
user login"): admin accounts sign in on the standard page even with sign-up off; the box's first use makes the
owner's admin account and signs them in, the box's own login given a random password of root's; once an admin
account can sign in, the admin's routes send a browser to that sign-in, never the box's own login prompt (a
script's login still passes); the header shows one account button, and Admin where it is useful.
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
tok, me = A.claim_admin("tom", "password-one", "10.0.0.1")
check("first use: the owner's admin account made and signed in", me["role"] == "admin" and A.session(tok)["name"] == "tom" and A.admin_ready())
check("  admins sign in with sign-up off", A.login("tom", "password-one")[1]["name"] == "tom")
A.make("ann", "user")
data = A._load(); data["accounts"]["ann"]["hash"] = A.hash_password("password-two"); A._save(data)
check("  a user's account does not, with sign-up off", refused(A.login, "ann", "password-two", says="no accounts"))
check("  a wrong admin password is refused, as always", refused(A.login, "tom", "wrong-one", says="don't match"))
tok2, me2 = A.claim_admin("Ann", "password-three")
check("the console's reset and first use again: an existing name made admin with the new password", me2["role"] == "admin"
      and A.login("ann", "password-three")[1]["role"] == "admin")
check("  the name rules hold ('admin' is kept for the box's own login)", refused(A.claim_admin, "admin", "password-four", says="kept"))
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
try:
    code, page, _ = req("/", raw=True)
    check("the header: Sign in for a guest, and Admin while no admin account can sign in (its first use)", code == 200
          and "👤</span> Sign in" in page and 'href="/admin/"' in page, page[:300])
    code, d, h = req("/admin/setup", {"name": "owner", "password": "pw-of-the-owner"})
    cookie = (h.get("Set-Cookie") or "").split(";")[0]
    reqs = [json.loads(p.read_text()) for p in (st / "control" / "requests").glob("*.json")] if (st / "control" / "requests").is_dir() else \
           [json.loads(p.read_text()) for p in st.rglob("requests/*.json")]
    pw_req = [r for r in reqs if r.get("action") == "password"]
    check("first use over the page: the admin account made, signed in, the box's own login a random password, the gates asked to follow",
          code == 202 and cookie.startswith("irate_session=") and pw_req and pw_req[0]["setup"] is True and pw_req[0]["password"] != "pw-of-the-owner"
          and len(pw_req[0]["password"]) >= 24 and any(r.get("action") == "admin-gate" for r in reqs), (code, d, [r.get("action") for r in reqs]))
    code, page, _ = req("/", raw=True, headers={"Cookie": cookie})
    check("  the header for the admin: My account and Admin", "My account" in page and 'href="/admin/"' in page)
    code, page, _ = req("/", raw=True)
    check("  for a guest now: Sign in, and no Admin (admins sign in where everyone does)", "👤</span> Sign in" in page and 'href="/admin/"' not in page)
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
