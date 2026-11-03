# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One IRC app, not two: the add-on group folded for the tile, /admin's app menu, Access and the
services, with ngIRCd installed, with InspIRCd, and with neither. python3 tests/sim_irc_app.py"""

import os
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="irc-app-"))
(T / "state").mkdir()
(T / "etc").mkdir()
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import access, manifests
from irate_box.hub import server

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def installed(option):
    (T / "state" / "install-options").write_text(option or "")
    return server.installed_addons()


def one(words):
    """(the folded IRC app, its service, /admin's entry, Access's row)."""
    option, desc, tile, status, unit = words
    installed(option)
    folded = server.folded_manifests()
    irc = [m for m in folded if (m.get("addon") or {}).get("group") == "irc"]
    services = [s for s in server._services() if s.get("app") in ("irc", "inspircd")]
    apps = [
        a for a in manifests.admin_apps(server.MANIFESTS, server.installed_addons()) if a["id"] in ("irc", "inspircd")
    ]
    rows = [a for a in access.apps(server.MANIFESTS, server.installed_addons()) if a["id"] in ("irc", "inspircd")]
    return option, desc, tile, status, unit, irc, services, apps, rows


NG = (
    "--with-irc",
    "Real-time chat in any IRC app",
    "IRC chat",
    "IRC server (ngIRCd)",
    "ngircd.service",
)
INSP = (
    "--with-inspircd",
    "Real-time chat in any IRC app, or in the browser",
    "IRC chat",
    "IRC server (InspIRCd)",
    "inspircd.service",
)
NEITHER = ("", "Real-time chat in any IRC app", "IRC chat", "IRC server (ngIRCd)", "ngircd.service")

for words in (NG, INSP, NEITHER):
    option, desc, tile, status, unit, irc, services, apps, rows = one(words)
    name = option or "neither"
    check(f"{name}: one IRC app, no second", len(irc) == 1, [m["id"] for m in irc])
    if not irc:
        continue
    m = irc[0]
    check(
        "  its words and state are the member's",
        m["tile"]["name"] == tile
        and m["tile"]["desc"] == desc
        and (m.get("status") or {}).get("name") == status
        and (m.get("status") or {}).get("unit") == unit,
        (m["tile"], m.get("status")),
    )
    check(
        "  one service, the member's unit",
        len(services) == 1 and services[0]["name"] == status and services[0]["unit"] == unit,
        services,
    )
    check(
        "  one /admin app, named IRC chat",
        len(apps) == 1 and apps[0]["name"] == tile and apps[0]["addon"] is True,
        apps,
    )
    check("  one Access row", len(rows) == 1 and rows[0]["title"] == tile, rows)
    page = server.render_tiles("apps", server.hidden_apps())
    check(
        "  one tile, the member's description",
        page.count('class="name">IRC chat<') == 1 and desc in page and "IRC chat (InspIRCd)" not in page,
        page[page.find("IRC chat") - 80 : page.find("IRC chat") + 200],
    )
    check(
        "  no second IRC tile or service",
        page.count(">IRC chat") == 1 and len([s for s in server._services() if "IRC server" in s["name"]]) == 1,
    )

# The installed member is chosen by install.sh's own option, not by the manifest's order.
installed("--with-inspircd")
check("inspircd's option picks InspIRCd, not the first manifest", server.installed_addons() == {"inspircd"})
installed("--with-irc")
check("ngircd's option picks ngIRCd", server.installed_addons() == {"irc"})
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
