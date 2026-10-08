# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Toolkits, root's half (toolkits-plan §1–3, §8): Debian packages kept in a local repository so
they can be installed and removed with no internet, and taken out again after a while.

A toolkit is toolkits/<id>.json in the hub's code (root-owned): a title, a summary, the package
names, a consent text and notes. Only those shipped kits are fetched or installed: a request
names a kit, never packages, so the hub cannot have root install anything else.

Under ROOT (/var/cache/irate-box/kits, root's):
  pool/*.deb, pool/Packages     every kit's packages, one pool, and the index apt reads
  manifests/<id>.json           the kit's current set: each package, version, file, size, sha256
  manifests/<id>.previous.json  the set before, for rolling back offline
  kits-status                   every cached package, in dpkg's status format: for debsecan --status
  kits.list, lists/             the one apt source the installs use (file:, trusted: the
                                signatures were checked when the .debs were fetched)
  installed.json                each installed kit: what it added, when, when it goes
  owner/                        the owner's own kits and extra tools (kitdefs.py), root's

A kit may name "services" it is for (the debug kit's systemd-coredump.socket): those are left
running; every other service its packages bring stays stopped and disabled.

Fetching (online): apt-get --download-only, resolved against this box's packages minus what kits
added, so a kit that is installed now is still cached whole. Installing: apt-get with only the
pool as a source and a proxy that goes nowhere, so it cannot reach the internet; services the
packages bring stay stopped and disabled (policy-rc.d for the transaction). Removing: exactly
what the install added, unless another installed kit added it too.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from irate_box.hub import kitdefs

ROOT = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits"))
DPKG_STATUS = Path(os.environ.get("HUB_DPKG_STATUS", "/var/lib/dpkg/status"))
POLICY_RC = Path(os.environ.get("HUB_POLICY_RC", "/usr/sbin/policy-rc.d"))
POOL = ROOT / "pool"
# The build kit's wheelhouse (toolkits-plan §5): PlatformIO and what it needs, as wheels, so a
# build's `pip install` needs no internet (ci.py sets PIP_NO_INDEX and PIP_FIND_LINKS to this).
WHEELHOUSE = ROOT / "wheelhouse"
MANIFESTS = ROOT / "manifests"
INSTALLED = ROOT / "installed.json"
ID_RE = kitdefs.ID_RE
PKG_RE = kitdefs.PKG_RE
PIP_RE = kitdefs.PIP_RE
DEB_RE = re.compile(r"^[A-Za-z0-9+.~_%-]{1,200}\.deb$")
WHEEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+.~_%-]{1,200}\.(?:whl|tar\.gz|zip)$")
UNIT_RE = re.compile(r"^/(?:usr/)?lib/systemd/system/([A-Za-z0-9@_.:-]+\.(?:service|socket|timer|path))$")
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "APT_LISTCHANGES_FRONTEND": "none", "LC_ALL": "C"}
DEAD_PROXY = "http://127.0.0.1:9"  # nothing listens there: an install that tried the network fails loudly


def run(cmd, timeout=1800, check=True, env=None):
    """Every command goes through here (the tests stand it in)."""
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=dict(os.environ, **APT_ENV, **(env or {})))
    if check and out.returncode != 0:
        lines = (out.stderr or out.stdout).strip().splitlines()
        # apt's reason is in its E: and "Depends:" lines; its last line is often only the solver's
        # "[no choices]" (the symbols kit on the Lyra, 2026-10-07).
        why = [l.strip() for l in lines if l.startswith("E: ") or "Depends:" in l][:3] or lines[-1:]
        verb = next((a for a in cmd[1:] if not a.startswith("-") and "=" not in a and "::" not in a), "")
        raise ValueError(f"{Path(cmd[0]).name} {verb}: {'; '.join(why)[:400] if why else 'failed'}")
    return out


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


def definitions():
    return kitdefs.definitions()


def arch():
    return run(["dpkg", "--print-architecture"]).stdout.strip()


def _packages(kit, fetching=False):
    """(the kit's packages for this box, those left out; fetching, its alternatives too). A kit from Debian's debug archive (the
    symbols kit) asks only for the symbols of what is installed here: a -dbgsym package depends on
    its program at exactly the same version, so asking for mosquitto's on a box without it would
    bring mosquitto too."""
    pkgs, left = kitdefs.packages_for(kit, arch(), fetching)
    if kit.get("debug_archive"):
        here = _installed_packages()
        absent = [p for p in pkgs if p.endswith("-dbgsym") and p[:-len("-dbgsym")] not in here]
        pkgs, left = [p for p in pkgs if p not in absent], sorted(left + absent)
    return pkgs, left


# Debian's debug archives (toolkits-plan §6): the -dbgsym packages, signed with the same keys as the
# rest of Debian. A kit that asks for it is fetched from it alone, its index kept apart from the
# box's own lists (so the box's apt never sees it), and that index vouches for it on a USB stick.
DEBUG_LISTS = ROOT / "lists-debug"
DEBIAN_KEYRING = Path(os.environ.get("HUB_DEBIAN_KEYRING", "/usr/share/keyrings/debian-archive-keyring.gpg"))


def _codename():
    try:
        for line in Path(os.environ.get("HUB_OS_RELEASE", "/etc/os-release")).read_text().splitlines():
            if line.startswith("VERSION_CODENAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    raise ValueError("this box's Debian release is not known (VERSION_CODENAME in /etc/os-release)")


def _debug_apt():
    """apt options for the debug archive alone, with its own lists."""
    DEBUG_LISTS.mkdir(parents=True, exist_ok=True)
    (DEBUG_LISTS / "partial").mkdir(exist_ok=True)
    src = ROOT / "debug.list"
    # Where Debian publishes symbols: the release's, the proposed updates', and the security
    # updates' (their own archive). A box with security updates (nginx +deb13u9 on the Lyra) finds
    # its versions' symbols only in the last.
    c, signed = _codename(), f"[signed-by={DEBIAN_KEYRING}]"
    src.write_text(f"deb {signed} http://deb.debian.org/debian-debug {c}-debug main\n"
                   f"deb {signed} http://deb.debian.org/debian-debug {c}-proposed-updates-debug main\n"
                   f"deb {signed} http://deb.debian.org/debian-security-debug {c}-security-debug main\n")
    return ["-o", f"Dir::Etc::sourcelist={src}", "-o", "Dir::Etc::sourceparts=-", "-o", f"Dir::State::Lists={DEBUG_LISTS}",
            "-o", "APT::Get::List-Cleanup=0"]


def _kit(kit_id):
    kits = definitions()
    if kit_id not in kits:
        raise ValueError(f"no toolkit {kit_id!r}: one of {', '.join(kits) or 'none'}")
    return kits[kit_id]


def installed_state():
    return _read(INSTALLED, {})


def _installed_versions():
    out = run(["dpkg-query", "-W", "-f", "${Package}\t${db:Status-Abbrev}\t${Version}\n"]).stdout
    rows = (l.split("\t") + ["", ""] for l in out.splitlines())
    return {r[0].split(":")[0]: r[2] for r in rows if r[1].startswith("ii")}


def _installed_packages():
    return set(_installed_versions())


def _kit_added(state, but=None):
    return {p for k, v in state.items() if k != but for p in v.get("added", [])}


def _base_status(dest, leave_out):
    """dpkg's status file without the packages kits added: what apt resolves a fetch against."""
    gone = {f"Package: {p}" for p in leave_out}
    keep = [s.strip("\n") for s in DPKG_STATUS.read_text(errors="replace").split("\n\n")
            if s.strip() and s.strip("\n").split("\n", 1)[0] not in gone]
    dest.write_text("\n\n".join(keep) + "\n")


def _deb_fields(path):
    out = run(["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture", "Source"]).stdout
    f = dict(l.split(": ", 1) for l in out.splitlines() if ": " in l)
    return f.get("Package", ""), f.get("Version", ""), f.get("Architecture", ""), f.get("Source", "")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(kit_id, which="current"):
    return _read(MANIFESTS / (f"{kit_id}.json" if which == "current" else f"{kit_id}.previous.json"), None)


def _referenced():
    files = {}
    for m in MANIFESTS.glob("*.json") if MANIFESTS.is_dir() else []:
        for p in (_read(m, {}) or {}).get("packages", []):
            files[p["file"]] = p
    return files


def _referenced_wheels():
    files = {}
    for m in MANIFESTS.glob("*.json") if MANIFESTS.is_dir() else []:
        for w in (_read(m, {}) or {}).get("wheels", []):
            files[w["file"]] = w
    return files


def pool_bytes():
    return sum(f.stat().st_size for f in POOL.glob("*.deb")) if POOL.is_dir() else 0


def wheelhouse_bytes():
    return sum(f.stat().st_size for f in WHEELHOUSE.glob("*")) if WHEELHOUSE.is_dir() else 0


def _fetch_wheels(kit_id, pip_pkgs, stage, log):
    """pip download, into stage/wheelhouse: platformio and everything it needs, as wheels. Needs
    no package installed on the box (python3-venv's `ensurepip` is disabled on Debian without
    it): python3-pip's own .deb is fetched (not installed) and unpacked for a one-off pip."""
    wstage = stage / "wheelhouse"
    wstage.mkdir()
    pipstage = stage / "pip-tool"
    pipstage.mkdir()
    log(f"{kit_id}: fetching a copy of pip to use while online (not installed on the box)")
    run(["apt-get", "install", "--download-only", "--reinstall", "-y", "-q", "--no-install-recommends",
         "-o", f"Dir::Cache::archives={pipstage}", "python3-pip"], timeout=300)
    deb = next(iter(sorted(pipstage.glob("python3-pip_*.deb"))), None)
    if not deb:
        raise ValueError(f"{kit_id}: could not fetch python3-pip, needed once to build the wheelhouse")
    extract = pipstage / "root"
    run(["dpkg-deb", "-x", str(deb), str(extract)], timeout=120)
    site = next(iter(sorted(extract.glob("usr/lib/python3*/dist-packages"))), None)
    if not site:
        raise ValueError(f"{kit_id}: python3-pip's .deb did not hold a usable pip")
    log(f"{kit_id}: downloading {len(pip_pkgs)} Python package(s) and what they need")
    run(["python3", "-m", "pip", "download", "--dest", str(wstage), "--no-input", *pip_pkgs],
        timeout=1800, env={"PYTHONPATH": str(site)})
    wheels = []
    for w in sorted(wstage.iterdir()):
        if WHEEL_RE.match(w.name) and w.is_file():
            wheels.append({"file": w.name, "size": w.stat().st_size, "sha256": sha256(w)})
    return wheels


def fetch(kit_id, budget_mb=500, update_lists=True, log=print):
    """The kit's packages and their dependencies into the pool (online), and, for a kit that
    names Python packages (the build kit: platformio), its wheelhouse too."""
    kit = _kit(kit_id)
    for d in (ROOT, POOL, MANIFESTS):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT, 0o755)
    debug = _debug_apt() if kit.get("debug_archive") else []
    if update_lists or debug:
        log("apt-get update" + (" (Debian's debug archive)" if debug else ""))
        run(["apt-get", *debug, "update", "-q"], timeout=900)
    stage = ROOT / f"stage-{kit_id}"
    shutil.rmtree(stage, ignore_errors=True)
    (stage / "partial").mkdir(parents=True)
    try:
        leave_out = _kit_added(installed_state())
        _base_status(stage / "status", leave_out)
        base = {l[len("Package: "):] for l in (stage / "status").read_text().splitlines() if l.startswith("Package: ")}
        packages, left_out = _packages(kit, fetching=True)
        log(f"{kit_id}: downloading {len(packages)} packages and what they need")
        run(["apt-get", *debug, "install", "--download-only", "-y", "-q", "--no-install-recommends",
             "-o", f"Dir::Cache::archives={stage}", "-o", f"Dir::State::status={stage / 'status'}",
             "-o", "Debug::NoLocking=1", *packages], timeout=3600)
        pkgs = []
        for deb in sorted(stage.glob("*.deb")):
            if not DEB_RE.match(deb.name):
                continue
            name, version, deb_arch, source = _deb_fields(deb)
            pkgs.append({"name": name, "version": version, "arch": deb_arch, "source": source, "file": deb.name,
                         "size": deb.stat().st_size, "sha256": sha256(deb)})
        on_box = sorted(p for p in packages if p in base)
        missing = sorted(set(packages) - {p["name"] for p in pkgs} - set(on_box))
        if missing:
            raise ValueError(f"apt fetched no {', '.join(missing)}")
        wheels = _fetch_wheels(kit_id, kit["pip"], stage, log) if kit.get("pip") else []
        # The budget: the pool and wheelhouse as they would be, with this kit's new sets current
        # and its current ones previous (if they differ), every other kit's sets as they are.
        cur = manifest(kit_id)
        same = bool(cur) and sorted((p["file"], p["sha256"]) for p in cur["packages"]) == sorted((p["file"], p["sha256"]) for p in pkgs) \
            and sorted((w["file"], w["sha256"]) for w in cur.get("wheels", [])) == sorted((w["file"], w["sha256"]) for w in wheels)
        prev = manifest(kit_id, "previous")
        other_debs = [(_read(m, {}) or {}).get("packages", []) for m in MANIFESTS.glob("*.json")
                      if m.name not in (f"{kit_id}.json", f"{kit_id}.previous.json")]
        other_wheels = [(_read(m, {}) or {}).get("wheels", []) for m in MANIFESTS.glob("*.json")
                        if m.name not in (f"{kit_id}.json", f"{kit_id}.previous.json")]
        deb_sets = other_debs + [pkgs, (cur or {}).get("packages", []) if not same else (prev or {}).get("packages", [])]
        wheel_sets = other_wheels + [wheels, (cur or {}).get("wheels", []) if not same else (prev or {}).get("wheels", [])]
        total = sum(p["size"] for p in {p["file"]: p for s_ in deb_sets for p in s_}.values()) \
            + sum(w["size"] for w in {w["file"]: w for s_ in wheel_sets for w in s_}.values())
        if total > budget_mb << 20:
            raise ValueError(f"the toolkits' cache would be {total >> 20} MB with {kit_id}, over its {budget_mb} MB budget")
        for p in pkgs:
            dest = POOL / p["file"]
            if not (dest.exists() and sha256(dest) == p["sha256"]):
                os.replace(stage / p["file"], dest)
            os.chmod(dest, 0o644)
        if wheels:
            WHEELHOUSE.mkdir(parents=True, exist_ok=True)
            for w in wheels:
                dest = WHEELHOUSE / w["file"]
                if not (dest.exists() and sha256(dest) == w["sha256"]):
                    os.replace(stage / "wheelhouse" / w["file"], dest)
                os.chmod(dest, 0o644)
        new = {"id": kit_id, "fetched": time.time(), "packages": pkgs, "wheels": wheels, "on_box": on_box, "left_out": left_out, "arch": arch(),
               "bytes": sum(p["size"] for p in pkgs) + sum(w["size"] for w in wheels)}
        if cur and not same:
            _write(MANIFESTS / f"{kit_id}.previous.json", cur)
        _write(MANIFESTS / f"{kit_id}.json", new)
        prune()
        write_index()
        changed = "unchanged" if same else f"{len(pkgs)} packages, {new['bytes'] >> 20} MB"
        return f"{kit['title']}: {changed}" + (f"; already on the box: {', '.join(on_box)}" if on_box else "") + \
            (f"; left out on this {new['arch']} board: {kitdefs.why_left_out(kit, left_out)}" if left_out else "")
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def prune():
    """Pool and wheelhouse files no manifest names any more."""
    keep = set(_referenced())
    for deb in POOL.glob("*.deb"):
        if deb.name not in keep:
            deb.unlink()
    keep_wheels = set(_referenced_wheels())
    for w in WHEELHOUSE.glob("*") if WHEELHOUSE.is_dir() else []:
        if w.name not in keep_wheels:
            w.unlink()


