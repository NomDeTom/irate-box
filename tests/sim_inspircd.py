# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""InspIRCd as an optional IRC add-on: the manifest the hub reads, and the installer parts that
make it one of the two IRC servers on :6667. Static: no server is started.
python3 tests/sim_inspircd.py"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import manifests

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


apps = {m["id"]: m for m in manifests.load()}
m = apps.get("inspircd") or {}
check("the hub loads an inspircd add-on manifest", bool(m), sorted(apps))
check(
    "  it is an install.sh add-on on :6667 with its own unit and marker",
    (m.get("addon") or {}).get("option") == "--with-inspircd"
    and m.get("network", {}).get("present") == "/etc/inspircd/irate-box.conf"
    and m.get("status", {}).get("unit") == "inspircd.service"
    and any(p.get("port") == 6667 for p in m.get("network", {}).get("listen", [])),
    m,
)
check(
    "  its consent names the port and what is missing",
    "6667" in (m.get("addon") or {}).get("consent", "")
    and "no encryption" in (m.get("addon") or {}).get("consent", ""),
    (m.get("addon") or {}).get("consent"),
)

text = (REPO / "install.sh").read_text()
check(
    "the installer has the option, record, keep, package, unit and removal",
    all(
        s in text
        for s in (
            "--with-inspircd)",
            "WITH_INSPIRCD=1",
            "keep_addon inspircd WITH_INSPIRCD",
            "pkgs+=(inspircd)",
            "units+=(inspircd)",
            "inspircd)",
            "/etc/systemd/system/inspircd.service.d/irate-box.conf",
        )
    ),
)
check(
    "  one IRC server only: the other is stopped and removed",
    "Choose one IRC server" in text and "REMOVE+=(irc)" in text and "REMOVE+=(inspircd)" in text,
)
config = text[text.index('<server name="irc.irate-box"') :]
check(
    "  the config has the limits, the cloak, no operator, and #lobby for everyone",
    'localmax="5"' in config
    and 'globalmax="5"' in config
    and '<module name="cloak"' in config
    and '<module name="cloak_sha256"' in config
    and 'modes="x"' in config
    and "<oper" not in config
    and '<autojoin channel="#lobby">' in config
    and '<permchannels channel="#lobby"' in config,
    config[:200],
)
check(
    "uninstall.sh stops it and removes its config and drop-in",
    all(
        s in (REPO / "uninstall.sh").read_text()
        for s in ("inspircd", "/etc/inspircd/irate-box.conf", "inspircd.service.d")
    ),
)
check(
    "the Debian package suggests it, and About names its licence",
    "inspircd" in (REPO / "packaging/deb/control").read_text() and "InspIRCd" in (REPO / "web/about.html").read_text(),
)

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
