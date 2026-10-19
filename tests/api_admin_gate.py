# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's own guard on /admin, against a hub it starts with a front secret: /admin refused
without the front's header (anything on loopback), /admin changes refused without the
/admin page's header or from another origin, and a refused request's body never left on a
kept-alive connection to poison the next one, refused or not. python3 tests/api_admin_gate.py"""

import http.client
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
state = tempfile.mkdtemp(prefix="admin-gate-")
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "f" * 64
env = dict(
    os.environ, HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=state, HUB_ETC_DIR=state, PORT=str(port), HUB_BIND="127.0.0.1"
)
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


try:
    for _ in range(50):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)

    def go(method, path, headers=None, body=None):
        h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        r.read()
        return r.status

    front = {"X-Irate-Front": SECRET}
    page = {**front, "X-Irate-Admin": "1", "Content-Type": "application/json"}
    check("GET /admin without the front's header: 403", go("GET", "/admin/settings") == 403)
    check("GET /admin with a wrong one: 403", go("GET", "/admin/settings", {"X-Irate-Front": "0" * 64}) == 403)
    check("GET /admin with it: 200", go("GET", "/admin/settings", front) == 200)
    check("guest pages need neither", go("GET", "/status") == 200)
    check(
        "POST /admin without X-Irate-Admin: 403",
        go("POST", "/admin/settings", {**front, "Content-Type": "application/json"}, b"{}") == 403,
    )
    check(
        "POST /admin from another origin: 403",
        go("POST", "/admin/settings", {**page, "Origin": "http://evil.example"}, b"{}") == 403,
    )
    check(
        "POST /admin from a cross-site fetch: 403",
        go("POST", "/admin/settings", {**page, "Sec-Fetch-Site": "cross-site"}, b"{}") == 403,
    )
    check(
        "POST /admin from the page itself: 200",
        go(
            "POST",
            "/admin/settings",
            {**page, "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"},
            b"{}",
        )
        == 200,
    )
    # The administration pages have an origin of their own (their port): the page's own requests carry that
    # port in Host, and Origin must be that very origin. The hub's port (the origin of any page that
    # could have been scripted) does not do, nor a different port of the same host.
    admin_host = {"Host": "127.0.0.1:8095"}
    bare = {"X-Irate-Admin": "1", "Content-Type": "application/json"}  # a page's headers, without the front's secret
    check(
        "POST /admin from the administration origin itself (Origin and Host both its port): 200",
        go(
            "POST",
            "/admin/settings",
            {**page, **admin_host, "Origin": "http://127.0.0.1:8095", "Sec-Fetch-Site": "same-origin"},
            b"{}",
        )
        == 200,
    )
    check(
        "  from the hub's origin, same host, another port: 403",
        go(
            "POST",
            "/admin/settings",
            {**page, **admin_host, "Origin": "http://127.0.0.1:80", "Sec-Fetch-Site": "same-site"},
            b"{}",
        )
        == 403,
    )
    check(
        "  and by Origin alone, with the hub's host and port in the Host header: 403",
        go("POST", "/admin/settings", {**page, **admin_host, "Origin": "http://127.0.0.1"}, b"{}") == 403,
    )
    # The second layer: the hub refuses /admin unless the web server of the administration origin said so
    # (X-Irate-Front, a secret from a root-only file, which every other origin of the front clears). A client
    # cannot send it: not its absence, not a guess, not a copy of the header's name.
    for label, headers in (
        ("none", {}),
        ("an empty one", {"X-Irate-Front": ""}),
        ("a guess", {"X-Irate-Front": "0" * 64}),
        ("the secret with a letter changed", {"X-Irate-Front": SECRET[:-1] + "0"}),
        ("the secret's first half", {"X-Irate-Front": SECRET[:32]}),
        ("the secret in another header", {"X-Irate-Frontier": SECRET, "X-Forwarded-Front": SECRET}),
    ):
        check(
            f"/admin from the hub's origin ({label}): 403",
            go("GET", "/admin/settings", headers) == 403
            and go("POST", "/admin/settings", {**bare, **headers}, b"{}") == 403,
        )
    # A refused request with a body, then another on the same kept-alive connection.
    go("POST", "/admin/settings", {**front, "Content-Type": "application/json"}, b'{"store_save_ttl_hours": 7}')
    check(
        "after a refused POST, the next request on the connection is itself", go("GET", "/admin/settings", front) == 200
    )
    go("POST", "/admin/settings", {"Content-Type": "application/json"}, b'{"x": 1}')
    check("after a refused POST (no secret), likewise", go("POST", "/admin/settings", page, b"{}") == 200)
    # Every route that answers without reading the body: the body is read away
    # after the answer, so the next request on the connection is still itself.
    for method, path in (
        ("PUT", "/nothing-here"),
        ("DELETE", "/nothing-here"),
        ("PATCH", "/nothing-here"),
        ("GET", "/status"),
        ("OPTIONS", "/nothing-here"),
        ("POST", "/store/" + "x" * 300),
    ):
        go(method, path, {"Content-Type": "application/json"}, b'{"store_save_ttl_hours": 7}' * 40)
        check(
            f"after {method} {path[:20]} with a body it does not read, the next request is itself",
            go("GET", "/admin/settings", front) == 200,
        )
    # A large one is read away too (up to DRAIN_MAX), not answered by closing on a client still
    # sending; a chunked one, which the hub never reads, closes the connection.
    check("a 2 MB unread body: answered", go("PUT", "/nothing-here", {}, b"x" * (2 << 20)) == 404)
    check("and the next request is itself", go("GET", "/admin/settings", front) == 200)
    c.request(
        "PUT", "/nothing-here", body=iter([b"x" * 100]), headers={"Host": f"127.0.0.1:{port}"}, encode_chunked=True
    )
    r = c.getresponse()
    r.read()
    check("a chunked unread body closes the connection", r.will_close, r.getheaders())
    check("and the next request, on a new one, is itself", go("GET", "/admin/settings", front) == 200)
    # A guest-level change from another site, or from a sibling port
    # (same-site), is refused; one that says nothing of where it came from is not a browser's.
    msg = {"Content-Type": "application/json"}
    check(
        "POST /messages from a cross-site page: 403",
        go("POST", "/messages", {**msg, "Sec-Fetch-Site": "cross-site"}, b"{}") == 403,
    )
    check(
        "POST /messages from a sibling port (same-site): 403",
        go("POST", "/messages", {**msg, "Sec-Fetch-Site": "same-site"}, b"{}") == 403,
    )
    check(
        "POST /messages with another Origin: 403",
        go("POST", "/messages", {**msg, "Origin": "http://evil.example"}, b"{}") == 403,
    )
    check("PUT /api/v2/x cross-site: 403", go("PUT", "/api/v2/x", {"Sec-Fetch-Site": "cross-site"}, b"x") == 403)
    check(
        "POST /messages from this origin, or with no word of where from: not 403",
        go("POST", "/messages", {**msg, "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}, b"{}")
        != 403
        and go("POST", "/messages", msg, b"{}") != 403,
    )
    check("and the connection is still itself after each", go("GET", "/admin/settings", front) == 200)

    # The setup gate: "Finish setup" refused while the Security page's last scan
    # says a passwordless sudo rule names an account sshd lets in by password.
    import json as _json

    ctl = Path(state) / "control"
    ctl.mkdir(exist_ok=True)
    (ctl / "security.json").write_text(
        _json.dumps(
            {"findings": [{"id": "sudo-nopasswd", "status": "problem", "detail": "claude-temp: lyra.", "actions": []}]}
        )
    )
    same = {**page, "Origin": f"http://127.0.0.1:{port}", "Sec-Fetch-Site": "same-origin"}
    check(
        "Finish setup while the scan says sudo-nopasswd is a problem: 409",
        go("POST", "/admin/settings", same, b'{"setup_done": true}') == 409,
    )
    (ctl / "security.json").write_text(
        _json.dumps({"findings": [{"id": "sudo-nopasswd", "status": "warn", "detail": "", "actions": []}]})
    )
    check("  a warning only, or no scan: finished", go("POST", "/admin/settings", same, b'{"setup_done": true}') == 200)
    (ctl / "security.json").unlink()
    check("  no scan at all: finished", go("POST", "/admin/settings", same, b'{"setup_done": false}') == 200)

    # The security doctor asks the same of the running hub (probes, not code markers).
    sys.path.insert(0, str(REPO))
    from irate_box.root import secdoctor

    secdoctor._hub_port = lambda: port
    check("the doctor's drain probe: this hub drains an unread body", secdoctor.probe_drains() is True)
    check(
        "the doctor's JSON-size probe: this hub refuses a 300 KB JSON body and stays usable",
        secdoctor.probe_json_cap() is True,
    )
    # The hub reads no JSON over 256 KB; the connection stays usable after.
    big = b'{"x": "' + b"y" * (300 * 1024) + b'"}'
    check("a JSON body over 256 KB: 413", go("POST", "/messages", {"Content-Type": "application/json"}, big) == 413)
    check("and the next request is itself", go("GET", "/admin/settings", front) == 200)
    # The store answers without CORS headers.
    c.request("GET", "/api/saves", headers={"Host": f"127.0.0.1:{port}", "Origin": "http://evil.example"})
    r = c.getresponse()
    r.read()
    check(
        "the store sends no Access-Control-Allow-Origin",
        r.getheader("Access-Control-Allow-Origin") is None,
        r.getheaders(),
    )
    # Malformed input is a 400, not a crashed handler; the connection stays usable.
    check("a JSON array body: 400", go("POST", "/messages", {"Content-Type": "application/json"}, b"[1, 2]") == 400)
    check(
        "Content-Length that is not a number: 400",
        go("POST", "/messages", {"Content-Type": "application/json", "Content-Length": "abc"}, None) in (400, 411),
    )
    check("and the hub still answers", go("GET", "/status") == 200)
finally:
    hub.terminate()


# The address it binds: loopback unless HUB_BIND says otherwise (the web server in front is what the network reaches).
def listening_on(p):
    """The local address of the TCP listener on port p, from /proc/net/tcp, or None."""
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            rows = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            cols = row.split()
            addr, _, hexport = cols[1].rpartition(":")
            if cols[3] == "0A" and int(hexport, 16) == p:
                return addr
    return None


def start(**extra):
    p = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
    e = {k: v for k, v in os.environ.items() if k != "HUB_BIND"}
    e.update(HUB_FRONT_SECRET=SECRET, HUB_STATE_DIR=state, HUB_ETC_DIR=state, PORT=str(p), **extra)
    proc = subprocess.Popen(
        [str(REPO / "irate-box"), "server"], env=e, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    for _ in range(50):
        if listening_on(p):
            break
        time.sleep(0.1)
    return proc, p


for label, extra, want in (
    ("HUB_BIND unset: the hub listens on loopback only", {}, "0100007F"),
    ("  HUB_BIND set: the override is kept", {"HUB_BIND": "0.0.0.0"}, "00000000"),
):
    proc, p = start(**extra)
    try:
        got = listening_on(p)
    finally:
        proc.terminate()
    check(label, got == want, got)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
