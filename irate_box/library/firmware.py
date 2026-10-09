# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Firmware as content: the librarian's mirror of Meshtastic releases for the web flasher, and
the optional build cache.

What it keeps, under $HUB_FIRMWARE_ROOT (/var/lib/hub/firmware), in release.meshtastic.org's own
layout so the flasher (flasher.py, /flasher/firmware/) reads it unchanged:

  <version>/firmware-<version>.json                the release's board list
  <version>/firmware-<board>-<version>.mt.json     one board's manifest
  <version>/<the files it names>                   minus the .elf (debug only, ~50 MB a board)
  <version>/pio-deps/                              the build cache, when carried (below)
  index.json                                       the flasher's firmware list (FirmwareReleases)
  config.d.json                                    meshtasticd's bin/config.d at the newest kept
                                                   release, every file's text: what the
                                                   calculators' pinout map lists offline
  status.json                                      what is kept, when it was checked, errors

A whole GitHub release is 1.8-2.9 GB, the firmware zips 260-315 MB, but one
board's flash files 4-9 MB. So it takes only what the boards' manifests name, each file checked
against the manifest's size and MD5, and never the debug-elfs, deps or source assets.

Settings ($HUB_STATE_DIR/library/firmware.json, /admin's Firmware page):
  enabled      off by default
  boards       a list of PlatformIO targets ("rak4631"), or "all"
  keep_alpha   alphas kept (2) ;  keep_beta  betas kept (1)
  configs      on by default: keep config.d.json (above), mirror or not. From, in order: a
               mirror of meshtastic/firmware on this box (mirrors.py) that holds the release's
               tag, read with git, offline and instant; the release's own source package
               (meshtasticd-*-src.zip) if the box holds it; a sparse git fetch of that one
               folder at the release's tag (a few hundred KB); or that source package streamed
               from the release, stopping once the folder has gone past (it comes 0.3 MB into
               the 465 MB at 2.8.1)
  cache        "discard" (default) | "native" | "whole": the newest release's
               platformio-deps zip (484 MB, for native-tft), kept so builds on push (ci.py,
               CI_PIO_DEPS) need no internet. "native" keeps what a headless meshtasticd uses
               (~250 MB unpacked): not lvgl and meshtastic-device-ui, and of PlatformIO's
               download cache only the small archives (36 MB at 2.8.1), as the platform itself
               (platform-native) is only there; the big ones are the UI libraries'. Only the
               newest kept release keeps one. A build matter, not the
               flasher's: set on the Git page's Builds, and kept whether or
               not flash files are.

The firmware's source is not kept here: it is a mirror like any other repository (mirrors.py,
the Git page's Mirrors), and source_mirror() finds it.

Runs inside the librarian (its lock, schedule and progress): librarian.py firmware [--check].
Stdlib only.
"""

import hashlib
import io
import json
import lzma
import os
import re
import shutil
import struct
import tarfile
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path

from irate_box.library import librarian
from irate_box.library.librarian import LibrarianError

ROOT = Path(os.environ.get("HUB_FIRMWARE_ROOT", librarian.STATE_DIR / "firmware"))
SETTINGS = librarian.LIB_DIR / "firmware.json"
STATUS = ROOT / "status.json"
INDEX = ROOT / "index.json"
REPO = "meshtastic/firmware"
RELEASE_BASE = "https://release.meshtastic.org"
DEFAULTS = {"enabled": False, "boards": [], "keep_alpha": 2, "keep_beta": 1, "cache": "discard", "configs": True}
CONFIGS = ROOT / "config.d.json"
CONFIGS_MAX = 4 << 20  # the whole folder's text; a few hundred KB in 2026
CACHES = ("discard", "native", "whole")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9a-f]{7,}$")
BOARD_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
FILE_RE = re.compile(r"^[A-Za-z0-9_.+-]{1,128}$")
# In the deps zip, what only the touchscreen build (native-tft) uses.
UI_LIBS = {"lvgl", "meshtastic-device-ui", "SdFat", "PNGdec", "libdeflate"}
# PlatformIO's download cache (core/.cache/downloads, each archive named by the SHA-1 of its URL)
# is where an offline build finds the platform, which the zip has nowhere else. At 2.8.1 its
# three archives over this size are the UI libraries' (207 MB); the rest come to 36 MB.
BIG_ARCHIVE = 16 << 20


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
    if "configs" in changes:
        cfg["configs"] = bool(changes["configs"])
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
        if rel.get("draft") or librarian.revoked(rel):
            continue
        version = rel["tag_name"].lstrip("v")
        names = {a["name"]: a for a in rel.get("assets", [])}
        if not VERSION_RE.match(version) or f"firmware-{version}.json" not in names:
            continue
        deps = next((a for n, a in names.items() if n.startswith("platformio-deps-") and n.endswith(".zip")), None)
        source = next((a for n, a in names.items() if n.startswith("meshtasticd-") and n.endswith("-src.zip")), None)
        item = {"version": version, "tag": rel["tag_name"], "published": rel.get("published_at"),
                "notes": rel.get("body") or "", "deps": deps and {"url": deps["browser_download_url"],
                                                                     "size": deps["size"], "name": deps["name"]},
                "source": source and {"url": source["browser_download_url"], "size": source["size"], "name": source["name"]}}
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
    got, files, size, unavailable = [], 0, 0, []
    for board in wanted:
        try:
            mt = _get_json(f"{base}/firmware-{board}-{version}.mt.json")
        except (OSError, ValueError) as exc:
            log(f"firmware {version}: no manifest for {board} ({exc})")
            continue
        try:
            n, b = _mirror_board(base, vdir, version, mt, policy)
        except _Unavailable as exc:
            # One board's file missing from the release (an HTTP 404) must not stop every board
            # after it, nor the build cache: only that board is left out, unoffered (its manifest is
            # not published), and said.
            log(f"firmware {version}: {board} left out: {exc}")
            unavailable.append({"board": board, "why": str(exc)[:200]})
            continue
        files += n
        size += b
        _publish(vdir / f"firmware-{board}-{version}.mt.json", mt)
        got.append(board)
    missing = [b for b in (cfg["boards"] if cfg["boards"] != "all" else []) if b not in targets]
    return {"channel": rel["channel"], "boards": got, "files": files, "bytes": size,
            "missing": missing, "unavailable": unavailable, "fetched": librarian.now_iso()}


class _Unavailable(LibrarianError):
    """One board's file cannot be had (not found, or not what its manifest says)."""


def _mirror_board(base, vdir, version, mt, policy):
    """A board's flash files, as its manifest lists them: (files, bytes). Running out of room
    stops the whole run (LibrarianError); a file that cannot be had stops only this board."""
    files = size = 0
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
                try:
                    librarian._download(f"{base}/{name}", tmp, name=f"firmware {version}: {name}",
                                        expected=entry.get("bytes", 0))
                except LibrarianError as exc:
                    tmp.unlink(missing_ok=True)
                    if "cannot reach" in str(exc):
                        raise  # the network, not this board: the whole run stops
                    raise _Unavailable(str(exc))
                if not _have(tmp, entry):
                    tmp.unlink(missing_ok=True)
                    raise _Unavailable(f"{name}: size or MD5 does not match the manifest")
                os.replace(tmp, dest)
            files += 1
            size += entry.get("bytes", 0)
    return files, size


DEPS_RESERVE = 512 << 20  # free space the deps extraction leaves on the card

def _subset(name, mode, size=0):
    """Where a deps-zip entry goes under pio-deps/, or None to leave it out."""
    parts = name.split("/")
    # No empty part (a "//" made the rest absolute, and joining that discarded the folder),
    # no "." or "..", no backslash.
    if len(parts) < 3 or any(p in ("", ".", "..") for p in parts[:-1]) or parts[-1] in (".", "..") or "\\" in name:
        return None
    _, area, *rest = parts            # pio-deps-<env>/<area>/...
    if mode == "whole":
        return "/".join([area, *rest])
    if area == "packages":
        return "/".join(["packages", *rest])
    if area == "libdeps" and len(rest) >= 2 and rest[1] not in UI_LIBS:
        return "/".join(["libdeps", *rest[1:]])  # libdeps/<env>/<lib>/... -> libdeps/<lib>/...
    if area == "core" and rest[:2] == [".cache", "downloads"] and len(rest) == 3 and rest[2] != "usage.db" and size < BIG_ARCHIVE:
        return "/".join(["core", *rest])
    return None                        # the big archives, the rest of core/.cache, the UI libraries


def _carry_cache(rel, cfg, policy, log):
    """The newest kept release's build cache, as chosen; any other release's goes."""
    for other in ROOT.glob("*/pio-deps"):
        if other.parent.name != rel["version"] or cfg["cache"] == "discard":
            shutil.rmtree(other, ignore_errors=True)
            try:
                other.parent.rmdir()  # a release kept only for its cache (no flash files)
            except OSError:
                pass
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
    if not (tmp.is_file() and tmp.stat().st_size == deps["size"]):
        librarian._download(deps["url"], tmp, name=f"build cache {rel['version']}", expected=deps["size"], resume=True)
    work = dest.with_name("pio-deps.new")
    shutil.rmtree(work, ignore_errors=True)
    try:
        with zipfile.ZipFile(tmp) as zf:
            # What it unpacks to must fit, with room left: a small zip can claim gigabytes.
            wanted = sum(i.file_size for i in zf.infolist() if _subset(i.filename, cfg["cache"], i.file_size) and not i.is_dir())
            free = shutil.disk_usage(ROOT).free
            if wanted > free - DEPS_RESERVE:
                raise LibrarianError(f"{deps['name']} unpacks to {wanted >> 20} MB; {free >> 20} MB are free, "
                                     f"and {DEPS_RESERVE >> 20} MB must stay free")
            for info in zf.infolist():
                rel_path = _subset(info.filename, cfg["cache"], info.file_size)
                if not rel_path or info.is_dir():
                    continue
                target = work / rel_path
                if not target.resolve().is_relative_to(work.resolve()):
                    raise LibrarianError(f"{deps['name']} names a file outside its folder: {info.filename[:80]}")
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


def flush_cache(log=print):
    """Git -> Builds, Flush: the build cache is gone now, whatever the policy. The next check or
    update re-fetches it if the policy still wants one (flushing is the builds' call, not only the
    librarian's schedule)."""
    gone = 0
    for other in ROOT.glob("*/pio-deps"):
        gone += _du(other)
        shutil.rmtree(other, ignore_errors=True)
        try:
            other.parent.rmdir()
        except OSError:
            pass
    st = status()
    st["cache"] = None
    _save_status(st)
    log(f"the build cache is gone ({gone >> 20} MB freed)" if gone else "no build cache was kept")
    return gone


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


class _Inflate(io.RawIOBase):
    """A zip member's bytes (deflated or stored) as a stream, from a file or a ranged download,
    decompressed as read: no seeking, so a .tar.xz inside can be read from the start only."""
    def __init__(self, raw, method):
        self.raw, self.z = raw, zlib.decompressobj(-15) if method == 8 else None
        self.buf, self.x = b"", lzma.LZMADecompressor()

    def readable(self):
        return True

    def readinto(self, b):
        while len(self.buf) < len(b) and not self.x.eof:
            chunk = self.raw.read(1 << 16)
            if not chunk:
                break
            self.buf += self.x.decompress(self.z.decompress(chunk) if self.z else chunk)
        n = min(len(b), len(self.buf))
        b[:n], self.buf = self.buf[:n], self.buf[n:]
        return n


def _configs_from_package(open_at):
    """bin/config.d out of a Debian source package zip (meshtasticd-*-src.zip): its first
    member is the .tar.xz; read until the folder has gone past. open_at(offset) gives the zip's
    bytes from there (a file, or a ranged request)."""
    head = open_at(0).read(30)
    if head[:4] != b"PK\x03\x04":
        raise LibrarianError("the source package is not a zip")
    method = struct.unpack("<H", head[8:10])[0]
    n, m = struct.unpack("<HH", head[26:30])
    if method not in (0, 8):
        raise LibrarianError("the source package uses a compression this does not read")
    files, inside, total = [], False, 0
    with tarfile.open(fileobj=io.BufferedReader(_Inflate(open_at(30 + n + m), method), 1 << 16), mode="r|") as tf:
        for ti in tf:
            rel = ti.name.split("/", 1)[-1]
            if rel.startswith("bin/config.d/") or rel == "bin/config.d":
                inside = True
                if ti.isfile() and ti.name.endswith((".yaml", ".yml")):
                    text = tf.extractfile(ti).read().decode("utf-8", "replace")
                    total += len(text)
                    if total > CONFIGS_MAX:
                        raise LibrarianError("bin/config.d is larger than expected; not kept")
                    files.append({"path": rel[len("bin/config.d/"):], "text": text})
            elif inside:
                break
    return sorted(files, key=lambda f: f["path"])


def source_mirror():
    """The mirror of meshtastic/firmware on this box (mirrors.py), or None: the first whose
    upstream is it on github.com and whose repository exists."""
    from irate_box.library import mirrors
    for m in mirrors.load():
        if (mirrors._github(m.get("upstream", "")) or "").lower() == REPO and (mirrors.repo_path(m) / "HEAD").exists():
            return m
    return None


def _configs_from_mirror(rel):
    """bin/config.d at the release's tag, read from the mirror with git: no network."""
    from irate_box.library import mirrors
    m = source_mirror()
    if not m:
        raise LibrarianError("no mirror of meshtastic/firmware on this box")
    path = str(mirrors.repo_path(m))
    try:
        librarian._git("-C", path, "rev-parse", "--verify", "--quiet", f"refs/tags/{rel['tag']}^{{commit}}")
    except LibrarianError:
        raise LibrarianError(f"the mirror {m['name']} does not hold {rel['tag']}")
    files, total = [], 0
    for line in librarian._git("-C", path, "ls-tree", "-r", "-z", f"refs/tags/{rel['tag']}", "--", "bin/config.d/").split("\0"):
        if not line:
            continue
        meta, _, name = line.partition("\t")
        mode, kind, sha = meta.split()
        if kind != "blob" or mode == "120000" or not name.endswith((".yaml", ".yml")):
            continue
        text = librarian._git("-C", path, "cat-file", "blob", sha)
        total += len(text)
        if total > CONFIGS_MAX:
            raise LibrarianError("bin/config.d is larger than expected; not kept")
        files.append({"path": name[len("bin/config.d/"):], "text": text})
    return sorted(files, key=lambda f: f["path"]), m["name"]


def _configs_from_git(rel, work):
    librarian._git("clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", "--branch", rel["tag"],
                   f"https://github.com/{REPO}.git", str(work / "repo"))
    librarian._git("-C", str(work / "repo"), "sparse-checkout", "set", "bin/config.d")
    top = work / "repo" / "bin" / "config.d"
    files, total = [], 0
    for f in sorted(top.rglob("*")):
        if f.is_file() and f.suffix in (".yaml", ".yml") and not f.is_symlink():
            text = f.read_text(encoding="utf-8", errors="replace")
            total += len(text)
            if total > CONFIGS_MAX:
                raise LibrarianError("bin/config.d is larger than expected; not kept")
            files.append({"path": f.relative_to(top).as_posix(), "text": text})
    return files


def _ranged(url):
    real = urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": librarian.USER_AGENT}),
                                  timeout=60).url
    return lambda off: urllib.request.urlopen(urllib.request.Request(
        real, headers={"Range": f"bytes={off}-", "User-Agent": librarian.USER_AGENT}), timeout=120)