def write_index():
    """pool/Packages, as apt-ftparchive would: each .deb's control fields, its file, size and
    sha256 (from the manifest, checked when it was fetched)."""
    stanzas = []
    for f, p in sorted(_referenced().items()):
        path = POOL / f
        if not path.is_file():
            continue
        out = run(["dpkg-deb", "-f", str(path)], check=False)
        if out.returncode != 0 or sha256(path) != p["sha256"]:
            continue  # damaged since it was fetched: not offered to apt (verify() reports it)
        control = out.stdout.rstrip("\n")
        stanzas.append(f"{control}\nFilename: ./{f}\nSize: {p['size']}\nSHA256: {p['sha256']}\n")
    (POOL / "Packages").write_text("\n".join(stanzas))
    # Every cached package as dpkg's status file would list it, so debsecan --status can say
    # which of them have known vulnerabilities before any is installed (security-doctor-plan §2).
    # With Source: as dpkg writes it, since debsecan matches vulnerabilities by source package
    # (libpython3.13's are python3.13's).
    rows = sorted({(p["name"], p["version"], p["arch"], p.get("source", "")) for p in _referenced().values()})
    (ROOT / "kits-status").write_text("".join(f"Package: {n}\nStatus: install ok installed\n" + (f"Source: {src}\n" if src else "")
                                              + f"Version: {v}\nArchitecture: {a}\n\n" for n, v, a, src in rows))
    (ROOT / "kits.list").write_text(f"deb [trusted=yes] file:{POOL} ./\n")
    (ROOT / "lists").mkdir(exist_ok=True)


