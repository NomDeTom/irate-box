# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The page widths, against a hub it starts: /layout.css public and set from
the owner's settings, only the offered widths taken (45, 60, 80, 90, 100), the defaults 80 for
the pages and 45 for the shoutbox and forum. python3 tests/sim_widths.py"""
import http.client, json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
state = tempfile.mkdtemp(prefix="widths-")
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
    def go(method, path, headers=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request(method, path, body=body, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
        r = c.getresponse(); return r.status, r.getheader("Content-Type") or "", r.read().decode()
    page = {"X-Irate-Front": SECRET, "X-Irate-Admin": "1", "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
    st, ctype, css = go("GET", "/layout.css")
    check("/layout.css is public and CSS", st == 200 and ctype.startswith("text/css"), (st, ctype))
    check("the defaults: 80rem pages, 45rem shoutbox and forum",
          "--page-width: 80rem" in css and "--shout-width: 45rem" in css and "--board-width: 45rem" in css, css)
    st, _, body = go("POST", "/admin/settings", page, json.dumps({"page_width": 90, "board_width": 100}).encode())
    check("the owner sets them on /admin", st == 200 and json.loads(body)["page_width"] == 90, body[:200])
    _, _, css = go("GET", "/layout.css")
    check("/layout.css follows", "--page-width: 90rem" in css and "--board-width: 100rem" in css, css)
    for bad in (70, "80", True, -1, 1000):
        go("POST", "/admin/settings", page, json.dumps({"page_width": bad}).encode())
    _, _, css = go("GET", "/layout.css")
    check("anything but 45, 60, 80, 90 or 100 is refused", "--page-width: 90rem" in css, css)
    # The admin pages' width, apart from the hub's.
    check("the admin pages: their own width, as the hub's until chosen apart", "body.admin { --page-width: 80rem; }" in css, css)
    st, _, body = go("POST", "/admin/settings", page, json.dumps({"admin_width": 60}).encode())
    _, _, css = go("GET", "/layout.css")
    check("  set apart: the admin pages 60rem, the hub's still 90rem", st == 200 and "body.admin { --page-width: 60rem; }" in css
          and ":root { --page-width: 90rem;" in css, css)
    # The emoji pickers' set (Appearance): 13.1 by default, 15.0 or 16.0 by the owner's choice.
    check("the emoji set: 13.1 by default", "--emoji-set: 13.1;" in css, css)
    st, _, body = go("POST", "/admin/settings", page, json.dumps({"emoji_set": "16.0"}).encode())
    _, _, css = go("GET", "/layout.css")
    check("  set to 16.0, /layout.css follows", st == 200 and "--emoji-set: 16.0;" in css, css)
    for bad in ("17.0", 15.0, "x; } body { display: none", None):
        go("POST", "/admin/settings", page, json.dumps({"emoji_set": bad}).encode())
    _, _, css = go("GET", "/layout.css")
    check("  anything but 13.1, 15.0 or 16.0 refused (nothing written into the CSS)", "--emoji-set: 16.0;" in css, css)
finally:
    hub.terminate()
# A box that chose a width before the admin pages had their own keeps it for both, until set apart.
d = tempfile.mkdtemp()
probe = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from irate_box.hub import server
p = server.SETTINGS_FILE
p.parent.mkdir(parents=True, exist_ok=True)
for saved in ({"page_width": 100}, {"page_width": 100, "admin_width": 60}):
    p.write_text(json.dumps(saved))
    s = server.load_settings()
    print(s["page_width"], s["admin_width"])
"""
r = subprocess.run([sys.executable, "-c", probe, str(REPO)], env=dict(os.environ, HUB_STATE_DIR=d, HUB_ETC_DIR=d + "/etc"),
                   capture_output=True, text=True, timeout=60)
check("a width chosen before the split: the admin pages keep it too; once set apart, their own",
      r.stdout.split() == ["100", "100", "100", "60"], (r.stdout, r.stderr[-300:]))
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
