# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Toolkits, root's half: Debian packages kept in a local repository so
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

import contextlib
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from irate_box.hub import kitdefs
from irate_box.root import aptram
from irate_box.msg import M, Refused, duration, sentences, when

ROOT = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits"))
DPKG_STATUS = Path(os.environ.get("HUB_DPKG_STATUS", "/var/lib/dpkg/status"))
POLICY_RC = Path(os.environ.get("HUB_POLICY_RC", "/usr/sbin/policy-rc.d"))
POOL = ROOT / "pool"
# The build kit's wheelhouse: PlatformIO and what it needs, as wheels, so a
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
    """Every command goes through here (the tests stand it in). apt's cache goes in RAM (aptram)."""
    with aptram.argv(cmd) as full:
        out = subprocess.run(  # noqa: TID251
            full, capture_output=True, text=True, timeout=timeout, env=dict(os.environ, **APT_ENV, **(env or {}))
        )
    if check and out.returncode != 0:
        lines = (out.stderr or out.stdout).strip().splitlines()
        # apt's reason is in its E: and "Depends:" lines; its last line is often only the solver's
        # "[no choices]".
        why = [ln.strip() for ln in lines if ln.startswith("E: ") or "Depends:" in ln][:3] or lines[-1:]
        verb = next((a for a in cmd[1:] if not a.startswith("-") and "=" not in a and "::" not in a), "")
        tool = f"{Path(cmd[0]).name} {verb}".strip()
        if not why:
            raise Refused(M("kits.command.failed", "{tool} failed.", tool=tool))
        raise Refused(M("kits.command.failed-said", "{tool} failed: {detail}", tool=tool, detail="; ".join(why)[:400]))
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


def _write_if_changed(path, text):
    """text into path unless it holds that already: a refresh that changed nothing writes nothing to the card."""
    try:
        if path.read_text() == text:
            return
    except OSError:
        pass
    path.write_text(text)


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
    if kit.get("kernel_headers"):
        headers = _kernel_headers()
        if headers:
            pkgs = pkgs + [headers]
        else:
            left = left + [f"linux-headers-{os.uname().release}"]
    if kit.get("debug_archive"):
        here = _installed_packages()
        absent = [p for p in pkgs if p.endswith("-dbgsym") and p[: -len("-dbgsym")] not in here]
        pkgs, left = [p for p in pkgs if p not in absent], sorted(left + absent)
    return pkgs, left


def _kernel_headers():
    """The package with the running kernel's headers (DKMS builds a driver against them): the one that already owns
    /lib/modules/<release>/build (a vendor's, as the Lyra's linux-headers-vendor-rockchip), else
    linux-headers-<release> (Debian's and Raspberry Pi OS's name) when the package lists know it; None otherwise."""
    release = os.uname().release
    found = run(["dpkg", "-S", f"/lib/modules/{release}/build"], check=False, timeout=60)
    owner = found.stdout.strip() if found.returncode == 0 else ""
    if owner and ":" in owner:
        name = owner.split(":", 1)[0].strip()
        if PKG_RE.match(name):
            return name
    name = f"linux-headers-{release}"
    if (
        PKG_RE.match(name)
        and run(["apt-cache", "show", "--no-all-versions", name], check=False, timeout=120).stdout.strip()
    ):
        return name
    return None


# Debian's debug archives: the -dbgsym packages, signed with the same keys as the
# rest of Debian. A kit that asks for it is fetched from it alone, its index kept apart from the
# box's own lists (so the box's apt never sees it), and that index vouches for it on a USB stick.
DEBUG_LISTS = ROOT / "lists-debug"
# When _update_lists last updated the system's lists and the debug archive's; a scheduled refresh updates them
# only when older than LISTS_FRESH_HOURS (each kit refreshes daily, in turn, so one update serves the day).
LISTS_UPDATED = ROOT / "lists-updated.json"
LISTS_FRESH_HOURS = 20
DEBIAN_KEYRING = Path(os.environ.get("HUB_DEBIAN_KEYRING", "/usr/share/keyrings/debian-archive-keyring.gpg"))


def _codename():
    try:
        for line in Path(os.environ.get("HUB_OS_RELEASE", "/etc/os-release")).read_text().splitlines():
            if line.startswith("VERSION_CODENAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    raise Refused(
        M("kits.codename.unknown", "The box does not know its Debian release: /etc/os-release has no VERSION_CODENAME.")
    )


def _debug_apt():
    """apt options for the debug archive alone, with its own lists."""
    DEBUG_LISTS.mkdir(parents=True, exist_ok=True)
    (DEBUG_LISTS / "partial").mkdir(exist_ok=True)
    src = ROOT / "debug.list"
    # Where Debian publishes symbols: the release's, the proposed updates', and the security
    # updates' (their own archive). A box with security updates (nginx +deb13u9, say) finds
    # its versions' symbols only in the last.
    c, signed = _codename(), f"[signed-by={DEBIAN_KEYRING}]"
    src.write_text(
        f"deb {signed} http://deb.debian.org/debian-debug {c}-debug main\n"
        f"deb {signed} http://deb.debian.org/debian-debug {c}-proposed-updates-debug main\n"
        f"deb {signed} http://deb.debian.org/debian-security-debug {c}-security-debug main\n"
    )
    return [
        "-o",
        f"Dir::Etc::sourcelist={src}",
        "-o",
        "Dir::Etc::sourceparts=-",
        "-o",
        f"Dir::State::Lists={DEBUG_LISTS}",
        "-o",
        "APT::Get::List-Cleanup=0",
    ]


def _kit(kit_id):
    kits = definitions()
    if kit_id not in kits:
        if not kits:
            raise Refused(
                M("kits.kit.unknown-none", "The box has no toolkit called {kit}. It has no toolkits.", kit=kit_id)
            )
        raise Refused(
            M(
                "kits.kit.unknown",
                "The box has no toolkit called {kit}. Its toolkits are {kits}.",
                kit=kit_id,
                kits=list(kits),
            )
        )
    return kits[kit_id]


def installed_state():
    return _read(INSTALLED, {})


def _installed_versions():
    out = run(["dpkg-query", "-W", "-f", "${Package}\t${db:Status-Abbrev}\t${Version}\n"]).stdout
    rows = (ln.split("\t") + ["", ""] for ln in out.splitlines())
    return {r[0].split(":")[0]: r[2] for r in rows if r[1].startswith("ii")}


def _installed_packages():
    return set(_installed_versions())


def _kit_added(state, but=None):
    return {p for k, v in state.items() if k != but for p in v.get("added", [])}


def _base_status(dest, leave_out):
    """dpkg's status file without the packages kits added: what apt resolves a fetch against."""
    gone = {f"Package: {p}" for p in leave_out}
    keep = [
        s.strip("\n")
        for s in DPKG_STATUS.read_text(errors="replace").split("\n\n")
        if s.strip() and s.strip("\n").split("\n", 1)[0] not in gone
    ]
    dest.write_text("\n\n".join(keep) + "\n")


