#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The OS's packages by kind, and the board's own packages held.

The hub updates the OS's packages through its root helper and apt; it cannot update the image (a new
release is a new card). Three kinds, each with its own choice:

  security   Debian's security archive and the fixes a point release folds in (as before, #176)
  os         Debian's other waiting updates
  board      the kernel, device tree, bootloader, board package and firmware, from the board's maker

The board's packages are held with `apt-mark hold` when irate-box installs, so neither a security
update nor unattended-upgrades swaps the kernel by surprise. What this module held is recorded in
/etc/hub/board-holds.json, so uninstall.sh releases it and a later update holds it again. A board
update is fetched into a pool of its own (/var/cache/irate-box/board), the installed version kept
beside it, so the update is ready offline and the old kernel is there to go back to.

  irate-box osupdates hold-board       hold the board's packages now, and record them
  irate-box osupdates release-board    let them move again (uninstall.sh's)
  irate-box osupdates kinds            what waits, by kind

Read only unless a command says otherwise. Stdlib only.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from irate_box.finding import action, finding
from irate_box.msg import M, Refused, render
from irate_box.root import aptram, safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CONTROL = STATE / "control"
HOLDS = Path(os.environ.get("HUB_BOARD_HOLDS", ETC / "board-holds.json"))
POOL = Path(os.environ.get("HUB_BOARD_POOL", "/var/cache/irate-box/board"))
APT_ARCHIVES = Path(os.environ.get("HUB_APT_ARCHIVES", "/var/cache/apt/archives"))
BOARD_KEEP = 2  # the version installed and the one before it, as a toolkit keeps
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "APT_LISTCHANGES_FRONTEND": "none", "LC_ALL": "C"}

# The board's own packages, by what they are. The kernel's headers are left out: they are a build aid,
# not something the next boot runs.
BOARD = (
    ("kernel", re.compile(r"^linux-image-")),
    ("device tree", re.compile(r"^linux-dtb-")),
    ("bootloader", re.compile(r"^(?:linux-u-boot|u-boot)")),
    ("board package", re.compile(r"^armbian-bsp")),
    ("firmware", re.compile(r"^(?:armbian-firmware|linux-firmware|firmware-)")),
)
KINDS = ("security", "os", "board")


def run(*cmd, timeout=120):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _kind(package):
    for kind, pattern in BOARD:
        if pattern.match(package):
            return kind
    return None


def _board(name):
    return _kind(name) is not None


def holds():
    """The board packages this module holds, as a set of names."""
    data = _read(HOLDS, {})
    return set(data.get("packages") or []) if isinstance(data, dict) else set()


def _record(packages, *, keep_fetched=None):
    data = _read(HOLDS, {})
    if not isinstance(data, dict):
        data = {}
    data["packages"] = sorted(set(packages))
    data["at"] = time.time()
    if keep_fetched is not None:
        data["fetched"] = keep_fetched
    _write(HOLDS, data)


def _apt_mark(verb, packages):
    packages = [p for p in packages if p]
    if not packages:
        return 0
    out = run("apt-mark", verb, *packages, timeout=120)
    return out.returncode


def installed_versions():
    """{package: version} for the installed packages."""
    out = run("dpkg-query", "-W", "-f", "${Package}\t${Version}\t${Status}\n", timeout=120)
    if out.returncode != 0:
        raise OSError((out.stderr or out.stdout or "dpkg-query failed").strip()[-200:])
    found = {}
    for line in out.stdout.splitlines():
        name, _, rest = line.partition("\t")
        version, _, status = rest.partition("\t")
        if name and "install ok installed" in status:
            found[name.split(":")[0]] = version
    return found


def installed_board():
    """{package: version} for the board's packages that are installed."""
    try:
        return {name: version for name, version in installed_versions().items() if _board(name)}
    except OSError:
        return {}


