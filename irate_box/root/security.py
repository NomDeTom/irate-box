# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security page's root side: what the box exposes, and the fixes the owner can choose.

hub_control.py runs this as root for /admin's Security page ("security-scan", "security-fix").
A scan looks at what a guest on the network can reach and how the box is set up -- every
listener not on loopback and the service that owns it, SSH's root and password logins, and
the security updates waiting -- and writes it all to control/security.json for the hub to show.

Nothing here changes the system unless the owner asks (the guiding principle: report, then
offer). Every fix is a drop-in file or a unit switched off, recorded in
/etc/hub/security-changes.json with how to undo it, so the page offers "Undo" and uninstall.sh
puts back what it found. Stdlib only.
"""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RECORD = ETC / "security-changes.json"
SSHD_DROPIN = Path(os.environ.get("HUB_SSHD_DROPIN", "/etc/ssh/sshd_config.d/01-irate-box.conf"))
RESOLVED_DROPIN = Path(os.environ.get("HUB_RESOLVED_DROPIN", "/etc/systemd/resolved.conf.d/irate-box.conf"))
UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
SYSCTL_DROPIN = Path(os.environ.get("HUB_SYSCTL_DROPIN", "/etc/sysctl.d/60-irate-box.conf"))
PROC_SYS = Path(os.environ.get("HUB_PROC_SYS", "/proc/sys"))
GROUP_FILE = Path(os.environ.get("HUB_GROUP_FILE", "/etc/group"))
PASSWD_FILE = Path(os.environ.get("HUB_PASSWD_FILE", "/etc/passwd"))
# Armbian's first-login marker: present until someone logs in as root at the console and answers its
# setup (a root password, a user). Left on a box set up some other way, its image's defaults stand.
FIRSTRUN = Path(os.environ.get("HUB_ARMBIAN_FIRSTRUN", "/root/.not_logged_in_yet"))
UPDATES_LOG_NAME = "security-updates.log"

# Listeners the hub knows by port, when the owning process does not say enough by itself.
KNOWN_PORTS = {
    ("tcp", 22): "SSH", ("tcp", 80): "the hub (web server)", ("tcp", 9090): "Cockpit",
    ("tcp", 1883): "MQTT broker (mosquitto)", ("tcp", 6667): "IRC server (ngIRCd)",
    ("tcp", 22000): "Syncthing", ("udp", 22000): "Syncthing",
    ("udp", 21027): "Syncthing discovery", ("udp", 5353): "mDNS (Avahi or resolved)",
    ("tcp", 5355): "LLMNR (systemd-resolved)", ("udp", 5355): "LLMNR (systemd-resolved)",
    ("udp", 41641): "Tailscale", ("udp", 53): "DNS", ("tcp", 53): "DNS", ("udp", 67): "DHCP",
}
# The hub's own listeners, there on purpose: reported, not offered for closing here.
OURS = {("tcp", 80), ("tcp", 1883), ("tcp", 6667), ("tcp", 22000), ("udp", 22000), ("udp", 21027)}
# Units of the hub and its add-ons: whatever they listen on is theirs (Syncthing, for one,
# also opens random UDP ports for its connections).
OUR_UNITS = re.compile(r"^(nginx|caddy|irate-box.*|kiwix|silverbullet|ttyd|mosquitto|ngircd|excalidraw-room|syncthing@.*)\.service$")
# The hub's front door: nginx, or Caddy, the fallback (install.sh --web).
WEB_UNITS = {"nginx.service": "nginx", "caddy.service": "Caddy"}
# The box's own network clients: they answer only the network's DHCP server.
CLIENT_PORTS = {("udp", 68): "DHCP client", ("udp", 546): "DHCPv6 client"}
# Units the page never offers to stop: the box would be unreachable or the hub would break.
PROTECTED = re.compile(r"^(ssh|sshd|systemd-.*|dbus|NetworkManager|wpa_supplicant|nginx|caddy|"
                       r"irate-box.*|tailscaled|mosquitto|ngircd|syncthing@.*|kiwix|init)\.(service|socket)$")


def run(*cmd, timeout=60):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _have(cmd):
    return shutil.which(cmd) is not None


def _finding(fid, title, status, detail, fix="", actions=()):
    """One line on the page: status ok | warn | problem; actions are {choice, label, confirm?}."""
    return {"id": fid, "title": title, "status": status, "detail": detail, "fix": fix,
            "actions": list(actions)}


def load_record():
    try:
        return json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {}


def save_record(rec):
    ETC.mkdir(parents=True, exist_ok=True)
    tmp = RECORD.with_name(RECORD.name + ".tmp")
    tmp.write_text(json.dumps(rec, indent=2))
    os.chmod(tmp, 0o600)
    os.replace(tmp, RECORD)


# --- listeners ----------------------------------------------------------------------

USERS_RE = re.compile(r'\("([^"]+)",pid=(\d+)')


def _unit_of(pid):
    """The systemd unit a process runs in, from its cgroup, or None."""
    try:
        for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines():
            m = re.search(r"/([^/]+\.(?:service|scope))$", line)
            if m:
                return m.group(1)
    except OSError:
        pass
    return None


def _socket_units():
    """{(port): socket unit} for systemd's own listening sockets (socket activation: the
    listener belongs to pid 1 until the service starts, as Cockpit's does)."""
    out = run("systemctl", "list-sockets", "--no-legend", "--no-pager", "--full")
    units = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            m = re.search(r":(\d+)$", parts[0])
            if m:
                units[int(m.group(1))] = parts[1]
    return units


def _is_loopback(addr):
    return addr.startswith("127.") or addr in ("::1", "[::1]") or addr.startswith("[::ffff:127.")


def parse_ss(text):
    """`ss -H -ltnup` lines -> [{proto, addr, port, process, pid}] for non-loopback listeners."""
    found = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0]
        local = parts[4]
        addr, _, port = local.rpartition(":")
        if not port.isdigit():
            continue
        addr, _, device = addr.partition("%")  # 127.0.0.53%lo: bound to one device
        if _is_loopback(addr) or device == "lo":
            continue
        m = USERS_RE.search(line)
        found.append({"proto": "udp" if proto.startswith("udp") else "tcp", "addr": addr or "*",
                      "port": int(port), "process": m.group(1) if m else None,
                      "pid": int(m.group(2)) if m else None})
    # One line per (proto, port): IPv4 and IPv6 sockets of the same service read as one.
    merged = {}
    for f in found:
        key = (f["proto"], f["port"])
        if key in merged:
            merged[key]["addr"] += f", {f['addr']}"
        else:
            merged[key] = f
    return sorted(merged.values(), key=lambda f: (f["port"], f["proto"]))


def listeners():
    out = run("ss", "-H", "-ltnup")
    found = parse_ss(out.stdout)
    sockets = None
    for f in found:
        unit = _unit_of(f["pid"]) if f["pid"] else None
        if f["process"] == "systemd" or unit in (None, "init.scope"):
            sockets = sockets if sockets is not None else _socket_units()
            unit = sockets.get(f["port"], unit)
        f["unit"] = unit if unit and unit != "init.scope" else None
        f["name"] = KNOWN_PORTS.get((f["proto"], f["port"])) or f["process"] or "unknown"
    return found