def _apt_offline():
    return ["-o", f"Dir::Etc::sourcelist={ROOT / 'kits.list'}", "-o", "Dir::Etc::sourceparts=-",
            "-o", f"Dir::State::Lists={ROOT / 'lists'}", "-o", "APT::Get::List-Cleanup=0",
            "-o", f"Acquire::http::Proxy={DEAD_PROXY}", "-o", f"Acquire::https::Proxy={DEAD_PROXY}"]


def verify(kit_id=None):
    """Problems with the cache: a file missing or not what its manifest says, or the folder not
    root's alone. [] when all is well."""
    problems = []
    if ROOT.exists():
        st = ROOT.stat()
        if st.st_uid != 0 and os.geteuid() == 0 or st.st_mode & 0o022:
            problems.append(f"{ROOT} is not root's alone")
    for m in sorted(MANIFESTS.glob("*.json")) if MANIFESTS.is_dir() else []:
        man = _read(m, {}) or {}
        if kit_id and man.get("id") != kit_id:
            continue
        for p in man.get("packages", []):
            path = POOL / p["file"]
            if not path.is_file():
                problems.append(f"{p['file']} is missing")
            elif path.stat().st_size != p["size"] or sha256(path) != p["sha256"]:
                problems.append(f"{p['file']} has changed since it was fetched")
        for w in man.get("wheels", []):
            path = WHEELHOUSE / w["file"]
            if not path.is_file():
                problems.append(f"{w['file']} is missing")
            elif path.stat().st_size != w["size"] or sha256(path) != w["sha256"]:
                problems.append(f"{w['file']} has changed since it was fetched")
    return sorted(set(problems))