def hold_board():
    """Hold the board's packages now and record them, so a security update cannot swap the kernel by surprise."""
    board = installed_board()
    if not board:
        return M("osupdates.hold.none", "This box runs none of a board's own packages, so there is nothing to hold.")
    if _apt_mark("hold", board):
        raise Refused(M("osupdates.hold.failed", "apt-mark could not hold the board's packages."))
    _record(set(holds()) | set(board))
    return M(
        "osupdates.hold.held",
        {
            "one": "The box holds {n} of the board's own package, so no update swaps the kernel by surprise.",
            "other": "The box holds {n} of the board's own packages, so no update swaps the kernel by surprise.",
        },
        n=len(board),
    )


def release_board():
    """Let the held board packages move again. Idempotent."""
    held = holds()
    if not held:
        return M("osupdates.release.none", "The box holds none of the board's own packages.")
    _apt_mark("unhold", held)
    try:
        HOLDS.unlink()
    except OSError:
        pass
    return M(
        "osupdates.release.released",
        {
            "one": "The box released {n} of the board's own package.",
            "other": "The box released {n} of the board's own packages.",
        },
        n=len(held),
    )


def kinds(simulated=""):
    """What waits, by kind: {security, os, board, other}, each a list of {package, version, origin}."""
    from irate_box.root import security

    every = security.pending_updates(simulated)
    secure = set(security.pending_security(simulated))
    out = {kind: [] for kind in (*KINDS, "other")}
    for item in every:
        if _board(item["package"]):
            out["board"].append(item)
        elif item["package"] in secure:
            out["security"].append(item)
        elif "Debian" in (item.get("origin") or "") and "Debian-Security" not in (item.get("origin") or ""):
            out["os"].append(item)
        else:
            out["other"].append(item)
    return out


def _simulated():
    with aptram.argv(["apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade"]) as full:
        return run(*full, timeout=180).stdout


def waiting(kind, simulated=None):
    return kinds(simulated if simulated is not None else _simulated())[kind]


def _deb_of(package, version):
    return any(APT_ARCHIVES.glob(f"{package}_{version}_*.deb"))


def fetched(kind, items):
    """How many of the waiting updates are already downloaded: in apt's archives for os, in the board pool for board."""
    if kind == "board":
        return sum(1 for item in items if any(POOL.glob(f"{item['package']}_{item['version']}_*.deb")))
    return sum(1 for item in items if _deb_of(item["package"], item["version"]))


def _apt(argv, timeout):
    with aptram.argv(argv) as full:
        return subprocess.run(  # noqa: TID251
            full,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=dict(os.environ, **APT_ENV),
            timeout=timeout,
        )


def _log_write(log, text):
    log.write(text)
    log.flush()


def _take(kind, log, items, install):
    """Install or download one kind's waiting updates. Board packages are released for this run and held again."""
    packages = [item["package"] for item in items]
    if not packages:
        return M("osupdates.none", "No update of this kind is waiting.")
    board = kind == "board"
    if board:
        keep_current(packages)
        _apt_mark("unhold", packages)
    argv = ["apt-get", "install", "-y", "--only-upgrade", *([] if install else ["--download-only"])]
    if board and not install:
        POOL.mkdir(parents=True, exist_ok=True)
        argv += ["-o", f"Dir::Cache::archives={POOL}"]
    if install:
        argv += ["-o", "Dpkg::Options::=--force-confold"]
    argv += packages
    try:
        _log_write(log, "$ " + " ".join(argv) + "\n\n")
        out = _apt(argv, 3600)
        _log_write(log, (out.stdout or "") + (out.stderr or "") + "\n")
    finally:
        if board:
            _apt_mark("hold", packages)
            _record(set(holds()) | set(packages), keep_fetched=_pool_versions())
    if out.returncode != 0:
        raise Refused(
            M(
                "osupdates.failed",
                "apt stopped with exit code {code}. Read the log on Updates.",
                code=out.returncode,
            )
        )
    if install:
        return M(
            "osupdates.installed",
            {"one": "The box installed {n} update.", "other": "The box installed {n} updates."},
            n=len(packages),
        )
    return M(
        "osupdates.downloaded",
        {
            "one": "The box downloaded {n} update. It is ready to install.",
            "other": "The box downloaded {n} updates. They are ready to install.",
        },
        n=len(packages),
    )


