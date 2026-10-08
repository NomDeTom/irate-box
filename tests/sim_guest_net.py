# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's side of guests' onward internet (root/share.py), against a hub it starts: what a
guest on the hotspot is told; tapping through the sheet asks root to let that device out, and the
captive API then says "not captive" to it alone; users only refuses a guest not signed in; nothing
is let out from off the hotspot, nor when nothing is shared; the owner's level goes to root.
python3 tests/sim_guest_net.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
state = Path(tempfile.mkdtemp(prefix="guest-net-"))
(state / "control" / "requests").mkdir(parents=True)
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "f" * 64
env = dict(os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=str(state), HUB_ETC_DIR=str(state), PORT=str(port), HUB_BIND="127.0.0.1")
hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def level(lv):
    (state / "control" / "share.json").write_text(json.dumps({"level": lv}))
def go(method, path, body=None, addr="192.168.4.23", admin=False):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Host": f"127.0.0.1:{port}", "X-Irate-Front": SECRET, "X-Forwarded-For": addr, "Content-Type": "application/json",
         "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
    if admin:
        h["X-Irate-Admin"] = "1"
    c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
    r = c.getresponse(); raw = r.read().decode()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw
def requests():
    out = []
    for f in sorted((state / "control" / "requests").glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except ValueError:
            pass
    return out
try:
    for _ in range(50):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status"); break
        except OSError:
            time.sleep(0.1)
    st, d = go("GET", "/api/guest-net")
    check("nothing shared (no level kept): off; this device is on the hotspot", st == 200 and d["level"] == "off" and d["here"] is True and d["out"] is False, d)
    st, d = go("POST", "/api/guest-net", {"agree": True})
    check("  tapping through: refused, nothing to let out to", st == 409, (st, d))
    check("  the captive API: captive", go("GET", "/api/captive")[1]["captive"] is True)
    level("sheet-web")
    st, d = go("POST", "/api/guest-net", {})
    check("after the sheet: not without agreeing", st == 400, (st, d))
    st, d = go("POST", "/api/guest-net", {"agree": True})
    asked = [r for r in requests() if r.get("action") == "share-allow"]
    check("  agreeing asks root to let this device out", st == 202 and d.get("out") is True and asked and asked[-1]["ip"] == "192.168.4.23", (st, d, asked))
    check("  the captive API: not captive for this device", go("GET", "/api/captive")[1]["captive"] is False)
    check("  still captive for another", go("GET", "/api/captive", addr="192.168.4.24")[1]["captive"] is True)
    check("  /api/guest-net says it is out", go("GET", "/api/guest-net")[1]["out"] is True)
    st, d = go("POST", "/api/guest-net", {"agree": True}, addr="192.168.1.50")
    check("  a device not on the hotspot: refused", st == 400, (st, d))
    level("users-web")
    st, d = go("POST", "/api/guest-net", {"agree": True}, addr="192.168.4.30")
    check("users only: a guest not signed in is refused, and told to sign in", st == 403 and "sign in" in d.get("error", ""), (st, d))
    level("open")
    check("open: nobody captive", go("GET", "/api/captive", addr="192.168.4.99")[1]["captive"] is False)
    st, d = go("POST", "/admin/hotspot", {"action": "share", "level": "sheet-all"}, admin=True)
    check("the owner's level goes to root", st == 202 and any(r.get("action") == "share-set" and r.get("level") == "sheet-all" for r in requests()), (st, d))
    st, d = go("POST", "/admin/hotspot", {"action": "share", "level": "everyone"}, admin=True)
    check("  a level not offered: refused", st == 400, (st, d))
    st, d = go("GET", "/admin/hotspot", admin=True)
    check("  /admin/hotspot says the level", isinstance(d, dict) and d.get("share") == "open", d if not isinstance(d, dict) else d.get("share"))
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
