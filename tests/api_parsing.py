# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What a guest can send that the hub must answer rather than drop, against a hub it starts: JSON
nested deeper than Python recurses, a negative Content-Length, a hue of 1e999, and a signature cookie that is
not ASCII. Each gets an answer, and the hub still answers after. python3 tests/api_parsing.py"""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from freeport import free_port

REPO = Path(__file__).resolve().parents[1]
state = tempfile.mkdtemp(prefix="parsing-")
port = free_port()
env = dict(os.environ, HUB_STATE_DIR=state, HUB_ETC_DIR=state, PORT=str(port), HUB_BIND="127.0.0.1")
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def raw(request, wait=5):
    """The status code of the answer to a request sent as bytes, or None if the hub closed without one."""
    with socket.create_connection(("127.0.0.1", port), timeout=wait) as s:
        s.sendall(request)
        got = b""
        try:
            while b"\r\n" not in got:
                chunk = s.recv(4096)
                if not chunk:
                    break
                got += chunk
        except TimeoutError:
            return "timeout"
    try:
        return int(got.split(b" ", 2)[1])
    except (IndexError, ValueError):
        return None


def post(path, body, extra=b""):
    head = f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
    return raw(head.encode() + extra + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)


try:
    for _ in range(300):  # up to 30 s: a busy CI runner can take longer than 5 s to start the hub
        try:
            if raw(f"GET /status HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode(), 1) == 200:
                break
        except OSError:
            time.sleep(0.1)
    alive = f"GET /status HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n".encode()

    deep = b"[" * 100_000 + b"]" * 100_000
    check("JSON nested 100,000 deep to the hub: an answer, 400", post("/messages", deep) == 400)
    check("... to the gallery: an answer, 400", post("/api/saves", deep) == 400)
    check(
        "a negative Content-Length: an answer at once, not a wait for the client to close",
        raw(f"POST /messages HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: -1\r\n\r\n".encode(), wait=3)
        not in (None, "timeout"),
    )
    status = post("/messages", b'{"name": "ann", "text": "hello", "hue": 1e999}')
    check("a hue of 1e999: the message taken, without a colour", status == 201, status)
    cookie = "Cookie: irate_admin_seen=9999999999.é\r\n".encode()
    check(
        "a signature cookie that is not ASCII: an answer, as a guest",
        raw(f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n".encode() + cookie + b"\r\n") == 200,
    )
    check("the hub answers after all of them", raw(alive) == 200)
finally:
    hub.terminate()
    hub.wait()
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
