"""Unique visitors (menu overhaul M11; checklist 5g), offline: the helper counts each device once
a day and a week, from the leases and the neighbour table; a new day starts again with a new salt;
nothing but the numbers reaches its file (no address, no hash); and the hub, against a hub it starts,
writes the owner's switch for the root path unit and reads the counts only while counting is on.
python3 tests/sim_visitors.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
work = Path(tempfile.mkdtemp(prefix="visitors-"))
leases, arp, out = work / "dnsmasq.leases", work / "arp", work / "run" / "counts.json"
os.environ.update(VISITORS_LEASES=str(leases), VISITORS_ARP=str(arp), VISITORS_OUT=str(out))
sys.path.insert(0, str(REPO))
from irate_box.hub import visitors  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

A, B, C = "aa:bb:cc:00:00:01", "AA:BB:CC:00:00:02", "aa:bb:cc:00:00:03"
leases.write_text(f"1790000000 {A} 192.168.4.10 phone *\n1790000000 {B} 192.168.4.11 laptop *\n")
arp.write_text("IP address       HW type     Flags       HW address            Mask     Device\n"
               f"192.168.4.10     0x1         0x2         {A}     *        wlan0\n"
               f"192.168.4.12     0x1         0x2         {C}     *        wlan0\n"
               "192.168.4.13     0x1         0x0         00:00:00:00:00:00     *        wlan0\n")
seen = visitors.macs()
check("addresses from the leases and the neighbour table, each once, lower-case", seen == {A, B.lower(), C}, seen)
c = visitors.Counter()
day1 = time.mktime((2026, 10, 8, 12, 0, 0, 0, 0, -1))
r = c.sweep(seen, day1)
check("three devices today and this week", (r["day"], r["week"]) == (3, 3), r)
r = c.sweep(seen, day1 + 600)
check("seen again: still three", (r["day"], r["week"]) == (3, 3), r)
salt1 = c.windows["day"]["salt"]
r = c.sweep({A}, day1 + 86400)
check("the next day: one so far today, still three this week", (r["day"], r["week"]) == (1, 3), r)
check("  with a new day's salt", c.windows["day"]["salt"] != salt1)
r = c.sweep({C}, day1 + 7 * 86400)
check("the next week: both start again", (r["day"], r["week"]) == (1, 1), r)
visitors.write(r)
text = out.read_text()
check("the file holds the numbers and nothing else", set(json.loads(text)) == {"day", "week", "date", "week_of", "at"}, text)
check("  no address in it", not any(x.lower() in text.lower() for x in (A, B, C)))
check("  no hash in it", all(h.hex() not in text for w in c.windows.values() for h in w["seen"]))
check("  readable by the hub", oct(out.stat().st_mode & 0o777) == "0o644", oct(out.stat().st_mode))

# The hub: the switch for the root path unit, and the counts only while on.
state = work / "state"
state.mkdir()
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "f" * 64
env = dict(os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=str(state), HUB_ETC_DIR=str(state), PORT=str(port), HUB_BIND="127.0.0.1",
           HUB_VISITORS_COUNTS=str(out))
hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(50):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status"); break
        except OSError:
            time.sleep(0.1)
    def post(body):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/admin/settings", body=json.dumps(body).encode(), headers={"Host": f"127.0.0.1:{port}", "X-Irate-Front": SECRET,
                  "X-Irate-Admin": "1", "Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"})
        r = c.getresponse(); return r.status, json.loads(r.read())
    st, cur = post({})
    check("counting is on by default (Tom)", cur.get("visitor_counts") is True, cur)
    want = state / "visitors.want"
    check("  and the hub has said so for the path unit", want.exists() and want.read_text().strip() == "on", want.exists() and want.read_text())
    post({"visitor_counts": False})
    check("off: the switch says off", want.read_text().strip() == "off")
    post({"visitor_counts": True})
    check("on again: on", want.read_text().strip() == "on")
    post({"visitor_counts": "yes"})
    check("only true or false taken", want.read_text().strip() == "on")
finally:
    hub.terminate()
# The hub's reading of the counts, off and on (the function the People tile uses).
os.environ.update(HUB_STATE_DIR=str(state), HUB_VISITORS_COUNTS=str(out))
from irate_box.hub import server  # noqa: E402
server._settings["visitor_counts"] = True
check("the hub reads only the numbers", server.visitor_counts() == {"day": 1, "week": 1, "date": r["date"]}, server.visitor_counts())
server._settings["visitor_counts"] = False
check("off: none, even with a file left over", server.visitor_counts() is None)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
