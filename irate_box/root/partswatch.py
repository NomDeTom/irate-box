# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The image's parts after an update: what an upgrade, `apt autoremove` or an interrupted
unattended run removed, broke or left half done.

After every dpkg run (`pkgcache`'s Post-Invoke hook, beside its reinstall record) a small snapshot of
the packages the box means to have is kept: the recommended set's parts, each installed toolkit's
packages, DKMS, and the kernels. The Updates doctor compares the last two snapshots and says what
changed, by which run. It also reads the update machinery itself:

  - dpkg --audit and apt-get check: half-configured or broken packages
  - the last unattended-upgrades run: an error, or apt-daily-upgrade.service's result
  - dkms status against the newest installed kernel: a kernel whose driver did not build
  - a watched service (chrony, bluetooth, NetworkManager, tailscaled, hostapd, meshtasticd) that
    failed after the last dpkg run
  - a held part with a newer candidate
  - a toolkit whose cached offline copy is behind what apt would install

Read only. Repairs are the owner's, on the page that owns the part; stdlib only.
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from irate_box.finding import finding
from irate_box.msg import M, when

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CONTROL = STATE / "control"
PARTS = Path(os.environ.get("HUB_PARTS_STATE", CONTROL / "parts.json"))
DPKG_LOG = Path(os.environ.get("HUB_DPKG_LOG", "/var/log/dpkg.log"))
KERNELS = Path(os.environ.get("HUB_KERNEL_MODULES", "/lib/modules"))
UNATTENDED = Path(os.environ.get("HUB_UNATTENDED_LOG", "/var/log/unattended-upgrades/unattended-upgrades.log"))

# The parts an image brings, watched whether or not a toolkit owns them. An installed toolkit's
# packages are added from its manifest, so the image-parts work needs no second list here.
BASE_PARTS = {
    "chrony",
    "systemd-timesyncd",
    "unattended-upgrades",
    "bluez",
    "bluez-tools",
    "bluetooth",
    "hostapd",
    "network-manager",
    "tailscale",
    "meshtasticd",
    "dkms",
    "wireless-regdb",
    "rfkill",
    "iw",
}
WATCHED_UNITS = {
    "chrony.service": "chrony",
    "bluetooth.service": "bluez",
    "NetworkManager.service": "network-manager",
    "tailscaled.service": "tailscale",
    "hostapd.service": "hostapd",
    "meshtasticd.service": "meshtasticd",
}
GO = {}


def _go():
    """The pages a repair lives on, read lazily (hub_control imports this module)."""
    if not GO:
        from irate_box.root import health

        GO.update(
            clock=health.GO_CLOCK,
            updates=health.GO_UPDATES,
            toolkits=health._go("toolkits", M("go.toolkits", "Toolkits")),
        )
    return GO


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
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, path)


def _installed_versions():
    out = run("dpkg-query", "-W", "-f", "${Package}\t${Version}\t${Status}\n", timeout=120)
    if out.returncode != 0:
        raise OSError((out.stderr or out.stdout or "dpkg-query failed").strip()[-200:])
    versions = {}
    for line in out.stdout.splitlines():
        name, _, rest = line.partition("\t")
        version, _, status = rest.partition("\t")
        if name and "install ok installed" in status:
            versions[name.split(":")[0]] = version
    return versions


def _kit_packages():
    """{package: kit} for each installed toolkit's current set."""
    out = {}
    try:
        from irate_box.root import kits

        for kit in kits.installed_state():
            man = kits.manifest(kit) or {}
            for package in man.get("packages", []):
                name = package.get("name")
                if isinstance(name, str) and name:
                    out.setdefault(name.split(":")[0], kit)
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        pass
    return out


def _watched():
    return BASE_PARTS | set(_kit_packages())


def dpkg_at():
    """The time of the last dpkg log line, or None."""
    try:
        lines = [ln for ln in DPKG_LOG.read_text(errors="replace").splitlines() if ln.strip()]
    except OSError:
        return None
    if not lines:
        return None
    try:
        stamp = " ".join(lines[-1].split()[:2])
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").timestamp()
    except (ValueError, IndexError):
        return None


