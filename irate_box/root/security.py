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

from irate_box.finding import action, english, finding
from irate_box.hub import services
from irate_box.msg import M, Refused, is_message, named, render, sentences, when
from irate_box.root import aptram, safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
# Whether the hotspot is up now (root/ap.py writes it): a port is open to hotspot guests only while it is.
AP_STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub")) / "control" / "ap.json"
RECORD = ETC / "security-changes.json"
SSHD_DROPIN = Path(os.environ.get("HUB_SSHD_DROPIN", "/etc/ssh/sshd_config.d/01-irate-box.conf"))
RESOLVED_DROPIN = Path(os.environ.get("HUB_RESOLVED_DROPIN", "/etc/systemd/resolved.conf.d/irate-box.conf"))
UNIT_DIR = Path(os.environ.get("HUB_UNIT_DIR", "/etc/systemd/system"))
SYSCTL_DROPIN = Path(os.environ.get("HUB_SYSCTL_DROPIN", "/etc/sysctl.d/60-irate-box.conf"))
PROC_SYS = Path(os.environ.get("HUB_PROC_SYS", "/proc/sys"))
GROUP_FILE = Path(os.environ.get("HUB_GROUP_FILE", "/etc/group"))
PASSWD_FILE = Path(os.environ.get("HUB_PASSWD_FILE", "/etc/passwd"))
FSTAB = Path(os.environ.get("HUB_FSTAB", "/etc/fstab"))
# The box's own /proc, as PID 1 has it: this unit runs under ProtectProc, so its own /proc/mounts always says hidepid.
INIT_MOUNTS = Path(os.environ.get("HUB_INIT_MOUNTS", "/proc/1/mounts"))
# Armbian's first-login marker: present until someone logs in as root at the console and answers its
# setup (a root password, a user). Left on a box set up some other way, its image's defaults stand.
FIRSTRUN = Path(os.environ.get("HUB_ARMBIAN_FIRSTRUN", "/root/.not_logged_in_yet"))
UPDATES_LOG_NAME = "security-updates.log"

# Listeners the hub knows by port, when the owning process does not say enough by itself.
KNOWN_PORTS = {
    ("tcp", 22): named("SSH"),
    ("tcp", 80): M("port.name.hub-server", "Hub server"),
    ("tcp", 9090): named("Cockpit"),
    ("tcp", 1883): named("mosquitto", M("port.role.mqtt", "MQTT broker")),
    ("tcp", 22000): named("Syncthing"),
    ("udp", 22000): named("Syncthing"),
    ("udp", 21027): named("Syncthing", M("port.role.discovery", "discovery")),
    ("udp", 5353): named("mDNS", "Avahi or systemd-resolved"),
    ("tcp", 5355): named("LLMNR", "systemd-resolved"),
    ("udp", 5355): named("LLMNR", "systemd-resolved"),
    ("udp", 41641): named("Tailscale"),
    ("udp", 53): named("DNS"),
    ("tcp", 53): named("DNS"),
    ("udp", 67): named("DHCP"),
}
# The hub's own listeners, there on purpose: reported, not offered for closing here.
OURS = {("tcp", 80), ("tcp", 1883), ("tcp", 22000), ("udp", 22000), ("udp", 21027)}
# Units of the hub and its add-ons: whatever they listen on is theirs (Syncthing, for one,
# also opens random UDP ports for its connections).
OUR_UNITS = re.compile(
    r"^(nginx|caddy|irate-box.*|kiwix|silverbullet|ttyd|mosquitto|excalidraw-room|syncthing@.*)\.service$"
)
# The hub's front door: nginx, or Caddy, the fallback (install.sh --web).
WEB_UNITS = {"nginx.service": "nginx", "caddy.service": "Caddy"}
# The box's own network clients: they answer only the network's DHCP server.
CLIENT_PORTS = {
    ("udp", 68): M("port.name.dhcp-client", "DHCP client"),
    ("udp", 546): M("port.name.dhcpv6-client", "DHCPv6 client"),
}
# Units the page never offers to stop: the box would be unreachable or the hub would break.
PROTECTED = re.compile(
    r"^(ssh|sshd|systemd-.*|dbus|NetworkManager|wpa_supplicant|nginx|caddy|"
    r"irate-box.*|tailscaled|mosquitto|syncthing@.*|kiwix|init)\.(service|socket)$"
)
# Services the add-ons declare (manifests' network block) are the hub's too: services.py.


def run(*cmd, timeout=60):
    """A command's result; one not installed, or that cannot start, answers 127 as a shell would."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except OSError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def _have(cmd):
    return shutil.which(cmd) is not None


def _installed_from(argv, env):
    """apt-get install's exit code; 127 when apt-get is not there."""
    try:
        return subprocess.run(  # noqa: TID251
            argv, capture_output=True, env=env, stdin=subprocess.DEVNULL, timeout=1200
        ).returncode
    except OSError:
        return 127


def _chip(group, choice, label, on, confirm=None):
    """One option of a choice, drawn as a chip in its group's row; `on` marks how it is now (pressing it does nothing)."""
    return {
        "choice": choice,
        "label": label,
        "group": group,
        "on": bool(on),
        **({"confirm": confirm} if confirm and not on else {}),
    }


UNDO = M("action.undo", "Undo")
ON, OFF = M("chip.on", "On"), M("chip.off", "Off")


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
        found.append(
            {
                "proto": "udp" if proto.startswith("udp") else "tcp",
                "addr": addr or "*",
                "port": int(port),
                "process": m.group(1) if m else None,
                "pid": int(m.group(2)) if m else None,
            }
        )
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
    from irate_box.hub import manifests, services

    try:
        found_manifests = manifests.load()
    except (manifests.ManifestError, OSError):
        found_manifests = []
    out = run("ss", "-H", "-ltnup")
    found = parse_ss(out.stdout)
    sockets, packages = None, {}
    for f in found:
        unit = _unit_of(f["pid"]) if f["pid"] else None
        if f["process"] == "systemd" or unit in (None, "init.scope"):
            sockets = sockets if sockets is not None else _socket_units()
            unit = sockets.get(f["port"], unit)
        f["unit"] = unit if unit and unit != "init.scope" else None
        decl = services.by_port(f["proto"], f["port"], found_manifests) or services.by_unit(f["unit"], found_manifests)
        own = (decl or {}).get("name") or f["process"]
        f["name"] = KNOWN_PORTS.get((f["proto"], f["port"])) or (
            named(own) if own else M("port.name.unknown", "Unknown program")
        )
        f["owner"] = _owner(f, decl, packages)
    return found


def _package_of(path, cache):
    """The Debian package a file belongs to (dpkg -S), or None."""
    if not path:
        return None
    if path not in cache:
        out = run("dpkg-query", "-S", path)
        # "nginx-core: /usr/sbin/nginx"; a file in two packages: "a, b: /path" (the first is said).
        line = out.stdout.splitlines()[0] if out.returncode == 0 and out.stdout else ""
        cache[path] = line.split(": ", 1)[0].split(",")[0].strip() if ": " in line else None
        cache[path] = cache[path] or None
    return cache[path]


def _owner(f, decl, cache):
    """What a listener belongs to, for the table's Belongs to column: the app or add-on that declares it, the
    hub's own units, else the package of its program (or of its unit's file), else nobody known: a
    program that may be left over and could be stopped."""
    if decl:
        return {"kind": "app", "name": decl.get("name") or decl.get("id")}
    if f.get("unit") and OUR_UNITS.match(f["unit"]):
        return {"kind": "hub", "name": "irate-box"}
    exe = None
    if f.get("pid"):
        try:
            exe = os.readlink(f"/proc/{f['pid']}/exe")
        except OSError:
            exe = None
    pkg = _package_of(exe, cache)
    if not pkg and f.get("unit"):
        frag = run("systemctl", "show", "-p", "FragmentPath", "--value", f["unit"]).stdout.strip()
        pkg = _package_of(frag, cache)
    return {"kind": "package", "name": pkg} if pkg else {"kind": "unknown", "name": exe or f.get("process")}


def _where(f):
    """Where a listener is, as the evidence of its finding: "TCP 9090", "0.0.0.0, ::", "cockpit.socket"."""
    return [f"{f['proto'].upper()} {f['port']}", f["addr"]] + ([f["unit"]] if f["unit"] else [])


HUB_UNIT = "irate-box.service"


