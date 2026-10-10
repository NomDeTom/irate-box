# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One WiFi connection's own settings, chosen on /admin's Network page: written to its NetworkManager
profile with nmcli, the value it had before recorded so undo (and uninstall) can put it back.

Only where the change lasts: a connection that is NetworkManager's own (a keyfile in /etc), or any
connection on a NetworkManager that keeps its settings in netplan itself. A connection netplan writes
afresh at every boot, on a NetworkManager that cannot write back to netplan, is refused, pointing at
handing it over (root/nmhandover.py). Client connections only: the hotspot's profile is irate-box's
hotspot code's.

A setting takes effect the next time the connection comes up; the page says so. Root only; stdlib only.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
RECORD = ETC / "nm-connection-changes.json"
NETPLAN_PROFILES = "/run/NetworkManager/system-connections/netplan-"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def _int(lo, hi):
    def check(v):
        if not re.fullmatch(r"-?\d{1,4}", v) or not lo <= int(v) <= hi:
            raise ValueError(f"a whole number from {lo} to {hi}")
        return str(int(v))
    return check


def _one_of(*allowed):
    def check(v):
        if v not in allowed:
            raise ValueError(f"one of {', '.join(a or '(none)' for a in allowed)}")
        return v
    return check


def _mac_or_empty(v):
    if v and not MAC_RE.match(v):
        raise ValueError("an access point's address (aa:bb:cc:dd:ee:ff), or none")
    return v.lower()


# name: (nmcli key, check, NetworkManager's own value: what "default" puts).
SETTINGS = {
    "autoconnect": ("connection.autoconnect", _one_of("yes", "no"), "yes"),
    "priority": ("connection.autoconnect-priority", _int(-999, 999), "0"),
    "autoconnect_retries": ("connection.autoconnect-retries", _int(-1, 1000), "-1"),
    "auth_retries": ("connection.auth-retries", _int(-1, 1000), "-1"),
    "metered": ("connection.metered", _one_of("yes", "no", "unknown"), "unknown"),
    "mac": ("802-11-wireless.cloned-mac-address", _one_of("", "preserve", "permanent", "random", "stable", "stable-ssid"), ""),
    "bssid": ("802-11-wireless.bssid", _mac_or_empty, ""),
    "band": ("802-11-wireless.band", _one_of("", "a", "bg"), ""),
    "channel": ("802-11-wireless.channel", _int(0, 196), "0"),
    "powersave": ("802-11-wireless.powersave", _one_of("0", "1", "2", "3"), "0"),
    "hidden": ("802-11-wireless.hidden", _one_of("yes", "no"), "no"),
    "dhcp_timeout": ("ipv4.dhcp-timeout", _int(0, 3600), "0"),
}
POWERSAVE_WORDS = {"default": "0", "ignore": "1", "disable": "2", "enable": "3"}


def run(*cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
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
    if rec:
        safeio.write(RECORD, json.dumps(rec, indent=1), 0o600)
    else:
        RECORD.unlink(missing_ok=True)


def _current(uuid):
    keys = ",".join(k for k, _c, _d in SETTINGS.values())
    code, out = run("nmcli", "-t", "-f", "connection.id," + keys + ",802-11-wireless.mode", "con", "show", "uuid", uuid)
    if code != 0:
        raise ValueError("NetworkManager has no such connection")
    kv = {}
    for line in out.splitlines():
        k, _, v = line.partition(":")
        kv[k] = v.replace("\\:", ":")
    if kv.get("802-11-wireless.mode", "infrastructure") not in ("", "infrastructure"):
        raise ValueError("only a connection the box joins can be set here (the hotspot has its own settings)")
    out_ = {"name": kv.get("connection.id")}
    for n, (key, _c, _d) in SETTINGS.items():
        v = kv.get(key, "")
        if n == "powersave":
            v = POWERSAVE_WORDS.get(v.split(" ")[0], v.split(" ")[0])
        else:
            v = v.split(" ")[0]
        out_[n] = v
    return out_


def lasts(uuid, integrated):
    """Whether a change to this connection survives a boot: (bool, why not)."""
    code, out = run("nmcli", "-t", "-f", "UUID,FILENAME", "con", "show")
    for line in out.splitlines() if code == 0 else []:
        u, _, f = line.partition(":")
        if u == uuid:
            if f.startswith(NETPLAN_PROFILES) and not integrated:
                return False, ("netplan writes this connection afresh at every boot, so a setting made for it alone "
                               "would be lost: hand it over to NetworkManager first (Network page, WiFi)")
            return True, ""
    return False, "NetworkManager has no such connection"


def change(uuid, changes, integrated=False):
    """Set one connection's own settings ({name: value | "default"}). One line saying what was done."""
    if not isinstance(uuid, str) or not UUID_RE.match(uuid):
        raise ValueError("not a connection's id")
    if not isinstance(changes, dict) or not changes:
        raise ValueError("nothing to change")
    ok, why = lasts(uuid, integrated)
    if not ok:
        raise ValueError(why)
    now = _current(uuid)
    args, said = [], []
    for n, v in changes.items():
        if n not in SETTINGS:
            raise ValueError(f"not a setting offered: {n}")
        key, check, nm_default = SETTINGS[n]
        v = nm_default if v == "default" else str(v).strip()
        try:
            v = check(v)
        except ValueError as exc:
            raise ValueError(f"{n}: {exc}")
        args += [key, v]
        said.append(f"{n} {v or '(none)'}")
    band = changes.get("band", now["band"]) if changes.get("band") != "default" else ""
    if changes.get("channel") not in (None, "default", "0") and not band:
        raise ValueError("a channel needs a band (2.4 or 5 GHz) chosen with it")
    rec = record()
    old = rec.setdefault(uuid, {"name": now["name"], "old": {}, "at": time.time()})
    for n in changes:
        old["old"].setdefault(n, now[n])   # the value from before irate-box first changed it
    code, out = run("nmcli", "con", "modify", "uuid", uuid, *args)
    if code != 0:
        raise ValueError(f"nmcli: {out[-200:]}")
    # A value back where it was before is no longer irate-box's change.
    after = _current(uuid)
    old["old"] = {n: v for n, v in old["old"].items() if after.get(n) != v}
    if not old["old"]:
        rec.pop(uuid)
    _save(rec)
    return f"{now['name']}: {', '.join(said)}; it takes effect the next time the connection comes up"


def undo(uuid):
    rec = record()
    if uuid not in rec:
        raise ValueError("nothing was changed on this connection here")
    entry = rec.pop(uuid)
    args = [x for n, v in entry["old"].items() if n in SETTINGS for x in (SETTINGS[n][0], v)]
    if args:
        code, out = run("nmcli", "con", "modify", "uuid", uuid, *args)
        if code != 0 and "not exist" not in out and "unknown connection" not in out.lower():
            rec[uuid] = entry
            _save(rec)
            raise ValueError(f"nmcli: {out[-200:]}")
    _save(rec)
    return f"{entry['name']}: its own settings put back as they were"


def undo_all():
    said = []
    for uuid in list(record()):
        try:
            said.append(undo(uuid))
        except ValueError as exc:
            said.append(f"{uuid}: {exc}")
    return "\n".join(said)


def public():
    """For the page: which settings irate-box changed on which connection (nothing secret)."""
    return {u: {"name": r["name"], "changed": sorted(r["old"])} for u, r in record().items()}


def main(argv):
    if argv[:1] == ["undo-all"]:
        said = undo_all()
        if said:
            print(said)
        return 0
    print("usage: nmconnection.py undo-all")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
