# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The front's headers are believed only beside its VIA_SECRET, and the visitor's address is an IP
from the forwarded list or the socket's peer, never an arbitrary name. Session cookies are checked
for shape and length before any lookup. python3 tests/sim_forwarded.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box import validate as V
from irate_box.hub import accounts, server

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Fake:
    _forwarded = server.Handler._forwarded
    _client_addr = server.Handler._client_addr
    _https = server.Handler._https

    def __init__(self, headers=None, client=("127.0.0.1", 40000), vouched=False):
        self.headers = headers or {}
        self.client_address = client
        self._vouched = vouched


real_secret = server.VIA_SECRET
try:
    server.VIA_SECRET = "s" * 32
    check("no forwarded header: the socket's peer", Fake()._client_addr() == "127.0.0.1")
    check(
        "vouched forwarded list: its first address",
        Fake({"X-Forwarded-For": "192.168.4.20, 10.0.0.1"}, vouched=True)._client_addr() == "192.168.4.20",
    )
    check(
        "unvouched forwarded list: the socket's peer",
        Fake({"X-Forwarded-For": "192.168.4.20"}, vouched=False)._client_addr() == "127.0.0.1",
    )
    check(
        "vouched but not an IP: the socket's peer",
        Fake({"X-Forwarded-For": "not-an-address"}, vouched=True)._client_addr() == "127.0.0.1",
    )
    check(
        "vouched IPv6: kept as an address",
        Fake({"X-Forwarded-For": "2001:db8::1"}, vouched=True)._client_addr() == "2001:db8::1",
    )
    check(
        "HTTPS only beside the vouched front",
        Fake({"X-Forwarded-For": "192.168.4.20", "X-Forwarded-Proto": "https"}, vouched=True)._https() is True,
    )
    check(
        "an unvouched proto is plain HTTP",
        Fake({"X-Forwarded-For": "192.168.4.20", "X-Forwarded-Proto": "https"}, vouched=False)._https() is False,
    )

    headers = {"X-Irate-Via": "wrong", "X-Forwarded-For": "192.168.4.20", "X-Forwarded-Proto": "https"}
    check("a wrong secret is not vouched", server.drop_unvouched(headers) is False)
    check("  its forwarded headers are gone", "X-Forwarded-For" not in headers and "X-Forwarded-Proto" not in headers)
    check("  and the secret is not repeated", "X-Irate-Via" not in headers)
    headers = {"X-Irate-Via": "s" * 32, "X-Forwarded-For": "192.168.4.20"}
    check("the right secret is vouched", server.drop_unvouched(headers) is True)
    check("  and the address stays", headers.get("X-Forwarded-For") == "192.168.4.20")
finally:
    server.VIA_SECRET = real_secret

server.VIA_SECRET = ""
check(
    "run bare: forwarded headers still believed, as before",
    Fake({"X-Forwarded-For": "192.168.4.20"})._client_addr() == "192.168.4.20",
)
server.VIA_SECRET = real_secret

# --- session tokens: shape and length before any lookup ---
for bad in ("", "short", "a" * 19, "a" * 101, "a" * 20 + "\n", "a" * 20 + "/", None, b"a" * 30):
    try:
        V.token(bad)
        check(f"token refused: {bad!r}", False)
    except ValueError:
        check(f"token refused: {str(bad)[:30]!r}", True)
check("a token_urlsafe-shaped token accepted", V.token("a" * 20 + "_-") == "a" * 20 + "_-")
check("a short cookie is no session", accounts.session("short") is None)
check("a non-URL-safe cookie is no session", accounts.session("a" * 20 + "/") is None)
check("a too-long cookie is no session", accounts.session("a" * 101) is None)
check("an invalid cookie logs out quietly", accounts.logout("a" * 20 + "/") is None)
check("its device is not looked up", accounts.session_device("a" * 20 + "/") is None)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