def listener_findings(found, rec):
    out = []
    llmnr_done = False
    for f in found:
        key = (f["proto"], f["port"])
        fid = f"port-{f['proto']}-{f['port']}"
        where = f"{f['proto'].upper()} {f['port']} on {f['addr']}" + (f" ({f['unit']})" if f["unit"] else "")
        if f["unit"] in WEB_UNITS and f["proto"] == "tcp":
            out.append(_finding(fid, f"the hub ({WEB_UNITS[f['unit']]})", "ok",
                                f"{where}. The hub's own front door; it is meant to be reachable."))
        elif key in CLIENT_PORTS:
            out.append(_finding(fid, CLIENT_PORTS[key], "ok",
                                f"{where}. The box asking the network for its address; it only answers the network's DHCP server."))
        elif key not in OURS and f["unit"] and OUR_UNITS.match(f["unit"]):
            out.append(_finding(fid, f["name"], "ok", f"{where}. Part of the hub's {f['unit'].split('.')[0].split('@')[0]}."))
        elif key in OURS:
            note = {("tcp", 1883): "Meshtastic nodes publish here; anyone on the network can too (anonymous, "
                                   "limited to msh/#)."}.get(key, "The hub's own; it is meant to be reachable.")
            out.append(_finding(fid, f["name"], "warn" if key == ("tcp", 1883) else "ok", f"{where}. {note}"))
        elif f["port"] == 9090 and f["unit"] and f["unit"].startswith("cockpit"):
            out.append(_finding(fid, "Cockpit", "problem",
                                f"{where}. The image's full Linux web console: a root-capable login page that "
                                "every guest on the network can reach.",
                                "Nothing on the hub needs it. Keep it for this box only (loopback), or switch it off.",
                                [{"choice": "cockpit-loopback", "label": "Loopback only"},
                                 {"choice": "cockpit-off", "label": "Switch off", "confirm": "Switch Cockpit off? Undo brings it back."}]))
        elif f["port"] == 22 and f["proto"] == "tcp":
            continue  # reported with SSH's own settings below
        elif f["port"] == 5355:
            if llmnr_done:
                continue  # TCP and UDP: one line, one switch
            llmnr_done = True
            out.append(_finding(fid, f["name"], "warn",
                                f"{where}. Answers name lookups from anyone on the local network; the hub does not use it.",
                                "Turning LLMNR off in systemd-resolved closes it; normal DNS is unaffected.",
                                [{"choice": "llmnr-off", "label": "Turn LLMNR off"}]))
        elif f["port"] in (41641,) or (f["unit"] or "").startswith("tailscaled"):
            out.append(_finding(fid, "Tailscale", "ok", f"{where}. Encrypted (WireGuard); switched on and off under Access."))
        else:
            unit = f["unit"]
            stoppable = bool(unit) and not PROTECTED.match(unit)
            out.append(_finding(fid, f["name"], "warn",
                                f"{where}. Not part of the hub. If nothing on the box needs it, it is one more thing a guest can try.",
                                "" if stoppable else "Stop it from a shell if it is not needed.",
                                [{"choice": f"unit-off:{unit}", "label": f"Stop and disable {unit}",
                                  "confirm": f"Stop {unit} and keep it from starting at boot? Undo starts it again."}]
                                if stoppable else []))
    for unit, change in rec.get("units", {}).items():
        out.append(_finding(f"unit-{unit}", unit, "ok",
                            f"Switched off {change.get('reason', 'from this page')} ({change.get('at', '')}).",
                            "", [{"choice": f"unit-undo:{unit}", "label": "Undo"}]))
    if "cockpit" in rec:
        out.append(_finding("cockpit-change", "Cockpit", "ok",
                            "Loopback only, from this page." if rec["cockpit"]["mode"] == "loopback"
                            else "Switched off from this page.", "",
                            [{"choice": "cockpit-undo", "label": "Undo"}]))
    if rec.get("llmnr"):
        out.append(_finding("llmnr-change", "LLMNR", "ok", "Turned off from this page.", "",
                            [{"choice": "llmnr-undo", "label": "Undo"}]))
    return out


# --- SSH ------------------------------------------------------------------------------

def sshd_settings():
    """sshd's effective settings (sshd -T), or None if there is no sshd."""
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    if not os.path.exists(sshd):
        return None
    out = run(sshd, "-T")
    if out.returncode != 0:
        return None
    return dict(line.split(None, 1) for line in out.stdout.splitlines() if " " in line)


def _login_users():
    """(name, home) for root and every account with a login shell and uid >= 1000."""
    users = []
    try:
        for line in PASSWD_FILE.read_text().splitlines():
            name, _, uid, _, _, home, shell = line.split(":")
            if (uid == "0" or int(uid) >= 1000) and not shell.endswith(("nologin", "false")):
                users.append((name, home))
    except (OSError, ValueError):
        pass
    return users


def _authorized_keys_files(name, home):
    """Where sshd would look for this account's keys (sshd -T -C user=…: AuthorizedKeysFile, a
    Match block's included), %h, %u and %% expanded, a relative path under the home; OpenSSH's
    default when sshd will not say (stance review 2026-10-08, the gates' item 4)."""
    files = [".ssh/authorized_keys", ".ssh/authorized_keys2"]
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    if os.path.exists(sshd):
        out = run(sshd, "-T", "-C", f"user={name},host=localhost,addr=127.0.0.1")
        for line in out.stdout.splitlines() if out.returncode == 0 else []:
            if line.startswith("authorizedkeysfile ") and len(line.split()) > 1:
                files = line.split()[1:]
    expanded = []
    for f in files:
        f = f.replace("%%", "\0").replace("%h", home).replace("%u", name).replace("\0", "%")
        expanded.append(Path(f) if f.startswith("/") else Path(home, f))
    return expanded


def keys_on_box():
    """Accounts that have at least one key where sshd looks for them."""
    have = []
    for name, home in _login_users():
        for path in _authorized_keys_files(name, home):
            try:
                lines = path.read_text().splitlines()
            except OSError:
                continue
            if any(line.strip() and not line.lstrip().startswith("#") for line in lines):
                have.append(name)
                break
    return have


def ssh_findings(settings, keys, rec):
    if settings is None:
        return [_finding("ssh", "SSH", "ok", "No SSH server on this box.")]
    out = []
    ours = rec.get("ssh", {})
    root = settings.get("permitrootlogin", "yes")
    if root == "yes":
        out.append(_finding("ssh-root", "SSH root login", "problem",
                            "Anyone on the network can try passwords for root, and root is the whole box.",
                            "Log in as an ordinary user and use sudo instead.",
                            [{"choice": "ssh-root-off", "label": "Turn root login off",
                              "confirm": "Turn off SSH logins as root? Make sure you can log in as another user with sudo first."}]))
    else:
        out.append(_finding("ssh-root", "SSH root login", "ok", f"PermitRootLogin {root}.", "",
                            [{"choice": "ssh-root-undo", "label": "Undo"}] if "root" in ours else []))
    pw = settings.get("passwordauthentication", "yes")
    if pw == "yes":
        if keys:
            out.append(_finding("ssh-password", "SSH password login", "warn",
                                f"Passwords are accepted, so they can be guessed. Keys are set up for: {', '.join(keys)}.",
                                "With a key on the box, password logins can be turned off.",
                                [{"choice": "ssh-password-off", "label": "Turn password login off",
                                  "confirm": f"Turn off SSH password logins? Only keys will work, for: {', '.join(keys)}."}]))
        else:
            out.append(_finding("ssh-password", "SSH password login", "warn",
                                "Passwords are accepted, and no account has a key yet, so turning them off would lock everyone out.",
                                "Add your public key to ~/.ssh/authorized_keys on the box first (ssh-copy-id); then this page offers to turn passwords off."))
    else:
        out.append(_finding("ssh-password", "SSH password login", "ok", "Keys only.", "",
                            [{"choice": "ssh-password-undo", "label": "Undo"}] if "password" in ours else []))
    # Forwarding (stance review 2026-10-08, I6): Debian's and Armbian's defaults leave TCP, agent
    # and X11 forwarding on. TCP forwarding hands anyone with a login a proxy from the hotspot
    # into the box's other network, which is the separation the hotspot design rests on.
    fwd = [n for n, k in (("TCP", "allowtcpforwarding"), ("agent", "allowagentforwarding"), ("X11", "x11forwarding"))
           if settings.get(k, "yes") == "yes"]
    if fwd:
        out.append(_finding("ssh-forwarding", "SSH forwarding", "warn",
                            f"{', '.join(fwd)} forwarding on: a login here is also a tunnel through the box, from the hotspot to its other "
                            "network, and to an agent or display on the machine that logged in.",
                            "Off unless you use it (an SSH tunnel to the box's network, from afar through Tailscale, needs TCP forwarding).",
                            [{"choice": "ssh-forwarding-off", "label": "Turn forwarding off",
                              "confirm": "Turn off SSH TCP, agent and X11 forwarding? Plain logins and scp still work; tunnels through the box don't."}]))
    else:
        out.append(_finding("ssh-forwarding", "SSH forwarding", "ok", "TCP, agent and X11 forwarding off.", "",
                            [{"choice": "ssh-forwarding-undo", "label": "Undo"}] if "forwarding" in ours else []))
    return out


# --- security updates ---------------------------------------------------------------------

INST_RE = re.compile(r"^Inst (\S+) .*\(([^)]*)\)")


