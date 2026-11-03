# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The library drive, offline: root refusing a non-USB device, an unknown UUID, a filesystem it
does not mount, and a path from the hub (it takes none); the record; the lenient book and podcast
paths while the drive is absent; library.xml with books on both; and a book's archive on the drive.
No real mount, no systemctl. python3 tests/sim_librarydrive.py"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

T = Path(tempfile.mkdtemp(prefix="librarydrive-"))
for d in ("etc", "state/control", "systemd", "card/zim", "mount"):
    (T / d).mkdir(parents=True, exist_ok=True)
os.environ["HUB_ETC_DIR"] = str(T / "etc")
os.environ["HUB_STATE_DIR"] = str(T / "state")
os.environ["HUB_LIBRARY_MOUNT"] = str(T / "mount")
os.environ["HUB_SYSTEMD_ETC"] = str(T / "systemd")
os.environ["HUB_USER"] = os.environ.get("USER") or "root"
(T / "etc" / "usb-filesystems.json").write_text(json.dumps({"level": "linux"}))
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import librarydrive
from irate_box.library import librarian, podcast
from irate_box.root import librarydrive as rootdrive
from irate_box.msg import Refused

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


LSBLK = {
    "blockdevices": [
        {
            "name": "mmcblk0",
            "path": "/dev/mmcblk0",
            "type": "disk",
            "tran": None,
            "rm": "0",
            "children": [
                {"name": "mmcblk0p1", "path": "/dev/mmcblk0p1", "type": "part", "fstype": "ext4", "uuid": "card-uuid"}
            ],
        },
        {
            "name": "sda",
            "path": "/dev/sda",
            "type": "disk",
            "tran": "usb",
            "rm": "1",
            "children": [
                {
                    "name": "sda1",
                    "path": "/dev/sda1",
                    "type": "part",
                    "fstype": "ext4",
                    "uuid": "drive-uuid",
                    "label": "LIB",
                }
            ],
        },
        {
            "name": "sdb",
            "path": "/dev/sdb",
            "type": "disk",
            "tran": "usb",
            "rm": "1",
            "children": [
                {"name": "sdb1", "path": "/dev/sdb1", "type": "part", "fstype": "exfat", "uuid": "exfat-uuid"}
            ],
        },
        {
            "name": "sdc",
            "path": "/dev/sdc",
            "type": "disk",
            "tran": "usb",
            "rm": "1",
            "children": [{"name": "sdc1", "path": "/dev/sdc1", "type": "part", "fstype": "btrfs", "uuid": "disc-uuid"}],
        },
    ]
}
mounted = {"on": False}


def fake_run(*cmd, timeout=30):
    if cmd and cmd[0] == "lsblk":
        return mock.Mock(returncode=0, stdout=json.dumps(LSBLK), stderr="")
    if cmd and cmd[0] == "systemctl" and "enable" in cmd:
        mounted["on"] = True
        return mock.Mock(returncode=0, stdout="", stderr="")
    if cmd and cmd[0] == "systemctl" and "stop" in cmd:
        mounted["on"] = False
        return mock.Mock(returncode=0, stdout="", stderr="")
    return mock.Mock(returncode=0, stdout="", stderr="")


def refused(name, uuid, why):
    try:
        rootdrive.choose({"uuid": uuid, "kinds": {"podcasts": "drive", "books": "drive"}})
        check(name, False, "accepted")
    except Refused as exc:
        check(name, why in str(exc), str(exc))


with (
    mock.patch.object(rootdrive, "_run", fake_run),
    mock.patch.object(rootdrive.os.path, "ismount", lambda p: mounted["on"] and str(p) == str(rootdrive.MOUNT)),
):
    refused("an unknown UUID is refused", "nope", "No USB drive")
    refused("a drive that is not USB is refused", "card-uuid", "not on a USB drive")
    refused("a filesystem the box does not mount is refused", "disc-uuid", "does not include it")
    refused("a path from the hub is not a UUID and is refused", "/media/irate-box/library", "not a filesystem UUID")
    # A crafted drive: zim is a link to somewhere root must not chown.
    link_target = T / "elsewhere"
    link_target.mkdir()
    (rootdrive.MOUNT / "zim").symlink_to(link_target)
    try:
        rootdrive.choose({"uuid": "drive-uuid", "kinds": {"podcasts": "drive", "books": "card"}})
        check("a folder that is a link is refused before any chown", False, "accepted")
    except Refused as exc:
        check("a folder that is a link is refused before any chown", "not a real folder" in str(exc), str(exc))
    (rootdrive.MOUNT / "zim").unlink()
    said = rootdrive.choose({"uuid": "drive-uuid", "kinds": {"podcasts": "drive", "books": "card"}})
    check("a USB ext4 drive is taken by its UUID", "mounted" in str(said), said)
    check(
        "  root writes its own mount unit, no path from the hub",
        rootdrive.UNIT.is_file()
        and f"Where={rootdrive.MOUNT}" in rootdrive.UNIT.read_text()
        and "nosuid,nodev,noexec,nofail" in rootdrive.UNIT.read_text(),
    )
    check("  and a record for the hub", json.loads(rootdrive.HUB_RECORD.read_text())["uuid"] == "drive-uuid")
    check(
        "FAT and exFAT get the hub user from the mount, ext4 does not",
        "uid=" in rootdrive._unit_text("u", "vfat")
        and "gid=" in rootdrive._unit_text("u", "vfat")
        and "umask=022" in rootdrive._unit_text("u", "exfat")
        and "uid=" not in rootdrive._unit_text("u", "ext4"),
    )
    commands = []
    old_run = fake_run

    def record_run(*cmd, timeout=30):
        commands.append(cmd)
        return old_run(*cmd, timeout=timeout)

    with mock.patch.object(rootdrive, "_run", record_run):
        message = rootdrive.eject({})
    order = [c[3] if len(c) > 3 else "" for c in commands if c and c[0] == "runuser"]
    mounted["on"] = True
    librarydrive.RECORD.write_text(
        json.dumps({"uuid": "drive-uuid", "kinds": {"podcasts": "drive", "books": "card"}, "mounted": True})
    )
    check(
        "Eject rebuilds without the drive, restarts Kiwix, then unmounts",
        any("rebuild-library" in c and "--without-drive" in c for c in commands)
        and any(c[:2] == ("systemctl", "restart") and c[2] == "kiwix.service" for c in commands)
        and [i for i, c in enumerate(commands) if "rebuild-library" in c][0]
        < [i for i, c in enumerate(commands) if c[:2] == ("systemctl", "restart")][0]
        < [i for i, c in enumerate(commands) if c[:2] == ("systemctl", "stop")][0]
        and "unmounted" in str(message),
        commands,
    )