def _deb_fields(path):
    out = run(["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture", "Source"]).stdout
    f = dict(ln.split(": ", 1) for ln in out.splitlines() if ": " in ln)
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
    run(
        [
            "apt-get",
            "install",
            "--download-only",
            "--reinstall",
            "-y",
            "-q",
            "--no-install-recommends",
            "-o",
            f"Dir::Cache::archives={pipstage}",
            "python3-pip",
        ],
        timeout=300,
    )
    deb = next(iter(sorted(pipstage.glob("python3-pip_*.deb"))), None)
    if not deb:
        raise Refused(
            M(
                "kits.wheels.no-pip",
                "{kit}: the box could not download python3-pip. It needs it once to build the wheelhouse.",
                kit=kit_id,
            )
        )
    extract = pipstage / "root"
    run(["dpkg-deb", "-x", str(deb), str(extract)], timeout=120)
    site = next(iter(sorted(extract.glob("usr/lib/python3*/dist-packages"))), None)
    if not site:
        raise Refused(M("kits.wheels.bad-pip", "{kit}: the python3-pip package did not hold a usable pip.", kit=kit_id))
    log(f"{kit_id}: downloading {len(pip_pkgs)} Python package(s) and what they need")
    run(
        ["python3", "-m", "pip", "download", "--dest", str(wstage), "--no-input", *pip_pkgs],
        timeout=1800,
        env={"PYTHONPATH": str(site)},
    )
    wheels = []
    for w in sorted(wstage.iterdir()):
        if WHEEL_RE.match(w.name) and w.is_file():
            wheels.append({"file": w.name, "size": w.stat().st_size, "sha256": sha256(w)})
    return wheels


def _update_lists(debug, max_hours, log):
    """apt-get update for the system's lists (or the debug archive's), unless this module updated them less than
    max_hours ago: one update a day serves every kit refreshed that day, where each used to bring its own, and
    every update writes the changed lists to the card."""
    key = "debug" if debug else "system"
    stamps = _read(LISTS_UPDATED, {}) or {}
    if max_hours and time.time() - float(stamps.get(key) or 0) < max_hours * 3600:
        return
    log("apt-get update" + (" (Debian's debug archive)" if debug else ""))
    run(["apt-get", *debug, "update", "-q"], timeout=900)
    stamps[key] = time.time()
    _write(LISTS_UPDATED, stamps)


# apt-get --print-uris: one line per file to download, 'URI' FILE SIZE HASH.
URI_RE = re.compile(r"^'[^']+' (?P<file>\S+) (?P<size>\d+)(?: SHA256:(?P<sha256>[0-9a-f]{64}))?")


def _wanted(said):
    """{file: (size, sha256 or None)} from apt-get --print-uris."""
    return {
        m["file"]: (int(m["size"]), m["sha256"])
        for m in map(URI_RE.match, said.splitlines())
        if m and DEB_RE.match(m["file"])
    }


def _link_cached(wanted, stage):
    """The wanted files the pool holds already (any kit's), linked into the stage, where apt looks for what it has
    downloaded: apt checks each one's size and hash and downloads only the rest. A link writes no data."""
    for f, (size, _) in wanted.items():
        cached = POOL / f
        with contextlib.suppress(OSError):
            if cached.stat().st_size == size:
                os.link(cached, stage / f)


def fetch(kit_id, budget_mb=500, lists_max_hours=0, log=print):
    """The kit's packages and their dependencies into the pool (online), and, for a kit that
    names Python packages (the build kit: platformio), its wheelhouse too. lists_max_hours: the
    lists are updated first unless updated within that many hours (0: always, as when the owner asks).
    Every apt run in it shares one folder in RAM, which also holds the filtered dpkg status."""
    with aptram.shared() as ram:
        return _fetch(kit_id, budget_mb, lists_max_hours, log, ram)


