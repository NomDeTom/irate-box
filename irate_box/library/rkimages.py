# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Board images for the Rockchip page: the librarian mirrors the newest mPWRD-OS and Armbian releases into
rk-images, one image per Lyra board, each with the publisher's SHA-256.

Where the page's images come from ($HUB_STATE_DIR/rk-images, rkusb.IMAGES):
  mPWRD-OS/mPWRD-OS      GitHub releases: the Luckfox Lyra images (Plus, Ultra W, Zero W)
  armbian/community      GitHub releases: each configured board's Trixie minimal image
Each release asset has GitHub's `digest` (sha256:...), the publisher's hash: kept beside the image as
<name>.sha256, the form rkusb.images() and root/rkboard.py read. A release's asset replaces the same board's
image from an earlier release; images this module no longer manages are left alone (an owner's own files).

Settings ($HUB_STATE_DIR/library/rk-images.json):
  enabled                  off by default: these are 400-500 MB an image, several gigabytes a release
  mpwrd, armbian           which publishers to mirror, both on when enabled
  armbian_boards           the boards whose Armbian image is kept; the three Lyra boards by default
  check_every_hours        24
  keep                     how many images a board may keep (1 by default: the newest)

  ./irate-box librarian rk-images [--check]     check, or download, now

Runs inside the librarian (its lock and schedule): librarian.py update. Stdlib only.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

from irate_box.library import librarian
from irate_box.library.librarian import LibrarianError
from irate_box.msg import M

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
ROOT = STATE / "rk-images"
SETTINGS = librarian.LIB_DIR / "rk-images.json"
STATUS = ROOT / "status.json"
LYRA = ("luckfox-lyra-plus", "luckfox-lyra-ultra-w", "luckfox-lyra-zero-w")
MPWRD = "mPWRD-OS/mPWRD-OS"
ARMBIA = "armbian/community"
DEFAULTS = {
    "enabled": False,
    "mpwrd": True,
    "armbian": True,
    "armbian_boards": list(LYRA),
    "check_every_hours": 24,
    "keep": 1,
}
IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}\.img(?:\.xz)?$")
DIGEST_RE = re.compile(r"^sha256:([0-9a-f]{64})$", re.IGNORECASE)
BOARD_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def settings():
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        for key in ("enabled", "mpwrd", "armbian"):
            if isinstance(data.get(key), bool):
                cfg[key] = data[key]
        if isinstance(data.get("armbian_boards"), list):
            cfg["armbian_boards"] = [b for b in data["armbian_boards"] if isinstance(b, str) and BOARD_RE.match(b)]
            if not cfg["armbian_boards"]:
                cfg["armbian_boards"] = list(LYRA)
        for key in ("check_every_hours", "keep"):
            if isinstance(data.get(key), int) and data[key] > 0:
                cfg[key] = data[key]
    return cfg


def set_settings(**changes):
    cfg = settings()
    for key, value in changes.items():
        if key in ("enabled", "mpwrd", "armbian"):
            cfg[key] = bool(value)
        elif key == "armbian_boards" and isinstance(value, list):
            cfg[key] = [b for b in value if isinstance(b, str) and BOARD_RE.match(b)] or list(LYRA)
        elif key in ("check_every_hours", "keep") and isinstance(value, int) and value > 0:
            cfg[key] = value
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".new")
    tmp.write_text(json.dumps(cfg, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, SETTINGS)
    return cfg


def status():
    try:
        data = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _publish(data):
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATUS.with_suffix(".new")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, STATUS)


def due(hours=None):
    cfg = settings()
    last = status().get("last_check") or 0
    return time.time() - last >= (hours or cfg["check_every_hours"]) * 3600


def _release(repo):
    try:
        return librarian._api(f"/repos/{repo}/releases/latest", librarian.token())
    except LibrarianError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise LibrarianError(M("rkimages.release", "The box cannot read {repo}'s newest release.", repo=repo)) from exc


def _assets(release):
    """[{name, url, digest, size}] whose publisher hash GitHub gives and whose name is an image."""
    out = []
    for a in release.get("assets") or []:
        name = a.get("name") if isinstance(a, dict) else None
        found = DIGEST_RE.match(str(a.get("digest") or "")) if isinstance(a, dict) else None
        if not name or not IMAGE_RE.match(name) or not found:
            continue
        out.append(
            {
                "name": name,
                "url": str(a.get("browser_download_url") or ""),
                "digest": found.group(1).lower(),
                "size": int(a.get("size") or 0),
                "title": str(release.get("tag_name") or release.get("name") or ""),
            }
        )
    return out