def install(kit_id, hours=24, log=print):
    """The kit from the pool, with no internet. hours: when it is removed again (None: never)."""
    kit = _kit(kit_id)
    if hours is not None and (type(hours) is not int or not 1 <= hours <= 24 * 365):
        raise ValueError("hours: 1 hour to a year, or never")
    man = manifest(kit_id)
    if not man:
        raise ValueError(f"{kit['title']} is not cached yet: fetch it while the box has internet")
    bad = verify(kit_id)
    if bad:
        raise ValueError("the cache fails its check, so nothing was installed: " + "; ".join(bad[:5]))
    state = installed_state()
    before = _installed_versions()
    # Services stay stopped: Debian starts a package's service on install unless this says no.
    if POLICY_RC.exists():
        raise ValueError(f"{POLICY_RC} exists already: another install is under way, or the box has its own")
    POLICY_RC.write_text("#!/bin/sh\n# irate-box: a toolkit is being installed; its services stay stopped.\nexit 101\n")
    os.chmod(POLICY_RC, 0o755)
    try:
        run(["apt-get", *_apt_offline(), "update", "-q"], timeout=300)
        log(f"{kit_id}: installing from the local repository")
        run(["apt-get", *_apt_offline(), "install", "-y", "-q", "--no-install-recommends", *_packages(kit)[0]], timeout=3600)
    finally:
        POLICY_RC.unlink(missing_ok=True)
    after = _installed_versions()
    added = sorted(set(after) - set(before))
    # A package the box had, brought up to the cached version because a new one needs exactly
    # that (libc6 for libc6-dev: Debian's security updates, on the Lyra on 2026-10-06). Said, and
    # recorded: removing the kit does not take it back down.
    upgraded = sorted(f"{p} {before[p]} → {after[p]}" for p in before if p in after and after[p] != before[p])
    # A service the kit is for (the debug kit's core dumps: systemd-coredump.socket) runs; every
    # other one its packages brought stays stopped and disabled.
    wanted = {u for u in kit.get("services", []) if isinstance(u, str) and UNIT_RE.match(f"/lib/systemd/system/{u}")}
    units = _disable_units(added, keep=wanted)
    for u in sorted(wanted):
        run(["systemctl", "enable", "--now", u], check=False, timeout=120)
    prev = state.get(kit_id, {})
    state[kit_id] = {"added": sorted(set(prev.get("added", [])) | set(added)), "at": prev.get("at") or time.time(),
                     "remove_at": None if hours is None else time.time() + hours * 3600, "units": units,
                     "upgraded": prev.get("upgraded", []) + upgraded}
    _write(INSTALLED, state)
    when = "kept until removed" if hours is None else f"removed after {hours} h"
    return f"{kit['title']}: installed {len(added)} packages from the local repository; {when}" + \
        (f"; services left stopped: {', '.join(units)}" if units else "") + \
        (f"; also brought up to date, as the new packages need (removing the kit leaves them): {', '.join(upgraded)}" if upgraded else "")


