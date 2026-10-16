# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Hotspot profiles that are not irate-box's (an image's setup hotspot, such as Armbian's
"armbiansetup-<board>"), at the owner's choice: removed, with the profile kept so undo can bring it
back (and that copy deleted too, for good), or left as it is and no longer listed among things to look at.

Such a profile shares the client link's radio and interface: started, it takes the box off its own
network. irate-box's own hotspot runs beside the link, so a second one adds nothing; its password
is often the image's default. The doctor warns whenever one is set to start by itself, left or not.

Records: /etc/hub/other-ap.json ({uuid: {name, ssid, choice: removed|left, kept, at}}); removed
profiles kept in ROOT_STATE/other-ap/ (root's, beside $STATE; older ones in STATE/other-ap/, read
only through safeio's checks). Root only; stdlib only.
"""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import safeio

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
RECORD = ETC / "other-ap.json"
KEPT = safeio.ROOT_STATE / "other-ap"
OLD_KEPT = STATE / "other-ap"   # before 2026-10: the hub owns $STATE and could swap this folder
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
    ETC.mkdir(parents=True, exist_ok=True)
    if rec:
        safeio.write(RECORD, json.dumps(rec, indent=1), 0o644)   # names and choices only: the page reads it
    else:
        RECORD.unlink(missing_ok=True)


def _profile(uuid):
    """(name, ssid, file) of an access-point profile that is not irate-box's, or Refused saying why not."""
    if not isinstance(uuid, str) or not UUID_RE.match(uuid):
        raise Refused(M("other-ap.request.uuid", "The request is not valid: the connection ID is not a UUID."))
    code, out = run("nmcli", "-t", "-f", "connection.id,802-11-wireless.mode,802-11-wireless.ssid", "con", "show", "uuid", uuid)
    if code != 0:
        raise Refused(M("other-ap.not-found", "NetworkManager has no connection profile with this ID."))
    kv = dict(line.partition(":")[::2] for line in out.splitlines())
    if kv.get("802-11-wireless.mode") != "ap":
        raise Refused(M("other-ap.not-hotspot", "You can remove or ignore only the profile of another hotspot here."))
    if kv.get("connection.id") == OURS:
        raise Refused(M("other-ap.ours", "This profile is the hotspot of irate-box. Switch it off on the Hotspot tab."))
    code, out = run("nmcli", "-t", "-f", "UUID,FILENAME", "con", "show")
    f = next((l.partition(":")[2] for l in out.splitlines() if l.startswith(uuid + ":")), "")
    return kv.get("connection.id"), kv.get("802-11-wireless.ssid"), f


def remove(uuid):
    name, ssid, f = _profile(uuid)
    src = Path(f)
    if not f or not src.is_relative_to(KEYFILES) or not src.is_file():
        raise Refused(M("other-ap.remove.no-file", "{name} is not a profile file in {folder}, so the box cannot keep a copy to restore it. "
                     "The box removed nothing.", name=name, folder=str(KEYFILES)))
    safeio.root_dir("other-ap")
    kept = KEPT / f"{time.strftime('%Y%m%d-%H%M%S')}-{src.name}"
    shutil.copy2(src, kept)
    code, out = run("nmcli", "con", "delete", "uuid", uuid)
    if code != 0:
        kept.unlink(missing_ok=True)
        raise Refused(M("other-ap.remove.nmcli", "nmcli did not delete the profile: {detail}", detail=out[-200:]))
    rec = record()
    rec[uuid] = {"name": name, "ssid": ssid, "choice": "removed", "kept": str(kept), "file": str(src), "at": time.time()}
    _save(rec)
    return M("other-ap.removed", "The box deleted the profile {name} ({ssid}). It keeps a copy in {kept}. Restore profile brings it back.",
             name=name, ssid=ssid, kept=str(kept))


def leave(uuid):
    name, ssid, _f = _profile(uuid)
    rec = record()
    rec[uuid] = {"name": name, "ssid": ssid, "choice": "left", "at": time.time()}
    _save(rec)
    return M("other-ap.ignored", "The box ignores {name} ({ssid}) and no longer lists it. The doctor still warns if the profile starts by itself.",
             name=name, ssid=ssid)


def forget(uuid):
    """A deleted profile's kept copy deleted too, for good: nothing left to restore."""
    rec = record()
    r = rec.get(uuid)
    if not r or r.get("choice") != "removed":
        raise Refused(M("other-ap.forget.none", "No deleted profile with this ID has a retained copy."))
    kept = Path(r["kept"])
    if kept.parent == KEPT:
        kept.unlink(missing_ok=True)
    elif kept.parent == OLD_KEPT:
        try:
            fd = safeio.own_dir(OLD_KEPT)
        except OSError:
            fd = None   # gone, or not root's folder now: nothing of root's to delete there
        if fd is not None:
            try:
                os.unlink(kept.name, dir_fd=fd)
            except FileNotFoundError:
                pass
            finally:
                os.close(fd)
    rec.pop(uuid)
    _save(rec)
    return M("other-ap.forgotten", "The box deleted the retained copy of {name}. You cannot restore the profile now.", name=r["name"])


def undo(uuid):
    rec = record()
    r = rec.get(uuid)
    if not r:
        raise Refused(M("other-ap.undo.none", "The box did not remove or ignore this profile."))
    if r["choice"] == "removed":
        dest = Path(r["file"])
        if dest.exists():
            raise Refused(M("other-ap.undo.exists", "{file} exists already, so the box did not restore the profile.", file=str(dest)))
        kept = Path(r["kept"])
        try:
            if kept.parent == KEPT:
                data = kept.read_bytes()
            elif kept.parent == OLD_KEPT:
                data = safeio.read_own(kept).encode()
            else:
                raise OSError("not a kept copy")
        except OSError as e:
            raise Refused(M("other-ap.undo.kept", "The box could not read the retained copy {kept}: {detail}",
                            kept=str(kept), detail=str(e)))
        try:
            with safeio.create(dest, 0o600) as fh:
                fh.write(data)
        except FileExistsError:
            raise Refused(M("other-ap.undo.exists", "{file} exists already, so the box did not restore the profile.", file=str(dest)))
        code, out = run("nmcli", "con", "load", str(dest))
        if code != 0:
            dest.unlink(missing_ok=True)
            raise Refused(M("other-ap.undo.nmcli", "nmcli did not load the profile: {detail}", detail=out[-200:]))
    rec.pop(uuid)
    _save(rec)
    if r["choice"] == "removed":
        return M("other-ap.restored", "The box restored the profile {name}.", name=r["name"])
    return M("other-ap.listed", "The box lists {name} again.", name=r["name"])
