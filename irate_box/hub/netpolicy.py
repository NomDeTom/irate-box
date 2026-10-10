# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Whether the box is on a metered network now, so updates and big downloads can wait for another.

The owner's switch is NetworkManager's own per connection (connection.metered, on /admin's Network
page, each network's own settings): yes, no, or unknown. With unknown, NetworkManager's own guess
counts (an Android phone says so over DHCP), and so does a network that looks like a phone's
hotspot (an iPhone's 172.20.10.0/28). A connection set to "no" is never counted as metered.

Scheduled downloads (the librarian's hourly run: books, mirrors, toolkits, firmware, the hub's own
updates) wait while it is metered and say so in STATE/metered-wait.json; a download started by hand
goes ahead, after the page has asked. stdlib only; no root.
"""
import ipaddress
import json
import os
import re
import subprocess
import time
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
WAIT = STATE / "metered-wait.json"
PHONE_NETS = (ipaddress.ip_network("172.20.10.0/28"),)   # an iPhone's personal hotspot


def run(*cmd, timeout=15):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return p.returncode, (p.stdout + p.stderr).strip()


def _route_iface(run_):
    code, out = run_("ip", "-4", "route", "show", "default")
    m = re.search(r"\bdev (\S+)", out) if code == 0 else None
    return m.group(1) if m else None


def metered_now(run_=None):
    """{iface, connection, metered, how}: how is "set", "guessed by NetworkManager" or "looks like a
    phone's hotspot" when metered, else why not."""
    run_ = run_ or run
    iface = _route_iface(run_)
    if not iface:
        return {"iface": None, "connection": None, "metered": False, "how": "no route to the internet"}
    code, out = run_("nmcli", "-t", "-f", "GENERAL.METERED,GENERAL.CONNECTION,IP4.ADDRESS", "dev", "show", iface)
    kv = {}
    for line in out.splitlines() if code == 0 else []:
        k, _, v = line.partition(":")
        kv.setdefault(k.split("[")[0], v)
    said = (kv.get("GENERAL.METERED") or "").strip()
    conn = kv.get("GENERAL.CONNECTION") or None
    base = {"iface": iface, "connection": conn}
    if said == "yes":
        return {**base, "metered": True, "how": "set"}
    if said == "yes (guessed)":
        return {**base, "metered": True, "how": "guessed by NetworkManager"}
    if said == "no":
        return {**base, "metered": False, "how": "set not metered"}
    try:
        addr = ipaddress.ip_interface(kv.get("IP4.ADDRESS", "").strip()).ip
    except ValueError:
        addr = None
    if addr is not None and any(addr in n for n in PHONE_NETS):
        return {**base, "metered": True, "how": "looks like a phone's hotspot"}
    return {**base, "metered": False, "how": "not metered" if said else "not known (NetworkManager not asked)"}


def gate(now=None, run_=None):
    """For a scheduled run: None to go ahead, else the line saying why it waits. Keeps the record of
    since when it has waited (for the page and the doctor), and clears it once it goes ahead."""
    m = metered_now(run_)
    now = now or time.time()
    if not m["metered"]:
        try:
            WAIT.unlink()
        except OSError:
            pass
        return None
    try:
        since = json.loads(WAIT.read_text()).get("since")
        since = now if since is None else since
    except (OSError, ValueError, AttributeError):
        since = now
    rec = {"since": since, "last": now, "connection": m["connection"], "iface": m["iface"], "how": m["how"]}
    try:
        WAIT.parent.mkdir(parents=True, exist_ok=True)
        tmp = WAIT.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec))
        os.replace(tmp, WAIT)
    except OSError:
        pass
    return (f"waiting: on a metered network ({m['connection'] or m['iface']}, {m['how']}); scheduled updates and "
            "downloads wait for another, or for one started by hand")


def waiting():
    try:
        return json.loads(WAIT.read_text())
    except (OSError, ValueError):
        return None
