# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The WiFi country (the kernel's regulatory domain): which channels and power each radio may use. The countries
to choose from, the one saved for boot, and the file that saves it.

  countries()  {code: name}: Debian's iso-codes list where the box has it, else the small list the hub ships
  by_name()    [(code, name)] in the order a person reads them: by name, accents aside (Åland under A)
  saved()      the country the boot-time file sets, or None
  conf_text()  that file's text for a country chosen on the Network page

The file is cfg80211's module option (options cfg80211 ieee80211_regdom=XX). root/nmhandover.py writes the same
file when it takes over a netplan file that set a country; the Network page's setting writes over it. Applying
a country now (iw reg set) is the root helper's (wifi-country). Stdlib only.

The names: iso-codes is the standard list, but a minimal image doesn't install it (23 MB, nearly all of it
translations), so the hub ships countries.json, the two-letter codes and English names taken from it
(tools/make-countries.py). A box that has iso-codes (one with a desktop, as Raspberry Pi OS) reads its own copy,
which can be newer, with the same choice of names. The page is in English. If it is ever translated, the country
names need the full iso-codes (its iso_3166-1 translations), installed with the language, not this list.
"""

import json
import os
import re
import unicodedata
from pathlib import Path

from irate_box import validate

ISO_CODES = Path(os.environ.get("HUB_ISO_CODES", "/usr/share/iso-codes/json/iso_3166-1.json"))
SHIPPED = Path(__file__).with_name("countries.json")
REGDOM_CONF = Path(os.environ.get("HUB_MODPROBE_DIR", "/etc/modprobe.d")) / "irate-box-regdom.conf"
SET_HERE = "# irate-box: the WiFi country chosen on the Network page"
OPTION_RE = re.compile(r"^\s*options\s+cfg80211\s+ieee80211_regdom=([A-Za-z]{2})\s*$", re.M)


def _iso_codes():
    """{code: name} from the box's own iso-codes, the common name where there is one; {} when it is missing or
    unreadable."""
    try:
        entries = json.loads(ISO_CODES.read_text(encoding="utf-8"))["3166-1"]
        return {
            e["alpha_2"]: e.get("common_name") or e["name"]
            for e in entries
            if isinstance(e.get("alpha_2"), str) and len(e["alpha_2"]) == 2 and e.get("name")
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def countries():
    """{code: name} of every country, by code: the box's iso-codes when it has them, else the shipped list."""
    found = _iso_codes()
    if not found:
        try:
            found = json.loads(SHIPPED.read_text(encoding="utf-8"))["countries"]
        except (OSError, ValueError, KeyError):
            found = {}
    return {code: name.strip() for code, name in sorted(found.items()) if code.isalpha() and code.isupper()}


def _sort_key(name):
    """A name as it sorts for a reader: accents taken off (Côte d'Ivoire with Costa Rica, Åland with Albania)."""
    plain = unicodedata.normalize("NFKD", name)
    return "".join(c for c in plain if not unicodedata.combining(c)).casefold()


def by_name():
    """The countries as the Network page lists them: [(code, name)] by name."""
    return sorted(countries().items(), key=lambda pair: _sort_key(pair[1]))


def saved():
    """The country the boot-time file sets, upper case, or None."""
    try:
        found = OPTION_RE.search(REGDOM_CONF.read_text())
    except OSError:
        return None
    return found.group(1).upper() if found else None


def conf_text(code):
    return f"{SET_HERE}\noptions cfg80211 ieee80211_regdom={validate.country(code)}\n"