def snapshot(versions=None):
    """Keep the watched parts beside pkgcache's record. Only a change writes the card."""
    versions = versions if versions is not None else _installed_versions()
    watched = _watched()
    keep = {
        name: version
        for name, version in versions.items()
        if name in watched or name.startswith(("linux-image", "linux-headers")) or name == "dkms"
    }
    current = {"installed": keep, "at": time.time(), "dpkg_at": dpkg_at()}
    old = _read(PARTS, {})
    if old.get("current", {}).get("installed") == keep:
        return old
    data = {"previous": old.get("current"), "current": current}
    try:
        _write(PARTS, data)
    except OSError:
        return old
    return data


def _removed():
    """Parts in the snapshot before that the newest snapshot no longer has."""
    data = _read(PARTS, {})
    was = (data.get("previous") or {}).get("installed") or {}
    now = (data.get("current") or {}).get("installed") or {}
    at = (data.get("current") or {}).get("dpkg_at")
    kits = _kit_packages()
    out = []
    for name, version in sorted(was.items()):
        if name in now or name not in _watched():
            continue
        kit = kits.get(name)
        if name in ("chrony", "systemd-timesyncd") and "systemd-timesyncd" in now:
            summary = M(
                "update.parts.clock",
                "{package} was removed by the update at {time}. The box keeps time with systemd-timesyncd again.",
                package=name,
                time=when(at or time.time()),
            )
        else:
            summary = M(
                "update.parts.removed",
                "{package} was removed by the update at {time}. The box no longer has it.",
                package=name,
                time=when(at or time.time()),
            )
        out.append(
            finding(
                f"parts-removed:{name}",
                M("update.parts.title", "Packages after an update"),
                "warn",
                summary,
                M(
                    "update.parts.removed.why",
                    "An upgrade or apt autoremove took a part the box was set up with. Something that used it "
                    "now works differently, or not at all.",
                ),
                M(
                    "update.parts.removed.fix-kit",
                    "On Toolkits, install the {kit} toolkit again.",
                    kit=kit,
                )
                if kit
                else (
                    M("update.parts.removed.fix-clock", "On Box, Clock, select the time source the box should keep.")
                    if name in ("chrony", "systemd-timesyncd")
                    else M(
                        "update.parts.removed.fix",
                        "Install the package again from the page that owns it, or from a toolkit that carries it.",
                    )
                ),
                evidence=[f"was {version}"],
                go=GO["toolkits"]
                if kit
                else (GO["clock"] if name in ("chrony", "systemd-timesyncd") else GO["updates"]),
            )
        )
    return out


def _broken():
    try:
        audit = run("dpkg", "--audit", timeout=120)
        check = run("apt-get", "check", "-q", timeout=180)
    except (OSError, subprocess.SubprocessError):
        return []
    lines = [ln for ln in (audit.stdout + check.stdout + audit.stderr + check.stderr).splitlines() if ln.strip()]
    if audit.returncode == 0 and check.returncode == 0 and not lines:
        return []
    count = len([ln for ln in audit.stdout.splitlines() if ln.strip()])
    return [
        finding(
            "packages-broken",
            M("update.parts-broken.title", "Packages after an update"),
            "problem",
            M(
                "update.parts-broken.found",
                {
                    "one": "One package is half set up or broken after an update.",
                    "other": "{n} packages are half set up or broken after an update.",
                },
                n=count or 1,
            ),
            M(
                "update.parts-broken.why",
                "An interrupted or stopped upgrade leaves packages half configured. The next install or upgrade "
                "cannot finish while they are.",
            ),
            M(
                "update.parts-broken.fix",
                "Let dpkg finish the packages, then let apt repair what depends on them. The commands here do "
                "both, and keep nothing that was not already installed.",
            ),
            commands=["dpkg --configure -a", "apt-get -f install"],
            evidence=lines[-6:],
            go=GO["updates"],
        )
    ]