def _disable_units(packages, keep=()):
    units = []
    for p in packages:
        out = run(["dpkg-query", "-L", p], check=False).stdout
        units += [m.group(1) for m in map(UNIT_RE.match, out.splitlines()) if m and "@" not in m.group(1)]
    units = sorted(set(units) - set(keep))
    for u in units:
        run(["systemctl", "disable", "--now", u], check=False, timeout=120)
    return units


def set_removal(kit_id, hours):
    state = installed_state()
    if kit_id not in state:
        raise ValueError(f"{kit_id} is not installed")
    if hours is not None and (type(hours) is not int or not 1 <= hours <= 24 * 365):
        raise ValueError("hours: 1 hour to a year, or never")
    state[kit_id]["remove_at"] = None if hours is None else time.time() + hours * 3600
    _write(INSTALLED, state)
    return f"{kit_id}: " + ("kept until removed" if hours is None else f"removed in {hours} h")


def _purgeable(go, keep):
    """Of the packages in go, those apt can purge while every package in keep stays, and without
    taking anything else with them (a purge also removes what depends on what it purges). Asked of
    apt itself (-s, a simulation), all at once first, then one more at a time until none can join."""
    def takes(cands):
        r = run(["apt-get", *_apt_offline(), "-s", "purge", *cands, *[f"{k}+" for k in sorted(keep)]], check=False)
        return None if r.returncode else {l.split()[1] for l in (r.stdout or "").splitlines() if l.startswith(("Purg ", "Remv "))}
    gone = takes(go)
    if gone is not None and gone <= set(go):
        return list(go)
    ok, rest, more = [], list(go), True
    while more:
        more = False
        for p in list(rest):
            gone = takes(ok + [p])
            if gone is not None and gone <= set(ok + [p]):
                ok.append(p); rest.remove(p); more = True
    return ok


def remove(kit_id, log=print):
    """Exactly what the kit's install added, but what another installed kit added or lists (kits
    overlap: Recovery and System both have smartmontools), and nothing another kit's packages need."""
    state = installed_state()
    if kit_id not in state:
        raise ValueError(f"{kit_id} is not installed")
    present = _installed_packages()
    defs = definitions()
    listed_by = {k: set(_packages(defs[k])[0]) for k in state if k != kit_id and k in defs}
    listed = set().union(*listed_by.values()) if listed_by else set()
    shared = (_kit_added(state, but=kit_id) | listed) & set(present)
    go = sorted(p for p in state[kit_id].get("added", []) if p not in shared and p in present)
    if go:
        can = _purgeable(go, shared)
        held = sorted(set(go) - set(can))
        if held:
            log(f"{kit_id}: keeping {', '.join(held)}: another kit's packages need them")
        shared |= set(held)
        go = can
    if go:
        log(f"{kit_id}: removing {len(go)} packages")
        run(["apt-get", *_apt_offline(), "purge", "-y", "-q", *go], timeout=1800)
    kept = shared & set(state[kit_id].get("added", []))
    # What this kit added and another still uses passes to the kits that list it (with what those
    # need), so it goes when the last of them does rather than staying for good; failing those, to
    # every kit still installed.
    takers = [k for k, names in listed_by.items() if names & kept] or [k for k in state if k != kit_id]
    for k in takers if kept else ():
        state[k]["added"] = sorted(set(state[k].get("added", [])) | kept)
    del state[kit_id]
    _write(INSTALLED, state)
    return f"{kit_id}: removed {len(go)} packages" + (f"; kept {len(kept)} another kit uses" if kept else "")


# --- the owner's own kits, and extra tools in a shipped kit (step 38) -----------------------

MAX_OWN = 40


def _checked_packages(pkgs, most):
    """Package names the page typed: Debian's rules, no repeats, and each one this box's package
    lists know (apt-cache), so a typo is refused now rather than at the next fetch."""
    if not isinstance(pkgs, list) or len(pkgs) > most or not all(isinstance(p, str) and PKG_RE.match(p) for p in pkgs):
        raise ValueError(f"packages: up to {most} Debian package names (lowercase letters, digits, + - .)")
    pkgs = list(dict.fromkeys(pkgs))
    unknown = [p for p in pkgs if not run(["apt-cache", "show", "--no-all-versions", p], check=False, timeout=120).stdout.strip()]
    if unknown:
        raise ValueError(f"not in this box's package lists: {', '.join(unknown)} (check the name, or refresh the lists while online)")
    return pkgs


