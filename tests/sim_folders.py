# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Folders of apps, against a hub it starts: /admin/folders lists
each list page's entries; hiding one takes it off that folder's page only; an entry put in another
folder shows there too; a folder's own order is followed; a folder with nothing shown loses its
tile; only real folders and entries are taken. python3 tests/sim_folders.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
state = tempfile.mkdtemp(prefix="folders-")
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
    def go(method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {"Host": f"127.0.0.1:{port}", "X-Irate-Front": SECRET, "X-Irate-Admin": "1", "Content-Type": "application/json",
             "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse(); return r.status, r.read().decode()
    st, body = go("GET", "/admin/folders")
    snap = json.loads(body)
    fol = {f["id"]: f for f in snap["folders"]}
    check("the list pages are the folders", {"meshtastic", "tools-rf", "tools-general", "tools-electronics"} <= set(fol), list(fol))
    rf = [e["href"] for e in fol["tools-rf"]["entries"]]
    air = next(h for h in rf if "lora-airtime" in h)
    check("an entry can be in two folders already (the airtime calculator)", air in [e["href"] for e in fol["meshtastic"]["entries"]])
    check("entries say what they are", all(e["kind"] in ("page", "download", "switches") for e in fol["tools-rf"]["entries"]))
    _, page = go("GET", "/tools-rf.html")
    check("the RF page lists the airtime calculator", "LoRa Airtime Calculator" in page)
    ohm = next(e["href"] for e in fol["tools-electronics"]["entries"] if "ohms-law" in e["href"])
    gen = [e["href"] for e in fol["tools-general"]["entries"]]
    st, body = go("POST", "/admin/folders", {"state": {"tools-rf": {"hidden": [air], "extra": [ohm]}, "tools-general": {"order": [gen[1], gen[0]]}}})
    check("the arrangement saved", st == 200, body[:200])
    _, page = go("GET", "/tools-rf.html")
    check("hidden from RF: gone from its page", "LoRa Airtime Calculator" not in page)
    _, page = go("GET", "/meshtastic.html")
    check("still in Meshtastic, where it wasn't hidden", "LoRa Airtime Calculator" in page)
    _, page = go("GET", "/tools-rf.html")
    check("put in RF from Electronics: there too", "Ohm" in page)
    _, page = go("GET", "/tools-electronics.html")
    check("and still in Electronics", "Ohm" in page)
    _, page = go("GET", "/tools-general.html")
    names = [n for n in (e["name"] for e in fol["tools-general"]["entries"][:2])]
    check("a folder's own order followed", page.find(names[1]) < page.find(names[0]), names)
    every = [e["href"] for e in fol["tools-electronics"]["entries"]]
    go("POST", "/admin/folders", {"state": {"tools-electronics": {"hidden": every}}})
    _, home = go("GET", "/")
    check("a folder with nothing shown: no tile", '<span class="name">Electronics</span>' not in home)
    go("POST", "/admin/folders", {"state": {}})
    _, home = go("GET", "/")
    check("and back", '<span class="name">Electronics</span>' in home)
    for bad in ({"nowhere": {"hidden": []}}, {"tools-rf": {"hidden": ["/not/an/entry"]}}, {"tools-rf": {"hidden": "x"}}, "x"):
        st, _ = go("POST", "/admin/folders", {"state": bad})
        check(f"refused: {str(bad)[:40]}", st == 400, st)
finally:
    hub.terminate()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
