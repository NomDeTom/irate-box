# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Rockchip boards on the box's USB, root's half: a board running its own system switched to Loader mode, a board's
onboard flash read or erased (in Maskrom mode, the loader for its chip sent first), a bootloader written to that
flash, or, when the board's loader shows its SD card instead, an image written to the card. The boards, their modes,
the tools, the loaders, the bootloaders and the images are read by hub/rkusb.py, which the hub uses too (see there
for the modes).

Which storage a loader shows is read first (rkdeveloptool rfi): a Lyra's SPI NAND is 256 MB, and a storage of
gigabytes is its SD card. rkdeveloptool's ef erases every block of whatever the loader shows, so an erase meant for
the onboard flash would empty the card: it is refused, and the page offers to write an image to the card instead.
An image is written through the loader in pieces held in RAM (a whole one would be gigabytes on the box's own card),
each read back and compared before the next.
"""

import functools
import hashlib
import json
import lzma
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from irate_box import validate
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
PROGRESS = rkusb.STATE / "control" / "rkboard-progress.json"
BACKUPS = rkusb.STATE / "rk-backups"
BACKUP_INDEX = rkusb.STATE / "control" / "rkboard-backups.json"
BACKUP_SETTINGS = rkusb.STATE / "control" / "rkboard-settings.json"
UPLOAD = rkusb.STATE / "rk-upload"
BACKUP_MARGIN = 64 << 20  # free space left on the box after a full copy
KEEP_DEFAULT = 2


def run(*cmd, timeout=600):
    """Every command goes through here (the tests stand it in)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _exclusive(fn):
    """One backup or install at a time: a second request is refused while the first runs."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
        busy = RUN / "rkboard-busy"
        try:
            fd = os.open(busy, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise Refused(M("rkboard.busy", "A recovery is already running. Wait for it to finish.")) from None
        except OSError:
            raise Refused(M("rkboard.busy", "A recovery is already running. Wait for it to finish.")) from None
        try:
            os.write(fd, fn.__name__.encode())
            os.close(fd)
            return fn(*args, **kwargs)
        finally:
            busy.unlink(missing_ok=True)

    return wrapper


def _progress(action, done, total, path=""):
    """The page's progress: small, in the control folder the hub can read."""
    try:
        PROGRESS.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        PROGRESS.write_text(json.dumps({"action": action, "done": done, "total": total, "path": path}))
    except OSError:
        pass


def _read_json_path(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _backup_entries():
    entries = _read_json_path(BACKUP_INDEX, [])
    if not isinstance(entries, list):
        return []
    return [
        e for e in entries if isinstance(e, dict) and isinstance(e.get("name"), str) and (BACKUPS / e["name"]).is_file()
    ]


def _write_index(entries):
    try:
        BACKUP_INDEX.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        BACKUP_INDEX.write_text(json.dumps(entries))
        os.chmod(BACKUP_INDEX, 0o644)
    except OSError:
        pass


def _keep():
    value = _read_json_path(BACKUP_SETTINGS, {})
    try:
        n = int(value.get("keep", KEEP_DEFAULT))
    except (TypeError, ValueError, AttributeError):
        n = KEEP_DEFAULT
    return max(1, min(20, n))


def _prune():
    entries = sorted(_backup_entries(), key=lambda e: (e.get("created") or "", e["name"]))
    keep = _keep()
    while len(entries) > keep:
        old = entries.pop(0)
        try:
            (BACKUPS / old["name"]).unlink(missing_ok=True)
        except OSError:
            pass
    _write_index(entries)


def _index_add(path, digest, serial, size):
    entries = [e for e in _backup_entries() if e.get("name") != path.name]
    entries.append(
        {
            "name": path.name,
            "size": size,
            "sha256": digest,
            "serial": serial,
            "created": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(path.stat().st_mtime)),
        }
    )
    _write_index(entries)
    _prune()


def backups_list():
    """The saved copies, newest first, as the page lists them."""
    return sorted(_backup_entries(), key=lambda e: (e.get("created") or "", e["name"]), reverse=True)


def set_keep(value):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise Refused(M("rkboard.keep.value", "Select how many copies to keep, from 1 to 20.")) from None
    if not 1 <= n <= 20:
        raise Refused(M("rkboard.keep.value", "Select how many copies to keep, from 1 to 20."))
    try:
        BACKUP_SETTINGS.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        BACKUP_SETTINGS.write_text(json.dumps({"keep": n}))
        os.chmod(BACKUP_SETTINGS, 0o644)
    except OSError as exc:
        raise Refused(
            M("rkboard.keep.write", "The setting could not be saved: {detail}", detail=str(exc)[-160:])
        ) from None
    _prune()
    return M("rkboard.keep", "Keeping the newest {n} copies.", n=n)


def delete_backup(name):
    entry = next((e for e in _backup_entries() if e["name"] == name), None)
    if not entry:
        raise Refused(M("rkboard.backup.missing", "That saved copy is no longer on the box."))
    try:
        (BACKUPS / name).unlink()
    except OSError as exc:
        raise Refused(
            M("rkboard.backup.delete", "The copy could not be removed: {detail}", detail=str(exc)[-160:])
        ) from None
    _write_index([e for e in _backup_entries() if e["name"] != name])
    return M("rkboard.backup.deleted", "Removed {name}.", name=name)


@_exclusive
def stage_bundle(bundle, name, temp):
    """Stage an uploaded bundle file after checking it against the sha256 in bootloaders.json."""
    meta = next((b for b in rkusb.bootloaders() if b.get("id") == bundle), None)
    if not meta:
        raise Refused(M("rkboard.bundle.unknown", "That bootloader is not described on this box."))
    item = next((f for f in meta.get("files") or [] if f.get("name") == name), None)
    if not item:
        raise Refused(
            M("rkboard.bundle.file", "{name} is not a file of {title}.", name=name, title=meta.get("title") or bundle)
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}", temp):
        raise Refused(M("rkboard.bundle.upload", "That upload cannot be read."))
    source = UPLOAD / temp
    if not source.is_file() or source.is_symlink():
        raise Refused(M("rkboard.bundle.upload", "That upload cannot be read."))
    expected = str(item.get("sha256") or "").lower()
    actual = _digest(source)
    if expected and actual != expected:
        source.unlink(missing_ok=True)
        raise Refused(
            M(
                "rkboard.bundle.mismatch",
                "{name} does not match its sha256 ({actual}), so nothing was staged.",
                name=name,
                actual=actual,
            )
        )
    dest = rkusb.BOOTLOADER_STATE / bundle / name
    try:
        dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        shutil.move(str(source), str(dest))
        os.chmod(dest, 0o644)
    except OSError as exc:
        source.unlink(missing_ok=True)
        raise Refused(
            M("rkboard.bundle.write", "The file could not be staged: {detail}", detail=str(exc)[-160:])
        ) from None
    return M("rkboard.bundle.staged", "Staged {name} for {title}.", name=name, title=meta.get("title") or bundle)


@_exclusive
def restore(name, loader_id=None, confirm=None):
    """Write a saved full copy back to the board, read it back and compare. The same read/confirm rule as an install."""
    entry = next((e for e in _backup_entries() if e["name"] == name), None)
    path = BACKUPS / name
    if not entry or not path.is_file():
        raise Refused(M("rkboard.backup.missing", "That saved copy is no longer on the box."))
    board = _one(("loader", "maskrom"))
    _need("rkdeveloptool")
    if board["mode"] == "maskrom":
        _send_loader(loader_id)
    shown = storage()
    if shown["card"]:
        raise Refused(
            M(
                "rkboard.restore.card-shown",
                "The board's loader shows its SD card, not its onboard flash, so nothing was written.",
            )
        )
    want = (shown.get("sectors") or 0) * SECTOR
    if not want or path.stat().st_size != want:
        raise Refused(
            M(
                "rkboard.restore.size",
                "The saved copy is {size} bytes, not the {want} the board's flash holds, so nothing was written.",
                size=path.stat().st_size,
                want=want,
            )
        )
    token, said = held_words(contents(shown))
    if token != "blank" and confirm != token:  # nosec B105 - a state word, not a password
        raise Refused(
            M(
                "rkboard.restore.unconfirmed",
                "The onboard flash is not blank: {held} Read it first, then confirm the restore.",
                held=said,
            )
        )
    r = run("rkdeveloptool", "wl", "0", str(path), timeout=1800)
    if r.returncode:
        raise Refused(
            M(
                "rkboard.restore.failed",
                "Writing the saved copy failed: {detail}",
                detail=(r.stderr or r.stdout).strip()[-200:],
            )
        )
    verify = BACKUPS / f".{name}.verify"
    try:
        back = run("rkdeveloptool", "rl", "0", str(want), str(verify), timeout=900)
        if back.returncode or not verify.is_file() or _digest(verify) != entry.get("sha256"):
            raise Refused(
                M("rkboard.restore.verify", "The board did not read back the saved copy. Check it before rebooting.")
            )
    finally:
        verify.unlink(missing_ok=True)
    run("rkdeveloptool", "rd", timeout=60)
    return M(
        "rkboard.restored", "Restored {name} to the onboard flash, read back identical, and reset the board.", name=name
    )


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
    r = run("adb", *(["-s", serial] if SERIAL_RE.fullmatch(serial) else []), "reboot", "loader", timeout=60)
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
    try:
        loader_id = validate.loader(loader_id)
    except ValueError:
        raise Refused(
            M("rkboard.loader-unknown", "Select the board's chip, so that the box sends the right loader.")
        ) from None
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


READ_SECTORS = 32768  # 16 MiB: enough for the boot area and UBI's first volume headers
SAMPLE_BYTES = 4096  # the first block of each 16 MiB above the boot area, where logs and state begin at 32 MiB


def _blank(data):
    # An erased NAND reads as 0xFF (the Lyra Zero W's after rkdeveloptool ef); all zero is blank too.
    return bool(data) and (data.count(0xFF) == len(data) or not any(data))


def contents(shown):
    """What the board's onboard flash holds, read (rkdeveloptool rl) before an erase: UBI's volume names, or blank.
    "Blank" means every sample reads as erased, not only the first 16 MiB: a NAND whose boot area was erased but
    whose logs and state (from 32 MiB) are intact must still be confirmed before it is erased."""
    sectors = shown.get("sectors") or 0
    if not sectors:
        return {"read": False, "blank": False, "ubi": False, "names": []}
    with tempfile.NamedTemporaryFile(prefix="rkboard-", delete=False) as fh:
        path = Path(fh.name)
    try:
        # rkdeveloptool rl takes its first sector and a length in bytes ("Length must be a multiple of sector size
        # (512)", the kit's 1.0.0 on a Lyra Zero W): 16 MiB, or the whole flash if smaller.
        read = run("rkdeveloptool", "rl", "0", str(min(sectors, READ_SECTORS) * SECTOR), str(path), timeout=300)
        if read.returncode:
            return {"read": False, "blank": False, "ubi": False, "names": []}
        data = path.read_bytes()
        blank = _blank(data)
        if blank and sectors > READ_SECTORS:
            for at in range(READ_SECTORS, sectors, READ_SECTORS):
                length = min(SAMPLE_BYTES, (sectors - at) * SECTOR) // SECTOR * SECTOR
                if not length:
                    break
                more = run("rkdeveloptool", "rl", str(at), str(length), str(path), timeout=300)
                if more.returncode:
                    return {"read": False, "blank": False, "ubi": False, "names": []}
                sample = path.read_bytes()
                if not sample or not _blank(sample):
                    blank = False
                    break
    except OSError:
        return {"read": False, "blank": False, "ubi": False, "names": []}
    finally:
        path.unlink(missing_ok=True)
    words = {w.lower() for w in re.findall(rb"[ -~]{3,}", data)}
    names = sorted({n for n in words if n in (b"rescue", b"logs", b"state", b"hub", b"irate")})
    return {"read": True, "blank": blank, "ubi": b"UBI#" in data, "names": [n.decode() for n in names]}


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


@_exclusive
def backup(loader_id=None):
    """Read and save the whole onboard flash before an erase, in the state's rk-backups folder, with its SHA-256.
    Refused when the loader shows the SD card, and it never writes to the board."""
    board = _one(("loader", "maskrom"))
    _need("rkdeveloptool")
    if board["mode"] == "maskrom":
        _send_loader(loader_id)
    shown = storage()
    if shown["card"]:
        raise Refused(
            M(
                "rkboard.backup.card-shown",
                "The board's loader shows {gb} GB: its SD card, not its onboard flash, so nothing was saved.",
                gb=_gb(shown["mb"]),
            )
        )
    sectors = shown.get("sectors") or 0
    if not sectors:
        raise Refused(M("rkboard.backup.no-size", "The board's flash size could not be read, so nothing was saved."))
    backups = BACKUPS
    want = sectors * SECTOR
    try:
        backups.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            os.chmod(backups, 0o755)  # nosec B103 - the hub reads a saved copy for the admin's download
        except OSError:
            pass
    except OSError as exc:
        raise Refused(
            M(
                "rkboard.backup.path",
                "The backup folder {path} could not be made: {detail}",
                path=str(backups),
                detail=str(exc)[-160:],
            )
        ) from None
    try:
        free = shutil.disk_usage(backups).free
    except OSError:
        free = 0
    if free < want + BACKUP_MARGIN:
        raise Refused(
            M(
                "rkboard.backup.space",
                "The box has {free} MB free. A {want} MB copy needs {need} MB. Free space first.",
                free=free >> 20,
                want=want >> 20,
                need=(want + BACKUP_MARGIN) >> 20,
            )
        )
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    serial = re.sub(r"[^A-Za-z0-9._-]", "", str(board.get("serial") or ""))[:32] or "board"
    path = backups / f"{stamp}-{serial}.bin"
    _progress("backup", 0, want, str(path))
    stop = threading.Event()

    def poll():
        while not stop.wait(0.5):
            try:
                done = path.stat().st_size
            except OSError:
                done = 0
            _progress("backup", min(done, want), want, str(path))

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    try:
        r = run("rkdeveloptool", "rl", "0", str(want), str(path), timeout=900)
    finally:
        stop.set()
        poller.join(timeout=1)
    if r.returncode:
        path.unlink(missing_ok=True)
        raise Refused(
            M(
                "rkboard.backup.failed",
                "The flash could not be read: {detail}",
                detail=(r.stderr or r.stdout).strip()[-200:],
            )
        )
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size != want:
        path.unlink(missing_ok=True)
        raise Refused(
            M(
                "rkboard.backup.size",
                "The backup is {size} bytes, not the {want} the board's flash holds. Nothing was kept.",
                size=size,
                want=want,
            )
        )
    _progress("backup", want, want, str(path))
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass
    digest = _digest(path)
    _index_add(path, digest, serial, want)
    message = M(
        "rkboard.backed-up",
        "Saved a full copy of the onboard flash: {mb} MB at {path} (sha256 {sha}). Keep it: the install erases "
        "the flash.",
        mb=want >> 20,
        path=str(path),
        sha=digest,
    )
    message["path"] = str(path)
    message["sha256"] = digest
    message["mb"] = want >> 20
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
                "The onboard flash is not blank: {held} Read it first, then confirm the erase.",
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
    try:
        name = validate.image(name)
    except ValueError:
        raise Refused(M("rkboard.image-name", "Select an image to write.")) from None
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


@_exclusive
def install(bootloader_id, confirm=None, loader_id=None):
    """A bootloader from rkusb.BOOTLOADER_STATE written to the board's onboard SPI NAND: the whole flash erased
    first (this is where logs kept on the NAND go), then each file at its recorded offset, in pieces, each read back
    and compared. The board is reset at the end. Refused when the loader shows the SD card, or when the flash was not
    read and confirmed first."""
    bundle = next((b for b in rkusb.bootloaders() if b["id"] == bootloader_id), None)
    if not bundle:
        raise Refused(M("rkboard.bootloader-unknown", "Select a bootloader to install."))
    if not bundle["ready"]:
        missing = ", ".join(bundle["missing"]) or "none"
        damaged = ", ".join(bundle["damaged"]) or "none"
        raise Refused(
            M(
                "rkboard.bootloader-not-ready",
                "The {name} is not ready. Missing: {missing}. Not matching its sha256: {damaged}. Stage its files "
                "in rk-bootloaders/{id} on the box.",
                name=bundle["title"],
                missing=missing,
                damaged=damaged,
                id=bundle["id"],
            )
        )
    board = _one(("loader", "maskrom"))
    _need("rkdeveloptool")
    if board["mode"] == "maskrom":
        _send_loader(loader_id)
    shown = storage()
    if shown["card"]:
        raise Refused(
            M(
                "rkboard.install.card-shown",
                "The board's loader shows {gb} GB: its SD card, not its onboard flash, so nothing was written. Take "
                "the card out to install a bootloader.",
                gb=_gb(shown["mb"]),
            )
        )
    token, said = held_words(contents(shown))
    if token != "blank" and confirm != token:  # nosec B105 - a state word, not a password
        raise Refused(
            M(
                "rkboard.install.unconfirmed",
                "The onboard flash is not blank: {held} Read it first, then confirm the install. Installing a "
                "bootloader erases it, including any logs kept on the NAND.",
                held=said,
            )
        )
    # Every file is read and checked before the erase: a file that changed, is gone or does not fit costs nothing.
    limit = (shown["sectors"] * SECTOR) if shown["sectors"] else (shown["mb"] or 0) << 20
    prepared = []
    for entry in bundle["files"]:
        path = rkusb.BOOTLOADER_STATE / bundle["id"] / entry["name"]
        try:
            data = path.read_bytes()
        except OSError:
            raise Refused(
                M(
                    "rkboard.bootloader-gone",
                    "The {name} is not ready: {file} is gone.",
                    name=bundle["title"],
                    file=entry["name"],
                )
            ) from None
        if entry.get("sha256") and hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise Refused(
                M(
                    "rkboard.bootloader-damaged",
                    "The {name} changed while it was being installed: {file} does not match its sha256.",
                    name=bundle["title"],
                    file=entry["name"],
                )
            )
        _check_at(entry["offset"], len(data), limit)
        prepared.append((data, entry["offset"]))
    total = sum(len(data) for data, _ in prepared)
    _progress("install", 0, total)
    r = run("rkdeveloptool", "ef", timeout=900)
    if r.returncode:
        raise Refused(
            M(
                "rkboard.install.erase-failed",
                "The erase failed: {detail}",
                detail=(r.stderr or r.stdout).strip()[-200:],
            )
        )
    RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    wrote = 0
    for data, offset in prepared:
        wrote += _write_at(data, offset, limit)
        _progress("install", min(wrote, total), total)
    run("rkdeveloptool", "rd", timeout=60)
    _progress("install", total, total)
    return M(
        "rkboard.installed",
        "The {name} is installed on the board's onboard flash: {mb} MB written and read back, and the board is "
        "reset. Installing it erased the flash, including any logs kept on the NAND.",
        name=bundle["title"],
        mb=wrote >> 20,
    )


def _check_at(offset, size, limit):
    """An offset and length that a bootloader file can be written at: whole sectors, and inside the flash."""
    if offset % SECTOR:
        raise Refused(M("rkboard.install.offset", "The bootloader's offset is not a whole sector."))
    if limit and offset + size > limit:
        raise Refused(M("rkboard.install.too-large", "The bootloader does not fit on the board's flash."))


def _write_at(data, offset, limit):
    """data written at a byte offset of the board's flash (rkdeveloptool wl), in pieces, each read back and compared.
    Returns the bytes written (the last piece padded to a sector)."""
    _check_at(offset, len(data), limit)
    with tempfile.TemporaryDirectory(prefix="rkboard-", dir=RUN) as tmp:
        piece, back = Path(tmp) / "piece.img", Path(tmp) / "back.img"
        sector = offset // SECTOR
        done = 0
        while done < len(data):
            chunk = data[done : done + CHUNK]
            if len(chunk) % SECTOR:
                chunk += bytes(SECTOR - len(chunk) % SECTOR)
            piece.write_bytes(chunk)
            if run("rkdeveloptool", "wl", str(sector), str(piece), timeout=600).returncode:
                raise Refused(
                    M("rkboard.install.write-failed", "Writing the bootloader failed at {mb} MB.", mb=done >> 20)
                )
            read = run("rkdeveloptool", "rl", str(sector), str(len(chunk)), str(back), timeout=600)
            if read.returncode or not back.exists() or _digest(back) != hashlib.sha256(chunk).hexdigest():
                raise Refused(
                    M(
                        "rkboard.install.verify-failed",
                        "What was written at {mb} MB did not read back the same: the flash is not usable as it is.",
                        mb=done >> 20,
                    )
                )
            sector += len(chunk) // SECTOR
            done += len(chunk)
        return done


def write_card(name, sha256, loader_id=None):
    """An image from rkusb.IMAGES (.img, or .img.xz unpacked as it goes) written to the SD card the board's loader
    shows, in pieces: each written (rkdeveloptool wl), read back (rl) and compared before the next; then the card's
    last sectors cleared and the board reset. Its checksum (the publisher's) checked first. Refused unless the
    loader shows a card."""
    try:
        sha256 = validate.sha256(sha256)
    except ValueError:
        raise Refused(M("rkboard.image-sha", "Give the image's sha256, as its publisher lists it.")) from None
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
                read = run("rkdeveloptool", "rl", str(sector), str(len(data)), str(back), timeout=600)  # bytes
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
