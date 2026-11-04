# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""USB sticks, card images and card/loader names: every name is matched whole, and the root side
uses the same card, image, checksum, loader and book-name rules as the hub.
python3 tests/sim_usb_cards.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box import validate as V
from irate_box.hub import rkusb
from irate_box.msg import Refused
from irate_box.root import rkboard, rkcard, usbstick

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def refused(name, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except (ValueError, Refused) as exc:
        check(name, bool(str(exc)), exc)
    else:
        check(name, False, "accepted")


# --- the shared names ---
check("image: .img", V.image("lyra-zero-w-v0.2.1.img") == "lyra-zero-w-v0.2.1.img")
check("image: .img.xz", V.image("alpine-rescue.img.xz") == "alpine-rescue.img.xz")
for bad, why in (
    ("x.img\n", "a newline"),
    ("x.img ", "a trailing space"),
    ("../x.img", "a path"),
    ("x.iso", "not an image"),
    ("x" * 210 + ".img", "too long"),
):
    refused(f"image refused: {why}", V.image, bad)
refused("card: a newline", V.card, "sda\n")
check("card: sda", V.card("sda") == "sda")
refused("sha256: a newline", V.sha256, "a" * 64 + "\n")
refused("loader: a newline", V.loader, "rk3506b\n")
check("loader: rk3506b", V.loader("rk3506b") == "rk3506b")

# The hub's own patterns are whole too.
for pattern, value in (
    (rkusb.IMAGE_RE, "x.img\n"),
    (rkusb.BUNDLE_RE, "file.bin\n"),
    (rkusb.RESCUE_RE, "alpine-rescue.img\n"),
):
    check(f"{pattern.pattern}: a trailing newline refused", pattern.fullmatch(value) is None)

# --- the root side uses the same rules ---
refused("rkboard: an image name with a newline refused", rkboard._image, "x.img\n")
refused("rkboard: a checksum with a newline refused", rkboard.write_card, "x.img", "a" * 64 + "\n")
refused("rkboard: a loader with a newline refused", rkboard._send_loader, "rk3506b\n")
refused("rkcard: a card name with a newline refused", rkcard._card, "sda\n", 0, "")
refused("rkcard: a checksum with a newline refused", rkcard.flash, "sda", 0, "", "x.img", "a" * 64 + "\n")
refused("usbstick: a book name with a newline refused", usbstick.export_zim, "sda", "book\n", "/tmp")
check("usbstick: a book name whole", usbstick.NAME_RE.fullmatch("book-1") is not None)
check("usbstick: a book name with a newline refused by the pattern", usbstick.NAME_RE.fullmatch("book\n") is None)

source = {
    p: (REPO / p).read_text()
    for p in (
        "irate_box/hub/rkusb.py",
        "irate_box/root/rkboard.py",
        "irate_box/root/rkcard.py",
        "irate_box/root/usbstick.py",
    )
}
for path, text in source.items():
    for name in ("IMAGE_RE", "BUNDLE_RE", "RESCUE_RE", "NAME_RE", "SERIAL_RE", "SHA_RE"):
        if f"{name}.match(" in text:
            check(f"{path}: {name} is matched whole", False)
check("the hub and root use whole names", True)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