def _fetch(kit_id, budget_mb, lists_max_hours, log, ram):
    kit = _kit(kit_id)
    for d in (ROOT, POOL, MANIFESTS):
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(ROOT, 0o755)  # The kits folder, read by apt. # nosec B103
    debug = _debug_apt() if kit.get("debug_archive") else []
    _update_lists(debug, lists_max_hours, log)
    stage = ROOT / f"stage-{kit_id}"
    shutil.rmtree(stage, ignore_errors=True)
    (stage / "partial").mkdir(parents=True)
    try:
        leave_out = _kit_added(installed_state())
        # dpkg's status, filtered (a few MB), in RAM: written afresh by every refresh, read once.
        status = ram / "status"
        _base_status(status, leave_out)
        base = {ln[len("Package: ") :] for ln in status.read_text().splitlines() if ln.startswith("Package: ")}
        packages, left_out = _packages(kit, fetching=True)
        options = [
            "--no-install-recommends",
            "-o",
            f"Dir::Cache::archives={stage}",
            "-o",
            f"Dir::State::status={status}",
            "-o",
            "Debug::NoLocking=1",
        ]
        # What the kit needs, resolved without downloading; what the pool has of it linked into the stage; only
        # the rest downloaded. An unchanged kit downloads, and writes to the card, nothing.
        wanted = _wanted(
            run(["apt-get", *debug, "install", "--print-uris", "-q", *options, *packages], timeout=600).stdout
        )
        _link_cached(wanted, stage)
        new_files = [f for f in wanted if not (stage / f).exists()]
        if new_files:
            log(f"{kit_id}: downloading {len(new_files)} of its {len(wanted)} packages")
            run(["apt-get", *debug, "install", "--download-only", "-y", "-q", *options, *packages], timeout=3600)
        pkgs = []
        # Only the files apt named: the stage may also hold what it does not need now.
        for deb in sorted(stage / f for f in wanted):
            if not deb.is_file():
                continue
            name, version, deb_arch, source = _deb_fields(deb)
            pkgs.append(
                {
                    "name": name,
                    "version": version,
                    "arch": deb_arch,
                    "source": source,
                    "file": deb.name,
                    "size": deb.stat().st_size,
                    "sha256": sha256(deb),
                }
            )
        on_box = sorted(p for p in packages if p in base)
        missing = sorted(set(packages) - {p["name"] for p in pkgs} - set(on_box))
        if missing:
            raise Refused(M("kits.fetch.missing", "apt did not download these packages: {packages}.", packages=missing))
        wheels = _fetch_wheels(kit_id, kit["pip"], stage, log) if kit.get("pip") else []
        # The budget: the pool and wheelhouse as they would be, with this kit's new sets current
        # and its current ones previous (if they differ), every other kit's sets as they are.
        cur = manifest(kit_id)
        same = (
            bool(cur)
            and sorted((p["file"], p["sha256"]) for p in cur["packages"])
            == sorted((p["file"], p["sha256"]) for p in pkgs)
            and sorted((w["file"], w["sha256"]) for w in cur.get("wheels", []))
            == sorted((w["file"], w["sha256"]) for w in wheels)
        )
        prev = manifest(kit_id, "previous")
        others = {
            m.name: _read(m, {}) or {}
            for m in MANIFESTS.glob("*.json")
            if m.name not in (f"{kit_id}.json", f"{kit_id}.previous.json")
        }
        mine = [
            (pkgs, wheels),
            ((cur or {}).get("packages", []), (cur or {}).get("wheels", []))
            if not same
            else ((prev or {}).get("packages", []), (prev or {}).get("wheels", [])),
        ]

        def total(without=()):
            sets = [(m.get("packages", []), m.get("wheels", [])) for n, m in others.items() if n not in without] + mine
            return sum(p["size"] for p in {p["file"]: p for d, _ in sets for p in d}.values()) + sum(
                w["size"] for w in {w["file"]: w for _, ws in sets for w in ws}.values()
            )

        # Over the budget: other toolkits' older sets (kept only for Roll back) go first, oldest first, as far as
        # needed; refused only if the cache would still not fit, and then nothing is dropped.
        drop = []
        if total() > budget_mb << 20:
            for n, _m in sorted(
                ((n, m) for n, m in others.items() if n.endswith(".previous.json")),
                key=lambda x: x[1].get("fetched", 0),
            ):
                drop.append(n)
                if total(drop) <= budget_mb << 20:
                    break
            if total(drop) > budget_mb << 20:
                raise Refused(
                    M(
                        "kits.fetch.over-limit",
                        "With {kit}, the toolkit cache would be {mb} MB, even without the older sets kept for Roll back. "
                        "This is more than its storage limit of {limit} MB. Delete a toolkit cache or increase the storage limit.",
                        kit=kit_id,
                        mb=total(drop) >> 20,
                        limit=budget_mb,
                    )
                )
        for n in drop:
            (MANIFESTS / n).unlink(missing_ok=True)
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
        lists, _ = _vouching({p["file"]: p["sha256"] for p in pkgs})
        new = {
            "id": kit_id,
            "fetched": time.time(),
            "packages": pkgs,
            "wheels": wheels,
            "on_box": on_box,
            "left_out": left_out,
            "arch": arch(),
            "bytes": sum(p["size"] for p in pkgs) + sum(w["size"] for w in wheels),
            "index_bytes": sum(ln.stat().st_size for ln in set(lists)),
        }
        if cur and not same:
            _write(MANIFESTS / f"{kit_id}.previous.json", cur)
        _write(MANIFESTS / f"{kit_id}.json", new)
        prune()
        write_index()
        dropped = [n[: -len(".previous.json")] for n in drop]
        return sentences(
            M("kits.fetch.unchanged", "{title}: the cache is unchanged.", title=kit["title"])
            if same
            else M(
                "kits.fetch.done",
                {
                    "one": "{title}: the cache holds {n} package, {mb} MB.",
                    "other": "{title}: the cache holds {n} packages, {mb} MB.",
                },
                title=kit["title"],
                n=len(pkgs),
                mb=new["bytes"] >> 20,
            ),
            on_box and M("kits.fetch.on-box", "Already on the box: {packages}.", packages=on_box),
            dropped
            and M(
                "kits.fetch.dropped",
                "To keep within the storage limit, the box removed the older sets of {kits}. "
                "Roll back is not available for them now.",
                kits=dropped,
            ),
            # why: kitdefs' English phrase ("gdb (64-bit only); pciutils (no PCI)").
            left_out
            and M(
                "kits.fetch.left-out",
                "Left out on this {arch} board: {why}.",
                arch=new["arch"],
                why=kitdefs.why_left_out(kit, left_out),
            ),
        )
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
    _write_if_changed(POOL / "Packages", "\n".join(stanzas))
    # Every cached package as dpkg's status file would list it, so debsecan --status can say
    # which of them have known vulnerabilities before any is installed.
    # With Source: as dpkg writes it, since debsecan matches vulnerabilities by source package
    # (libpython3.13's are python3.13's).
    rows = sorted({(p["name"], p["version"], p["arch"], p.get("source", "")) for p in _referenced().values()})
    _write_if_changed(
        ROOT / "kits-status",
        "".join(
            f"Package: {n}\nStatus: install ok installed\n"
            + (f"Source: {src}\n" if src else "")
            + f"Version: {v}\nArchitecture: {a}\n\n"
            for n, v, a, src in rows
        ),
    )
    _write_if_changed(ROOT / "kits.list", f"deb [trusted=yes] file:{POOL} ./\n")
    (ROOT / "lists").mkdir(exist_ok=True)


def _apt_offline():
    return [
        "-o",
        f"Dir::Etc::sourcelist={ROOT / 'kits.list'}",
        "-o",
        "Dir::Etc::sourceparts=-",
        "-o",
        f"Dir::State::Lists={ROOT / 'lists'}",
        "-o",
        "APT::Get::List-Cleanup=0",
        "-o",
        f"Acquire::http::Proxy={DEAD_PROXY}",
        "-o",
        f"Acquire::https::Proxy={DEAD_PROXY}",
    ]


def verify(kit_id=None):
    """Problems with the cache: a file missing or not what its manifest says, or the folder not
    root's alone. [] when all is well. English lines: the doctors read the file name off the front of each."""
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
        raise Refused(M("kits.hours.invalid", "Hours: enter 1 hour to 1 year, or never."))
    man = manifest(kit_id)
    if not man:
        raise Refused(
            M(
                "kits.install.not-cached",
                "{title} is not in the cache. Download it while the box has internet.",
                title=kit["title"],
            )
        )
    bad = verify(kit_id)
    if bad:
        raise Refused(
            M(
                "kits.install.cache-bad",
                "Cache verification failed. The box installed no package. The problems: {problems}.",
                problems=bad[:5],
            )
        )
    state = installed_state()
    before = _installed_versions()
    # Services stay stopped: Debian starts a package's service on install unless this says no.
    if POLICY_RC.exists():
        raise Refused(
            M(
                "kits.install.policy-rc",
                "{path} exists already. Another install is running, or the box has its own file.",
                path=str(POLICY_RC),
            )
        )
    POLICY_RC.write_text("#!/bin/sh\n# irate-box: a toolkit is being installed; its services stay stopped.\nexit 101\n")
    os.chmod(POLICY_RC, 0o755)  # policy-rc.d is a script dpkg runs. # nosec B103
    try:
        run(["apt-get", *_apt_offline(), "update", "-q"], timeout=300)
        log(f"{kit_id}: installing from the local repository")
        run(
            ["apt-get", *_apt_offline(), "install", "-y", "-q", "--no-install-recommends", *_packages(kit)[0]],
            timeout=3600,
        )
    finally:
        POLICY_RC.unlink(missing_ok=True)
    after = _installed_versions()
    added = sorted(set(after) - set(before))
    # A package the box had, brought up to the cached version because a new one needs exactly
    # that (libc6 for libc6-dev, after Debian's security updates). Refused, and
    # recorded: removing the kit does not take it back down.
    upgraded = sorted(f"{p} {before[p]} → {after[p]}" for p in before if p in after and after[p] != before[p])
    # A service the kit is for (the debug kit's core dumps: systemd-coredump.socket) runs; every
    # other one its packages brought stays stopped and disabled.
    wanted = {u for u in kit.get("services", []) if isinstance(u, str) and UNIT_RE.match(f"/lib/systemd/system/{u}")}
    units = _disable_units(added, keep=wanted)
    for u in sorted(wanted):
        run(["systemctl", "enable", "--now", u], check=False, timeout=120)
    prev = state.get(kit_id, {})
    state[kit_id] = {
        "added": sorted(set(prev.get("added", [])) | set(added)),
        "at": prev.get("at") or time.time(),
        "remove_at": None if hours is None else time.time() + hours * 3600,
        "units": units,
        "upgraded": prev.get("upgraded", []) + upgraded,
    }
    _write(INSTALLED, state)
    return sentences(
        M(
            "kits.install.done",
            {
                "one": "Install from cache: {title}. The box installed {n} package.",
                "other": "Install from cache: {title}. The box installed {n} packages.",
            },
            title=kit["title"],
            n=len(added),
        ),
        M("kits.install.keep", "The box keeps the toolkit until you remove it.")
        if hours is None
        else M("kits.install.remove-after", "The box removes the toolkit after {wait}.", wait=duration(hours * 3600)),
        units and M("kits.install.stopped", "Services left stopped: {units}.", units=units),
        upgraded
        and M(
            "kits.install.upgraded",
            "The new packages needed these packages to be brought up to date: {packages}. "
            "Removing the toolkit does not undo this.",
            packages=upgraded,
        ),
    )


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
        raise Refused(M("kits.kit.not-installed", "{kit} is not installed.", kit=kit_id))
    if hours is not None and (type(hours) is not int or not 1 <= hours <= 24 * 365):
        raise Refused(M("kits.hours.invalid", "Hours: enter 1 hour to 1 year, or never."))
    state[kit_id]["remove_at"] = None if hours is None else time.time() + hours * 3600
    _write(INSTALLED, state)
    if hours is None:
        return M("kits.removal.keep", "Keep installed: the box keeps {kit} until you remove it.", kit=kit_id)
    return M(
        "kits.removal.scheduled",
        "Removal scheduled: the box removes {kit} in {wait}.",
        kit=kit_id,
        wait=duration(hours * 3600),
    )