def pending_security(simulated):
    """Package names from `apt-get -s upgrade` output whose new version comes from a security archive."""
    pkgs = []
    for line in simulated.splitlines():
        m = INST_RE.match(line)
        if m and "security" in m.group(2).lower():
            pkgs.append(m.group(1))
    return pkgs


UNATTENDED_LOG = Path(os.environ.get("HUB_UNATTENDED_LOG", "/var/log/unattended-upgrades/unattended-upgrades.log"))


def _apt_periodic():
    """{key: value} of APT::Periodic::Enable and ::Unattended-Upgrade as apt sees them (every
    apt.conf.d file merged), or {} with no apt-config."""
    if not _have("apt-config"):
        return {}
    out = run("apt-config", "dump", "--format", "%f=%v%n", "APT::Periodic::Enable", "APT::Periodic::Unattended-Upgrade")
    return dict(l.split("=", 1) for l in out.stdout.splitlines() if "=" in l)


def unattended_finding(installed, periodic, log_age_days):
    """Automatic security updates, by what would actually run (stance review 2026-10-08, I3):
    Armbian images ship APT::Periodic::Enable "0", which switches the whole of apt's periodic
    work off whatever Unattended-Upgrade says, so the binary being there meant nothing."""
    enabled = installed and periodic.get("APT::Periodic::Enable", "1") != "0" and periodic.get("APT::Periodic::Unattended-Upgrade", "0") not in ("0", "")
    if not installed:
        return _finding("unattended", "Automatic security updates", "warn",
                        "Not set up: security updates wait until someone installs them.",
                        "Planned: unattended-upgrades for Debian-Security updates only. Until then, install them here.")
    if not enabled:
        return _finding("unattended", "Automatic security updates", "warn",
                        "unattended-upgrades is installed but never runs: apt's periodic work is off "
                        f"(APT::Periodic::Enable {periodic.get('APT::Periodic::Enable', 'unset')}, "
                        f"Unattended-Upgrade {periodic.get('APT::Periodic::Unattended-Upgrade', 'unset')}; the image's own setting).",
                        'Set APT::Periodic::Enable "1"; and APT::Periodic::Unattended-Upgrade "1"; in a file of your own in /etc/apt/apt.conf.d.')
    if log_age_days is None:
        return _finding("unattended", "Automatic security updates", "warn", "Set up, but has not run yet (no log).",
                        "It runs daily when the box is online; an offline box must have them installed here.")
    if log_age_days > 14:
        return _finding("unattended", "Automatic security updates", "warn", f"Set up, but last ran {log_age_days:.0f} days ago.",
                        "It runs daily when the box is online; an offline box must have them installed here.")
    return _finding("unattended", "Automatic security updates", "ok", f"Set up and ran {log_age_days:.0f} day(s) ago.")


def update_findings():
    if not _have("apt-get"):
        return [], []
    out = run("apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade", timeout=180)
    pkgs = pending_security(out.stdout)
    try:
        log_age = (time.time() - UNATTENDED_LOG.stat().st_mtime) / 86400
    except OSError:
        log_age = None
    findings = []
    if pkgs:
        findings.append(_finding("security-updates", "Security updates", "problem",
                                 f"{len(pkgs)} waiting: {', '.join(pkgs[:8])}{'…' if len(pkgs) > 8 else ''}. "
                                 "The package lists are as fresh as the last apt-get update.",
                                 "Installing them changes only those packages; the hub's own updates are separate (Updates).",
                                 [{"choice": "security-updates", "label": f"Install {len(pkgs)} security update{'s' if len(pkgs) != 1 else ''}",
                                   "confirm": "Install the waiting security updates now? It can take several minutes on this board."}]))
    else:
        findings.append(_finding("security-updates", "Security updates", "ok",
                                 "None waiting (as of the last apt-get update)."))
    findings.append(unattended_finding(_have("unattended-upgrade"), _apt_periodic(), log_age))
    return findings, pkgs


# --- scan ---------------------------------------------------------------------------------

# --- the kernel's settings, and accounts in root's groups (plan §9 step 4) ----------------------

# Each set is one button. The values are the ones Debian ships in its own kernels; the image's
# vendor kernel leaves them off.
KERNEL = {
    "links": {"title": "Kernel link protections", "status": "problem",
              "keys": {"fs.protected_symlinks": "1", "fs.protected_hardlinks": "1"},
              "why": "Links in shared folders such as /tmp are followed across users, so a program that writes "
                     "there as root can be made to write elsewhere (one way local attacks become root).",
              "label": "Turn link protections on"},
    "info": {"title": "Kernel addresses and log", "status": "warn",
             "keys": {"kernel.kptr_restrict": "1", "kernel.dmesg_restrict": "1"},
             "why": "Any account can read the kernel's log and the addresses of its code, which help an attacker "
                    "aim an exploit.",
             "label": "Hide them from ordinary accounts"},
}


# Debian's own kernel defaults: the link protections and the rest a stock Debian box has (FIFOs and
# regular files in shared folders, source routes refused, the SysRq mask). Armbian builds leave
# recommended packages out, so mPWRD-OS images lack it (the Lyra, 2026-10-07; Armbian fixed it
# upstream on 2026-08-26). Offered before the hub's own file wherever apt can get it.
DEBIAN_SYSCTL = "linux-sysctl-defaults"


def debian_sysctl():
    """'installed', 'available' (apt has a candidate, from the network or its cache), or None."""
    if not _have("dpkg-query") or not _have("apt-cache"):
        return None
    if "install ok installed" in (run("dpkg-query", "-W", "-f=${Status}", DEBIAN_SYSCTL).stdout or ""):
        return "installed"
    m = re.search(r"Candidate:\s*(\S+)", run("apt-cache", "policy", DEBIAN_SYSCTL).stdout or "")
    return "available" if m and m.group(1) != "(none)" else None


def _sysctl_path(key):
    return PROC_SYS / key.replace(".", "/")


def _sysctl(key):
    try:
        return _sysctl_path(key).read_text().strip()
    except OSError:
        return None


def kernel_findings(rec):
    out, ours = [], rec.get("kernel", {})
    for name, k in KERNEL.items():
        now = {key: _sysctl(key) for key in k["keys"]}
        if any(v is None for v in now.values()):
            continue  # not this kernel's to set (or not readable here)
        shown = ", ".join(f"{key.split('.', 1)[1]}={v}" for key, v in now.items())
        if all(now[key] == want for key, want in k["keys"].items()):
            out.append(_finding(f"kernel-{name}", k["title"], "ok", shown + ".", "",
                                [{"choice": f"kernel-{name}-undo", "label": "Undo"}] if name in ours else []))
        elif name == "links" and debian_sysctl() == "available":
            out.append(_finding(f"kernel-{name}", k["title"], k["status"], f"{shown}. {k['why']}",
                                f"Debian's own defaults ({DEBIAN_SYSCTL}) set these and the rest a stock Debian "
                                "box has; or the hub sets just these two (a file in /etc/sysctl.d). Either one now "
                                "and at every boot.",
                                [{"choice": "kernel-links-debian", "label": "Install Debian's defaults",
                                  "confirm": f"Install {DEBIAN_SYSCTL} from Debian and apply it now? It also "
                                             "protects FIFOs and files in shared folders and refuses source routes, "
                                             "as on any Debian box."},
                                 {"choice": f"kernel-{name}-on", "label": "Just these two"}]))
        else:
            out.append(_finding(f"kernel-{name}", k["title"], k["status"], f"{shown}. {k['why']}",
                                "Set them, now and at every boot (a file in /etc/sysctl.d).",
                                [{"choice": f"kernel-{name}-on", "label": k["label"]}]))
    return out


def _write_sysctl_dropin(kernel):
    keys = {key: want for name, e in kernel.items() if not e.get("by") for key, want in KERNEL[name]["keys"].items()}
    if not keys:
        SYSCTL_DROPIN.unlink(missing_ok=True)
        return
    SYSCTL_DROPIN.parent.mkdir(parents=True, exist_ok=True)
    SYSCTL_DROPIN.write_text("# Written by irate-box's Security page (/admin). Undo there, or delete this file.\n"
                             + "".join(f"{key} = {v}\n" for key, v in keys.items()))


