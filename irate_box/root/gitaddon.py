# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Git add-on: the serving of the box's repositories (cgit's web view, git http-backend, behind
fcgiwrap). The repositories themselves are the box's storage and stay without it, as do the
librarian's mirrors, which are kept by the git program and not by cgit.

    sudo ./irate-box gitaddon status
    sudo ./irate-box gitaddon remove [--yes] [--purge]
        stops the servers and removes their units and cgit's configuration; leaves a marker
        (gitrepos.REMOVED) that the hub, the pages, the doctors and install.sh read (an update
        keeps Git out). --purge also removes the packages cgit, fcgiwrap and libjs-highlight.js.
        Git access is set to off, so the web server answers 404 for /git/ and /git-private/.
    sudo ./irate-box gitaddon add-back
        install.sh's half of --with-git: the marker taken away and Git's access on again (the
        installer writes the servers' units and configuration).
    Adding Git back: install.sh --with-git.

Nothing is deleted from the repositories' folder but the marker.
"""

import os
import subprocess
import sys
from pathlib import Path

# Run by hand (irate-box gitaddon …) there is no service's environment: the box's own folders, as install.sh sets them.
os.environ.setdefault("HUB_GIT_ROOT", str(Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "git"))

from irate_box.hub import gitrepos
from irate_box.root import safeio

UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
UNITS = ("irate-box-git.socket", "irate-box-git.service")
PACKAGES = ("cgit", "fcgiwrap", "libjs-highlight.js")
AREAS = ("public", "private")

# What stops working, said before it is done (the same lines as the pages say in place).
STOPS = (
    "Nobody can browse, clone or push to the repositories, so no push builds.",
    "The librarian's mirrors are kept and updated on disk, but nobody can browse or clone them.",
    "The Git tiles go from the home page.",
)
KEPT = "The repositories and the mirrors stay on disk, and are still backed up."
ADD_BACK = "To add Git back, run install.sh again with --with-git."


def run(*cmd, timeout=300):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251


def status():
    """What of the add-on is on the box: {served, units: [present...], cgitrc: [present...]}."""
    return {
        "served": gitrepos.served(),
        "units": [u for u in UNITS if (UNIT_DIR / u).exists()],
        "cgitrc": [a for a in AREAS if (gitrepos.ROOT / f"cgitrc-{a}").exists()],
    }


def remove(purge=False, say=print):
    """Take the serving away. Returns the lines of what was done."""
    done = []
    for unit in UNITS:
        run("systemctl", "disable", "--now", unit)
    for unit in UNITS:
        path = UNIT_DIR / unit
        if path.exists():
            path.unlink()
            done.append(f"removed {path}")
    run("systemctl", "daemon-reload")
    for area in AREAS:
        path = gitrepos.ROOT / f"cgitrc-{area}"
        if path.is_file() and not path.is_symlink():
            path.unlink()
            done.append(f"removed {path}")
    safeio.write(gitrepos.REMOVED, "")
    done.append(f"wrote {gitrepos.REMOVED}")
    # Off: the web server answers 404 for /git/ and /git-private/ and the hub's tiles go with it.
    try:
        from irate_box.root import hub_control

        hub_control.access_set({"app": "git", "mode": "off"})
        done.append("set Git access to off")
    except Exception as exc:  # noqa: BLE001 - the marker already hides it; say why the web server was not told
        done.append(f"could not set Git access to off ({exc}); set it on /admin, Access")
    if purge:
        out = run("apt-get", "remove", "-y", *PACKAGES, timeout=900)
        done.append(
            "removed the packages " + ", ".join(PACKAGES)
            if out.returncode == 0
            else f"apt-get failed: {out.stderr.strip()[-200:]}"
        )
    for line in done:
        say(line)
    return done


def add_back(say=print):
    """The marker taken away, then Git's access on again if the removal turned it off. Returns the lines of what
    was done."""
    done = []
    if gitrepos.REMOVED.exists():
        gitrepos.REMOVED.unlink()
        done.append(f"removed {gitrepos.REMOVED}")
    from irate_box.hub import access
    from irate_box.root import hub_control

    if access.mode_of(access.read(hub_control.ACCESS_FILE), "git") == "off":
        try:
            hub_control.access_set({"app": "git", "mode": access.ROUTED["git"][0]})
            done.append(f"set Git access to {access.ROUTED['git'][0]}")
        except Exception as exc:  # noqa: BLE001 - Git is back; say why its access is still off
            done.append(f"could not set Git access ({exc}); set it on /admin, Access")
    for line in done:
        say(line)
    return done


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    flags = {a for a in argv if a.startswith("--")}
    if args == ["status"] and not flags:
        for key, value in status().items():
            print(f"{key}: {value}")
        return 0
    if args not in (["remove"], ["add-back"]) or flags - {"--yes", "--purge"} or (args == ["add-back"] and flags):
        print("Usage: irate-box gitaddon status | remove [--yes] [--purge] | add-back", file=sys.stderr)
        return 2
    if os.geteuid() != 0:
        print("Run as root.", file=sys.stderr)
        return 1
    if args == ["add-back"]:
        add_back()
        return 0
    if not gitrepos.served():
        print("Git is already removed from this box.")
        return 0
    print("Removing Git stops these:")
    for line in STOPS:
        print(f"  - {line}")
    print(KEPT)
    print(ADD_BACK)
    if "--yes" not in flags and input("Remove Git? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Nothing changed.")
        return 0
    remove(purge="--purge" in flags)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
