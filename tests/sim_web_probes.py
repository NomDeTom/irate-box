# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Web add-ons asked past the web server and through it (webprobe.py): the Security doctor's points for one that
serves its pages on its own port with no sign-in (a warning on loopback, a problem where the box's other addresses
reach it), believes a forged proxy's headers, or lets any site call it; and for a web server that does not keep the
add-on's sign-in. The Box doctor's for one that does not answer through the web server. Add-ons and the front are
small servers on loopback; 127.0.0.2 stands for one of the box's other addresses. python3 tests/sim_web_probes.py"""

import json
import os
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="webprobe-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_APPS_D=str(T / "apps.d"))
for d in ("state", "etc", "apps.d"):
    (T / d).mkdir()
(T / "etc/access.json").write_text(json.dumps({"wiki": "private"}))
sys.path.insert(0, str(REPO))
from irate_box.msg import render
from irate_box.root import health, webprobe
from irate_box.root import secdoctor as sd

real_front_address = webprobe.front_address

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def serve(answer, host="0.0.0.0"):
    """A server answering with answer(handler) -> (status, headers, body); its port."""

    class H(BaseHTTPRequestHandler):
        def _go(self):
            status, headers, body = answer(self)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_OPTIONS = _go

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


OPEN = serve(lambda h: (200, {}, b"<html>pages</html>"))
OPEN_LOCAL = serve(lambda h: (200, {}, b"<html>pages</html>"), host="127.0.0.1")
FORGED = serve(lambda h: (200, {}, b"in") if h.headers.get("X-Forwarded-User") else (401, {}, b""), host="127.0.0.1")
CORS = serve(
    lambda h: (
        (204, {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Credentials": "true"}, b"")
        if h.command == "OPTIONS"
        else (401, {}, b"")
    ),
    host="127.0.0.1",
)
GUARDED = serve(lambda h: (401, {}, b""), host="127.0.0.1")
raw = socket.socket()
raw.bind(("127.0.0.1", 0))
raw.listen()
NOT_HTTP = raw.getsockname()[1]
threading.Thread(
    target=lambda: [c.sendall(b"NOTICE * :not http\r\n") or c.close() for c, _ in iter(raw.accept, None)], daemon=True
).start()
FRONT_GATE = serve(lambda h: (401, {}, b""), host="127.0.0.1")
FRONT_OPEN = serve(lambda h: (302, {"Location": f"http://127.0.0.1:{OPEN_LOCAL}/wiki/"}, b""), host="127.0.0.1")
FRONT_DOWN = serve(lambda h: (502, {}, b"bad gateway"), host="127.0.0.1")


def serve_ws(host="127.0.0.1"):
    """A WebSocket-only listener, as mosquitto's on 9001: the handshake gets 101; a plain GET, the connection closed
    with no answer (what a web server in front reports as a 502)."""

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Upgrade", "").lower() != "websocket":
                self.close_connection = True
                return
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
            self.end_headers()
            self.close_connection = True

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


WS = serve_ws()
# The web server in front of it: passes the handshake on (101), and a plain GET gets its 502.
FRONT_WS = serve(
    lambda h: (
        (101, {"Upgrade": "websocket", "Connection": "Upgrade"}, b"")
        if h.headers.get("Upgrade", "").lower() == "websocket"
        else (502, {}, b"bad gateway")
    ),
    host="127.0.0.1",
)
OTHER = ["127.0.0.2 (eth0)"]


def points(port, front=FRONT_GATE, app="wiki"):
    webprobe.front_address = lambda: ("127.0.0.1", front)
    return {i["key"]: i for i in sd.web_points({"id": app, "port": port, "path": "/wiki/"}, OTHER)}


got = points(OPEN)
check(
    "open on every address, access private: web-open, a problem",
    got.get("web-open", {}).get("status") == "problem",
    got,
)
check("  naming the address that reaches it", "127.0.0.2 (eth0)" in render(got["web-open"]["summary"]), got)
got = points(OPEN_LOCAL)
check("open on loopback only: web-open, a warning", got.get("web-open", {}).get("status") == "warn", got)
check("  said as on this box", "on this box" in render(got["web-open"]["summary"]), got)
got = points(OPEN_LOCAL, app="themes")
check("open, but a public app: nothing to say", "web-open" not in got, got)
got = points(FORGED)
check("in only with a proxy's headers: web-forged", list(got) == ["web-forged"], got)
got = points(CORS)
check("any site may call it with credentials: web-cors", list(got) == ["web-cors"], got)
check("  the answer as evidence", "Allow-Origin: *" in got["web-cors"]["evidence"][0], got)
got = points(GUARDED)
check("a sign-in of its own, no headers trusted, no cross-origin: nothing", got == {}, got)
got = points(NOT_HTTP)
check("a port that does not speak HTTP: left to the listener checks", got == {}, got)
check(
    "webprobe: a WebSocket route answers its handshake with 101, and a plain GET with nothing",
    webprobe.ask("127.0.0.1", WS, "GET", "/mqtt", websocket=True)["status"] == 101
    and not webprobe.ask("127.0.0.1", WS, "GET", "/mqtt")["http"],
)
lk = webprobe.leaks(WS, "/mqtt", [], False, websocket=True)
check("  past the web server, asked with the handshake: it opens with no sign-in", lk["http"] and lk["open"], lk)
webprobe.front_address = lambda: ("127.0.0.1", FRONT_GATE)
got = {
    i["key"]: i
    for i in sd.web_points({"id": "mqtt", "port": NOT_HTTP, "web_port": WS, "websocket": True, "path": "/mqtt"}, OTHER)
}
check("  the Security doctor asks web_port, not the broker's own port; a public route: nothing to say", got == {}, got)
got = points(GUARDED, front=FRONT_OPEN)
check(
    "the web server lets it open with no sign-in, access private: web-front, a problem",
    got.get("web-front", {}).get("status") == "problem" and "private" in render(got["web-front"]["summary"]),
    got,
)
check(
    "  following the web server's redirect to the app's own origin",
    f":{OPEN_LOCAL}/wiki/" in got["web-front"]["evidence"][0],
    got,
)

# The Box doctor: a running web add-on that does not answer through the web server.
(T / "apps.d/30-wiki.json").write_text(
    json.dumps(
        {"id": "wiki", "order": 30, "status": {"name": "Wiki", "port": OPEN, "path": "/wiki/", "unit": "kiwix.service"}}
    )
)
health.unit_props = lambda unit: {"ActiveState": "active"}
webprobe.front_address = lambda: ("127.0.0.1", FRONT_DOWN)
f = health.check_addon_front()
check(
    "Box doctor: a 502 through the web server while the add-on answers on its own port: the web server's route",
    [(x["id"], x["status"], x["summary"]["id"]) for x in f] == [("addon-front:wiki", "problem", "addon-front.route")],
    f,
)
check(
    "  run the installer again, no restart of the add-on",
    [a["choice"] for a in f[0]["actions"]] == ["rerun-install"],
    f,
)
check("  both answers as evidence", len(f[0]["evidence"]) == 2 and ": 200" in f[0]["evidence"][1], f)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    DEAD = s.getsockname()[1]  # closed again: the add-on answers nothing
(T / "apps.d/30-wiki.json").write_text(
    json.dumps(
        {"id": "wiki", "order": 30, "status": {"name": "Wiki", "port": DEAD, "path": "/wiki/", "unit": "kiwix.service"}}
    )
)
f = health.check_addon_front()
check(
    "Box doctor: a 502 through the web server and nothing on its own port either: the add-on, with a restart",
    [(x["id"], x["status"], x["summary"]["id"]) for x in f] == [("addon-front:wiki", "problem", "addon-front.down")]
    and any(a["choice"] == "unit-restart:kiwix.service" for a in f[0]["actions"])
    and str(DEAD) in render(f[0]["summary"]),
    f,
)
# Item 176: a WebSocket-only route (MQTT's /mqtt): a plain GET through the web server is a 502 though all is well.
mqtt = {"name": "MQTT broker", "port": NOT_HTTP, "web_port": WS, "path": "/mqtt", "unit": "mosquitto.service"}
(T / "apps.d/30-wiki.json").unlink()
(T / "apps.d/85-mqtt.json").write_text(json.dumps({"id": "mqtt", "order": 85, "status": {**mqtt, "websocket": True}}))
webprobe.front_address = lambda: ("127.0.0.1", FRONT_WS)
f = health.check_addon_front()
check(
    "Box doctor: a WebSocket route asked with the handshake, 101 through the web server: fine (item 176)",
    [(x["id"], x["status"]) for x in f] == [("addon-front:mqtt", "ok")] and "WebSocket" in f[0]["evidence"][0],
    f,
)
(T / "apps.d/85-mqtt.json").write_text(json.dumps({"id": "mqtt", "order": 85, "status": mqtt}))
f = health.check_addon_front()
check(
    "  the same route asked with a plain GET (no websocket in the manifest): its own listener asked on web_port",
    [(x["id"], x["status"]) for x in f] == [("addon-front:mqtt", "problem")]
    and f"127.0.0.1:{WS}/mqtt" in f[0]["evidence"][1],
    f,
)
(T / "apps.d/85-mqtt.json").unlink()
(T / "apps.d/30-wiki.json").write_text(
    json.dumps(
        {"id": "wiki", "order": 30, "status": {"name": "Wiki", "port": OPEN, "path": "/wiki/", "unit": "kiwix.service"}}
    )
)
webprobe.front_address = lambda: ("127.0.0.1", FRONT_GATE)
f = health.check_addon_front()
check("Box doctor: a sign-in through the web server is working", [x["status"] for x in f] == ["ok"], f)
# Item 175: nothing answers on the web server's port at all (the Lyra, its hub moved to 8080, nothing on 80): the
# add-on's finding names the web server, with no offer to restart the add-on; and the port asked is the recorded one.
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    SILENT = s.getsockname()[1]  # closed again: nothing listens there
webprobe.front_address = lambda: ("127.0.0.1", SILENT)
f = health.check_addon_front()
check(
    "Box doctor: the web server answering nothing: a note naming it, not a restart of the add-on (item 175)",
    [(x["id"], x["status"]) for x in f] == [("addon-front:wiki", "warn")]
    and not f[0]["actions"]
    and str(SILENT) in render(f[0]["summary"]),
    f,
)
webprobe.front_address = real_front_address
webprobe.ETC.mkdir(parents=True, exist_ok=True)
(webprobe.ETC / "install-options").write_text("--repo\nx\n--port\n8080\n")
check("  the web server asked on the port the installer recorded (--port 8080)", webprobe.front_address()[1] == 8080)
(webprobe.ETC / "install-options").write_text("--repo\nx\n")
check("  and on 80 with none recorded", webprobe.front_address()[1] == 80)
health.unit_props = lambda unit: {"ActiveState": "inactive"}
check("Box doctor: an add-on that is not running is left to the units check", health.check_addon_front() == [])

sys.exit(1 if fails else 0)