def _kernel(rec, name, on):
    if name not in KERNEL:
        raise ValueError(f"{name} is not a kernel setting this page changes")
    kernel = rec.setdefault("kernel", {})
    if on:
        old = kernel.get(name, {}).get("old") or {key: _sysctl(key) for key in KERNEL[name]["keys"]}
        for key, want in KERNEL[name]["keys"].items():
            _sysctl_path(key).write_text(want + "\n")
        kernel[name] = {"old": old, "at": time.strftime("%Y-%m-%d")}
        _write_sysctl_dropin(kernel)
        return f"{KERNEL[name]['title']}: on, now and at every boot"
    entry = kernel.pop(name, None)
    if entry is None:
        raise ValueError(f"{KERNEL[name]['title']} were not changed from this page")
    if entry.get("by") == DEBIAN_SYSCTL:
        out = run("apt-get", "remove", "-y", DEBIAN_SYSCTL, timeout=600)
        if out.returncode != 0:
            kernel[name] = entry
            raise ValueError(f"apt-get remove {DEBIAN_SYSCTL}: {(out.stderr or out.stdout).strip()[-200:]}")
    for key, v in (entry.get("old") or {}).items():
        if v is not None:
            _sysctl_path(key).write_text(v + "\n")
    _write_sysctl_dropin(kernel)
    if not kernel:
        rec.pop("kernel")
    return f"{KERNEL[name]['title']}: back as they were"


def _kernel_debian(rec):
    """Debian's own defaults installed and applied (systemd-sysctl reads every sysctl.d file again)."""
    kernel = rec.setdefault("kernel", {})
    old = kernel.get("links", {}).get("old") or {key: _sysctl(key) for key in KERNEL["links"]["keys"]}
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    out = subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", DEBIAN_SYSCTL],
                         capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL, timeout=600)
    if out.returncode != 0:
        raise ValueError(f"apt-get install {DEBIAN_SYSCTL}: {(out.stderr or out.stdout).strip()[-200:]} "
                         "(Just these two works without it)")
    kernel["links"] = {"old": old, "at": time.strftime("%Y-%m-%d"), "by": DEBIAN_SYSCTL}
    _write_sysctl_dropin(kernel)   # the hub's own file no longer carries these two
    save_record(rec)               # installed: Undo must be possible even if what follows fails
    _systemctl("restart", "systemd-sysctl.service")
    now = {key: _sysctl(key) for key in KERNEL["links"]["keys"]}
    if any(now[key] != want for key, want in KERNEL["links"]["keys"].items()):
        raise ValueError(f"{DEBIAN_SYSCTL} is installed but the link protections are still off "
                         f"({', '.join(f'{k}={v}' for k, v in now.items())}): something later in sysctl.d sets them back")
    return f"{KERNEL['links']['title']}: on, from Debian's own defaults ({DEBIAN_SYSCTL}), now and at every boot"


# Groups that are root by another name: docker runs containers as root with any folder mounted;
# disk reads and writes the raw card, password file and all.
ROOTISH_GROUPS = ("docker", "disk")
NAME_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")


def _login_accounts():
    out = set()
    for line in (PASSWD_FILE.read_text(errors="replace") if PASSWD_FILE.exists() else "").splitlines():
        f = line.split(":")
        if len(f) >= 7 and f[2].isdigit() and int(f[2]) >= 1000 and f[0] != "nobody" \
                and not f[6].endswith(("nologin", "false")):
            out.add(f[0])
    return out


def _group_members():
    out = {}
    for line in (GROUP_FILE.read_text(errors="replace") if GROUP_FILE.exists() else "").splitlines():
        f = line.split(":")
        if len(f) >= 4:
            out[f[0]] = {m for m in f[3].split(",") if m}
    return out


def group_findings(rec):
    out, ours = [], rec.get("groups", {})
    members, logins = _group_members(), _login_accounts()
    for group in ROOTISH_GROUPS:
        if group not in members:
            continue
        risky = sorted(members[group] & logins)
        undo = [{"choice": f"group-undo:{k}", "label": f"Put {k.split('@')[0]} back"} for k in ours if k.endswith("@" + group)]
        if not risky:
            out.append(_finding(f"group-{group}", f"The {group} group", "ok", "No login account is in it.", "", undo))
            continue
        what = ("runs containers as root with any folder mounted" if group == "docker"
                else "reads and writes the raw storage, password file included")
        out.append(_finding(f"group-{group}", f"The {group} group", "warn",
                            f"{', '.join(risky)} {'is' if len(risky) == 1 else 'are'} in it. The {group} group {what}: "
                            "the same as root, without a password.",
                            "Take the account out unless it needs it (it applies from its next login).",
                            [{"choice": f"group-drop:{u}@{group}", "label": f"Take {u} out",
                              "confirm": f"Take {u} out of the {group} group? It applies from {u}'s next login."}
                             for u in risky] + undo))
    return out


def _group(rec, arg, on):
    user, _, group = arg.partition("@")
    if group not in ROOTISH_GROUPS or not NAME_RE.match(user):
        raise ValueError(f"{arg} is not an account and group this page changes")
    groups = rec.setdefault("groups", {})
    if on:
        if user not in _group_members().get(group, set()):
            raise ValueError(f"{user} is not in the {group} group")
        r = run("gpasswd", "-d", user, group)
        if r.returncode:
            raise ValueError(f"gpasswd: {(r.stderr or r.stdout).strip()[:160]}")
        groups[arg] = time.strftime("%Y-%m-%d")
        return f"{user} is out of the {group} group (from its next login)"
    if arg not in groups:
        raise ValueError(f"{user} was not taken out of {group} from this page")
    r = run("gpasswd", "-a", user, group)
    if r.returncode:
        raise ValueError(f"gpasswd: {(r.stderr or r.stdout).strip()[:160]}")
    groups.pop(arg)
    if not groups:
        rec.pop("groups")
    return f"{user} is back in the {group} group"


# --- root's own login (Tom, 2026-10-08: "is root being available as an unlocked user, or with no
# first-run, a security checkpoint?") ------------------------------------------------------------

def _root_password():
    """P (a password), NP (none), L (locked), or None: from `passwd -S`, never the hash itself."""
    if not _have("passwd"):
        return None
    f = (run("passwd", "-S", "root").stdout or "").split()
    return f[1] if len(f) > 1 and f[0] == "root" and f[1] in ("P", "NP", "L") else None


def _sudoers():
    """The login accounts that can use sudo (the sudo or wheel group): who reaches root once it's locked."""
    members = _group_members()
    return sorted((members.get("sudo", set()) | members.get("wheel", set())) & _login_accounts())


def root_findings(rec):
    out, ours = [], rec.get("root", {})
    state, firstrun, admins = _root_password(), FIRSTRUN.exists(), _sudoers()
    lock = [{"choice": "root-lock", "label": "Lock root's password",
             "confirm": f"Lock root's password? Root is then reached only through sudo ({', '.join(admins)}); "
                        "the console's root login stops working."}] if admins else []
    no_lock = "" if admins else " No other account here can use sudo, so it isn't locked from here: add one first."
    undo = [{"choice": "root-lock-undo", "label": "Undo"}] if "lock" in ours else []
    if state == "L":
        out.append(_finding("root-password", "Root's password", "ok", "Locked: root is reached through sudo.", "", undo))
    elif state == "NP":
        out.append(_finding("root-password", "Root's password", "problem",
                            "Root has no password: anyone at the console, or wherever root may log in, is root.",
                            "Lock it, so root is reached only through sudo." + no_lock, lock))
    elif state == "P" and firstrun:
        out.append(_finding("root-password", "Root's password", "problem",
                            "Root still has the image's own password: Armbian's first-login setup never ran, so it's "
                            "the one every image of this kind ships with (usually 1234), and anyone who knows it is root.",
                            "Lock it, so root is reached only through sudo." + no_lock, lock))
    elif state == "P":
        out.append(_finding("root-password", "Root's password", "warn",
                            "Root has a password of its own: fine if you chose it, and know it's strong.",
                            "Or lock it, so root is reached only through sudo." + no_lock, lock))
    if firstrun:
        out.append(_finding("root-firstrun", "First-login setup", "problem",
                            "Armbian's first-login setup never ran (/root/.not_logged_in_yet): the image's defaults "
                            "were never changed, and any root login starts it, asking for a new root password and user.",
                            "This box was set up another way: put the marker aside (kept, to undo).",
                            [{"choice": "firstrun-off", "label": "Put it aside"}]))
    elif "firstrun" in ours:
        out.append(_finding("root-firstrun", "First-login setup", "ok", "Its marker is put aside.", "",
                            [{"choice": "firstrun-undo", "label": "Put it back"}]))
    return out