def _purgeable(go, keep):
    """Of the packages in go, those apt can purge while every package in keep stays, and without
    taking anything else with them (a purge also removes what depends on what it purges). Asked of
    apt itself (-s, a simulation), all at once first, then one more at a time until none can join."""

    def takes(cands):
        r = run(["apt-get", *_apt_offline(), "-s", "purge", *cands, *[f"{k}+" for k in sorted(keep)]], check=False)
        return (
            None
            if r.returncode
            else {ln.split()[1] for ln in (r.stdout or "").splitlines() if ln.startswith(("Purg ", "Remv "))}
        )

    gone = takes(go)
    if gone is not None and gone <= set(go):
        return list(go)
    ok, rest, more = [], list(go), True
    while more:
        more = False
        for p in list(rest):
            gone = takes(ok + [p])
            if gone is not None and gone <= set(ok + [p]):
                ok.append(p)
                rest.remove(p)
                more = True
    return ok


def remove(kit_id, log=print):
    """Exactly what the kit's install added, but what another installed kit added or lists (kits
    overlap: Recovery and System both have smartmontools), and nothing another kit's packages need."""
    state = installed_state()
    if kit_id not in state:
        raise Refused(M("kits.kit.not-installed", "{kit} is not installed.", kit=kit_id))
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
            log(
                f"{kit_id}: keeping {', '.join(held)}. Other installed packages need them (another toolkit's, or a driver DKMS built)"
            )
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
    return sentences(
        M(
            "kits.remove.done",
            {"one": "{kit}: the box removed {n} package.", "other": "{kit}: the box removed {n} packages."},
            kit=kit_id,
            n=len(go),
        ),
        kept
        and M(
            "kits.remove.kept",
            {
                "one": "Package required by another toolkit: the box kept {n} package.",
                "other": "Packages required by another toolkit: the box kept {n} packages.",
            },
            n=len(kept),
        ),
    )


# --- the owner's own kits, and extra tools in a shipped kit ----------------------------------

MAX_OWN = 40


def _checked_packages(pkgs, most):
    """Package names the page typed: Debian's rules, no repeats, and each one this box's package
    lists know (apt-cache), so a typo is refused now rather than at the next fetch."""
    if not isinstance(pkgs, list) or len(pkgs) > most or not all(isinstance(p, str) and PKG_RE.match(p) for p in pkgs):
        raise Refused(
            M(
                "kits.packages.invalid",
                "Enter up to {most} Debian package names. Use lowercase letters, digits, '+', '-' and '.'.",
                most=most,
            )
        )
    pkgs = list(dict.fromkeys(pkgs))
    with aptram.shared():
        unknown = [
            p
            for p in pkgs
            if not run(["apt-cache", "show", "--no-all-versions", p], check=False, timeout=120).stdout.strip()
        ]
    if unknown:
        raise Refused(
            M(
                "kits.packages.unknown",
                "These packages are not in this box's package lists: {packages}. "
                "Check the names, or refresh the lists while the box has internet.",
                packages=unknown,
            )
        )
    return pkgs


def define(spec):
    """A kit of the owner's own, new or changed. spec: {id, title, summary, packages, remove_after_hours}."""
    if not isinstance(spec, dict):
        raise Refused(M("kits.define.not-object", "The request is not valid: the kit must be an object."))
    kid = str(spec.get("id", ""))
    if not ID_RE.match(kid) or kid == "extras":
        raise Refused(M("kits.define.bad-id", "The ID is not valid. Use up to 32 lowercase letters, digits and '-'."))
    if kid in kitdefs.shipped():
        raise Refused(
            M("kits.define.shipped", "{kit} is one of the box's own toolkits. Add tools to it instead.", kit=kid)
        )
    title = str(spec.get("title", "")).strip()
    summary = str(spec.get("summary", "")).strip()
    if not 1 <= len(title) <= 60 or len(summary) > 200 or any(c in title + summary for c in "\n\r<>"):
        raise Refused(
            M(
                "kits.define.bad-title",
                "Enter a title of 1 to 60 characters and a summary of up to 200 characters, one line each.",
            )
        )
    hours = spec.get("remove_after_hours", 24)
    if hours is not None and (type(hours) is not int or not 1 <= hours <= 24 * 365):
        raise Refused(
            M(
                "kits.define.bad-hours",
                "The request is not valid: remove_after_hours must be 1 to 8760, or null for never.",
            )
        )
    pkgs = _checked_packages(spec.get("packages"), MAX_OWN)
    if not pkgs:
        raise Refused(M("kits.define.no-packages", "Enter at least one package."))
    kitdefs.OWNER.mkdir(parents=True, exist_ok=True)
    os.chmod(kitdefs.OWNER, 0o755)  # A folder of kit definitions the hub reads. # nosec B103
    new = not (kitdefs.OWNER / f"{kid}.json").exists()
    _write(
        kitdefs.OWNER / f"{kid}.json",
        {
            "id": kid,
            "title": title,
            "summary": summary or f"{len(pkgs)} packages that an administrator selected.",
            "packages": pkgs,
            "remove_after_hours": hours,
            "consent": "Packages you selected: " + ", ".join(pkgs) + ". Some can run services (left stopped) or "
            "need root to use. They are removed again after the time you choose.",
            "notes": ["One of your own: added on this box's Library → Toolkits."],
        },
    )
    os.chmod(kitdefs.OWNER / f"{kid}.json", 0o644)
    if new:
        return M(
            "kits.define.created",
            {
                "one": "Custom toolkit created: {title}, {n} package. To cache it, download it while the box has internet.",
                "other": "Custom toolkit created: {title}, {n} packages. To cache it, download it while the box has internet.",
            },
            title=title,
            n=len(pkgs),
        )
    return M(
        "kits.define.changed",
        {
            "one": "Custom toolkit changed: {title}, {n} package. To cache it, download it while the box has internet.",
            "other": "Custom toolkit changed: {title}, {n} packages. To cache it, download it while the box has internet.",
        },
        title=title,
        n=len(pkgs),
    )


