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
  kits.list, lists/             the one apt source the installs use (file:, trusted: the
                                signatures were checked when the .debs were fetched)
  installed.json                each installed kit: what it added, when, when it goes

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
import time
from pathlib import Path

ROOT = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits"))
DEFS = Path(os.environ.get("HUB_KITS_DEFS", Path(__file__).resolve().parents[2] / "toolkits"))
DPKG_STATUS = Path(os.environ.get("HUB_DPKG_STATUS", "/var/lib/dpkg/status"))
POLICY_RC = Path(os.environ.get("HUB_POLICY_RC", "/usr/sbin/policy-rc.d"))
POOL = ROOT / "pool"
MANIFESTS = ROOT / "manifests"
INSTALLED = ROOT / "installed.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,62}$")
DEB_RE = re.compile(r"^[A-Za-z0-9+.~_%-]{1,200}\.deb$")
UNIT_RE = re.compile(r"^/(?:usr/)?lib/systemd/system/([A-Za-z0-9@_.:-]+\.(?:service|socket|timer|path))$")
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "APT_LISTCHANGES_FRONTEND": "none", "LC_ALL": "C"}
DEAD_PROXY = "http://127.0.0.1:9"  # nothing listens there: an install that tried the network fails loudly


def run(cmd, timeout=1800, check=True):
    """Every command goes through here (the tests stand it in)."""
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=dict(os.environ, **APT_ENV))
    if check and out.returncode != 0:
        lines = (out.stderr or out.stdout).strip().splitlines()
        raise ValueError(f"{Path(cmd[0]).name} {cmd[1] if len(cmd) > 1 else ''}: {lines[-1][:300] if lines else 'failed'}")
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
    """{id: kit}, from the shipped toolkit files; anything malformed is left out."""
    out = {}
    for f in sorted(DEFS.glob("*.json")):
        k = _read(f, None)
        if (isinstance(k, dict) and ID_RE.match(str(k.get("id", ""))) and k["id"] == f.stem
                and isinstance(k.get("packages"), list) and k["packages"] and all(isinstance(p, str) and PKG_RE.match(p) for p in k["packages"])):
            out[k["id"]] = k
    return out


def _kit(kit_id):
    kits = definitions()
    if kit_id not in kits:
        raise ValueError(f"no toolkit {kit_id!r}: one of {', '.join(kits) or 'none'}")
    return kits[kit_id]


def installed_state():
    return _read(INSTALLED, {})


def _installed_packages():
    out = run(["dpkg-query", "-W", "-f", "${Package}\t${db:Status-Abbrev}\n"]).stdout
    return {name.split(":")[0] for name, _, st in (l.partition("\t") for l in out.splitlines()) if st.startswith("ii")}


def _kit_added(state, but=None):
    return {p for k, v in state.items() if k != but for p in v.get("added", [])}


def _base_status(dest, leave_out):
    """dpkg's status file without the packages kits added: what apt resolves a fetch against."""
    gone = {f"Package: {p}" for p in leave_out}
    keep = [s.strip("\n") for s in DPKG_STATUS.read_text(errors="replace").split("\n\n")
            if s.strip() and s.strip("\n").split("\n", 1)[0] not in gone]
    dest.write_text("\n\n".join(keep) + "\n")


def _deb_fields(path):
    out = run(["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"]).stdout
    f = dict(l.split(": ", 1) for l in out.splitlines() if ": " in l)
    return f.get("Package", ""), f.get("Version", ""), f.get("Architecture", "")


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


def pool_bytes():
    return sum(f.stat().st_size for f in POOL.glob("*.deb")) if POOL.is_dir() else 0


