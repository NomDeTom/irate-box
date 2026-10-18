# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The store's budget (audit H3): an item larger than the whole budget is refused before anything is
evicted, and an item that fits is never evicted by its own write. python3 tests/sim_store_quota.py"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import store

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Clock:
    t = 0

    def ticks(self):
        Clock.t += 1
        return Clock.t


T = Path(tempfile.mkdtemp(prefix="store-quota-"))
store.MAX_TOTAL = 10_000
s = store.Store(T, Clock())
s.save("t", "old", b"a" * 3000)
s.save("t", "locked", b"b" * 3000, lock_new={"head": "0" * 64, "n": 5})
before = sorted(p.name for p in (T / "saves").iterdir())
for what, run in (
    ("save", lambda: s.save("t", "huge", b"x" * 10_001)),
    ("save with its thumbnail", lambda: s.save("t", "huge", b"x" * 9000, thumb=b"y" * 1001)),
    ("scene", lambda: s.create_scene(b"x" * 10_001)),
    ("room", lambda: s.put("rooms", "r1", b"x" * 10_001)),
):
    try:
        run()
        refused = False
    except store.TooLarge:
        refused = True
    check(
        f"a {what} over the whole budget: refused, nothing evicted",
        refused and sorted(p.name for p in (T / "saves").iterdir()) == before,
    )
m = s.save("t", "big", b"z" * 9000)
left = {p.name for p in (T / "saves").iterdir()}
check(
    "a save that fits evicts older ones, never itself",
    {m["id"] + ".data", m["id"] + ".meta"} <= left and s.get_save(m["id"]) is not None,
    left,
)
shutil.rmtree(T, ignore_errors=True)
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
