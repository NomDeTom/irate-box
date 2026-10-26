# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Passwords being set, as the administrator chooses (Security, Sign-in and identity): the shortest allowed, the rules
(none, letters and digits, three kinds), the commonest refused, and never the username; a password set before a
stricter choice still signs in. And the hub's memory: at most two password checks at once, the rest told
the box is busy; the hub's unit with a memory ceiling; nginx limiting sign-ins per address.
python3 tests/sim_passwords.py"""

import os
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="passwords-"))
os.environ.update(HUB_STATE_DIR=str(T))
sys.path.insert(0, str(REPO))
from irate_box.hub import accounts as A

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


choice = dict(A.SIGNIN_DEFAULTS)
A.SIGNIN = lambda: choice


def said(pw, name=None):
    """The refusal's message id, or None when the password is taken."""
    try:
        A._valid_password(pw, name)
        return None
    except A.AccountError as e:
        return e.message["id"]


check("by default: 8 characters (12 and 15 are the administrator's choices)", said("tidepool") is None)
check("  7: refused, saying how long", said("tidepoo") == "accounts.password.length")
choice["min_length"] = 8
check("shortest 8: 8 taken", said("tide pool") is None)
check("  one of the commonest: refused", said("password1") == "accounts.password.common")
check("  the username in it: refused", said("alice in chains", "alice") == "accounts.password.name")
choice["block_common"] = False
check("common passwords allowed (the administrator's choice): taken", said("password1") is None)
choice["min_length"] = 15
check("back to 15: 12 refused", said("tide pool 12") == "accounts.password.length")
try:
    A._valid_password("short", None)
except A.AccountError as e:
    check("  the refusal says 15", e.message["args"]["least"] == 15, e.message)
check("  a phrase of 15 taken", said("tide pool at dusk") is None)
choice["min_length"] = 8
choice["rules"] = "letters-digits"
check("letters and digits: letters alone refused", said("tidepools") == "accounts.password.letters-digits")
check("  digits alone refused", said("12345678912") == "accounts.password.letters-digits")
check("  both taken", said("tidepool9") is None)
choice["rules"] = "three-kinds"
check("three kinds: two refused", said("tidepool9") == "accounts.password.three-kinds")
check("  three taken", said("Tidepool9") is None and said("tide pool 9") is None)
choice.update(A.SIGNIN_DEFAULTS)

# A password set before a stricter choice still signs in: the rules are for passwords being set.
A.set_settings(signup="open")
choice["min_length"] = 8
A.signup("carol", "tide pool", "10.0.0.1")
choice["min_length"], choice["rules"] = 15, "three-kinds"
check(
    "a password set before stricter choices: still signs in",
    A.login("carol", "tide pool", "10.0.0.1")[1]["name"] == "carol",
)
choice.update(A.SIGNIN_DEFAULTS)

# At most two password checks at once; a third is told the box is busy.
A.HASH_WAIT = 0.2
stored = A.hash_password("tide pool")
for _ in range(A.HASHES_AT_ONCE):
    A._hashing.acquire()
try:
    A.check_password("tide pool", stored)
    busy = None
except A.Wait as e:
    busy = e.message["id"]
check("two checks running: a third waits, then is told the box is busy", busy == "accounts.busy", busy)
for _ in range(A.HASHES_AT_ONCE):
    A._hashing.release()
check("  freed: checked", A.check_password("tide pool", stored))
results = []
threads = [threading.Thread(target=lambda: results.append(A.check_password("tide pool", stored))) for _ in range(4)]
A.HASH_WAIT = 30
for t in threads:
    t.start()
for t in threads:
    t.join()
check("  four at once, with time to wait: all checked, two at a time", results == [True] * 4, results)

inst = (REPO / "install.sh").read_text()
unit = inst[inst.index("cat >/etc/systemd/system/irate-box.service") :]
unit = unit[: unit.index("\nEOF")]
check(
    "the hub's unit: a memory ceiling, and freed memory given back",
    "MemoryHigh=" in unit and "MemoryMax=" in unit and "Environment=MALLOC_ARENA_MAX=2" in unit,
)
nginx = (REPO / "config/irate-box.nginx").read_text()
check(
    "nginx limits sign-ins per address, and says so with a 429",
    "limit_req_zone $binary_remote_addr zone=irate_box_account" in nginx
    and "location = /api/account {" in nginx
    and "limit_req zone=irate_box_account" in nginx
    and "limit_req_status 429;" in nginx,
)
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