def define(spec):
    """A kit of the owner's own, new or changed. spec: {id, title, summary, packages, remove_after_hours}."""
    if not isinstance(spec, dict):
        raise ValueError("kit: an object")
    kid = str(spec.get("id", ""))
    if not ID_RE.match(kid) or kid == "extras":
        raise ValueError("id: lowercase letters, digits and -, up to 32")
    if kid in kitdefs.shipped():
        raise ValueError(f"{kid} is one of the box's own kits: add tools to it instead")
    title = str(spec.get("title", "")).strip()
    summary = str(spec.get("summary", "")).strip()
    if not 1 <= len(title) <= 60 or len(summary) > 200 or any(c in title + summary for c in "\n\r<>"):
        raise ValueError("title: 1 to 60 characters; summary up to 200; one line each")
    hours = spec.get("remove_after_hours", 24)
    if hours is not None and (type(hours) is not int or not 1 <= hours <= 24 * 365):
        raise ValueError("remove_after_hours: 1 to 8760, or null for never")
    pkgs = _checked_packages(spec.get("packages"), MAX_OWN)
    if not pkgs:
        raise ValueError("packages: at least one")
    kitdefs.OWNER.mkdir(parents=True, exist_ok=True)
    os.chmod(kitdefs.OWNER, 0o755)
    new = not (kitdefs.OWNER / f"{kid}.json").exists()
    _write(kitdefs.OWNER / f"{kid}.json", {"id": kid, "title": title, "summary": summary or f"{len(pkgs)} packages of the owner's choosing.",
                                           "packages": pkgs, "remove_after_hours": hours,
                                           "consent": "Packages you chose: " + ", ".join(pkgs) + ". Some may run services (left stopped) or "
                                                      "need root to use. They are removed again after the time you choose.",
                                           "notes": ["One of your own: added on this box's Library → Toolkits."]})
    os.chmod(kitdefs.OWNER / f"{kid}.json", 0o644)
    return f"{title}: {'added' if new else 'changed'}, {len(pkgs)} packages; Refresh it while the box has internet to cache it"


def undefine(kit_id, log=print):
    """One of the owner's own kits, gone: its definition, its cache (its manifests; the pool
    keeps what another kit still names)."""
    if kit_id not in definitions() or not definitions()[kit_id].get("owner"):
        raise ValueError(f"{kit_id} is not one of your own kits")
    if kit_id in installed_state():
        raise ValueError(f"{kit_id} is installed: remove it first")
    (kitdefs.OWNER / f"{kit_id}.json").unlink(missing_ok=True)
    for m in (f"{kit_id}.json", f"{kit_id}.previous.json"):
        (MANIFESTS / m).unlink(missing_ok=True)
    if POOL.is_dir():
        prune()
        write_index()
    return f"{kit_id}: deleted, and its cache"


def set_extra(kit_id, packages):
    """Extra tools tracked in a shipped kit ([] clears them)."""
    if kit_id not in kitdefs.shipped():
        raise ValueError(f"{kit_id} is not one of the box's own kits")
    base = set(kitdefs.shipped()[kit_id]["packages"])
    pkgs = [p for p in _checked_packages(packages, 20) if p not in base]
    kitdefs.OWNER.mkdir(parents=True, exist_ok=True)
    os.chmod(kitdefs.OWNER, 0o755)
    extras = _read(kitdefs.OWNER / "extras.json", {}) or {}
    if pkgs:
        extras[kit_id] = pkgs
    else:
        extras.pop(kit_id, None)
    _write(kitdefs.OWNER / "extras.json", extras)
    os.chmod(kitdefs.OWNER / "extras.json", 0o644)
    return f"{kit_id}: " + (f"also tracks {', '.join(pkgs)}; the next refresh fetches them" if pkgs else "no extra tools")


def rollback(kit_id, log=print):
    """The previous set becomes current (and the current one previous), offline; an installed kit
    is put back to those versions."""
    kit = _kit(kit_id)
    cur, prev = manifest(kit_id), manifest(kit_id, "previous")
    if not prev:
        raise ValueError(f"{kit['title']} has no previous version to go back to")
    bad = verify(kit_id)
    if bad:
        raise ValueError("the cache fails its check, so nothing changed: " + "; ".join(bad[:5]))
    _write(MANIFESTS / f"{kit_id}.json", prev)
    _write(MANIFESTS / f"{kit_id}.previous.json", cur)
    write_index()
    if kit_id in installed_state():
        present = _installed_versions()
        pins = [f"{p['name']}={p['version']}" for p in prev["packages"] if p["name"] in present and present[p["name"]] != p["version"]]
        if pins:
            log(f"{kit_id}: back to the previous versions of {len(pins)} packages")
            POLICY_RC.write_text("#!/bin/sh\nexit 101\n")
            os.chmod(POLICY_RC, 0o755)
            try:
                run(["apt-get", *_apt_offline(), "update", "-q"], timeout=300)
                run(["apt-get", *_apt_offline(), "install", "-y", "-q", "--no-install-recommends", "--allow-downgrades", *pins], timeout=3600)
            finally:
                POLICY_RC.unlink(missing_ok=True)
        return f"{kit['title']}: back to the set fetched {time.strftime('%Y-%m-%d', time.gmtime(prev['fetched']))}" + \
            (f"; {len(pins)} installed packages put back" if pins else "")
    return f"{kit['title']}: the cache is back to the set fetched {time.strftime('%Y-%m-%d', time.gmtime(prev['fetched']))}"


# --- by USB stick (step 32, toolkits-plan §4.3) ---------------------------------------------------
# A stick's .debs are trusted only as far as Debian's own signatures vouch for them, as apt does
# online: the export carries the signed indexes (InRelease, and each Packages file that lists the
# kit's packages) from /var/lib/apt/lists; the import checks each InRelease's signature with this
# box's keys (gpgv), each Packages file against its InRelease, and each .deb against its Packages
# file, before anything enters the pool. Layout on the stick:
#   irate-box/kits/<arch>/<kit>/manifest.json, debs/*.deb, lists/*_InRelease, lists/*_Packages

APT_LISTS = Path(os.environ.get("HUB_APT_LISTS", "/var/lib/apt/lists"))
KEYRING_DIRS = [Path(p) for p in os.environ.get("HUB_APT_KEYRINGS", "/usr/share/keyrings:/etc/apt/trusted.gpg.d:/etc/apt/keyrings").split(":")]
LIST_RE = re.compile(r"^(?P<release>.+_dists_[^_]+)_(?P<path>.+_Packages)$")


