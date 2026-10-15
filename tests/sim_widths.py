# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The page widths, against a hub it starts: /layout.css public and set from
the owner's settings, only the offered widths taken (45, 60, 80, 90, 100), the defaults 80 for
the pages and 45 for the shoutbox and forum. python3 tests/sim_widths.py"""
import http.client, json, re, os, subprocess, sys, tempfile, time
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
        r = c.getresponse(); return r.status, r.getheader("Content-Type") or "", r.read().decode("utf-8", "replace")
    page = {"X-Irate-Front": SECRET, "X-Irate-Admin": "1", "Content-Type": "application/json",
            "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5); c.request("GET", "/", headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse(); r.read()
    check("the home page: never kept from before signing in or out (Cache-Control: no-cache)",
          r.status == 200 and r.getheader("Cache-Control") == "no-cache", (r.status, r.getheader("Cache-Control")))
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
    # The hub's name, the line under it, and the accent colour.
    _, _, home = go("GET", "/")
    _, _, css = go("GET", "/layout.css")
    check("the hub's words by default: Hub, and the connected line, with their message IDs; no accent in /layout.css",
          '<h1 data-i18n="index.header.heading">Hub</h1>' in home and '<title data-i18n="index.head.title">Hub</title>' in home
          and "connected to the network of the box" in home and "--accent" not in css)
    st, _, _ = go("POST", "/admin/settings", page, json.dumps({"hub_title": "Camp <Krab>", "hub_subtitle": "", "accent": "#ffd400"}).encode())
    _, _, home = go("GET", "/")
    check("  a name set: the heading and the tab, escaped; an empty line under it left out", st == 200
          and "<h1>Camp &lt;Krab&gt;</h1>" in home and "<title>Camp &lt;Krab&gt;</title>" in home and 'class="subtitle"' not in home, home[:600])
    _, _, css = go("GET", "/layout.css")
    check("  an accent set: the main themes only, with dark ink on a light accent",
          '[data-theme="dark"]' in css and "--accent: #ffd400; --accent-ink: #0f1117;" in css and "eink" not in css, css)
    for key, bad in (("accent", "red"), ("accent", "#FFD400; } body { display: none"), ("hub_title", "  "),
                     ("hub_title", "x" * 41), ("hub_subtitle", "a\nb")):
        go("POST", "/admin/settings", page, json.dumps({key: bad}).encode())
    _, _, home = go("GET", "/")
    _, _, css = go("GET", "/layout.css")
    check("  a bad colour, an empty or long name, a line break: refused", "--accent: #ffd400;" in css and "<h1>Camp &lt;Krab&gt;</h1>" in home, css)
    go("POST", "/admin/settings", page, json.dumps({"accent": "#1a237e"}).encode())
    _, _, css = go("GET", "/layout.css")
    check("  white ink on a dark accent", "--accent-ink: #fff;" in css, css)
    # The light and dark palettes: each mode its own, as rules for the main themes; the old accent cleared on Save.
    pal = {"light": {"accent": "#0f766e", "muted": "#5f6380"}, "dark": {"accent": "#2dd4bf", "bg": "#101418"}}
    st, _, _ = go("POST", "/admin/settings", page, json.dumps({"palette": pal, "accent": ""}).encode())
    _, _, css = go("GET", "/layout.css")
    dark_rule = 'html:root:not([data-palette])[data-theme="dark"] { --accent: #2dd4bf; --bg: #101418; --accent-ink: #0f1117; }'
    light_rule = 'html:root:not([data-palette])[data-theme="light"] { --accent: #0f766e; --muted: #5f6380; --accent-ink: #fff; }'
    check("palettes: a rule per mode, with its own accent's ink; the old accent gone", st == 200
          and dark_rule in css and light_rule in css and "#1a237e" not in css, css)
    check("  Auto: the dark one only where the device does not ask for light, the light one where it does",
          "@media not all and (prefers-color-scheme: light) { html:root:not([data-palette]):not([data-theme]) { --accent: #2dd4bf;" in css
          and "@media (prefers-color-scheme: light) { html:root:not([data-palette]):not([data-theme]) { --accent: #0f766e;" in css, css)
    check("  a visitor's own palette choice (standard, more contrast) left alone: every rule only without data-palette",
          all(":not([data-palette])" in line for line in css.splitlines() if "--accent" in line), css)
    check("  the other themes untouched", "eink" not in css and "cyber" not in css)
    for bad in ({"sepia": {"accent": "#000000"}}, {"dark": {"font": "#000000"}}, {"dark": {"accent": "#FFD400"}},
                {"dark": {"accent": "#000000; } body { display: none"}}, {"dark": "#000000"}, ["dark"]):
        go("POST", "/admin/settings", page, json.dumps({"palette": bad}).encode())
    _, _, css = go("GET", "/layout.css")
    check("  a mode, a role or a colour not allowed, or not as written: refused, the saved one kept",
          dark_rule in css and "display: none" not in css and "sepia" not in css and "font" not in css, css)
    go("POST", "/admin/settings", page, json.dumps({"palette": {}}).encode())
    _, _, css = go("GET", "/layout.css")
    check("  {}: the standard theme again", "--accent" not in css, css)
    # The favicon: none until uploaded; a PNG or an icon file by its first bytes; not SVG; removed again.
    import base64
    st, _, _ = go("GET", "/favicon.ico")
    check("no favicon at first: 404", st == 404, st)
    check("  and /layout.css names none: the pages keep their own emoji icons", "--favicon" not in go("GET", "/layout.css")[2])
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 40
    st, _, _ = go("POST", "/admin/picture", page, json.dumps({"which": "favicon", "data": base64.b64encode(png).decode()}).encode())
    st2, ctype, _ = go("GET", "/favicon.ico")
    check("  uploaded: /layout.css names it, with its time (themes.js puts it over the pages' own icons)",
          re.search(r'--favicon: "\d+"', go("GET", "/layout.css")[2]) is not None, go("GET", "/layout.css")[2])
    check("  a PNG uploaded: served as one, to anyone", st == 200 and st2 == 200 and ctype == "image/png", (st, st2, ctype))
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    bad = [go("POST", "/admin/picture", page, json.dumps({"which": "favicon", "data": d}).encode())[0]
           for d in (base64.b64encode(svg).decode(), "not base64!", base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 70000).decode())]
    _, ctype, _ = go("GET", "/favicon.ico")
    check("  an SVG, a broken upload, one too big: refused, the PNG kept", bad == [400, 400, 400] and ctype == "image/png", (bad, ctype))
    st = go("POST", "/admin/picture", {**page, "Origin": "http://evil.example"}, json.dumps({"which": "favicon", "remove": True}).encode())[0]
    check("  another site can't remove it", st == 403 and go("GET", "/favicon.ico")[0] == 200, st)
    go("POST", "/admin/picture", page, json.dumps({"which": "favicon", "remove": True}).encode())
    check("  removed: 404 again", go("GET", "/favicon.ico")[0] == 404)
    check("  and /layout.css names none again", "--favicon" not in go("GET", "/layout.css")[2])
    # The hub page's header picture: a WebP over the name, then gone again; not one given as a favicon's kind.
    webp = b"RIFF\x10\0\0\0WEBPVP8 " + b"\0" * 40
    st = go("POST", "/admin/picture", page, json.dumps({"which": "hub-art", "data": base64.b64encode(webp).decode()}).encode())[0]
    st2, ctype, _ = go("GET", "/hub-art")
    _, _, home = go("GET", "/")
    check("a header picture (WebP): served, and on the hub page over its name", st == 200 and st2 == 200 and ctype == "image/webp"
          and '<img class="hub-art" src="/hub-art?' in home and home.index('class="hub-art"') < home.index("<h1>"), (st, st2, ctype))
    ico = b"\x00\x00\x01\x00" + b"\0" * 20
    st = go("POST", "/admin/picture", page, json.dumps({"which": "hub-art", "data": base64.b64encode(ico).decode()}).encode())[0]
    st2 = go("POST", "/admin/picture", page, json.dumps({"which": "nope", "remove": True}).encode())[0]
    check("  an icon file as the header, or a picture with no name: refused", st == 400 and st2 == 400, (st, st2))
    go("POST", "/admin/picture", page, json.dumps({"which": "hub-art", "remove": True}).encode())
    _, _, home = go("GET", "/")
    check("  removed: the page without it", go("GET", "/hub-art")[0] == 404 and "hub-art" not in home)
    # The Krab art: on by default; off hides it everywhere through /layout.css.
    _, _, css = go("GET", "/layout.css")
    on = ".admin-art" not in css
    go("POST", "/admin/settings", page, json.dumps({"krab_art": False}).encode())
    _, _, css = go("GET", "/layout.css")
    check("the Krab art: on by default; off, /layout.css hides it", on and ".admin-art, .art-loop { display: none" in css, css)
    # What guests see: a notice at the top until its time, and who looks after the box at the foot.
    later = int(time.time()) + 3600
    go("POST", "/admin/settings", page, json.dumps({"notice_text": "Back <online> at 6", "notice_until": later, "keeper_contact": "Tom, on #lobby"}).encode())
    _, _, home = go("GET", "/")
    check("a notice: at the top of the hub page, escaped; who looks after it at the foot",
          '<p class="hub-notice" role="status">Back &lt;online&gt; at 6</p>' in home and "Looked after by: Tom, on #lobby" in home
          and home.index("hub-notice") < home.index("services"), home[:900])
    go("POST", "/admin/settings", page, json.dumps({"notice_until": int(time.time()) - 5}).encode())
    _, _, home = go("GET", "/")
    check("  its time past: gone by itself", "hub-notice" not in home and "Looked after by" in home)
    go("POST", "/admin/settings", page, json.dumps({"notice_until": 0, "keeper_contact": ""}).encode())
    _, _, home = go("GET", "/")
    check("  no end: there again; no contact, no footer", "hub-notice" in home and "Looked after by" not in home)
    # The Network page's changes hidden as old news: their ids kept; anything else refused.
    ok = ["handover:30-wifis-dhcp.yaml", "otherap:0c3bb1fa-0f6a-3707-8c8d-4e23598253db", "wifisync"]
    _, _, body = go("POST", "/admin/settings", page, json.dumps({"net_changes_hidden": ok}).encode())
    kept = json.loads(body).get("net_changes_hidden")
    for bad in (["something:else"], ["handover:../x y"], "wifisync", [1]):
        go("POST", "/admin/settings", page, json.dumps({"net_changes_hidden": bad}).encode())
    _, _, body = go("POST", "/admin/settings", page, b"{}")
    check("changes hidden on the Network page: kept by id, anything else refused", kept == ok and json.loads(body)["net_changes_hidden"] == ok, body[:300])
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
