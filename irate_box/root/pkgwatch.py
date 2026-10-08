# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Packages from their makers' own repositories, watched for updates on the channel the owner picks
(Tom, 2026-10-08: "Add Meshtasticd to the list of apps being watched for updates, with options to
automatically update against beta, alpha or nightly, or alpha/nightly after a certain period of
time"). Root's half; the hub asks through hub_control.py, the librarian on its schedule.

A watched package is packages.d/<id>.json in the hub's code (root-owned): the Debian package, its
maker's channels (each an apt repository, {suite} standing for this box's Debian_13 or the like),
the maker's signing key (shipped in config/signing/, its fingerprint in the manifest) and its unit.
meshtasticd's channels are Meshtastic's openSUSE Build Service repositories (beta, alpha, daily).

Each check (online) reads the chosen channel through an apt view of its own (that repository
alone, signed by the shipped key, its lists kept here, never the box's own sources) and downloads
a build it hasn't seen into a cache, noting when it was first seen. A maker's repository holds
only its newest build, so this cache is what lets "after N days" install a build that has been out
that long once a newer one has replaced it, and what makes Roll back work offline.

The owner's mode (the box doctor's sense of consent: nothing changes the box until chosen):
  watch    checks and says what's newer on the channel; the box's own apt carries on as before
           (the default)
  auto     installs each new build on the channel as it's seen
  aged     installs the newest build that has been out at least the chosen number of days
With aged, apt's preferences keep the box's own `apt upgrade` from taking the package from the
maker's repositories before its time (/etc/apt/preferences.d/irate-box-<id>.pref); only this installs
it then. Update (a build now) and Roll back (the one before) work in any mode.

An image may have a tool of its own for the same package (mPWRD-OS's mpwrd-menu, for meshtasticd; Tom,
2026-10-08: "There's tools in mpwrd-os for this as well - it needs to adapt"). A manifest's "image" says
how that tool keeps the channel (one apt list per channel, and its key, by name); then the channel is
read the way the tool reads it (exactly one list: that channel; none or several: from the installed
build), and choosing one here writes the same files the tool would (the other channels' taken away,
the key from the box's own copy), so the tool and the box's own apt follow the same channel. Only
"aged" holds the package from apt (preferences): with one channel listed, apt upgrade already gives
the newest, which is what "auto" means.

Under ROOT (/var/cache/irate-box/pkgwatch/<id>/, root's): sources.list, lists/, debs/, seen.json.
Settings in /etc/hub/pkgwatch.json (root's); what the hub may show in control/pkgwatch.json."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

CODE = Path(__file__).resolve().parents[2]
DEFS = Path(os.environ.get("HUB_PKG_DEFS", CODE / "packages.d"))
ROOT = Path(os.environ.get("HUB_PKG_ROOT", "/var/cache/irate-box/pkgwatch"))
ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
SETTINGS = ETC / "pkgwatch.json"
PUBLIC = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control" / "pkgwatch.json"
PREFS = Path(os.environ.get("HUB_APT_PREFS_DIR", "/etc/apt/preferences.d"))
OS_RELEASE = Path(os.environ.get("HUB_OS_RELEASE", "/etc/os-release"))
IMAGE_ROOT = Path(os.environ.get("HUB_IMAGE_ROOT", "/"))   # where an image's own files are (tests move it)
MODES = ("watch", "auto", "aged")
DAYS = (1, 3, 7, 14, 30)
KEEP = 8                     # builds kept in the cache, besides the installed and the previous
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9.+~:-]{1,100}$")
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "APT_LISTCHANGES_FRONTEND": "none", "LC_ALL": "C"}


def run(cmd, timeout=900, cwd=None):
    """Every command goes through here (the tests stand it in)."""
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, env=dict(os.environ, **APT_ENV))


def _json(p, default):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


def _write(p, data, mode=0o644):
    from irate_box.root import safeio
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    safeio.write(Path(p), data if isinstance(data, str) else json.dumps(data, indent=1), mode)


def definitions():
    """{id: manifest} from the hub's code; anything malformed left out."""
    out = {}
    for f in sorted(DEFS.glob("*.json")):
        d = _json(f, None)
        if isinstance(d, dict) and ID_RE.match(str(d.get("id", ""))) and d["id"] == f.stem and isinstance(d.get("channels"), dict) \
                and d.get("package") and re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", d["package"]) \
                and all(re.fullmatch(r"https://[A-Za-z0-9./:_{}-]+/", u) for u in d["channels"].values()):
            out[d["id"]] = d
    return out