def _packages_hashes(path):
    """{deb file name: sha256} from an apt Packages file."""
    out, name, sha = {}, None, None
    with open(path, errors="replace") as fh:
        for line in fh:
            if line.startswith("Filename: "):
                name = line[10:].strip().rsplit("/", 1)[-1]
            elif line.startswith("SHA256: "):
                sha = line[8:].strip()
            elif not line.strip():
                if name and sha:
                    out[name] = sha
                name = sha = None
    if name and sha:
        out[name] = sha
    return out


def export_usb(kit_id, dest, report=None):
    """The kit's current set onto a stick folder (`dest`, its irate-box/kits/), with the signed
    indexes that vouch for it. Returns where it went."""
    kit = _kit(kit_id)
    man = manifest(kit_id)
    if not man:
        raise ValueError(f"{kit['title']} is not cached: refresh it while the box has internet first")
    if verify(kit_id):
        raise ValueError("the cache fails its check, so it was not copied: refresh it first")
    want = {p["file"]: p["sha256"] for p in man["packages"]}
    lists, vouched = [], set()
    for pk in sorted(p for d in (APT_LISTS, DEBUG_LISTS) for p in d.glob("*_Packages")):
        m = LIST_RE.match(pk.name)
        if not m or not (pk.parent / f"{m.group('release')}_InRelease").exists():
            continue
        # By hash alone: apt saves a .deb with its version's epoch in the name (valgrind_1%3a3.24…),
        # the index lists it without; a SHA-256 is the whole of the check either way.
        hashes = set(_packages_hashes(pk).values())
        hit = {f for f, h in want.items() if h in hashes}
        if hit:
            lists += [pk, pk.parent / f"{m.group('release')}_InRelease"]
            vouched |= hit
    unvouched = sorted(set(want) - vouched)
    if unvouched:
        raise ValueError("these are in no signed index on this box, so another box could not check them: "
                         + ", ".join(unvouched[:5]) + " (refresh the package lists, then the kit, while online)")
    folder = Path(dest) / man.get("arch", arch()) / kit_id
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "debs").mkdir(parents=True)
    (folder / "lists").mkdir()
    files = [(POOL / f, folder / "debs" / f) for f in sorted(want)] + [(l, folder / "lists" / l.name) for l in sorted(set(lists))]
    total = sum(src.stat().st_size for src, _ in files)
    if shutil.disk_usage(folder).free < total:
        shutil.rmtree(folder, ignore_errors=True)
        raise ValueError(f"not enough room on the stick: {total >> 20} MB needed")
    done = 0
    for src, dst in files:
        shutil.copyfile(src, dst, follow_symlinks=False)
        done += src.stat().st_size
        if report:
            report(done, total)
    _write(folder / "manifest.json", dict(man, kit=kit_id, title=kit["title"], exported=time.time()))
    return f"{kit['title']}: {len(want)} packages and {len(set(lists)) // 2} signed indexes, {total >> 20} MB"


def stick_kits(root):
    """Kits on a stick's irate-box/kits/, for this box's architecture or not."""
    out = []
    base = Path(root) / "irate-box" / "kits"
    for m in sorted(base.glob("*/*/manifest.json")) if base.is_dir() else []:
        if m.is_symlink():
            continue
        man = _read(m, None)
        if isinstance(man, dict) and ID_RE.match(m.parent.name) and isinstance(man.get("packages"), list):
            out.append({"kit": m.parent.name, "arch": m.parent.parent.name, "title": str(man.get("title", m.parent.name))[:60],
                        "packages": len(man["packages"]), "bytes": int(man.get("bytes") or 0), "fetched": man.get("fetched")})
    return out


def _keyrings():
    rings = []
    for d in KEYRING_DIRS:
        for k in sorted(d.glob("*")) if d.is_dir() else []:
            if k.suffix in (".gpg", ".asc", ".pgp") and "removed" not in k.name and k.is_file():
                rings += ["--keyring", str(k)]
    return rings


def _verified_release(inrelease, work):
    """The signed content of an InRelease file, checked with this box's keys; ValueError if not."""
    out = work / f"{inrelease.name}.verified"
    r = run(["gpgv", *_keyrings(), "--output", str(out), str(inrelease)], check=False, timeout=120)
    if r.returncode != 0 or not out.exists():
        raise ValueError(f"{inrelease.name}: its signature does not check with this box's keys")
    return out.read_text(errors="replace")


def _release_date(text, field):
    """A Release file's Date or Valid-Until, as a timestamp, or None."""
    import email.utils
    for line in text.splitlines():
        if line.startswith(field + ":"):
            try:
                return email.utils.parsedate_to_datetime(line.split(":", 1)[1].strip()).timestamp()
            except (TypeError, ValueError):
                return None
    return None


def _not_stale(text, name):
    """A signed release from a stick must be within its Valid-Until and no older than the same
    release this box already holds: a stick could otherwise replay an old, signed, since-fixed set
    (stance review 2026-10-08, N12). ValueError if not."""
    until = _release_date(text, "Valid-Until")
    if until is not None and until < time.time():
        raise ValueError(f"{name}: the stick's signed release expired on {time.strftime('%Y-%m-%d', time.gmtime(until))}: "
                         "export the kit again from a box that has been online since")
    date = _release_date(text, "Date")
    mine = APT_LISTS / name
    if date is not None and mine.is_file() and not mine.is_symlink():
        own = _release_date(mine.read_text(errors="replace"), "Date")
        if own is not None and date < own:
            raise ValueError(f"{name}: the stick's release ({time.strftime('%Y-%m-%d', time.gmtime(date))}) is older than the one this box "
                             f"already has ({time.strftime('%Y-%m-%d', time.gmtime(own))}): not imported")


