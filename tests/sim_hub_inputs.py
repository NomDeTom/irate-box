# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's handlers match a request value whole, so a trailing newline is not "the end" of a
name, choice or key. Each anchored pattern is used with fullmatch, and the handlers that read a
value back (a queue item, a report key, an accepted finding) refuse the second line.
python3 tests/sim_hub_inputs.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import server
from irate_box.msg import Refused

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def refused(name, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except (ValueError, Refused) as exc:
        check(name, bool(str(exc)), exc)
    else:
        check(name, False, "accepted")


# Every request pattern is used whole. A pattern ending in "$" still matches before a trailing
# newline under re.match, so the source must say fullmatch.
source = (REPO / "irate_box/hub/server.py").read_text()
for name in (
    "ICON_TEXT",
    "ACCEPT_KEY_RE",
    "HEALTH_KEY_RE",
    "LOCAL_ID_RE",
    "REPORT_KEY_RE",
    "USB_DEVICE_RE",
    "ADDON_ID_RE",
    "HEALTH_CHOICE_RE",
    "IFACE_NAME_RE",
    "SECURITY_CHOICE_RE",
):
    check(f"{name} is matched whole", f"{name}.match(" not in source and f"{name}.fullmatch(" in source)
for name in ("manifests.ID_RE", "gitrepos.NAME_RE", "podcast.NAME_RE", "store.ID_RE"):
    check(f"{name} is matched whole", f"{name}.match(" not in source)

# The patterns themselves still refuse the second line.
for pattern, value in (
    (server.ICON_TEXT, "ab\n"),
    (server.ACCEPT_KEY_RE, "hub:x\n"),
    (server.HEALTH_KEY_RE, "hub:x\n"),
    (server.LOCAL_ID_RE, "eliza\n"),
    (server.REPORT_KEY_RE, "drop:abc\n"),
    (server.USB_DEVICE_RE, "sda\n"),
    (server.ADDON_ID_RE, "draw\n"),
    (server.HEALTH_CHOICE_RE, "x\n"),
    (server.IFACE_NAME_RE, "wlan0\n"),
    (server.SECURITY_CHOICE_RE, "tcp/80\n"),
):
    check(f"{pattern.pattern}: a trailing newline refused", pattern.fullmatch(value) is None)

check("an icon: a letter and a digit", server.valid_icon("ab") is True)
check("an icon: a trailing newline refused", server.valid_icon("ab\n") is False)
check("a queue item: a trailing newline refused", server._queue_item({"kind": "app", "id": "draw\n"}) is None)
check("a queue item: the manifest's id kept", server._queue_item({"kind": "app", "id": "draw"})["id"] == "draw")
check("a report key: a trailing newline refused", server.report_key("drop", {"id": "abc\n"}) is None)
check("a report key: the id kept", server.report_key("drop", {"id": "abc"}) == "drop:abc")
refused("an accepted security finding: a trailing newline refused", server.security_accept, "hub:x\n", "x", True)
refused("an accepted health finding: a trailing newline refused", server.health_accept, "hub:x\n", "x", True)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
