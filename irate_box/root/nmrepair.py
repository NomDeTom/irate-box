# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""NetworkManager brought in on an image that does not run it, by the safe route, and put back if the box
is not on a network two minutes later.

Stock Armbian describes the WiFi in netplan and runs it with systemd-networkd. irate-box is built on
NetworkManager (joining a network, a connection's own settings, a roam test, the hotspot on many radios), so
the Box doctor offers NetworkManager as a repair. The install starts nothing while apt runs; the WiFi's
netplan file is given `renderer: NetworkManager`, netplan is applied, and the profile is handed over to
NetworkManager when that works. A transient timer checks two minutes later: a box that is on a WiFi network
keeps the change, and a box that is not is put back exactly as it was.

Record: /etc/hub/nm-install.json (root's) holds the netplan files as they were and, while the change waits,
the timer's phase. The file can hold a WiFi password, so it is root's alone.
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from irate_box.msg import M, Refused, render, sentences
from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RECORD = ETC / "nm-install.json"
NETPLAN = Path(os.environ.get("HUB_NETPLAN_DIR", "/etc/netplan"))
POLICY_RC = Path(os.environ.get("HUB_POLICY_RC", "/usr/sbin/policy-rc.d"))
CODE = Path(os.environ.get("HUB_CODE_DIR", Path(__file__).resolve().parents[2]))
CHECK_UNIT = "irate-box-nm-check"
CHECK_SECONDS = 120
WIFI_FILE_RE = re.compile(r"^\s*(wifis|access-points):", re.M)


def run(cmd, timeout=1800):
    return subprocess.run(  # noqa: TID251
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
        env=dict(os.environ, DEBIAN_FRONTEND="noninteractive"),
    )


def _load():
    try:
        rec = json.loads(RECORD.read_text())
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(rec):
    safeio.write(RECORD, json.dumps(rec, indent=1), mode=0o600)


def _installed(run):
    out = run(["dpkg-query", "-W", "-f", "${Status}", "network-manager"])
    return out.stdout.strip() == "install ok installed"


def _wifi_file():
    """The netplan file that describes the WiFi, or None."""
    for path in sorted(NETPLAN.glob("*.yaml")):
        try:
            if WIFI_FILE_RE.search(path.read_text()):
                return path
        except OSError:
            continue
    return None


def _renderer(text):
    """The netplan file with `renderer: NetworkManager` under `network:`. None when it has no network block."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not re.match(r"^network:\s*$", line):
            continue
        # The renderer belongs to the network block, at two spaces. An older line there is replaced.
        j = i + 1
        while j < len(lines) and (not lines[j].strip() or lines[j].startswith((" ", "\t"))):
            if re.match(r"^\s+renderer:\s*", lines[j]):
                indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
                lines[j] = f"{indent}renderer: NetworkManager"
                return "\n".join(lines) + "\n"
            j += 1
        lines.insert(i + 1, "  renderer: NetworkManager")
        return "\n".join(lines) + "\n"
    return None


def _nm_wifi(run):
    """Whether NetworkManager has a WiFi device connected: the box is back on a network through it."""
    out = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])
    if out.returncode != 0:
        return False
    for line in out.stdout.splitlines():
        parts = line.rsplit(":", 2)
        if len(parts) == 3 and parts[1] in ("wifi", "802-11-wireless") and parts[2] == "connected":
            return True
    return False


def _snapshot():
    files = {}
    for path in sorted(NETPLAN.glob("*.yaml")):
        try:
            files[path.name] = path.read_text()
        except OSError:
            continue
    return files


def _restore(rec, run):
    """Every netplan file as it was, and the network service back. NetworkManager is left installed, stopped."""
    for name, text in (rec.get("files") or {}).items():
        path = NETPLAN / name
        safeio.write(path, text, mode=0o600)
    if not rec.get("installed_before", True):
        run(["systemctl", "disable", "--now", "NetworkManager"])
    run(["netplan", "generate"], timeout=120)
    out = run(["netplan", "apply"], timeout=180)
    return out.returncode == 0


def _restore_policy(rec):
    """The policy-rc.d that was there before the install: put back, or taken away when there was none."""
    saved = rec.get("policy")
    if saved is None:
        POLICY_RC.unlink(missing_ok=True)
    else:
        POLICY_RC.write_bytes(base64.b64decode(saved))
        os.chmod(POLICY_RC, 0o755)


def _cancel_check(run):
    run(["systemctl", "stop", f"{CHECK_UNIT}.timer"], timeout=30)
    run(["systemctl", "reset-failed", f"{CHECK_UNIT}.timer", f"{CHECK_UNIT}.service"], timeout=30)


def install(run=run):
    """Install NetworkManager, give it the WiFi in netplan, and hand the profile over. A check two minutes
    later keeps the change when a WiFi device is connected, and puts everything back when none is."""
    rec = _load()
    if rec.get("phase") == "checking":
        raise Refused(
            M("nm-install.waiting", "NetworkManager was just installed. The box keeps or undoes it in two minutes.")
        )
    if rec.get("phase") == "installing":
        # A run that stopped before it changed the WiFi left the policy file behind: put it back and go on.
        _restore_policy(rec)
    if _nm_wifi(run):
        return M("nm-install.already", "NetworkManager already runs a WiFi connection on this box.")
    wifi = _wifi_file()
    if wifi is None:
        raise Refused(
            M(
                "nm-install.no-netplan",
                "The box has no netplan WiFi file to hand over, so NetworkManager cannot take the WiFi safely.",
            )
        )
    installed_before = _installed(run)
    saved_policy = POLICY_RC.read_bytes() if POLICY_RC.exists() else None
    rec = {
        "at": time.time(),
        "phase": "installing",
        "file": wifi.name,
        "files": _snapshot(),
        "installed_before": installed_before,
        "policy": base64.b64encode(saved_policy).decode() if saved_policy is not None else None,
    }
    _save(rec)
    POLICY_RC.write_text("#!/bin/sh\n# irate-box: NetworkManager is being installed without starting.\nexit 101\n")
    os.chmod(POLICY_RC, 0o755)  # policy-rc.d is a script dpkg runs. # nosec B103
    try:
        out = run(["apt-get", "update", "-q"], timeout=900)
        if out.returncode != 0:
            detail = (out.stderr or out.stdout).strip().splitlines()[-1:] or ["apt-get update failed"]
            raise Refused(
                M(
                    "nm-install.apt-update",
                    "The box could not update its package lists: {detail}",
                    detail=detail[0][:200],
                )
            )
        out = run(["apt-get", "install", "-y", "-q", "--no-install-recommends", "network-manager"], timeout=1800)
        if out.returncode != 0:
            detail = (out.stderr or out.stdout).strip().splitlines()[-1:] or ["apt-get install failed"]
            raise Refused(
                M("nm-install.apt", "The box could not install NetworkManager: {detail}", detail=detail[0][:200])
            )
    except Exception:
        _restore_policy(rec)
        RECORD.unlink(missing_ok=True)
        raise
    _restore_policy(rec)

    changed = _renderer((rec["files"] or {}).get(wifi.name, ""))
    if changed is None:
        _restore(rec, run)
        RECORD.unlink(missing_ok=True)
        raise Refused(M("nm-install.no-network", "The netplan file {name} has no network block.", name=wifi.name))
    safeio.write(wifi, changed, mode=0o600)
    out = run(["netplan", "generate"], timeout=120)
    run(["systemctl", "enable", "--now", "NetworkManager"], timeout=180)
    applied = run(["netplan", "apply"], timeout=180)
    if out.returncode != 0 or applied.returncode != 0:
        _restore(rec, run)
        RECORD.unlink(missing_ok=True)
        detail = (out.stderr or applied.stderr or "").strip().splitlines()[-1:] or ["netplan failed"]
        raise Refused(
            M("nm-install.apply", "The WiFi could not change to NetworkManager: {detail}", detail=detail[0][:200])
        )

    handed = None
    try:
        from irate_box.root import nmhandover

        handed = nmhandover.handover(wifi.name)
    except Exception:  # noqa: BLE001 - the netplan renderer already works; the handover is the extra step
        handed = None
    rec.update(phase="checking", handover=wifi.name if handed else None)
    _save(rec)
    run(
        [
            "systemd-run",
            "--quiet",
            "--collect",
            f"--on-active={CHECK_SECONDS}",
            f"--unit={CHECK_UNIT}",
            str(CODE / "irate-box"),
            "nmrepair",
            "check",
        ],
        timeout=60,
    )
    return sentences(
        M(
            "nm-install.done",
            "NetworkManager is installed and now runs the WiFi. The box keeps the change if a WiFi connection is up in two minutes. Otherwise it puts everything back.",
        ),
        handed,
    )


def check(run=run):
    """The two-minute check: keep the change if a WiFi device is connected, else put everything back."""
    rec = _load()
    if rec.get("phase") != "checking":
        return "There is no NetworkManager change waiting."
    _cancel_check(run)
    if _nm_wifi(run):
        rec.update(phase="kept", checked=time.time())
        _save(rec)
        return "The box is on a WiFi connection through NetworkManager. The change stays."
    revert(run=run)
    return "The box was not on a WiFi connection, so it restored the earlier network settings."


def revert(run=run):
    """Put back the netplan files as they were, and the earlier network service."""
    rec = _load()
    if not rec:
        raise Refused(M("nm-install.undo.none", "The box did not change the WiFi to NetworkManager."))
    _cancel_check(run)
    if rec.get("handover"):
        try:
            from irate_box.root import nmhandover

            nmhandover.undo(rec["handover"])
        except Exception:  # noqa: BLE001 - the netplan files below are the authoritative copy
            pass
    ok = _restore(rec, run)
    RECORD.unlink(missing_ok=True)
    if not ok:
        raise Refused(
            M(
                "nm-install.restore.failed",
                "The box could not apply the earlier network settings. Its files were put back. Reboot it to use them.",
            )
        )
    return M(
        "nm-install.restored",
        "The box is back on the earlier network settings. NetworkManager stays installed, stopped.",
    )


if __name__ == "__main__":
    import sys

    if os.geteuid() != 0:
        sys.exit("run as root")
    argv = sys.argv[1:]
    try:
        if argv[:1] == ["check"]:
            print(check())
        elif argv[:1] == ["revert"]:
            print(render(revert()))
        elif argv[:1] == ["renderer-check"]:
            print(_renderer(sys.stdin.read()) or "")
        else:
            print(render(install()))
    except Refused as exc:
        sys.exit(render(exc.message))
