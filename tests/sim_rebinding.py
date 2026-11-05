# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A name that answers a public address first and a private one next (DNS rebinding) cannot send a
fetch to the box's own network, and a redirect to a numeric host is refused (item 87, pattern P7).
A listener on 127.0.0.1 stands for "the box's own network": it must never be connected to.
python3 tests/sim_rebinding.py"""

import socket
import sys
import threading
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box import validate as V
from irate_box import webfetch
from irate_box.library import librarian
from irate_box.msg import Refused

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


PUBLIC = "93.184.216.34"
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 0))
listener.listen(5)
PORT = listener.getsockname()[1]
accepted = []


def accept_forever():
    while True:
        try:
            connection, _ = listener.accept()
        except OSError:
            return
        accepted.append(connection)
        connection.close()


threading.Thread(target=accept_forever, daemon=True).start()


class Rebinding:
    """A resolver: the first answer is public, every later one is the loopback address."""

    def __init__(self):
        self.calls = 0

    def __call__(self, host, port, *args, **kwargs):
        self.calls += 1
        address = PUBLIC if self.calls == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 0))]


# The check on its own: the first look finds a public address, the connection looks again.
resolver = Rebinding()
check(
    "the first look is public", V.public_url("https://rebind.example/x", resolve=resolver) == "https://rebind.example/x"
)
try:
    V.connect_public("rebind.example", PORT, 5, resolve=resolver)
    check("the connection that looks again is refused", False, "connected")
except V.Invalid as exc:
    check("the connection that looks again is refused", "own networks" in str(exc), exc)
check("  and nothing connected to the loopback listener", not accepted, len(accepted))


# A public answer that stays public connects to the address it was checked against.
def steady(host, port, *a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]


try:
    V.connect_public("steady.example", PORT, 5, resolve=steady)
    check("an answer that is private from the start is refused", False, "connected")
except V.Invalid:
    check("an answer that is private from the start is refused", True)


def mixed(host, port, *a, **k):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC, port)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
    ]


try:
    V.connect_public("mixed.example", PORT, 5, resolve=mixed)
    check("an answer with one private address among the public ones is refused", False, "connected")
except V.Invalid:
    check("an answer with one private address among the public ones is refused", True)
check("  and nothing connected to the loopback listener", not accepted, len(accepted))

# The librarian's download: its check looks the name up, then urllib connects. The same resolver
# answers urllib's own lookup too, so the old code (which let urllib look the name up again) would
# have connected to the listener.
resolver = Rebinding()
old_resolve, old_getaddrinfo = librarian.RESOLVE, socket.getaddrinfo
librarian.RESOLVE = resolver
socket.getaddrinfo = resolver
try:
    try:
        librarian._open(f"https://rebind.example:{PORT}/x")
        outcome = "opened"
    except librarian.LibrarianError as exc:
        outcome = "refused"
        reason = str(exc.args[0]) if exc.args else str(exc)
finally:
    librarian.RESOLVE, socket.getaddrinfo = old_resolve, old_getaddrinfo
check("the librarian's download of a rebinding name is refused", outcome == "refused", outcome)
check("  without a connection to the loopback listener", not accepted, len(accepted))
check("  after the check's look and the connection's look", resolver.calls >= 2, resolver.calls)

# The shared opener (root's downloads and the podcasts' episodes use it): the same.
resolver = Rebinding()
resolver.calls = 1  # the first look has been spent: the opener's connection gets the private answer
try:
    webfetch.opener(resolve=resolver, schemes=("http", "https")).open(f"http://rebind.example:{PORT}/x", timeout=5)
    outcome = "opened"
except (OSError, ValueError):
    outcome = "refused"
check("webfetch: an address that turns private is refused at connect", outcome == "refused", outcome)
check("  without a connection to the loopback listener", not accepted, len(accepted))

# Redirects: to a numeric host in any spelling, and to a name that resolves privately.
handler = librarian._CheckedRedirect()
request = urllib.request.Request("https://example.com/x")
for hostile in (
    "https://2130706433/x",
    "https://0x7f.1/x",
    "https://127.1/x",
    "https://017700000001/x",
    "https://[::ffff:127.0.0.1]/x",
):
    old = librarian.RESOLVE
    librarian.RESOLVE = lambda host, *a, **k: [(2, 1, 6, "", (PUBLIC, 0))]
    try:
        handler.redirect_request(request, None, 302, "Found", {}, hostile)
        check(f"a redirect to {hostile} is refused", False, "followed")
    except (ValueError, Refused):
        check(f"a redirect to {hostile} is refused", True)
    finally:
        librarian.RESOLVE = old
redirect = webfetch._CheckedRedirect(lambda host, *a, **k: [(2, 1, 6, "", (PUBLIC, 0))])
for hostile in ("https://2130706433/x", "https://127.1/x"):
    try:
        redirect.redirect_request(request, None, 302, "Found", {}, hostile)
        check(f"webfetch: a redirect to {hostile} is refused", False, "followed")
    except ValueError:
        check(f"webfetch: a redirect to {hostile} is refused", True)

# A podcast episode's address came out of a feed: a numeric host is refused, a name is opened through
# the public-only opener, and only the feed's own host (the owner's choice) is opened as it is.
from irate_box.library import podcast

for hostile in ("http://2130706433/a.mp3", "http://127.1/a.mp3", "http://[::1]/a.mp3", "ftp://example.org/a.mp3"):
    try:
        podcast._episode_opener(hostile, "https://feed.example/f.xml")
        check(f"an episode at {hostile} is refused", False, "opened")
    except librarian.LibrarianError:
        check(f"an episode at {hostile} is refused", True)
other = podcast._episode_opener("http://cdn.example/a.mp3", "https://feed.example/f.xml")
check(
    "an episode on another host goes through the public-only opener",
    any(type(h).__module__ == "irate_box.webfetch" for h in other.handlers),
)
own = podcast._episode_opener("http://feed.example/a.mp3", "http://feed.example/f.xml")
check(
    "an episode on the feed's own host is opened as it is",
    not any(type(h).__module__ == "irate_box.webfetch" for h in own.handlers),
)
check(
    "an episode of a feed on a numeric host: its own host stays allowed",
    podcast._episode_opener("http://192.168.1.5/a.mp3", "http://192.168.1.5/f.xml") is not None,
)

listener.close()
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
