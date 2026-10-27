# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Sign-in and identity: the levels and what each buys, played out (more guesses a day for the looser, longer waits for
a known device for the stricter); how a returning device is known (its device cookie, its address, either, both); a
changed password ending every device cookie; a common password counting as several failures; adaptive a step looser
when people keep needing their tries and a step tighter for what is under attack; and, against a hub it starts, the
device cookie set at sign-in, the levels' numbers for the page, and each person's storage share.
python3 tests/sim_signin.py"""

import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="signin-"))
os.environ.update(HUB_STATE_DIR=str(T / "lib"))
sys.path.insert(0, str(REPO))
from freeport import free_port

from irate_box.hub import accounts as A

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# What each level buys.
nums = {lv: A.preset_numbers(lv) for lv in A.LADDER}
g = [nums[lv]["guesses_a_day"] for lv in A.LADDER]
w = [nums[lv]["known_wait"] for lv in A.LADDER]
check(
    "looser levels: more guesses a day for an attacker; stricter: fewer",
    g == sorted(g, reverse=True) and len(set(g)) == 5,
    g,
)
check("  and a longer wait for a known device's own mistakes the stricter it is", w == sorted(w), w)
check(
    "  the default, Standard: under 100 guesses a day at one account, whatever the devices",
    nums["standard"]["guesses_a_day"] < 100,
    nums,
)

settings = {"level": "standard", "adaptive": False, "common": True, "known_by": "cookie"}
A.SIGNIN = lambda: settings
A.set_settings(signup="open")
A.signup("alice", "a long pass phrase", "10.0.0.1")
_, _ = A.login("alice", "a long pass phrase", "10.0.0.1")  # her phone: an address the account knows from now on
mark = A.device_mark("alice")
now = time.time()


def known(how, addr, cookie):
    settings["known_by"] = how
    return A._known("alice", addr, cookie, now)


check(
    "by cookie: her cookie from anywhere is known, her address alone is not",
    known("cookie", "10.9.9.9", mark) and not known("cookie", "10.0.0.1", None),
)
check(
    "by address: her address is known, a cookie alone is not",
    known("device", "10.0.0.1", None) and not known("device", "10.9.9.9", mark),
)
check("either: one is enough", known("either", "10.9.9.9", mark) and known("either", "10.0.0.1", None))
check(
    "both: only together",
    known("both", "10.0.0.1", mark) and not known("both", "10.9.9.9", mark) and not known("both", "10.0.0.1", None),
)
check(
    "a cookie for another account, or forged: not known",
    not known("cookie", "10.0.0.1", "bob." + mark.split(".")[1]) and not known("cookie", "", "alice.0000"),
)
settings["known_by"] = "cookie"
tok, _ = A.login("alice", "a long pass phrase", "10.0.0.1")
A.change_password(tok, "a long pass phrase", "another long phrase", "10.0.0.1")
check(
    "a changed password ends every device cookie",
    not A._mark_ok("alice", mark) and A._mark_ok("alice", A.device_mark("alice")),
)

# A common password counts as several failures.
A._fails.clear()
for _ in range(2):
    try:
        A.login("alice", "password", "10.5.0.1")
    except A.AccountError:
        pass
check(
    "two wrong passwords that are common: counted as six",
    len(A._fails[("pair", "alice\0" + "10.5.0.1")]) == 2 * A.COMMON_WEIGHT,
)
settings["common"] = False
A._fails.clear()
try:
    A.login("alice", "password", "10.5.0.1")
except A.AccountError:
    pass
check("  off: counted as one", len(A._fails[("pair", "alice\0" + "10.5.0.1")]) == 1)
settings["common"] = True

# Adaptive: a step tighter for an account under attack, a step looser for a box whose people keep needing their tries.
settings["adaptive"] = True
A._fails.clear()
t0 = time.time()
for n in range(A.NAME_ATTACK):
    A._failed("alice", f"10.7.{n // 250}.{n % 250}", t0)
check(
    "an account tried from many unknown devices: a step tighter (Standard to Strict)",
    A._level(("name", "alice"), t0) == "strict",
)
check(
    "  its other devices, and other accounts, as they were",
    A._level(("name", "bob"), t0) == "standard" and A._level(("addr", "10.0.0.1"), t0) == "standard",
)
check("  the page says which account", A.adaptive_now(t0)["tight_names"] == ["alice"])
settings["level"] = "strict"
check("  from Strict, one step further: siege", A._level(("name", "alice"), t0) == "siege")
settings["level"] = "standard"
A._tight.clear()
A._fails.clear()
A._honest.clear()
for n in range(A.RELAX_AFTER):
    A._fails[("pair", f"carol{n}\0" + "10.8.0.1")] = [t0] * (A.PRESETS["standard"]["free"] - 1)
    A._came_right(f"carol{n}", "10.8.0.1", t0)
check(
    "people needing nearly all their tries, three times in a day: the whole box a step looser",
    A._level(None, t0) == "relaxed" and A.adaptive_now(t0)["relaxed_until"],
)
for n in range(A.DEVICE_ATTACK):
    A._failed(f"name{n}", "10.9.0.66", t0)
check(
    "  one device failing many names: that device a step tighter, and the box no longer looser",
    A._level(("addr", "10.9.0.66"), t0) == "strict" and A._level(None, t0) == "standard",
)
settings["adaptive"] = False
check("adaptive off: the owner's level everywhere", A._level(("addr", "10.9.0.66"), t0) == "standard")

# Against a hub: the device cookie, the levels' numbers, each person's storage share.
(T / "hub").mkdir()
port = free_port()
SECRET = "f" * 64
env = dict(
    os.environ,
    HUB_FRONT_SECRET=SECRET,
    HUB_STATE_DIR=str(T / "hub"),
    HUB_ETC_DIR=str(T / "hub"),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
    HUB_STORE_MAX_TOTAL=str(400_000),
)
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
try:
    for _ in range(150):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)

    def go(method, path, body=None, cookie=None, admin=False, account=False, at="10.1.0.1", raw=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        h = {
            "Host": f"127.0.0.1:{port}",
            "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}",
            "Sec-Fetch-Site": "same-origin",
            "X-Forwarded-For": at,
        }
        if cookie:
            h["Cookie"] = cookie
        if admin:
            h.update({"X-Irate-Front": SECRET, "X-Irate-Admin": "1"})
        if account:
            h["X-Irate-Account"] = "1"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        text = r.read().decode()
        try:
            return r.status, json.loads(text), r.getheader("Set-Cookie") or "", r.msg.get_all("Set-Cookie") or []
        except ValueError:
            return r.status, text, "", []

    go("POST", "/admin/accounts", {"action": "settings", "signup": "open"}, admin=True)
    st, _, _, cookies = go(
        "POST", "/api/account", {"action": "signup", "name": "dana", "password": "her long pass phrase"}, account=True
    )
    dev = next((c.split(";")[0] for c in cookies if c.startswith("irate_device=dana.")), "")
    check(
        "signing in sets a device cookie for the account, HttpOnly, for 90 days",
        st == 200
        and dev
        and any("Max-Age=7776000" in c and "HttpOnly" in c for c in cookies if c.startswith("irate_device=")),
        cookies,
    )
    for n in range(12):
        go(
            "POST",
            "/api/account",
            {"action": "login", "name": "dana", "password": "wrong guess " + str(n)},
            account=True,
            at=f"10.2.0.{n}",
        )
    st, d, _, _ = go(
        "POST",
        "/api/account",
        {"action": "login", "name": "dana", "password": "her long pass phrase"},
        account=True,
        at="10.3.0.1",
    )
    check("after strangers' wrong passwords, a new browser waits", st == 429, (st, d))
    st, d, _, _ = go(
        "POST",
        "/api/account",
        {"action": "login", "name": "dana", "password": "her long pass phrase"},
        cookie=dev,
        account=True,
        at="10.3.0.2",
    )
    check("  her own browser (its device cookie) signs in at once", st == 200, (st, d))
    _, d, _, _ = go("GET", "/admin/signin", admin=True)
    check(
        "the page gets each level's numbers from the hub",
        d["levels"]["standard"] == A.preset_numbers("standard") and "adaptive" in d,
        d,
    )
    st, d, _, _ = go("POST", "/admin/settings", {"signin_level": "lax"}, admin=True)
    check(
        "a level that is not one: not taken (the settings keep only valid values)",
        d.get("signin_level") == "standard",
        d.get("signin_level"),
    )
    # The administrator's password choices reach sign-up.
    go("POST", "/admin/settings", {"signin_min_length": 15}, admin=True)
    st, d, _, _ = go(
        "POST", "/api/account", {"action": "signup", "name": "erin", "password": "tide pool 12"}, account=True
    )
    check(
        "shortest password set to 15 by the administrator: a 12-character one refused at sign-up",
        st == 400 and d["message"]["id"] == "accounts.password.length" and d["message"]["args"]["least"] == 15,
        (st, d),
    )
    go("POST", "/admin/settings", {"signin_min_length": 8}, admin=True)
    st, d, _, _ = go(
        "POST", "/api/account", {"action": "signup", "name": "erin", "password": "password1"}, account=True
    )
    check(
        "  one of the commonest: refused (the default)",
        st == 400 and d["message"]["id"] == "accounts.password.common",
        (st, d),
    )
    # The store's share: 1 MB (the hub's setting), a quarter each a day by default.
    go("POST", "/admin/settings", {"store_max_total_mb": 1}, admin=True)
    blob = b"x" * 100_000
    codes = [go("POST", "/api/v2/scenes", raw=blob, at="10.4.0.1")[0] for _ in range(2)]
    st, d, _, _ = go("POST", "/api/v2/scenes", raw=blob, at="10.4.0.1")
    check(
        "one person's writes past a quarter of the store in a day: refused, said so",
        codes == [201, 201]
        and st == 429
        and d.get("message", {}).get("id") == "store.share.used"
        and "(0.2 MB)" in d.get("error", ""),
        (codes, st, d),
    )
    check("  another person still writes", go("POST", "/api/v2/scenes", raw=blob, at="10.4.0.2")[0] == 201)
    check(
        "  the administrator has no share", go("POST", "/api/v2/scenes", raw=blob, at="10.4.0.1", admin=True)[0] == 201
    )
    go("POST", "/admin/settings", {"storage_share": "off"}, admin=True)
    check("  shares off: written", go("POST", "/api/v2/scenes", raw=blob, at="10.4.0.1")[0] == 201)
finally:
    hub.terminate()
    hub.wait()
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
