# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Backups and a new box as offers with sizes (item 34; Tom, 2026-10-09: "backup is just a hyperlink. It
should be an offer on the level of backup to make - settings only, settings and data, full image. An
estimated size of export is needed"; "make a new box needs a similar offer list about what to include …
books, toolkits, git repos … a size budget is critical"; "a third option … simply an export of the library
or the toolkits for updating an offline box").

The hub's half: what each level of backup holds, the sizes of everything on offer, and the filter the
download is made with. The kit and the stick (a new box, an offline box, a full image) are root's
(hub_control.py offline-kit, backup-image; usbstick.py export).

The hub's state ($STATE) is sorted into three:
  settings   what the owner chose: the hub's own JSON settings, accounts, the apps' and add-ons' settings,
             the library's and mirrors' sources, the mesh's channels, the Factory's targets
  data       what the owner made: notes, saved work, the board, the shoutbox, dropped files, imported scan
             reports, the mesh's heard nodes, the box's own git repositories
  refetched  never in a backup: books (their own choice), the firmware and git mirrors, builds and their
             caches, crash evidence, the library's archive and caches, the toolkits' cache, transient files.
             Before item 34 a backup held all of these but books (on the Lyra, 2026-10-09: 13.7 GB of builds
             and 0.7 GB of firmware, against a few MB the owner had made)."""

import json
import os
import shutil
from pathlib import Path

LEVELS = ("settings", "data")   # the download's levels; the full image is root's, to a USB stick
# Paths relative to $STATE. A file or folder not named here is settings if it is a top-level file and
# data if it is a folder: something new is kept rather than lost (and listed by `unsorted` for the tests).
DATA = ("notes", "store", "drop", "board.json", "messages.json", "service-history.json", "security-imports",
        "mesh/nodes.json", "git", "library/status.json", "library/mirrors-status.json")
REFETCHED = ("zim", "firmware", "ci", "crashwatch", "control", "kits", "source", "library/archive", "library/tmp",
             "library/apps", "library/api-cache", "library/debsecan", "library/lock", "library/github-token",
             "library/progress.json", "helper-busy.json", "boot_id")
# Syncthing's identity: only when asked for (whoever holds it can pose as the box to its peers).
SYNCTHING = (".local/state/syncthing", ".config/syncthing")
SETTINGS_DIRS = ("apps.d", "addons", "factory-targets", "library", "mesh", ".local", ".config")


def _under(rel, names):
    return any(rel == n or rel.startswith(n + "/") for n in names)


def kind_of(rel, git_mirrors=()):
    """settings, data or refetched, for a file's path relative to $STATE (git_mirrors: the mirrors' folders
    under git/). A top-level file is settings, a file in a folder is the folder's (settings folders named in
    SETTINGS_DIRS, the rest data), unless named in DATA or REFETCHED: something new is kept, not lost."""
    if not rel or _under(rel, REFETCHED) or _under(rel, git_mirrors) or rel.endswith((".tmp", ".part")) \
            or any(rel.startswith(m) for m in git_mirrors if m.endswith("--")) \
            or _under(rel, ("git/.incoming",)):
        return "refetched"
    if rel.startswith(("git/cgitrc-", "git/mirror-urls.json")):
        return "settings"
    if _under(rel, DATA):
        return "data"
    if "/" not in rel:
        return "settings"
    return "settings" if rel.split("/", 1)[0] in SETTINGS_DIRS else "data"


def git_mirrors(state, mirrors=None):
    """The git mirrors' folders under git/ (git/public/NAME.git …): fetched again, never backed up."""
    if mirrors is None:
        try:
            mirrors = json.loads((state / "library" / "mirrors.json").read_text()).get("mirrors", [])
        except (OSError, ValueError, AttributeError):
            mirrors = []
    # A mirror's submodules are mirrors too, beside it as NAME--SUB.git (on the Lyra: meshtastic-firmware--protobufs).
    return tuple(p for m in mirrors if isinstance(m, dict) and m.get("name")
                 for p in (f"git/{m.get('area', 'public')}/{m['name']}.git", f"git/{m.get('area', 'public')}/{m['name']}--"))