def _def(pid):
    d = definitions().get(pid)
    if not d:
        raise ValueError(f"{pid} is not a watched package")
    return d


def suite():
    """The maker's name for this box's distribution: Debian_13 on trixie."""
    rel = dict(l.split("=", 1) for l in OS_RELEASE.read_text().splitlines() if "=" in l)
    ident, ver = rel.get("ID", "").strip('"'), rel.get("VERSION_ID", "").strip('"')
    if ident != "debian" or not ver.isdigit():
        raise ValueError(f"the makers' repositories are for Debian; this box is {rel.get('PRETTY_NAME', ident)}")
    return f"Debian_{ver}"


def arch():
    return run(["dpkg", "--print-architecture"]).stdout.strip() or "?"


def installed(pkg):
    r = run(["dpkg-query", "-W", "-f", "${Status}\t${Version}", pkg])
    status, _, ver = r.stdout.partition("\t")
    return ver.strip() if r.returncode == 0 and status.endswith("installed") else None


def newer(a, b):
    """dpkg's own order: is version a newer than b?"""
    return bool(a) and (not b or run(["dpkg", "--compare-versions", a, "gt", b]).returncode == 0)


def channel_of(d, version):
    """The channel a version came from, by its suffix (meshtasticd's: ~beta, ~alpha, ~unstable)."""
    for ch, suffix in (d.get("suffixes") or {}).items():
        if version and version.endswith(suffix):
            return ch
    return None


def image_channels(d):
    """The channels an image's own tool has listed (mpwrd-menu: one network:Meshtastic:<channel>.list each)."""
    img = d.get("image")
    if not img:
        return []
    return [ch for ch in d["channels"] if (IMAGE_ROOT / img["list"].format(channel=ch)).exists()]


def has_image_tool(d):
    img = d.get("image")
    return bool(img) and ((IMAGE_ROOT / img["tool"]).exists() or bool(image_channels(d)))


def settings(pid=None):
    raw = _json(SETTINGS, {})
    if pid is None:
        return raw
    d = _def(pid)
    s = raw.get(pid) if isinstance(raw.get(pid), dict) else {}
    listed = image_channels(d)
    ch = (s.get("channel") if s.get("channel") in d["channels"] else listed[0] if len(listed) == 1 else
          channel_of(d, installed(d["package"])) or d.get("default_channel") or next(iter(d["channels"])))
    return {"channel": ch, "mode": s.get("mode") if s.get("mode") in MODES else "watch",
            "days": s.get("days") if s.get("days") in DAYS else 7}


def _dir(pid):
    return ROOT / pid


def _apt(pid):
    d = _dir(pid)
    (d / "prefs.d").mkdir(parents=True, exist_ok=True)
    return ["-o", f"Dir::Etc::sourcelist={d / 'sources.list'}", "-o", "Dir::Etc::sourceparts=-",
            "-o", f"Dir::State::Lists={d / 'lists'}", "-o", "APT::Get::List-Cleanup=0",
            "-o", f"Dir::Etc::preferences={d / 'prefs.d' / 'none'}", "-o", f"Dir::Etc::preferencesparts={d / 'prefs.d'}"]


def _index(pid, pkg):
    """The channel's newest build of pkg for this box: {version, filename, sha256, size}, or None."""
    a, best = arch(), None
    for f in (_dir(pid) / "lists").glob("*_Packages"):
        for stanza in f.read_text(errors="replace").split("\n\n"):
            fields = dict(l.split(": ", 1) for l in stanza.splitlines() if ": " in l and not l.startswith(" "))
            if fields.get("Package") == pkg and fields.get("Architecture") in (a, "all") and VERSION_RE.match(fields.get("Version", "")):
                if not best or newer(fields["Version"], best["version"]):
                    best = {"version": fields["Version"], "filename": fields.get("Filename", ""), "sha256": fields.get("SHA256", ""),
                            "size": int(fields.get("Size", "0") or 0)}
    return best


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def seen(pid):
    return _json(_dir(pid) / "seen.json", {})


