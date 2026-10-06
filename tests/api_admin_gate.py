# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's own guard on /admin, against a hub it starts with a front secret: /admin refused
without the front's header (F27, F31: anything on loopback), /admin changes refused without the
/admin page's header or from another origin (S3), and a refused request's body never left on a
kept-alive connection to poison the next one (F2), refused or not. python3 tests/api_admin_gate.py"""
import http.client, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
state = tempfile.mkdtemp(prefix="admin-gate-")
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
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    def go(method, path, headers=None, body=None):
        h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
        c.request(method, path, body=body, headers=h); r = c.getresponse(); r.read(); return r.status
    front = {"X-Irate-Front": SECRET}
    page = {**front, "X-Irate-Admin": "1", "Content-Type": "application/json"}
    check("GET /admin without the front's header: 403", go("GET", "/admin/settings") == 403)
    check("GET /admin with a wrong one: 403", go("GET", "/admin/settings", {"X-Irate-Front": "0" * 64}) == 403)
    check("GET /admin with it: 200", go("GET", "/admin/settings", front) == 200)
    check("guest pages need neither", go("GET", "/status") == 200)
    check("POST /admin without X-Irate-Admin: 403", go("POST", "/admin/settings", {**front, "Content-Type": "application/json"}, b"{}") == 403)
    check("POST /admin from another origin: 403", go("POST", "/admin/settings", {**page, "Origin": "http://evil.example"}, b"{}") == 403)
    check("POST /admin from a cross-site fetch: 403", go("POST", "/admin/settings", {**page, "Sec-Fetch-Site": "cross-site"}, b"{}") == 403)
    check("POST /admin from the page itself: 200", go("POST", "/admin/settings", {**page, "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}, b"{}") == 200)
    # A refused request with a body, then another on the same kept-alive connection.
    go("POST", "/admin/settings", {**front, "Content-Type": "application/json"}, b'{"store_save_ttl_hours": 7}')
    check("after a refused POST, the next request on the connection is itself", go("GET", "/admin/settings", front) == 200)
    go("POST", "/admin/settings", {"Content-Type": "application/json"}, b'{"x": 1}')
    check("after a refused POST (no secret), likewise", go("POST", "/admin/settings", page, b"{}") == 200)
    # Every route that answers without reading the body (F2 properly): the body is read away
    # after the answer, so the next request on the connection is still itself.
    for method, path in (("PUT", "/nothing-here"), ("DELETE", "/nothing-here"), ("PATCH", "/nothing-here"),
                         ("GET", "/status"), ("OPTIONS", "/nothing-here"), ("POST", "/store/" + "x" * 300)):
        go(method, path, {"Content-Type": "application/json"}, b'{"store_save_ttl_hours": 7}' * 40)
        check(f"after {method} {path[:20]} with a body it does not read, the next request is itself",
              go("GET", "/admin/settings", front) == 200)
    # A large one is read away too (up to DRAIN_MAX), not answered by closing on a client still
    # sending; a chunked one, which the hub never reads, closes the connection.
    check("a 2 MB unread body: answered", go("PUT", "/nothing-here", {}, b"x" * (2 << 20)) == 404)
    check("and the next request is itself", go("GET", "/admin/settings", front) == 200)
    c.request("PUT", "/nothing-here", body=iter([b"x" * 100]), headers={"Host": f"127.0.0.1:{port}"}, encode_chunked=True)
    r = c.getresponse(); r.read()
    check("a chunked unread body closes the connection", r.will_close, r.getheaders())
    check("and the next request, on a new one, is itself", go("GET", "/admin/settings", front) == 200)
    # F15: the hub reads no JSON over 256 KB; the connection stays usable after.
    big = b'{"x": "' + b"y" * (300 * 1024) + b'"}'
    check("a JSON body over 256 KB: 413", go("POST", "/messages", {"Content-Type": "application/json"}, big) == 413)
    check("and the next request is itself", go("GET", "/admin/settings", front) == 200)
    # F20: the store answers without CORS headers.
    c.request("GET", "/api/saves", headers={"Host": f"127.0.0.1:{port}", "Origin": "http://evil.example"})
    r = c.getresponse(); r.read()
    check("the store sends no Access-Control-Allow-Origin", r.getheader("Access-Control-Allow-Origin") is None, r.getheaders())
finally:
    hub.terminate()
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