def _sha256_section(release_text):
    """{path: sha256} from a Release file's SHA256 section."""
    out, inside = {}, False
    for line in release_text.splitlines():
        if line.startswith("SHA256:"):
            inside = True
            continue
        if inside:
            if not line.startswith(" "):
                break
            parts = line.split()
            if len(parts) == 3:
                out[parts[2]] = parts[0]
    return out


def import_usb(src_root, kit_id, budget_mb=500, report=None):
    """A kit from a stick (`src_root` its irate-box/kits/) into the pool, every .deb checked against
    Debian's signatures first. The kit's current set becomes previous, as after a fetch."""
    kit = _kit(kit_id)
    here = arch()
    folder = Path(src_root) / here / kit_id
    if not (folder / "manifest.json").is_file():
        raise ValueError(f"no {kit_id} kit for this box's architecture ({here}) on the stick")
    man = _read(folder / "manifest.json", None)
    if not isinstance(man, dict) or not isinstance(man.get("packages"), list):
        raise ValueError("the kit's manifest on the stick is unreadable")
    work = Path(tempfile.mkdtemp(prefix="kit-usb-"))
    try:
        vouch = set()
        for pk in sorted((folder / "lists").glob("*_Packages")):
            m = LIST_RE.match(pk.name)
            rel = folder / "lists" / f"{m.group('release')}_InRelease" if m else None
            if not rel or not rel.is_file() or pk.is_symlink() or rel.is_symlink():
                continue
            text = _verified_release(rel, work)
            _not_stale(text, rel.name)
            listed = _sha256_section(text)
            path = m.group("path").replace("_", "/")
            if listed.get(path) != sha256(pk):
                raise ValueError(f"{pk.name} is not the index its signed release lists")
            vouch |= set(_packages_hashes(pk).values())
        pkgs = []
        for p in man["packages"]:
            name = str(p.get("file", ""))
            deb = folder / "debs" / name
            if not DEB_RE.match(name) or deb.is_symlink() or not deb.is_file():
                raise ValueError(f"{name or 'a package'} is missing from the stick")
            h = sha256(deb)
            if h not in vouch:
                raise ValueError(f"{name}: not vouched for by a signed index on the stick, so nothing was imported")
            pkgs.append({"name": str(p["name"]), "version": str(p["version"]), "arch": str(p.get("arch", "")), "source": str(p.get("source", "")),
                         "file": name, "size": deb.stat().st_size, "sha256": h})
        total = sum(q["size"] for q in pkgs) + pool_bytes()
        if total > budget_mb << 20:
            raise ValueError(f"the toolkits' cache would be {total >> 20} MB, over its {budget_mb} MB budget")
        for d in (ROOT, POOL, MANIFESTS):
            d.mkdir(parents=True, exist_ok=True)
        done = 0
        for q in pkgs:
            dest = POOL / q["file"]
            if not (dest.exists() and sha256(dest) == q["sha256"]):
                tmp = POOL / f".{q['file']}.usb"
                shutil.copyfile(folder / "debs" / q["file"], tmp, follow_symlinks=False)
                if sha256(tmp) != q["sha256"]:
                    tmp.unlink()
                    raise ValueError(f"{q['file']} changed while it was copied: the stick may be failing")
                os.chmod(tmp, 0o644)
                os.replace(tmp, dest)
            done += q["size"]
            if report:
                report(done, total)
        cur = manifest(kit_id)
        if cur and sorted(x["sha256"] for x in cur["packages"]) != sorted(q["sha256"] for q in pkgs):
            _write(MANIFESTS / f"{kit_id}.previous.json", cur)
        _write(MANIFESTS / f"{kit_id}.json", {"id": kit_id, "fetched": man.get("fetched") or time.time(), "packages": pkgs,
                                              "on_box": man.get("on_box", []), "left_out": man.get("left_out", []), "arch": here,
                                              "bytes": sum(q["size"] for q in pkgs), "from_usb": time.time()})
        prune()
        write_index()
        return f"{kit['title']}: {len(pkgs)} packages imported, each checked against Debian's signed indexes"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def expire(now=None):
    """Remove every kit whose time is up. Returns the lines."""
    now = now or time.time()
    return [remove(k) for k, v in installed_state().items() if v.get("remove_at") and v["remove_at"] <= now]


def status():
    """For the hub (control/kits.json): what is cached, installed, and what's wrong."""
    kits = {}
    for kid, k in definitions().items():
        cur, prev = manifest(kid), manifest(kid, "previous")
        kits[kid] = {"cached": cur and {"fetched": cur["fetched"], "packages": len(cur["packages"]), "bytes": cur["bytes"],
                                        "wheels": len(cur.get("wheels", [])),
                                        "on_box": cur.get("on_box", []), "left_out": cur.get("left_out", []), "arch": cur.get("arch"), "versions": {p["name"]: p["version"] for p in cur["packages"]}},
                     "previous": prev and {"fetched": prev["fetched"], "bytes": prev["bytes"]}}
    return {"at": time.time(), "kits": kits, "installed": installed_state(), "pool_bytes": pool_bytes(), "wheelhouse_bytes": wheelhouse_bytes(),
            "problems": verify()}
