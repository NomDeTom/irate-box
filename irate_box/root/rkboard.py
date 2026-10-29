# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Rockchip boards on the box's USB, root's half: a board running its own system switched to Loader mode, a board's
onboard flash erased (in Maskrom mode, the loader for its chip sent first), or, when the board's loader shows its SD
card instead, an image written to the card. The boards, their modes, the tools, the loaders and the images are read
by hub/rkusb.py, which the hub uses too (see there for the modes).

Which storage a loader shows is read first (rkdeveloptool rfi): a Lyra's SPI NAND is 256 MB, and a storage of
gigabytes is its SD card. rkdeveloptool's ef erases every block of whatever the loader shows, so an erase meant for
the onboard flash would empty the card: it is refused, and the page offers to write an image to the card instead.
An image is written through the loader in pieces held in RAM (a whole one would be gigabytes on the box's own card),
each read back and compared before the next.
"""

import hashlib
import lzma
import os
import re
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from irate_box.hub import rkusb
from irate_box.hub.rkusb import boards, loaders, status, tools  # noqa: F401 - the root helper's names for them
from irate_box.msg import M, Refused

SERIAL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
WAIT = 25  # seconds for a board to come back after a reboot into Loader mode, or after a loader is sent
LARGE_MB = 1024  # a Lyra's SPI NAND is 256 MB: a storage larger than this is its SD card (or a board's eMMC)
SIZE_MB_RE = re.compile(r"Flash Size:\s*(\d+)\s*MB")
SIZE_SECTORS_RE = re.compile(r"Flash Size:\s*(\d+)\s*Sectors")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SECTOR = 512
CHUNK = 32 << 20  # an image written in pieces of this size, each in RAM, then read back
TAIL = 2048  # sectors cleared at the card's end after an image: an older image's backup partition table
RUN = Path(os.environ.get("HUB_RK_RUN", "/run/irate-box-rk"))


def run(*cmd, timeout=600):
    """Every command goes through here (the tests stand it in)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _one(modes):
    """The one board on USB, which must be in one of these modes; refused for none, or more than one."""
    found = boards()
    if not found:
        raise Refused(M("rkboard.none", "No Rockchip board is connected to the box's USB."))
    if len(found) > 1:
        raise Refused(M("rkboard.several", "{n} Rockchip boards are connected. Connect one at a time.", n=len(found)))
    if found[0]["mode"] not in modes:
        raise Refused(
            M("rkboard.wrong-mode", "The board is not in a mode for this step ({mode}).", mode=found[0]["mode"])
        )
    return found[0]


def _need(tool):
    if not tools()[tool]:
        raise Refused(
            M(
                "rkboard.no-tool",
                "{tool} is not installed. Install the Rockchip boards toolkit on Tools, Toolkits.",
                tool=tool,
            )
        )


def _wait_for(modes):
    for _ in range(WAIT):
        found = [b for b in boards() if b["mode"] in modes]
        if found:
            return found[0]
        time.sleep(1)
    return None


def to_loader():
    """A board running its own system restarted into Loader mode (adb reboot loader)."""
    board = _one(("system",))
    _need("adb")
    serial = board["serial"]
    r = run("adb", *(["-s", serial] if SERIAL_RE.match(serial) else []), "reboot", "loader", timeout=60)
    if r.returncode:
        raise Refused(
            M(
                "rkboard.adb-failed",
                "The board did not take the request: {detail}",
                detail=(r.stderr or r.stdout).strip()[-200:],
            )
        )
    if not _wait_for(("loader",)):
        raise Refused(
            M(
                "rkboard.no-loader",
                "The board restarted but did not come back in Loader mode. Unplug it, hold its boot button, "
                "and connect it again (Maskrom mode).",
            )
        )
    return M("rkboard.in-loader", "The board is in Loader mode, ready to erase.")


def _send_loader(loader_id):
    loader = next((x for x in loaders() if x["id"] == loader_id), None)
    if not loader:
        raise Refused(M("rkboard.loader-unknown", "Select the board's chip, so that the box sends the right loader."))
    path = rkusb.LOADERS / loader["file"]
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        digest = ""
    if digest != loader["sha256"]:
        raise Refused(M("rkboard.loader-damaged", "The loader for {chip} is damaged or missing.", chip=loader["chip"]))
    r = run("rkdeveloptool", "db", str(path), timeout=120)
    if r.returncode or not _wait_for(("loader",)):
        raise Refused(
            M(
                "rkboard.loader-failed",
                "The board did not start the {chip} loader. Check that the board has that chip. {detail}",
                chip=loader["chip"],
                detail=(r.stderr or r.stdout).strip()[-160:],
            )
        )


def storage():
    """What the board's loader shows (rkdeveloptool rfi): {mb, sectors, card}; card when it is gigabytes, so not the
    onboard flash. mb None when it could not be read."""
    r = run("rkdeveloptool", "rfi", timeout=60)
    text = "" if r.returncode else r.stdout or ""
    mb, sectors = SIZE_MB_RE.search(text), SIZE_SECTORS_RE.search(text)
    mb = int(mb.group(1)) if mb else None
    return {"mb": mb, "sectors": int(sectors.group(1)) if sectors else None, "card": mb is not None and mb > LARGE_MB}


def _gb(mb):
    return f"{mb / 1024:.1f}"


def contents(shown):
    """What the board's onboard flash holds, read (rkdeveloptool rl) before an erase: UBI's volume names, or blank."""
    sectors = shown.get("sectors") or 0
    if not sectors:
        return {"read": False, "blank": False, "ubi": False, "names": []}
    with tempfile.NamedTemporaryFile(prefix="rkboard-", delete=False) as fh:
        path = Path(fh.name)
    try:
        read = run("rkdeveloptool", "rl", "0", str(min(sectors, 32768)), str(path), timeout=300)
        if read.returncode:
            return {"read": False, "blank": False, "ubi": False, "names": []}
        data = path.read_bytes()
    except OSError:
        return {"read": False, "blank": False, "ubi": False, "names": []}
    finally:
        path.unlink(missing_ok=True)
    words = {w.lower() for w in re.findall(rb"[ -~]{3,}", data)}
    names = sorted({n for n in words if n in (b"rescue", b"logs", b"state", b"hub", b"irate")})
    return {"read": True, "blank": not any(data), "ubi": b"UBI#" in data, "names": [n.decode() for n in names]}


def held_words(held):
    """(the token the page confirms with, the words shown for it)."""
    if not held["read"]:
        return "unknown", M("rkboard.held.unknown", "Its contents could not be read.")
    if held["blank"]:
        return "blank", M("rkboard.held.blank", "It is blank.")
    if held["names"]:
        return ",".join(held["names"]), M("rkboard.held.names", "It holds {names}.", names=", ".join(held["names"]))
    if held["ubi"]:
        return "ubi", M("rkboard.held.ubi", "It holds a UBI filesystem whose volumes could not be named.")
    return "data", M("rkboard.held.data", "It holds data this box does not recognise.")


def read_flash(loader_id=None):
    """Read the onboard flash and say what it holds, before an erase. Refused when the loader shows the SD card."""
    board = _one(("loader", "maskrom"))
    _need("rkdeveloptool")
    if board["mode"] == "maskrom":
        _send_loader(loader_id)
    shown = storage()
    if shown["card"]:
        raise Refused(
            M(
                "rkboard.read.card-shown",
                "The board's loader shows {gb} GB: its SD card, not its onboard flash.",
                gb=_gb(shown["mb"]),
            )
        )
    token, said = held_words(contents(shown))
    message = M("rkboard.read", "The onboard flash: {held}", held=said)
    message["token"] = token
    return message


def erase(loader_id=None, confirm=None):
    """The board's onboard flash erased (rkdeveloptool ef), then the board reset. A board in Maskrom mode is sent
    the loader for its chip first. Refused when the loader shows the SD card: ef would empty the card."""
    board = _one(("loader", "maskrom"))
    _need("rkdeveloptool")
    if board["mode"] == "maskrom":
        _send_loader(loader_id)
    shown = storage()
    if shown["card"]:
        raise Refused(
            M(
                "rkboard.card-shown",
                "The board's loader shows {gb} GB: its SD card, not its onboard flash, so nothing was erased. Take the "
                "card out to erase the onboard flash, or write an image to the card.",
                gb=_gb(shown["mb"]),
            )
        )
    held = contents(shown)
    token, said = held_words(held)
    if token != "blank" and confirm != token:  # nosec B105 - a state word, not a password
        raise Refused(
            M(
                "rkboard.erase.unconfirmed",
                "The onboard flash {held} Read it first, then confirm the erase.",
                held=said,
            )
        )
    r = run("rkdeveloptool", "ef", timeout=900)
    if r.returncode:
        raise Refused(
            M("rkboard.erase-failed", "The erase failed: {detail}", detail=(r.stderr or r.stdout).strip()[-200:])
        )
    run("rkdeveloptool", "rd", timeout=60)
    return M(
        "rkboard.erased",
        "The board's onboard flash is erased. It held: {held} Unplug the board, put its SD card in, and power it: "
        "it starts from the card.",
        held=said,
    )


def _image(name):
    """An image in rkusb.IMAGES, opened without following a link: a regular file, or refused."""
    if not rkusb.IMAGE_RE.match(name or ""):
        raise Refused(M("rkboard.image-name", "Select an image to write."))
    try:
        fd = os.open(rkusb.IMAGES / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise Refused(M("rkboard.image-missing", "The image {name} is not on the box.", name=name)) from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise Refused(M("rkboard.image-missing", "The image {name} is not on the box.", name=name))
    return os.fdopen(fd, "rb")


def _digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_card(name, sha256, loader_id=None):
    """An image from rkusb.IMAGES (.img, or .img.xz unpacked as it goes) written to the SD card the board's loader
    shows, in pieces: each written (rkdeveloptool wl), read back (rl) and compared before the next; then the card's
    last sectors cleared and the board reset. Its checksum (the publisher's) checked first. Refused unless the
    loader shows a card."""
    if not SHA_RE.match(sha256 or ""):
        raise Refused(M("rkboard.image-sha", "Give the image's sha256, as its publisher lists it."))
    with _image(name) as fh:
        h = hashlib.sha256()
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != sha256:
        raise Refused(
            M("rkboard.image-damaged", "The image {name} does not match its sha256: nothing was written.", name=name)
        )
    board = _one(("loader", "maskrom"))
    _need("rkdeveloptool")
    if board["mode"] == "maskrom":
        _send_loader(loader_id)
    shown = storage()
    if not shown["card"]:
        raise Refused(
            M(
                "rkboard.not-card",
                "The board's loader does not show an SD card (it shows {mb} MB): nothing was written. Put the card in "
                "the board and connect it again.",
                mb=shown["mb"] if shown["mb"] is not None else "?",
            )
        )
    RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    piece, back = RUN / "piece.img", RUN / "back.img"
    sector = 0
    try:
        with _image(name) as fh:
            stream = lzma.open(fh) if name.endswith(".xz") else fh
            while data := stream.read(CHUNK):
                if len(data) % SECTOR:
                    data += bytes(SECTOR - len(data) % SECTOR)
                count = len(data) // SECTOR
                if shown["sectors"] and sector + count > shown["sectors"]:
                    raise Refused(
                        M("rkboard.image-large", "The image is larger than the card ({gb} GB).", gb=_gb(shown["mb"]))
                    )
                piece.write_bytes(data)
                if run("rkdeveloptool", "wl", str(sector), str(piece), timeout=600).returncode:
                    raise Refused(
                        M("rkboard.write-failed", "Writing the card failed at {mb} MB.", mb=sector * SECTOR >> 20)
                    )
                read = run("rkdeveloptool", "rl", str(sector), str(count), str(back), timeout=600)
                if read.returncode or not back.exists() or _digest(back) != hashlib.sha256(data).hexdigest():
                    raise Refused(
                        M(
                            "rkboard.verify-failed",
                            "What was written at {mb} MB did not read back the same: the card is not usable as it is.",
                            mb=sector * SECTOR >> 20,
                        )
                    )
                sector += count
        if shown["sectors"] and shown["sectors"] > sector + TAIL:
            piece.write_bytes(bytes(TAIL * SECTOR))
            run("rkdeveloptool", "wl", str(shown["sectors"] - TAIL), str(piece), timeout=120)
    finally:
        for f in (piece, back):
            f.unlink(missing_ok=True)
    run("rkdeveloptool", "rd", timeout=60)
    return M(
        "rkboard.card-written",
        "The image is on the board's SD card: {mb} MB written and read back. The board restarts from the card.",
        mb=sector * SECTOR >> 20,
    )
