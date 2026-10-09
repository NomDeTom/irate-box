# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Unique visitors, counted without keeping who they were: through hashing, and said plainly.

A small helper reading MAC addresses and leases, disabled when the count isn't used: a small service of its own (irate-box-visitors.service), running only
while the owner has counting on, unprivileged (DynamicUser), (the hub records the choice in $STATE/visitors.want and a root
path unit starts or stops this; scripts/visitors-apply.sh).

Every minute it reads the network addresses of the devices the box has seen: the DHCP leases it
handed out (dnsmasq's, NetworkManager's shared mode's) and the kernel's neighbour table
(/proc/net/arp). Each address is hashed (HMAC-SHA256) with a salt made at random for the day,
and again with one for the week; the hashes go in two sets in this process's memory and nowhere
else. When the day ends its salt and set are dropped (the week's likewise), so no hash can be
turned back into a device or matched with another day's. All it writes is two numbers, for the
hub's People tile: how many different devices today and this week, in /run (gone at reboot).
Restarting it starts the counts again. Phones that use a random address per network may count
more than once, so the numbers are approximate, and the hub says so where it shows them.

Stdlib only. Run as: irate-box visitors   (--once: one sweep, for the tests)
"""

import datetime
import glob
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

LEASES = os.environ.get("VISITORS_LEASES",
                        "/var/lib/misc/dnsmasq.leases:/var/lib/NetworkManager/dnsmasq-*.leases")
ARP = Path(os.environ.get("VISITORS_ARP", "/proc/net/arp"))
OUT = Path(os.environ.get("VISITORS_OUT", "/run/irate-box-visitors/counts.json"))
EVERY = int(os.environ.get("VISITORS_EVERY", "60"))
MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
NOBODY = "00:00:00:00:00:00"


def macs():
    """The addresses seen: from the leases (field 2 of a dnsmasq lease) and the neighbour table
    (field 4 of /proc/net/arp, complete entries only). Lower-case; never kept beyond the sweep."""
    seen = set()
    for pattern in LEASES.split(":"):
        for path in glob.glob(pattern):
            try:
                for line in Path(path).read_text(errors="replace").splitlines():
                    parts = line.split()
                    if len(parts) >= 2:
                        seen.add(parts[1].lower())
            except OSError:
                continue
    try:
        for line in ARP.read_text(errors="replace").splitlines()[1:]:
            parts = line.split()
            # IP, HW type, flags (0x2 = complete), HW address, mask, device
            if len(parts) >= 4 and parts[2] != "0x0":
                seen.add(parts[3].lower())
    except OSError:
        pass
    return {m for m in seen if MAC_RE.match(m) and m != NOBODY}


class Counter:
    """Two windows, the day and the ISO week, each a salt and a set of hashes, in memory only."""

    def __init__(self):
        self.windows = {}

    def _window(self, name, key):
        w = self.windows.get(name)
        if not w or w["key"] != key:
            # A new day or week: the old salt and hashes go, and nothing links the two.
            w = {"key": key, "salt": secrets.token_bytes(32), "seen": set()}
            self.windows[name] = w
        return w

    def sweep(self, addresses, now=None):
        t = time.localtime(now if now is not None else time.time())
        day = time.strftime("%Y-%m-%d", t)
        iso = datetime.date(t.tm_year, t.tm_mon, t.tm_mday).isocalendar()
        week = "%d-W%02d" % (iso[0], iso[1])
        for name, key in (("day", day), ("week", week)):
            w = self._window(name, key)
            for mac in addresses:
                w["seen"].add(hmac.new(w["salt"], mac.encode(), hashlib.sha256).digest()[:16])
        return {"day": len(self.windows["day"]["seen"]), "week": len(self.windows["week"]["seen"]),
                "date": day, "week_of": week, "at": int(now if now is not None else time.time())}


def write(counts):
    """Only the numbers, readable by the hub; written whole."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.parent / (OUT.name + ".tmp")
    tmp.write_text(json.dumps(counts) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)


def main(argv):
    counter = Counter()
    while True:
        write(counter.sweep(macs()))
        if "--once" in argv:
            return 0
        time.sleep(EVERY)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
