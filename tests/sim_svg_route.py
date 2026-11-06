# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""SVG-Edit at /svg/: its own origin, the off switch, the gallery without the hub's cookie, and
the policy that keeps a hostile SVG from running.

The policy is the defence, not SVG-Edit's sanitizer: scripts are limited to the app's own path,
with no 'unsafe-inline' or 'unsafe-eval', so an SVG's <script>, on*= handler and javascript: link
do not run; and a save's state comes back as JSON, so a <script href="/api/saves/<id>"> is not a
script either. python3 tests/sim_svg_route.py"""

import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import access, store
from irate_box.root import secdoctor, tls

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


plain = {path.name: content for path, content in tls._plain_files("http", "allow").items()}
text = (REPO / "config" / "irate-box.nginx").read_text()
text = re.sub(
    r"(?m)^\s*include\s+\S*/((?:nginx)-(?:http|policy)-[a-z]+\.inc);?\s*$",
    lambda match: plain[match[1]],
    text,
)
for token, value in (
    ("PORT", "80"),
    ("ADDON_PORT", "8090"),
    ("NOTES_PORT", "8091"),
    ("WIKI_PORT", "8092"),
    ("GIT_PORT", "8093"),
    ("NOTEBOOK_PORT", "8094"),
    ("ADMIN_PORT", "8095"),
    ("DRAW_PORT", "8096"),
    ("MERMAID_PORT", "8097"),
    ("SVG_PORT", "8099"),
    ("SYNC_PORT", "8098"),
    ("TLS_PORT", "443"),
    ("ADDON_TLS_PORT", "8490"),
    ("NOTES_TLS_PORT", "8491"),
    ("WIKI_TLS_PORT", "8492"),
    ("GIT_TLS_PORT", "8493"),
    ("NOTEBOOK_TLS_PORT", "8494"),
    ("ADMIN_TLS_PORT", "8495"),
    ("DRAW_TLS_PORT", "8496"),
    ("MERMAID_TLS_PORT", "8497"),
    ("SVG_TLS_PORT", "8499"),
    ("SYNC_TLS_PORT", "8498"),
):
    text = text.replace(f"@{token}@", value)
servers = {tuple(ports): block for ports, block in secdoctor._nginx_servers(text)}
hub, svg = servers.get(("80",), ""), servers.get(("8099",), "")
raw = (REPO / "config" / "irate-box.nginx").read_text()

check(
    "the hub's port sends /svg to the SVG-Edit origin and keeps the off switch",
    "location = /svg { return 302 $scheme://$host:$irate_box_svg_port/svg/; }" in hub
    and "if ($irate_box_off_svg) { return 404; }" in hub
    and "return 302 $scheme://$host:$irate_box_svg_port$request_uri;" in hub,
)
check(
    "8099 is a server block of its own, with the TLS and policy includes and the access map",
    svg
    and "listen 8099;" in svg
    and "include @TLS@/nginx-svg.conf*;" in svg
    and "include @ACCESS@;" in svg
    and "include @TLS@/nginx-http-svg.inc;" in raw
    and "include @TLS@/nginx-policy-svg.inc;" in raw,
    bool(svg),
)
check(
    "  the app is served from its installed folder behind the access switch",
    "alias @APPS@/svg/;" in svg
    and "try_files $uri /svg/index.html;" in svg
    and "auth_basic $irate_box_auth_svg;" in svg
    and "include @ACCESS@.d/gate-svg.conf*;" in svg,
)
check(
    "  and off is a 404",
    "if ($irate_box_off_svg) { return 404; }" in svg,
)
check(
    "  the gallery is proxied, scoped to svgedit, without the hub's session cookie",
    "location ^~ /api/saves {" in svg
    and 'proxy_set_header X-Irate-App "svg";' in svg
    and "proxy_set_header Cookie $irate_box_app_cookie;" in svg
    and 'proxy_set_header X-Irate-Front "";' in svg
    and "add_header X-Content-Type-Options nosniff always;" in svg
    and "proxy_pass http://irate_box_hub;" in svg,
)
check(
    "  and nothing else of the hub: no scenes, rooms or relay",
    "/api/v2" not in svg and "/socket.io" not in svg,
)
check(
    "  the link back to the hub is the hub's own file under /svg/, where the policy admits it",
    "location = /svg/hub-return.js {" in svg and "alias @STATIC@/hub-return.js;" in svg,
)

csp = re.search(r'set \$irate_box_svg_csp "([^"]+)";', svg)
csp = csp.group(1) if csp else ""
directives = dict((part.split(" ", 1)[0], part.split(" ", 1)[1]) for part in csp.split("; ") if " " in part)
script = directives.get("script-src", "")
check(
    "the policy limits scripts to this app's own path and port, never 'self'",
    script == "$scheme://$host:$irate_box_svg_port/svg/",
    script,
)
check(
    "  no inline script and no eval: an SVG's <script>, on*= and javascript: link do not run",
    "'unsafe-inline'" not in script and "'unsafe-eval'" not in script and "default-src 'self'" in csp,
)
check(
    "  no objects, no foreign frames, no <base> or form escape",
    directives.get("object-src") == "'none'"
    and directives.get("frame-src") == "'self'"
    and directives.get("base-uri") == "'none'"
    and directives.get("form-action") == "'self'",
    directives,
)
check(
    "  the hub's origin may frame it, named explicitly",
    directives.get("frame-ancestors") == "'self' $scheme://$host:$irate_box_hub_port $scheme://$host",
    directives.get("frame-ancestors"),
)
check(
    "  connect-src is this origin alone",
    directives.get("connect-src") == "'self'",
    directives.get("connect-src"),
)

hostile = (
    '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
    "<script>alert(1)</script><foreignObject><body onload='alert(1)'>x</body></foreignObject>"
    '<a href="javascript:alert(1)">x</a></svg>'
)
check(
    "the hostile SVG's vectors are each covered by a directive",
    "<script>" in hostile
    and "onload=" in hostile
    and "foreignObject" in hostile
    and "javascript:" in hostile
    and "'unsafe-inline'" not in script
    and directives.get("frame-src") == "'self'",
)


class Handler:
    """A request for the store, recording the headers it answers with."""

    def __init__(self):
        self.command = "GET"
        self.headers = {}
        self.path = "/api/saves"
        self.code = None
        self.sent = {}
        self.body = b""

    def send_response(self, code):
        self.code = code

    def send_header(self, key, value):
        self.sent[key] = value

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self.body += data


T = Path(tempfile.mkdtemp(prefix="svg-route-"))
store.VIEWER = None
store.ADMIN = None
s = store.Store(T / "store")
saved = s.save("svgedit", "hostile", json.dumps(hostile).encode())
handler = Handler()
store.handle(handler, "GET", f"/api/saves/{saved['id']}", s)
check(
    "a save whose state is a hostile SVG comes back as JSON, not as a script",
    handler.code == 200
    and handler.sent.get("Content-Type") == "application/json"
    and handler.body.lstrip().startswith(b"{")
    and json.loads(handler.body)["state"] == hostile,
    (handler.code, handler.sent, handler.body[:80]),
)
check(
    "  and the policy does not admit /api/saves as a script source",
    "/api/saves" not in script,
)

check(
    "the app is public by default, like Excalidraw",
    access.ROUTED["svg"] == ("public", False) and access.defaults()["svg"] == "public",
)
conf = access.nginx_conf(access.defaults())
check(
    "  its access variables say public (no login, not off)",
    "set $irate_box_auth_svg off;" in conf and 'set $irate_box_off_svg "";' in conf,
)
snippets = access.caddy_snippets(access.defaults(), "x")
check(
    "  Caddy: a public snippet and no off snippet",
    "svg.caddy" in snippets and "error 404" not in snippets["svg.caddy"] and "svg-off.caddy" in snippets,
)
caddy = (REPO / "config" / "Caddyfile").read_text()
check(
    "Caddy: 8099 is a site of its own with the same policy and scoped gallery",
    "(irate_box_svg) {" in caddy
    and "script-src {scheme}://{host}:{irate_box_svg_port}/svg/" in caddy
    and "header_up X-Irate-App svg" in caddy
    and 'header_up Cookie "irate_session=[^;]*;?\\s*" ""' in caddy
    and "header_down X-Content-Type-Options nosniff" in caddy
    and "root * {$HUB_SVG_ROOT:" in caddy,
)
check(
    "TLS: the twin is 8499, so each scheme keeps its own origin",
    tls.PLAIN_PORTS["svg"] == 8099 and tls.TWINS["svg"] == 8499,
)
install = (REPO / "install.sh").read_text()
check(
    "install.sh names the ports and fills them into the front and the hub's record",
    "SVG_PORT=8099" in install and "SVG_TLS_PORT=8499" in install and "HUB_SVG_PORT=$SVG_PORT" in install,
)

import shutil

shutil.rmtree(T, ignore_errors=True)
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
