# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The WiFi country (the kernel's regulatory domain): which channels and power each radio may use. The countries
to choose from, the one saved for boot, and the file that saves it.

  countries()  {code: name}, from tzdata's ISO 3166 table (on every Debian box), so the hub ships no list
  saved()      the country the boot-time file sets, or None
  conf_text()  that file's text for a country chosen on the Network page

The file is cfg80211's module option (options cfg80211 ieee80211_regdom=XX). root/nmhandover.py writes the same
file when it takes over a netplan file that set a country; the Network page's setting writes over it. Applying
a country now (iw reg set) is the root helper's (wifi-country). Stdlib only.
"""

import os
import re
from pathlib import Path

ISO_TABLE = Path(os.environ.get("HUB_ISO3166", "/usr/share/zoneinfo/iso3166.tab"))
REGDOM_CONF = Path(os.environ.get("HUB_MODPROBE_DIR", "/etc/modprobe.d")) / "irate-box-regdom.conf"
SET_HERE = "# irate-box: the WiFi country chosen on the Network page"
OPTION_RE = re.compile(r"^\s*options\s+cfg80211\s+ieee80211_regdom=([A-Za-z]{2})\s*$", re.M)


def countries():
    """{code: name} of every country, in the table's order (by code); empty when the table is missing."""
    out = {}
    try:
        lines = ISO_TABLE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        code, _, name = line.partition("\t")
        if len(code) == 2 and code.isalpha() and code.isupper() and name.strip():
            out[code] = name.strip()
    return out


def saved():
    """The country the boot-time file sets, upper case, or None."""
    try:
        found = OPTION_RE.search(REGDOM_CONF.read_text())
    except OSError:
        return None
    return found.group(1).upper() if found else None


def conf_text(code):
    return f"{SET_HERE}\noptions cfg80211 ieee80211_regdom={code}\n"
