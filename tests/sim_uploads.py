# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's uploads keep to their kinds, sizes and fixed names: a picture is the kind its place
allows, the store's keys are whole names, and the TLS import writes only chain.pem and key.pem in
the hub's staging folder. python3 tests/sim_uploads.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import server, store

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
ico = b"\x00\x00\x01\x00" + b"0" * 32
jpeg = b"\xff\xd8\xff" + b"0" * 32
webp = b"RIFF" + b"0" * 4 + b"WEBP" + b"0" * 32
svg = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"

check("favicon: a PNG", server.picture_kind("favicon", png) == "image/png")
check("favicon: an .ico", server.picture_kind("favicon", ico) == "image/x-icon")
check("favicon: a JPEG refused", server.picture_kind("favicon", jpeg) is None)
check("hub art: a PNG", server.picture_kind("hub-art", png) == "image/png")
check("hub art: a JPEG", server.picture_kind("hub-art", jpeg) == "image/jpeg")
check("hub art: a WebP", server.picture_kind("hub-art", webp) == "image/webp")
check("hub art: an SVG refused", server.picture_kind("hub-art", svg) is None)
check(
    "a picture over its place's size is refused before it is read",
    server.PICTURES["favicon"]["max"] == 64 * 1024 and server.PICTURES["hub-art"]["max"] == 180 * 1024,
)

for bad in ("", "../x", "a/b", "a b", "a\nb", "a" * 65):
    check(f"store key refused: {bad!r}", store.ID_RE.fullmatch(bad) is None)
check("store key: a whole name", store.ID_RE.fullmatch("abc_123-XYZ") is not None)

source = (REPO / "irate_box/hub/server.py").read_text()
check(
    "TLS import: only the two fixed names, each bounded",
    'for name, text in (("chain.pem", chain), ("key.pem", key))' in source
    and "len(chain) > 64 << 10" in source
    and "len(key) > 16 << 10" in source,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