def _root(rec, what, on):
    root = rec.setdefault("root", {})
    if what == "lock":
        if on:
            if not _sudoers():
                raise ValueError("no other account here can use sudo: locking root would leave nobody who can be root")
            was = _root_password()
            r = run("passwd", "-l", "root")
            if r.returncode:
                raise ValueError(f"passwd -l: {(r.stderr or r.stdout).strip()[:160]}")
            root["lock"] = {"was": was, "at": time.strftime("%Y-%m-%d")}
            msg = "root's password is locked: root is reached through sudo"
        else:
            entry = root.pop("lock", None)
            if entry is None:
                raise ValueError("root's password was not locked from this page")
            if entry.get("was") in ("P", "NP"):
                r = run("passwd", "-u", "root")
                if r.returncode:
                    root["lock"] = entry
                    raise ValueError(f"passwd -u: {(r.stderr or r.stdout).strip()[:160]}")
            msg = "root's password is as it was"
    else:
        kept = ETC / "armbian-firstrun.kept"
        if on:
            if not FIRSTRUN.exists():
                raise ValueError("there is no first-login marker to put aside")
            kept.write_bytes(FIRSTRUN.read_bytes())
            kept.chmod(0o600)
            FIRSTRUN.unlink()
            root["firstrun"] = time.strftime("%Y-%m-%d")
            msg = "the first-login marker is put aside: a root login no longer starts Armbian's setup"
        else:
            if root.pop("firstrun", None) is None or not kept.exists():
                raise ValueError("the first-login marker was not put aside from this page")
            FIRSTRUN.write_bytes(kept.read_bytes())
            kept.unlink()
            msg = "the first-login marker is back"
    if not root:
        rec.pop("root")
    return msg


# --- sudo rules, apt's trust, logs in RAM (stance review 2026-10-08: I1, I4, I7) -----------------
# What the image left that the first review's offers did not reach: a passwordless sudo rule for
# an account sshd lets in by password (one guess is root), repository keys in trusted.gpg.d
# (trusted for every repository), and /var/log on a RAM disk (the evidence goes with the power).
SUDOERS = Path(os.environ.get("HUB_SUDOERS", "/etc/sudoers"))
SUDOERS_DIR = Path(os.environ.get("HUB_SUDOERS_DIR", "/etc/sudoers.d"))
SUDOERS_KEPT = ETC / "sudoers-removed"
APT_DIR = Path(os.environ.get("HUB_APT_DIR", "/etc/apt"))
KEYRINGS = Path(os.environ.get("HUB_KEYRINGS_DIR", "/usr/share/keyrings"))
KEYS_KEPT = ETC / "apt-keys-removed"
RAMLOG_DEFAULT = Path(os.environ.get("HUB_RAMLOG_DEFAULT", "/etc/default/armbian-ramlog"))
JOURNALD_DROPIN = Path(os.environ.get("HUB_JOURNALD_DROPIN", "/etc/systemd/journald.conf.d/irate-box.conf"))
LOG_DIR = os.environ.get("HUB_LOG_DIR", "/var/log")
DEBIAN_KEYS = ("debian-archive-", "debian-ports-archive-")
FILE_RE = re.compile(r"^[A-Za-z0-9@_][A-Za-z0-9@._-]*$")


def _nopasswd_rules():
    """[(file name, rule, account)]: NOPASSWD rules naming a login account, or a group it is in."""
    members, logins = _group_members(), _login_accounts()
    files = ([SUDOERS] if SUDOERS.is_file() else []) + \
        (sorted(p for p in SUDOERS_DIR.iterdir() if p.is_file() and "." not in p.name and not p.name.endswith("~")) if SUDOERS_DIR.is_dir() else [])
    out = []
    for f in files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "NOPASSWD" not in s:
                continue
            who = s.split()[0]
            accounts = (members.get(who[1:], set()) & logins) if who.startswith("%") else ({who} & logins)
            out.extend((f.name, s, a) for a in sorted(accounts))
    return out


def sudo_findings(rec, settings=None):
    ours = rec.get("sudo", {})
    undo = [{"choice": f"sudo-undo:{f}", "label": f"Put {f} back"} for f in ours]
    rules = _nopasswd_rules()
    if not rules:
        return [_finding("sudo-nopasswd", "Passwordless sudo", "ok", "No NOPASSWD rule names a login account.", "", undo)]
    settings = sshd_settings() if settings is None else settings
    guessable = bool(settings) and settings.get("passwordauthentication", "yes") == "yes"
    by_file = {}
    for f, _, a in rules:
        by_file.setdefault(f, set()).add(a)
    shown = "; ".join(f"{f}: {', '.join(sorted(a))}" for f, a in by_file.items())
    drops = [f for f in by_file if f != SUDOERS.name and FILE_RE.match(f)]
    return [_finding("sudo-nopasswd", "Passwordless sudo" + (", and SSH accepts passwords" if guessable else ""),
                     "problem" if guessable else "warn",
                     f"{shown}: root with no password for whoever is that account"
                     + (", and sshd lets anyone on the network try that account's password: one guess is root." if guessable else "."),
                     "Remove the rule once the work it was added for is done"
                     + (", and until then turn SSH password logins off (above)." if guessable else "."),
                     [{"choice": f"sudo-drop:{f}", "label": f"Remove {f}",
                       "confirm": f"Remove /etc/sudoers.d/{f}? sudo then asks the account's password (it stays in the sudo group); Undo puts the file back."}
                      for f in drops] + undo)]


def _sudo(rec, name, on):
    if not FILE_RE.match(name) or name == SUDOERS.name:
        raise ValueError(f"{name} is not a sudoers.d file this page removes")
    ours = rec.setdefault("sudo", {})
    src, kept = SUDOERS_DIR / name, SUDOERS_KEPT / name
    if on:
        if not src.is_file():
            raise ValueError(f"/etc/sudoers.d/{name} is not there")
        SUDOERS_KEPT.mkdir(mode=0o700, exist_ok=True)
        shutil.move(str(src), str(kept))
        ours[name] = time.strftime("%Y-%m-%d")
        return f"/etc/sudoers.d/{name} removed (kept under /etc/hub for Undo)"
    if name not in ours or not kept.is_file():
        raise ValueError(f"{name} was not removed from this page")
    shutil.move(str(kept), str(src))
    os.chmod(src, 0o440)
    ours.pop(name)
    if not ours:
        rec.pop("sudo")
    return f"/etc/sudoers.d/{name} is back"


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _key_for(stem, keys):
    """The trusted.gpg.d key whose name is the source's (OBS writes home_mPWRD_OS.gpg beside home:mPWRD:OS.list)."""
    return next((k for k in keys if _norm(k.rsplit(".", 1)[0]) == _norm(stem)), None)


def _apt_keys():
    trusted = APT_DIR / "trusted.gpg.d"
    return sorted(p.name for p in trusted.iterdir() if p.is_file() and not p.name.startswith(DEBIAN_KEYS)) if trusted.is_dir() else []


def _unsigned_lists():
    from irate_box.root import secdoctor
    return [(f, uri) for f, uri, _, signed in secdoctor._apt_sources(APT_DIR) if not signed and f.endswith(".list")]


def apt_findings(rec):
    ours = rec.get("apt", {})
    keys, unsigned = _apt_keys(), _unsigned_lists()
    undo = [{"choice": f"apt-undo:{k}", "label": f"Put {k} back"} for k in ours]
    if not keys and not unsigned:
        return [_finding("apt-trust", "Repository keys", "ok", "Every source names its own key; nothing in trusted.gpg.d but Debian's.", "", undo)]
    actions = []
    for f, _ in unsigned:
        k = _key_for(f[:-len(".list")], keys)
        if k and FILE_RE.match(k):
            actions.append({"choice": f"apt-signedby:{k}", "label": f"Tie {k} to {f}",
                            "confirm": f"Move {k} out of trusted.gpg.d into /usr/share/keyrings, and name it in {f} as that source's "
                                       "signed-by key? apt then trusts it for that repository alone. Undo puts both back."})
    return [_finding("apt-trust", "Repository keys trusted for every repository", "warn",
                     (f"Keys in trusted.gpg.d ({', '.join(keys)}) can sign packages for any repository, Debian's included. " if keys else "")
                     + (f"Sources with no key of their own: {', '.join(f for f, _ in unsigned)}." if unsigned else ""),
                     "Each key named in its own source's signed-by. A key no source matches is removed by hand: rm /etc/apt/trusted.gpg.d/<key>.",
                     actions + undo)]


