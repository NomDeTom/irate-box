# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Where the box's packages come from, what Debian marks as its own, and the board's own packages.

The image adds apt sources beyond Debian's (Armbian's, mPWRD-OS's, Meshtastic's, Tailscale's, Caddy's).
A package the hub or a toolkit relies on that comes from one of those is a warning: it is updated on
that repository's schedule, not Debian's, and a repository that stops is a package that stops.

  - origins          each installed package's source, read with `apt-cache policy` once a day and kept in
                     control/package-origins.json, so a doctor run does not ask twice
  - priorities       Debian's required, important and standard packages that are not installed, streamed
                     from `apt-cache dumpavail` (never held whole), with the System toolkit and a shell's
                     apt install as the cures, and which priorities warn as the administrator's setting

Read only. Stdlib only.
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from irate_box.finding import finding
from irate_box.msg import M

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CONTROL = STATE / "control"
ORIGINS = Path(os.environ.get("HUB_PACKAGE_ORIGINS", CONTROL / "package-origins.json"))
CODE = Path(__file__).resolve().parents[2]
ORIGINS_MAX_AGE = 86400  # once a day: apt-cache policy over every installed package is a second or two on a small board

# A source's name, as a reader knows it. Anything not here is named by its host.
SOURCES = {
    "deb.debian.org": "Debian archive",
    "security.debian.org": "Debian Security",
    "ftp.debian.org": "Debian archive",
    "apt.armbian.com": "Armbian",
    "repo.armbian.com": "Armbian",
    "download.opensuse.org": "openSUSE Build Service",
    "pkgs.tailscale.com": "Tailscale",
    "dl.cloudsmith.io": "Cloudsmith (Caddy)",
}

PRIORITIES = ("required", "important", "standard")


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
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, path)


def installed():
    """The names of the installed packages, without their architecture suffix."""
    out = run("dpkg-query", "-W", "-f", "${Package}\t${Status}\n", timeout=120)
    if out.returncode != 0:
        raise OSError((out.stderr or out.stdout or "dpkg-query failed").strip()[-200:])
    names = {}
    for line in out.stdout.splitlines():
        name, _, status = line.partition("\t")
        if name and "install ok installed" in status:
            names[name.split(":")[0]] = name
    return names


def _architecture():
    try:
        return run("dpkg", "--print-architecture", timeout=30).stdout.strip() or "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


DEBIAN_ARCHIVE = {"deb.debian.org", "ftp.debian.org", "http.debian.net"}
DEBIAN_SECURITY = {"security.debian.org"}


def _source_of(url, suite=""):
    """(source, origin) for an apt URL: Debian, Debian Security, or the repository by name. The host is matched
    exactly (a look-alike such as evil-debian.org is another repository), and security comes from apt's own suite
    name, not a word in the path."""
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    archive = suite.split("/", 1)[0]
    if host in DEBIAN_SECURITY or (host in DEBIAN_ARCHIVE and archive.endswith("-security")):
        return "debian-security", SOURCES.get(host, host or url)
    if host in DEBIAN_ARCHIVE:
        return "debian", SOURCES[host]
    return "other", SOURCES.get(host, host or url)


def _policy(names):
    """{package: {source, origin, suite, url}} from `apt-cache policy`, in chunks so the command line stays short."""
    out = {}
    names = sorted(names)
    for i in range(0, len(names), 200):
        chunk = names[i : i + 200]
        try:
            said = run("apt-cache", "policy", *chunk, timeout=180).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        for block in said.split("\n\n"):
            lines = [ln.rstrip() for ln in block.splitlines() if ln.strip()]
            if not lines or not lines[0].endswith(":"):
                continue
            package = lines[0][:-1].split(":")[0]
            installed_version = ""
            for line in lines:
                if line.strip().startswith("Installed:"):
                    installed_version = line.split(":", 1)[1].strip()
            if not installed_version or installed_version == "(none)":
                continue
            for n, line in enumerate(lines):
                parts = line.split()
                if len(parts) < 2 or parts[0] not in ("***", installed_version):
                    continue
                if parts[0] == "***":
                    parts = parts[1:]
                if parts[0] != installed_version:
                    continue
                for after in lines[n + 1 : n + 4]:
                    fields = after.split()
                    if len(fields) >= 2 and fields[0].isdigit() and "://" in after:
                        url = next((f for f in fields if "://" in f), "")
                        suite = re.sub(r"\s+Packages$", "", after.split(url, 1)[1]).strip()
                        source, origin = _source_of(url, suite)
                        out[package] = {"source": source, "origin": origin, "suite": suite, "url": url}
                        break
                break
    return out