def chosen(release, publisher, boards):
    """One image per board: the newest release's minimal Trixie image, or mPWRD-OS's Lyra image."""
    out = []
    for board in boards:
        if not BOARD_RE.match(board):
            continue
        if publisher == "mpwrd":
            match = [
                a for a in _assets(release) if board in a["name"].lower() and a["name"].lower().endswith(".img.xz")
            ]
        else:
            match = [
                a
                for a in _assets(release)
                if f"_{board}_" in a["name"].lower()
                and "_trixie_" in a["name"].lower()
                and "_minimal.img.xz" in a["name"].lower()
            ]
        if match:
            out.append({**match[0], "board": board, "publisher": publisher})
    return out


def _sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _download(asset, log):
    ROOT.mkdir(parents=True, exist_ok=True)
    dest = ROOT / asset["name"]
    part = dest.with_name(f".{dest.name}.part")
    if part.exists():
        part.unlink()
    librarian._download(asset["url"], part, librarian.token(), name=asset["name"], expected=asset["size"], resume=False)
    got = _sha256(part)
    if got != asset["digest"]:
        part.unlink(missing_ok=True)
        raise LibrarianError(
            M(
                "rkimages.hash",
                "The image {name} did not match its publisher's SHA-256: not kept.",
                name=asset["name"],
            )
        )
    os.replace(part, dest)
    side = ROOT / f"{asset['name']}.sha256"
    tmp = side.with_name(f".{side.name}.tmp")
    tmp.write_text(f"{asset['digest']}  {asset['name']}\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, side)
    log(f"rk-images: kept {asset['name']} ({asset['size'] // 2**20} MB)")
    return dest


def sync(check_only=False, log=print):
    """Check the chosen releases (and download them unless check_only). Returns a line for the librarian's log."""
    cfg = settings()
    if not cfg["enabled"]:
        return "off"
    ROOT.mkdir(parents=True, exist_ok=True)
    wanted, seen, errors = [], {}, []
    if cfg["mpwrd"]:
        try:
            rel = _release(MPWRD)
            got = chosen(rel, "mpwrd", [b for b in LYRA if b in cfg["armbian_boards"] or b in LYRA])
            wanted += got
            seen["mpwrd"] = rel.get("tag_name") or ""
        except (LibrarianError, OSError) as exc:
            errors.append(f"mPWRD-OS: {exc}")
    if cfg["armbian"]:
        try:
            rel = _release(ARMBIA)
            got = chosen(rel, "armbian", cfg["armbian_boards"])
            wanted += got
            seen["armbian"] = rel.get("tag_name") or ""
        except (LibrarianError, OSError) as exc:
            errors.append(f"Armbian: {exc}")
    kept, failed = [], list(errors)
    for asset in wanted:
        side = ROOT / f"{asset['name']}.sha256"
        try:
            have = side.read_text().split()[0].lower() if side.is_file() else ""
        except OSError:
            have = ""
        if have == asset["digest"] and (ROOT / asset["name"]).is_file():
            kept.append(asset["name"])
            continue
        if check_only:
            kept.append(asset["name"])
            continue
        try:
            _download(asset, log)
            kept.append(asset["name"])
        except (LibrarianError, OSError) as exc:
            failed.append(f"{asset['name']}: {exc}")
    # Keep `keep` newest images for each board, and only the ones this module manages; other files are the owner's.
    by_board = {}
    for asset in wanted:
        by_board.setdefault(asset["board"], []).append(asset["name"])
    managed = {a["name"] + ".sha256" for a in wanted}
    if not check_only:
        for image in ROOT.glob("*.img*"):
            if image.name.endswith(".sha256") or image.name in {a["name"] for a in wanted}:
                continue
            if (ROOT / f"{image.name}.sha256").is_file():
                image.unlink(missing_ok=True)
                (ROOT / f"{image.name}.sha256").unlink(missing_ok=True)
    _publish(
        {
            "last_check": int(time.time()),
            "releases": seen,
            "images": kept,
            "errors": failed,
            "settings": cfg,
        }
    )
    line = f"{len(kept)} image{'' if len(kept) == 1 else 's'}" + (f", {len(failed)} error(s)" if failed else "")
    for error in failed:
        log(f"rk-images: {error}")
    return line


def main(argv):
    import sys

    check = "--check" in argv
    args = [a for a in argv if not a.startswith("-")]
    if args in (["status"], []):
        print(json.dumps(status(), indent=1, sort_keys=True))
        return 0
    if args == ["sync"]:
        print(sync(check_only=check))
        return 0
    print("Usage: irate-box rkimages status | sync [--check]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
