# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The offline kit made again (irate_box/hub/kitauto.py): the last export's choices kept for Refresh, and when the
librarian makes it again by itself: never by default, after each hub update, or every week; not asked twice for one
slow export. python3 tests/sim_kitauto.py"""
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import kitauto as K  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


S = Path(tempfile.mkdtemp(prefix="kitauto-"))
now = time.time()
req = {"action": "offline-kit", "books": False, "kits": [], "repos": [], "state": "none", "budget_mb": None}
kit = {"name": "irate-box-kit-1ee7f47-armv7l.tar", "at": now - 3 * 86400}
check("nothing kept yet: only when asked, nothing to refresh", K.load(S) == {"when": "manual", "request": None, "asked": 0})
K.remember(S, req)
check("an export asked for: its choices kept for Refresh", K.load(S)["request"] == req)
check("  only when asked (the default): never due by itself", K.due(S, kit, "b2582f4", now) is None)
try:
    K.set_when(S, "hourly"); check("an unknown schedule refused", False)
except ValueError:
    check("an unknown schedule refused", True)
K.set_when(S, "update")
check("after each hub update: due when the kit holds an older hub than the installed one", K.due(S, kit, "b2582f4", now) == req)
check("  not due when the kit holds the installed hub", K.due(S, kit, "1ee7f47", now) is None)
check("  a content export (no hub in it) is never due on an update",
      K.due(S, {"name": "irate-box-content-2026-10-09.tar", "at": now - 30 * 86400}, "b2582f4", now) is None)
K.asked(S, now)
check("  asked: not asked again for the same slow export within the gap", K.due(S, kit, "b2582f4", now + 60) is None
      and K.due(S, kit, "b2582f4", now + K.ASK_GAP + 1) == req)
K.set_when(S, "weekly")
check("every week: not due at 3 days", K.due(S, kit, "1ee7f47", now + K.ASK_GAP + 1) is None)
check("  due at 8 days", K.due(S, dict(kit, at=now - 8 * 86400), "1ee7f47", now + K.ASK_GAP + 1) == req)
check("the last kit gone: made again", K.due(S, None, "1ee7f47", now + K.ASK_GAP + 1) == req)
import shutil  # noqa: E402
shutil.rmtree(S, ignore_errors=True)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
