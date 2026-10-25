# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Requests the doctors send to a web add-on, to see how it answers: straight to its own port (past the web server
in front, where the hub's sign-ins are kept), and through the web server, as a guest's browser would. The Security
doctor asks whether it leaks past the front; the Box doctor whether it works through it.

Only GET and OPTIONS, with no body, no cookie and no password, and never the web server's secret: nothing sent can
change an add-on's data. At most LIMIT bytes of an answer are read, TIMEOUT seconds a request. Stdlib only."""

import http.client
import os
import socket
import ssl
from pathlib import Path

LIMIT = 64 << 10
TIMEOUT = 5
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))


def front_address():
    """The web server's plain address on this box: port 80, or the one the installer moved the hub to because port 80
    was taken (--port, recorded in install-options; the Lyra was on 8080 until 2026-10-25)."""
    try:
        opts = (ETC / "install-options").read_text().split("\n")
    except OSError:
        opts = []
    at = opts.index("--port") + 1 if "--port" in opts else len(opts)
    port = opts[at] if at < len(opts) else "80"
    return "127.0.0.1", int(port) if port.isdigit() and 0 < int(port) < 65536 else 80


# The headers a proxy adds, as a forged request would carry them: a guest's address, an administrator's name.
FORGED = {
    "X-Forwarded-For": "10.73.0.9",
    "X-Real-IP": "10.73.0.9",
    "X-Forwarded-User": "admin",
    "X-Remote-User": "admin",
    "Remote-User": "admin",
    "X-Forwarded-Proto": "https",
}
FOREIGN = "http://example.invalid"


def ask(host, port, method, path, headers=None, tls=False):
    """{http: True, status, headers (lower-case names), body (bytes, up to LIMIT)} for an HTTP answer;
    {http: False, error} when nothing answers, or what answers does not speak HTTP."""
    try:
        if tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE  # the box's own certificate, asked on loopback: what it says is the point
            conn = http.client.HTTPSConnection(host, port, timeout=TIMEOUT, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
        conn.request(method, path, headers={"Host": host, "User-Agent": "irate-box-doctor", **(headers or {})})
        r = conn.getresponse()
        body = r.read(LIMIT)
        conn.close()
        return {
            "http": True,
            "status": r.status,
            "headers": {k.lower(): v for k, v in r.getheaders()},
            "body": body,
        }
    except (OSError, http.client.HTTPException, socket.timeout) as exc:
        return {"http": False, "error": f"{type(exc).__name__}: {exc}"[:200]}


def ok(r):
    return r["http"] and 200 <= r["status"] < 300


def gate(r):
    """A sign-in asked for: 401, or a redirect to the hub's account page."""
    return r["http"] and (
        r["status"] == 401
        or (r["status"] in (301, 302, 303, 307, 308) and "account" in r["headers"].get("location", ""))
    )


def through_front(path, front=None):
    """GET path through the web server, following its redirects to an app's own origin (on loopback, up to three)
    but not to a sign-in. The last answer, and where it came from."""
    from urllib.parse import urlsplit

    host, port = front or front_address()
    tls, where = False, path
    r = {"http": False, "error": "not asked"}
    for _ in range(4):
        r = ask(host, port, "GET", where, tls=tls)
        if not r["http"] or r["status"] not in (301, 302, 303, 307, 308) or gate(r):
            break
        loc = urlsplit(r["headers"].get("location", ""))
        if loc.scheme not in ("", "http", "https"):
            break
        tls = loc.scheme == "https" if loc.scheme else tls
        port = loc.port or (port if not loc.scheme else (443 if tls else 80))
        where = (loc.path or "/") + (f"?{loc.query}" if loc.query else "")
    return r, f"{'https' if tls else 'http'}://{host}:{port}{where}"


def leaks(port, path, addresses, gated):
    """How an add-on answers on its own port, past the front. gated: the front asks for a sign-in (or keeps it
    off). {http: False} when its port does not speak HTTP; else {path, open, reach, forged, cors}:
    open: it serves its pages with no sign-in; reach: the box's own addresses (beside loopback) it answers on;
    forged: it serves them only when a request carries a proxy's headers; cors: it lets any site's page call it."""
    plain = ask("127.0.0.1", port, "GET", path)
    if not plain["http"]:
        return {"http": False, "error": plain["error"]}
    if plain["status"] == 404 and path != "/":
        path, plain = "/", ask("127.0.0.1", port, "GET", "/")
    reach = [a for a in addresses if ask(a, port, "GET", path)["http"]]
    forged = ask("127.0.0.1", port, "GET", path, FORGED)
    pre = ask("127.0.0.1", port, "OPTIONS", path, {"Origin": FOREIGN, "Access-Control-Request-Method": "POST"})
    allow = pre["headers"].get("access-control-allow-origin", "") if pre["http"] else ""
    creds = pre["http"] and pre["headers"].get("access-control-allow-credentials", "").lower() == "true"
    return {
        "http": True,
        "path": path,
        "status": plain["status"],
        "open": ok(plain) and bool(plain["body"]),
        "reach": reach,
        "forged": not ok(plain) and ok(forged),
        "cors": allow in ("*", FOREIGN) and (creds or gated),
        "allow": allow,
    }
