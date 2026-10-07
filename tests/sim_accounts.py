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
A.change("x0x", "delete")
check("delete: gone; the list shows no hash", "x0x" not in [a["name"] for a in A.listing()] and all("hash" not in a for a in A.listing()))
check("counts", A.counts() == {"asked": 4, "user": 3, "disabled": 0, "admins": 1}, A.counts())
check("bad settings refused", refused(A.set_settings, "everyone") and refused(A.set_settings, None, "sometimes"))

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
            return resp.status, json.loads(resp.read()), resp.headers
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
finally:
    hub.terminate()
    hub.wait()
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