def _apt(rec, key, on):
    if not FILE_RE.match(key):
        raise ValueError(f"{key} is not a key this page moves")
    ours = rec.setdefault("apt", {})
    trusted, ring, kept = APT_DIR / "trusted.gpg.d" / key, KEYRINGS / key, KEYS_KEPT / key
    if on:
        lst = next((APT_DIR / "sources.list.d" / f for f, _ in _unsigned_lists() if _key_for(f[:-len(".list")], [key])), None)
        if not trusted.is_file() or lst is None:
            raise ValueError(f"{key} is not in trusted.gpg.d, or no source of its name lacks a key")
        text = lst.read_text()
        new = re.sub(r"(?m)^(\s*deb(?:-src)?)\s+(?!\[)", rf"\1 [signed-by={ring}] ", text)
        new = re.sub(r"(?m)^(\s*deb(?:-src)?\s+\[)(?![^\]]*signed-by=)", rf"\1signed-by={ring} ", new)
        KEYS_KEPT.mkdir(mode=0o700, exist_ok=True)
        (KEYS_KEPT / f"{key}.list").write_text(text)
        KEYRINGS.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(trusted, ring)
        os.chmod(ring, 0o644)
        lst.write_text(new)
        shutil.move(str(trusted), str(kept))
        ours[key] = {"source": lst.name, "date": time.strftime("%Y-%m-%d")}
        return f"{key} vouches for {lst.name} alone now (apt-get update reads it at the next refresh)"
    if key not in ours or not kept.is_file():
        raise ValueError(f"{key} was not moved from this page")
    lst = APT_DIR / "sources.list.d" / ours[key]["source"]
    saved = KEYS_KEPT / f"{key}.list"
    if saved.is_file():
        lst.write_text(saved.read_text())
        saved.unlink()
    shutil.move(str(kept), str(trusted))
    ring.unlink(missing_ok=True)
    ours.pop(key)
    if not ours:
        rec.pop("apt")
    return f"{key} is back in trusted.gpg.d, and {lst.name} as it was"


def _log_in_ram():
    """True when /var/log is a RAM disk (tmpfs, or Armbian's zram), False when on the card, None unknown."""
    if not _have("findmnt"):
        return None
    out = run("findmnt", "-no", "SOURCE,FSTYPE", LOG_DIR)
    f = out.stdout.split()
    if out.returncode != 0 or len(f) < 2:
        return None
    return f[1] == "tmpfs" or f[0].startswith("/dev/zram")


def log_findings(rec):
    ours = rec.get("logs")
    undo = [{"choice": "logs-undo", "label": "Undo"}] if ours else []
    ram = _log_in_ram()
    if ram is None:
        return [_finding("logs-ram", "Logs on the card", "ok", "Set from this page; it applies from the next boot.", "", undo)] if ours else []
    if not ram:
        return [_finding("logs-ram", "Logs on the card", "ok",
                         "/var/log is on the card: what sshd and the hub log survives a reboot or a power cut." + (" Set from this page." if ours else ""), "", undo)]
    return [_finding("logs-ram", "Logs live in RAM", "warn",
                     "/var/log is a RAM disk (Armbian's ramlog): sshd's log and the journal are trimmed every 15 minutes and gone at a "
                     "power cut, so the evidence of an intrusion goes with them; the doctor's \"reached from the internet\" sees only since the last boot.",
                     "Logs on the card, a few MB a week: Armbian's ramlog off and the journal kept, capped at 32 MB. It applies from the next boot.",
                     [{"choice": "logs-card", "label": "Keep logs on the card",
                       "confirm": "Turn Armbian's ramlog off and keep the journal on the card (32 MB cap)? It applies at the next boot; the card does a little more writing."}]
                     + undo)]


def _logs(rec, on):
    if on:
        was = None
        if RAMLOG_DEFAULT.is_file():
            was = RAMLOG_DEFAULT.read_text()
            new = re.sub(r"(?m)^ENABLED=.*$", "ENABLED=false", was) if re.search(r"(?m)^ENABLED=", was) else was + "\nENABLED=false\n"
            RAMLOG_DEFAULT.write_text(new)
        JOURNALD_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        JOURNALD_DROPIN.write_text("# Written by irate-box's Security page (/admin): logs on the card. Undo there, or delete this file.\n"
                                   "[Journal]\nStorage=persistent\nSystemMaxUse=32M\n")
        rec["logs"] = {"date": time.strftime("%Y-%m-%d"), "ramlog": was}
        return "logs on the card from the next boot (ramlog off, the journal kept, 32 MB cap)"
    if "logs" not in rec:
        raise ValueError("logs were not moved to the card from this page")
    if rec["logs"].get("ramlog") is not None and RAMLOG_DEFAULT.is_file():
        RAMLOG_DEFAULT.write_text(rec["logs"]["ramlog"])
    JOURNALD_DROPIN.unlink(missing_ok=True)
    rec.pop("logs")
    return "logs back in RAM from the next boot, as the image had them"


# --- the network floor (stance review §4 item 3): what a guest on the hotspot can reach -----------

FLOOR_WORDS = {"hub": "the hub's pages, DNS and DHCP", "apps": "the hub, its apps, DNS and DHCP"}


def _floor_apply(floor):
    """The floor as given (None: off) with guests' internet as it is: one ruleset (firewall.apply_all)."""
    from irate_box.root import firewall, share
    fl = dict(floor, services=firewall.services_here(floor.get("services", []))) if floor else None
    firewall.apply_all(fl, share.load())


def firewall_findings(rec):
    from irate_box.root import firewall
    ours = rec.get("firewall")
    loaded = firewall.loaded()
    iface = firewall.hotspot_iface()
    if loaded is None:
        return [_finding("firewall", "What a guest on the hotspot can reach", "warn",
                         "nftables is not installed, so nothing limits what a guest on the hotspot can reach: every listener on the box.",
                         "Update the box (install.sh installs nftables), then switch the floor on here.")]
    wanted = sorted(ours.get("services", [])) if ours else []
    level = (ours or {}).get("level", "apps")
    if not ours or not loaded:
        tcp, udp = firewall.ports(firewall.services_here(["mqtt", "sync"]))
        on = lambda lv: {"choice": f"firewall-{lv}", "label": "Hub and apps" if lv == "apps" else "Hub only",  # noqa: E731
                         "confirm": f"Limit what a guest on the hotspot ({iface}) can reach to {FLOOR_WORDS[lv]}, and MQTT and Syncthing "
                                    "where installed? SSH from the hotspot is closed until you open it here. Your own network is not affected."}
        return [_finding("firewall", "What a guest on the hotspot can reach", "warn" if not ours else "problem",
                         ("Everything that listens on the box, SSH and the rest: there is no floor." if not ours else
                          "The floor is switched on, but its rules are not loaded (nft list table inet irate_box).")
                         + f" The floor would allow, on {iface} alone: TCP {', '.join(map(str, tcp))}; UDP {', '.join(map(str, udp))}; "
                         "and drop the rest. The box's other networks are not touched.",
                         "A default-drop ruleset on the hotspot's interface, loaded at boot, at one of two levels: the hub and its "
                         "apps, or the hub alone (its apps' ports closed to guests). MQTT and Syncthing open to guests where installed, "
                         "SSH only if you say so. Undo here.",
                         [on("apps"), on("hub")])]
    tcp, udp = firewall.ports(firewall.services_here(wanted), level)
    ssh = "ssh" in wanted
    other = "hub" if level == "apps" else "apps"
    return [_finding("firewall", "What a guest on the hotspot can reach", "ok",
                     f"{'Hub and apps' if level == 'apps' else 'Hub only'}: on {iface}, TCP {', '.join(map(str, tcp))}; UDP {', '.join(map(str, udp))}; the rest dropped"
                     + (" (SSH from the hotspot open, by your choice)." if ssh else "; SSH from the hotspot closed."),
                     "",
                     [{"choice": f"firewall-{other}", "label": "Hub only: close the apps' ports" if other == "hub" else "Hub and apps: open the apps' ports",
                       "confirm": None},
                      {"choice": "firewall-ssh-off" if ssh else "firewall-ssh-on",
                       "label": "Close SSH from the hotspot" if ssh else "Open SSH from the hotspot",
                       "confirm": None if ssh else "Let guests on the hotspot reach SSH (port 22)? Keys-only logins are strongly advised first (above)."},
                      {"choice": "firewall-off", "label": "Switch the floor off", "confirm": "Take the floor away? Every listener on the box is then reachable from the hotspot again."}])]


