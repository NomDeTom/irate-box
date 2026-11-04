# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Query strings are parsed once, decoded, bounded and asked for as strings, flags or numbers; a
path the hub gives root for an app bundle is under the librarian's staging folder.
python3 tests/sim_queries_paths.py"""

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box import validate as V
from irate_box.hub import server
from irate_box.msg import Refused
from irate_box.root import hub_control

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Query:
    _query = server.Handler._query
    _query_one = server.Handler._query_one
    _query_flag = server.Handler._query_flag
    _query_int = server.Handler._query_int

    def __init__(self, path):
        self.path = path


q = Query("/x?a=1&b=two+words&c=%2Fadmin&names=1")
check("query: values are decoded", q._query_one("b") == "two words" and q._query_one("c") == "/admin")
check("query: a flag", q._query_flag("names") is True)
check("query: a missing value has its default", q._query_one("gone", "fallback") == "fallback")
check("query: a repeated value takes the first", Query("/x?a=1&a=2")._query_one("a") == "1")
check("query: a number", Query("/x?page=3")._query_int("page", 1, 1, 10) == 3)
check("query: a number below the range takes the default", Query("/x?page=0")._query_int("page", 1, 1, 10) == 1)
check("query: a number not a number takes the default", Query("/x?page=no")._query_int("page", 1, 1, 10) == 1)
check("query: a bounded string", q._query_one("b", "", 3) == "two")
check("query: over its length is empty", Query("/x?q=" + "a" * server.MAX_QUERY)._query() == {})
many = "/x?" + "&".join(f"f{i}=1" for i in range(server.MAX_QUERY_FIELDS + 1))
check("query: too many fields is empty", Query(many)._query() == {})


# --- a path from the hub to root: the app bundle's staging folder ---
def refused(name, fn, *args, **kw):
    try:
        fn(*args, **kw)
    except (ValueError, Refused) as exc:
        check(name, bool(str(exc)), exc)
    else:
        check(name, False, "accepted")


refused("app bundle: /etc/passwd is not a staged bundle", hub_control.install_app, "draw", "/etc/passwd")
refused("app bundle: ../ out of the staging folder", hub_control.install_app, "draw", "../outside.zip")
refused("app bundle: an empty path", hub_control.install_app, "draw", "")

T = Path(tempfile.mkdtemp(prefix="queries-"))
staging = T / "staging"
staging.mkdir()
(staging / "draw.zip").write_bytes(b"not a zip")
outside = T / "outside.zip"
outside.write_bytes(b"not a zip")
(staging / "link.zip").symlink_to(outside)
real_root, real_taken = hub_control.APP_STAGING, hub_control.APP_TAKEN
try:
    hub_control.APP_STAGING = staging
    hub_control.APP_TAKEN = T / "taken"
    check("app bundle: a staged file is inside the named folder", V.path("draw.zip", staging) == staging / "draw.zip")
    refused("app bundle: a link out of the staging folder", hub_control.install_app, "draw", str(staging / "link.zip"))
finally:
    hub_control.APP_STAGING, hub_control.APP_TAKEN = real_root, real_taken

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