def _hub_port():
    """The port the hub's own server is configured to listen on (PORT in hub.env, as install.sh writes it)."""
    try:
        for line in (ETC / "hub.env").read_text().splitlines():
            if line.startswith("PORT="):
                return int(line.split("=", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return 8000


def _hotspot_up():
    try:
        return json.loads(AP_STATE.read_text()).get("up") is True
    except (OSError, ValueError, AttributeError):
        return False


def listener_findings(found, rec):
    out = []
    hotspot = _hotspot_up()
    llmnr_done = False
    hub_port = _hub_port()
    for f in found:
        key = (f["proto"], f["port"])
        fid = f"port-{f['proto']}-{f['port']}"
        name = f["name"] if is_message(f["name"]) else named(f["name"])
        where = _where(f)
        if f["unit"] == HUB_UNIT and f["proto"] == "tcp" and f["port"] == hub_port:
            # Every listener here is off loopback (parse_ss). The hub's backend is meant to be reached only by the web
            # server in front of it, which adds the checks; on the network it is reached without them.
            out.append(
                finding(
                    fid,
                    named("irate-box", M("port.role.hub-backend", "hub backend")),
                    "problem",
                    M("port.hub-backend", "The hub's own server listens on the network, not only on this box."),
                    M(
                        "port.hub-backend.why",
                        "Devices on the network can reach it directly, past the web server's access checks and TLS.",
                    ),
                    M(
                        "port.hub-backend.fix",
                        "Set HUB_BIND=127.0.0.1 in hub.env, then restart the hub. The web server reaches it there.",
                    ),
                    evidence=where,
                )
            )
        elif f["unit"] in WEB_UNITS and f["proto"] == "tcp":
            out.append(
                finding(
                    fid,
                    named(WEB_UNITS[f["unit"]], M("port.role.hub-server", "hub server")),
                    "ok",
                    M(
                        "port.hub-server",
                        "The hub server. Devices on the network reach the hub and the administration pages here.",
                    ),
                    evidence=where,
                )
            )
        elif key in CLIENT_PORTS:
            out.append(
                finding(
                    fid,
                    CLIENT_PORTS[key],
                    "ok",
                    M(
                        "port.dhcp-client",
                        "The box asks the network for its address here. It accepts answers only from the network's DHCP server.",
                    ),
                    evidence=where,
                )
            )
        elif decl := (services.by_port(f["proto"], f["port"]) or services.by_unit(f["unit"])):
            # The add-on's own words (its manifest): they are translated with the manifest, not here.
            out.append(
                finding(
                    fid,
                    named(decl["name"]),
                    decl["risk"],
                    decl["says"],
                    fix=None
                    if decl["risk"] == "ok"
                    else M(
                        "port.declared.fix",
                        "If nobody uses this add-on, remove it on Add-ons. "
                        "The hotspot firewall, below, sets whether devices on the hotspot can reach it.",
                    ),
                    evidence=where,
                )
            )
        elif key not in OURS and f["unit"] and OUR_UNITS.match(f["unit"]):
            out.append(
                finding(
                    fid,
                    name,
                    "ok",
                    M(
                        "port.ours",
                        "Part of the box's {service} service.",
                        service=f["unit"].split(".")[0].split("@")[0],
                    ),
                    evidence=where,
                )
            )
        elif key == ("tcp", 1883):
            out.append(
                finding(
                    fid,
                    name,
                    "warn",
                    M("port.mqtt", "The MQTT broker. Meshtastic nodes publish their messages here."),
                    M(
                        "port.mqtt.why",
                        "Any device on the network can also publish here, without an account. "
                        "Publishing is limited to the msh/# topics.",
                    ),
                    evidence=where,
                )
            )
        elif key in OURS:
            out.append(
                finding(
                    fid,
                    name,
                    "ok",
                    M("port.hub-own", "A service of the hub. Devices on the network are meant to reach it."),
                    evidence=where,
                )
            )
        elif f["port"] == 9090 and f["unit"] and f["unit"].startswith("cockpit"):
            continue  # said with Cockpit's own setting (cockpit_findings)
        elif f["port"] == 22 and f["proto"] == "tcp":
            continue  # reported with SSH's own settings below
        elif f["port"] == 5355:
            if llmnr_done:
                continue  # TCP and UDP: one line, one switch
            llmnr_done = True
            group = M("port.llmnr.group", "LLMNR")
            out.append(
                finding(
                    fid,
                    name,
                    "warn",
                    M(
                        "port.llmnr",
                        "systemd-resolved answers LLMNR name lookups from any device on the local network.",
                    ),
                    M(
                        "port.llmnr.why",
                        "The hub does not use LLMNR. Each service that answers the network is one more thing that an attacker can try.",
                    ),
                    M("port.llmnr.fix", "Disable LLMNR in systemd-resolved. Normal DNS does not change."),
                    evidence=where,
                    actions=[_chip(group, "llmnr-on-now", ON, True), _chip(group, "llmnr-off", OFF, False)],
                )
            )
        elif f["port"] in (41641,) or (f["unit"] or "").startswith("tailscaled"):
            out.append(
                finding(
                    fid,
                    named("Tailscale"),
                    "ok",
                    M(
                        "port.tailscale",
                        "Tailscale. Its traffic is encrypted with WireGuard. Turn it on or off under Access.",
                    ),
                    evidence=where,
                )
            )
        else:
            # Not the hub's and not declared: the owner's own, or the image's. A scale, from the most closed:
            # stop it; keep it for the box's own networks (the hotspot firewall keeps hotspot guests off: the default);
            # or open its port to hotspot guests too.
            unit = f["unit"]
            stoppable = bool(unit) and not PROTECTED.match(unit)
            fw = rec.get("firewall")
            opened = bool(fw) and f"port:{f['proto']}/{f['port']}" in fw.get("services", [])
            loopback = f["addr"] in ("127.0.0.1", "::1", "[::1]", "localhost")
            acts = (
                [
                    action(
                        f"unit-off:{unit}",
                        M("port.unit-off", "Stop and disable {unit}", unit=unit),
                        M(
                            "port.unit-off.confirm",
                            "{unit} stops and does not start at boot. Undo puts back its previous state.",
                            unit=unit,
                        ),
                    )
                ]
                if stoppable
                else []
            )
            if fw and not loopback:
                # Open or closed to the hotspot: a row of two chips, the one in effect marked.
                proto = f["proto"].upper()
                group = M("port.fw-group", "{proto} {port} on the hotspot", proto=proto, port=f["port"])
                acts += [
                    _chip(
                        group,
                        f"firewall-port-on:{f['proto']}/{f['port']}",
                        M("port.fw-chip-open", "Open"),
                        opened,
                        M(
                            "port.fw-open.confirm",
                            "Devices on the hotspot can reach {name} ({proto} {port}). The hub does not know what it does or who can use it.",
                            name=name,
                            proto=proto,
                            port=f["port"],
                        ),
                    ),
                    _chip(
                        group,
                        f"firewall-port-off:{f['proto']}/{f['port']}",
                        M("port.fw-chip-closed", "Closed"),
                        not opened,
                    ),
                ]
            reach = (
                M("port.other.opened", "Not part of the hub. You let devices on the hotspot reach it.")
                if opened
                else M(
                    "port.other.closed",
                    "Not part of the hub. The hotspot firewall keeps devices on the hotspot away from it. "
                    "Devices on your own network can reach it.",
                )
                if fw
                else M(
                    "port.other.no-firewall",
                    "Not part of the hub. The hotspot firewall is off, so devices on the hotspot can reach it too.",
                )
                if hotspot
                else M(
                    "port.other.no-firewall-down",
                    "Not part of the hub. Devices on your own network can reach it. The hotspot is off now. "
                    "While it is on, its guests can reach it too, as the hotspot firewall is off.",
                )
            )
            out.append(
                finding(
                    fid,
                    name,
                    "warn",
                    reach,
                    M(
                        "port.other.why",
                        "If nothing on the box needs it, it is one more thing that an attacker can try.",
                    ),
                    (
                        M(
                            "port.other.fix",
                            "If nothing on the box needs it, stop it. To keep it, select Accept risk on the Security doctor.",
                        )
                        if stoppable
                        else M(
                            "port.other.fix-shell",
                            "If nothing on the box needs it, stop it from a shell. To keep it, select Accept risk on the Security doctor.",
                        )
                    ),
                    evidence=where,
                    actions=acts,
                )
            )
    for unit, change in rec.get("units", {}).items():
        reason = change.get("reason", "from this page")
        said = (
            M("unit.off.page", "Stopped and disabled from this page on {date}.", date=change.get("at", ""))
            if reason == "from this page"
            else M(
                "unit.off.port-80",
                "Stopped and disabled by the installer on {date}, so that the hub server can use port 80.",
                date=change.get("at", ""),
            )
            if "take-port-80" in reason
            else M(
                "unit.off.other", "Stopped and disabled on {date}: {reason}.", date=change.get("at", ""), reason=reason
            )
        )
        out.append(finding(f"unit-{unit}", named(unit), "ok", said, actions=[action(f"unit-undo:{unit}", UNDO)]))
    if rec.get("llmnr"):
        group = M("port.llmnr.group", "LLMNR")
        out.append(
            finding(
                "llmnr-change",
                named("LLMNR"),
                "ok",
                M("llmnr.off-here", "Disabled from this page."),
                actions=[_chip(group, "llmnr-undo", ON, False), _chip(group, "llmnr-off-now", OFF, True)],
            )
        )
    return out


# --- Cockpit -----------------------------------------------------------------------------

COCKPIT_MODES = ("network", "loopback", "off")


def _cockpit_installed():
    out = run("systemctl", "show", "-p", "LoadState", "--value", "cockpit.socket")
    return out.returncode == 0 and out.stdout.strip() == "loaded"


def _cockpit_ports(found):
    return [f for f in found if f["port"] == 9090 and (f.get("unit") or "").startswith("cockpit")]


def _cockpit_now(ports):
    """network, loopback or off. Only listeners off loopback are found (parse_ss): one is Cockpit on the
    network. Otherwise a socket still active listens on loopback only."""
    if ports:
        return "network"
    return "loopback" if run("systemctl", "is-active", "cockpit.socket").stdout.strip() == "active" else "off"


def cockpit_findings(found, rec):
    """Cockpit, where the image has it: one setting, always there, with its three values (on the
    network, on loopback only, off), the one in effect marked. The value it had before this page
    changed it is the one Undo puts back, so choosing that one undoes."""
    ports = _cockpit_ports(found)
    if not ports and "cockpit" not in rec and not _cockpit_installed():
        return []
    now = _cockpit_now(ports)
    ours = rec.get("cockpit")
    before = (ours or {}).get("before", "network")  # records from before this setting: it was on the network
    group = M("cockpit.group", "Cockpit listens")
    labels = {
        "network": M("cockpit.network", "On the network"),
        "loopback": M("cockpit.loopback", "On loopback only"),
        "off": OFF,
    }
    confirm = {
        "network": M(
            "cockpit.network.confirm",
            "Every device on the network can reach Cockpit's sign-in page, which gives root access.",
        ),
        "off": M("port.cockpit.off.confirm", "Cockpit stops and is disabled. Undo puts back its previous state."),
    }
    chips = [
        _chip(
            group,
            "cockpit-undo" if ours and m == before and m != now else f"cockpit-{m}",
            labels[m],
            m == now,
            None if ours and m == before else confirm.get(m),
        )
        for m in COCKPIT_MODES
    ]
    said = {
        "network": M("port.cockpit", "Cockpit, the image's Linux web console, listens on the network."),
        "loopback": M("cockpit.loopback-state", "Cockpit listens on loopback only. Only the box itself reaches it."),
        "off": M("cockpit.off-state", "Cockpit is stopped and disabled."),
    }[now]
    if now == "network":
        return [
            finding(
                "cockpit",
                named("Cockpit"),
                "problem",
                said,
                M(
                    "port.cockpit.why",
                    "Its sign-in page gives root access, and every device on the network can reach it.",
                ),
                M(
                    "port.cockpit.fix",
                    "Nothing on the box needs Cockpit. Bind it to loopback, so that only the box reaches it, or disable it.",
                ),
                evidence=_where(ports[0]) if ports else [],
                actions=chips,
            )
        ]
    return [
        finding(
            "cockpit",
            named("Cockpit"),
            "ok",
            said,
            notes=[M("cockpit.set-here", "Set on this page on {date}.", date=ours.get("at", ""))] if ours else [],
            actions=chips,
        )
    ]


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
    default when sshd will not say."""
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


def _ssh_chips(rec, what, group, on_label, off_label, is_on, confirm_off, confirm_on=None):
    """Both values of an SSH setting as chips, always: the one in effect marked; the other undoes what
    this page set, or sets it here."""
    ours = what in rec.get("ssh", {}) or what in rec.get("ssh_on", {})
    other = f"ssh-{what}-undo" if ours else f"ssh-{what}-{'off' if is_on else 'on'}"
    return [
        _chip(group, other if not is_on else f"ssh-{what}-on", on_label, is_on, None if ours else confirm_on),
        _chip(group, other if is_on else f"ssh-{what}-off", off_label, not is_on, None if ours else confirm_off),
    ]


def ssh_findings(settings, keys, rec):
    if settings is None:
        return [finding("ssh", M("ssh.title", "SSH"), "ok", M("ssh.none", "This box has no SSH server."))]
    out = []
    ours = rec.get("ssh", {})
    title = M("ssh-root.title", "SSH sign-in as root")
    root = settings.get("permitrootlogin", "yes")
    if root == "yes":
        out.append(
            finding(
                "ssh-root",
                title,
                "problem",
                M("ssh-root.on", "SSH accepts sign-in as root."),
                M(
                    "ssh-root.on.why",
                    "Any device on the network can try passwords for root, and root controls the whole box.",
                )
                if settings.get("passwordauthentication", "yes") == "yes"
                else M(
                    "ssh-root.on.why-keys",
                    "Root controls the whole box. A sign-in as an ordinary user, then sudo, records who did what.",
                ),
                M("ssh-root.on.fix", "Sign in as an ordinary user and use sudo. Then disable SSH sign-in as root."),
                evidence=[f"PermitRootLogin {root}"],
                actions=[
                    action(
                        "ssh-root-off",
                        M("ssh-root.off", "Disable root SSH sign-in"),
                        M(
                            "ssh-root.off.confirm",
                            "Root cannot sign in over SSH. First make sure that you can sign in as another user who can use sudo.",
                        ),
                    )
                ],
            )
        )
    else:
        out.append(
            finding(
                "ssh-root",
                title,
                "ok",
                M("ssh-root.no", "SSH does not accept sign-in as root.")
                if root == "no"
                else M("ssh-root.limited", "SSH accepts sign-in as root only with a key or a fixed command."),
                evidence=[f"PermitRootLogin {root}"],
                actions=[action("ssh-root-undo", UNDO)] if "root" in ours else [],
            )
        )
    title = M("ssh-password.title", "SSH password sign-in")
    pw = settings.get("passwordauthentication", "yes")
    pw_chips = _ssh_chips(
        rec,
        "password",
        M("ssh-password.group", "SSH sign-in with"),
        M("ssh-password.chip-on", "A password or a key"),
        M("ssh-password.chip-off", "A key only"),
        pw == "yes",
        M(
            "ssh-password.off.confirm",
            "Only keys sign in over SSH, for {accounts}.",
            accounts=keys,
        )
        if keys
        else None,
        M("ssh-password.on.confirm", "Any device on the network can try passwords over SSH."),
    )
    if pw == "yes" and keys:
        out.append(
            finding(
                "ssh-password",
                title,
                "warn",
                M("ssh-password.on", "SSH accepts passwords."),
                M("ssh-password.on.why", "Any device on the network can try to guess a password."),
                M(
                    "ssh-password.on.fix",
                    "Sign in with your key once to make sure that it works. Then disable password sign-in.",
                ),
                notes=[
                    M(
                        "ssh-password.keys",
                        {
                            "one": "This account has a key for SSH on the box: {accounts}.",
                            "other": "These accounts have a key for SSH on the box: {accounts}.",
                        },
                        n=len(keys),
                        accounts=keys,
                    )
                ],
                actions=pw_chips,
            )
        )
    elif pw == "yes":
        out.append(
            finding(
                "ssh-password",
                title,
                "warn",
                M("ssh-password.on", "SSH accepts passwords."),
                M("ssh-password.on.why", "Any device on the network can try to guess a password."),
                M(
                    "ssh-password.no-keys.fix",
                    "Put your public key on the box first, for example with ssh-copy-id from your computer. "
                    "Then this page offers to disable passwords.",
                ),
                notes=[
                    M(
                        "ssh-password.no-keys",
                        "No account has a key for SSH on the box. "
                        "If you disable passwords now, nobody can sign in over SSH.",
                    )
                ],
                actions=pw_chips,
            )
        )
    else:
        out.append(
            finding(
                "ssh-password",
                title,
                "ok",
                M("ssh-password.keys-only", "SSH accepts keys only, not passwords."),
                actions=pw_chips,
            )
        )
    # Forwarding: Debian's and Armbian's defaults leave TCP, agent
    # and X11 forwarding on. TCP forwarding hands anyone who can sign in a proxy from the hotspot
    # into the box's other network, which is the separation the hotspot design rests on.
    title = M("ssh-forwarding.title", "SSH forwarding")
    fwd = [
        k for k in ("allowtcpforwarding", "allowagentforwarding", "x11forwarding") if settings.get(k, "yes") == "yes"
    ]
    fwd_chips = _ssh_chips(
        rec,
        "forwarding",
        M("ssh-forwarding.group", "Forwarding through SSH"),
        ON,
        OFF,
        bool(fwd),
        M(
            "ssh-forwarding.off.confirm",
            "Tunnels through the box stop. Sign-ins and copies with scp still work.",
        ),
        M(
            "ssh-forwarding.on.confirm",
            "A user who can sign in can tunnel through the box.",
        ),
    )
    if fwd:
        out.append(
            finding(
                "ssh-forwarding",
                title,
                "warn",
                M("ssh-forwarding.on", "SSH lets a signed-in user forward connections through the box."),
                M(
                    "ssh-forwarding.on.why",
                    "A user who can sign in can then tunnel from the hotspot into the box's other network. "
                    "The hotspot design depends on that separation.",
                ),
                M("ssh-forwarding.on.fix", "Disable forwarding unless you use it."),
                notes=[
                    M(
                        "ssh-forwarding.tailscale",
                        "An SSH tunnel into the box's network from another network, for example over Tailscale, "
                        "needs TCP forwarding.",
                    )
                ],
                evidence=[f"{k} yes" for k in fwd],
                actions=fwd_chips,
            )
        )
    else:
        out.append(
            finding(
                "ssh-forwarding",
                title,
                "ok",
                M("ssh-forwarding.off-state", "SSH does not forward TCP, agent or X11 connections."),
                actions=fwd_chips,
            )
        )
    return out


# --- security updates ---------------------------------------------------------------------

INST_RE = re.compile(r"^Inst (\S+) .*\(([^)]*)\)")
# Debian's own fixes to a stable release carry +debNuM (or ~debNuM) in the version: the security
# team's, and the point releases', which fold the security fixes in (those come as
# "Debian:13.N/stable", not from trixie-security, so counting only "security" origins misses them).
DEB_FIX_RE = re.compile(r"[+~]deb\d+u\d+")
APT_LISTS = Path(os.environ.get("HUB_APT_LISTS", "/var/lib/apt/lists"))
# Automatic security updates: the system's own choice, made here, independent of the toolkits.
# One file of apt's, after the image's own (Armbian's
# 02-armbian-periodic switches apt's periodic work off).
AUTOUPDATE_CONF = Path(os.environ.get("HUB_AUTOUPDATE_CONF", "/etc/apt/apt.conf.d/52irate-box-autoupdate"))
KIT_POOL = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits")) / "pool"
APT_TIMERS = ("apt-daily.timer", "apt-daily-upgrade.timer")
# The one pattern for every update: how often to look (hours; 0 Manual, only when Check now is pressed) and what to do with what is found (0 Flag, 1
# Fetch, 2 Install). The same numbers as the librarian's policy, the same chips on every surface.
OFTEN = (0, 6, 24, 168)
ACTS = (0, 1, 2)
OFTEN_WORDS = {0: "by hand only", 6: "every 6 hours", 24: "every day", 168: "every week"}
DEFAULT_PATTERN = (24, 0)  # the safe one
# The three earlier choices, read as the pattern's.
LEVELS_WAS = {"off": (0, 0), "download": (24, 1), "install": (24, 2)}
# apt's periodic intervals are in days; "always" leaves the timing to the timers (apt.systemd.daily).
INTERVAL = {6: "always", 24: "1", 168: "7"}
# Every 6 hours needs apt's timers to run that often (they run twice a day as Debian ships them).
TIMER_DROPIN = Path(os.environ.get("HUB_SYSTEMD_DIR", "/etc/systemd/system"))
TIMER_SIX = {"apt-daily.timer": "*-*-* 00/6:00", "apt-daily-upgrade.timer": "*-*-* 01/6:30"}
APT_ARCHIVES = Path(os.environ.get("HUB_APT_ARCHIVES", "/var/cache/apt/archives"))


def periodic_conf(often, act):
    """apt.conf lines for a choice of the pattern."""
    if not often:
        return 'APT::Periodic::Enable "0";\n'
    iv = INTERVAL[often]
    return (
        f'APT::Periodic::Enable "1";\nAPT::Periodic::Update-Package-Lists "{iv}";\n'
        f'APT::Periodic::Download-Upgradeable-Packages "{iv if act >= 1 else "0"}";\n'
        f'APT::Periodic::Unattended-Upgrade "{iv if act >= 2 else "0"}";\n'
    )


# What the box does with the security updates it finds, as the end of a sentence.
ACT_DOES = {0: "flags what it finds", 1: "downloads what it finds, for you to install", 2: "installs what it finds"}


def pattern_words(often, act):
    """The pattern as a clause, for the comment in apt's file ("// Written by …"); the page says PATTERN_SAYS."""
    return f"the box checks for security updates {OFTEN_WORDS[often]} and {ACT_DOES[act]}"


def chosen_pattern(rec):
    """(often, act) as chosen on the Updates page, or None; the earlier levels read as theirs."""
    cur = rec.get("autoupdate") or {}
    if cur.get("often") in OFTEN and cur.get("act") in ACTS:
        return cur["often"], cur["act"]
    return LEVELS_WAS.get(cur.get("level"))


def parse_pattern(arg):
    """'24-1' (from the page's choice autoupdate-set:24-1) as (24, 1); a closed set, else ValueError."""
    try:
        often, act = (int(x) for x in arg.split("-"))
    except ValueError:
        raise Refused(
            M(
                "security-fix.pattern.bad",
                "This is not a choice of how often to check and what to do: {choice}.",
                choice=arg,
            )
        ) from None
    if often not in OFTEN or act not in ACTS:
        raise Refused(
            M(
                "security-fix.pattern.bad",
                "This is not a choice of how often to check and what to do: {choice}.",
                choice=arg,
            )
        )
    return often, act


def pending_security(simulated):
    """Package names from `apt-get -s upgrade` output whose new version comes from a security archive,
    or is one of Debian's stable fixes (from a Debian origin, versioned +debNuM)."""
    pkgs = []
    for line in simulated.splitlines():
        m = INST_RE.match(line)
        if not m:
            continue
        version, _, origin = m.group(2).partition(" ")
        if "security" in origin.lower() or (origin.startswith("Debian") and DEB_FIX_RE.search(version)):
            pkgs.append(m.group(1))
    return pkgs


def lists_age_days():
    """How old apt's package lists are (their folder's newest change), or None."""
    try:
        return (time.time() - max(p.stat().st_mtime for p in APT_LISTS.glob("*_InRelease"))) / 86400
    except (OSError, ValueError):
        return None


UNATTENDED_LOG = Path(os.environ.get("HUB_UNATTENDED_LOG", "/var/log/unattended-upgrades/unattended-upgrades.log"))
# apt's own record of the last run, on the card: the log is in /var/log, which Armbian keeps in RAM, so it is gone after
# a reboot (a board said "it has not run yet" a day after it ran).
UNATTENDED_STAMP = Path(os.environ.get("HUB_UNATTENDED_STAMP", "/var/lib/apt/periodic/unattended-upgrades-stamp"))


def _apt_periodic():
    """{key: value} of APT::Periodic::Enable and ::Unattended-Upgrade as apt sees them (every
    apt.conf.d file merged), or {} with no apt-config."""
    if not _have("apt-config"):
        return {}
    out = run("apt-config", "dump", "--format", "%f=%v%n", "APT::Periodic::Enable", "APT::Periodic::Unattended-Upgrade")
    return dict(ln.split("=", 1) for ln in out.stdout.splitlines() if "=" in ln)


# The update pattern as a whole sentence for each (how often, what to do) that runs on its own.
PATTERN_SAYS = {
    (6, 0): M("pattern.6h-flag", "The box checks for security updates every 6 hours and flags what it finds."),
    (6, 1): M(
        "pattern.6h-fetch",
        "The box checks for security updates every 6 hours and downloads what it finds, for you to install.",
    ),
    (6, 2): M("pattern.6h-install", "The box checks for security updates every 6 hours and installs what it finds."),
    (24, 0): M("pattern.day-flag", "The box checks for security updates every day and flags what it finds."),
    (24, 1): M(
        "pattern.day-fetch",
        "The box checks for security updates every day and downloads what it finds, for you to install.",
    ),
    (24, 2): M("pattern.day-install", "The box checks for security updates every day and installs what it finds."),
    (168, 0): M("pattern.week-flag", "The box checks for security updates every week and flags what it finds."),
    (168, 1): M(
        "pattern.week-fetch",
        "The box checks for security updates every week and downloads what it finds, for you to install.",
    ),
    (168, 2): M("pattern.week-install", "The box checks for security updates every week and installs what it finds."),
}
# Manual: what Check now does with what it finds.
MANUAL_SAYS = {
    0: M("pattern.manual-flag", "Manual, as you selected. When you select Check now, the box flags what it finds."),
    1: M(
        "pattern.manual-fetch",
        "Manual, as you selected. When you select Check now, the box downloads what it finds, for you to install.",
    ),
    2: M(
        "pattern.manual-install", "Manual, as you selected. When you select Check now, the box installs what it finds."
    ),
}


def _last_ran(log_age_days, last_at):
    n = round(log_age_days)
    if last_at:
        return M(
            "unattended.last-ran-at",
            {"one": "It last ran on {when}, {n} day ago.", "other": "It last ran on {when}, {n} days ago."},
            n=n,
            when=when(last_at),
        )
    return M("unattended.last-ran", {"one": "It last ran {n} day ago.", "other": "It last ran {n} days ago."}, n=n)


def unattended_finding(installed, periodic, log_age_days, chosen=None, last_at=None):
    """Automatic security updates: the owner's choice on the Updates page (how often to look, what to do
    with what is found: the update pattern) and, once installing, whether it really runs (Armbian
    images ship APT::Periodic::Enable "0", which switches the whole of apt's
    periodic work off whatever Unattended-Upgrade says, so the binary being there meant nothing).
    chosen: (often, act) or None. The choice itself is made with the chips on Updates, not buttons here."""
    title = M("unattended.title", "Automatic security updates")
    acts = [action("autoupdate-undo", M("unattended.undo", "Restore the image's settings"))] if chosen else []
    running = (
        installed
        and periodic.get("APT::Periodic::Enable", "1") != "0"
        and periodic.get("APT::Periodic::Unattended-Upgrade", "0") not in ("0", "")
    )
    if not chosen and running:
        # Debian's own package can switch it on when installed (20auto-upgrades): it runs, but was not chosen here.
        return finding(
            "unattended",
            title,
            "ok",
            M("unattended.by-image", "Enabled, but not from this page. Debian's own package or the image enabled it."),
            notes=[
                M(
                    "unattended.by-image.does",
                    "The box installs security updates every day while it has internet. "
                    "Select below to keep this or to change it.",
                )
            ],
            actions=acts,
        )
    if not chosen:
        return finding(
            "unattended",
            title,
            "warn",
            M(
                "unattended.unset",
                "Not chosen yet. Security updates wait until someone checks for them and installs them.",
            ),
            fix=M(
                "unattended.unset.fix",
                "Select below how often the box checks for them, and what it does with what it finds.",
            ),
            notes=[
                M(
                    "unattended.unset.can",
                    "The box can check every 6 hours, every day or every week. "
                    "It can flag, download or install what it finds.",
                )
            ],
            actions=acts,
        )
    often, act = chosen
    if not often:
        return finding("unattended", title, "ok", MANUAL_SAYS[act], actions=acts)
    said = PATTERN_SAYS[(often, act)]
    save_again = M("unattended.save-again", "Save the setting again below.")
    if periodic.get("APT::Periodic::Enable", "1") == "0":
        return finding(
            "unattended",
            title,
            "warn",
            said,
            M(
                "unattended.disabled-again",
                "But it does not run: another file in /etc/apt/apt.conf.d disables apt's periodic work.",
            ),
            save_again,
            evidence=['APT::Periodic::Enable "0"'],
            actions=acts,
        )
    if act < 2:
        return finding(
            "unattended",
            title,
            "ok",
            said,
            notes=[M("unattended.needs-internet", "It does this while the box has internet.")],
            actions=acts,
        )
    value = periodic.get("APT::Periodic::Unattended-Upgrade", "")
    if not installed:
        return finding(
            "unattended",
            title,
            "warn",
            said,
            M("unattended.not-installed", "But unattended-upgrades, which installs them, is not installed."),
            save_again,
            actions=acts,
        )
    if value in ("0", ""):
        return finding(
            "unattended",
            title,
            "warn",
            said,
            M("unattended.would-not-run", "But unattended-upgrades, which installs them, does not run."),
            save_again,
            evidence=[f"APT::Periodic::Unattended-Upgrade {value or 'unset'}"],
            actions=acts,
        )
    if log_age_days is None:
        return finding(
            "unattended",
            title,
            "ok",
            said,
            notes=[M("unattended.not-run", "It has not run yet. It runs when the box has internet.")],
            actions=acts,
        )
    if log_age_days > max(14, often / 24 * 2):
        return finding(
            "unattended",
            title,
            "warn",
            said,
            M("unattended.stale.why", "It runs only when the box has internet, so updates can wait for a long time."),
            M("unattended.stale.fix", "If the box has no internet, install the waiting updates on this page."),
            notes=[_last_ran(log_age_days, last_at)],
            actions=acts,
        )
    return finding("unattended", title, "ok", said, notes=[_last_ran(log_age_days, last_at)], actions=acts)


def _timers_six(rec_timers, on):
    """apt's two timers every 6 hours (a drop-in of ours each), or as Debian ships them."""
    changed = False
    for timer, schedule in TIMER_SIX.items():
        drop = TIMER_DROPIN / f"{timer}.d" / "52irate-box.conf"
        if on:
            drop.parent.mkdir(parents=True, exist_ok=True)
            drop.write_text(
                "# Written by irate-box's Updates page: look for security updates every 6 hours.\n"
                f"[Timer]\nOnCalendar=\nOnCalendar={schedule}\nRandomizedDelaySec=30min\n"
            )
            changed = True
        elif drop.exists():
            drop.unlink()
            changed = True
    if changed:
        run("systemctl", "daemon-reload")
    return changed


def _autoupdate(rec, how):
    """The owner's choice of automatic security updates (how: "undo", or "OFTEN-ACT"; the earlier "off",
    "download" and "install" read as theirs): one apt.conf.d file of ours, apt's timers on (every 6
    hours by a drop-in when chosen), and unattended-upgrades installed for installing (from Debian, or
    the System toolkit's cache when offline). Undo puts back the image's own: the file as it was, the
    timers as they were, the package removed if it was installed here."""
    cur = rec.get("autoupdate")
    if how == "undo":
        if not cur:
            raise Refused(
                M(
                    "security-fix.autoupdate.not-set",
                    "Automatic security updates were not set from this page. There is nothing to restore.",
                )
            )
        if cur.get("conf") is not None:
            AUTOUPDATE_CONF.write_text(cur["conf"])
        else:
            AUTOUPDATE_CONF.unlink(missing_ok=True)
        _timers_six(None, False)
        for timer, was in (cur.get("timers") or {}).items():
            if not was:
                _systemctl("disable", "--now", timer)
        if cur.get("installed"):
            run("apt-get", "remove", "-y", "unattended-upgrades", timeout=600)
        rec.pop("autoupdate")
        return M("security-fix.autoupdate.restored", "Automatic security updates are back as the image had them.")
    often, act = LEVELS_WAS[how] if how in LEVELS_WAS else parse_pattern(how)
    if not cur:
        cur = {
            "date": time.strftime("%Y-%m-%d"),
            "conf": AUTOUPDATE_CONF.read_text() if AUTOUPDATE_CONF.is_file() else None,
            "timers": {},
            "installed": False,
        }
    said = []
    if act == 2 and often and not _have("unattended-upgrade"):
        env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
        code = _installed_from(["apt-get", "install", "-y", "unattended-upgrades"], env)
        if code != 0:
            debs = sorted(KIT_POOL.glob("unattended-upgrades_*.deb")) if KIT_POOL.is_dir() else []
            if not debs or _installed_from(["apt-get", "install", "-y", str(debs[-1])], env) != 0:
                raise Refused(
                    M(
                        "security-fix.unattended.failed",
                        "The box could not install unattended-upgrades. Connect the box to the "
                        "internet once, or refresh the cache of the System toolkit on Toolkits.",
                    )
                )
            said.append(
                M(
                    "security-fix.unattended.installed-cache",
                    "The box installed unattended-upgrades from the toolkits' cache.",
                )
            )
        else:
            said.append(M("security-fix.unattended.installed", "The box installed unattended-upgrades."))
        cur["installed"] = True
    AUTOUPDATE_CONF.parent.mkdir(parents=True, exist_ok=True)
    AUTOUPDATE_CONF.write_text(
        "// Written by irate-box's Updates page (/admin): "
        f"{pattern_words(often, act)}. Change it there.\n" + periodic_conf(often, act)
    )
    _timers_six(cur["timers"], often == 6)
    if often:
        for timer in APT_TIMERS:
            if timer not in cur["timers"]:
                cur["timers"][timer] = run("systemctl", "is-enabled", timer).stdout.strip() == "enabled"
            _systemctl("enable", "--now", timer)
        if often == 6:
            for timer in APT_TIMERS:
                run("systemctl", "restart", timer)
    cur.pop("level", None)
    cur["often"], cur["act"] = often, act
    rec["autoupdate"] = cur
    return sentences(*said, PATTERN_SAYS.get((often, act)) or MANUAL_SAYS[act])


def fetched_debs(simulated):
    """How many of the waiting security updates are downloaded already (apt's archives hold their .deb)."""
    n = 0
    for line in simulated.splitlines():
        m = INST_RE.match(line)
        if m and m.group(1) in pending_security(line):
            ver = m.group(2).partition(" ")[0].replace(":", "%3a")
            n += any(APT_ARCHIVES.glob(f"{m.group(1)}_{ver}_*.deb"))
    return n


def update_findings():
    if not _have("apt-get"):
        return [], []
    out = run("apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade", timeout=180)
    pkgs = pending_security(out.stdout)
    fetched = fetched_debs(out.stdout) if pkgs else 0
    # The last run: apt's stamp (kept on the card), else unattended-upgrades' own log.
    last_at = None
    for f in (UNATTENDED_STAMP, UNATTENDED_LOG):
        try:
            last_at = f.stat().st_mtime
            break
        except OSError:
            continue
    log_age = (time.time() - last_at) / 86400 if last_at else None
    age = lists_age_days()
    findings = [security_updates_finding(pkgs, age)]
    findings[-1]["lists_age_days"] = age  # the page's badge: how recently "none waiting" was looked for
    findings[-1]["waiting"], findings[-1]["fetched"] = len(pkgs), fetched  # the pattern's Fetch and Install
    findings[-1]["packages"] = pkgs  # the card's list of what is waiting (its state; the finding is the doctor's)
    chosen = chosen_pattern(load_record())
    findings.append(unattended_finding(_have("unattended-upgrade"), _apt_periodic(), log_age, chosen, last_at))
    findings[-1]["last_at"] = last_at  # the card's "last ran", as a time
    findings[-1]["pattern"] = {
        "often": (chosen or DEFAULT_PATTERN)[0],
        "act": (chosen or DEFAULT_PATTERN)[1],
        "chosen": bool(chosen),
    }
    return findings, pkgs


def security_updates_finding(pkgs, age):
    """Debian's security updates waiting, and how fresh the package lists are that say so."""
    fresh = (
        M("updates.lists.today", "The package lists are from today.")
        if age is not None and age < 1
        else M(
            "updates.lists.age",
            {
                "one": "The package lists are {n} day old. Newer fixes can be available.",
                "other": "The package lists are {n} days old. Newer fixes can be available.",
            },
            n=round(age),
        )
        if age is not None
        else M("updates.lists.unknown", "The age of the package lists is not known.")
    )
    look = action("apt-lists", M("updates.lists.refresh", "Refresh the package lists (needs internet)"))
    title = M("updates.title", "Security updates")
    if not pkgs:
        return finding(
            "security-updates",
            title,
            "ok",
            M("updates.none", "No security updates are waiting."),
            notes=[
                fresh,
                M(
                    "updates.none.limit",
                    "This means only that the package lists have no newer fix. "
                    "It does not prove that the installed packages have no weaknesses.",
                ),
            ],
            actions=[look],
        )
    return finding(
        "security-updates",
        title,
        "problem",
        M(
            "updates.waiting",
            {"one": "{n} security update is waiting.", "other": "{n} security updates are waiting."},
            n=len(pkgs),
        ),
        M(
            "updates.waiting.why",
            "They come from Debian's security archive or its stable fixes, which fix known security problems.",
        ),
        M(
            "updates.waiting.fix",
            "Install them. Other packages change only where these need a newer version. "
            "The box's own updates are separate, on Updates.",
        ),
        notes=[fresh],
        evidence=pkgs,
        actions=[
            action(
                "security-updates",
                M(
                    "updates.install",
                    {"one": "Install {n} security update", "other": "Install {n} security updates"},
                    n=len(pkgs),
                ),
                M(
                    "updates.install.confirm",
                    "This can take several minutes on this board.",
                ),
            ),
            look,
        ],
    )


# --- scan ---------------------------------------------------------------------------------

# --- the kernel's settings, and accounts in root's groups ---------------------------------------

# Each set is one button. The values are the ones Debian ships in its own kernels; the image's
# vendor kernel leaves them off.
KERNEL = {
    "links": {
        "title": M("kernel-links.title", "Kernel link protections"),
        "status": "problem",
        "keys": {"fs.protected_symlinks": "1", "fs.protected_hardlinks": "1"},
        "off": M("kernel-links.off", "The kernel's link protections are not all on."),
        "why": M(
            "kernel-links.why",
            "The kernel follows links in shared folders such as /tmp across users. "
            "A program that writes there as root can then be made to write somewhere else.",
        ),
        "label": M("kernel-links.on", "Enable kernel link protections"),
    },
    "info": {
        "title": M("kernel-info.title", "Kernel addresses and log"),
        "status": "warn",
        "keys": {"kernel.kptr_restrict": "1", "kernel.dmesg_restrict": "1"},
        "off": M("kernel-info.off", "Any account can read the kernel's log and the addresses of its code."),
        "why": M("kernel-info.why", "The addresses and the log help an attacker aim an exploit at the kernel."),
        "label": M("kernel-info.on", "Hide them from ordinary accounts"),
    },
}


# Debian's own kernel defaults: the link protections and the rest a stock Debian box has (FIFOs and
# regular files in shared folders, source routes refused, the SysRq mask). Armbian builds leave
# recommended packages out, so mPWRD-OS images lack it (Armbian has since fixed it upstream).
# Offered before the hub's own file wherever apt can get it.
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
        shown = [f"{key}={v}" for key, v in now.items()]
        if all(now[key] == want for key, want in k["keys"].items()):
            out.append(
                finding(
                    f"kernel-{name}",
                    k["title"],
                    "ok",
                    M("kernel.on", "On, as Debian sets them."),
                    evidence=shown,
                    actions=[action(f"kernel-{name}-undo", UNDO)] if name in ours else [],
                )
            )
        elif name == "links" and debian_sysctl() == "available":
            out.append(
                finding(
                    f"kernel-{name}",
                    k["title"],
                    k["status"],
                    k["off"],
                    k["why"],
                    M(
                        "kernel-links.debian.fix",
                        "Install Debian's own kernel defaults, or let the box set just these two. "
                        "Either one applies now and at every boot.",
                    ),
                    notes=[
                        M(
                            "kernel-links.debian.note",
                            "Debian's defaults ({package}) set these and the other settings that a "
                            "standard Debian box has. The box writes just these two in /etc/sysctl.d.",
                            package=DEBIAN_SYSCTL,
                        )
                    ],
                    evidence=shown,
                    actions=[
                        action(
                            "kernel-links-debian",
                            M("kernel-links.debian", "Install Debian's defaults"),
                            M(
                                "kernel-links.debian.confirm",
                                "This installs {package} from Debian and applies it at once. It also restricts FIFOs and files in shared folders and refuses source routes, as on any Debian box.",
                                package=DEBIAN_SYSCTL,
                            ),
                        ),
                        action(f"kernel-{name}-on", M("kernel-links.just-two", "Set just these two")),
                    ],
                )
            )
        else:
            out.append(
                finding(
                    f"kernel-{name}",
                    k["title"],
                    k["status"],
                    k["off"],
                    k["why"],
                    M("kernel.fix", "Set them now and at every boot. The box writes a file in /etc/sysctl.d."),
                    evidence=shown,
                    actions=[action(f"kernel-{name}-on", k["label"])],
                )
            )
    return out


def _write_sysctl_dropin(kernel):
    keys = {key: want for name, e in kernel.items() if not e.get("by") for key, want in KERNEL[name]["keys"].items()}
    if not keys:
        SYSCTL_DROPIN.unlink(missing_ok=True)
        return
    SYSCTL_DROPIN.parent.mkdir(parents=True, exist_ok=True)
    SYSCTL_DROPIN.write_text(
        "# Written by irate-box's Security page (/admin). Undo there, or delete this file.\n"
        + "".join(f"{key} = {v}\n" for key, v in keys.items())
    )


def _kernel(rec, name, on):
    if name not in KERNEL:
        raise Refused(
            M("security-fix.kernel.unknown", "{name} is not a kernel setting that this page changes.", name=name)
        )
    kernel = rec.setdefault("kernel", {})
    if on:
        old = kernel.get(name, {}).get("old") or {key: _sysctl(key) for key in KERNEL[name]["keys"]}
        for key, want in KERNEL[name]["keys"].items():
            _sysctl_path(key).write_text(want + "\n")
        kernel[name] = {"old": old, "at": time.strftime("%Y-%m-%d")}
        _write_sysctl_dropin(kernel)
        return M("security-fix.kernel.on", "{setting}: on now and at every boot.", setting=KERNEL[name]["title"])
    entry = kernel.pop(name, None)
    if entry is None:
        raise Refused(
            M(
                "security-fix.kernel.not-changed",
                "{setting}: not changed from this page. There is nothing to restore.",
                setting=KERNEL[name]["title"],
            )
        )
    if entry.get("by") == DEBIAN_SYSCTL:
        out = run("apt-get", "remove", "-y", DEBIAN_SYSCTL, timeout=600)
        if out.returncode != 0:
            kernel[name] = entry
            raise Refused(
                M(
                    "security-fix.kernel.remove-failed",
                    "apt-get could not remove {package}: {detail}",
                    package=DEBIAN_SYSCTL,
                    detail=(out.stderr or out.stdout).strip()[-200:],
                )
            )
    for key, v in (entry.get("old") or {}).items():
        if v is not None:
            _sysctl_path(key).write_text(v + "\n")
    _write_sysctl_dropin(kernel)
    if not kernel:
        rec.pop("kernel")
    return M(
        "security-fix.kernel.restored", "{setting}: restored to the previous values.", setting=KERNEL[name]["title"]
    )


def _kernel_debian(rec):
    """Debian's own defaults installed and applied (systemd-sysctl reads every sysctl.d file again)."""
    kernel = rec.setdefault("kernel", {})
    old = kernel.get("links", {}).get("old") or {key: _sysctl(key) for key in KERNEL["links"]["keys"]}
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    try:
        out = subprocess.run(  # noqa: TID251
            ["apt-get", "install", "-y", "--no-install-recommends", DEBIAN_SYSCTL],
            capture_output=True,
            text=True,
            env=env,
            stdin=subprocess.DEVNULL,
            timeout=600,
        )
    except OSError as exc:
        out = subprocess.CompletedProcess([], 127, "", str(exc))
    if out.returncode != 0:
        raise Refused(
            M(
                "security-fix.kernel.install-failed",
                "apt-get could not install {package}: {detail} "
                "To set just these two without the package, select Set just these two.",
                package=DEBIAN_SYSCTL,
                detail=(out.stderr or out.stdout).strip()[-200:],
            )
        )
    kernel["links"] = {"old": old, "at": time.strftime("%Y-%m-%d"), "by": DEBIAN_SYSCTL}
    _write_sysctl_dropin(kernel)  # the hub's own file no longer carries these two
    save_record(rec)  # installed: Undo must be possible even if what follows fails
    _systemctl("restart", "systemd-sysctl.service")
    now = {key: _sysctl(key) for key in KERNEL["links"]["keys"]}
    if any(now[key] != want for key, want in KERNEL["links"]["keys"].items()):
        raise Refused(
            M(
                "security-fix.kernel.still-off",
                "{package} is installed, but the link protections are still off: {values}. "
                "A later file in sysctl.d sets them back.",
                package=DEBIAN_SYSCTL,
                values=[f"{k}={v}" for k, v in now.items()],
            )
        )
    return M(
        "security-fix.kernel.debian",
        "{setting}: on now and at every boot, from the Debian defaults ({package}).",
        setting=KERNEL["links"]["title"],
        package=DEBIAN_SYSCTL,
    )


# Groups that are root by another name: docker runs containers as root with any folder mounted;
# disk reads and writes the raw card, password file and all.
ROOTISH_GROUPS = ("docker", "disk")
NAME_RE = re.compile(r"^[a-z_][a-z0-9_.-]{0,31}$")


def _login_accounts():
    out = set()
    for line in (PASSWD_FILE.read_text(errors="replace") if PASSWD_FILE.exists() else "").splitlines():
        f = line.split(":")
        if (
            len(f) >= 7
            and f[2].isdigit()
            and int(f[2]) >= 1000
            and f[0] != "nobody"
            and not f[6].endswith(("nologin", "false"))
        ):
            out.add(f[0])
    return out


def _group_members():
    out = {}
    for line in (GROUP_FILE.read_text(errors="replace") if GROUP_FILE.exists() else "").splitlines():
        f = line.split(":")
        if len(f) >= 4:
            out[f[0]] = {m for m in f[3].split(",") if m}
    return out


GROUP_WHY = {
    "docker": M(
        "group-docker.why",
        "Members of the docker group can run containers as root with any folder of the box mounted. "
        "That is a route to root without a password.",
    ),
    "disk": M(
        "group-disk.why",
        "Members of the disk group can read and write the raw storage, the password file included. "
        "That is a route to root without a password.",
    ),
}


def group_findings(rec):
    out, ours = [], rec.get("groups", {})
    members, logins = _group_members(), _login_accounts()
    for group in ROOTISH_GROUPS:
        if group not in members:
            continue
        risky = sorted(members[group] & logins)
        title = M("group.title", "The {group} group", group=group)
        # Each account in it, or taken out here, with both values: in the group, or not.
        removed = sorted(k.split("@")[0] for k in ours if k.endswith("@" + group))
        undo = []
        for u in sorted(set(members[group] & logins) | set(removed)):
            label = M("group.account", "The {group} group: {account}", account=u, group=group)
            if u in removed and u not in members[group]:
                undo += [
                    _chip(label, f"group-undo:{u}@{group}", M("group.member", "In the group"), False),
                    _chip(label, f"group-drop:{u}@{group}", M("group.not-member", "Not in the group"), True),
                ]
            else:
                undo += [
                    _chip(label, f"group-undo:{u}@{group}", M("group.member", "In the group"), True),
                    _chip(
                        label,
                        f"group-drop:{u}@{group}",
                        M("group.not-member", "Not in the group"),
                        False,
                        M(
                            "group.drop.confirm",
                            "{account} leaves the {group} group from its next sign-in.",
                            account=u,
                            group=group,
                        ),
                    ),
                ]
        if not risky:
            out.append(
                finding(
                    f"group-{group}",
                    title,
                    "ok",
                    M("group.none", "No account that can sign in is in it."),
                    actions=undo,
                )
            )
            continue
        out.append(
            finding(
                f"group-{group}",
                title,
                "warn",
                M(
                    "group.members",
                    {
                        "one": "{n} account that can sign in is in it.",
                        "other": "{n} accounts that can sign in are in it.",
                    },
                    n=len(risky),
                ),
                GROUP_WHY[group],
                M(
                    "group.fix",
                    "Remove each account that does not need it. The change applies from the account's next sign-in.",
                ),
                evidence=risky,
                actions=undo,
            )
        )
    return out


def _group(rec, arg, on):
    user, _, group = arg.partition("@")
    if group not in ROOTISH_GROUPS or not NAME_RE.match(user):
        raise Refused(
            M("security-fix.group.unknown", "{choice} is not an account and group that this page changes.", choice=arg)
        )
    groups = rec.setdefault("groups", {})
    if on:
        if user not in _group_members().get(group, set()):
            raise Refused(
                M("security-fix.group.not-member", "{account} is not in the {group} group.", account=user, group=group)
            )
        r = run("gpasswd", "-d", user, group)
        if r.returncode:
            raise Refused(
                M(
                    "security-fix.gpasswd-failed",
                    "gpasswd failed: {detail}",
                    detail=(r.stderr or r.stdout).strip()[:160],
                )
            )
        groups[arg] = time.strftime("%Y-%m-%d")
        return M(
            "security-fix.group.removed",
            "Account removed from privileged group: {account} is out of the {group} group from its next sign-in.",
            account=user,
            group=group,
        )
    if arg not in groups:
        raise Refused(
            M(
                "security-fix.group.not-removed",
                "{account} was not removed from the {group} group on this page.",
                account=user,
                group=group,
            )
        )
    r = run("gpasswd", "-a", user, group)
    if r.returncode:
        raise Refused(
            M("security-fix.gpasswd-failed", "gpasswd failed: {detail}", detail=(r.stderr or r.stdout).strip()[:160])
        )
    groups.pop(arg)
    if not groups:
        rec.pop("groups")
    return M("security-fix.group.restored", "{account} is back in the {group} group.", account=user, group=group)


# --- root's own login ---------------------------------------------------------------------------


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
    lock = (
        [
            action(
                "root-lock",
                M("root-password.lock", "Lock root's password"),
                M(
                    "root-lock.confirm",
                    "Root can no longer sign in with a password, at the console or over SSH. These accounts keep sudo: {accounts}. Keys for root and open root sessions do not change.",
                    accounts=admins,
                ),
            )
        ]
        if admins
        else []
    )
    no_lock = (
        []
        if admins
        else [
            M(
                "root-lock.no-sudo",
                "No other account here can use sudo, so this page does not lock the password. "
                "Add such an account first.",
            )
        ]
    )
    lock_fix = M("root-password.lock.fix", "Lock the password, so that root cannot sign in with a password.")
    undo = [action("root-lock-undo", UNDO)] if "lock" in ours else []
    title = M("root-password.title", "Root's password")
    if state == "L":
        out.append(
            finding(
                "root-password",
                title,
                "ok",
                M("root-password.locked", "Locked: root cannot sign in with a password."),
                notes=[M("root-password.locked.note", "A key for root, and sudo, still reach root.")],
                actions=undo,
            )
        )
    elif state == "NP":
        out.append(
            finding(
                "root-password",
                title,
                "problem",
                M("root-password.none", "Root has no password."),
                M("root-password.none.why", "Anyone at the console, or wherever root can sign in, is root."),
                lock_fix,
                notes=no_lock,
                actions=lock,
            )
        )
    elif state == "P" and firstrun:
        out.append(
            finding(
                "root-password",
                title,
                "problem",
                M(
                    "root-password.image",
                    "Root has a password, and Armbian's first sign-in setup never ran. "
                    "The password is probably the image's default, usually 1234.",
                ),
                M("root-password.image.why", "Anyone who knows the default password can act as root."),
                lock_fix,
                notes=no_lock,
                actions=lock,
            )
        )
    elif state == "P":
        out.append(
            finding(
                "root-password",
                title,
                "warn",
                M("root-password.set", "Root has a password of its own."),
                M(
                    "root-password.set.why",
                    "That is acceptable if you selected it and it is strong. "
                    "Anyone who learns it can sign in as root wherever root sign-in is enabled.",
                ),
                M(
                    "root-password.set.fix",
                    "If you do not use it, lock the password, so that root cannot sign in with a password.",
                ),
                notes=no_lock,
                actions=lock,
            )
        )
    title = M("root-firstrun.title", "First sign-in setup")
    if firstrun:
        out.append(
            finding(
                "root-firstrun",
                title,
                "problem",
                M("root-firstrun.on", "Armbian's first sign-in setup never ran."),
                M(
                    "root-firstrun.on.why",
                    "The image's defaults can still apply. The next root sign-in starts the setup, "
                    "which asks for a new root password and a new user.",
                ),
                M(
                    "root-firstrun.on.fix",
                    "If you set up this box another way, put the marker aside. "
                    "The box keeps a copy, so that you can put it back.",
                ),
                evidence=[str(FIRSTRUN)],
                actions=[action("firstrun-off", M("root-firstrun.off", "Put the marker aside"))],
            )
        )
    elif "firstrun" in ours:
        out.append(
            finding(
                "root-firstrun",
                title,
                "ok",
                M("root-firstrun.aside", "Its marker is put aside."),
                actions=[action("firstrun-undo", M("root-firstrun.undo", "Put the marker back"))],
            )
        )
    return out


def _root(rec, what, on):
    root = rec.setdefault("root", {})
    if what == "lock":
        if on:
            if not _sudoers():
                raise Refused(
                    M(
                        "security-fix.root.no-sudo",
                        "No other account here can use sudo. If root's password is locked, no account can become root.",
                    )
                )
            was = _root_password()
            r = run("passwd", "-l", "root")
            if r.returncode:
                raise Refused(
                    M(
                        "security-fix.passwd-failed",
                        "passwd failed: {detail}",
                        detail=(r.stderr or r.stdout).strip()[:160],
                    )
                )
            root["lock"] = {"was": was, "at": time.strftime("%Y-%m-%d")}
            msg = M(
                "security-fix.root.locked",
                "Root password authentication disabled: root cannot sign in with a password. "
                "SSH keys for root, sudo and sessions that are open still give root access.",
            )
        else:
            entry = root.pop("lock", None)
            if entry is None:
                raise Refused(
                    M(
                        "security-fix.root.not-locked",
                        "Root's password was not locked from this page. There is nothing to restore.",
                    )
                )
            if entry.get("was") in ("P", "NP"):
                r = run("passwd", "-u", "root")
                if r.returncode:
                    root["lock"] = entry
                    raise Refused(
                        M(
                            "security-fix.passwd-failed",
                            "passwd failed: {detail}",
                            detail=(r.stderr or r.stdout).strip()[:160],
                        )
                    )
            msg = M("security-fix.root.restored", "Root's password is restored to its previous state.")
    else:
        kept = ETC / "armbian-firstrun.kept"
        if on:
            if not FIRSTRUN.exists():
                raise Refused(M("security-fix.firstrun.none", "There is no first sign-in marker to put aside."))
            kept.write_bytes(FIRSTRUN.read_bytes())
            kept.chmod(0o600)
            FIRSTRUN.unlink()
            root["firstrun"] = time.strftime("%Y-%m-%d")
            msg = M(
                "security-fix.firstrun.off",
                "First sign-in wizard disabled: the box put its marker aside. A root sign-in no longer "
                "starts the Armbian setup.",
            )
        else:
            if root.pop("firstrun", None) is None or not kept.exists():
                raise Refused(
                    M(
                        "security-fix.firstrun.not-off",
                        "The first sign-in marker was not put aside from this page. There is nothing to restore.",
                    )
                )
            FIRSTRUN.write_bytes(kept.read_bytes())
            kept.unlink()
            msg = M("security-fix.firstrun.restored", "The first sign-in marker is restored.")
    if not root:
        rec.pop("root")
    return msg


# --- sudo rules, apt's trust, logs in RAM ---------------------------------------------------------
# What the image left that the other offers do not reach: a passwordless sudo rule for
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
    files = ([SUDOERS] if SUDOERS.is_file() else []) + (
        sorted(p for p in SUDOERS_DIR.iterdir() if p.is_file() and "." not in p.name and not p.name.endswith("~"))
        if SUDOERS_DIR.is_dir()
        else []
    )
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


def _sudo_chips(f, on):
    """A sudoers.d file's passwordless rule as a row of chips: On (the file in place), Off (moved aside here)."""
    group = M("sudo.group", "Passwordless sudo ({file})", file=f)
    return [
        _chip(group, f"sudo-undo:{f}", ON, on),
        _chip(
            group,
            f"sudo-drop:{f}",
            OFF,
            not on,
            M(
                "sudo.drop.confirm",
                "/etc/sudoers.d/{file} goes: sudo asks for the account's password again, and the account stays in the sudo group. On puts the file back.",
                file=f,
            ),
        ),
    ]


def sudo_findings(rec, settings=None):
    ours = rec.get("sudo", {})
    undo = [chip for f in ours for chip in _sudo_chips(f, False)]
    rules = _nopasswd_rules()
    if not rules:
        return [
            finding(
                "sudo-nopasswd",
                M("sudo.title", "Passwordless sudo"),
                "ok",
                M("sudo.none", "No NOPASSWD rule names an account that can sign in."),
                actions=undo,
            )
        ]
    settings = sshd_settings() if settings is None else settings
    guessable = bool(settings) and settings.get("passwordauthentication", "yes") == "yes"
    by_file = {}
    for f, _, a in rules:
        by_file.setdefault(f, set()).add(a)
    accounts = sorted({a for _, _, a in rules})
    drops = [f for f in by_file if f != SUDOERS.name and FILE_RE.match(f)]
    return [
        finding(
            "sudo-nopasswd",
            M("sudo.title-ssh", "Passwordless sudo, and SSH accepts passwords")
            if guessable
            else M("sudo.title", "Passwordless sudo"),
            "problem" if guessable else "warn",
            M(
                "sudo.rules",
                {
                    "one": "{n} account that can sign in can use sudo with no password.",
                    "other": "{n} accounts that can sign in can use sudo with no password.",
                },
                n=len(accounts),
            ),
            M(
                "sudo.rules.why-ssh",
                "Whoever signs in as one of them is root. SSH lets any device on the network try "
                "their passwords, so one correct guess is root.",
            )
            if guessable
            else M("sudo.rules.why", "Whoever signs in as one of them is root, with no second password."),
            M(
                "sudo.fix-ssh",
                "Remove the rule when the work that it was added for is done. Until then, disable SSH password sign-in (above).",
            )
            if guessable
            else M("sudo.fix", "Remove the rule when the work that it was added for is done."),
            evidence=[f"{f}: {', '.join(sorted(a))}" for f, a in by_file.items()],
            actions=[chip for f in drops for chip in _sudo_chips(f, True)]
            + [chip for chip in undo if chip["choice"].split(":", 1)[1] not in drops],
        )
    ]


def _sudo(rec, name, on):
    if not FILE_RE.match(name) or name == SUDOERS.name:
        raise Refused(
            M("security-fix.sudo.unknown", "{name} is not a file in sudoers.d that this page removes.", name=name)
        )
    ours = rec.setdefault("sudo", {})
    src, kept = SUDOERS_DIR / name, SUDOERS_KEPT / name
    if on:
        if not src.is_file():
            raise Refused(M("security-fix.sudo.missing", "{file} does not exist.", file=f"/etc/sudoers.d/{name}"))
        SUDOERS_KEPT.mkdir(mode=0o700, exist_ok=True)
        shutil.move(str(src), str(kept))
        ours[name] = time.strftime("%Y-%m-%d")
        return M(
            "security-fix.sudo.removed",
            "Passwordless sudo rule removed: the box moved {file} to {kept}, for Undo.",
            file=f"/etc/sudoers.d/{name}",
            kept=str(SUDOERS_KEPT),
        )
    if name not in ours or not kept.is_file():
        raise Refused(
            M(
                "security-fix.sudo.not-removed",
                "{name} was not removed from this page. There is nothing to restore.",
                name=name,
            )
        )
    shutil.move(str(kept), str(src))
    os.chmod(src, 0o440)
    ours.pop(name)
    if not ours:
        rec.pop("sudo")
    return M("security-fix.sudo.restored", "{file} is restored.", file=f"/etc/sudoers.d/{name}")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _key_for(stem, keys):
    """The trusted.gpg.d key whose name is the source's (OBS writes home_mPWRD_OS.gpg beside home:mPWRD:OS.list)."""
    return next((k for k in keys if _norm(k.rsplit(".", 1)[0]) == _norm(stem)), None)


def _apt_keys():
    trusted = APT_DIR / "trusted.gpg.d"
    return (
        sorted(p.name for p in trusted.iterdir() if p.is_file() and not p.name.startswith(DEBIAN_KEYS))
        if trusted.is_dir()
        else []
    )


def _unsigned_lists():
    from irate_box.root import secdoctor

    return [(f, uri) for f, uri, _, signed in secdoctor._apt_sources(APT_DIR) if not signed and f.endswith(".list")]


def apt_findings(rec):
    ours = rec.get("apt", {})
    keys, unsigned = _apt_keys(), _unsigned_lists()
    any_repo = M("apt.any", "Any repository")
    # One choice per key: what it may sign for, how it is now selected.
    undo = []
    for k in ours:
        undo += [
            _chip(
                k,
                f"apt-undo:{k}",
                any_repo,
                False,
                M("apt.undo.confirm", "{key} goes back into trusted.gpg.d, trusted for every repository again.", key=k),
            ),
            _chip(k, f"apt-tied:{k}", M("apt.own", "Only its own source"), True),
        ]
    if not keys and not unsigned:
        return [
            finding(
                "apt-trust",
                M("apt-trust.title", "Repository keys"),
                "ok",
                M("apt-trust.ok", "Each source names its own key. trusted.gpg.d holds only Debian's keys."),
                actions=undo,
            )
        ]
    actions = []
    for f, _ in unsigned:
        k = _key_for(f[: -len(".list")], keys)
        if k and FILE_RE.match(k):
            actions += [
                _chip(k, f"apt-any:{k}", any_repo, True),
                _chip(
                    k,
                    f"apt-signedby:{k}",
                    M("apt.only", "Only {source}", source=f),
                    False,
                    M(
                        "apt.signedby.confirm",
                        "{key} moves to /usr/share/keyrings and signs for {source} only. Any repository puts both back.",
                        key=k,
                        source=f,
                    ),
                ),
            ]
    unsigned_note = M(
        "apt-trust.unsigned",
        {"one": "{n} source does not name a key of its own.", "other": "{n} sources do not name a key of their own."},
        n=len(unsigned),
    )
    return [
        finding(
            "apt-trust",
            M("apt-trust.title-wide", "Repository keys trusted for every repository"),
            "warn",
            M(
                "apt-trust.keys",
                {
                    "one": "{n} key in trusted.gpg.d can sign packages for any repository, Debian's included.",
                    "other": "{n} keys in trusted.gpg.d can sign packages for any repository, Debian's included.",
                },
                n=len(keys),
            )
            if keys
            else unsigned_note,
            M(
                "apt-trust.why",
                "A key that apt trusts for every repository can sign a package that replaces one of Debian's.",
            ),
            M(
                "apt-trust.fix",
                "Bind each key to its own source (signed-by). If no source matches a key, check that nothing uses it, "
                "then remove it by hand.",
            ),
            notes=[unsigned_note] if keys and unsigned else [],
            evidence=keys + [f for f, _ in unsigned],
            actions=actions + undo,
        )
    ]


def _apt(rec, key, on):
    if not FILE_RE.match(key):
        raise Refused(M("security-fix.apt.unknown", "{key} is not a key that this page moves.", key=key))
    ours = rec.setdefault("apt", {})
    trusted, ring, kept = APT_DIR / "trusted.gpg.d" / key, KEYRINGS / key, KEYS_KEPT / key
    if on:
        lst = next(
            (APT_DIR / "sources.list.d" / f for f, _ in _unsigned_lists() if _key_for(f[: -len(".list")], [key])), None
        )
        if not trusted.is_file() or lst is None:
            raise Refused(
                M(
                    "security-fix.apt.not-applicable",
                    "{key} is not in trusted.gpg.d, or no source of its name lacks a key.",
                    key=key,
                )
            )
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
        return M(
            "security-fix.apt.restricted",
            "Repository signing key restricted: apt now trusts {key} for {source} only (signed-by). "
            "apt-get update reads it at the next refresh.",
            key=key,
            source=lst.name,
        )
    if key not in ours or not kept.is_file():
        raise Refused(
            M("security-fix.apt.not-moved", "{key} was not moved from this page. There is nothing to restore.", key=key)
        )
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
    return M(
        "security-fix.apt.restored",
        "Global key trust restored: {key} is back in trusted.gpg.d, and {source} is as it was.",
        key=key,
        source=lst.name,
    )


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
    ram = _log_in_ram()
    where = M("logs.group", "Where the logs are kept")
    back = _chip(
        where,
        "logs-undo",
        M("logs.ram-image", "In RAM, as the image had it"),
        False,
        M("logs.ram-image.confirm", "Armbian's ramlog comes back as it was, from the next boot."),
    )
    card = M("logs.card", "On the card")
    title = M("logs-card.title", "Logs on the card")
    if ram is None:
        return (
            [
                finding(
                    "logs-ram",
                    title,
                    "ok",
                    M("logs.card-next-boot", "Set on this page. This applies from the next boot."),
                    actions=[back, _chip(where, "logs-card", card, True)],
                )
            ]
            if ours
            else []
        )
    if not ram:
        return [
            finding(
                "logs-ram",
                title,
                "ok",
                M("logs.on-card", "/var/log is on the card. The logs of sshd and the hub server stay after a reboot."),
                notes=[M("logs.set-here", "Set on this page.")] if ours else [],
                actions=[back, _chip(where, "logs-card", card, True)] if ours else [],
            )
        ]
    return [
        finding(
            "logs-ram",
            M("logs-ram.title", "Logs kept in RAM"),
            "warn",
            M(
                "logs.in-ram",
                "/var/log is a RAM disk (Armbian's ramlog). The box trims the journal and the log of sshd every 15 minutes "
                "and loses them at a power cut.",
            ),
            M(
                "logs.in-ram.why",
                "The evidence of an intrusion goes with them. The doctor's check for sign-ins from the internet "
                "sees only the time since the last boot.",
            ),
            M(
                "logs.in-ram.fix",
                "Keep the logs on the card. This disables Armbian's ramlog and keeps the journal, with a limit of 32 MB. "
                "It applies from the next boot.",
            ),
            notes=[M("logs.in-ram.cost", "The card then gets a few MB more writing each week.")],
            actions=[back, _chip(where, "logs-card", M("logs.card-next", "On the card, from the next boot"), True)]
            if ours
            else [
                _chip(where, "logs-ram-now", M("logs.ram", "In RAM"), True),
                _chip(
                    where,
                    "logs-card",
                    card,
                    False,
                    M(
                        "logs.card.confirm",
                        "From the next boot, the journal stays on the card, up to 32 MB. The card gets a few MB more writing each week.",
                    ),
                ),
            ],
        )
    ]


def _logs(rec, on):
    if on:
        was = None
        if RAMLOG_DEFAULT.is_file():
            was = RAMLOG_DEFAULT.read_text()
            new = (
                re.sub(r"(?m)^ENABLED=.*$", "ENABLED=false", was)
                if re.search(r"(?m)^ENABLED=", was)
                else was + "\nENABLED=false\n"
            )
            RAMLOG_DEFAULT.write_text(new)
        JOURNALD_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        JOURNALD_DROPIN.write_text(
            "# Written by irate-box's Security page (/admin): logs on the card. Undo there, or delete this file.\n"
            "[Journal]\nStorage=persistent\nSystemMaxUse=32M\n"
        )
        rec["logs"] = {"date": time.strftime("%Y-%m-%d"), "ramlog": was}
        return M(
            "security-fix.logs.card",
            "Persistent logs: from the next boot, the journal is kept on the card, with a limit of {limit}. "
            "Armbian's ramlog is off.",
            limit="32 MB",
        )
    if "logs" not in rec:
        raise Refused(
            M(
                "security-fix.logs.not-moved",
                "Logs were not moved to the card from this page. There is nothing to restore.",
            )
        )
    if rec["logs"].get("ramlog") is not None and RAMLOG_DEFAULT.is_file():
        RAMLOG_DEFAULT.write_text(rec["logs"]["ramlog"])
    JOURNALD_DROPIN.unlink(missing_ok=True)
    rec.pop("logs")
    return M(
        "security-fix.logs.ram", "In-memory logs: from the next boot, logs are in RAM again, as the image had them."
    )


# --- process visibility: who sees which processes --------------------------------------------------------

HIDEPID = re.compile(r"(?:^|,)hidepid=(1|2|invisible|noaccess|ptraceable)(?:,|$)")
# The options systemd mounts /proc with, and hidepid: "defaults" would turn suid, dev and exec back on at boot, when
# systemd-remount-fs applies this line.
PROC_LINE = "proc /proc proc nosuid,nodev,noexec,hidepid=2 0 0"
PROC_MARK = "# Written by irate-box's Security page (/admin): each account sees only its own processes. Undo there."


def _proc_hidden():
    """True when the box's /proc hides each account's processes from the others (hidepid), None when unknown."""
    try:
        m = re.search(r"^\S+ /proc proc (\S+)", INIT_MOUNTS.read_text(), re.M)
    except OSError:
        return None
    return bool(m and HIDEPID.search(m.group(1)))


def _fstab_proc(lines):
    """The index of /etc/fstab's line for /proc, or None."""
    return next(
        (
            i
            for i, line in enumerate(lines)
            if not line.lstrip().startswith("#") and len(line.split()) >= 4 and line.split()[1] == "/proc"
        ),
        None,
    )


def _write_fstab(text):
    tmp = FSTAB.with_name(f".{FSTAB.name}.irate-box")
    tmp.write_text(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, FSTAB)


def _remount_proc(hidepid):
    """Remount the box's /proc (PID 1's mount namespace, not this unit's own) with hidepid=N. ValueError if it fails."""
    out = run("nsenter", "--target", "1", "--mount", "mount", "-o", f"remount,hidepid={hidepid}", "/proc")
    if out.returncode != 0:
        raise Refused(
            M(
                "security-fix.proc.remount-failed",
                "The box could not remount /proc. mount said: {detail}",
                detail=(out.stderr or out.stdout).strip()[-200:],
            )
        )


def proc_findings(rec):
    ours = rec.get("proc")
    hidden = _proc_hidden()
    if hidden is None:
        return []
    group = M("proc.group", "Who sees which processes")
    every = M("proc.every", "Every account sees every process")
    own = M("proc.own", "Each account sees only its own")
    hide_confirm = M(
        "proc.own.confirm",
        "Each account sees only its own processes, at once and at each boot. Root and the doctors still see every process. Other accounts need sudo to see the rest.",
    )
    back = _chip(
        group,
        "proc-undo",
        M("proc.every-image", "Every account sees every process, as the image has it"),
        False,
        M(
            "proc.every.confirm",
            "Every account sees every process again, as the image has it, at once and at each boot.",
        ),
    )
    title = M("proc-hidden.title", "Process visibility")
    if hidden:
        return [
            finding(
                "proc-hidden",
                title,
                "ok",
                M("proc.hidden", "Each account sees only its own processes. Root sees every process."),
                notes=[M("proc.set-here", "Set on this page.")] if ours else [],
                actions=[back, _chip(group, "proc-hide", own, True)] if ours else [],
            )
        ]
    return [
        finding(
            "proc-hidden",
            title,
            "warn",
            M("proc.visible", "Every account on the box can see every process and its command line."),
            M(
                "proc.visible.why",
                "A local process, a build or a compromised add-on, for example, can read a secret that another "
                "process has on its command line.",
            ),
            M(
                "proc.visible.fix",
                "Hide the processes of each account from the other accounts. Root and the doctors keep their "
                "view of every process. This applies at once and at each boot.",
            ),
            actions=[_chip(group, "proc-every-now", every, True), _chip(group, "proc-hide", own, False, hide_confirm)],
        )
    ]


def _proc(rec, on):
    lines = FSTAB.read_text().splitlines() if FSTAB.exists() else []
    i = _fstab_proc(lines)
    if on:
        if "proc" in rec:
            return M("security-fix.proc.already", "Each account already sees only its own processes. Nothing changed.")
        _remount_proc(2)
        was = lines[i] if i is not None else None
        if i is None:
            lines += [PROC_MARK, PROC_LINE]
        else:
            f = lines[i].split()
            opts = [o for o in f[3].split(",") if not o.startswith("hidepid=")] + ["hidepid=2"]
            lines[i] = " ".join(f[:3] + [",".join(opts)] + f[4:])
        try:
            _write_fstab("\n".join(lines) + "\n")
        except OSError as exc:
            _remount_proc(0)
            raise Refused(
                M(
                    "security-fix.proc.fstab-failed",
                    "The box could not write {path}: {detail}. It showed every process again.",
                    path=str(FSTAB),
                    detail=str(exc),
                )
            ) from exc
        rec["proc"] = {"date": time.strftime("%Y-%m-%d"), "fstab": was}
        return M(
            "security-fix.proc.hidden",
            "Process visibility: each account now sees only its own processes. Root sees every process. "
            "This also applies at each boot.",
        )
    if "proc" not in rec:
        raise Refused(
            M("security-fix.proc.not-hidden", "Processes were not hidden from this page. There is nothing to restore.")
        )
    was = rec["proc"].get("fstab")
    if i is not None:
        if was is None:
            del lines[i]
            if i > 0 and lines[i - 1] == PROC_MARK:
                del lines[i - 1]
        else:
            lines[i] = was
    _write_fstab("\n".join(lines) + "\n" if lines else "")
    _remount_proc(0)
    rec.pop("proc")
    return M(
        "security-fix.proc.shown", "Process visibility: every account sees every process again, as the image has it."
    )


# --- the hotspot firewall: what a guest on the hotspot can reach -------------------------------------

# The hotspot firewall's level, said when it is set: (just turned on?, level).
FIREWALL_SAID = {
    (True, "apps"): M(
        "security-fix.firewall.on-apps",
        "Hotspot firewall on: devices on the hotspot reach the hub, its apps, DNS and DHCP.",
    ),
    (True, "hub"): M(
        "security-fix.firewall.on-hub",
        "Hotspot firewall on: devices on the hotspot reach the hub's pages, DNS and DHCP only.",
    ),
    (False, "apps"): M(
        "security-fix.firewall.apps", "Hotspot firewall: devices on the hotspot reach the hub, its apps, DNS and DHCP."
    ),
    (False, "hub"): M(
        "security-fix.firewall.hub",
        "Hotspot firewall: devices on the hotspot reach the hub's pages, DNS and DHCP only.",
    ),
}


# What the hotspot firewall opens to guests besides the hub when first turned on, where each is installed (firewall.services_here):
# MQTT and Syncthing, and every declared service whose manifest says hotspot "open".
def firewall_default():
    from irate_box.hub import services

    return ["mqtt", "sync"] + [s["id"] for s in services.declared() if s["hotspot"] == "open"]


def _open_in_rules(tcp, udp, rules):
    """Are these ports in the loaded firewall's accept lines (a service added after the rules were written is not)."""
    return all(re.search(rf"\b{p}\b", rules) for p in (*tcp, *udp)) if (tcp or udp) else False


def _firewall_apply(fw):
    """The hotspot firewall as given (None: off) with guests' internet as it is: one ruleset (firewall.apply_all)."""
    from irate_box.root import firewall, share

    fl = dict(fw, services=firewall.services_here(fw.get("services", []))) if fw else None
    firewall.apply_all(fl, share.load())


REACH = M("firewall.reach", "Devices on the hotspot reach")
LEVEL_CHIP = {"apps": M("firewall.level.apps", "Hub and apps"), "hub": M("firewall.level.hub", "Hub only")}


def firewall_findings(rec):
    from irate_box.root import firewall

    ours = rec.get("firewall")
    loaded = firewall.loaded()
    iface = firewall.hotspot_iface()
    title = M("firewall.title", "Hotspot firewall")
    reach_all = M(
        "firewall.off.why",
        "Devices on the hotspot can reach every service that listens on the hotspot's network interface, SSH included.",
    )
    if loaded is None:
        return [
            finding(
                "firewall",
                title,
                "warn",
                M(
                    "firewall.no-nft",
                    "nftables is not installed, so the box cannot limit what devices on the hotspot reach.",
                ),
                reach_all,
                M(
                    "firewall.no-nft.fix",
                    "Update the box, which installs nftables. Then enable the hotspot firewall here.",
                ),
            )
        ]
    from irate_box.hub import services

    wanted = sorted(ours.get("services", [])) if ours else []
    level = (ours or {}).get("level", "apps")
    if not ours or not loaded:
        tcp, udp = firewall.ports(firewall.services_here(firewall_default()))
        extra = ["MQTT", "Syncthing"] + [
            s["name"] for s in services.declared() if s["hotspot"] == "open" and services.installed(s)
        ]
        confirm = {
            "apps": M(
                "firewall.on-apps.confirm",
                "A device on the hotspot ({iface}) reaches only the hub, its apps, DNS and DHCP, and {services} where installed. SSH from the hotspot stays closed until you open it here. Your own network does not change.",
                iface=iface,
                services=extra,
            ),
            "hub": M(
                "firewall.on-hub.confirm",
                "A device on the hotspot ({iface}) reaches only the hub's pages, DNS and DHCP, and {services} where installed. SSH from the hotspot stays closed until you open it here. Your own network does not change.",
                iface=iface,
                services=extra,
            ),
        }
        return [
            finding(
                "firewall",
                title,
                "warn" if not ours else "problem",
                M("firewall.off", "The hotspot firewall is off.")
                if not ours
                else M("firewall.not-loaded", "The hotspot firewall is enabled, but its rules are not loaded."),
                reach_all,
                M(
                    "firewall.off.fix",
                    "Select what devices on the hotspot can reach: the hub and its apps, or the hub only.",
                ),
                notes=[
                    M(
                        "firewall.would-open",
                        "When it is on, it lets through TCP {tcp} and UDP {udp} on {iface} only, and drops the rest. "
                        "The box's other networks do not change.",
                        tcp=tcp,
                        udp=udp,
                        iface=iface,
                    ),
                    M(
                        "firewall.extra",
                        "{services} stay open to the hotspot where they are installed. "
                        "SSH stays closed until you open it.",
                        services=extra,
                    ),
                ],
                commands=[] if not ours else ["nft list table inet irate_box"],
                actions=[
                    _chip(REACH, "firewall-off", M("firewall.level.off", "Everything (firewall off)"), True),
                    *(_chip(REACH, f"firewall-{lv}", LEVEL_CHIP[lv], False, confirm[lv]) for lv in ("apps", "hub")),
                ],
            )
        ]
    tcp, udp = firewall.ports(firewall.services_here(wanted), level)
    ssh = "ssh" in wanted
    try:
        rules = firewall.RULES.read_text()
    except OSError:
        rules = ""
    opened, closed, switches = [], [], []
    for s in services.declared():
        if not services.installed(s):
            continue
        is_open = s["id"] in wanted and _open_in_rules(*firewall.service_ports(s["id"]), rules)
        (opened if is_open else closed).append(s["name"])
        ports = ", ".join(f"{pr.upper()} {p}" for pr, p in s["listen"])
        switches.append(
            action(
                f"firewall-svc-off:{s['id']}", M("firewall.svc-close", "Close {name} to the hotspot", name=s["name"])
            )
            if is_open
            else action(
                f"firewall-svc-on:{s['id']}",
                M("firewall.svc-open", "Open {name} to the hotspot", name=s["name"]),
                M(
                    "firewall.svc-open.confirm",
                    "Devices on the hotspot can reach {name} ({ports}). {says}",
                    name=s["name"],
                    ports=ports,
                    says=s["says"],
                ),
            )
        )
    by_hand = []
    for w in wanted:
        if firewall.PORT_SERVICE.match(w):
            port = w[5:].replace("/", " ").upper()
            by_hand.append(port)
            switches.append(
                action(f"firewall-port-off:{w[5:]}", M("firewall.port-close", "Close {port} to the hotspot", port=port))
            )
    notes = [
        M(
            "firewall.ports",
            "On {iface}, it lets through TCP {tcp} and UDP {udp}, and drops the rest.",
            iface=iface,
            tcp=tcp,
            udp=udp,
        ),
        M("firewall.ssh-open", "SSH from the hotspot is open, as you selected.")
        if ssh
        else M("firewall.ssh-closed", "SSH from the hotspot is closed."),
    ]
    if opened:
        notes.append(
            M(
                "firewall.svcs-open",
                {
                    "one": "This add-on is open to the hotspot: {names}.",
                    "other": "These add-ons are open to the hotspot: {names}.",
                },
                n=len(opened),
                names=opened,
            )
        )
    if closed:
        notes.append(
            M(
                "firewall.svcs-closed",
                {
                    "one": "This add-on is closed to the hotspot: {names}.",
                    "other": "These add-ons are closed to the hotspot: {names}.",
                },
                n=len(closed),
                names=closed,
            )
        )
    if "time" in firewall.services_here(wanted):
        notes.append(
            M(
                "firewall.time-open",
                "The time service (UDP 123) is open to the hotspot. The box serves the time, as set on the Clock page.",
            )
        )
    if by_hand:
        notes.append(
            M(
                "firewall.ports-open",
                {
                    "one": "You opened this port to the hotspot: {ports}.",
                    "other": "You opened these ports to the hotspot: {ports}.",
                },
                n=len(by_hand),
                ports=by_hand,
            )
        )
    ssh_group = M("firewall.ssh", "SSH from the hotspot")
    return [
        finding(
            "firewall",
            title,
            "ok",
            M("firewall.on-apps", "On: devices on the hotspot reach the hub, its apps, DNS and DHCP.")
            if level == "apps"
            else M("firewall.on-hub", "On: devices on the hotspot reach the hub's pages, DNS and DHCP only."),
            notes=notes,
            actions=[
                _chip(
                    REACH,
                    "firewall-off",
                    M("firewall.level.off", "Everything (firewall off)"),
                    False,
                    M(
                        "firewall.off.confirm",
                        "Devices on the hotspot can reach every service on the box.",
                    ),
                ),
                _chip(REACH, "firewall-apps", LEVEL_CHIP["apps"], level == "apps"),
                _chip(REACH, "firewall-hub", LEVEL_CHIP["hub"], level != "apps"),
                _chip(ssh_group, "firewall-ssh-off", M("chip.closed", "Closed"), not ssh),
                _chip(
                    ssh_group,
                    "firewall-ssh-on",
                    M("chip.open", "Open"),
                    ssh,
                    M(
                        "firewall.ssh-on.confirm",
                        "Devices on the hotspot can reach SSH (port 22). Disable SSH password sign-in first (above).",
                    ),
                ),
                *switches,
            ],
        )
    ]


def _firewall(rec, what):
    """what: on, apps, hub, off; ssh-on/off; svc-on/off:<declared service id>; port-on/off:<tcp|udp>/<port>."""
    from irate_box.hub import services as declared_services
    from irate_box.root import firewall

    if what == "off":
        if "firewall" not in rec:
            raise Refused(
                M(
                    "security-fix.firewall.not-on",
                    "The hotspot firewall was not enabled from this page. There is nothing to restore.",
                )
            )
        _firewall_apply(None)
        rec.pop("firewall")
        return M(
            "security-fix.firewall.off",
            "Hotspot firewall off: the hub no longer limits what devices on the hotspot can reach on the box.",
        )
    act, _, arg = what.partition(":")
    if act in ("svc-on", "svc-off") and not declared_services.by_id(arg):
        raise Refused(
            M(
                "security-fix.firewall.unknown-service",
                "{service} is not a service that an add-on declares.",
                service=arg,
            )
        )
    if act in ("port-on", "port-off") and not (
        firewall.PORT_SERVICE.match(f"port:{arg}") and 0 < int(arg.split("/")[1]) < 65536
    ):
        raise Refused(M("security-fix.firewall.bad-port", "A port is tcp/N or udp/N, with N from 1 to 65535."))
    if act not in ("on", "apps", "hub", "ssh-on", "ssh-off", "svc-on", "svc-off", "port-on", "port-off"):
        raise Refused(M("security-fix.firewall.unknown", "{setting} is not a hotspot firewall setting.", setting=what))
    cur = rec.get("firewall") or {"services": firewall_default(), "level": "apps"}
    services = set(cur.get("services", []))
    level = {"on": "apps", "apps": "apps", "hub": "hub"}.get(act, cur.get("level", "apps"))
    name = {
        "svc-on": arg,
        "svc-off": arg,
        "port-on": f"port:{arg}",
        "port-off": f"port:{arg}",
        "ssh-on": "ssh",
        "ssh-off": "ssh",
    }.get(act)
    if name:
        (services.add if act.endswith("-on") else services.discard)(name)
    fw = {"services": sorted(services), "level": level, "date": time.strftime("%Y-%m-%d")}
    _firewall_apply(fw)
    rec["firewall"] = fw
    label = (
        (declared_services.by_id(arg) or {}).get("name", arg)
        if act.startswith("svc")
        else arg.replace("/", " ").upper()
    )
    if act in ("on", "apps", "hub"):
        return sentences(
            FIREWALL_SAID[(act == "on", level)],
            M("security-fix.firewall.services", "The services that it opens are open too, where they are installed.")
            if act == "on" and services - {"ssh"}
            else None,
        )
    return {
        "ssh-on": M("security-fix.firewall.ssh-on", "SSH is open to the hotspot."),
        "ssh-off": M("security-fix.firewall.ssh-off", "SSH is closed to the hotspot."),
        "svc-on": M("security-fix.firewall.opened", "{name} is open to the hotspot.", name=label),
        "svc-off": M("security-fix.firewall.closed", "{name} is closed to the hotspot.", name=label),
        "port-on": M("security-fix.firewall.opened", "{name} is open to the hotspot.", name=label),
        "port-off": M("security-fix.firewall.closed", "{name} is closed to the hotspot.", name=label),
    }[act]


# Guest internet access: each level said whole, and each part of the containment by its heading and what it stops.
SHARE_LEVEL = {
    "off": M("guest-net.level.off", "Guests reach the box and no other network."),
    "users-web": M(
        "guest-net.level.users-web", "A device that is signed in to an account reaches the web through the box."
    ),
    "sheet-web": M(
        "guest-net.level.sheet-web", "A device reaches the web through the box after its user accepts the welcome page."
    ),
    "sheet-all": M(
        "guest-net.level.sheet-all",
        "A device reaches every service on the internet through the box after its user accepts the welcome page.",
    ),
    "open": M(
        "guest-net.level.open",
        "Every device on the hotspot reaches every service on the internet through the box, with no welcome page.",
    ),
}
CONTAIN_HEADING = {
    "lan": M("guest-net.lan", "Block private-network destinations"),
    "tunnels": M("guest-net.tunnels", "Block Tailscale and container routes"),
    "by_mac": M("guest-net.by-mac", "Tie each guest's access to their device"),
    "dns_hold": M("guest-net.dns-hold", "Block name lookups before the welcome page"),
}
CONTAIN_OFF_WHY = {
    "lan": M(
        "guest-net.lan.why",
        "Guests can reach the router page, printers and shared folders on the network behind the box.",
    ),
    "tunnels": M(
        "guest-net.tunnels.why",
        "Guest traffic can enter Tailscale, WireGuard or a container network with the box's access.",
    ),
    "by_mac": M(
        "guest-net.by-mac.why",
        "A device that gets an address that another device had in the last 12 hours also gets that device's "
        "internet access, without the welcome page.",
    ),
    "dns_hold": M(
        "guest-net.dns-hold.why",
        "A device that has not accepted the welcome page can look up outside names through the box. "
        "Attackers can use this to send data out.",
    ),
}


def share_findings():
    """Guests' internet (share.py): one setting, the level as it is and each part of the containment as a
    row of On and Off (the page's chips, held until Save). Each part turned off, and rules not loaded, are
    findings of their own, for the Security doctor (the page leaves them to it)."""
    from irate_box.root import firewall, share

    st = share.load()
    t = M("guest-net.title", "Guest internet access")
    off = [k for k in share.CONTAIN if not st["contain"][k]]
    shared = share.on(st)
    out = []
    if shared and not firewall.loaded():
        out.append(
            finding(
                "guest-net-rules",
                t,
                "problem",
                M("guest-net.not-loaded", "Guest internet access is set, but its rules are not loaded."),
                fix=M("guest-net.not-loaded.fix", "On Network, Hotspot, set the level again, or turn the access off."),
                notes=[SHARE_LEVEL[st["level"]]],
                commands=["nft list table inet irate_box"],
            )
        )
    for k in off:
        out.append(
            finding(
                f"guest-net-{k}",
                M("guest-net.part-off.title", "Guest internet access: {name} is off", name=CONTAIN_HEADING[k]),
                "warn" if shared else "ok",
                M("guest-net.part-off", "You disabled this protection."),
                CONTAIN_OFF_WHY[k] if shared else None,
                notes=(
                    []
                    if shared
                    else [M("guest-net.stay-off.one", "It stays disabled when you share the internet again.")]
                ),
                actions=[
                    action(
                        f"share-contain-{k}-on", M("guest-net.contain-on", "Turn on: {name}", name=CONTAIN_HEADING[k])
                    )
                ],
            )
        )
    chips = []
    for k in share.CONTAIN:
        on = st["contain"][k]
        chips += [
            _chip(CONTAIN_HEADING[k], f"share-contain-{k}-on", ON, on),
            _chip(
                CONTAIN_HEADING[k],
                f"share-contain-{k}-off",
                OFF,
                not on,
                M(
                    "guest-net.contain-off.confirm",
                    "{name} goes off. {why} The Security doctor shows a warning while it is off.",
                    name=CONTAIN_HEADING[k],
                    why=CONTAIN_OFF_WHY[k],
                ),
            ),
        ]
    out.append(
        finding(
            "guest-net",
            t,
            "warn" if shared and off else "ok",
            SHARE_LEVEL[st["level"]],
            actions=chips,
            # The level is set with the hotspot: a link there, not a sentence.
            go={"section": "network/hotspot", "where": M("guest-net.level-go", "the level on Network → Hotspot")},
        )
    )
    return out


def _share_contain(what):
    from irate_box.root import firewall, share

    k, _, onoff = what.rpartition("-")
    if k not in share.CONTAIN or onoff not in ("on", "off"):
        raise Refused(
            M(
                "security-fix.unknown",
                "The Security page has no action called {choice}.",
                choice=f"share-contain-{what}",
            )
        )
    st = share.load()
    st["contain"][k] = onoff == "on"
    if share.on(st):
        fw = load_record().get("firewall")
        firewall.apply_all(dict(fw, services=firewall.services_here(fw.get("services", []))) if fw else None, st)
    share.save(st)
    return (
        M("security-fix.contain.on", "Enabled: {name}.", name=CONTAIN_HEADING[k])
        if onoff == "on"
        else M("security-fix.contain.off", "Disabled: {name}.", name=CONTAIN_HEADING[k])
    )


def listener_part(found, rec):
    """The scan's findings on what listens. No ss: nothing can be said of what listens, which is not the same as
    nothing listening."""
    if _have("ss"):
        return listener_findings(found, rec)
    return [
        finding(
            "listeners-unread",
            M("listeners.unread.title", "What listens on the network"),
            "warn",
            M("listeners.unread", "The box could not list what listens on the network: ss is not installed."),
            fix=M("listeners.unread.fix", "Install the package iproute2."),
        )
    ]


def scan():
    rec = load_record()
    found = listeners()
    findings = listener_part(found, rec)
    settings = sshd_settings()
    findings += cockpit_findings(found, rec)
    findings += ssh_findings(settings, keys_on_box(), rec)
    findings += sudo_findings(rec, settings or {})
    findings += kernel_findings(rec)
    findings += group_findings(rec)
    findings += root_findings(rec)
    findings += apt_findings(rec)
    findings += log_findings(rec)
    findings += proc_findings(rec)
    findings += firewall_findings(rec)
    findings += share_findings()
    upd, _ = update_findings()
    findings += upd
    return {"at": time.time(), "listeners": found, "findings": findings}


# --- fixes --------------------------------------------------------------------------------


def _systemctl(*args):
    out = run("systemctl", *args)
    if out.returncode != 0:
        raise Refused(
            M(
                "security-fix.systemctl-failed",
                "The command systemctl {command} failed: {detail}",
                command=" ".join(args),
                detail=((out.stderr or out.stdout).strip().splitlines() or ["?"])[-1],
            )
        )


def _write_sshd_dropin(ssh, on=()):
    """The one drop-in for the SSH settings: `ssh` the ones set off here, `on` the ones set on here
    (where the image had them off). It sorts first, and sshd keeps the first value it reads."""
    if not ssh and not on:
        SSHD_DROPIN.unlink(missing_ok=True)
    else:
        SSHD_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Written by irate-box's Security page (/admin). Undo there, or delete this file."]
        if "root" in ssh:
            lines.append("PermitRootLogin no")
        for what, keys in (
            ("password", ("PasswordAuthentication", "KbdInteractiveAuthentication")),
            ("forwarding", ("AllowTcpForwarding", "AllowAgentForwarding", "X11Forwarding")),
        ):
            if what in ssh or what in on:
                lines += [f"{k} {'no' if what in ssh else 'yes'}" for k in keys]
        SSHD_DROPIN.write_text("\n".join(lines) + "\n")
    sshd = shutil.which("sshd") or "/usr/sbin/sshd"
    check = run(sshd, "-t")
    if check.returncode != 0:
        SSHD_DROPIN.unlink(missing_ok=True)
        raise Refused(
            M(
                "security-fix.sshd.rejected",
                "sshd rejected the change, so the box did not make it: {detail}",
                detail=check.stderr.strip()[:200],
            )
        )
    for unit in ("ssh.service", "sshd.service"):
        if run("systemctl", "reload", unit).returncode == 0:
            return
    raise Refused(
        M(
            "security-fix.sshd.not-reloaded",
            "The setting is saved, but sshd did not reload. The setting applies when sshd restarts.",
        )
    )


SSH_SAID = {
    ("root", True): M("security-fix.ssh.root-off", "SSH sign-in as root disabled."),
    ("root", False): M("security-fix.ssh.root-restored", "SSH sign-in as root is restored to its previous state."),
    ("password", True): M("security-fix.ssh.password-off", "SSH password sign-in disabled."),
    ("password", False): M(
        "security-fix.ssh.password-restored", "SSH password sign-in is restored to its previous state."
    ),
    ("forwarding", True): M("security-fix.ssh.forwarding-off", "SSH forwarding disabled: TCP, agent and X11."),
    ("forwarding", False): M(
        "security-fix.ssh.forwarding-restored", "SSH forwarding is restored to its previous state."
    ),
    ("password", "on"): M("security-fix.ssh.password-on", "SSH password sign-in enabled."),
    ("forwarding", "on"): M("security-fix.ssh.forwarding-on", "SSH forwarding enabled: TCP, agent and X11."),
}


def _ssh(rec, what, on):
    """on: True sets it off here, False undoes what this page set (off or on), "on" sets it on here."""
    ssh, ssh_on = set(rec.get("ssh", {})), set(rec.get("ssh_on", {}))
    if on is True and what == "password" and not keys_on_box():
        raise Refused(
            M(
                "security-fix.ssh.no-keys",
                "No account has an SSH key on the box. Without passwords, no account could sign in over SSH.",
            )
        )
    ssh.discard(what)
    ssh_on.discard(what)
    if on is True:
        ssh.add(what)
    elif on == "on":
        ssh_on.add(what)
    _write_sshd_dropin(ssh, ssh_on)
    for key, keep in (("ssh", ssh), ("ssh_on", ssh_on)):
        rec[key] = {k: rec.get(key, {}).get(k, time.strftime("%Y-%m-%d")) for k in keep}
        if not rec[key]:
            rec.pop(key)
    return SSH_SAID[(what, on)]


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
        else:
            _systemctl("disable", "--now", "cockpit.socket")  # it was off: off again, not running
        return M("security-fix.cockpit.restored", "Cockpit is restored to its previous state.")
    old = rec.get("cockpit") or {}
    was_enabled = old.get("was_enabled", run("systemctl", "is-enabled", "cockpit.socket").stdout.strip() == "enabled")
    # What Undo puts back: how it was before this page first changed it. A record from before there was a
    # setting was made from Cockpit on the network.
    before = old.get("before") or ("network" if old else _cockpit_now(_cockpit_ports(listeners())))
    if mode == "network":
        dropin.unlink(missing_ok=True)
        _systemctl("daemon-reload")
        _systemctl("enable", "cockpit.socket")
        _systemctl("restart", "cockpit.socket")
    elif mode == "loopback":
        dropin.parent.mkdir(parents=True, exist_ok=True)
        dropin.write_text(
            "# Written by irate-box's Security page (/admin). Undo there, or delete this file.\n"
            "[Socket]\nListenStream=\nListenStream=127.0.0.1:9090\n"
        )
        _systemctl("daemon-reload")
        _systemctl("enable", "cockpit.socket")  # from off, too
        _systemctl("restart", "cockpit.socket")
    else:
        _systemctl("disable", "--now", "cockpit.socket")
        run("systemctl", "stop", "cockpit.service")
    rec["cockpit"] = {"mode": mode, "before": before, "was_enabled": was_enabled, "at": time.strftime("%Y-%m-%d")}
    return {
        "network": M("security-fix.cockpit.network", "Cockpit now listens on the network."),
        "loopback": M("security-fix.cockpit.loopback", "Cockpit now listens on loopback only."),
    }.get(mode) or M("security-fix.cockpit.off", "Cockpit is stopped and disabled.")


def _llmnr(rec, on):
    if on:
        RESOLVED_DROPIN.parent.mkdir(parents=True, exist_ok=True)
        RESOLVED_DROPIN.write_text(
            "# Written by irate-box's Security page (/admin). Undo there, or delete this file.\n[Resolve]\nLLMNR=no\n"
        )
        rec["llmnr"] = time.strftime("%Y-%m-%d")
    else:
        RESOLVED_DROPIN.unlink(missing_ok=True)
        rec.pop("llmnr", None)
    _systemctl("restart", "systemd-resolved.service")
    return (
        M("security-fix.llmnr.off", "LLMNR is off.")
        if on
        else M("security-fix.llmnr.restored", "LLMNR is restored to its previous state.")
    )


def _hub_on_80():
    out = run("ss", "-Hltnp", "sport = :80")
    return '"nginx"' in out.stdout or '"caddy"' in out.stdout


UNIT_RE = re.compile(r"^[A-Za-z0-9@_][A-Za-z0-9@._-]*\.(service|socket)$")  # no leading "-"


def _unit(rec, unit, on, reason="from this page"):
    if not UNIT_RE.match(unit) or PROTECTED.match(unit):
        raise Refused(M("security-fix.unit.unknown", "{unit} is not a unit that this page switches off.", unit=unit))
    units = rec.setdefault("units", {})
    if on:
        was_enabled = run("systemctl", "is-enabled", unit).stdout.strip() == "enabled"
        _systemctl("disable", "--now", unit)
        units[unit] = {"was_enabled": was_enabled, "at": time.strftime("%Y-%m-%d"), "reason": reason}
        return M("security-fix.unit.off", "{unit} is stopped and disabled.", unit=unit)
    old = units.get(unit)
    if old is None:
        raise Refused(
            M(
                "security-fix.unit.not-off",
                "{unit} was not disabled from this page. There is nothing to restore.",
                unit=unit,
            )
        )
    if "take-port-80" in old.get("reason", "") and _hub_on_80():
        raise Refused(
            M(
                "security-fix.unit.port-80",
                "{unit} gave port 80 to the hub, which still has it. First move the hub: run "
                "install.sh --port 8080 again. Or uninstall the hub, which gives the port back.",
                unit=unit,
            )
        )
    units.pop(unit)
    _systemctl("enable" if old.get("was_enabled") else "start", *(["--now"] if old.get("was_enabled") else []), unit)
    if not units:
        rec.pop("units")
    return M("security-fix.unit.restored", "{unit} is restored to its previous state.", unit=unit)


APT_ENV = {"DEBIAN_FRONTEND": "noninteractive"}


def _apt_logged(log, argv, timeout):
    """One apt-get run, its command and output appended to the open log; its exit code."""
    log.write("$ " + " ".join(argv) + "\n\n")
    log.flush()
    env = dict(os.environ, **APT_ENV, HOME=os.environ.get("HOME", "/root"))
    try:
        with aptram.argv(argv) as full:
            code = subprocess.run(  # noqa: TID251
                full, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, timeout=timeout
            ).returncode
    except OSError as exc:
        log.write(f"{exc}\n")
        code = 127
    log.write("\n")
    return code


def _waiting():
    with aptram.argv(["apt-get", "-s", "-o", "Debug::NoLocking=1", "upgrade"]) as cmd:
        return pending_security(run(*cmd, timeout=180).stdout)


def _lists(log):
    if _apt_logged(log, ["apt-get", "update"], 900) != 0:
        raise Refused(
            M(
                "security-fix.lists.failed",
                "apt-get update failed. The box can be without internet access. Read the log on Updates.",
            )
        )
    return M("security-fix.lists.done", "The box refreshed the package lists.")


NONE_WAITING = M("security-fix.updates.none", "No security updates are waiting.")


def _take(log, pkgs, install):
    """The waiting security updates downloaded (the pattern's Fetch) or installed."""
    if not pkgs:
        return NONE_WAITING
    argv = ["apt-get", "install", "-y", "--only-upgrade"] + (
        ["-o", "Dpkg::Options::=--force-confold"] if install else ["--download-only"]
    )
    code = _apt_logged(log, argv + pkgs, 3600)
    if code != 0:
        raise Refused(
            M(
                "security-fix.updates.failed",
                "apt-get stopped with exit code {code}. Read the log on Updates.",
                code=code,
            )
        )
    if install:
        return M(
            "security-fix.updates.installed",
            {"one": "The box installed {n} security update.", "other": "The box installed {n} security updates."},
            n=len(pkgs),
        )
    return M(
        "security-fix.updates.downloaded",
        {
            "one": "The box downloaded {n} security update. It is ready to install.",
            "other": "The box downloaded {n} security updates. They are ready to install.",
        },
        n=len(pkgs),
    )


def install_security_updates(log_path):
    """Upgrade only the packages whose new version is from a security archive; output to log_path."""
    pkgs = _waiting()
    if not pkgs:
        return NONE_WAITING
    with safeio.open_new(log_path) as log:  # in control/, which the hub can change
        return _take(log, pkgs, True)


def fetch_security_updates(log_path):
    """Download the waiting security updates without installing them (the pattern's Fetch)."""
    pkgs = _waiting()
    if not pkgs:
        return NONE_WAITING
    with safeio.open_new(log_path) as log:
        return _take(log, pkgs, False)


def check_security_updates(rec, log_path):
    """Check now: the package lists fetched afresh, then what is found taken as far as the owner chose
    (with Manual, the second choice applies to a check by hand; on a schedule it is apt's own
    periodic work that does it)."""
    act = (chosen_pattern(rec) or DEFAULT_PATTERN)[1]
    with safeio.open_new(log_path) as log:
        said = [_lists(log)]
        pkgs = _waiting()
        if act and pkgs:
            said.append(_take(log, pkgs, act == 2))
        else:
            said.append(
                M(
                    "security-fix.updates.waiting",
                    {"one": "{n} security update is waiting.", "other": "{n} security updates are waiting."},
                    n=len(pkgs),
                )
                if pkgs
                else NONE_WAITING
            )
    return sentences(*said)


def reach_security_updates(log_path, install):
    """The hub card's "Check for updates and download" / "...download and install" for Debian: the package
    lists afresh, then what waits downloaded or installed, whatever the owner's pattern says about a check."""
    with safeio.open_new(log_path) as log:
        said = [_lists(log)]
        pkgs = _waiting()
        said.append(_take(log, pkgs, install))
    return sentences(*said)


def refresh_lists(log_path):
    """apt-get update, its output to log_path; what it found is in the scan that follows."""
    with safeio.open_new(log_path) as log:
        return _lists(log)


def fix(choice, updates_log):
    """Carry out one of the page's offers. Returns a message; raises ValueError."""
    rec = load_record()
    if choice in ("ssh-root-off", "ssh-root-undo"):
        msg = _ssh(rec, "root", choice.endswith("off"))
    elif choice in [f"ssh-{w}-{m}" for w in ("password", "forwarding") for m in ("off", "on", "undo")]:
        _, what, mode = choice.split("-")
        msg = _ssh(rec, what, {"off": True, "on": "on", "undo": False}[mode])
    elif choice in ("cockpit-network", "cockpit-loopback", "cockpit-off", "cockpit-undo"):
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
    elif choice in ("proc-hide", "proc-undo"):
        msg = _proc(rec, choice == "proc-hide")
    elif choice.startswith("firewall-"):
        msg = _firewall(rec, choice[len("firewall-") :])  # each switch checked there
    elif choice.startswith("share-contain-"):
        return _share_contain(choice[len("share-contain-") :])
    elif choice.startswith("autoupdate-set:"):
        msg = _autoupdate(rec, choice.split(":", 1)[1])
    elif choice.startswith("autoupdate-"):
        msg = _autoupdate(rec, choice[len("autoupdate-") :])
    elif choice == "security-updates":
        return install_security_updates(updates_log)
    elif choice == "security-fetch":
        return fetch_security_updates(updates_log)
    elif choice == "security-check":
        return check_security_updates(rec, updates_log)
    elif choice in ("security-reach-fetch", "security-reach-install"):
        return reach_security_updates(updates_log, choice == "security-reach-install")
    elif choice == "apt-lists":
        return refresh_lists(updates_log)
    else:
        raise Refused(M("security-fix.unknown", "The Security page has no action called {choice}.", choice=choice))
    save_record(rec)
    return msg


def undo_all():
    """Put back everything this page changed (uninstall.sh). Returns what was undone."""
    rec = load_record()
    done = []
    # Not root's lock nor the first-login marker: undoing those would bring the image's known default
    # password back. They stay as they are (the page's own Undo still puts them back before then).
    for choice in (
        (["ssh-root-undo"] if "root" in rec.get("ssh", {}) else [])
        + (["ssh-password-undo"] if "password" in {**rec.get("ssh", {}), **rec.get("ssh_on", {})} else [])
        + (["ssh-forwarding-undo"] if "forwarding" in {**rec.get("ssh", {}), **rec.get("ssh_on", {})} else [])
        + (["cockpit-undo"] if "cockpit" in rec else [])
        + (["llmnr-undo"] if rec.get("llmnr") else [])
        + [f"unit-undo:{u}" for u in rec.get("units", {})]
        + [f"kernel-{n}-undo" for n in rec.get("kernel", {})]
        + [f"group-undo:{g}" for g in rec.get("groups", {})]
        + [f"sudo-undo:{f}" for f in rec.get("sudo", {})]
        + [f"apt-undo:{k}" for k in rec.get("apt", {})]
        + (["logs-undo"] if "logs" in rec else [])
        + (["proc-undo"] if "proc" in rec else [])
        + (["firewall-off"] if "firewall" in rec else [])
        + (["autoupdate-undo"] if "autoupdate" in rec else [])
    ):
        try:
            done.append(render(fix(choice, None)))  # for uninstall.sh's terminal
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
                said = english(f)
                print(f"{'problem' if f['status'] == 'problem' else 'note   '}  {said['title']}: {said['detail']}")
    elif len(sys.argv) in (3, 4) and sys.argv[1] == "unit-off":
        rec = load_record()
        print(_unit(rec, sys.argv[2], True, *(sys.argv[3:] or ["from this page"])))
        save_record(rec)
    elif sys.argv[1:] == ["undo-all"]:
        for line in undo_all():
            print(line)
    else:
        sys.exit("usage: security.py scan | summary | undo-all | unit-off UNIT [REASON]")
