# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One IRC entry on /admin: the two manifests in one add-on group, one port, different install options, and the
group check refusing two members that would be the same add-on or on different ports. python3 tests/sim_irc_group.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import manifests

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(manifests_in):
    try:
        manifests.check_groups(manifests_in)
    except manifests.ManifestError as exc:
        return str(exc)
    return None


all_manifests = manifests.load()
irc = [m for m in all_manifests if (m.get("addon") or {}).get("group") == "irc"]
check(
    "the two IRC servers are one group, one port and different install options",
    [m["id"] for m in irc] == ["irc", "inspircd"]
    and len({(m["addon"]).get("group_title") for m in irc}) == 1
    and len({(m.get("status") or {}).get("port") for m in irc}) == 1
    and len({(m["addon"]).get("option") for m in irc}) == 2,
    [(m["id"], m["addon"].get("group_title"), m.get("status", {}).get("port")) for m in irc],
)
check(
    "a group with two different ports is refused",
    refused(
        [
            {"id": "a", "addon": {"group": "x", "option": "--with-a"}, "status": {"port": 1}},
            {"id": "b", "addon": {"group": "x", "option": "--with-b"}, "status": {"port": 2}},
        ]
    )
    is not None,
)
check(
    "a group whose members are the same add-on is refused",
    refused(
        [
            {"id": "a", "addon": {"group": "x", "option": "--with-a"}, "status": {"port": 1}},
            {"id": "b", "addon": {"group": "x", "option": "--with-a"}, "status": {"port": 1}},
        ]
    )
    is not None,
)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