def _firewall(rec, what):
    if what == "off":
        if "firewall" not in rec:
            raise ValueError("the floor is not on from this page")
        _floor_apply(None)
        rec.pop("firewall")
        return "the floor is off: every listener is reachable from the hotspot again"
    cur = rec.get("firewall") or {"services": ["mqtt", "sync"], "level": "apps"}
    services = set(cur.get("services", []))
    level = {"on": "apps", "apps": "apps", "hub": "hub"}.get(what, cur.get("level", "apps"))
    if what == "ssh-on":
        services.add("ssh")
    elif what == "ssh-off":
        services.discard("ssh")
    floor = {"services": sorted(services), "level": level, "date": time.strftime("%Y-%m-%d")}
    _floor_apply(floor)
    rec["firewall"] = floor
    return {"on": f"the floor is on: guests on the hotspot reach {FLOOR_WORDS[level]}" + (", MQTT and Syncthing where installed" if services - {"ssh"} else ""),
            "apps": f"the floor: guests on the hotspot reach {FLOOR_WORDS['apps']}", "hub": f"the floor: guests on the hotspot reach {FLOOR_WORDS['hub']} only",
            "ssh-on": "SSH open from the hotspot", "ssh-off": "SSH closed from the hotspot"}[what]


def share_findings():
    """Guests' internet (share.py), while the owner shares it: what is shared, and each part of the
    containment the owner has turned off, a finding of its own with the way back (Tom, 2026-10-08)."""
    from irate_box.root import firewall, share
    st = share.load()
    t = "Guests' internet through the box"
    off = [k for k in share.CONTAIN if not st["contain"][k]]
    if not share.on(st):
        # Nothing shared, nothing at risk; but a part left off would be off again the moment sharing is.
        return [_finding("guest-net", t, "ok", "Not shared: guests reach the box and nothing else. When it is, these parts of the "
                         "containment will be off, as you left them: " + "; ".join(share.CONTAIN_WORDS[k] for k in off) + ".", "",
                         [{"choice": f"share-contain-{k}-on", "label": f"Turn back on: {share.CONTAIN_WORDS[k]}", "confirm": None} for k in off])] if off else []
    out = []
    if not firewall.loaded():
        out.append(_finding("guest-net-rules", t, "problem", f"Sharing is set ({share.WORDS[st['level']]}), but its rules are not loaded.",
                            "Set the level again on Network → the hotspot, or switch it off there."))
    for k in off:
        out.append(_finding(f"guest-net-{k}", f"{t}: containment part off", "warn",
                            f"Off by your choice: {share.CONTAIN_WORDS[k]}.",
                            {"lan": "Guests can reach your own network behind the box: the router's page, printers, shares.",
                             "tunnels": "Guests' traffic can go into Tailscale or a container's network, as the box.",
                             "by_mac": "A device given an address another had within the last 12 hours is let out without the sheet.",
                             "dns_hold": "A device that hasn't tapped through can look outside names up through the box (a way to tunnel data out)."}[k],
                            [{"choice": f"share-contain-{k}-on", "label": "Turn it back on", "confirm": None}]))
    on_parts = [k for k in share.CONTAIN if st["contain"][k]]
    out.append(_finding("guest-net", t, "ok" if not off else "warn",
                        f"Shared: {share.WORDS[st['level']]}. Contained: " + ("; ".join(share.CONTAIN_WORDS[k] for k in on_parts) or "nothing") + ".",
                        "", [{"choice": f"share-contain-{k}-off", "label": f"Turn off: {share.CONTAIN_WORDS[k]}",
                              "confirm": f"Turn this part of the containment off ({share.CONTAIN_WORDS[k]})? The Security doctor will warn while it is."}
                             for k in on_parts]))
    return out


def _share_contain(what):
    from irate_box.root import firewall, share
    k, _, onoff = what.rpartition("-")
    if k not in share.CONTAIN or onoff not in ("on", "off"):
        raise ValueError(f"share-contain-{what} is not something the Security page does")
    st = share.load()
    st["contain"][k] = onoff == "on"
    if share.on(st):
        floor = load_record().get("firewall")
        firewall.apply_all(dict(floor, services=firewall.services_here(floor.get("services", []))) if floor else None, st)
    share.save(st)
    return f"{'On again' if onoff == 'on' else 'Turned off'}: {share.CONTAIN_WORDS[k]}"


def scan():
    rec = load_record()
    found = listeners()
    findings = listener_findings(found, rec)
    settings = sshd_settings()
    findings += ssh_findings(settings, keys_on_box(), rec)
    findings += sudo_findings(rec, settings or {})
    findings += kernel_findings(rec)
    findings += group_findings(rec)
    findings += root_findings(rec)
    findings += apt_findings(rec)
    findings += log_findings(rec)
    findings += firewall_findings(rec)
    findings += share_findings()
    upd, _ = update_findings()
    findings += upd
    return {"at": time.time(), "listeners": found, "findings": findings}


# --- fixes --------------------------------------------------------------------------------

def _systemctl(*args):
    out = run("systemctl", *args)
    if out.returncode != 0:
        raise ValueError(f"systemctl {' '.join(args)}: {(out.stderr or out.stdout).strip().splitlines()[-1:] or ['failed']}")


def _write_sshd_dropin(ssh):
    """The one drop-in for both SSH settings; it sorts first, and sshd keeps the first value it reads."""
    if not ssh:
        SSHD_DROPIN.unlink(missing_ok=True)
    else:
        SSHD_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Written by irate-box's Security page (/admin). Undo there, or delete this file."]
        if "root" in ssh:
            lines.append("PermitRootLogin no")
        if "password" in ssh:
            lines += ["PasswordAuthentication no", "KbdInteractiveAuthentication no"]
        if "forwarding" in ssh:
            lines += ["AllowTcpForwarding no", "AllowAgentForwarding no", "X11Forwarding no"]
        SSHD_DROPIN.write_text("\n".join(lines) + "\n")
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    check = run(sshd, "-t")
    if check.returncode != 0:
        SSHD_DROPIN.unlink(missing_ok=True)
        raise ValueError(f"sshd rejected the change, so it was not made: {check.stderr.strip()[:200]}")
    for unit in ("ssh.service", "sshd.service"):
        if run("systemctl", "reload", unit).returncode == 0:
            return
    raise ValueError("the setting is written, but sshd could not be reloaded; it applies at the next restart")


def _ssh(rec, what, on):
    ssh = set(rec.get("ssh", {}))
    if on and what == "password" and not keys_on_box():
        raise ValueError("no account has an SSH key on the box; turning passwords off would lock everyone out")
    (ssh.add if on else ssh.discard)(what)
    _write_sshd_dropin(ssh)
    rec["ssh"] = {k: rec.get("ssh", {}).get(k, time.strftime("%Y-%m-%d")) for k in ssh}
    if not rec["ssh"]:
        rec.pop("ssh")
    return {"root": "SSH root login", "password": "SSH password login", "forwarding": "SSH forwarding"}[what] + (" turned off" if on else ": back as it was")


def _cockpit(rec, mode):
    dropin = UNIT_DIR / "cockpit.socket.d" / "irate-box.conf"
    if mode == "undo":
        old = rec.pop("cockpit", None) or {}
        dropin.unlink(missing_ok=True)
        _systemctl("daemon-reload")
        if old.get("was_enabled", True):
            _systemctl("enable", "cockpit.socket")
        # A restart, not a start: a socket still bound to loopback must rebind as it was.
        _systemctl("restart", "cockpit.socket")
        return "Cockpit: back as it was"
    was_enabled = run("systemctl", "is-enabled", "cockpit.socket").stdout.strip() == "enabled"
    if mode == "loopback":
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text("# Written by irate-box's Security page (/admin). Undo there, or delete this file.\n"
                          "[Socket]\nListenStream=\nListenStream=127.0.0.1:9090\n")
        _systemctl("daemon-reload")
        _systemctl("restart", "cockpit.socket")
    else:
        _systemctl("disable", "--now", "cockpit.socket")
        run("systemctl", "stop", "cockpit.service")
    rec["cockpit"] = {"mode": mode, "was_enabled": was_enabled, "at": time.strftime("%Y-%m-%d")}
    return "Cockpit: loopback only" if mode == "loopback" else "Cockpit: switched off"