def walk(state, git_mirrors_=None):
    """{settings, data, syncthing: bytes, books, firmware: bytes left out} for $STATE. A folder never
    backed up is not walked (ci/ is gigabytes of small files); the two big ones the page names are measured."""
    root = Path(state)
    gm = git_mirrors(root) if git_mirrors_ is None else git_mirrors_
    sums = {"settings": 0, "data": 0, "syncthing": 0, "books": 0, "firmware": 0}
    for base, dirs, files in os.walk(root):
        rel_base = os.path.relpath(base, root)
        rel_base = "" if rel_base == "." else rel_base
        for d in list(dirs):
            rel = f"{rel_base}/{d}" if rel_base else d
            if kind_of(rel, gm) == "refetched":
                dirs.remove(d)
                if rel == "zim":
                    sums["books"] = du(root / rel)
                elif rel == "firmware":
                    sums["firmware"] = du(root / rel)
        for f in files:
            rel = f"{rel_base}/{f}" if rel_base else f
            try:
                size = os.lstat(root / rel).st_size
            except OSError:
                continue
            if _under(rel, SYNCTHING):
                sums["syncthing"] += size
                continue
            k = kind_of(rel, gm)
            if k != "refetched":
                sums[k] += size
    return sums


def du(path):
    total = 0
    for base, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(base, f)).st_size
            except OSError:
                pass
    return total


def keep_for(level, with_syncthing, git_mirrors_=()):
    """The tarfile filter for a download at `level`: members are irate-box-state/REL."""
    allowed = ("settings",) if level == "settings" else ("settings", "data")

    def keep(info):
        rel = info.name.split("/", 1)[1] if "/" in info.name else ""
        if not rel:
            return info
        if _under(rel, SYNCTHING):
            return info if with_syncthing else None
        k = kind_of(rel, git_mirrors_)
        if info.isdir():
            # Folders are walked into unless never backed up; the files inside decide.
            return None if k == "refetched" else info
        return info if k in allowed else None
    return keep


def image_estimate(root_dev_size=None, used=None):
    """A full image: the card's size, and the used space (what it compresses towards, roughly)."""
    if used is None:
        st = shutil.disk_usage("/")
        used = st.used
    return {"card": root_dev_size, "used": used}


def card_size():
    """The size of the disk holding / (from /sys/block), or None."""
    try:
        dev = os.stat("/").st_dev
        major, minor = os.major(dev), os.minor(dev)
        part = Path(f"/sys/dev/block/{major}:{minor}").resolve()
        disk = part.parent if (part / "partition").exists() else part
        return int((disk / "size").read_text()) * 512
    except (OSError, ValueError):
        return None


def plan(state, zim_dir, kits_status=None, mirrors=None, apps_dir=None, kit_titles=None):
    """Everything on offer, with sizes in bytes, for the Backup page."""
    state = Path(state)
    gm = git_mirrors(state, mirrors)
    sums = walk(state, gm)
    books = sorted(({"name": p.stem, "size": p.stat().st_size} for p in Path(zim_dir).glob("*.zim") if p.is_file()),
                   key=lambda b: b["name"])
    kits = [{"id": k, "title": (kit_titles or {}).get(k, k), "size": (v.get("cached") or {}).get("bytes") or 0}
            for k, v in sorted(((kits_status or {}).get("kits") or {}).items()) if (v.get("cached") or {}).get("bytes")]
    repos = []
    for area in ("public", "private"):
        for p in sorted((state / "git" / area).glob("*.git")) if (state / "git" / area).is_dir() else []:
            rel = f"git/{area}/{p.name}"
            repos.append({"name": p.name[:-4], "area": area, "mirror": kind_of(rel, gm) == "refetched", "size": du(p)})
    code = du(Path(apps_dir)) if apps_dir and Path(apps_dir).is_dir() else 0
    return {"levels": {"settings": sums["settings"], "data": sums["settings"] + sums["data"]},
            "syncthing": sums["syncthing"], "left_out": {"books": sums["books"], "firmware": sums["firmware"]},
            "image": image_estimate(card_size()), "books": books, "kits": kits, "repos": repos,
            "hub": code + (64 << 20)}   # the code, the apps and the release files kept: a rough floor
