# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Part B of the security plan (the 2026-10-02 review's F30 points), offline:
  B1  the Meshtastic flasher page in the scheme it was downloaded over, with the check and the digests of the
      firmware here at the top (the check itself: tests/jsdom/dom-flasher-check.cjs)
  B3  adapt_tools removes every link before it writes: a planted directory-override.css leaves its target alone
  B4  USB drives: the box mounts the common filesystems only until the administrator widens it; a drive of another
      kind is said, not mounted; one another program mounted already is read where it is; the setting kept
python3 tests/sim_firmware_integrity.py"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="firmware-integrity-"))
os.environ.update(
    HUB_FLASHER_ROOT=str(T / "flasher"),
    HUB_FIRMWARE_ROOT=str(T / "firmware"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_USB_MOUNTS=str(T / "mounts"),
)
sys.path.insert(0, str(REPO))
from irate_box.hub import flasher
from irate_box.library import adapt_tools, firmware
from irate_box.msg import Refused
from irate_box.root import usbstick

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# --- B1: the downloaded flasher ---
(T / "flasher").mkdir()
(T / "flasher" / "flasher.html").write_text(
    '<!doctype html><html><HEAD lang="en"><title>f</title><script src="__IRATE_BOX_ORIGIN__/flasher/app.js"></script>'
    "</head><body></body></html>"
)
release = T / "firmware" / "2.8.2.c39728e"
release.mkdir(parents=True)
(release / "a.bin").write_bytes(b"firmware")
firmware.record_digests(release)
page = flasher.page("192.168.4.1", "https").decode()
check(
    "downloaded over HTTPS: the page's addresses are https://",
    "https://192.168.4.1/flasher/app.js" in page and "http://192.168.4.1" not in page,
)
check(
    "  the check right after <head>, before the page's own scripts, with the digest of each firmware file here",
    page.index("Firmware refused") < page.index("/flasher/app.js")
    and page.index("<HEAD") < page.index("Firmware refused")
    and json.dumps({"/flasher/firmware/2.8.2.c39728e/a.bin": hashlib.sha256(b"firmware").hexdigest()}) in page,
)
check("over plain HTTP: http://", "http://192.168.4.1/flasher/app.js" in flasher.page("192.168.4.1").decode())
check("  a scheme that is neither: no page", flasher.page("192.168.4.1", "ftp") is None)
(release / "sha256.json").write_text(json.dumps({"x.bin": [1, 2, "not a digest"], "../y": [1, 2, "0" * 64]}))
check("a digests file with junk: none of it carried", flasher.digests() == {}, flasher.digests())

# --- B3: links in the calculators' tree ---
tools, outside = T / "tools", T / "hub-file.json"
tools.mkdir()
outside.write_text('{"keep": true}')
(tools / adapt_tools.OVERRIDE).symlink_to(outside)
(tools / "sub").mkdir()
(tools / "sub" / "link.html").symlink_to(outside)
adapt_tools.main(tools, REPO / "web")
check(
    "adapt_tools: a planted link removed before anything is written, its target untouched",
    outside.read_text() == '{"keep": true}'
    and not (tools / adapt_tools.OVERRIDE).is_symlink()
    and (tools / adapt_tools.OVERRIDE).is_file()
    and not any(p.is_symlink() for p in tools.rglob("*")),
)

# --- B4: the filesystems root mounts from USB drives ---
usbstick.safeio.write = lambda path, text, mode=0o644: (
    Path(path).parent.mkdir(parents=True, exist_ok=True),
    Path(path).write_text(text),
)
check("USB drives: the common filesystems only until set", usbstick.level() == "common")
ext4 = {"name": "sda1", "path": "/dev/sda1", "fstype": "ext4", "mountpoint": None}
try:
    with usbstick.Mounted(ext4):
        pass
    said = None
except Refused as exc:
    said = exc.message
check(
    "  an ext4 drive: not mounted, and said where it is set",
    said and said["id"] == "usb.fs.not-mounted" and "Books" in said["en"],
    said,
)
elsewhere = dict(ext4, mountpoint=str(T))
with usbstick.Mounted(elsewhere) as root:
    check("  one another program mounted already: read where it is", root == T)
usbstick.set_level("linux")
check(
    "widened to Linux's: kept, and ext4 then mountable",
    usbstick.level() == "linux"
    and "ext4" in usbstick.LEVELS[usbstick.level()]
    and "btrfs" not in usbstick.LEVELS["linux"],
)
try:
    usbstick.set_level("everything")
    check("  a level that is not one: refused", False)
except Refused as exc:
    check("  a level that is not one: refused", exc.message["id"] == "usb.level.bad")
check(
    "  the levels: each the one before plus more",
    set(usbstick.LEVELS["common"]) < set(usbstick.LEVELS["linux"]) < set(usbstick.LEVELS["all"])
    and set(usbstick.LEVELS["all"]) == usbstick.FILESYSTEMS,
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
