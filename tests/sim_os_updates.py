# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Updates by kind (root/osupdates.py): what waits split into Debian's security updates, Debian's other updates,
the board's own packages and everything else; the board's packages held and released with a record; a board
update's hold lifted for one upgrade and put back; the installed version kept in the box's own pool, the newest two
kept; and the findings the Update page and the Updates doctor show. apt, dpkg and the disk stood in; each case by
message id. python3 tests/sim_os_updates.py"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="osupdates-"))
ETC, STATE, POOL, ARCHIVES = T / "etc", T / "state", T / "pool", T / "archives"
for d in (ETC, STATE, POOL, ARCHIVES, STATE / "control"):
    d.mkdir(parents=True)
os.environ.update(
    HUB_ETC_DIR=str(ETC),
    HUB_STATE_DIR=str(STATE),
    HUB_BOARD_HOLDS=str(ETC / "board-holds.json"),
    HUB_BOARD_POOL=str(POOL),
    HUB_APT_ARCHIVES=str(ARCHIVES),
)
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids as msg_ids
from irate_box.root import osupdates

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


class R:
    def __init__(self, rc=0, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


SIM = """
Inst linux-image-vendor-rockchip (26.8.3 armhf [armbian]) []
Inst linux-u-boot-luckfox-lyra (2024.10 armhf [armbian]) []
Inst openssl (3.5.4-1~deb13u1 armhf [Debian-Security:13.1/stable]) []
Inst tzdata (2026a-1 armhf [Debian:13.1/stable]) []
Inst platformio (6.1.16 armhf [network:Meshtastic:beta]) []
"""
VERSIONS = {
    "linux-image-vendor-rockchip": "6.1.115",
    "linux-u-boot-luckfox-lyra": "2024.10",
    "bash": "1.0",
    "openssl": "1.0",
    "u-boot-tools": "2025.01",
}
INSTALLED = list(VERSIONS)
apt_mark_calls = []
apt_calls = []


def run(*cmd, timeout=120):
    if cmd[0] == "dpkg-query":
        return R(out="".join(f"{n}\t{VERSIONS[n]}\tinstall ok installed\n" for n in INSTALLED))
    if cmd[0] == "dpkg" and cmd[1] == "--print-architecture":
        return R(out="armhf\n")
    if cmd[0] == "apt-mark":
        apt_mark_calls.append(list(cmd))
        return R()
    if cmd[0] == "apt-get" and "-s" in cmd:
        return R(out=SIM)
    return R()


apt_cwds = []


def apt(argv, timeout=3600, cwd=None):
    apt_calls.append(list(argv))
    apt_cwds.append(cwd)
    if len(argv) > 1 and argv[1] == "download":
        package, _, version = argv[2].partition("=")
        (POOL / f"{package}_{version}_armhf.deb").write_text("kept")
    return R()


patches = [mock.patch.object(osupdates, "run", run), mock.patch.object(osupdates, "_apt", apt)]
for p in patches:
    p.start()
try:
    found = osupdates.kinds(SIM)
    check(
        "the waiting updates split by kind: board, security, Debian's other, and the rest",
        [i["package"] for i in found["board"]] == ["linux-image-vendor-rockchip", "linux-u-boot-luckfox-lyra"]
        and [i["package"] for i in found["security"]] == ["openssl"]
        and [i["package"] for i in found["os"]] == ["tzdata"]
        and [i["package"] for i in found["other"]] == ["platformio"],
        {k: [i["package"] for i in v] for k, v in found.items()},
    )
    said = osupdates.hold_board()
    check(
        "holding: apt-mark holds exactly the board's packages and the record keeps them",
        said["id"] == "osupdates.hold.held"
        and apt_mark_calls[-1] == ["apt-mark", "hold", "linux-image-vendor-rockchip", "linux-u-boot-luckfox-lyra"]
        and osupdates.holds() == {"linux-image-vendor-rockchip", "linux-u-boot-luckfox-lyra"}
        and json.loads((ETC / "board-holds.json").read_text())["packages"]
        == ["linux-image-vendor-rockchip", "linux-u-boot-luckfox-lyra"],
        said,
    )
    # Working the kinds out leaves the page's copy, and a held board with nothing waiting keeps its names.
    (STATE / "control" / "update-kinds.json").unlink(missing_ok=True)
    quiet = {f["id"]: f for f in osupdates.kind_findings("")}["board-updates"]
    check(
        "working the kinds out writes the Updates page's report",
        (STATE / "control" / "update-kinds.json").is_file()
        and json.loads((STATE / "control" / "update-kinds.json").read_text())["findings"],
    )
    check(
        "  nothing waiting but the board held: the finding keeps the held names",
        quiet["waiting"] == 0 and quiet["held"] == ["linux-image-vendor-rockchip", "linux-u-boot-luckfox-lyra"],
        quiet,
    )
    said = osupdates.install_kind("board", T / "update.log")
    check(
        "installing a board update: the hold is lifted, apt runs --only-upgrade, and the hold goes back",
        said["id"] == "osupdates.installed"
        and apt_mark_calls[-2][:2] == ["apt-mark", "unhold"]
        and apt_mark_calls[-1][:2] == ["apt-mark", "hold"]
        and "--only-upgrade" in apt_calls[-1]
        and "--no-download" in apt_calls[-1]
        and any(a.startswith("Dir::Cache::archives=") for a in apt_calls[-1])
        and apt_cwds[-1] == POOL
        and "linux-image-vendor-rockchip" in apt_calls[-1]
        and osupdates.holds() == {"linux-image-vendor-rockchip", "linux-u-boot-luckfox-lyra"},
        (said, apt_mark_calls[-3:], apt_calls[-1], apt_cwds[-1]),
    )
    said = osupdates.release_board()
    check(
        "releasing: apt-mark unholds the record and the record goes",
        said["id"] == "osupdates.release.released"
        and apt_mark_calls[-1][:2] == ["apt-mark", "unhold"]
        and not (ETC / "board-holds.json").exists(),
        said,
    )

    # A package the administrator held already is not this module's to record or release.
    with mock.patch.object(osupdates, "showhold", lambda: {"linux-image-vendor-rockchip"}):
        said = osupdates.hold_board()
    check(
        "a package already held before install is not recorded as this module's",
        said["id"] == "osupdates.hold.held"
        and apt_mark_calls[-1] == ["apt-mark", "hold", "linux-u-boot-luckfox-lyra"]
        and osupdates.holds() == {"linux-u-boot-luckfox-lyra"},
        (said, apt_mark_calls[-1], osupdates.holds()),
    )
    # u-boot-tools is Debian's userspace tool, not a board bootloader, and an old record holding it is released.
    check(
        "u-boot-tools is not a board package",
        osupdates._kind("u-boot-tools") is None and osupdates._kind("u-boot-rockchip") == "bootloader",
    )
    (ETC / "board-holds.json").write_text(json.dumps({"packages": ["linux-image-vendor-rockchip", "u-boot-tools"]}))
    with mock.patch.object(osupdates, "showhold", lambda: {"linux-image-vendor-rockchip", "u-boot-tools"}):
        said = osupdates.hold_board()
    check(
        "a wrong hold from the old rule is released, the board's kept",
        any(c[:3] == ["apt-mark", "unhold", "u-boot-tools"] for c in apt_mark_calls)
        and osupdates.holds() == {"linux-image-vendor-rockchip", "linux-u-boot-luckfox-lyra"},
        (said, apt_mark_calls[-1], osupdates.holds()),
    )
    osupdates.release_board()
    check(
        "releasing leaves the administrator's own hold alone",
        apt_mark_calls[-1][:2] == ["apt-mark", "unhold"]
        and set(apt_mark_calls[-1][2:]) == {"linux-image-vendor-rockchip", "linux-u-boot-luckfox-lyra"},
        apt_mark_calls[-1],
    )

    # The installed version is kept in the pool, and the newest two are kept.
    (POOL / "linux-image-vendor-rockchip_6.1.115_armhf.deb").unlink(missing_ok=True)
    (ARCHIVES / "linux-image-vendor-rockchip_6.1.115_armhf.deb").write_text("old")
    osupdates.keep_current(["linux-image-vendor-rockchip"])
    check(
        "the installed version's .deb is copied from apt's archive into the box's pool",
        any(POOL.glob("linux-image-vendor-rockchip_6.1.115_*.deb"))
        and (POOL / "linux-image-vendor-rockchip_6.1.115_armhf.deb").read_text() == "old",
    )
    # Not in apt's archive: apt-get download it while the repository still has it.
    (ARCHIVES / "linux-u-boot-luckfox-lyra_2024.10_armhf.deb").unlink(missing_ok=True)
    missing = osupdates.keep_current(["linux-u-boot-luckfox-lyra"])
    check(
        "the installed version is downloaded into the pool when apt's archive no longer has it",
        missing == [] and any(POOL.glob("linux-u-boot-luckfox-lyra_2024.10_*.deb")),
        missing,
    )

    for n, version in enumerate(("1", "2", "3")):
        deb = POOL / f"somepkg_{version}_armhf.deb"
        deb.write_text(version)
        os.utime(deb, (1000 + n, 1000 + n))
    osupdates.prune_pool()
    check(
        "the pool keeps the newest two versions of a package",
        sorted(p.name for p in POOL.glob("somepkg_*.deb")) == ["somepkg_2_armhf.deb", "somepkg_3_armhf.deb"],
    )
    apt_calls.clear()
    said = osupdates.fetch_kind("board", T / "update.log")
    check(
        "fetching a board update: --download-only into the box's own pool",
        said["id"] == "osupdates.downloaded"
        and "--download-only" in apt_calls[-1]
        and any(a.startswith("Dir::Cache::archives=") for a in apt_calls[-1]),
        apt_calls[-1],
    )

    # Holding is the administrator's setting: off, nothing is held and the finding says so.
    from irate_box.root import recommended

    with mock.patch.object(recommended, "chosen", lambda: {"board-hold": "off"}):
        said = osupdates.hold_board()
        board = {f["id"]: f for f in osupdates.kind_findings("")}["board-updates"]
    check(
        "holding off: the module holds nothing, and the board finding is a note that says so",
        said["id"] == "osupdates.hold.off" and board["status"] == "note" and "osupdates.board.off" in msg_ids(board),
        (said, board),
    )

    # The findings the page and the Updates doctor show.
    found = {f["id"]: f for f in osupdates.kind_findings(SIM)}
    os_find = found["os-updates"]
    check(
        "Debian's other updates waiting: a warning with the count and both offers",
        os_find["status"] == "warn"
        and os_find["waiting"] == 1
        and [a["choice"] for a in os_find["actions"]] == ["os-updates", "os-fetch"],
        os_find,
    )
    board = found["board-updates"]
    check(
        "the board's packages waiting behind the hold: a warning with the kernel and bootloader said, and both offers",
        board["status"] == "warn"
        and board["waiting"] == 2
        and "osupdates.board.bootloader" in msg_ids(board)
        and [a["choice"] for a in board["actions"]] == ["board-updates", "board-fetch"]
        and board["actions"][0].get("confirm") is not None,
        board,
    )
    # Unheld: the same packages, with the offer to hold them.
    if (ETC / "board-holds.json").exists():
        (ETC / "board-holds.json").unlink()
    board = {f["id"]: f for f in osupdates.kind_findings("")}["board-updates"]
    check(
        "not held: a warning that says so, with the offer to hold",
        board["status"] == "warn"
        and "osupdates.board.unheld" in msg_ids(board)
        and [a["choice"] for a in board["actions"]] == ["board-hold"],
        board,
    )
    report = osupdates.kinds_report("")
    check(
        "the kinds report is kept for the Updates page's cards",
        json.loads((STATE / "control" / "update-kinds.json").read_text())["findings"] == report["findings"],
    )
finally:
    for p in patches:
        p.stop()

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
