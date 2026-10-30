# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The owner's choice of how strict each kind of security change is, and the record of approvals.

Six kinds (secmanifest.py's toggles) at three levels:

  flag       show it in the update's checks as a warning, and install.
  authorise  hold the update until an administrator approves it over HTTPS.
  console    hold it until the console command is run, so a stolen session cannot approve it.

One switch raises every "authorise" to "console" while it is on. The page may turn it on;
only the console turns it off. Approvals and a forced install are kept in an append-only
record under root's state. Stdlib only.
"""

import hashlib
import json
import os
import time
from pathlib import Path

from irate_box import durable, security_levels
from irate_box.msg import M, Refused

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
ROOT = Path(os.environ.get("HUB_ROOT_STATE_DIR") or STATE.parent / "irate-box-root")
FILE = ETC / "security-changes.json"
PUBLIC = STATE / "control" / "security-changes.json"
APPROVAL = ETC / "security-approval.json"
LOG = ROOT / "security-changes.jsonl"
RECORD_STATE = ROOT / "security-record.json"
READ_FILE = ROOT / "security-record-read.json"
TOGGLES = security_levels.TOGGLES
LEVELS = security_levels.LEVELS
DEFAULTS = security_levels.DEFAULTS


def load():
    try:
        data = json.loads(durable.read_own(FILE))
    except (OSError, ValueError, AttributeError):
        data = {}
    toggles = dict(DEFAULTS)
    for name in TOGGLES:
        level = (data.get("toggles") or {}).get(name)
        if level in LEVELS:
            toggles[name] = level
    return {"toggles": toggles, "console_only": data.get("console_only") is True}


def levels(data=None):
    data = data or load()
    if not data["console_only"]:
        return dict(data["toggles"])
    return {name: ("console" if level == "authorise" else level) for name, level in data["toggles"].items()}


def public(data=None):
    data = data or load()
    return {"toggles": data["toggles"], "console_only": data["console_only"], "effective": levels(data)}


def publish(data=None):
    durable.write(PUBLIC, json.dumps(public(data)), 0o644)


def _check_toggle(name, level):
    if name not in TOGGLES or level not in LEVELS:
        raise Refused(
            M(
                "security.changes.invalid",
                "The security changes are not valid.",
            )
        )


def save(req, write=durable.write_checked):
    data = load()
    toggles = req.get("toggles", {})
    if not isinstance(toggles, dict):
        raise Refused(M("security.changes.invalid", "The security changes are not valid."))
    for name, level in toggles.items():
        _check_toggle(name, level)
        data["toggles"][name] = level
    want = req.get("console_only")
    if want is not None:
        if not isinstance(want, bool):
            raise Refused(M("security.changes.invalid", "The security changes are not valid."))
        if data["console_only"] and not want:
            raise Refused(
                M(
                    "security.changes.console-only-off",
                    "Approving security changes at the console cannot be turned off from the page. Run "
                    "sudo /opt/irate-box/irate-box hub_control security-console off at the console or over SSH.",
                )
            )
        data["console_only"] = want
    write(FILE, json.dumps(data, indent=2, sort_keys=True), 0o600)
    publish(data)
    if data["console_only"]:
        return M(
            "security.changes.saved-console",
            "Saved. Approvals that were over HTTPS now need the console, and the page cannot turn that off.",
        )
    return M("security.changes.saved", "Saved. The next check of an update uses these choices.")


def console_only_off(write=durable.write_checked):
    data = load()
    data["console_only"] = False
    write(FILE, json.dumps(data, indent=2, sort_keys=True), 0o600)
    publish(data)
    return M("security.changes.console-off", "Approvals over HTTPS are allowed again.")


def approval():
    try:
        data = json.loads(durable.read_own(APPROVAL))
    except (OSError, ValueError, AttributeError):
        return None
    return data if isinstance(data, dict) else None


def _record(entry):
    LOG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    line = json.dumps(entry, sort_keys=True) + "\n"
    fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, line.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    data = LOG.read_bytes()
    durable.write_checked(
        RECORD_STATE,
        json.dumps({"lines": data.count(b"\n"), "sha256": hashlib.sha256(data).hexdigest()}),
        0o600,
    )


def record_health():
    """None while there is no record; else {"ok": bool, "why": "missing" | "shortened" | "changed"}."""
    try:
        state = json.loads(durable.read_own(RECORD_STATE))
    except (OSError, ValueError, AttributeError):
        return None
    try:
        data = LOG.read_bytes()
    except OSError:
        return {"ok": False, "why": "missing"}
    lines = data.count(b"\n")
    if lines < int(state.get("lines") or 0):
        return {"ok": False, "why": "shortened"}
    if lines == int(state.get("lines") or 0) and hashlib.sha256(data).hexdigest() != state.get("sha256"):
        return {"ok": False, "why": "changed"}
    return {"ok": True, "why": ""}


def approve(sha, commit, changes, how, who, at_levels, write=durable.write_checked):
    record = {
        "sha": sha,
        "commit": commit,
        "changes": list(changes),
        "how": how,
        "who": who,
        "levels": dict(at_levels),
        "at": time.time(),
    }
    write(APPROVAL, json.dumps(record, indent=2, sort_keys=True), 0o600)
    _record({"event": "approved", **record})
    return record


def approved(sha, changes):
    record = approval()
    if not record or record.get("sha") != sha:
        return None
    if sorted(record.get("changes") or []) != sorted(changes):
        return None
    return record


def clear(write=durable.write_checked):
    try:
        APPROVAL.unlink()
    except OSError:
        pass


def record_install(commit, tag, signing_level, at_levels, changes, how, forced, write=durable.write_checked):
    entry = {
        "event": "installed",
        "commit": commit,
        "tag": tag,
        "signing": signing_level,
        "levels": dict(at_levels),
        "changes": {
            "widenings": [item["text"] for item in changes.get("widenings", [])],
            "narrowings": [item["text"] for item in changes.get("narrowings", [])],
            "neutral": [item["text"] for item in changes.get("neutral", [])],
        },
        "how": how,
        "forced": bool(forced),
        "at": time.time(),
    }
    _record(entry)
    clear(write)
    return entry


def entries(limit=20):
    try:
        lines = LOG.read_text().splitlines()
    except OSError:
        return []
    if limit is not None:
        lines = lines[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _written():
    try:
        return len(LOG.read_text().splitlines())
    except OSError:
        return 0


def unread():
    try:
        read = int(json.loads(durable.read_own(READ_FILE)).get("lines") or 0)
    except (OSError, ValueError, AttributeError, TypeError):
        read = 0
    return entries(None)[read:]


def mark_read(write=durable.write_checked):
    lines = _written()
    write(READ_FILE, json.dumps({"lines": lines, "at": time.time()}), 0o600)
    return M("security.record.read", "The security record is marked as read up to now.")
