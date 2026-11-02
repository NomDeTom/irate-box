# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""NetworkManager brought in on an image that does not run it, by the safe route, and put back if the box
is not on a network two minutes later.

Stock Armbian describes the WiFi in netplan and runs it with systemd-networkd. irate-box is built on
NetworkManager (joining a network, a connection's own settings, a roam test, the hotspot on many radios), so
the Box doctor offers NetworkManager as a repair. The install starts nothing while apt runs; the WiFi's
netplan file is given `renderer: NetworkManager`, netplan is applied, and the profile is handed over to
NetworkManager when that works. A transient timer checks two minutes later, and a unit enabled for the same
period checks at the next boot too: a box that is on its WiFi connection keeps the change, and a box that is
not is put back exactly as it was. Once kept, the record drops the netplan copy, which can hold the WiFi
password.
"""

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
CODE = Path(os.environ.get("HUB_CODE_DIR", Path(__file__).resolve().parents[2]))
UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
CHECK_SERVICE = "irate-box-nm-check.service"
CHECK_TIMER = "irate-box-nm-check-timer"
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


def _wifi_device(text):
    """The interface a netplan WiFi file describes: its first key under wifis."""
    base = None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if base is None:
            if re.match(r"^\s*wifis:\s*$", line):
                base = indent
            continue
        if indent <= base:
            break
        m = re.match(r"^\s*([A-Za-z0-9_.:-]+):\s*$", line)
        if m:
            return m.group(1)
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


def _nm_wifi(run, device=None):
    """Whether NetworkManager runs the handed-over WiFi interface, with an address and a default route.
    Any connected wifi device is not enough: an access-point connection also counts as connected."""
    out = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device", "status"])
    if out.returncode != 0:
        return False
    wifi = []
    for line in out.stdout.splitlines():
        parts = line.rsplit(":", 2)
        if len(parts) == 3 and parts[1] in ("wifi", "802-11-wireless") and parts[2] == "connected":
            wifi.append(parts[0])
    for iface in wifi:
        if device is not None and iface != device:
            continue
        if run(["ip", "-o", "-4", "addr", "show", "dev", iface]).stdout.strip() == "":
            continue
        if "via " in run(["ip", "-4", "route", "show", "default", "dev", iface]).stdout:
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


def _boot_unit_text():
    return (
        "[Unit]\n"
        "Description=Irate-Box: finish the NetworkManager change after a reboot\n"
        "After=NetworkManager.service NetworkManager-wait-online.service network-online.target\n"
        "Wants=NetworkManager-wait-online.service network-online.target\n"
        f"ConditionPathExists={RECORD}\n"
        "\n[Service]\n"
        "Type=oneshot\n"
        "TimeoutStartSec=180\n"
        f"ExecStart={CODE}/irate-box nmrepair check boot\n"
        "\n[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _arm_boot_check(run):
    """The check runs at the next boot too, so a box that is power-cycled off its network is put back."""
    path = UNIT_DIR / CHECK_SERVICE
    text = _boot_unit_text()
    try:
        if path.read_text() == text:
            return
    except OSError:
        pass
    safeio.write(path, text, mode=0o644)
    run(["systemctl", "daemon-reload"], timeout=60)
    run(["systemctl", "enable", CHECK_SERVICE], timeout=60)


def _disarm_boot_check(run):
    # disable, not disable --now: from the boot unit itself, stopping the unit would stop this check.
    run(["systemctl", "disable", CHECK_SERVICE], timeout=60)
    (UNIT_DIR / CHECK_SERVICE).unlink(missing_ok=True)
    run(["systemctl", "daemon-reload"], timeout=60)


def _cancel_check(run):
    run(["systemctl", "stop", f"{CHECK_TIMER}.timer"], timeout=30)
    run(["systemctl", "reset-failed", f"{CHECK_TIMER}.timer", f"{CHECK_TIMER}.service"], timeout=30)


def install(run=run):
    """Install NetworkManager, give it the WiFi in netplan, and hand the profile over. A check two minutes
    later, or at the next boot, keeps the change when the WiFi interface is connected with an address and a
    default route, and puts everything back when it is not."""
    rec = _load()
    if rec.get("phase") == "checking":
        raise Refused(
            M("nm-install.waiting", "NetworkManager was just installed. The box keeps or undoes it in two minutes.")
        )
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
    original = (rec.get("files") or {}).get(wifi.name) or wifi.read_text()
    rec = {
        "at": time.time(),
        "phase": "installing",
        "file": wifi.name,
        "device": _wifi_device(original),
        "files": _snapshot(),
        "installed_before": installed_before,
    }
    _save(rec)
    # Masked at run time only: a package script's systemctl start does nothing, and a reboot clears it.
    masked = run(["systemctl", "mask", "--runtime", "NetworkManager"], timeout=120).returncode == 0
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
        if masked:
            run(["systemctl", "unmask", "--runtime", "NetworkManager"], timeout=120)
        RECORD.unlink(missing_ok=True)
        raise
    if masked:
        run(["systemctl", "unmask", "--runtime", "NetworkManager"], timeout=120)

    changed = _renderer(original)
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
    _arm_boot_check(run)
    run(
        [
            "systemd-run",
            "--quiet",
            "--collect",
            f"--on-active={CHECK_SECONDS}",
            f"--unit={CHECK_TIMER}",
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


def check(run=run, grace=0, sleep=time.sleep):
    """Keep the change if the WiFi is connected, else put everything back. At boot, grace gives a slow
    join the same two minutes the install's own timer allows before the box is put back."""
    rec = _load()
    if rec.get("phase") != "checking":
        _disarm_boot_check(run)  # a stale unit from a kept record cleans itself up
        return "There is no NetworkManager change waiting."
    _cancel_check(run)
    found = _nm_wifi(run, rec.get("device"))
    waited = 0
    while not found and waited < grace:
        step = min(5, grace - waited)
        sleep(step)
        waited += step
        found = _nm_wifi(run, rec.get("device"))
    if found:
        rec.update(phase="kept", checked=time.time())
        rec.pop("files", None)  # the netplan copy can hold a WiFi password; it is not needed once kept
        _save(rec)
        _disarm_boot_check(run)
        return "The box is on the WiFi connection through NetworkManager. The change stays."
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
        except Exception:  # noqa: BLE001 - the netplan files below are the authoritative copy # nosec B110
            pass
    ok = _restore(rec, run)
    RECORD.unlink(missing_ok=True)
    _disarm_boot_check(run)
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
            print(check(grace=120 if argv[1:2] == ["boot"] else 0))
        elif argv[:1] == ["revert"]:
            print(render(revert()))
        elif argv[:1] == ["renderer-check"]:
            print(_renderer(sys.stdin.read()) or "")
        else:
            print(render(install()))
    except Refused as exc:
        sys.exit(render(exc.message))
