# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""confine.under: a path under its folder, or refused (the second line behind every name check,
in the form CodeQL's path-injection query recognises). python3 tests/sim_confine.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box import confine  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def refused(*a):
    try:
        confine.under(*a)
    except ValueError:
        return True
    return False
check("a name, and names in turn, under the folder", confine.under("/srv/git", "public", "a.git") == Path("/srv/git/public/a.git"))
check("  a Path for the folder works the same", confine.under(Path("/srv/git/"), "x") == Path("/srv/git/x"))
check("  a dot-name is a name", confine.under("/srv", ".a.zim.fetched") == Path("/srv/.a.zim.fetched"))
check("refused: up and out, absolute, the folder itself, a sibling sharing its prefix",
      refused("/srv/git", "..", "etc") and refused("/srv/git", "/etc/passwd") and refused("/srv/git", "")
      and refused("/srv/git", ".") and refused("/srv/git", "../git-private/x") and refused("/srv/git", "a/../../x"))
check("  but a/../b stays inside, and is b", confine.under("/srv/git", "a/../b") == Path("/srv/git/b"))
print("\nfailures:", fails)
sys.exit(1 if fails else 0)
