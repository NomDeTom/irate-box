# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A librarian source's failure in plain words: GitHub's 404 on a repository or a workflow, no
successful run, an expired artifact, and the rate limit with its reset time. python3 tests/sim_source_words.py"""

import sys
import time
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.library import librarian
from irate_box.msg import M

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def http404(code=404):
    return librarian.LibrarianError(
        M("library.download.http-error", "Download failed. {url} answered HTTP {code}.", url="u", code=code)
    )


with mock.patch.object(librarian, "_open", side_effect=http404(404)):
    try:
        librarian._api("/repos/NomDeTom/web-flasher/releases")
        check("a 404 on a repository is said as that", False)
    except librarian.LibrarianError as exc:
        check(
            "a 404 on a repository is said as that",
            exc.message["id"] == "library.source.no-repository"
            and "NomDeTom/web-flasher" in str(exc)
            and "no token" in str(exc),
            exc,
        )
with mock.patch.object(librarian, "_open", side_effect=http404(404)):
    try:
        librarian._api("/repos/NomDeTom/web-flasher/actions/workflows/irate-box-bundle.yml/runs")
        check("a 404 on a workflow names the workflow", False)
    except librarian.LibrarianError as exc:
        check(
            "a 404 on a workflow names the workflow",
            exc.message["id"] == "library.source.no-workflow"
            and "irate-box-bundle.yml" in str(exc)
            and "NomDeTom/web-flasher" in str(exc),
            exc,
        )

src = {
    "repo": "NomDeTom/web-flasher",
    "workflow": "irate-box-bundle.yml",
    "branch": "irate-box",
    "pattern": "irate-box-*",
}
with mock.patch.object(librarian, "_api", return_value={"workflow_runs": []}):
    try:
        librarian._newest_artifact(src, None)
        check("no successful run is said in plain words", False)
    except librarian.LibrarianError as exc:
        check(
            "no successful run is said in plain words",
            exc.message["id"] == "library.artifact.not-found-branch"
            and "has not built" in str(exc)
            and "successfully yet" in str(exc),
            exc,
        )
run = {"id": 1, "event": "push", "head_repository": {"full_name": src["repo"]}}
with mock.patch.object(
    librarian,
    "_api",
    side_effect=[{"workflow_runs": [run]}, {"artifacts": [{"name": "irate-box-1.zip", "expired": True}]}],
):
    try:
        librarian._newest_artifact(src, None)
        check("an expired artifact says it expired", False)
    except librarian.LibrarianError as exc:
        check(
            "an expired artifact says it expired",
            exc.message["id"] == "library.artifact.expired" and "90 days" in str(exc),
            exc,
        )

# The rate limit's message carries its reset time.
import urllib.error

headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(time.time()) + 1800)}
with mock.patch.object(
    librarian.urllib.request, "urlopen", side_effect=urllib.error.HTTPError("u", 403, "no", headers, None)
):
    try:
        librarian._open("https://api.github.com/repos/a/b/releases")
        check("the rate limit names its reset time", False)
    except librarian.LibrarianError as exc:
        check(
            "the rate limit names its reset time",
            exc.message["id"] == "library.github.rate-limited" and "resets at" in str(exc),
            exc,
        )
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
