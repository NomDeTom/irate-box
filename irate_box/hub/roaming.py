# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Roaming between access points that share a name: the two choices that change the owner's own WiFi
(item 35; uplink-roaming-options-plan §2, C and D). Root's (the watchdog's and hub_control's), by consent,
recorded in /etc/hub/uplink-roaming.json and undone the way "Keep retrying" is: here, by undo-all, and by
uninstalling.

  lock     the WiFi profile held to one access point (NetworkManager's 802-11-wireless.bssid): no roaming,
           no background scans (NetworkManager's own behaviour). Its old value kept and put back. Applied at
           once (a reconnect); should the locked access point not answer, the old value goes back and the
           link up again, so a lock can't strand the box.
  no-scan  wpa_supplicant's background scan cleared for the network in use while the hotspot shares the
           radio (wpa_cli set_network … bgscan ""; taken live, no re-association: proven on the Lyra,
           2026-10-09, see the plan's stage 5). NetworkManager sets it again each time it connects, so the
           watchdog (uplink.py, root's, every check) clears it again: at most one check's worth of scans.

ignore and count are the watchdog's own (uplink.py): nothing on the box changes for them."""

import json
import os
import re
import time

from irate_box.hub import uplink

RECORD = uplink.ETC / "uplink-roaming.json"


def record():
    try:
        rec = json.loads(RECORD.read_text())
        return rec if isinstance(rec, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(rec):
    uplink.ETC.mkdir(parents=True, exist_ok=True)
    if rec:
        RECORD.write_text(json.dumps(rec, indent=2))
        os.chmod(RECORD, 0o644)
    else:
        RECORD.unlink(missing_ok=True)


def _profile():
    prof = uplink._uplink_profile()   # ValueError when NetworkManager does not run the link
    return prof


def lock(bssid, run=None):
    """Hold the WiFi profile to one access point. Returns what was done; raises ValueError."""
    run = run or uplink.run
    bssid = (bssid or "").lower()
    if not uplink.BSSID_RE.match(bssid):
        raise ValueError("not an access point's BSSID")
    prof = _profile()
    rec = record()
    cur = rec.get("lock")
    if cur and cur["uuid"] == prof["uuid"] and cur["bssid"] == bssid:
        return f"{prof['name']}: already locked to {bssid}"
    if cur and cur["uuid"] != prof["uuid"]:
        unlock(run)
        rec = record()
        cur = None
    if not cur:
        code, out = run("nmcli", "-g", "802-11-wireless.bssid", "con", "show", "uuid", prof["uuid"])
        if code:
            raise ValueError(f"could not read {prof['name']}: {out}")
        old = out.strip().replace("\\:", ":")
    else:
        old = cur["old"]
    code, out = run("nmcli", "con", "modify", "uuid", prof["uuid"], "802-11-wireless.bssid", bssid)
    if code:
        raise ValueError(f"nmcli: {out}")
    code, out = run("nmcli", "-w", "45", "con", "up", "uuid", prof["uuid"], timeout=60)
    if code:
        # Back to what was in force before this call: the lock there was, or the profile's own value.
        run("nmcli", "con", "modify", "uuid", prof["uuid"], "802-11-wireless.bssid", cur["bssid"] if cur else old)
        run("nmcli", "-w", "45", "con", "up", "uuid", prof["uuid"], timeout=60)
        raise ValueError(f"{bssid} did not answer ({out[-160:]}): " + (f"still locked to {cur['bssid']}, as before" if cur
                         else "the lock is taken off again") + ", and the link back as it was")
    rec["lock"] = {"uuid": prof["uuid"], "name": prof["name"], "bssid": bssid, "old": old, "at": time.time()}
    _save(rec)
    return f"{prof['name']}: locked to {bssid}; no roaming and no background scans until it is unlocked"


def unlock(run=None):
    run = run or uplink.run
    rec = record()
    cur = rec.pop("lock", None)
    if not cur:
        return "not locked"
    code, out = run("nmcli", "con", "modify", "uuid", cur["uuid"], "802-11-wireless.bssid", cur.get("old") or "")
    if code and "not exist" not in out and "unknown connection" not in out.lower():
        raise ValueError(f"nmcli: {out}")
    _save(rec)
    # Not reconnected here: the next connection is free to roam again; the page says so.
    return f"{cur['name']}: unlocked from {cur['bssid']} (from its next connection it may roam again)"


def wpa_network_id(iface, run):
    code, out = run("wpa_cli", "-i", iface, "status")
    m = re.search(r"^id=(\d+)$", out, re.M) if code == 0 else None
    return m.group(1) if m else None


def clear_scan(iface, run=None):
    """The background scan off for the network in use (live), if the hotspot shares the radio and it is on.
    Returns what was done, or "" when nothing needed doing."""
    run = run or uplink.run
    if "no-scan" not in record() or not uplink.shared_radio(iface):
        return ""
    nid = wpa_network_id(iface, run)
    if nid is None:
        return ""
    code, out = run("wpa_cli", "-i", iface, "get_network", nid, "bgscan")
    if code or out.strip().strip('"') == "":
        return ""
    rec = record()
    rec["no-scan"].setdefault("was", out.strip().strip('"'))
    _save(rec)
    code, out = run("wpa_cli", "-i", iface, "set_network", nid, "bgscan", '""')
    return f"background scans off on {iface}" if code == 0 and "OK" in out else f"wpa_cli: {out[-120:]}"


def no_scan(on, iface, run=None):
    run = run or uplink.run
    rec = record()
    if on:
        rec["no-scan"] = rec.get("no-scan") or {"at": time.time()}
        _save(rec)
        said = clear_scan(iface, run)
        return "no background scans while the hotspot shares the radio" + (f": {said}" if said else
                                                                           " (it does not share it now: nothing to change yet)")
    cur = rec.pop("no-scan", None)
    _save(rec)
    if not cur:
        return "background scans as NetworkManager sets them"
    nid = wpa_network_id(iface, run) if iface else None
    if nid is not None and cur.get("was"):
        run("wpa_cli", "-i", iface, "set_network", nid, "bgscan", f'"{cur["was"]}"')
    return "background scans back as NetworkManager sets them"


def apply(new, old, iface, run=None):
    """A settings change's roaming part: lock or unlock, background scans off or back. Returns a line."""
    run = run or uplink.run
    said = []
    if old.get("roaming") == "lock" and (new.get("roaming") != "lock"):
        said.append(unlock(run))
    if old.get("roaming") == "no-scan" and new.get("roaming") != "no-scan":
        said.append(no_scan(False, iface, run))
    if new.get("roaming") == "lock" and (old.get("roaming") != "lock" or old.get("lock_bssid") != new.get("lock_bssid")
                                         or "lock" not in record()):
        said.append(lock(new["lock_bssid"], run))
    if new.get("roaming") == "no-scan" and (old.get("roaming") != "no-scan" or "no-scan" not in record()):
        said.append(no_scan(True, iface, run))
    return "; ".join(said)


def undo_all(iface=None, run=None):
    run = run or uplink.run
    said = []
    if "lock" in record():
        said.append(unlock(run))
    if "no-scan" in record():
        said.append(no_scan(False, iface, run))
    return said
