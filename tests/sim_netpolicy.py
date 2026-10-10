# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Updates and big downloads on metered networks (hub/netpolicy.py), offline with ip and nmcli stood in:
NetworkManager's metered setting and its guess, a phone's hotspot by its addresses, a network set not
metered never counted; the scheduled run waiting and saying since when, clearing once it goes ahead;
the librarian's scheduled run stopping before anything; the Box doctor after three days.
python3 tests/sim_netpolicy.py"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="netpolicy-"))
os.environ.update(HUB_STATE_DIR=str(T))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import netpolicy as N  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def fake(metered, addr="192.168.1.181/24", route=True):
    def run_(*cmd, timeout=15):
        if cmd[:2] == ("ip", "-4"):
            return 0, "default via 192.168.1.1 dev wlan0 proto dhcp" if route else ""
        if cmd[0] == "nmcli":
            return 0, f"GENERAL.METERED:{metered}\nGENERAL.CONNECTION:Home\nIP4.ADDRESS[1]:{addr}"
        return 1, ""
    return run_


cases = [("yes", True, "set"), ("yes (guessed)", True, "guessed by NetworkManager"), ("no", False, "set not metered"),
         ("no (guessed)", False, "not metered"), ("unknown", False, "not metered")]
for said, metered, how in cases:
    m = N.metered_now(fake(said))
    check(f"NetworkManager says {said!r}: metered {metered} ({how})", m["metered"] is metered and m["how"] == how and m["connection"] == "Home", m)
m = N.metered_now(fake("no (guessed)", addr="172.20.10.2/28"))
check("an iPhone's hotspot by its addresses: metered", m["metered"] and m["how"] == "looks like a phone's hotspot", m)
check("  but not when the owner set the network not metered", not N.metered_now(fake("no", addr="172.20.10.2/28"))["metered"])
check("no route: not metered, said why", N.metered_now(fake("yes", route=False)) == {"iface": None, "connection": None, "metered": False,
                                                                                    "how": "no route to the internet"})

why = N.gate(now=1000, run_=fake("yes"))
w = N.waiting()
check("the scheduled run waits, saying why, and since when", why.startswith("waiting: on a metered network (Home, set)") and w["since"] == 1000, (why, w))
N.gate(now=5000, run_=fake("yes"))
check("  still waiting later: since the first time", N.waiting()["since"] == 1000 and N.waiting()["last"] == 5000)
check("  on another network: goes ahead, and the wait forgotten", N.gate(now=6000, run_=fake("no (guessed)")) is None and N.waiting() is None)

# The librarian's scheduled run stops before it starts anything.
from irate_box.library import librarian  # noqa: E402
N.gate = lambda *a, **k: "waiting: on a metered network (Phone, set)"
said = []
out = librarian.update(scheduled=True, log=said.append)
check("librarian: a scheduled run on a metered network does nothing, and says why",
      out == {"_metered": "waiting: on a metered network (Phone, set)"} and said == [out["_metered"]], (out, said))

# The Box doctor, after three days of waiting.
from irate_box.root import health  # noqa: E402
N.WAIT.write_text('{"since": 0, "last": 10, "connection": "Phone", "iface": "wlan0", "how": "set"}')
check("doctor: nothing within three days", health.metered_findings(now=2 * 86400) == [])
f = health.metered_findings(now=4 * 86400)
check("  after: a warning, how long and which network, and what to do", f and f[0]["status"] == "warn" and "For 4 days" in f[0]["detail"]
      and "Phone" in f[0]["detail"] and "not metered" in f[0]["fix"], f)

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
