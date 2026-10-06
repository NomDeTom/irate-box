# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's own updates, on the librarian's schedule: the same three steps as /admin's
Updates buttons (check, fetch, install), asked of the root helper in turn, as far as the owner
chose (policy "hub_auto"):

  0  check only: an update found is shown on /admin (Updates, and its badge); nothing else
  1  also fetch it: the root helper verifies it and caches what its installer needs
  2  also install it, but only a version that passed verification, inside the window
     (policy "hub_window_start" to "hub_window_end", the box's local hours), with nobody on
     the hub, and nothing else running

One step per run of the timer (hourly), each only when the last has answered. A version that
failed verification is never fetched again on its own, and never installed (that is the
Updates doctor's "Install anyway", by hand). Each version is tried once per step; a new
version starts afresh. The librarian runs as the hub's user: it writes requests to the root
helper's queue, as server.py does, and reads the answers and the update state it leaves.

State: $HUB_STATE_DIR/library/hub-update.json. Stdlib only.
"""

import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path

STATE_DIR = Path(os.environ.get("HUB_STATE_DIR", Path(__file__).resolve().parents[2]))
CONTROL = STATE_DIR / "control"
STATE = STATE_DIR / "library" / "hub-update.json"
UPDATE = CONTROL / "update.json"
PROGRESS = CONTROL / "update-progress.json"
STATUS_URL = os.environ.get("HUB_STATUS_URL", "http://127.0.0.1:8000/status")
GIVE_UP_S = 3 * 3600  # an answer this late is not coming (the helper died, the box rebooted)


def _read(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def load_state():
    return _read(STATE, {})


def _save(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name(STATE.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, STATE)


def _queue(action):
    rid = os.urandom(8).hex()
    (CONTROL / "requests").mkdir(parents=True, exist_ok=True)
    tmp = STATE_DIR / f".request-{rid}.tmp"  # the hub's folder: control/ is root's (F3)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"action": action, "id": rid, "by": "librarian"}, fh)
    os.replace(tmp, CONTROL / "requests" / f"{rid}.json")
    return rid


def _busy():
    """Something for the root helper is queued or running (an install, an add-on, a scan)."""
    if any((CONTROL / "requests").glob("*.json")):
        return "the root helper has requests waiting"
    p = _read(PROGRESS, None)
    if isinstance(p, dict):
        try:
            os.kill(int(p.get("pid", 0)), 0)
            return f"the root helper is busy ({p.get('action', 'working')})"
        except (OSError, ValueError, TypeError):
            pass
    return None


def _online():
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=10) as r:
            return int(json.loads(r.read()).get("online") or 0)
    except (OSError, ValueError):
        return None  # cannot tell: treat as someone there


def in_window(policy, hour=None):
    start, end = policy["hub_window_start"] % 24, policy["hub_window_end"] % 24
    h = datetime.now().hour if hour is None else hour
    return start <= h < end if start < end else (h >= start or h < end) if start != end else False


def _due(state, hours, now):
    return hours > 0 and now - state.get("last_check", 0) >= hours * 3600 - 300


def step(policy, log=print, now=None):
    """One step towards the newest version, as far as the policy goes. Returns what it did."""
    now = time.time() if now is None else now
    state = load_state()
    said = None
    w = state.get("waiting")
    if w:
        answer = _read(CONTROL / "results" / f"{w['id']}.json", None)
        if answer:
            state.pop("waiting")
            state["last"] = {"step": w["step"], "ok": answer.get("ok"), "message": answer.get("message"), "at": now}
            said = f"{w['step']}: {answer.get('message')}"
        elif now - w["at"] > GIVE_UP_S:
            state.pop("waiting")
            said = f"{w['step']}: no answer from the root helper; trying again later"
        else:
            return f"waiting for the root helper ({w['step']})"
    busy = _busy()
    if busy:
        _save(state)
        return said or busy

    def ask(step_name, action, **extra):
        state["waiting"] = {"step": step_name, "id": _queue(action), "at": now}
        state.update(extra)
        _save(state)
        log(f"hub update: asking the root helper to {step_name}")
        return f"asked to {step_name}"

    if _due(state, policy["hub_check_every_hours"], now):
        return ask("check", "update-check", last_check=now)
    upd = _read(UPDATE, {})
    avail = upd.get("available")
    if not avail or upd.get("up_to_date"):
        _save(state)
        return said or "up to date"
    auto = policy["hub_auto"]
    if auto >= 1 and upd.get("verified") != avail and state.get("fetch_tried") != avail:
        return ask("fetch", "update-fetch", fetch_tried=avail)
    if auto >= 2 and upd.get("verified") == avail and state.get("install_tried") != avail:
        if not in_window(policy):
            reason = f"{avail} is ready; installs between {policy['hub_window_start']:02d}:00 and {policy['hub_window_end']:02d}:00"
        elif (people := _online()) != 0:
            reason = f"{avail} is ready; waiting for nobody to be on the hub" + ("" if people is None else f" ({people} now)")
        else:
            return ask("install", "update-install", install_tried=avail)
        state["note"] = reason
        _save(state)
        return said or reason
    _save(state)
    if upd.get("verified") == avail:
        return said or f"{avail} is ready to install on /admin"
    if state.get("fetch_tried") == avail and auto >= 1:
        return said or f"{avail} did not pass verification: see the Updates doctor"
    return said or f"{avail} is available"
