# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What comes from the internet and from the mesh: a source URL is a web address (https for a book),
never a user name or a control character; a Meshtastic channel name is one line with none of the
characters the protocol reserves. python3 tests/sim_internet.py"""

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import meshbridge
from irate_box.library import librarian
from irate_box.msg import Refused

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def refused(name, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except (ValueError, Refused, librarian.LibrarianError) as exc:
        check(name, bool(str(exc)), exc)
    else:
        check(name, False, "accepted")


# --- a source URL: https for a book, a web scheme for a download ---
good = librarian.validate_source({"name": "book", "type": "url", "url": "https://example.com/book.zim"})
check("a source URL: https kept", good["url"] == "https://example.com/book.zim")
lan = librarian.validate_source({"name": "book", "type": "url", "url": "https://192.168.1.10/book.zim"})
check("a source URL: a LAN address kept", lan["url"] == "https://192.168.1.10/book.zim")
for bad, why in (
    ("file:///etc/passwd", "file:"),
    ("javascript:alert(1)", "javascript:"),
    ("http://example.com/book.zim", "plain HTTP"),
    ("https://user:pass@example.com/book.zim", "a user name"),
    ("https://example.com/a b.zim", "a space"),
    ("https://example.com/a\nb.zim", "a newline"),
):
    refused(f"a source URL refused: {why}", librarian.validate_source, {"name": "book", "type": "url", "url": bad})

refused("a download: file: refused", librarian._open, "file:///etc/passwd")
refused("a download: javascript: refused", librarian._open, "javascript:alert(1)")
refused("a download: a user name refused", librarian._open, "https://user:pass@example.com/x")
refused("a download: a newline refused", librarian._open, "https://example.com/a\nb")

# --- a Meshtastic channel name ---
T = Path(tempfile.mkdtemp(prefix="mesh-"))
real = meshbridge.CHANNELS
try:
    meshbridge.CHANNELS = T / "channels.json"
    meshbridge.add_channel("LongFast", "AQ==")
    check("a channel name: the ordinary name kept", [c["name"] for c in meshbridge.channels()] == ["LongFast"])
    for bad, why in (
        ("a\nb", "a newline"),
        ("a\tb", "a tab"),
        ("a\0b", "a NUL"),
        ("a/b", "a slash"),
        ("#x", "a hash"),
        ("+x", "a plus"),
        ("", "empty"),
        ("x" * 12, "too long"),
    ):
        refused(f"a channel name refused: {why}", meshbridge.add_channel, bad, "AQ==")
    refused("a channel removal with a newline refused", meshbridge.remove_channel, "LongFast\n")
finally:
    meshbridge.CHANNELS = real

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