def keep_current(packages):
    """Keep the installed version of each package beside the new one, so the old kernel is there to go back to."""
    try:
        installed = installed_versions()
    except OSError:
        return
    POOL.mkdir(parents=True, exist_ok=True)
    for package in packages:
        version = installed.get(package)
        if not version or any(POOL.glob(f"{package}_{version}_*.deb")):
            continue
        found = sorted(APT_ARCHIVES.glob(f"{package}_{version}_*.deb"))
        if found:
            try:
                shutil.copy2(found[-1], POOL / found[-1].name)
            except OSError:
                pass
    prune_pool()


def _pool_versions():
    """{package: newest version in the pool} for the record."""
    out = {}
    for deb in POOL.glob("*.deb"):
        package, _, rest = deb.name.partition("_")
        version = rest.rpartition("_")[0]
        if package and version:
            out[package] = max(out.get(package, ""), version)
    return out


def prune_pool():
    """Keep the newest two versions of each package in the pool: the one fetched and the one before it."""
    try:
        debs = sorted(POOL.glob("*.deb"))
    except OSError:
        return
    by_package = {}
    for deb in debs:
        package = deb.name.partition("_")[0]
        by_package.setdefault(package, []).append(deb)
    for found in by_package.values():
        if len(found) <= BOARD_KEEP:
            continue
        found.sort(key=lambda p: p.stat().st_mtime)
        for old in found[:-BOARD_KEEP]:
            try:
                old.unlink()
            except OSError:
                pass


def install_kind(kind, log_path):
    """Install the waiting updates of one kind, with the board's packages released for this run and held again."""
    items = waiting(kind)
    if not items:
        return M("osupdates.none", "No update of this kind is waiting.")
    with safeio.open_new(log_path) as log:
        return _take(kind, log, items, True)


def fetch_kind(kind, log_path):
    """Download one kind's waiting updates without installing them."""
    items = waiting(kind)
    if not items:
        return M("osupdates.none", "No update of this kind is waiting.")
    with safeio.open_new(log_path) as log:
        return _take(kind, log, items, False)


def check_kind(kind, log_path):
    """Apt's lists afresh, then what waits of this kind, said."""
    with safeio.open_new(log_path) as log:
        _log_write(log, "$ apt-get update\n\n")
        out = _apt(["apt-get", "update", "-q"], 900)
        _log_write(log, (out.stdout or "") + (out.stderr or "") + "\n")
    items = waiting(kind)
    if not items:
        return M("osupdates.none", "No update of this kind is waiting.")
    return M(
        "osupdates.waiting",
        {"one": "{n} update of this kind is waiting.", "other": "{n} updates of this kind are waiting."},
        n=len(items),
    )


