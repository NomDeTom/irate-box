# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A sign-in tied to its hotspot device, against a hub it starts with a stand-in hotspot (its network, its DHCP
leases): the session keeps the device's hardware address; its cookie works from that device, and from off the
hotspot, but not from the hotspot with another address; off (pin_sessions), the cookie alone. And whether the
hotspot could give each account a WiFi password of its own (ap.per_account), from what runs it.
python3 tests/sim_device_pin.py"""

import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from freeport import free_port

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
state = Path(tempfile.mkdtemp(prefix="device-pin-"))
leases = state / "leases"
leases.write_text(
    "1700000000 aa:aa:aa:aa:aa:01 10.9.0.11 alices-phone *\n1700000000 bb:bb:bb:bb:bb:02 10.9.0.22 someone *\n"
)
port = free_port()
SECRET = "f" * 64
env = dict(
    os.environ,
    HUB_FRONT_SECRET=SECRET,
    HUB_STATE_DIR=str(state),
    HUB_ETC_DIR=str(state),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
    HUB_AP_NET="10.9.0.0/24",
    HUB_LEASES=str(leases),
    HUB_ARP_TABLE=str(state / "no-arp"),
)
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


try:
    for _ in range(150):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)

    def go(method, path, body=None, cookie=None, admin=False, account=False, at="10.9.0.11"):
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
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        raw = r.read().decode()
        try:
            return r.status, json.loads(raw), (r.getheader("Set-Cookie") or "").split(";")[0]
        except ValueError:
            return r.status, raw, ""

    def me(cookie, at):
        return (go("GET", "/api/account", cookie=cookie, at=at)[1] or {}).get("me")

    go("POST", "/admin/accounts", {"action": "settings", "signup": "open"}, admin=True)
    st, d, alice = go(
        "POST",
        "/api/account",
        {"action": "signup", "name": "alice", "password": "correct horse staple 1"},
        account=True,
    )
    check("alice signs up on her phone, on the hotspot", st == 200 and alice, (st, d))
    sessions = json.loads((state / "accounts.json").read_text())["sessions"]
    check(
        "the session keeps her phone's hardware address, from the leases",
        [s.get("device") for s in sessions.values()] == ["aa:aa:aa:aa:aa:01"],
        sessions,
    )
    check("her cookie from her phone: signed in", (me(alice, "10.9.0.11") or {}).get("name") == "alice")
    check("the same cookie from another hotspot device: not taken", me(alice, "10.9.0.22") is None)
    check("from a hotspot address the box has no lease for: taken (nothing to tell)", me(alice, "10.9.0.99"))
    check("from off the hotspot (the owner's network): taken", me(alice, "192.168.1.50"))
    st, d, _ = go("POST", "/api/account", {"action": "logout"}, cookie=alice, account=True, at="10.9.0.22")
    check(
        "a sign-out sent with her cookie by another device: her session stays",
        (me(alice, "10.9.0.11") or {}).get("name") == "alice",
    )
    st, _, _ = go("POST", "/admin/settings", {"pin_sessions": False}, admin=True)
    check("off: the cookie alone, from any device", st == 200 and me(alice, "10.9.0.22"))
    go("POST", "/admin/settings", {"pin_sessions": True}, admin=True)
    _, _, bob = go(
        "POST",
        "/api/account",
        {"action": "signup", "name": "bob", "password": "correct horse staple 2"},
        account=True,
        at="192.168.1.60",
    )
    sessions = json.loads((state / "accounts.json").read_text())["sessions"]
    check(
        "a sign-in from off the hotspot keeps no device, and works from anywhere",
        sum(1 for s in sessions.values() if "device" not in s) == 1 and me(bob, "10.9.0.22"),
        sessions,
    )
finally:
    hub.terminate()
    hub.wait()

# Whether the hotspot could give each account a WiFi password of its own.
from irate_box.root import ap

new, old = state / "hostapd-new", state / "hostapd-old"
new.write_bytes(b"\x00AP-STA-CONNECTED %s keyid=%s\x00")
old.write_bytes(b"\x00AP-STA-CONNECTED %s\x00")
caps1, caps2 = {"ap_slots": 1}, {"ap_slots": 2}
wpa2 = {"mode": "sae", "allow_wpa2": True}
for name, args, possible, said in (
    ("NetworkManager runs the hotspot", ({"backend": "nm"}, wpa2, caps1, new), False, "hotspot-per-account.nm"),
    ("hostapd too old", ({"backend": "hostapd"}, wpa2, caps1, old), False, "hotspot-per-account.old"),
    ("no hostapd at all", ({"backend": "hostapd"}, wpa2, caps1, state / "none"), False, "hotspot-per-account.old"),
    ("hostapd, WPA3 with WPA2", ({"backend": "hostapd"}, wpa2, caps1, new), True, "hotspot-per-account.possible"),
    (
        "hostapd, open, one network",
        ({"backend": "hostapd"}, {"mode": "open"}, caps1, new),
        False,
        "hotspot-per-account.one-net",
    ),
    (
        "hostapd, open, room for a second",
        ({"backend": "hostapd"}, {"mode": "open"}, caps2, new),
        True,
        "hotspot-per-account.possible",
    ),
):
    got = ap.per_account(*args)
    check(
        f"per account: {name}: {'possible' if possible else 'not possible'}",
        got["possible"] is possible and got["said"]["id"] == said,
        got,
    )
got = ap.per_account({"backend": "hostapd"}, {"mode": "sae"}, caps1, new)
check(
    "  WPA3 only: possible, each password tied to its device's address, untested said",
    got["possible"] and {n["id"] for n in got["notes"]} == {"hotspot-per-account.wpa3", "hotspot-per-account.untested"},
    got,
)
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