def _llmnr(rec, on):
    if on:
        RESOLVED_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        RESOLVED_DROPIN.write_text("# Written by irate-box's Security page (/admin). Undo there, or delete this file.\n"
                                   "[Resolve]\nLLMNR=no\n")
        rec["llmnr"] = time.strftime("%Y-%m-%d")
    else:
        RESOLVED_DROPIN.unlink(missing_ok=True)
        rec.pop("llmnr", None)
    _systemctl("restart", "systemd-resolved.service")
    return "LLMNR turned off" if on else "LLMNR: back as it was"


def _hub_on_80():
    out = run("ss", "-Hltnp", "sport = :80")
    return '"nginx"' in out.stdout or '"caddy"' in out.stdout


UNIT_RE = re.compile(r"^[A-Za-z0-9@_][A-Za-z0-9@._-]*\.(service|socket)$")  # no leading "-" (F14)


def _unit(rec, unit, on, reason="from this page"):
    if not UNIT_RE.match(unit) or PROTECTED.match(unit):
        raise ValueError(f"{unit} is not a unit this page switches off")
    units = rec.setdefault("units", {})
    if on:
        was_enabled = run("systemctl", "is-enabled", unit).stdout.strip() == "enabled"
        _systemctl("disable", "--now", unit)
        units[unit] = {"was_enabled": was_enabled, "at": time.strftime("%Y-%m-%d"), "reason": reason}
        return f"{unit}: stopped and disabled"
    old = units.get(unit)
    if old is None:
        raise ValueError(f"{unit} was not switched off from this page")
    if "take-port-80" in old.get("reason", "") and _hub_on_80():
        raise ValueError(f"{unit} gave port 80 to the hub, which still has it. Move the hub first "
                         "(rerun install.sh --port 8080), or uninstall, which hands the port back")
    units.pop(unit)
    _systemctl("enable" if old.get("was_enabled") else "start", *(["--now"] if old.get("was_enabled") else []), unit)
    if not units:
        rec.pop("units")
    return f"{unit}: back as it was"


def install_security_updates(log_path):
    """Upgrade only the packages whose new version is from a security archive; output to log_path."""
    out = run("apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade", timeout=180)
    pkgs = pending_security(out.stdout)
    if not pkgs:
        return "no security updates waiting"
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive", HOME=os.environ.get("HOME", "/root"))
    with safeio.open_new(log_path) as log:  # in control/, which the hub can change (F3)
        log.write("$ apt-get install --only-upgrade " + " ".join(pkgs) + "\n\n")
        log.flush()
        code = subprocess.run(["apt-get", "install", "-y", "--only-upgrade", "-o", "Dpkg::Options::=--force-confold",
                               *pkgs], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                              env=env, timeout=3600).returncode
    if code != 0:
        raise ValueError(f"apt-get exited with {code}; see the log on the Security page")
    return f"installed {len(pkgs)} security update{'s' if len(pkgs) != 1 else ''}"


def fix(choice, updates_log):
    """Carry out one of the page's offers. Returns a message; raises ValueError."""
    rec = load_record()
    if choice in ("ssh-root-off", "ssh-root-undo"):
        msg = _ssh(rec, "root", choice.endswith("off"))
    elif choice in ("ssh-password-off", "ssh-password-undo"):
        msg = _ssh(rec, "password", choice.endswith("off"))
    elif choice in ("ssh-forwarding-off", "ssh-forwarding-undo"):
        msg = _ssh(rec, "forwarding", choice.endswith("off"))
    elif choice in ("cockpit-loopback", "cockpit-off", "cockpit-undo"):
        msg = _cockpit(rec, choice.split("-", 1)[1])
    elif choice in ("llmnr-off", "llmnr-undo"):
        msg = _llmnr(rec, choice == "llmnr-off")
    elif choice in ("root-lock", "root-lock-undo"):
        msg = _root(rec, "lock", choice == "root-lock")
    elif choice in ("firstrun-off", "firstrun-undo"):
        msg = _root(rec, "firstrun", choice == "firstrun-off")
    elif choice == "kernel-links-debian":
        msg = _kernel_debian(rec)
    elif choice in [f"kernel-{n}-{w}" for n in KERNEL for w in ("on", "undo")]:
        msg = _kernel(rec, choice.split("-")[1], choice.endswith("-on"))
    elif choice.startswith(("group-drop:", "group-undo:")):
        msg = _group(rec, choice.split(":", 1)[1], choice.startswith("group-drop:"))
    elif choice.startswith(("unit-off:", "unit-undo:")):
        msg = _unit(rec, choice.split(":", 1)[1], choice.startswith("unit-off:"))
    elif choice.startswith(("sudo-drop:", "sudo-undo:")):
        msg = _sudo(rec, choice.split(":", 1)[1], choice.startswith("sudo-drop:"))
    elif choice.startswith(("apt-signedby:", "apt-undo:")):
        msg = _apt(rec, choice.split(":", 1)[1], choice.startswith("apt-signedby:"))
    elif choice in ("logs-card", "logs-undo"):
        msg = _logs(rec, choice == "logs-card")
    elif choice in ("firewall-on", "firewall-apps", "firewall-hub", "firewall-off", "firewall-ssh-on", "firewall-ssh-off"):
        msg = _firewall(rec, choice[len("firewall-"):])
    elif choice.startswith("share-contain-"):
        return _share_contain(choice[len("share-contain-"):])
    elif choice == "security-updates":
        return install_security_updates(updates_log)
    else:
        raise ValueError(f"{choice} is not something the Security page does")
    save_record(rec)
    return msg


def undo_all():
    """Put back everything this page changed (uninstall.sh). Returns what was undone."""
    rec = load_record()
    done = []
    # Not root's lock nor the first-login marker: undoing those would bring the image's known default
    # password back. They stay as they are (the page's own Undo still puts them back before then).
    for choice in (["ssh-root-undo"] if "root" in rec.get("ssh", {}) else []) + \
                  (["ssh-password-undo"] if "password" in rec.get("ssh", {}) else []) + \
                  (["ssh-forwarding-undo"] if "forwarding" in rec.get("ssh", {}) else []) + \
                  (["cockpit-undo"] if "cockpit" in rec else []) + (["llmnr-undo"] if rec.get("llmnr") else []) + \
                  [f"unit-undo:{u}" for u in rec.get("units", {})] + \
                  [f"kernel-{n}-undo" for n in rec.get("kernel", {})] + [f"group-undo:{g}" for g in rec.get("groups", {})] + \
                  [f"sudo-undo:{f}" for f in rec.get("sudo", {})] + [f"apt-undo:{k}" for k in rec.get("apt", {})] + \
                  (["logs-undo"] if "logs" in rec else []) + (["firewall-off"] if "firewall" in rec else []):
        try:
            done.append(fix(choice, None))
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            done.append(f"{choice}: {exc}")
    RECORD.unlink(missing_ok=True)
    return done


if __name__ == "__main__":
    import sys
    if os.geteuid() != 0:
        sys.exit("run as root")
    if sys.argv[1:] == ["scan"]:
        print(json.dumps(scan(), indent=2))
    elif sys.argv[1:] == ["summary"]:
        # For the end of install.sh: what needs the owner's attention, one line each.
        for f in scan()["findings"]:
            if f["status"] != "ok":
                print(f"{'problem' if f['status'] == 'problem' else 'note   '}  {f['title']}: {f['detail']}")
    elif len(sys.argv) in (3, 4) and sys.argv[1] == "unit-off":
        rec = load_record()
        print(_unit(rec, sys.argv[2], True, *(sys.argv[3:] or ["from this page"])))
        save_record(rec)
    elif sys.argv[1:] == ["undo-all"]:
        for line in undo_all():
            print(line)
    else:
        sys.exit("usage: security.py scan | summary | undo-all | unit-off UNIT [REASON]")
