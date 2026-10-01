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

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RECORD = ETC / "security-changes.json"
SSHD_DROPIN = Path(os.environ.get("HUB_SSHD_DROPIN", "/etc/ssh/sshd_config.d/01-irate-box.conf"))
RESOLVED_DROPIN = Path(os.environ.get("HUB_RESOLVED_DROPIN", "/etc/systemd/resolved.conf.d/irate-box.conf"))
UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
UPDATES_LOG_NAME = "security-updates.log"

# Listeners the hub knows by port, when the owning process does not say enough by itself.
KNOWN_PORTS = {
    ("tcp", 22): "SSH", ("tcp", 80): "the hub (Caddy)", ("tcp", 9090): "Cockpit",
    ("tcp", 1883): "MQTT broker (mosquitto)", ("tcp", 22000): "Syncthing", ("udp", 22000): "Syncthing",
    ("udp", 21027): "Syncthing discovery", ("udp", 5353): "mDNS (Avahi or resolved)",
    ("tcp", 5355): "LLMNR (systemd-resolved)", ("udp", 5355): "LLMNR (systemd-resolved)",
    ("udp", 41641): "Tailscale", ("udp", 53): "DNS", ("tcp", 53): "DNS", ("udp", 67): "DHCP",
}
# The hub's own listeners, there on purpose: reported, not offered for closing here.
OURS = {("tcp", 80), ("tcp", 1883), ("tcp", 22000), ("udp", 22000), ("udp", 21027)}
# Units of the hub and its add-ons: whatever they listen on is theirs (Syncthing, for one,
# also opens random UDP ports for its connections).
OUR_UNITS = re.compile(r"^(caddy|irate-box.*|kiwix|silverbullet|ttyd|mosquitto|excalidraw-room|syncthing@.*)\.service$")
# The box's own network clients: they answer only the network's DHCP server.
CLIENT_PORTS = {("udp", 68): "DHCP client", ("udp", 546): "DHCPv6 client"}
# Units the page never offers to stop: the box would be unreachable or the hub would break.
PROTECTED = re.compile(r"^(ssh|sshd|systemd-.*|dbus|NetworkManager|wpa_supplicant|caddy|"
                       r"irate-box.*|tailscaled|mosquitto|syncthing@.*|kiwix|init)\.(service|socket)$")


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
        if f["unit"] == "caddy.service" and f["proto"] == "tcp":
            out.append(_finding(fid, "the hub (Caddy)", "ok",
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
        for line in Path("/etc/passwd").read_text().splitlines():
            name, _, uid, _, _, home, shell = line.split(":")
            if (uid == "0" or int(uid) >= 1000) and not shell.endswith(("nologin", "false")):
                users.append((name, home))
    except (OSError, ValueError):
        pass
    return users


def keys_on_box():
    """Accounts that have at least one key in ~/.ssh/authorized_keys."""
    have = []
    for name, home in _login_users():
        try:
            lines = Path(home, ".ssh", "authorized_keys").read_text().splitlines()
        except OSError:
            continue
        if any(line.strip() and not line.lstrip().startswith("#") for line in lines):
            have.append(name)
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


def update_findings():
    if not _have("apt-get"):
        return [], []
    out = run("apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade", timeout=180)
    pkgs = pending_security(out.stdout)
    unattended = _have("unattended-upgrade")
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
    findings.append(_finding("unattended", "Automatic security updates", "ok" if unattended else "warn",
                             "unattended-upgrades is installed." if unattended else
                             "Not set up: security updates wait until someone installs them.",
                             "" if unattended else "Planned: unattended-upgrades for Debian-Security updates only. Until then, install them here."))
    return findings, pkgs


# --- scan ---------------------------------------------------------------------------------

def scan():
    rec = load_record()
    found = listeners()
    findings = listener_findings(found, rec)
    findings += ssh_findings(sshd_settings(), keys_on_box(), rec)
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
    return {"root": "SSH root login", "password": "SSH password login"}[what] + (" turned off" if on else ": back as it was")


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


def _caddy_on_80():
    out = run("ss", "-Hltnp", "sport = :80")
    return '"caddy"' in out.stdout


UNIT_RE = re.compile(r"^[A-Za-z0-9@._-]+\.(service|socket)$")


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
    if "take-port-80" in old.get("reason", "") and _caddy_on_80():
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
    with open(log_path, "w") as log:
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
    elif choice in ("cockpit-loopback", "cockpit-off", "cockpit-undo"):
        msg = _cockpit(rec, choice.split("-", 1)[1])
    elif choice in ("llmnr-off", "llmnr-undo"):
        msg = _llmnr(rec, choice == "llmnr-off")
    elif choice.startswith(("unit-off:", "unit-undo:")):
        msg = _unit(rec, choice.split(":", 1)[1], choice.startswith("unit-off:"))
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
    for choice in (["ssh-root-undo"] if "root" in rec.get("ssh", {}) else []) + \
                  (["ssh-password-undo"] if "password" in rec.get("ssh", {}) else []) + \
                  (["cockpit-undo"] if "cockpit" in rec else []) + (["llmnr-undo"] if rec.get("llmnr") else []) + \
                  [f"unit-undo:{u}" for u in rec.get("units", {})]:
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