def undefine(kit_id, log=print):
    """One of the owner's own kits, gone: its definition, its cache (its manifests; the pool
    keeps what another kit still names)."""
    if kit_id not in definitions() or not definitions()[kit_id].get("owner"):
        raise Refused(M("kits.undefine.not-own", "{kit} is not one of your own toolkits.", kit=kit_id))
    if kit_id in installed_state():
        raise Refused(
            M("kits.undefine.installed", "{kit} is installed. Remove the toolkit before you delete it.", kit=kit_id)
        )
    (kitdefs.OWNER / f"{kit_id}.json").unlink(missing_ok=True)
    for m in (f"{kit_id}.json", f"{kit_id}.previous.json"):
        (MANIFESTS / m).unlink(missing_ok=True)
    if POOL.is_dir():
        prune()
        write_index()
    return M(
        "kits.undefine.done", "Custom toolkit deleted: {kit}. The box also deleted its cached packages.", kit=kit_id
    )


def set_extra(kit_id, packages):
    """Extra tools tracked in a shipped kit ([] clears them)."""
    if kit_id not in kitdefs.shipped():
        raise Refused(M("kits.extra.not-shipped", "{kit} is not one of the box's own toolkits.", kit=kit_id))
    base = set(kitdefs.shipped()[kit_id]["packages"])
    pkgs = [p for p in _checked_packages(packages, 20) if p not in base]
    kitdefs.OWNER.mkdir(parents=True, exist_ok=True)
    os.chmod(kitdefs.OWNER, 0o755)  # A folder of kit definitions the hub reads. # nosec B103
    extras = _read(kitdefs.OWNER / "extras.json", {}) or {}
    if pkgs:
        extras[kit_id] = pkgs
    else:
        extras.pop(kit_id, None)
    _write(kitdefs.OWNER / "extras.json", extras)
    os.chmod(kitdefs.OWNER / "extras.json", 0o644)
    if pkgs:
        return M(
            "kits.extra.set", "{kit} also tracks {packages}. The next download gets them.", kit=kit_id, packages=pkgs
        )
    return M("kits.extra.none", "{kit} has no extra tools.", kit=kit_id)


def rollback(kit_id, log=print):
    """The previous set becomes current (and the current one previous), offline; an installed kit
    is put back to those versions."""
    kit = _kit(kit_id)
    cur, prev = manifest(kit_id), manifest(kit_id, "previous")
    if not prev:
        raise Refused(M("kits.rollback.none", "{title} has no previous version to go back to.", title=kit["title"]))
    bad = verify(kit_id)
    if bad:
        raise Refused(
            M(
                "kits.rollback.cache-bad",
                "Cache verification failed. The box changed nothing. The problems: {problems}.",
                problems=bad[:5],
            )
        )
    _write(MANIFESTS / f"{kit_id}.json", prev)
    _write(MANIFESTS / f"{kit_id}.previous.json", cur)
    write_index()
    restored = M(
        "kits.rollback.restored",
        "Restore cached package versions: {title}. The cache now holds the set downloaded on {date}.",
        title=kit["title"],
        date=when(prev["fetched"]),
    )
    if kit_id in installed_state():
        present = _installed_versions()
        pins = [
            f"{p['name']}={p['version']}"
            for p in prev["packages"]
            if p["name"] in present and present[p["name"]] != p["version"]
        ]
        if pins:
            log(f"{kit_id}: installing the previous versions of {len(pins)} packages")
            POLICY_RC.write_text("#!/bin/sh\nexit 101\n")
            os.chmod(POLICY_RC, 0o755)  # policy-rc.d is a script dpkg runs. # nosec B103
            try:
                run(["apt-get", *_apt_offline(), "update", "-q"], timeout=300)
                run(
                    [
                        "apt-get",
                        *_apt_offline(),
                        "install",
                        "-y",
                        "-q",
                        "--no-install-recommends",
                        "--allow-downgrades",
                        *pins,
                    ],
                    timeout=3600,
                )
            finally:
                POLICY_RC.unlink(missing_ok=True)
        return sentences(
            restored,
            pins
            and M(
                "kits.rollback.downgraded",
                {
                    "one": "The box also installed the previous version of {n} installed package.",
                    "other": "The box also installed the previous versions of {n} installed packages.",
                },
                n=len(pins),
            ),
        )
    return sentences(
        restored, M("kits.rollback.not-installed", "The toolkit is not installed, so no installed package changed.")
    )


def drop_previous(kit_id, log=print):
    """The older set kept for Roll back, removed to save space: its files go unless the current sets still use them."""
    kit = _kit(kit_id)
    prev = manifest(kit_id, "previous")
    if not prev:
        raise Refused(M("kits.drop.none", "{title} has no older set to remove.", title=kit["title"]))
    freed = _trailing(manifest(kit_id), prev)["extra_bytes"]
    (MANIFESTS / f"{kit_id}.previous.json").unlink(missing_ok=True)
    prune()
    write_index()
    return M(
        "kits.drop.done",
        "{title}: the box removed the older set and freed {mb} MB. Roll back is available again after a refresh "
        "brings new versions.",
        title=kit["title"],
        mb=freed >> 20,
    )


def drop_old_previous(days, now=None, log=print):
    """The sweep by age (Toolkits, Older sets kept for Roll back): each older set whose newer set replaced it more than
    days ago is removed, as Remove older set does."""
    now = now or time.time()
    dropped, freed = [], 0
    for kid in definitions():
        prev, cur = manifest(kid, "previous"), manifest(kid)
        if not prev or not cur or now - (cur.get("fetched") or now) < days * 86400:
            continue
        freed += _trailing(cur, prev)["extra_bytes"]
        (MANIFESTS / f"{kid}.previous.json").unlink(missing_ok=True)
        dropped.append(_kit(kid)["title"])
    if not dropped:
        return M(
            "kits.sweep.none",
            {
                "one": "No older set is past {n} day. Nothing changed.",
                "other": "No older set is past {n} days. Nothing changed.",
            },
            n=days,
        )
    prune()
    write_index()
    return M(
        "kits.sweep.done",
        {
            "one": "The box removed {n} older set past its days ({kits}) and freed {mb} MB.",
            "other": "The box removed {n} older sets past their days ({kits}) and freed {mb} MB.",
        },
        n=len(dropped),
        kits=dropped,
        mb=freed >> 20,
    )


