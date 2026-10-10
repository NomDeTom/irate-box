# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Hotspot profiles that are not irate-box's (an image's setup hotspot, such as Armbian's
"armbiansetup-<board>"), at the owner's choice: removed, with the profile kept so undo can bring it
back, or left as it is and no longer listed among things to look at.

Such a profile shares the client link's radio and interface: started, it takes the box off its own
network. irate-box's own hotspot runs beside the link, so a second one adds nothing; its password
is often the image's default. The doctor warns whenever one is set to start by itself, left or not.

Records: /etc/hub/other-ap.json ({uuid: {name, ssid, choice: removed|left, kept, at}}); removed
profiles kept in STATE/other-ap/. Root only; stdlib only.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
RECORD = ETC / "other-ap.json"
KEPT = STATE / "other-ap"
KEYFILES = Path(os.environ.get("HUB_NM_CONNECTIONS", "/etc/NetworkManager/system-connections"))
OURS = "irate-box-ap"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


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
        safeio.write(RECORD, json.dumps(rec, indent=1), 0o644)   # names and choices only: the page reads it
    else:
        RECORD.unlink(missing_ok=True)


def _profile(uuid):
    """(name, ssid, file) of an access-point profile that is not irate-box's, or ValueError."""
    if not isinstance(uuid, str) or not UUID_RE.match(uuid):
        raise ValueError("not a connection's id")
    code, out = run("nmcli", "-t", "-f", "connection.id,802-11-wireless.mode,802-11-wireless.ssid", "con", "show", "uuid", uuid)
    if code != 0:
        raise ValueError("NetworkManager has no such connection")
    kv = dict(line.partition(":")[::2] for line in out.splitlines())
    if kv.get("802-11-wireless.mode") != "ap":
        raise ValueError("only another hotspot's profile can be removed or left here")
    if kv.get("connection.id") == OURS:
        raise ValueError("that is irate-box's own hotspot: switch it off on the Hotspot tab")
    code, out = run("nmcli", "-t", "-f", "UUID,FILENAME", "con", "show")
    f = next((l.partition(":")[2] for l in out.splitlines() if l.startswith(uuid + ":")), "")
    return kv.get("connection.id"), kv.get("802-11-wireless.ssid"), f


def remove(uuid):
    name, ssid, f = _profile(uuid)
    src = Path(f)
    if not f or not src.is_relative_to(KEYFILES) or not src.is_file():
        raise ValueError(f"{name} is not a profile file in {KEYFILES}, so it can't be kept for undo; nothing removed")
    KEPT.mkdir(parents=True, exist_ok=True)
    os.chmod(KEPT, 0o700)
    kept = KEPT / f"{time.strftime('%Y%m%d-%H%M%S')}-{src.name}"
    shutil.copy2(src, kept)
    code, out = run("nmcli", "con", "delete", "uuid", uuid)
    if code != 0:
        kept.unlink(missing_ok=True)
        raise ValueError(f"nmcli: {out[-200:]}")
    rec = record()
    rec[uuid] = {"name": name, "ssid": ssid, "choice": "removed", "kept": str(kept), "file": str(src), "at": time.time()}
    _save(rec)
    return f"{name} ({ssid}) removed; its profile is kept in {kept}, and undo brings it back"


def leave(uuid):
    name, ssid, _f = _profile(uuid)
    rec = record()
    rec[uuid] = {"name": name, "ssid": ssid, "choice": "left", "at": time.time()}
    _save(rec)
    return f"{name} ({ssid}) left as it is, and no longer listed (the doctor still warns if it's set to start by itself)"


def undo(uuid):
    rec = record()
    r = rec.get(uuid)
    if not r:
        raise ValueError("nothing was chosen for that profile here")
    if r["choice"] == "removed":
        dest = Path(r["file"])
        if dest.exists():
            raise ValueError(f"{dest} is there already; nothing put back")
        shutil.copy2(r["kept"], dest)
        os.chmod(dest, 0o600)
        code, out = run("nmcli", "con", "load", str(dest))
        if code != 0:
            dest.unlink(missing_ok=True)
            raise ValueError(f"nmcli: {out[-200:]}")
    rec.pop(uuid)
    _save(rec)
    return f"{r['name']} " + ("put back" if r["choice"] == "removed" else "listed again")