def origins(refresh=False):
    """Every installed package's source, read at most once a day unless refresh. Returns the record."""
    cached = _read(ORIGINS, {})
    packages = cached.get("packages") if isinstance(cached, dict) else None
    if not refresh and isinstance(packages, dict) and cached.get("at") and time.time() - cached["at"] < ORIGINS_MAX_AGE:
        return cached
    try:
        have = installed()
    except OSError:
        return cached if isinstance(cached, dict) else {"at": 0, "packages": {}}
    found = _policy(have.values())
    record = {"at": time.time(), "packages": found}
    try:
        _write(ORIGINS, record)
    except OSError:
        pass
    return record


def _system_kit():
    """The System toolkit's package names, the cure for what Debian calls its own."""
    try:
        from irate_box.root import kits

        man = kits.definitions().get("system") or {}
        return set(man.get("packages", [])) | set(man.get("alternatives", []))
    except (OSError, ValueError, TypeError, ImportError):
        return set()


def _relied_on():
    """The packages the hub's own package files and the toolkits name."""
    names = set(_system_kit())
    try:
        from irate_box.root import kits

        for man in kits.definitions().values():
            names.update(man.get("packages", []))
            names.update(man.get("alternatives", []))
    except (OSError, ValueError, TypeError, ImportError):
        pass
    try:
        for path in sorted((CODE / "packages.d").glob("*.json")):
            data = json.loads(path.read_text())
            if data.get("package"):
                names.add(data["package"])
    except (OSError, ValueError):
        pass
    return names


def origin_findings(record=None):
    """Where each installed package comes from, and a warning for a package the hub or a toolkit relies on
    that comes from outside Debian."""
    record = record if record is not None else origins()
    packages = record.get("packages") or {}
    title = M("package-origins.title", "Where packages come from")
    if not packages:
        return [
            finding(
                "package-origins",
                title,
                "note",
                M("origins.unknown", "The box could not read where its installed packages come from."),
                notes=[M("origins.unknown.note", "apt-cache policy is needed for this check.")],
            )
        ]
    other = {name: info for name, info in packages.items() if info.get("source") == "other"}
    relied = sorted(name for name in other if name in _relied_on())
    evidence = [f"{name}: {other[name]['origin']}" for name in relied]
    if relied:
        return [
            finding(
                "package-origins",
                title,
                "warn",
                M(
                    "origins.outside",
                    {
                        "one": "{n} package the hub or its toolkits rely on comes from outside Debian.",
                        "other": "{n} packages the hub or its toolkits rely on come from outside Debian.",
                    },
                    n=len(relied),
                ),
                M(
                    "origins.outside.why",
                    "It is updated on that repository's schedule, not Debian's, and a repository that stops is a "
                    "package that stops. Debian's security tracker does not see it.",
                ),
                M(
                    "origins.outside.fix",
                    "Read the source's page on Toolkits before relying on it. Where Debian has the same package, "
                    "prefer Debian's.",
                ),
                notes=(
                    [
                        M(
                            "origins.outside.more",
                            "Other packages also come from outside Debian, and are not listed here.",
                        )
                    ]
                    if len(other) > len(relied)
                    else []
                ),
                evidence=evidence,
            )
        ]
    return [
        finding(
            "package-origins",
            title,
            "ok",
            M(
                "origins.ok",
                {
                    "one": "Every package the hub or its toolkits rely on comes from Debian. {n} other package comes from outside.",
                    "other": "Every package the hub or its toolkits rely on comes from Debian. {n} other packages come from outside.",
                },
                n=len(other),
            ),
            notes=(
                [
                    M(
                        "origins.other",
                        "The ones outside Debian are from {names}.",
                        names=", ".join(sorted({info["origin"] for info in other.values()})),
                    )
                ]
                if other
                else []
            ),
        )
    ]