def drop_all_previous(log=print):
    """Clear old rollback cache: every older set kept for Roll back removed at once. Their files go unless a current set
    still uses them."""
    dropped, freed = [], 0
    for kid in definitions():
        prev = manifest(kid, "previous")
        if not prev:
            continue
        freed += _trailing(manifest(kid), prev)["extra_bytes"]
        (MANIFESTS / f"{kid}.previous.json").unlink(missing_ok=True)
        dropped.append(_kit(kid)["title"])
    if not dropped:
        return M("kits.drop-all.none", "No toolkit has an older set. Nothing changed.")
    prune()
    write_index()
    return M(
        "kits.drop-all.done",
        {
            "one": "The box removed the older set of {n} toolkit ({kits}) and freed {mb} MB. Roll back is "
            "available again after a refresh brings new versions.",
            "other": "The box removed the older sets of {n} toolkits ({kits}) and freed {mb} MB. Roll back "
            "is available again after a refresh brings new versions.",
        },
        n=len(dropped),
        kits=dropped,
        mb=freed >> 20,
    )


# --- by USB stick --------------------------------------------------------------------------------
# A stick's .debs are trusted only as far as Debian's own signatures vouch for them, as apt does
# online: the export carries the signed indexes (InRelease, and each Packages file that lists the
# kit's packages) from /var/lib/apt/lists; the import checks each InRelease's signature with this
# box's keys (gpgv), each Packages file against its InRelease, and each .deb against its Packages
# file, before anything enters the pool. Layout on the stick:
#   irate-box/kits/<arch>/<kit>/manifest.json, debs/*.deb, lists/*_InRelease, lists/*_Packages

APT_LISTS = Path(os.environ.get("HUB_APT_LISTS", "/var/lib/apt/lists"))
LIST_RE = re.compile(r"^(?P<release>.+_dists_[^_]+)_(?P<path>.+_Packages)$")


def _packages_hashes(path):
    """{deb file name: sha256} from an apt Packages file."""
    with open(path, errors="replace") as fh:
        return _hashes_in(fh)


def _hashes_in(lines):
    out, name, sha = {}, None, None
    for line in lines:
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


INDEX_XZ_RATIO = 5  # Debian's Packages indexes shrink about this much in xz, for the size said beforehand
INDEX_MAX = 512 << 20  # an index read back from a stick is refused past this, uncompressed


def _stick_index(path):
    """(sha256 of the index as its release lists it, {deb file: sha256}) for a Packages index on a stick, plain or
    .xz: read as a stream, nothing unpacked to disk, refused past INDEX_MAX."""
    h, n = hashlib.sha256(), 0

    def lines(fh):
        nonlocal n
        rest = b""
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            n += len(chunk)
            if n > INDEX_MAX:
                raise Refused(
                    M(
                        "kits.usb.index-too-big",
                        "{file} unpacks to more than {mb} MB. Import refused.",
                        file=path.name,
                        mb=INDEX_MAX >> 20,
                    )
                )
            h.update(chunk)
            *whole, rest = (rest + chunk).split(b"\n")
            for line in whole:
                yield line.decode(errors="replace") + "\n"
        if rest:
            yield rest.decode(errors="replace")

    try:
        with (lzma.open if path.suffix == ".xz" else open)(path, "rb") as fh:
            hashes = _hashes_in(lines(fh))
    except (lzma.LZMAError, EOFError, OSError) as e:
        raise Refused(
            M(
                "kits.usb.index-unreadable",
                "The box could not read {file}: {detail}. Import refused.",
                file=path.name,
                detail=str(e),
            )
        ) from None
    return h.hexdigest(), hashes


def _vouching(want):
    """([index, its InRelease, ...], {file vouched for}): the signed indexes on this box that list the wanted
    files' hashes. want: {file: sha256}."""
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
    return lists, vouched