def _kind_finding(kind, items, simulated):
    title = {
        "os": M("osupdates.os.title", "Debian's other updates"),
        "board": M("osupdates.board.title", "The board's packages"),
    }[kind]
    down = fetched(kind, items)
    if kind == "os":
        if not items:
            return [
                finding(
                    "os-updates",
                    title,
                    "ok",
                    M("osupdates.os.none", "No other Debian update is waiting."),
                )
            ]
        return [
            finding(
                "os-updates",
                title,
                "warn",
                M(
                    "osupdates.os.waiting",
                    {
                        "one": "{n} Debian update is waiting, apart from security's.",
                        "other": "{n} Debian updates are waiting, apart from security's.",
                    },
                    n=len(items),
                ),
                M(
                    "osupdates.os.why",
                    "These are Debian's own packages, outside the security archive. They are the point releases and routine fixes.",
                ),
                M("osupdates.os.fix", "Download and install them here, or leave them until you need them."),
                evidence=[f"{item['package']} {item['version']}" for item in items],
                waiting=len(items),
                fetched=down,
                packages=[item["package"] for item in items],
                actions=[
                    action(
                        "os-updates",
                        M(
                            "osupdates.os.install",
                            {"one": "Install {n} Debian update", "other": "Install {n} Debian updates"},
                            n=len(items),
                        ),
                        M("osupdates.os.install.confirm", "This can take several minutes on this board."),
                    ),
                    action("os-fetch", M("osupdates.download", "Download {n}", n=len(items))),
                ],
            )
        ]
    board = installed_board()
    held = holds() & set(board)
    evidence = [f"{item['package']} {item['version']}" for item in items]
    if not board:
        return [
            finding(
                "board-updates",
                title,
                "ok",
                M("osupdates.board.none", "This box runs none of a board's own packages."),
            )
        ]
    if not items and not held:
        return [
            finding(
                "board-updates",
                title,
                "warn",
                M("osupdates.board.unheld", "The board's own packages are not held."),
                M(
                    "osupdates.board.unheld.why",
                    "Without the hold, a security update or unattended-upgrades can swap the kernel by surprise.",
                ),
                M(
                    "osupdates.board.unheld.fix",
                    "Hold them. Their updates are then fetched and installed here, with the warning.",
                ),
                evidence=sorted(board),
                actions=[action("board-hold", M("osupdates.board.hold", "Hold the board's packages"))],
            )
        ]
    if not items:
        return [
            finding(
                "board-updates",
                title,
                "ok",
                M(
                    "osupdates.board.ok",
                    {
                        "one": "{n} board package is held, and none waits for an update.",
                        "other": "{n} board packages are held, and none waits for an update.",
                    },
                    n=len(held),
                ),
                evidence=sorted(held),
            )
        ]
    return [
        finding(
            "board-updates",
            title,
            "warn",
            M(
                "osupdates.board.waiting",
                {
                    "one": "{n} of the board's own packages waits behind the hold.",
                    "other": "{n} of the board's own packages wait behind the hold.",
                },
                n=len(items),
            ),
            M(
                "osupdates.board.why",
                "On this image /boot holds one kernel. A kernel that does not boot leaves a headless box down until its "
                "card is rewritten, and the kernel can be one the image was not built with.",
            ),
            M(
                "osupdates.board.fix",
                "The offer lifts the hold for one upgrade and holds again after. Going back works only while the box "
                "still boots.",
            ),
            notes=[
                M(
                    "osupdates.board.bootloader",
                    "A bootloader package does not flash itself: installing its update does nothing unless the board's "
                    "maker's tool writes it.",
                )
            ],
            evidence=evidence,
            waiting=len(items),
            fetched=down,
            packages=[item["package"] for item in items],
            held=sorted(held),
            actions=[
                action(
                    "board-updates",
                    M(
                        "osupdates.board.install",
                        {"one": "Install {n} board update", "other": "Install {n} board updates"},
                        n=len(items),
                    ),
                    M(
                        "osupdates.board.install.confirm",
                        "Install the board's waiting packages? The hold is lifted for this upgrade and put back after. "
                        "One kernel and no fallback: a kernel that does not boot needs the card rewritten.",
                    ),
                ),
                action("board-fetch", M("osupdates.download", "Download {n}", n=len(items))),
            ],
        )
    ]


def kind_findings(simulated):
    """The OS and board kinds as findings, for the Updates page and the Updates doctor. Security's is its own."""
    found = kinds(simulated)
    return _kind_finding("os", found["os"], simulated) + _kind_finding("board", found["board"], simulated)


def _main(argv):
    if not argv:
        print(__doc__)
        return 2
    verb = argv[0]
    if verb == "hold-board":
        print(render(hold_board()))
        return 0
    if verb == "release-board":
        print(render(release_board()))
        return 0
    if verb == "kinds":
        found = kinds()
        for kind in (*KINDS, "other"):
            print(f"{kind}: {', '.join(item['package'] for item in found[kind]) or 'none'}")
        return 0
    print(f"osupdates: unknown command: {verb}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
