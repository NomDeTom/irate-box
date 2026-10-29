# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box's not-found and not-running pages, offline: a hub of this checkout answers a browser's request for an address
it does not have with the confused krab's page (404), a script's with nothing (404, as before); and the web servers'
own 404s and 502–504s go to the same static pages (nginx's error_page in the hub's server block, Caddy's
handle_errors in its snippet; an app switched off raises "error 404" on Caddy). python3 tests/sim_not_found.py"""

import http.client
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
from freeport import free_port

state = Path(tempfile.mkdtemp(prefix="not-found-"))
port = free_port()
env = dict(os.environ, HUB_STATE_DIR=str(state), HUB_ETC_DIR=str(state), PORT=str(port), HUB_BIND="127.0.0.1")
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def request(path, accept):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}", "Accept": accept})
    r = c.getresponse()
    return r.status, r.getheader("Content-Type") or "", r.read()


try:
    for _ in range(100):
        try:
            request("/status", "*/*")
            break
        except OSError:
            time.sleep(0.1)
    browser = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    status, kind, body = request("/no-such-page", browser)
    check(
        "a browser asking for an address the hub does not have: 404, the not-found page with the krab",
        status == 404
        and kind.startswith("text/html")
        and b"Not on this box" in body
        and b"/art/krab-confused.webp" in body,
        (status, kind, body[:120]),
    )
    status, kind, body = request("/no-such-page", "*/*")
    check(
        "  a script's request (fetch's */*): 404 with nothing, as before",
        status == 404 and body == b"",
        (status, body[:80]),
    )
finally:
    hub.terminate()
    hub.wait(timeout=10)
    shutil.rmtree(state, ignore_errors=True)

nginx = (REPO / "config/irate-box.nginx").read_text()
hub_block = nginx[nginx.index("server {") : nginx.index("server {", nginx.index("server {") + 1)]
check(
    "nginx: the hub's server block sends its own 404 to not-found.html and 502–504 to not-ready.html",
    "error_page 404 /not-found.html;" in hub_block and "error_page 502 503 504 /not-ready.html;" in hub_block,
)
caddy = (REPO / "config/Caddyfile").read_text()
snippet = caddy[caddy.index("(irate_box_hub) {") :]
snippet = snippet[: snippet.index("\n}\n")]
check(
    "Caddy: the hub's snippet answers its errors with the same pages",
    "handle_errors {" in snippet and "rewrite * /not-found.html" in snippet and "rewrite * /not-ready.html" in snippet,
)
sys.path.insert(0, str(REPO))
from irate_box.hub import access

off = access.caddy_snippets({"notes": "off"}, "hash")
check(
    "Caddy: an app switched off raises error 404 (caught by handle_errors), not respond 404",
    off["notes.caddy"].rstrip().endswith("error 404")
    and off["notes-off.caddy"].rstrip().endswith("error 404")
    and all("respond 404" not in text for text in off.values() if "notes" in text),
    {k: v for k, v in off.items() if k.startswith("notes")},
)
for page in ("not-found.html", "not-ready.html"):
    text = (REPO / "web" / page).read_text()
    check(f"web/{page}: the krab, and a way back to the hub", "/art/krab-confused.webp" in text and 'href="/"' in text)
check("web/art/krab-confused.webp is a WebP", (REPO / "web/art/krab-confused.webp").read_bytes()[8:12] == b"WEBP")

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