def sync_configs(rel, log=print):
    """meshtasticd's bin/config.d at the release, into config.d.json; kept until a newer
    release's replaces it. Tries the source package held here, then git at the tag, then the
    source package from the release. Returns an outcome."""
    have = librarian._read_json(CONFIGS, {})
    if have.get("tag") == rel["tag"]:
        return f"configs: {rel['tag']} already held ({len(have.get('files', []))} files)"
    src = rel.get("source")
    held = ROOT / rel["version"] / src["name"] if src else None
    tried = []
    files, method = None, None
    try:
        files, name = _configs_from_mirror(rel)
        method = f"the mirror {name}.git, at the tag {rel['tag']}"
    except LibrarianError as exc:
        tried.append(f"the mirror: {exc}")
    if not files and held and held.is_file():
        try:
            files = _configs_from_package(lambda off: _seek(open(held, "rb"), off))
            method = "the release's source package, held on this box"
        except (LibrarianError, OSError, tarfile.TarError, lzma.LZMAError, zlib.error) as exc:
            tried.append(f"the held source package: {exc}")
    if not files:
        work = Path(librarian.tempfile.mkdtemp(prefix="config.d-", dir=librarian.LIB_DIR))
        try:
            log(f"firmware configs: bin/config.d at {rel['tag']} (git)")
            files = _configs_from_git(rel, work)
            method = f"git, at the tag {rel['tag']}"
        except (LibrarianError, OSError) as exc:
            tried.append(f"git: {exc}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if not files and src:
        try:
            log(f"firmware configs: bin/config.d from {src['name']} (streamed)")
            files = _configs_from_package(_ranged(src["url"]))
            method = "the release's source package, streamed"
        except (LibrarianError, OSError, tarfile.TarError, lzma.LZMAError, zlib.error) as exc:
            tried.append(f"the release's source package: {exc}")
    if not files:
        raise LibrarianError("no configs: " + ("; ".join(tried) or f"none in bin/config.d at {rel['tag']}"))
    ROOT.mkdir(parents=True, exist_ok=True)
    _publish(CONFIGS, {"repo": REPO, "tag": rel["tag"], "version": rel["version"], "from": method,
                       "fetched": librarian.now_iso(), "files": files})
    return f"configs: {len(files)} from {rel['tag']} ({method})"


def _seek(fh, off):
    fh.seek(off)
    return fh


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
        configs = None
        if cfg["configs"] and kept:
            try:
                configs = sync_configs(kept[0], log)
            except LibrarianError as exc:
                configs = f"configs: {exc}"
            st["configs"] = configs
        if not cfg["enabled"]:
            # The build cache is the builds': kept whether or not flash files are.
            # Flash files already held go: they are what "Keep firmware on this box" keeps.
            gone = _drop_flash_files()
            st.pop("versions", None)
            st["cache"] = _carry_cache(kept[0], cfg, policy, log) if kept else None
            st.pop("error", None)
            outcome = "; ".join(x for x in (configs, f"build cache {st['cache']['bytes'] >> 20} MB" if st.get("cache") else None,
                                            f"flash files removed ({gone >> 20} MB)" if gone else None) if x) or "flash files off"
            st["outcome"] = outcome
            _save_status(st)
            return outcome
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
                  (f"; build cache {st['cache']['bytes'] >> 20} MB" if st.get("cache") else "") + \
                  (f"; {configs}" if configs else "")
    except (LibrarianError, OSError, ValueError, KeyError) as exc:
        st["error"] = str(exc)
        outcome = f"error: {exc}"
    st["outcome"] = outcome
    _save_status(st)
    return outcome


def _drop_flash_files():
    """Every release folder's flash files and manifests, leaving the build cache (pio-deps/);
    a folder left empty goes too. Returns the bytes freed."""
    gone = 0
    for d in ROOT.iterdir() if ROOT.is_dir() else []:
        if not (d.is_dir() and VERSION_RE.match(d.name)):
            continue
        for f in d.iterdir():
            if f.name == "pio-deps" or f.is_dir():
                continue
            gone += f.stat().st_size
            f.unlink()
        if not any(d.iterdir()):
            d.rmdir()
    return gone


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
    m = source_mirror()
    return {"settings": settings(), "status": status(), "targets": targets(),
            "source": m and {"name": m["name"], "area": m["area"]},
            "free_mb": librarian._free_bytes(ROOT) >> 20 if ROOT.exists() else None}