def fetch(kit_id, budget_mb=500, update_lists=True, log=print):
    """The kit's packages and their dependencies into the pool (online). Returns a line."""
    kit = _kit(kit_id)
    for d in (ROOT, POOL, MANIFESTS):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT, 0o755)
    if update_lists:
        log("apt-get update")
        run(["apt-get", "update", "-q"], timeout=900)
    stage = ROOT / f"stage-{kit_id}"
    shutil.rmtree(stage, ignore_errors=True)
    (stage / "partial").mkdir(parents=True)
    try:
        leave_out = _kit_added(installed_state())
        _base_status(stage / "status", leave_out)
        base = {l[len("Package: "):] for l in (stage / "status").read_text().splitlines() if l.startswith("Package: ")}
        log(f"{kit_id}: downloading {len(kit['packages'])} packages and what they need")
        run(["apt-get", "install", "--download-only", "-y", "-q", "--no-install-recommends",
             "-o", f"Dir::Cache::archives={stage}", "-o", f"Dir::State::status={stage / 'status'}",
             "-o", "Debug::NoLocking=1", *kit["packages"]], timeout=3600)
        pkgs = []
        for deb in sorted(stage.glob("*.deb")):
            if not DEB_RE.match(deb.name):
                continue
            name, version, arch = _deb_fields(deb)
            pkgs.append({"name": name, "version": version, "arch": arch, "file": deb.name,
                         "size": deb.stat().st_size, "sha256": sha256(deb)})
        on_box = sorted(p for p in kit["packages"] if p in base)
        missing = sorted(set(kit["packages"]) - {p["name"] for p in pkgs} - set(on_box))
        if missing:
            raise ValueError(f"apt fetched no {', '.join(missing)}")
        # The budget: the pool as it would be, with this kit's new set current and its current one
        # previous (if they differ), every other kit's sets as they are.
        cur = manifest(kit_id)
        same = bool(cur) and sorted((p["file"], p["sha256"]) for p in cur["packages"]) == sorted((p["file"], p["sha256"]) for p in pkgs)
        sets = [(_read(m, {}) or {}).get("packages", []) for m in MANIFESTS.glob("*.json")
                if m.name not in (f"{kit_id}.json", f"{kit_id}.previous.json")]
        sets += [pkgs, (cur or {}).get("packages", []) if not same else (manifest(kit_id, "previous") or {}).get("packages", [])]
        total = sum(p["size"] for p in {p["file"]: p for s_ in sets for p in s_}.values())
        if total > budget_mb << 20:
            raise ValueError(f"the toolkits' cache would be {total >> 20} MB with {kit_id}, over its {budget_mb} MB budget")
        for p in pkgs:
            dest = POOL / p["file"]
            if not (dest.exists() and sha256(dest) == p["sha256"]):
                os.replace(stage / p["file"], dest)
            os.chmod(dest, 0o644)
        new = {"id": kit_id, "fetched": time.time(), "packages": pkgs, "on_box": on_box,
               "bytes": sum(p["size"] for p in pkgs)}
        if cur and not same:
            _write(MANIFESTS / f"{kit_id}.previous.json", cur)
        _write(MANIFESTS / f"{kit_id}.json", new)
        prune()
        write_index()
        changed = "unchanged" if same else f"{len(pkgs)} packages, {new['bytes'] >> 20} MB"
        return f"{kit['title']}: {changed}" + (f"; already on the box: {', '.join(on_box)}" if on_box else "")
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def prune():
    """Pool files no manifest names any more."""
    keep = set(_referenced())
    for deb in POOL.glob("*.deb"):
        if deb.name not in keep:
            deb.unlink()


def write_index():
    """pool/Packages, as apt-ftparchive would: each .deb's control fields, its file, size and
    sha256 (from the manifest, checked when it was fetched)."""
    stanzas = []
    for f, p in sorted(_referenced().items()):
        path = POOL / f
        if not path.is_file():
            continue
        control = run(["dpkg-deb", "-f", str(path)]).stdout.rstrip("\n")
        stanzas.append(f"{control}\nFilename: ./{f}\nSize: {p['size']}\nSHA256: {p['sha256']}\n")
    (POOL / "Packages").write_text("\n".join(stanzas))
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
    before = _installed_packages()
    # Services stay stopped: Debian starts a package's service on install unless this says no.
    if POLICY_RC.exists():
        raise ValueError(f"{POLICY_RC} exists already: another install is under way, or the box has its own")
    POLICY_RC.write_text("#!/bin/sh\n# irate-box: a toolkit is being installed; its services stay stopped.\nexit 101\n")
    os.chmod(POLICY_RC, 0o755)
    try:
        run(["apt-get", *_apt_offline(), "update", "-q"], timeout=300)
        log(f"{kit_id}: installing from the local repository")
        run(["apt-get", *_apt_offline(), "install", "-y", "-q", "--no-install-recommends", *kit["packages"]], timeout=3600)
    finally:
        POLICY_RC.unlink(missing_ok=True)
    added = sorted(_installed_packages() - before)
    # A service the kit is for (the debug kit's core dumps: systemd-coredump.socket) runs; every
    # other one its packages brought stays stopped and disabled.
    wanted = {u for u in kit.get("services", []) if isinstance(u, str) and UNIT_RE.match(f"/lib/systemd/system/{u}")}
    units = _disable_units(added, keep=wanted)
    for u in sorted(wanted):
        run(["systemctl", "enable", "--now", u], check=False, timeout=120)
    prev = state.get(kit_id, {})
    state[kit_id] = {"added": sorted(set(prev.get("added", [])) | set(added)), "at": prev.get("at") or time.time(),
                     "remove_at": None if hours is None else time.time() + hours * 3600, "units": units}
    _write(INSTALLED, state)
    when = "kept until removed" if hours is None else f"removed after {hours} h"
    return f"{kit['title']}: installed {len(added)} packages from the local repository; {when}" + \
        (f"; services left stopped: {', '.join(units)}" if units else "")


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


def remove(kit_id, log=print):
    """Exactly what the kit's install added, but what another installed kit added too."""
    state = installed_state()
    if kit_id not in state:
        raise ValueError(f"{kit_id} is not installed")
    shared = _kit_added(state, but=kit_id)
    present = _installed_packages()
    go = sorted(p for p in state[kit_id].get("added", []) if p not in shared and p in present)
    if go:
        log(f"{kit_id}: removing {len(go)} packages")
        run(["apt-get", *_apt_offline(), "purge", "-y", "-q", *go], timeout=1800)
    del state[kit_id]
    _write(INSTALLED, state)
    return f"{kit_id}: removed {len(go)} packages" + (f"; kept {len(shared & set(present))} another kit uses" if shared else "")


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
                                        "on_box": cur.get("on_box", []), "versions": {p["name"]: p["version"] for p in cur["packages"]}},
                     "previous": prev and {"fetched": prev["fetched"], "bytes": prev["bytes"]}}
    return {"at": time.time(), "kits": kits, "installed": installed_state(), "pool_bytes": pool_bytes(),
            "problems": verify()}