def check(pid, now=None, log=print):
    """The channel read, a new build cached, and installed if the owner's mode says so."""
    now = now or time.time()
    d, s = _def(pid), settings(pid)
    root = _dir(pid)
    # The lists afresh each time: one small index, and never a channel left over from before.
    shutil.rmtree(root / "lists", ignore_errors=True)
    (root / "lists" / "partial").mkdir(parents=True, exist_ok=True)
    (root / "debs").mkdir(parents=True, exist_ok=True)
    key = CODE / d["key"]
    url = d["channels"][s["channel"]].replace("{suite}", suite())
    _write(root / "sources.list", f"deb [signed-by={key}] {url} /\n")
    r = run(["apt-get", *_apt(pid), "update", "-q"])
    if r.returncode:
        raise ValueError("apt-get update: " + ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1][:200])
    top = _index(pid, d["package"])
    got = seen(pid)
    said = []
    if top and top["version"] not in got:
        r = run(["apt-get", *_apt(pid), "download", "-q", f"{d['package']}={top['version']}"], cwd=str(root / "debs"))
        # Named as the index names it (apt may date the file by the server's clock, so not the newest by time).
        name = top["filename"].rsplit("/", 1)[-1]
        f = root / "debs" / name if re.fullmatch(r"[A-Za-z0-9+.~_%-]+\.deb", name) else None
        f = f if f and f.is_file() else None
        if r.returncode or not f or (top["sha256"] and sha256(f) != top["sha256"]):
            raise ValueError(f"{top['version']} did not download whole: " + ((r.stderr or "").strip().splitlines() or ["?"])[-1][:200])
        got[top["version"]] = {"first_seen": now, "file": f.name, "sha256": sha256(f), "channel": s["channel"], "size": f.stat().st_size}
        said.append(f"{top['version']} new on {s['channel']}")
        log(f"{pid}: {said[-1]}")
    _prune(pid, got, installed(d["package"]))
    _write(root / "seen.json", got)
    target = due(pid, now)
    if target and s["mode"] in ("auto", "aged"):
        said.append(install(pid, target, log=log))
    publish(pid, now)
    return "; ".join(said) or f"{d['package']}: nothing new on {s['channel']}"


def due(pid, now=None):
    """The build the owner's mode would install now, if newer than what is installed: the newest seen
    on the channel (auto), or the newest that has been out the chosen days (aged)."""
    now = now or time.time()
    d, s = _def(pid), settings(pid)
    have = installed(d["package"])
    cands = [(v, e) for v, e in seen(pid).items() if e.get("channel") == s["channel"]]
    if s["mode"] == "aged":
        cands = [(v, e) for v, e in cands if now - e["first_seen"] >= s["days"] * 86400]
    best = None
    for v, _ in cands:
        if newer(v, best):
            best = v
    return best if best and newer(best, have) else None


def _prune(pid, got, have):
    keep = set(sorted(got, key=lambda v: got[v]["first_seen"])[-KEEP:]) | {have, _record(pid).get("previous")}
    for v in [v for v in got if v not in keep]:
        (_dir(pid) / "debs" / got[v]["file"]).unlink(missing_ok=True)
        got.pop(v)


def _record(pid):
    return _json(_dir(pid) / "record.json", {})


def install(pid, version, log=print):
    """A cached build installed (its dependencies from the box's own sources, as apt finds them)."""
    d = _def(pid)
    if not VERSION_RE.match(str(version)):
        raise ValueError("not a version")
    e = seen(pid).get(version)
    f = _dir(pid) / "debs" / (e or {}).get("file", "-")
    if not e or not f.is_file() or sha256(f) != e["sha256"]:
        raise ValueError(f"{version} is not in the cache (Check first)")
    before = installed(d["package"])
    r = run(["apt-get", "install", "-y", "-q", "--allow-downgrades", "--no-install-recommends", str(f)], timeout=1800)
    if r.returncode:
        raise ValueError("apt-get install: " + ((r.stderr or r.stdout).strip().splitlines() or ["?"])[-1][:200])
    rec = _record(pid)
    if before and before != version:
        rec["previous"] = before
    rec.setdefault("history", []).append({"at": time.time(), "version": version, "from": before})
    rec["history"] = rec["history"][-20:]
    _write(_dir(pid) / "record.json", rec)
    if d.get("unit"):
        run(["systemctl", "try-restart", d["unit"]], timeout=120)
    publish(pid)
    msg = f"{d['package']} {version} installed" + (f" (was {before})" if before else "")
    log(f"{pid}: {msg}")
    return msg