def _dumpavail():
    """apt's whole available-package list as an iterator of lines, streamed: the list is never held whole."""
    try:
        proc = subprocess.Popen(  # noqa: TID251
            ["apt-cache", "dumpavail"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        )
    except OSError:
        return iter(())

    def lines():
        try:
            if proc.stdout is not None:
                yield from proc.stdout
        finally:
            if proc.stdout is not None:
                proc.stdout.close()
            proc.wait()

    return lines()


def available_priorities(lines, arch="?"):
    """{priority: [package]} for Debian's required, important and standard packages available for this board."""
    wanted = set(PRIORITIES)
    found = {p: set() for p in PRIORITIES}
    fields = {}

    def keep():
        name = fields.get("Package", "")
        priority = fields.get("Priority", "").lower()
        package_arch = fields.get("Architecture", "all")
        if name and priority in wanted and package_arch in (arch, "all", "?"):
            found[priority].add(name.split(":")[0])
        fields.clear()

    for line in lines:
        if not line.strip():
            keep()
            continue
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    keep()
    return {priority: sorted(names) for priority, names in found.items() if names}


PRIORITY_CHOICES = ("required", "important", "standard")


def _warned_priorities():
    """Which of Debian's priorities the doctor warns about: the administrator's setting, important and above."""
    from irate_box.root import recommended

    value = recommended.chosen().get("package-priorities", "important")
    if value not in PRIORITY_CHOICES:
        value = "important"
    order = {"required": 0, "important": 1, "standard": 2}
    return {p for p in PRIORITY_CHOICES if order[p] <= order[value]}


def _missing_priorities():
    """({priority: [missing names]}, None) or (None, why it could not be read)."""
    try:
        have = installed()
    except OSError:
        return None, "dpkg-query failed"
    found = available_priorities(_dumpavail(), _architecture())
    if not found:
        return None, "apt-cache dumpavail named nothing"
    missing = {priority: [n for n in names if n not in have] for priority, names in found.items()}
    return {p: names for p, names in missing.items() if names}, None


def priority_findings():
    """Debian's required, important and standard packages that are not installed, each group said once, with
    the System toolkit named where it carries one."""
    title = M("priorities.title", "Debian's own packages")
    missing, why = _missing_priorities()
    if missing is None:
        return [
            finding(
                "package-priorities",
                title,
                "note",
                M("priorities.unknown", "The box could not read Debian's package priorities: {why}.", why=why),
                notes=[M("priorities.unknown.note", "apt's package lists are needed for this check.")],
            )
        ]
    out = []
    kit = _system_kit()
    warned = _warned_priorities()
    for priority in PRIORITIES:
        names = missing.get(priority)
        if not names:
            continue
        in_kit = [n for n in names if n in kit]
        status = "problem" if priority == "required" else "warn" if priority in warned else "note"
        said = {
            "required": M(
                "priorities.missing.required",
                {
                    "one": "{n} package Debian calls required is not installed.",
                    "other": "{n} packages Debian calls required are not installed.",
                },
                n=len(names),
            ),
            "important": M(
                "priorities.missing.important",
                {
                    "one": "{n} package Debian calls important is not installed.",
                    "other": "{n} packages Debian calls important are not installed.",
                },
                n=len(names),
            ),
            "standard": M(
                "priorities.missing.standard",
                {
                    "one": "{n} package Debian calls standard is not installed.",
                    "other": "{n} packages Debian calls standard are not installed.",
                },
                n=len(names),
            ),
        }[priority]
        out.append(
            finding(
                f"package-priority-{priority}",
                title,
                status,
                said,
                M(
                    "priorities.missing.why",
                    "A minimal image leaves out what it does not need. Some of these are tools an administrator "
                    "expects, and the image's own choice is why they are absent.",
                ),
                (
                    M(
                        "priorities.missing.fix-kit",
                        "Install the System toolkit on Toolkits for {names}. At a shell, install the rest with the "
                        "command here.",
                        names=", ".join(in_kit),
                    )
                    if in_kit
                    else M(
                        "priorities.missing.fix-shell",
                        "At a shell, install the ones you need with the command here.",
                    )
                ),
                notes=(
                    [
                        M(
                            "priorities.missing.setting",
                            "Which of Debian's priorities are warned about is set on the Box doctor's page.",
                        )
                    ]
                    if priority == "standard"
                    else []
                ),
                commands=[f"sudo apt install {' '.join(names[:20])}"],
                evidence=names,
            )
        )
    if not out:
        return [
            finding(
                "package-priorities",
                title,
                "ok",
                M("priorities.ok", "Debian's required, important and standard packages are installed."),
            )
        ]
    return out