def export_usb(kit_id, dest, report=None):
    """The kit's current set onto a stick folder (`dest`, its irate-box/kits/), with the signed
    indexes that vouch for it. Returns what went, in English: the offline kit's list of contents holds it as text."""
    kit = _kit(kit_id)
    man = manifest(kit_id)
    if not man:
        raise Refused(
            M(
                "kits.export.not-cached",
                "{title} is not in the cache. Download it while the box has internet, then export it.",
                title=kit["title"],
            )
        )
    if verify(kit_id):
        raise Refused(
            M(
                "kits.export.cache-bad",
                "Cache verification failed. The box did not export the toolkit. Download it again, then export it.",
            )
        )
    want = {p["file"]: p["sha256"] for p in man["packages"]}
    lists, vouched = _vouching(want)
    unvouched = sorted(set(want) - vouched)
    if unvouched:
        raise Refused(
            M(
                "kits.export.unvouched",
                "Signed index unavailable: another box could not verify {packages}. Export refused. "
                "While the box has internet, refresh the package lists and then download the toolkit again.",
                packages=unvouched[:5],
            )
        )
    folder = Path(dest) / man.get("arch", arch()) / kit_id
    shutil.rmtree(folder, ignore_errors=True)
    (folder / "debs").mkdir(parents=True)
    (folder / "lists").mkdir()
    # The indexes go xz-compressed (a fifth of their size or less); the import checks what they decompress to
    # against the hash their signed release lists, as for a plain one.
    files = [(POOL / f, folder / "debs" / f) for f in sorted(want)] + [
        (ln, folder / "lists" / (ln.name + ".xz" if ln.name.endswith("_Packages") else ln.name))
        for ln in sorted(set(lists))
    ]
    total = sum(src.stat().st_size // (INDEX_XZ_RATIO if dst.suffix == ".xz" else 1) for src, dst in files)
    if shutil.disk_usage(folder).free < total:
        shutil.rmtree(folder, ignore_errors=True)
        raise Refused(
            M(
                "kits.export.no-space",
                "Not enough space on the USB drive. The export needs {need} MB. The USB drive has {free} MB free.",
                need=total >> 20,
                free=shutil.disk_usage(Path(dest)).free >> 20,
            )
        )
    done = 0
    for src, dst in files:
        if dst.suffix == ".xz":
            with open(src, "rb") as inp, lzma.open(dst, "wb", preset=3) as out:
                shutil.copyfileobj(inp, out, 1 << 20)
        else:
            shutil.copyfile(src, dst, follow_symlinks=False)
        done += dst.stat().st_size
        if report:
            report(done, total)
    total = sum(dst.stat().st_size for _, dst in files)
    _write(folder / "manifest.json", dict(man, kit=kit_id, title=kit["title"], exported=time.time()))
    return f"{kit['title']}: {len(want)} packages and {len(set(lists)) // 2} signed indexes, {total >> 20} MB, copied to the USB drive"


def stick_kits(root):
    """Kits on a stick's irate-box/kits/, for this box's architecture or not."""
    out = []
    base = Path(root) / "irate-box" / "kits"
    for m in sorted(base.glob("*/*/manifest.json")) if base.is_dir() else []:
        if m.is_symlink():
            continue
        man = _read(m, None)
        if isinstance(man, dict) and ID_RE.match(m.parent.name) and isinstance(man.get("packages"), list):
            out.append(
                {
                    "kit": m.parent.name,
                    "arch": m.parent.parent.name,
                    "title": str(man.get("title", m.parent.name))[:60],
                    "packages": len(man["packages"]),
                    "bytes": int(man.get("bytes") or 0),
                    "fetched": man.get("fetched"),
                }
            )
    return out


def _verified_release(inrelease, work):
    """The signed content of an InRelease file, checked with Debian's archive keyring alone; Refused if not.
    Not every key apt trusts here: a key the owner added for another repository must not vouch for a kit,
    and the release's own Origin line is the release's word, not proof."""
    if not DEBIAN_KEYRING.is_file():
        raise Refused(
            M(
                "kits.usb.no-keyring",
                "Debian's archive keyring {file} is missing, so the box cannot check a kit. Import refused. "
                "Install the package debian-archive-keyring.",
                file=str(DEBIAN_KEYRING),
            )
        )
    out = work / f"{inrelease.name}.verified"
    r = run(["gpgv", "--keyring", str(DEBIAN_KEYRING), "--output", str(out), str(inrelease)], check=False, timeout=120)
    if r.returncode != 0 or not out.exists():
        raise Refused(
            M(
                "kits.usb.signature",
                "Signature invalid for {file}. Debian's archive keyring does not verify it. Import refused.",
                file=inrelease.name,
            )
        )
    return out.read_text(errors="replace")


def _release_date(text, field):
    """A Release file's Date or Valid-Until, as a timestamp, or None (absent or unreadable)."""
    got = _release_field(text, field)
    return got if isinstance(got, float) else None


def _release_field(text, field):
    """A Release file's Date or Valid-Until: a timestamp, None when the field is absent, "bad" when it is
    there but unreadable."""
    import email.utils

    for line in text.splitlines():
        if line.startswith(field + ":"):
            try:
                return email.utils.parsedate_to_datetime(line.split(":", 1)[1].strip()).timestamp()
            except (TypeError, ValueError):
                return "bad"
    return None


def _clock_trusted():
    """Why the system clock can be believed (rtc.trusted: network time, or set by an administrator), or None."""
    from irate_box.root import rtc

    return rtc.trusted()


UNKNOWN_TIME = "unknown-time"  # the freshness approval: imported although the clock could not date it


def _not_stale(text, name, approved=False):
    """A signed release from a stick must be within its Valid-Until and no older than the same
    release this box already holds: a stick could otherwise replay an old, signed, since-fixed set.
    The expiry needs a clock the box can believe. Without one, the import is refused unless approved
    (approved=True: the owner accepted that freshness is not checked); the rest is checked either way.
    Returns UNKNOWN_TIME when the expiry went unchecked, else None. Refused if not fresh."""
    date, until = _release_field(text, "Date"), _release_field(text, "Valid-Until")
    if not isinstance(date, float) or until == "bad":
        raise Refused(
            M(
                "kits.usb.release-date",
                "The release file {file} has no readable Date, or an unreadable Valid-Until. Import refused.",
                file=name,
            )
        )
    clock = _clock_trusted()
    if not clock and not approved:
        raise Refused(
            M(
                "kits.usb.time-untrusted",
                "The box cannot check that {file} is still current: its clock has no network time and no "
                "administrator set it. A signature shows who made a release, not that it is the latest. "
                "Set the clock on the Clock page, or import without a freshness check.",
                file=name,
            )
        )
    if clock and until is not None and until < time.time():
        raise Refused(
            M(
                "kits.usb.expired",
                "Release metadata expired for {file} on {date}. Import refused. "
                "Export the toolkit again from a box that has had internet since then.",
                file=name,
                date=when(until),
            )
        )
    mine = APT_LISTS / name
    if mine.is_file() and not mine.is_symlink():
        own = _release_date(mine.read_text(errors="replace"), "Date")
        if own is not None and date < own:
            raise Refused(
                M(
                    "kits.usb.older",
                    "Imported metadata older than local copy for {file}: the USB drive has the release of {theirs}. "
                    "This box has the release of {ours}. Import refused.",
                    file=name,
                    theirs=when(date),
                    ours=when(own),
                )
            )
    return None if clock else UNKNOWN_TIME


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


def import_usb(src_root, kit_id, budget_mb=500, report=None, freshness=None):
    """A kit from a stick (`src_root` its irate-box/kits/) into the pool, every .deb checked against
    Debian's signatures first. The kit's current set becomes previous, as after a fetch.
    freshness=UNKNOWN_TIME: the owner's approval to import although the clock cannot date the release
    (its caller has checked that the owner may give it); recorded in the kit's manifest."""
    kit = _kit(kit_id)
    here = arch()
    folder = Path(src_root) / here / kit_id
    if not (folder / "manifest.json").is_file():
        raise Refused(
            M(
                "kits.usb.arch",
                "Wrong architecture: the USB drive has no {kit} toolkit for this box's architecture ({arch}). Import refused.",
                kit=kit_id,
                arch=here,
            )
        )
    man = _read(folder / "manifest.json", None)
    if not isinstance(man, dict) or not isinstance(man.get("packages"), list):
        raise Refused(M("kits.usb.manifest", "The toolkit manifest on the USB drive is unreadable. Import refused."))
    work = Path(tempfile.mkdtemp(prefix="kit-usb-"))
    unchecked = []  # the releases imported on the owner's word, their freshness unchecked
    try:
        vouch = set()
        for pk in sorted([*(folder / "lists").glob("*_Packages"), *(folder / "lists").glob("*_Packages.xz")]):
            m = LIST_RE.match(pk.name.removesuffix(".xz"))
            rel = folder / "lists" / f"{m.group('release')}_InRelease" if m else None
            if not rel or not rel.is_file() or pk.is_symlink() or rel.is_symlink():
                continue
            text = _verified_release(rel, work)
            if _not_stale(text, rel.name, approved=freshness == UNKNOWN_TIME):
                # Kept so that once the clock can be believed, the security doctor can date it after all.
                until = _release_field(text, "Valid-Until")
                unchecked.append({"file": rel.name, "date": _release_date(text, "Date"), "valid_until": until})
            listed = _sha256_section(text)
            path = m.group("path").replace("_", "/")
            digest, hashes = _stick_index(pk)
            if listed.get(path) != digest:
                raise Refused(
                    M(
                        "kits.usb.index-mismatch",
                        "Signature invalid: {file} does not match the index that its signed release lists. Import refused.",
                        file=pk.name,
                    )
                )
            vouch |= set(hashes.values())
        pkgs = []
        for p in man["packages"]:
            name = str(p.get("file", ""))
            deb = folder / "debs" / name
            if not DEB_RE.match(name) or deb.is_symlink() or not deb.is_file():
                if not name:
                    raise Refused(
                        M(
                            "kits.usb.unnamed",
                            "The toolkit manifest on the USB drive lists a package with no file. Import refused.",
                        )
                    )
                raise Refused(M("kits.usb.missing", "{file} is missing from the USB drive. Import refused.", file=name))
            h = sha256(deb)
            if h not in vouch:
                raise Refused(
                    M(
                        "kits.usb.hash",
                        "Package hash mismatch for {file}: no signed index on the USB drive lists its hash. Import refused. "
                        "The box imported nothing.",
                        file=name,
                    )
                )
            pkgs.append(
                {
                    "name": str(p["name"]),
                    "version": str(p["version"]),
                    "arch": str(p.get("arch", "")),
                    "source": str(p.get("source", "")),
                    "file": name,
                    "size": deb.stat().st_size,
                    "sha256": h,
                }
            )
        total = sum(q["size"] for q in pkgs) + pool_bytes()
        if total > budget_mb << 20:
            raise Refused(
                M(
                    "kits.usb.over-limit",
                    "The toolkit cache would be {mb} MB. This is more than its storage limit of {limit} MB. Import refused.",
                    mb=total >> 20,
                    limit=budget_mb,
                )
            )
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
                    raise Refused(
                        M(
                            "kits.usb.copy-changed",
                            "Copy changed during transfer: {file} differs after copying. Import refused. "
                            "Try again or use another USB drive.",
                            file=q["file"],
                        )
                    )
                os.chmod(tmp, 0o644)
                os.replace(tmp, dest)
            done += q["size"]
            if report:
                report(done, total)
        cur = manifest(kit_id)
        if cur and sorted(x["sha256"] for x in cur["packages"]) != sorted(q["sha256"] for q in pkgs):
            _write(MANIFESTS / f"{kit_id}.previous.json", cur)
        _write(
            MANIFESTS / f"{kit_id}.json",
            {
                "id": kit_id,
                "fetched": man.get("fetched") or time.time(),
                "packages": pkgs,
                "on_box": man.get("on_box", []),
                "left_out": man.get("left_out", []),
                "arch": here,
                "bytes": sum(q["size"] for q in pkgs),
                "from_usb": time.time(),
                # Imported on the owner's word that a release the clock could not date is current enough.
                **({"freshness": UNKNOWN_TIME, "releases_unchecked": unchecked} if unchecked else {}),
            },
        )
        prune()
        write_index()
        if unchecked:
            return M(
                "kits.usb.imported.unchecked",
                {
                    "one": "{title}: {n} package imported, its hash checked against Debian's signed indexes on the USB "
                    "drive. Their freshness was not checked: the clock could not date them.",
                    "other": "{title}: {n} packages imported, each hash checked against Debian's signed indexes on the "
                    "USB drive. Their freshness was not checked: the clock could not date them.",
                },
                title=kit["title"],
                n=len(pkgs),
            )
        return M(
            "kits.usb.imported",
            {
                "one": "{title}: {n} package imported. The box checked its hash against Debian's signed indexes on the USB drive.",
                "other": "{title}: {n} packages imported. The box checked each package hash against Debian's signed indexes "
                "on the USB drive.",
            },
            title=kit["title"],
            n=len(pkgs),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def expire(now=None):
    """Remove every kit whose time is up. Returns the lines, in English: the root helper's kit-expire joins them."""
    now = now or time.time()
    return [str(remove(k)) for k, v in installed_state().items() if v.get("remove_at") and v["remove_at"] <= now]


def _trailing(cur, prev):
    """The previous set, kept for Roll back: when it was fetched, its size, and what it keeps beyond the
    current set: the packages at another version, and the bytes of the files only it uses."""
    now = {p["name"]: p["version"] for p in (cur or {}).get("packages", [])}
    files = {p["file"] for p in (cur or {}).get("packages", [])} | {w["file"] for w in (cur or {}).get("wheels", [])}
    older = sorted(p["name"] for p in prev.get("packages", []) if p["name"] in now and now[p["name"]] != p["version"])
    extra = sum(x["size"] for x in [*prev.get("packages", []), *prev.get("wheels", [])] if x["file"] not in files)
    return {"fetched": prev["fetched"], "bytes": prev["bytes"], "older": older, "extra_bytes": extra}


def unchecked(now=None):
    """The cached kits imported without a freshness check, as they stand now: [{kit, title, state}], state
    "unknown" while the clock cannot be believed, "expired" when it can and a release's Valid-Until has passed.
    A kit whose releases turn out current once the clock is believed is not listed: its mark has done its job.
    Re-importing with a believed clock, downloading it again or removing it writes a manifest without the mark."""
    clock, now = _clock_trusted(), now or time.time()
    out = []
    for kid, k in definitions().items():
        cur = manifest(kid)
        if not cur or cur.get("freshness") != UNKNOWN_TIME:
            continue
        rels = cur.get("releases_unchecked") or []
        if not clock:
            state = "unknown"
        elif any(isinstance(r.get("valid_until"), (int, float)) and r["valid_until"] < now for r in rels):
            state = "expired"
        else:
            continue
        out.append({"kit": kid, "title": k.get("title", kid), "state": state})
    return out


def status():
    """For the hub (control/kits.json): what is cached, installed, and what's wrong."""
    kits = {}
    for kid, _k in definitions().items():
        cur, prev = manifest(kid), manifest(kid, "previous")
        kits[kid] = {
            "cached": cur
            and {
                "fetched": cur["fetched"],
                "packages": len(cur["packages"]),
                "bytes": cur["bytes"],
                "export_bytes": cur["bytes"] + cur.get("index_bytes", 0) // INDEX_XZ_RATIO,
                "wheels": len(cur.get("wheels", [])),
                "on_box": cur.get("on_box", []),
                "left_out": cur.get("left_out", []),
                "arch": cur.get("arch"),
                "versions": {p["name"]: p["version"] for p in cur["packages"]},
            },
            "previous": prev and _trailing(cur, prev),
        }
    return {
        "at": time.time(),
        "kits": kits,
        "installed": installed_state(),
        "pool_bytes": pool_bytes(),
        "wheelhouse_bytes": wheelhouse_bytes(),
        "problems": verify(),
    }


def import_dir(root, budget_mb=None, freshness=None):
    """Every kit in a folder of the stick layout (an offline kit's kits/), into the pool. Lines.
    freshness=UNKNOWN_TIME: root at the console approves releases the clock cannot date (the offline
    installer and the content import pass --unknown-time); each such kit's line says so."""
    if budget_mb is None:
        try:
            budget_mb = int(
                json.loads(
                    (Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "library" / "toolkits.json").read_text()
                ).get("budget_mb", 500)
            )
        except (OSError, ValueError, TypeError, AttributeError):
            budget_mb = 500
    out = []
    for folder in sorted((Path(root) / arch()).glob("*")):
        if not (folder / "manifest.json").is_file():
            continue
        try:
            out.append(import_usb(root, folder.name, budget_mb, freshness=freshness))
        except (ValueError, OSError) as exc:
            out.append(f"{folder.name}: {exc}")
    return out or [f"no toolkits for this box's architecture ({arch()}) in {root}"]


if __name__ == "__main__":
    import sys

    if os.geteuid() != 0:
        sys.exit("run as root")
    args = sys.argv[1:]
    unknown_time = "--unknown-time" in args
    args = [a for a in args if a != "--unknown-time"]
    if len(args) == 2 and args[0] == "import-dir":
        for line in import_dir(args[1], freshness=UNKNOWN_TIME if unknown_time else None):
            print(line)
        from irate_box.root import safeio

        state = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
        try:
            safeio.write(state / "control" / "kits.json", json.dumps(status()))
        except OSError:
            pass
    else:
        sys.exit(
            "usage: kits.py import-dir DIR [--unknown-time]\n"
            "  --unknown-time  import releases this box's clock cannot date (no network time, not set by an\n"
            "                  administrator): their freshness is not checked, and each such kit says so"
        )