# The hub reads the record: the drive's folder for podcasts, the card for books.
with mock.patch.object(librarydrive.os.path, "ismount", lambda p: mounted["on"] and str(p) == str(rootdrive.MOUNT)):
    rec = librarydrive.status()
    check(
        "the hub reads root's record and puts podcasts on the drive",
        rec["mounted"] and rec["kinds"]["podcasts"] == "drive",
        rec,
    )
    check(
        "  place() answers the drive's folder, or the card",
        librarydrive.place("podcasts", T / "card/podcasts") == T / "mount/podcasts"
        and librarydrive.place("books", T / "card/zim") == T / "card/zim",
    )

# The drive goes away: neither kind writes to the card in its place.
mounted["on"] = False
(librarydrive.RECORD).write_text(
    json.dumps({"uuid": "drive-uuid", "kinds": {"podcasts": "drive", "books": "drive"}, "mounted": False})
)
check(
    "with the drive absent, place() says so instead of the card",
    librarydrive.place("podcasts", T / "card/podcasts") is None and librarydrive.drive_missing("books"),
)
podcast.set_settings(enabled=True, feeds=[{"id": "one", "url": "https://example.org/feed.xml", "title": "One"}])
with mock.patch.object(librarydrive.os.path, "ismount", lambda p: False):
    check(
        "  the podcast run waits, and a book bound for it is skipped",
        podcast.sync() == "waiting: the library drive is not plugged in" and librarian.book_home() is None,
        podcast.sync(),
    )

# A book's archive is beside the book, on the drive's own filesystem.
drive_book = T / "mount/zim/book.zim"
card_book = librarian.ZIM_DIR / "book.zim"
check(
    "a drive book archives on the drive, a card book on the card",
    librarian.archive_of(drive_book) == T / "mount/zim/.archive"
    and librarian.archive_of(card_book) == librarian.ARCHIVE_DIR,
    (
        librarian.archive_of(drive_book),
        T / "mount/zim/.archive",
        librarian.archive_of(card_book),
        librarian.ARCHIVE_DIR,
    ),
)

# library.xml, without and then with the drive's books.
(librarian.ZIM_DIR).mkdir(parents=True, exist_ok=True)
(librarian.ZIM_DIR / "card.zim").write_bytes(b"x")
(T / "mount" / "zim").mkdir(parents=True, exist_ok=True)
(drive_book).write_bytes(b"y")
seen = []
with (
    mock.patch.object(
        librarydrive,
        "record",
        return_value={
            "uuid": "drive-uuid",
            "mount": str(T / "mount"),
            "kinds": {"podcasts": "drive", "books": "drive"},
            "mounted": False,
        },
    ),
    mock.patch.object(librarian, "_kiwix_add", lambda lib, zims: seen.extend(zims) or []),
):
    librarian.rebuild_library()
check("with the drive absent, only the card's books are in library.xml", [p.name for p in seen] == ["card.zim"], seen)
seen.clear()
with (
    mock.patch.object(
        librarydrive,
        "record",
        return_value={
            "uuid": "drive-uuid",
            "mount": str(T / "mount"),
            "kinds": {"podcasts": "drive", "books": "drive"},
            "mounted": True,
        },
    ),
    mock.patch.object(librarian, "_kiwix_add", lambda lib, zims: seen.extend(zims) or []),
):
    librarian.rebuild_library()
check("with it back, both places' books are listed", sorted(p.name for p in seen) == ["book.zim", "card.zim"], seen)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
