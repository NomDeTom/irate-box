# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""For the browser tests: stands where the web server would, in front of a hub started for the test, adding what the
web server adds (its secret and the admin's mark), and serving the test's own pages under /__test/.
python3 front-proxy.py LISTEN_PORT HUB_HOST HUB_PORT TEST_DIR   (FRONT_SECRET in the environment)"""

import http.client
import http.server
import os
import socketserver
import sys
from pathlib import Path

listen, hub_host, hub_port, test_dir = int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), Path(sys.argv[4])
SECRET = os.environ["FRONT_SECRET"]
# The test's own pages, by name, read once: a request names one of these, never a path.
PAGES = {f.name: f.read_bytes() for f in test_dir.iterdir() if f.is_file() and f.suffix in (".html", ".js")}
# The hub's response headers passed on, by name; their values without line breaks.
PASSED = (
    "Content-Type",
    "Cache-Control",
    "Location",
    "Set-Cookie",
    "Content-Encoding",
    "ETag",
    "Last-Modified",
    "Content-Security-Policy",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Vary",
)


class Front(http.server.BaseHTTPRequestHandler):
    def _go(self):
        if self.path.startswith("/__test/"):
            name = self.path[len("/__test/") :].split("?")[0]
            data = PAGES.get(name)
            if data is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/html; charset=utf-8" if name.endswith(".html") else "application/javascript"
            )
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "x-irate-front", "x-irate-admin")}
        headers.update(
            {"Host": "localhost", "X-Irate-Front": SECRET, "X-Irate-Admin": "1", "X-Forwarded-For": "192.168.4.2"}
        )
        c = http.client.HTTPConnection(hub_host, hub_port, timeout=60)
        try:
            c.request(self.command, self.path, body, headers)
            r = c.getresponse()
            data = r.read()
        except OSError as exc:
            self.send_error(502, str(exc))
            return
        self.send_response(r.status)
        for name in PASSED:
            for v in r.headers.get_all(name) or ():
                self.send_header(name, v.replace("\r", "").replace("\n", ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_HEAD = _go

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


Server(("0.0.0.0", listen), Front).serve_forever()