def rollback(pid, log=print):
    prev = _record(pid).get("previous")
    if not prev:
        raise ValueError("no previous build to go back to")
    if prev not in seen(pid):
        raise ValueError(f"{prev}, the previous build, is not in the cache")
    return install(pid, prev, log=log)


def pref_path(d):
    return PREFS / f"irate-box-{d['id']}.pref"


def set_settings(pid, channel, mode, days):
    """The owner's choice. auto and aged hold the package away from the box's own apt (preferences);
    watch lets it be."""
    d = _def(pid)
    if channel not in d["channels"] or mode not in MODES or days not in DAYS:
        raise ValueError(f"channel one of {', '.join(d['channels'])}; mode one of {', '.join(MODES)}; days one of {', '.join(map(str, DAYS))}")
    raw = settings()
    raw[pid] = {"channel": channel, "mode": mode, "days": days}
    _write(SETTINGS, raw, 0o644)
    host = re.match(r"https://([^/]+)/", next(iter(d["channels"].values()))).group(1)
    switched = _switch_image_channel(d, channel) if has_image_tool(d) else ""
    if mode != "aged":
        pref_path(d).unlink(missing_ok=True)
    else:
        _write(pref_path(d), f"# Written by irate-box (root/pkgwatch.py): {d['package']} is updated by the box's watcher\n"
                             f"# ({d['title']}, /admin), on the channel the owner chose, not by apt upgrade.\n"
                             f"Package: {d['package']}\nPin: origin \"{host}\"\nPin-Priority: -1\n")
    publish(pid)
    words = {"watch": "watched: newer builds said, nothing installed by itself",
             "auto": "each new build installed as it's seen",
             "aged": f"the newest build installed once it has been out {days} day{'s' if days != 1 else ''}"}[mode]
    return f"{d['package']} on {channel}: {words}" + (f"; {switched}" if switched else "")


def dearmor(armored):
    """An armoured key as the binary keyring apt's trusted.gpg.d holds (gpg --dearmor); None without gpg."""
    try:
        p = subprocess.run(["gpg", "--dearmor"], input=armored, capture_output=True, timeout=60)
    except OSError:
        return None
    return p.stdout if p.returncode == 0 and p.stdout else None


def _switch_image_channel(d, channel):
    """The channel set the way the image's own tool sets it (mpwrd-menu's switch_repo_channel): every
    channel's list and key taken away, the chosen one's written, the key from the box's own copy."""
    img = d["image"]
    if image_channels(d) == [channel] and (IMAGE_ROOT / img["key"].format(channel=channel)).exists():
        return ""
    binary = dearmor((CODE / d["key"]).read_bytes())
    if binary is None:
        raise ValueError("gpg is needed to write the channel's key as the image's tool does")
    for ch in d["channels"]:
        (IMAGE_ROOT / img["list"].format(channel=ch)).unlink(missing_ok=True)
        (IMAGE_ROOT / img["key"].format(channel=ch)).unlink(missing_ok=True)
    url = d["channels"][channel].replace("{suite}", suite())
    _write(IMAGE_ROOT / img["list"].format(channel=channel), f"deb {url} /\n")
    key = IMAGE_ROOT / img["key"].format(channel=channel)
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(binary)
    os.chmod(key, 0o644)
    return f"{img['name']} and the box's apt now on {channel} too"


def publish(pid, now=None):
    """What the hub may show: settings, what is installed, what the channel has, the cache, history."""
    d, s = _def(pid), settings(pid)
    got = seen(pid)
    pub = _json(PUBLIC, {})
    rec = _record(pid)
    pub[pid] = {"title": d["title"], "package": d["package"], "channels": list(d["channels"]), "settings": s,
                "installed": installed(d["package"]), "previous": rec.get("previous"),
                "builds": [{"version": v, "channel": e["channel"], "first_seen": e["first_seen"], "size": e.get("size")}
                           for v, e in sorted(got.items(), key=lambda x: x[1]["first_seen"])],
                "due": due(pid, now), "checked": now or pub.get(pid, {}).get("checked"), "history": rec.get("history", [])[-5:],
                "held": pref_path(d).exists(), "modes": list(MODES), "days": list(DAYS),
                "labels": d.get("labels", {}), "image": {"name": d["image"]["name"], "channels": image_channels(d)} if has_image_tool(d) else None}
    _write(PUBLIC, pub)
    return pub[pid]


def watched():
    """The watched packages that are on this box."""
    return [pid for pid, d in definitions().items() if installed(d["package"])]
