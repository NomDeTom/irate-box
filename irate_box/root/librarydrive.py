# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The library drive: a USB drive root keeps mounted for the librarian's podcasts and books.

/admin asks only "use the drive whose filesystem UUID is X"; root decides the rest. It checks that
the UUID is a USB device it can see, with a filesystem the administrator allows (usbstick.LEVELS),
creates its own mount point, writes a systemd mount unit (nofail, nosuid, nodev, noexec), and
creates one folder per kind owned by the hub user. The hub reads the record it leaves in
control/library-drive.json; it never chooses a path.

    choose({"uuid": "...", "kinds": {"podcasts": "drive", "books": "card"}})
    choose({"uuid": "", ...})            no library drive (the unit is stopped and removed)
    eject()                              unmount it; the record stays, so it returns on replug
    status()

Every kind chooses the card or the drive on its own. A drive that is not plugged in is never
written to in the card's place: status() says it is absent, and the librarian skips what is bound
for it. Stdlib only.
"""

import json
import os
import pwd
import re
import subprocess
import time
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import safeio, usbstick

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
RECORD = ETC / "library-drive.json"
HUB_RECORD = STATE / "control" / "library-drive.json"
MOUNT = Path(os.environ.get("HUB_LIBRARY_MOUNT", "/media/irate-box/library"))
UNIT_DIR = Path(os.environ.get("HUB_SYSTEMD_ETC", "/etc/systemd/system"))
UNIT_NAME = "media-irate-box-library.mount"
UNIT = UNIT_DIR / UNIT_NAME
HUB_USER = os.environ.get("HUB_USER", "irate-box")
UUID_RE = re.compile(r"^[A-Za-z0-9.-]{1,64}$")
KINDS = ("podcasts", "books")
PLACES = ("card", "drive")
FOLDERS = {"podcasts": "podcasts", "books": "zim"}


def _run(*cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _record():
    try:
        data = json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {"uuid": "", "kinds": {k: "card" for k in KINDS}, "mount": str(MOUNT)}
    if not isinstance(data, dict):
        return {"uuid": "", "kinds": {k: "card" for k in KINDS}, "mount": str(MOUNT)}
    data.setdefault("kinds", {})
    for kind in KINDS:
        data["kinds"][kind] = data["kinds"].get(kind) if data["kinds"].get(kind) in PLACES else "card"
    return data


def _save(record, mounted=None):
    record = dict(record, mount=str(MOUNT), updated=int(time.time()))
    safeio.write(RECORD, json.dumps(record, indent=1), mode=0o644)
    mounted = os.path.ismount(MOUNT) if mounted is None else mounted
    hub = dict(record, mounted=bool(mounted), folders={k: str(MOUNT / f) for k, f in FOLDERS.items()})
    safeio.write(HUB_RECORD, json.dumps(hub, indent=1), mode=0o644)
    return hub


def status():
    record = _record()
    mounted = bool(record.get("uuid")) and os.path.ismount(MOUNT)
    out = dict(record, mount=str(MOUNT), mounted=mounted)
    out["folders"] = {k: str(MOUNT / f) for k, f in FOLDERS.items()} if mounted else {}
    if mounted:
        try:
            st = os.statvfs(MOUNT)
            out["total"], out["free"] = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        except OSError:
            pass
    return out


def _candidates():
    out = _run("lsblk", "-J", "-b", "-o", "NAME,PATH,TYPE,RM,TRAN,FSTYPE,SIZE,LABEL,UUID,MOUNTPOINT")
    if out.returncode != 0:
        raise Refused(M("library-drive.lsblk", "The box could not list its drives."))
    try:
        data = json.loads(out.stdout or "{}")
    except ValueError:
        raise Refused(M("library-drive.lsblk", "The box could not list its drives.")) from None
    found = []

    def walk(devs, parent_usb=False):
        for dev in devs:
            usb = parent_usb or dev.get("tran") == "usb" or str(dev.get("rm")) in ("1", "True", "true")
            found.append((dev, usb))
            walk(dev.get("children") or [], usb)

    walk(data.get("blockdevices", []))
    return found


def _find(uuid):
    for dev, usb in _candidates():
        if (dev.get("uuid") or "").lower() != uuid.lower() or not dev.get("fstype"):
            continue
        if not usb:
            raise Refused(M("library-drive.not-usb", "That filesystem is not on a USB drive the box can see."))
        fs = str(dev["fstype"]).lower()
        if fs not in usbstick.LEVELS[usbstick.level()]:
            raise Refused(
                M(
                    "library-drive.filesystem",
                    "The box does not mount {fs} drives. The administrator's list of filesystems the box mounts does not include it.",
                    fs=usbstick.FS_WORDS.get(fs, fs),
                )
            )
        return dev
    raise Refused(M("library-drive.unknown", "No USB drive with that filesystem UUID is plugged in."))


def _unit_text(uuid, fstype):
    return (
        "# Written by irate-box's library drive (irate_box/root/librarydrive.py).\n"
        "[Unit]\n"
        "Description=Irate-Box library drive\n"
        "After=local-fs.target\n"
        "\n"
        "[Mount]\n"
        f"What=/dev/disk/by-uuid/{uuid}\n"
        f"Where={MOUNT}\n"
        f"Type={fstype}\n"
        "Options=nosuid,nodev,noexec,nofail,x-systemd.device-timeout=10s\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _folders():
    uid = pwd.getpwnam(HUB_USER).pw_uid
    gid = pwd.getpwnam(HUB_USER).pw_gid
    for name in FOLDERS.values():
        path = MOUNT / name
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, uid, gid)


def choose(req):
    """Root's side of /admin's Library drive: a UUID or none, and where each kind is kept."""
    uuid = str(req.get("uuid") or "").strip()
    kinds = req.get("kinds") or {}
    if not isinstance(kinds, dict) or any(kinds.get(k, "card") not in PLACES for k in KINDS):
        raise Refused(M("library-drive.kinds", "Select the card or the library drive for each kind."))
    kinds = {k: kinds.get(k, "card") for k in KINDS}
    if uuid.lower() in ("", "none"):
        _run("systemctl", "disable", "--now", UNIT_NAME)
        UNIT.unlink(missing_ok=True)
        _run("systemctl", "daemon-reload")
        _save({"uuid": "", "kinds": {k: "card" for k in KINDS}}, mounted=False)
        return M("library-drive.none", "The box no longer uses a library drive.")
    if not UUID_RE.match(uuid):
        raise Refused(M("library-drive.uuid", "That is not a filesystem UUID."))
    dev = _find(uuid)
    if MOUNT.is_symlink():
        raise Refused(M("library-drive.mount-link", "The library drive's mount point is a link. Mount refused."))
    MOUNT.mkdir(parents=True, exist_ok=True)
    safeio.write(UNIT, _unit_text(uuid, str(dev["fstype"]).lower()), mode=0o644)
    _run("systemctl", "daemon-reload")
    out = _run("systemctl", "enable", "--now", UNIT_NAME)
    mounted = os.path.ismount(MOUNT)
    if mounted:
        _folders()
    record = {"uuid": uuid, "fstype": str(dev["fstype"]).lower(), "label": dev.get("label") or "", "kinds": kinds}
    _save(record, mounted=mounted)
    if not mounted:
        raise Refused(
            M(
                "library-drive.mount-failed",
                "The box wrote the mount unit but the drive did not mount. It is kept for the next replug.",
            )
        )
    return M("library-drive.saved", "The library drive is mounted. Each kind now goes where you selected.")


def eject(req=None):
    """Unmount it after the librarian's lock is free: the next boot or replug mounts it again."""
    record = _record()
    if not record.get("uuid"):
        raise Refused(M("library-drive.no-drive", "The box has no library drive."))
    # The librarian's own lock, taken briefly as the hub user: a download in flight finishes first.
    _run("runuser", "-u", HUB_USER, "--", "irate-box", "librarian", "library-lock-wait", timeout=300)
    _run("systemctl", "stop", UNIT_NAME)
    _run("runuser", "-u", HUB_USER, "--", "irate-box", "librarian", "rebuild-library", timeout=300)
    _run("systemctl", "restart", "kiwix.service")
    _save(record, mounted=os.path.ismount(MOUNT))
    return M("library-drive.ejected", "The library drive is unmounted. Plug it back in and it returns.")
