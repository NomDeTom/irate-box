# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Services kept off the SD card where what they write is throwaway: a path of a service's own (a cache, a download,
an export) given a folder in RAM for that service, bound over the path, lost at each restart of the service or the
box and made again. Root's. Stdlib only.

Where the paths come from:
  - an add-on's manifest (apps.d), its "writes": {"ram": [...]} beside "status": {"unit": ...}: its maker's word
  - the owner, for a service with no manifest (a tracker put on the box by hand), from Box doctor's finding of a
    service that writes much to the card: /etc/hub/service-ram.json, {unit: [paths]}

How: one drop-in a unit, /etc/systemd/system/<unit>.d/50-irate-box-ram.conf, as was written by hand for the EWS
tracker on 2026-10-27: a RuntimeDirectory of the unit's own (/run/irate-box-ram/<unit>/<n>, made with the unit's
user, kept between runs while the box is up) bound over each path (BindPaths). The unit restarts to take it.
"""

import json
import os
import re
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
OWNER = ETC / "service-ram.json"
UNITS_ETC = Path(os.environ.get("HUB_SYSTEMD_UNITS", "/etc/systemd/system"))
DROPIN = "50-irate-box-ram.conf"
UNIT_RE = re.compile(r"^[A-Za-z0-9@._-]{1,128}\.service$")
PATH_RE = re.compile(r"^/(?!run/|proc/|sys/|dev/)[A-Za-z0-9._@+/-]{1,200}$")
MAX_PATHS = 8


def run(*cmd, timeout=120):
    """Every command goes through here (the tests stand it in)."""
    import subprocess

    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def ok_path(path):
    """A path that can be put in RAM: absolute, not under /run (already RAM) or the kernel's, no .. or //."""
    return bool(PATH_RE.match(path or "")) and ".." not in path and "//" not in path and path.rstrip("/") != ""


def owner():
    try:
        got = json.loads(OWNER.read_text())
    except (OSError, ValueError):
        return {}
    return {u: [p for p in ps if ok_path(p)] for u, ps in got.items() if UNIT_RE.match(u) and isinstance(ps, list)}


def declared(manifests=None):
    """{unit: [paths]}: the manifests' and the owner's, merged."""
    if manifests is None:
        from irate_box.hub import manifests as mf

        manifests = mf.load()
    out = {}
    for m in manifests:
        unit = (m.get("status") or {}).get("unit")
        ram = (m.get("writes") or {}).get("ram") or []
        if unit and ram:
            out.setdefault(unit, []).extend(p for p in ram if ok_path(p))
    for unit, paths in owner().items():
        out.setdefault(unit, []).extend(p for p in paths if p not in out.get(unit, []))
    return {u: ps[:MAX_PATHS] for u, ps in out.items() if ps}


def _stem(unit):
    return unit[: -len(".service")].replace("@", "-")


def dropin_text(unit, paths):
    dirs = [f"irate-box-ram/{_stem(unit)}/{i}" for i in range(len(paths))]
    lines = [
        "# Written by irate-box (servicewrites.py): these paths in RAM for this service, lost at each restart.",
        "[Service]",
        "RuntimeDirectory=" + " ".join(dirs),
        "RuntimeDirectoryPreserve=yes",
    ]
    lines += [f"BindPaths=/run/{d}:{p}" for d, p in zip(dirs, paths, strict=True)]
    return "\n".join(lines) + "\n"


def in_effect(unit, paths):
    """Whether the running unit has each path bound to RAM (systemd's own view of it)."""
    out = run("systemctl", "show", unit, "-p", "BindPaths", "--value", timeout=10).stdout
    bound = {pair.split(":")[1] for pair in out.split() if ":" in pair}
    return all(p in bound for p in paths)


def apply(manifests=None):
    """Every declared unit's drop-in written (or removed when it declares nothing now); systemd reloaded, and each
    unit whose drop-in changed restarted if it was running. [(unit, "set" | "removed")]."""
    want = declared(manifests)
    changed = []
    for unit, paths in want.items():
        if not UNIT_RE.match(unit):
            continue
        folder = UNITS_ETC / f"{unit}.d"
        text = dropin_text(unit, paths)
        try:
            same = (folder / DROPIN).read_text() == text
        except OSError:
            same = False
        if not same:
            safeio.mkdir(folder, mode=0o755)
            safeio.write(folder / DROPIN, text)
            changed.append((unit, "set"))
    for dropin in UNITS_ETC.glob(f"*.service.d/{DROPIN}"):
        unit = dropin.parent.name[: -len(".d")]
        if unit not in want:
            dropin.unlink()
            changed.append((unit, "removed"))
    if changed:
        run("systemctl", "daemon-reload", timeout=60)
        for unit, _ in changed:
            if run("systemctl", "is-active", unit, timeout=10).stdout.strip() == "active":
                run("systemctl", "try-restart", unit, timeout=120)
    return changed


def set_owner(unit, path, on=True):
    """The owner's choice for one path of one service: kept in RAM (on) or back on the card."""
    if not UNIT_RE.match(unit or "") or not ok_path(path):
        raise Refused(M("service-ram.bad", "That is not a service and path that can be kept in RAM."))
    mine = owner()
    paths = [p for p in mine.get(unit, []) if p != path]
    if on:
        if len(paths) >= MAX_PATHS:
            raise Refused(M("service-ram.many", "A service can keep at most {n} paths in RAM.", n=MAX_PATHS))
        paths.append(path)
    if paths:
        mine[unit] = paths
    else:
        mine.pop(unit, None)
    safeio.mkdir(ETC, mode=0o755)
    safeio.write(OWNER, json.dumps(mine, indent=1, sort_keys=True) + "\n")
    apply()
    if on:
        return M(
            "service-ram.on",
            "{path} is in RAM for {unit} now: what it holds is made again after each restart.",
            path=path,
            unit=unit,
        )
    return M("service-ram.off", "{path} is on the SD card again for {unit}.", path=path, unit=unit)
