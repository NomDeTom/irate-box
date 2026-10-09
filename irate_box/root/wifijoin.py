# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Networks the box may join, added on /admin's Network page, including ones it hasn't seen yet (SSID
and password). Root's half, through hub_control.py.

Only where NetworkManager runs the box's WiFi. A network is a NetworkManager profile of its own, written
as a keyfile (root's, 0600) and loaded, so the password never appears on a command line or in a process
list. It joins by itself when the networks it knows are gone (priority below the owner's own profiles),
or at once when asked: then, should it not come up within a minute, the profile in use before is brought
back up, so a wrong password cannot strand a box with no screen.

What was added here is recorded in /etc/hub/wifi-joined.json; Forget removes only those. Uninstalling
keeps them (they are the owner's networks, and taking one away could leave the box off its network),
and says how to remove them."""

import json
import os
import re
import subprocess
import time
import uuid as uuidlib
from pathlib import Path

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RECORD = ETC / "wifi-joined.json"
KEYFILES = Path(os.environ.get("HUB_NM_CONNECTIONS", "/etc/NetworkManager/system-connections"))
SECURITY = ("wpa-psk", "sae", "open")   # WPA2 (and WPA3 transition), WPA3 only, no password
PRIORITY = -10                          # below the owner's own profiles (0): a fallback unless chosen
SSID_RE = re.compile(r"^[^\x00-\x1f\x7f\\]{1,32}$")
PSK_RE = re.compile(r"^[\x20-\x7e]{8,63}$|^[0-9A-Fa-f]{64}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def run(*cmd, timeout=60):
    """Every command goes through here (the tests stand it in). A missing program answers 127."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not installed"
    return p.returncode, (p.stdout + p.stderr).strip()


def record():
    try:
        data = json.loads(RECORD.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(rec):
    from irate_box.root import safeio
    ETC.mkdir(parents=True, exist_ok=True)
    safeio.write(RECORD, json.dumps(rec, indent=1), 0o600)


def validate(ssid, security, psk, hidden):
    """The network as asked, or ValueError saying what is wrong (never echoing the password)."""
    if not isinstance(ssid, str) or not SSID_RE.match(ssid) or len(ssid.encode()) > 32 or ssid != ssid.strip():
        raise ValueError("the network's name (SSID): 1 to 32 bytes, no control characters or backslash, no spaces at either end")
    if security not in SECURITY:
        raise ValueError(f"security one of {', '.join(SECURITY)}")
    if security == "open":
        if psk:
            raise ValueError("an open network takes no password")
    elif not isinstance(psk, str) or not PSK_RE.match(psk):
        raise ValueError("the password: 8 to 63 printable characters, or 64 hex digits")
    if not isinstance(hidden, bool):
        raise ValueError("hidden is true or false")


def _escape(value):
    """A value as GLib's key files read it (NetworkManager's keyfiles): backslashes and leading spaces escaped."""
    v = value.replace("\\", "\\\\")
    return "\\s" + v[1:] if v.startswith(" ") else v


def keyfile(name, uid, ssid, security, psk, hidden):
    lines = ["[connection]", f"id={_escape(name)}", f"uuid={uid}", "type=wifi", "autoconnect=true",
             f"autoconnect-priority={PRIORITY}", "", "[wifi]", "mode=infrastructure", f"ssid={_escape(ssid)}"]
    if hidden:
        lines.append("hidden=true")
    if security != "open":
        lines += ["", "[wifi-security]", f"key-mgmt={security}", f"psk={_escape(psk)}"]
    lines += ["", "[ipv4]", "method=auto", "", "[ipv6]", "method=auto", ""]
    return "\n".join(lines)


def _active_wifi():
    """The uuid of the WiFi profile in use, or None."""
    code, out = run("nmcli", "-t", "-f", "UUID,TYPE", "connection", "show", "--active")
    for line in out.splitlines() if code == 0 else []:
        u, _, kind = line.partition(":")
        if kind == "802-11-wireless":
            return u
    return None


def join(ssid, security="wpa-psk", psk="", hidden=False, now=False, wait=60, sleep=time.sleep):
    """Add a network for the box to join (by itself when the ones it knows are gone, or at once). Returns
    what was done; raises ValueError."""
    validate(ssid, security, psk, hidden)
    code, out = run("nmcli", "-t", "-f", "RUNNING", "general")
    if code != 0 or out.strip() != "running":
        raise ValueError("NetworkManager does not run this box's WiFi, so networks cannot be added here")
    rec = record()
    if any(r.get("ssid") == ssid for r in rec.values()):
        raise ValueError(f"{ssid} was added here already: forget it first to change it")
    uid = str(uuidlib.uuid4())
    name = f"irate-box {ssid}"
    path = KEYFILES / f"irate-box-{uid[:8]}.nmconnection"
    KEYFILES.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(keyfile(name, uid, ssid, security, psk, hidden))
    code, out = run("nmcli", "connection", "load", str(path))
    if code != 0:
        path.unlink(missing_ok=True)
        raise ValueError(f"NetworkManager did not take it: {out[-200:]}")
    rec[uid] = {"name": name, "ssid": ssid, "security": security, "hidden": hidden, "file": str(path), "at": time.time()}
    _save(rec)
    said = f"{ssid} added: the box joins it by itself when the networks it knows are out of reach"
    if not now:
        return said
    before = _active_wifi()
    code, out = run("nmcli", "connection", "up", "uuid", uid, timeout=wait + 30)
    if code == 0:
        return f"{ssid} added, and the box is on it now"
    if before:
        for _ in range(3):
            if run("nmcli", "connection", "up", "uuid", before, timeout=90)[0] == 0:
                break
            sleep(10)
    return f"{ssid} added, but joining it failed ({out[-160:]}); back on the network it was on. It stays known, to join when in reach"


def forget(uid):
    """Remove a network added here (and only those)."""
    if not isinstance(uid, str) or not UUID_RE.match(uid):
        raise ValueError("not a network's id")
    rec = record()
    if uid not in rec:
        raise ValueError("only networks added on this page can be forgotten here")
    r = rec.pop(uid)
    code, out = run("nmcli", "connection", "delete", "uuid", uid)
    if code != 0 and "not exist" not in out and "unknown connection" not in out.lower():
        raise ValueError(f"nmcli: {out[-200:]}")
    Path(r.get("file", "")).unlink(missing_ok=True) if r.get("file", "").startswith(str(KEYFILES)) else None
    _save(rec)
    return f"{r['ssid']} forgotten"


def public():
    """What the page may show: the networks added here, without their passwords."""
    return [{"uuid": u, "ssid": r["ssid"], "security": r.get("security"), "hidden": r.get("hidden", False), "at": r.get("at")}
            for u, r in sorted(record().items(), key=lambda x: x[1].get("at", 0))]


def kept_on_uninstall():
    """uninstall.sh's line: the networks added here stay, and how to remove them."""
    rec = record()
    if not rec:
        return ""
    return ("kept the networks added on /admin (the owner's): " + ", ".join(r["ssid"] for r in rec.values())
            + "; remove one with: nmcli connection delete uuid UUID (" + ", ".join(rec) + ")")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["kept"]:
        print(kept_on_uninstall())
    else:
        sys.exit("usage: wifijoin.py kept")
