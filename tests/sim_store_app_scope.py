# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Each app origin's gallery is its own kind: the fixed X-Irate-App header the web server sets
on /draw/, /mermaid/ and /svg/ scopes the list, read, thumbnail, rename, delete and write, and
the hub's own origin clears it. An SVG-Edit thumbnail is PNG bytes and no more than the cap.
python3 tests/sim_store_app_scope.py"""

import base64
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import store

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Clock:
    t = 0

    def ticks(self):
        Clock.t += 1
        return Clock.t


class Handler:
    """Enough of a request handler for store.handle: headers, a body to read, an answer to write."""

    def __init__(self, method="GET", headers=None, body=b"", path="/api/saves"):
        self.command = method
        self.headers = {**headers, "Content-Length": str(len(body))} if body else dict(headers or {})
        self.rfile = io.BytesIO(body)
        self.path = path
        self.code = None
        self.body = b""

    def send_response(self, code):
        self.code = code

    def send_header(self, *_):
        pass

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self.body += data


T = Path(tempfile.mkdtemp(prefix="store-app-scope-"))
s = store.Store(T / "store", Clock())

excalidraw = s.save("excalidraw", "a whiteboard", b"{}", thumb=b"\x89PNG\r\n\x1a\nx")
mermaid = s.save("mermaid", "a diagram", b"{}", thumb=b"<svg/>")
svgedit = s.save("svgedit", "a drawing", b"{}", thumb=b"\x89PNG\r\n\x1a\nx")
jspaint = s.save("jspaint", "a picture", b"{}")


def ids(handler):
    store.handle(handler, "GET", "/api/saves", s)
    return {row["id"] for row in json.loads(handler.body)["saves"]}


check(
    "the hub's own gallery (no app header): every kind",
    ids(Handler()) == {excalidraw["id"], mermaid["id"], svgedit["id"], jspaint["id"]},
)
check(
    "/draw/ sees only excalidraw rows",
    ids(Handler(headers={"X-Irate-App": "draw"})) == {excalidraw["id"]},
)
check(
    "/mermaid/ sees only mermaid rows",
    ids(Handler(headers={"X-Irate-App": "mermaid"})) == {mermaid["id"]},
)
check(
    "/svg/ sees only svgedit rows (its preview may be an SVG, as the other apps' may)",
    ids(Handler(headers={"X-Irate-App": "svg"})) == {svgedit["id"]},
)
check(
    "an unknown app header scopes nothing: never wider than the app it names",
    len(ids(Handler(headers={"X-Irate-App": "not-an-app"}))) == 4,
)

for app, mine, other in (
    ("draw", excalidraw, mermaid),
    ("mermaid", mermaid, svgedit),
    ("svg", svgedit, mermaid),
):
    h = Handler(headers={"X-Irate-App": app}, path=f"/api/saves/{mine['id']}")
    store.handle(h, "GET", h.path, s)
    check(f"/{app}/ opens its own save", h.code == 200, h.code)
    h = Handler(headers={"X-Irate-App": app}, path=f"/api/saves/{other['id']}")
    store.handle(h, "GET", h.path, s)
    check("  and not another app's: 404, not its name or state", h.code == 404, h.code)
    h = Handler(headers={"X-Irate-App": app}, path=f"/api/saves/{other['id']}/thumb")
    store.handle(h, "GET", h.path, s)
    check("  nor its thumbnail: 404", h.code == 404, h.code)

h = Handler("PATCH", {"X-Irate-App": "svg"}, json.dumps({"name": "renamed"}).encode(), f"/api/saves/{mermaid['id']}")
store.handle(h, "PATCH", h.path, s)
meta, _ = s.get_save(mermaid["id"])
check(
    "another app cannot rename a save: 404 and the name kept",
    h.code == 404 and meta["name"] == "a diagram",
    (h.code, meta["name"]),
)

h = Handler("DELETE", {"X-Irate-App": "draw"}, path=f"/api/saves/{svgedit['id']}")
store.handle(h, "DELETE", h.path, s)
check(
    "another app cannot delete a save: 404 and it is still there",
    h.code == 404 and s.get_save(svgedit["id"]) is not None,
)

# A write from an app origin keeps that app's kind, whatever the body says.
h = Handler(
    "POST",
    {"X-Irate-App": "draw"},
    json.dumps({"kind": "svgedit", "name": "sneaky", "state": "<svg/>"}).encode(),
)
store.handle(h, "POST", "/api/saves", s)
check(
    "a /draw/ write is stored as excalidraw, whatever kind the body names",
    h.code == 201 and json.loads(h.body)["kind"] == "excalidraw",
    h.body,
)

h = Handler(
    "POST",
    {"X-Irate-App": "draw"},
    json.dumps({"id": svgedit["id"], "kind": "excalidraw", "name": "stolen", "state": "{}"}).encode(),
)
store.handle(h, "POST", "/api/saves", s)
kept = s.get_save(svgedit["id"])
check(
    "an app cannot save over another kind's id: 404 and that row unchanged",
    h.code == 404 and kept[0]["kind"] == "svgedit" and kept[0]["name"] == "a drawing",
    (h.code, kept[0]),
)


def post_svg(thumb):
    body = json.dumps({"kind": "svgedit", "name": "drawing", "state": "<svg/>", "thumb": thumb}).encode()
    h = Handler("POST", {"X-Irate-App": "svg"}, body)
    store.handle(h, "POST", "/api/saves", s)
    return h


png = base64.b64encode(store.PNG_MAGIC + b"preview").decode()
check("an SVG-Edit PNG preview: saved", post_svg(png).code == 201)
check(
    "an SVG-Edit preview that is not PNG: 400, nothing stored",
    post_svg(base64.b64encode(b"<svg><script>alert(1)</script></svg>").decode()).code == 400,
)
check(
    "an SVG-Edit preview over the cap: 400",
    post_svg(base64.b64encode(store.PNG_MAGIC + b"x" * store.THUMB_MAX).decode()).code == 400,
)
check(
    "the next real save still works after the refusals",
    post_svg(png).code == 201,
)

# The app origins' proxies set the header and the hub's own clears it, in both web servers.
nginx = (REPO / "config" / "irate-box.nginx").read_text()
caddy = (REPO / "config" / "Caddyfile").read_text()
server = (REPO / "irate_box" / "hub" / "server.py").read_text()
check("nginx sets /draw/'s app header", nginx.count('proxy_set_header X-Irate-App "draw";') == 1)
check("nginx sets /mermaid/'s app header", nginx.count('proxy_set_header X-Irate-App "mermaid";') == 1)
check(
    "nginx clears it on the hub's own gallery and the admin's thumbnails",
    nginx.count('proxy_set_header X-Irate-App "";') >= 2,
)
check("Caddy sets /draw/'s app header", caddy.count("header_up X-Irate-App draw") == 1)
check("Caddy sets /mermaid/'s app header", caddy.count("header_up X-Irate-App mermaid") == 1)
check("Caddy clears it on the hub's own gallery and the admin's thumbnails", caddy.count("header_up -X-Irate-App") >= 2)
check("the hub drops the app header from an unvouched request too", '"X-Irate-App",' in server)

shutil.rmtree(T, ignore_errors=True)
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