def _unattended():
    out = []
    try:
        text = UNATTENDED.read_text(errors="replace")
    except OSError:
        text = ""
    if text:
        lines = [ln for ln in text.splitlines() if re.search(r"\b(error|failed|could not)\b", ln, re.I)]
        if lines:
            out.append(
                finding(
                    "unattended-failed",
                    M("update.unattended.title", "Automatic updates"),
                    "warn",
                    M(
                        "update.unattended.failed",
                        "The automatic update failed: {line}",
                        line=lines[-1].strip()[:300],
                    ),
                    M(
                        "update.unattended.failed.why",
                        "A run that stops on an error installs nothing after it, including security updates.",
                    ),
                    M(
                        "update.unattended.failed.fix",
                        "Run the automatic update again now, or leave it to flag only on Updates.",
                    ),
                    evidence=lines[-3:],
                    go=GO["updates"],
                )
            )
    props = {}
    try:
        said = run(
            "systemctl",
            "show",
            "-p",
            "Result",
            "-p",
            "ExecMainStatus",
            "-p",
            "ActiveState",
            "apt-daily-upgrade.service",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        said = None
    for line in (said.stdout if said else "").splitlines():
        key, _, value = line.partition("=")
        props[key] = value
    if props.get("Result") == "failed" or (props.get("ExecMainStatus") or "0") not in ("0", ""):
        out.append(
            finding(
                "unattended-service",
                M("update.unattended.title", "Automatic updates"),
                "problem",
                M(
                    "update.unattended.service",
                    "The automatic update service did not finish: {result}.",
                    result=props.get("Result") or props.get("ExecMainStatus") or "failed",
                ),
                fix=M(
                    "update.unattended.service.fix",
                    "Run the automatic update again now, or leave it to flag only on Updates.",
                ),
                go=GO["updates"],
            )
        )
    return out


def _version_key(name):
    return tuple(int(x) if x.isdigit() else x for x in re.split(r"[._+-]", name))


def _newest_kernel():
    try:
        names = [p.name for p in KERNELS.iterdir() if p.is_dir() and not p.is_symlink()]
    except OSError:
        return None, []
    names = [n for n in names if not n.startswith(".")]
    if not names:
        return None, []
    try:
        names.sort(key=_version_key)
    except TypeError:
        names.sort()
    return names[-1], names


def _kernel():
    newest, kernels = _newest_kernel()
    if not newest:
        return []
    try:
        said = run("dkms", "status", timeout=120)
    except (OSError, subprocess.SubprocessError):
        return []
    if said.returncode != 0 or not said.stdout.strip():
        return []
    installed = {}
    for line in said.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        module = parts[0].split("/", 1)[0]
        kernel = parts[1]
        status = parts[-1].split(":")[-1].strip()
        installed.setdefault(module, {})[kernel] = status
    missing = {
        module: kernels_
        for module, kernels_ in installed.items()
        if any(state == "installed" for state in kernels_.values()) and newest not in kernels_
    }
    if not missing:
        return []
    modules = ", ".join(sorted(missing))
    return [
        finding(
            "kernel-dkms",
            M("update.kernel.title", "The newest kernel's drivers"),
            "problem",
            M(
                "update.kernel.missing",
                "The new kernel {kernel} has no DKMS driver for: {modules}. After a reboot the box can lose its radio or network.",
                kernel=newest,
                modules=modules,
            ),
            M(
                "update.kernel.missing.why",
                "The newest installed kernel is the one the next boot takes. A driver that did not build for it "
                "leaves the hardware it drives without a module.",
            ),
            M(
                "update.kernel.missing.fix",
                "Do not restart the box until this is repaired. Rebuild the driver for the new kernel, or keep the "
                "running kernel as the default.",
            ),
            commands=["dkms status", f"uname -r  # now running, not {newest}"],
            evidence=[f"{m}: " + ", ".join(sorted(missing[m])) for m in sorted(missing)],
            go=GO["toolkits"],
        )
    ]


def _parse_systemd_time(value):
    m = re.match(r"^\w+ (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", value or "")
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S").timestamp()
    except ValueError:
        return None


def _services(installed=None):
    installed = installed if installed is not None else _installed_versions()
    since = dpkg_at()
    out = []
    for unit, package in WATCHED_UNITS.items():
        if package not in installed:
            continue
        try:
            said = run(
                "systemctl",
                "show",
                "-p",
                "ActiveState",
                "-p",
                "Result",
                "-p",
                "ActiveEnterTimestamp",
                unit,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        props = dict(line.partition("=")[::2] for line in said.stdout.splitlines() if "=" in line)
        state, result = props.get("ActiveState"), props.get("Result")
        started = _parse_systemd_time(props.get("ActiveEnterTimestamp"))
        if state != "failed" and result not in ("failed", "exit-code", "signal"):
            continue
        if since and started and started < since - 60:
            continue
        out.append(
            finding(
                f"parts-service:{unit}",
                M("update.parts-service.title", "Services after an update"),
                "warn",
                M(
                    "update.parts-service.failed",
                    "{unit} fails since the update at {time}.",
                    unit=unit,
                    time=when(since or time.time()),
                ),
                M(
                    "update.parts-service.failed.why",
                    "The package was updated and its service did not come back. Until it does, what it provides "
                    "is not on the box.",
                ),
                M(
                    "update.parts-service.failed.fix",
                    "Install the version the box kept for a reinstall, or restart the service after the repair.",
                ),
                evidence=[f"{unit}: {state or result}"],
                go=GO["updates"],
            )
        )
    return out


def _held():
    try:
        said = run("apt-mark", "showhold", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    if said.returncode != 0:
        return []
    watched = _watched()
    out = []
    for name in (ln.strip().split(":")[0] for ln in said.stdout.splitlines()):
        if not name or name not in watched:
            continue
        try:
            policy = run("apt-cache", "policy", name, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        versions = {}
        for line in policy.stdout.splitlines():
            key, _, value = line.strip().partition(":")
            if key in ("Installed", "Candidate"):
                versions[key] = value.strip()
        if versions.get("Installed") and versions.get("Candidate") and versions["Installed"] != versions["Candidate"]:
            out.append(
                finding(
                    f"parts-held:{name}",
                    M("update.parts-held.title", "Held packages"),
                    "note",
                    M(
                        "update.parts-held.waiting",
                        "{package} is held at {installed}. {candidate} waits.",
                        package=name,
                        installed=versions["Installed"],
                        candidate=versions["Candidate"],
                    ),
                    fix=M(
                        "update.parts-held.fix",
                        "If the hold is no longer wanted, unhold the package on Updates before installing.",
                    ),
                    evidence=[f"apt-mark showhold: {name}"],
                    go=GO["updates"],
                )
            )
    return out


def _kits_behind():
    out = []
    try:
        from irate_box.root import kits

        for kit in kits.installed_state():
            result = kits.check_result(kit) or {}
            if result.get("state") != "newer":
                continue
            changes = result.get("changes") or []
            out.append(
                finding(
                    f"parts-kit:{kit}",
                    M("update.parts-kit.title", "Toolkit copies"),
                    "warn",
                    M(
                        "update.parts-kit.behind",
                        {
                            "one": "The {kit} toolkit's offline copy is older than the installed packages: {n} change.",
                            "other": "The {kit} toolkit's offline copy is older than the installed packages: {n} changes.",
                        },
                        kit=kit,
                        n=len(changes),
                    ),
                    M(
                        "update.parts-kit.behind.why",
                        "An offline reinstall from this toolkit would go back a version, or cannot install what the "
                        "box has now.",
                    ),
                    M("update.parts-kit.behind.fix", "On Toolkits, refresh the {kit} toolkit's cache.", kit=kit),
                    evidence=[f"{c.get('name')}: {c.get('from') or '?'} to {c.get('to') or '?'}" for c in changes[:6]],
                    go=GO["toolkits"],
                )
            )
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        pass
    return out


def findings():
    """Every finding for the Updates doctor: the parts an update removed or broke, the update
    machinery's own failures, and the parts the box's kits would install differently."""
    _go()
    installed = None
    out = []
    try:
        installed = _installed_versions()
    except OSError:
        pass
    for check in (_removed, _broken, _unattended, _kernel):
        try:
            out += check()
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            continue
    for check in (lambda: _services(installed or {}), _held, _kits_behind):
        try:
            out += check()
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, KeyError):
            continue
    return out


if __name__ == "__main__":
    if sys.argv[1:] == ["snapshot"]:
        snapshot()
    elif sys.argv[1:] == ["findings"]:
        for item in findings():
            print(item["id"], item["status"], item["summary"])
    else:
        sys.exit("usage: irate-box partswatch snapshot|findings")
