# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Firmware as content: the librarian's mirror of Meshtastic releases for the web flasher, and
the optional build cache (plan §5, web-flasher stage 2).

What it keeps, under $HUB_FIRMWARE_ROOT (/var/lib/hub/firmware), in release.meshtastic.org's own
layout so the flasher (flasher.py, /flasher/firmware/) reads it unchanged:

  <version>/firmware-<version>.json                the release's board list
  <version>/firmware-<board>-<version>.mt.json     one board's manifest
  <version>/<the files it names>                   minus the .elf (debug only, ~50 MB a board)
  <version>/pio-deps/                              the build cache, when carried (below)
  index.json                                       the flasher's firmware list (FirmwareReleases)
  status.json                                      what is kept, when it was checked, errors

Measured 2026-10-02: a whole GitHub release is 1.8-2.9 GB, the firmware zips 260-315 MB, but one
board's flash files 4-9 MB. So it takes only what the boards' manifests name, each file checked
against the manifest's size and MD5, and never the debug-elfs, deps or source assets.

Settings ($HUB_STATE_DIR/library/firmware.json, /admin's Firmware page):
  enabled      off by default
  boards       a list of PlatformIO targets ("rak4631"), or "all"
  keep_alpha   alphas kept (2) ;  keep_beta  betas kept (1)
  cache        "discard" (default) | "native" | "whole": the newest kept release's
               platformio-deps zip (484 MB, for native-tft), kept so builds on push (ci.py,
               CI_PIO_DEPS) need no internet. "native" keeps what a headless meshtasticd uses
               (~215 MB unpacked): not lvgl and meshtastic-device-ui, not PlatformIO's copy of
               the archives. Only the newest kept release keeps one.

Runs inside the librarian (its lock, schedule and progress): librarian.py firmware [--check].
Stdlib only.
"""

import hashlib
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

import librarian
from librarian import LibrarianError

ROOT = Path(os.environ.get("HUB_FIRMWARE_ROOT", librarian.STATE_DIR / "firmware"))
SETTINGS = librarian.LIB_DIR / "firmware.json"
STATUS = ROOT / "status.json"
INDEX = ROOT / "index.json"
REPO = "meshtastic/firmware"
RELEASE_BASE = "https://release.meshtastic.org"
DEFAULTS = {"enabled": False, "boards": [], "keep_alpha": 2, "keep_beta": 1, "cache": "discard"}
CACHES = ("discard", "native", "whole")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9a-f]{7,}$")
BOARD_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
FILE_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,128}$")
# In the deps zip, what only the touchscreen build (native-tft) uses.
UI_LIBS = {"lvgl", "meshtastic-device-ui", "SdFat", "PNGdec", "libdeflate"}


def _publish(path, data):
    """JSON the web server (and builds on push, as hubci) read: world-readable, unlike the
    librarian's own files, which mkstemp makes 600."""
    librarian._write_json(path, data)
    os.chmod(path, 0o644)


def settings():
    cfg = dict(DEFAULTS)
    cfg.update(librarian._read_json(SETTINGS, {}))
    return cfg


def set_settings(**changes):
    cfg = settings()
    if "enabled" in changes:
        cfg["enabled"] = bool(changes["enabled"])
    if "boards" in changes:
        b = changes["boards"]
        if b != "all" and not (isinstance(b, list) and all(isinstance(x, str) and BOARD_RE.match(x) for x in b)):
            raise LibrarianError('boards is "all" or a list of board names')
        cfg["boards"] = b if b == "all" else sorted(set(b))
    for key in ("keep_alpha", "keep_beta"):
        if key in changes:
            n = changes[key]
            if type(n) is not int or not 0 <= n <= 5:
                raise LibrarianError(f"{key} is 0 to 5")
            cfg[key] = n
    if "cache" in changes:
        if changes["cache"] not in CACHES:
            raise LibrarianError(f"cache is one of {', '.join(CACHES)}")
        cfg["cache"] = changes["cache"]
    librarian._write_json(SETTINGS, cfg)
    return cfg


def status():
    return librarian._read_json(STATUS, {})


def _save_status(st):
    ROOT.mkdir(parents=True, exist_ok=True)
    _publish(STATUS, st)


# --- what to keep --------------------------------------------------------------------------

def _releases(cfg):
    """The releases to keep, newest first: [{version, tag, channel, title, notes, deps}]. A
    release counts only once it has its board list (firmware-<version>.json); 2.8.0.7239fe8
    was published with no assets at all. A revoked one never counts: Meshtastic marks it in
    the name only ("… Alpha (Revoked)"; 2.8.0.47db0e3 has all its assets and a packet-signing
    bug), and their own firmware list leaves it out too."""
    data = librarian._api(f"/repos/{REPO}/releases?per_page=40", librarian.token())
    alphas, betas = [], []
    for rel in data:
        if rel.get("draft") or "revoked" in (rel.get("name") or "").lower():
            continue
        version = rel["tag_name"].lstrip("v")
        names = {a["name"]: a for a in rel.get("assets", [])}
        if not VERSION_RE.match(version) or f"firmware-{version}.json" not in names:
            continue
        deps = next((a for n, a in names.items() if n.startswith("platformio-deps-") and n.endswith(".zip")), None)
        item = {"version": version, "tag": rel["tag_name"], "published": rel.get("published_at"),
                "notes": rel.get("body") or "", "deps": deps and {"url": deps["browser_download_url"],
                                                                     "size": deps["size"], "name": deps["name"]}}
        (alphas if rel.get("prerelease") else betas).append(item)
    keep = [dict(r, channel="alpha") for r in alphas[:cfg["keep_alpha"]]] + \
           [dict(r, channel="beta") for r in betas[:cfg["keep_beta"]]]
    keep.sort(key=lambda r: r["published"] or "", reverse=True)
    for r in keep:
        r["title"] = f"Meshtastic Firmware {r['version']} {r['channel'].capitalize()}"
    return keep


def _get_json(url):
    with librarian._open(url, timeout=60) as resp:
        return json.load(resp)


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _have(path, entry):
    return path.is_file() and path.stat().st_size == entry.get("bytes") and _md5(path) == entry.get("md5")


def _mirror_version(rel, cfg, policy, log):
    """One release's board list, the chosen boards' manifests, and their flash files."""
    version = rel["version"]
    vdir = ROOT / version
    vdir.mkdir(parents=True, exist_ok=True)
    base = f"{RELEASE_BASE}/{version}"
    board_list = _get_json(f"{base}/firmware-{version}.json")
    _publish(vdir / f"firmware-{version}.json", board_list)
    targets = sorted({t["board"] for t in board_list.get("targets", []) if BOARD_RE.match(t.get("board", ""))})
    wanted = targets if cfg["boards"] == "all" else [b for b in targets if b in cfg["boards"]]
    got, files, size = [], 0, 0
    for board in wanted:
        try:
            mt = _get_json(f"{base}/firmware-{board}-{version}.mt.json")
        except (OSError, ValueError) as exc:
            log(f"firmware {version}: no manifest for {board} ({exc})")
            continue
        for entry in mt.get("files", []):
            name = entry.get("name", "")
            if not FILE_RE.match(name) or name.endswith(".elf"):
                continue
            dest = vdir / name
            if not _have(dest, entry):
                need = entry.get("bytes", 0) + policy["min_free_mb"] * 2**20
                if librarian._free_bytes(ROOT) < need:
                    raise LibrarianError(f"not enough space for {name}: keep {policy['min_free_mb']} MB free")
                tmp = dest.with_name(dest.name + ".part")
                librarian._download(f"{base}/{name}", tmp, name=f"firmware {version}: {name}",
                                    expected=entry.get("bytes", 0))
                if not _have(tmp, entry):
                    tmp.unlink(missing_ok=True)
                    raise LibrarianError(f"{name}: size or MD5 does not match the manifest")
                os.replace(tmp, dest)
            files += 1
            size += entry.get("bytes", 0)
        _publish(vdir / f"firmware-{board}-{version}.mt.json", mt)
        got.append(board)
    missing = [b for b in (cfg["boards"] if cfg["boards"] != "all" else []) if b not in targets]
    return {"channel": rel["channel"], "boards": got, "files": files, "bytes": size,
            "missing": missing, "fetched": librarian.now_iso()}


def _subset(name, mode):
    """Where a deps-zip entry goes under pio-deps/, or None to leave it out."""
    parts = name.split("/")
    if len(parts) < 3 or ".." in parts:
        return None
    _, area, *rest = parts            # pio-deps-<env>/<area>/...
    if mode == "whole":
        return "/".join([area, *rest])
    if area == "packages":
        return "/".join(["packages", *rest])
    if area == "libdeps" and len(rest) >= 2 and rest[1] not in UI_LIBS:
        return "/".join(["libdeps", *rest[1:]])  # libdeps/<env>/<lib>/... -> libdeps/<lib>/...
    return None                        # core/.cache (the archives again), and the UI libraries


def _carry_cache(rel, cfg, policy, log):
    """The newest kept release's build cache, as chosen; any other release's goes."""
    for other in ROOT.glob("*/pio-deps"):
        if other.parent.name != rel["version"] or cfg["cache"] == "discard":
            shutil.rmtree(other, ignore_errors=True)
    if cfg["cache"] == "discard" or not rel.get("deps"):
        return None
    dest = ROOT / rel["version"] / "pio-deps"
    stamp = dest / ".irate-box-cache"
    if stamp.is_file() and stamp.read_text().strip() == cfg["cache"]:
        return {"version": rel["version"], "mode": cfg["cache"], "bytes": _du(dest)}
    deps = rel["deps"]
    if librarian._free_bytes(ROOT) < deps["size"] * 3 + policy["min_free_mb"] * 2**20:
        raise LibrarianError(f"not enough space for the build cache ({deps['size'] >> 20} MB zipped)")
    tmp = librarian.TMP_DIR / deps["name"]
    tmp.parent.mkdir(parents=True, exist_ok=True)
    log(f"firmware {rel['version']}: downloading the build cache ({deps['size'] >> 20} MB)")
    librarian._download(deps["url"], tmp, name=f"build cache {rel['version']}", expected=deps["size"])
    work = dest.with_name("pio-deps.new")
    shutil.rmtree(work, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp) as zf:
            for info in zf.infolist():
                rel_path = _subset(info.filename, cfg["cache"])
                if not rel_path or info.is_dir():
                    continue
                target = work / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
    except zipfile.BadZipFile:
        raise LibrarianError(f"{deps['name']} is not a whole zip")
    finally:
        tmp.unlink(missing_ok=True)
    (work / ".irate-box-cache").write_text(cfg["cache"] + "\n")
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(work, dest)
    return {"version": rel["version"], "mode": cfg["cache"], "bytes": _du(dest)}


def _du(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _write_index(kept, st):
    """The flasher's list (types/api.ts FirmwareReleases), from what is actually on disk."""
    out = {"releases": {"stable": [], "alpha": []}, "pullRequests": []}
    for rel in kept:
        if not st["versions"].get(rel["version"], {}).get("boards"):
            continue
        item = {"id": rel["tag"], "title": rel["title"], "release_notes": rel["notes"]}
        out["releases"]["alpha" if rel["channel"] == "alpha" else "stable"].append(item)
    _publish(INDEX, out)


def sync(check_only=False, log=print):
    """Bring the mirror in line with the settings. Returns a one-line outcome."""
    cfg = settings()
    st = status()
    st["last_check"] = librarian.now_iso()
    try:
        kept = _releases(cfg)
        st["latest"] = [{"version": r["version"], "channel": r["channel"]} for r in kept]
        if check_only:
            st.pop("error", None)
            _save_status(st)
            return "newest: " + ", ".join(f"{r['version']} ({r['channel']})" for r in kept)
        policy = librarian.load_config()["policy"]
        versions = {}
        for rel in kept:
            log(f"firmware {rel['version']}: {len(cfg['boards']) if cfg['boards'] != 'all' else 'all'} boards")
            versions[rel["version"]] = _mirror_version(rel, cfg, policy, log)
        # Releases no longer kept go, and so does anything else a version-shaped folder holds.
        for d in ROOT.iterdir() if ROOT.is_dir() else []:
            if d.is_dir() and VERSION_RE.match(d.name) and d.name not in versions:
                shutil.rmtree(d, ignore_errors=True)
        st["versions"] = versions
        st["cache"] = _carry_cache(kept[0], cfg, policy, log) if kept else None
        _write_index(kept, st)
        st.pop("error", None)
        outcome = f"{len(versions)} releases, {sum(v['bytes'] for v in versions.values()) >> 20} MB" + \
                  (f"; build cache {st['cache']['bytes'] >> 20} MB" if st.get("cache") else "")
    except (LibrarianError, OSError, ValueError, KeyError) as exc:
        st["error"] = str(exc)
        outcome = f"error: {exc}"
    st["outcome"] = outcome
    _save_status(st)
    return outcome


def due(hours):
    return librarian._due(status(), hours)


def cache_dir():
    """The build cache in use (for ci.py: CI_PIO_DEPS), or None."""
    c = status().get("cache")
    path = ROOT / c["version"] / "pio-deps" if c else None
    return path if path and path.is_dir() else None


def targets():
    """The boards the newest kept release offers, for /admin's picker: [{board, platform}]."""
    for v in sorted((d for d in ROOT.iterdir() if d.is_dir() and VERSION_RE.match(d.name)), reverse=True) \
            if ROOT.is_dir() else []:
        data = librarian._read_json(v / f"firmware-{v.name}.json", {})
        if data.get("targets"):
            return data["targets"]
    return []


def snapshot():
    return {"settings": settings(), "status": status(), "targets": targets(),
            "free_mb": librarian._free_bytes(ROOT) >> 20 if ROOT.exists() else None}
