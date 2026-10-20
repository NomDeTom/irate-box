# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The update doctor (hub_control.doctor) in finding.py's shape, with the network, git, apt and the disk stood in:
each case said by its message ID, its commands, what it saw and the place to go as data, a place only for what is
not fine; then the checks of a downloaded version and the update actions' answers as messages, the result file
keeping the English for every reader that expects text. python3 tests/sim_update_doctor.py"""

# The stand-ins defined inside the loops below are called in the iteration that defines them, so B023 does not apply.
# ruff: noqa: B023

import io
import json
import os
import pwd
import socket
import ssl
import subprocess  # CompletedProcess mock results only. # nosec B404
import sys
import tempfile
import threading
import time
import urllib.parse
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="updoctor-"))
for d in ("state/control/results", "state/control/requests", "etc", "code", "cache"):
    (T / d).mkdir(parents=True)
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_RUN_DIR=str(T / "run"),
    HUB_CODE_DIR=str(T / "code"),
    HUB_UPDATE_DIR=str(T / "cache" / "src"),
    HUB_DOWNLOAD_CACHE=str(T / "cache" / "downloads"),
    HUB_WEB_SERVER="nginx",
)
sys.path.insert(0, str(REPO))
from irate_box.finding import english
from irate_box.finding import message_args as msg_args
from irate_box.finding import message_ids as msg_ids
from irate_box.msg import is_message, render
from irate_box.root import hub_control as H

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Resp(io.BytesIO):
    def __init__(self, body=b"", headers=None):
        super().__init__(body)
        self.headers = headers or {}


def world(
    *,
    addrs=("140.82.121.3",),
    mine=(),
    connect=True,
    date=None,
    tls_error=None,
    remaining=50,
    heads="abc1234def refs/heads/main\n",
    ls_code=0,
    apt=(0, "Hit:1 https://deb.debian.org/debian trixie InRelease\n"),
    free_mb=5000,
    head_sha="",
    bash_code=0,
    status_code=0,
    remote="https://example.invalid/irate-box",
):
    """The network, git, apt and the disk as one case wants them."""

    def getaddrinfo(host, port, proto=0):
        if addrs is None:
            raise OSError("Temporary failure in name resolution")
        return [(2, 1, 6, "", (a, port)) for a in addrs]

    def create_connection(addr, timeout=None):
        if not connect:
            raise OSError("timed out")
        return io.BytesIO()

    def tls_get(url, timeout=15):
        if urllib.parse.urlsplit(url).hostname == "github.com":
            if tls_error:
                raise tls_error
            return Resp(headers={"Date": formatdate(date or time.time(), usegmt=True)})
        return Resp(
            json.dumps(
                {"resources": {"core": {"remaining": remaining, "limit": 60, "reset": time.time() + 1800}}}
            ).encode()
        )

    def run(*cmd, timeout=120, **kw):
        if cmd[0] == "git" and "ls-remote" in cmd:
            return subprocess.CompletedProcess(
                cmd, ls_code, heads if ls_code == 0 else "", "fatal: repository not found\n"
            )
        if cmd[0] == "apt-get":
            return subprocess.CompletedProcess(cmd, apt[0], apt[1], "")
        if cmd[:2] == ("bash", "-n"):
            return subprocess.CompletedProcess(cmd, bash_code, "", "")
        if cmd[0] == "git" and "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 0, head_sha + "\n", "")
        if cmd[0] == "git" and "status" in cmd:
            return subprocess.CompletedProcess(cmd, status_code, "", "fatal: not a git repository\n")
        if cmd[0] == "git" and "get-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, remote + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    usage = mock.Mock(free=free_mb << 20)
    return [
        mock.patch("socket.getaddrinfo", getaddrinfo),
        mock.patch("socket.create_connection", create_connection),
        mock.patch.object(H, "_tls_get", tls_get),
        mock.patch.object(H, "run", run),
        mock.patch.object(H, "_local_addresses", lambda: set(mine)),
        mock.patch.object(H.shutil, "disk_usage", lambda p: usage),
        mock.patch.object(H.kits, "verify", lambda: []),
        mock.patch.object(H.kits, "definitions", lambda: {}),
        mock.patch.object(H.kits, "installed_state", lambda: {}),
        mock.patch.object(H.kits, "pool_bytes", lambda: 0),
    ]


def doctor(**case):
    patches = world(**case)
    for p in patches:
        p.start()
    try:
        return {f["id"]: f for f in H.doctor()}
    finally:
        for p in patches:
            p.stop()


def well_said(fs):
    """Every finding in the shared shape: its title and summary messages, said in English with every placeholder
    filled; a place to go only when it is not fine; evidence and commands as text."""
    bad = []
    for f in fs.values():
        said = [render(m) for m in (f["title"], f["summary"], f.get("why"), f.get("fix"), *f["notes"]) if m is not None]
        if (
            not (is_message(f["title"]) and is_message(f["summary"]))
            or any("{" in s for s in said)
            or (f["status"] == "ok" and f.get("go"))
            or not all(isinstance(e, str) for e in f["evidence"] + f["commands"])
        ):
            bad.append(f["id"])
        english(f)
    return bad


OPTIONS = T / "etc" / "install-options"
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n")

# All well: each part fine, nowhere to go.
fs = doctor()
check(
    "all well: every check fine, but the schedule (no run recorded yet)",
    {k for k, f in fs.items() if f["status"] != "ok"} == {"scheduled"},
    {k: f["status"] for k, f in fs.items()},
)
check(
    "  each said by its own message: options, names, connection, clock, certificate, GitHub, branch, space, cache, sources",
    msg_args(fs["options"], "update.options.ok") == {"branch": "main", "repo": "https://example.invalid/irate-box"}
    and "update.dns.ok" in msg_ids(fs["dns"])
    and "update.connection.ok" in msg_ids(fs["connection"])
    and "update.clock.ok" in msg_ids(fs["clock"])
    and "update.tls.ok" in msg_ids(fs["tls"])
    and msg_args(fs["github-api"], "update.github-api.ok") == {"left": 50, "limit": 60}
    and msg_args(fs["repo"], "update.repo.ok") == {"branch": "main", "commit": "abc1234"}
    and "update.space.ok" in msg_ids(fs["space:system"])
    and "update.cache.empty" in msg_ids(fs["cache"])
    and "update.sources.ok" in msg_ids(fs["sources"]),
    fs,
)
check(
    "  no run of the schedule recorded: a warning, the log's command to copy, the librarian named as the place",
    "update.scheduled.none" in msg_ids(fs["scheduled"])
    and fs["scheduled"]["commands"] == ["journalctl -u irate-box-librarian -n 80"]
    and fs["scheduled"]["go"]["section"] == "sources",
    fs["scheduled"],
)
check("  every finding well said, in the shared shape", not well_said(fs), well_said(fs))
check("  the default branch: no finding about the update branch", "target" not in fs, fs.get("target"))

# A box that followed the branch it was installed from: a warning that names the branch and the file.
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nfix-topic\n")
fs = doctor()
check(
    "a topic branch recorded: a warning with the branch as data, the cure said, the options file as evidence",
    fs["target"]["status"] == "warn"
    and msg_args(fs["target"], "update.target.branch") == {"branch": "fix-topic"}
    and {"update.target.branch.why", "update.target.branch.fix"} <= msg_ids(fs["target"])
    and fs["target"]["evidence"] == [str(OPTIONS)],
    fs["target"],
)
check(
    "  its link lands on the Branch field",
    fs["target"]["go"]["mark"] == "update-source-branch",
    fs["target"].get("go"),
)
check(
    "  a button to ask the repository for its default branch",
    [a["choice"] for a in fs["target"]["actions"]] == ["poll-target"]
    and is_message(fs["target"]["actions"][0]["label"]),
    fs["target"]["actions"],
)
check("  every finding well said", not well_said(fs), well_said(fs))


# The button: ask the repository which branch it serves by default.
def poll(out, code=0):
    def run(*cmd, **kw):
        return subprocess.CompletedProcess(cmd, code, out, "fatal: no\n")

    with mock.patch.object(H, "run", run):
        try:
            return H.update_poll_target({})
        except H.Refused as exc:
            return exc.args[0] if exc.args else exc


SYMREF = "ref: refs/heads/main\tHEAD\nabc1234def\tHEAD\n"
said = poll(SYMREF)
check(
    "polling: the default is main, the box follows fix-topic: both said as data",
    msg_args({"summary": said}, "update.target.poll.differs")
    == {"repo": "https://example.invalid/irate-box", "default": "main", "branch": "fix-topic"},
    said,
)
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n")
check(
    "  the same branch: said so",
    msg_args({"summary": poll(SYMREF)}, "update.target.poll.same") is not None,
    poll(SYMREF),
)
check(
    "  no symref from the server: said it did not say",
    msg_args({"summary": poll("abc1234def\tHEAD\n")}, "update.target.poll.unknown") is not None,
)
check(
    "  the repository unreadable: refused, and said",
    "could not read https://example.invalid/irate-box" in str(poll("", 128)),
    poll("", 128),
)

# Changing the source (a fork): checked before any git runs, recorded only once the remote lists the branch.
GOOD = "https://example.org/fork/irate-box"
calls = []


def source(req, heads="abc1234def\trefs/heads/dev\n", code=0):
    def run(*cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, code, heads if code == 0 else "", "fatal: no\n")

    with mock.patch.object(H, "run", run):
        try:
            return H.update_set_source(req)
        except H.Refused as exc:
            return exc.args[0] if exc.args else exc


before = OPTIONS.read_text()
for bad in (
    "http://example.org/x",
    "git://example.org/x",
    "file:///srv/x",
    "ssh://git@example.org/x",
    "git@example.org:x/y.git",
    "/home/hub/irate-box",
    "https://user:pw@example.org/x",
    "https://example.org/x?a=1",
    "https://example.org/x#f",
    "https:///x",
    "https://example.org/x y",
    "",
    "https://example.org:notaport/x",
):
    calls.clear()
    got = source({"repo": bad, "branch": "dev", "confirmed": True})
    check(
        f"a source of {bad!r}: refused before git runs, nothing recorded",
        ("cannot be used" in str(got) or "Updates are not signed" in str(got))
        and not calls
        and OPTIONS.read_text() == before,
        (got, calls),
    )
for bad in ("", "-x", "a..b", "a b", "x.lock", "x/", "$(id)"):
    calls.clear()
    got = source({"repo": GOOD, "branch": bad, "confirmed": True})
    check(f"a branch of {bad!r}: refused before git runs", not calls and OPTIONS.read_text() == before, (got, calls))
(T / "etc" / "update-signing.json").write_text(json.dumps({"level": "github", "signers": []}))
for ok in ("http://example.org/x", "git://example.org/x", "file:///srv/x", "/home/hub/irate-box"):
    calls.clear()
    got = source({"repo": ok, "branch": "dev", "confirmed": True})
    check(
        f"signing on: a source of {ok!r} is asked about its branch, with git's allow-list on the command",
        calls
        and calls[0][:3] == ("git", "-c", "protocol.allow=never")
        and "protocol.https.allow=always" in calls[0]
        and "ls-remote" in calls[0],
        (got, calls),
    )
(T / "etc" / "update-signing.json").unlink()
OPTIONS.write_text(before)
calls.clear()
got = source({"repo": GOOD, "branch": "dev"})
check("not confirmed: refused before git runs", not calls and OPTIONS.read_text() == before, (got, calls))
got = source({"repo": GOOD, "branch": "nope", "confirmed": True})
check(
    "a branch the remote does not list: refused, nothing recorded",
    "no branch named nope" in str(got) and OPTIONS.read_text() == before,
    got,
)
got = source({"repo": GOOD, "branch": "dev", "confirmed": True}, code=128)
check(
    "a repository that cannot be read: refused, nothing recorded",
    "could not read" in str(got) and OPTIONS.read_text() == before,
    got,
)
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n--web\nnginx\n--with-notes\n")
(T / "cache" / "src").mkdir(parents=True, exist_ok=True)
(T / "cache" / "src" / "x").write_text("old cache")
got = source({"repo": GOOD, "branch": "dev", "confirmed": True})
check(
    "a fork the remote lists: recorded in both copies, the other options kept, the old cache gone",
    OPTIONS.read_text() == f"--repo\n{GOOD}\n--branch\ndev\n--web\nnginx\n--with-notes\n"
    and (T / "state" / "install-options").read_text() == OPTIONS.read_text()
    and not (T / "cache" / "src").exists()
    and msg_args({"summary": got}, "update.source.saved") == {"repo": GOOD, "branch": "dev"},
    got,
)
check(
    "  security policy: refused when only the console may change it, the poll left free",
    "update-set-source" in H.POLICY_ACTIONS and "update-poll-target" not in H.POLICY_ACTIONS,
)
with mock.patch.object(H, "policy_mode", lambda: "console"):
    try:
        H.policy_check("update-set-source")
        locked = False
    except H.Refused:
        locked = True
check("  and it is", locked)
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n")

# The hotspot: every name is the box's own; nothing answers; the card is nearly full; a lock left in the cache; apt fails.
(T / "cache" / "src" / ".git").mkdir(parents=True)
(T / "cache" / "src" / ".git" / "index.lock").write_text("")
apt_out = (
    "Err:5 https://dl.example.org/deb stable InRelease\n  The following signatures were invalid: EXPKEYSIG 1234\n"
    "E: The repository 'https://dl.example.org/deb stable InRelease' is not signed.\n"
)
H.UPDATE_STATE.write_text(
    json.dumps(
        {
            "checks": [
                H._check(
                    "python",
                    False,
                    H.M("update.verify.python.title", "Python files compile"),
                    H.M(
                        "update.verify.python.failed",
                        {"one": "{n} Python file does not compile.", "other": "{n} Python files do not compile."},
                        n=1,
                    ),
                    evidence=["irate_box/x.py: invalid syntax (x.py, line 1)"],
                ),
                {
                    "name": "install.sh parses",
                    "ok": False,
                    "detail": "line 3: syntax error",
                    "warn": False,
                },  # kept from before the messages
                {"name": "A warning only", "ok": False, "detail": "", "warn": True},
            ]
        }
    )
)
fs = doctor(addrs=("192.168.4.1",), mine=("192.168.4.1",), connect=False, free_mb=100, apt=(100, apt_out))
check(
    "hotspot: the name leads to the box itself, said so, the address as evidence, Network the place to go",
    fs["dns"]["status"] == "problem"
    and "update.dns.self" in msg_ids(fs["dns"])
    and fs["dns"]["evidence"] == ["192.168.4.1"]
    and fs["dns"]["go"]["section"] == "network",
    fs["dns"],
)
check(
    "  the connection failed: said with what one failed attempt does not show, the error as evidence",
    {"update.connection.failed", "update.probe.note"} <= msg_ids(fs["connection"])
    and fs["connection"]["evidence"] == ["timed out"],
    fs["connection"],
)
check(
    "  nothing that needs the connection is claimed (certificate, GitHub, branch)",
    not {"tls", "github-api", "repo", "clock"} & set(fs),
    sorted(fs),
)
check(
    "  space: both short, with how much is free and needed, and Books the place to make room",
    msg_args(fs["space:system"], "update.space.low") == {"mb": 100, "need": 300}
    and msg_args(fs["space:cache"], "update.space.low") == {"mb": 100, "need": 200}
    and fs["space:system"]["go"]["section"] == "books",
    fs["space:system"],
)
check(
    "  a stale cache lock: counted, named, no link (the cure is a button on the finding)",
    msg_args(fs["cache"], "update.cache.lock") == {"n": 1}
    and fs["cache"]["evidence"] == ["index.lock"]
    and not fs["cache"].get("go"),
    fs["cache"],
)
check(
    "  apt fails: the failing source named, a command to find its list file, keep a copy before removing it",
    "update.sources.failed" in msg_ids(fs["sources"])
    and "update.sources.failed.fix" in msg_ids(fs["sources"])
    and fs["sources"]["commands"] == ["grep -rlF dl.example.org /etc/apt/sources.list /etc/apt/sources.list.d/"]
    and "https://dl.example.org/deb" in fs["sources"]["evidence"]
    and "safe place" in render(fs["sources"]["fix"]),
    fs["sources"],
)
lc = fs.get("last-check", {})
check(
    "  the last download's failed checks: counted (a warning is not), each said, the new by its message, the old by its words",
    msg_args(lc, "update.last-check.failed") == {"n": 2}
    and any(is_message(n) and n["id"] == "update.last-check.item" for n in lc["notes"])
    and "install.sh parses: line 3: syntax error" in lc["notes"]
    and any("x.py" in e for e in lc["evidence"])
    and lc.get("go") is None,
    lc,
)
check("  every finding well said", not well_said(fs), well_said(fs))
CLEAR = [("clear-cache", "update.cache.clear.button", "update.cache.clear.confirm")]


def cure(f):
    return [(a["choice"], a["label"]["id"], a["confirm"]["id"]) for a in f["actions"]]


check("  the lock has the button to clear the update cache, behind a question", cure(fs["cache"]) == CLEAR, fs["cache"])
check("  no Install anyway without a downloaded version that was checked", not lc["actions"], lc["actions"])

# A cache that cannot be read, and one copied from another repository: the same button.
(T / "cache" / "src" / ".git" / "index.lock").unlink()
fs = doctor(status_code=128)
check(
    "a cache that cannot be read: the same button, behind the same question",
    "update.cache.damaged" in msg_ids(fs["cache"]) and cure(fs["cache"]) == CLEAR,
    fs["cache"],
)
fs = doctor(remote="https://example.invalid/other")
check(
    "  a cache from another repository: a warning, the same button",
    fs["cache"]["status"] == "warn" and cure(fs["cache"]) == CLEAR,
    fs["cache"],
)
fs = doctor()
check("  a cache that is intact: no button", fs["cache"]["status"] == "ok" and not fs["cache"]["actions"], fs["cache"])


# The last download's failed checks and Install anyway: offered by the rule update_force_install refuses by.
def failed_check(cid, name, warn=False):
    return {"id": cid, "name": name, "ok": False, "detail": "", "warn": warn}


def force_case(checks, sha="a" * 40, **case):
    (T / "cache" / "src" / "install.sh").write_text("#!/bin/bash\n")
    H.UPDATE_STATE.write_text(json.dumps({"checked_sha": "a" * 40, "checks": checks}))
    return doctor(head_sha=sha, **case)["last-check"]


lc = force_case([failed_check("python", "Python files compile"), failed_check("nginx", "nginx accepts it", warn=True)])
check(
    "Install anyway on the last check: one button, its question lists exactly the checks it skips (not the warning)",
    [a["choice"] for a in lc["actions"]] == ["force-install"]
    and lc["actions"][0]["label"]["id"] == "update.last-check.force"
    and msg_args({"summary": lc["actions"][0]["confirm"]}, "update.last-check.force.confirm")
    == {"n": 1, "checks": ["Python files compile"]},
    lc["actions"],
)
lc = force_case([failed_check("python", "Python files compile"), failed_check("signed", "Signed as this box requires")])
check("  not when the signing level failed", not lc["actions"], lc["actions"])
lc = force_case([{"name": H.SIGNED_OLD, "ok": False, "detail": "", "warn": False}])
check("  nor when it failed in a state kept before the checks had IDs", not lc["actions"], lc["actions"])
lc = force_case([failed_check("python", "Python files compile")], bash_code=2)
check("  nor when the downloaded installer does not parse", not lc["actions"], lc["actions"])
lc = force_case([failed_check("python", "Python files compile")], sha="b" * 40)
check("  nor when the checked version is not the one downloaded", not lc["actions"], lc["actions"])
lc = force_case([failed_check("nginx", "nginx accepts it", warn=True)])
check("  nor when only a warning failed: nothing to skip", not lc["actions"], lc["actions"])
(T / "cache" / "src" / "install.sh").unlink()

# The certificate refused; GitHub's rate used up; the branch gone; the clock two hours fast.
H.UPDATE_STATE.unlink()
fs = doctor(tls_error=ssl.SSLError("CERTIFICATE_VERIFY_FAILED"), remaining=0, heads="")
check(
    "a certificate refused: said, the possible causes a note, the clock the place to look first",
    {"update.tls.failed", "update.tls.failed.causes", "update.tls.failed.fix"} <= msg_ids(fs["tls"])
    and fs["tls"]["go"]["section"] == "clock",
    fs["tls"],
)
check(
    "  the rate limit used up: how long to wait, and the token's setting marked",
    (msg_args(fs["github-api"], "update.github-api.used-up.fix") or {}).get("n") in (29, 30)
    and fs["github-api"]["go"] == {"section": "sources", "where": H.GO_TOKEN["where"], "mark": "library-token"},
    fs["github-api"],
)
check(
    "  a branch that is not there: Branch not found, with the repository and the branch as data",
    msg_args(fs["repo"], "update.repo.no-branch") == {"repo": "https://example.invalid/irate-box", "branch": "main"},
    fs["repo"],
)
check(
    "  and its link lands on the Branch field of Update source, which the page marks",
    fs["repo"]["go"]["section"] == "updoctor" and fs["repo"]["go"]["mark"] == "update-source-branch",
    fs["repo"].get("go"),
)
fs = doctor(ls_code=128)
check(
    "a repository that cannot be read: the link lands on the Repository field",
    fs["repo"]["status"] == "problem" and fs["repo"]["go"]["mark"] == "update-source-repo",
    fs["repo"].get("go"),
)
fs = doctor(date=time.time() - 7200)
check(
    "a clock two hours ahead: said with how far, the command to set it to copy, Clock the place",
    msg_args(fs["clock"], "update.clock.ahead")["host"] == "github.com"
    and abs(msg_args(fs["clock"], "update.clock.ahead")["gap"]["seconds"] - 7200) < 60
    and fs["clock"]["commands"] == [H.CLOCK_SET]
    and fs["clock"]["go"]["section"] == "clock",
    fs["clock"],
)

# No install options, and no names at all.
OPTIONS.unlink()
fs = doctor(addrs=None)
check(
    "no install options: said, with why and what to do",
    {"update.options.none", "update.options.none.why", "update.options.none.fix"} <= msg_ids(fs["options"]),
    fs["options"],
)
check(
    "  no name lookup: said, with what one failure does not show",
    {"update.dns.failed", "update.probe.note"} <= msg_ids(fs["dns"])
    and fs["dns"]["evidence"] == ["Temporary failure in name resolution"],
    fs["dns"],
)
OPTIONS.write_text("--branch\nmain\n")
fs = doctor()
check(
    "options with no repository: a problem of its own, not 'updates come from None'",
    "update.options.no-repo" in msg_ids(fs["options"]) and fs["options"]["status"] == "problem",
    fs["options"],
)
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n")
SIGNING = T / "etc" / "update-signing.json"
for bad, ids in (
    ("/srv/irate-box", {"update.check.source-level"}),
    ("file:///srv/irate-box", {"update.check.source-level"}),
    ("git://example.invalid/irate-box", {"update.check.source-level"}),
    ("http://example.invalid/irate-box", {"update.check.source-level"}),
    ("ssh://git@example.invalid/irate-box", {"update.check.source-bad"}),
    ("ext::sh -c x", {"update.check.source-bad"}),
    ("https://user:pw@example.invalid/irate-box", {"update.check.source-bad"}),
):
    OPTIONS.write_text(f"--repo\n{bad}\n--branch\nmain\n")
    fs = doctor()
    check(
        f"a recorded source the signing level does not allow ({bad}): a problem, said, the Update source the place; "
        "the repository is not asked",
        fs["options"]["status"] == "problem"
        and msg_ids(fs["options"]) & ids
        and "update.options.bad-source.fix" in msg_ids(fs["options"])
        and fs["options"]["go"]["mark"] == "update-source-repo"
        and "repo" not in fs,
        fs["options"],
    )
# With signing on, a source off the web is fine, except where it is never fine.
SIGNING.write_text(json.dumps({"level": "github", "signers": []}))
for ok in ("/srv/irate-box", "http://example.invalid/irate-box", "git://example.invalid/irate-box"):
    OPTIONS.write_text(f"--repo\n{ok}\n--branch\nmain\n")
    fs = doctor()
    check(
        f"  signing on: {ok} is a fine source, and the repository is asked",
        fs["options"]["status"] == "ok" and "repo" in fs,
        fs["options"],
    )
OPTIONS.write_text("--repo\nssh://git@example.invalid/irate-box\n--branch\nmain\n")
check("  and ssh is not", doctor()["options"]["status"] == "problem")
SIGNING.unlink()
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n")

# The doctor's own answer, and its report.
patches = world()
for p in patches:
    p.start()
said = H.update_doctor({})
for p in patches:
    p.stop()
rep = json.loads(H.DOCTOR_STATE.read_text())
check(
    "update_doctor: its answer a message, the report written with the findings in the shared shape",
    said["id"] in ("update.doctor.none", "update.doctor.problems")
    and all("summary" in f and "title" in f for f in rep["findings"]),
    (said, rep),
)

# The answers the update actions give: messages, the result file keeping the English beside them.
H.answer(
    "r1",
    True,
    H.M("update.cache.cleared", "The update cache is cleared. The next check starts again from the beginning."),
)
r = json.loads((H.RESULTS / "r1.json").read_text())
check(
    "a result: the English as message (every reader of results expects text), the message beside it",
    r["message"].startswith("The update cache is cleared.") and r["msg"]["id"] == "update.cache.cleared",
    r,
)
H.answer("r2", False, "a plain refusal")
r = json.loads((H.RESULTS / "r2.json").read_text())
check("  a plain string: as before, no msg", r["message"] == "a plain refusal" and "msg" not in r, r)
try:
    H.update_install({})
    refusal = None
except H.Refused as exc:
    refusal = exc
check(
    "Install with nothing downloaded: refused by message, its English the exception's text",
    refusal and refusal.message["id"] == "update.install.none" and str(refusal) == render(refusal.message),
    refusal,
)
state = {
    "up_to_date": False,
    "verified": None,
    "available": "abc1234",
    "branch": "main",
    "changes": ["a", "b", "c"],
    "changes_known": True,
}
with mock.patch.object(H, "_check_for_update", lambda progress: (dict(state), [])):
    said = H.update_check({})
check(
    "a check that finds an update: Update available, with the count of new commits as a plural",
    said["id"] == "update.check.new"
    and said["args"] == {"commit": "abc1234", "branch": "main", "n": 3}
    and render(said) == "Update available: abc1234 on main, with 3 new commits. Download it to verify it.",
    said,
)
with mock.patch.object(H, "_check_for_update", lambda progress: (dict(state, changes_known=False), [])):
    said = H.update_check({})
check("  from a history it cannot count: a different version, said as that", said["id"] == "update.check.other", said)
with mock.patch.object(H, "_check_for_update", lambda progress: (dict(state, up_to_date=True), [])):
    said = H.update_check({})
check("  none newer: No newer version detected", said["id"] == "update.check.none", said)

# The checks of a downloaded version: each with an ID, said as messages, what it saw as evidence.
src = T / "src"
(src / "irate_box").mkdir(parents=True)
(src / "install.sh").write_text("#!/bin/bash\necho ok\n")
(src / "uninstall.sh").write_text("#!/bin/bash\nif then fi (\n")
(src / "irate_box" / "good.py").write_text("x = 1\n")
(src / "irate_box" / "bad.py").write_text("def (:\n")
checks = {c["id"]: c for c in H.verify_update(src, None, [])}
check(
    "verification: each check by its ID",
    {"parses:install.sh", "parses:uninstall.sh", "python", "nginx"} <= set(checks),
    sorted(checks),
)
check(
    "  a script that parses, and one that does not, with the shell's line as evidence",
    checks["parses:install.sh"]["ok"]
    and not checks["parses:uninstall.sh"]["ok"]
    and checks["parses:uninstall.sh"]["evidence"]
    and checks["parses:uninstall.sh"]["detail"]["id"] == "update.verify.parses.failed",
    checks["parses:uninstall.sh"],
)
check(
    "  a Python file that does not compile: counted, named in the evidence",
    not checks["python"]["ok"]
    and checks["python"]["detail"]["args"] == {"n": 1}
    and "irate_box/bad.py" in checks["python"]["evidence"][0],
    checks["python"],
)
check(
    "  the nginx site missing from the update: said, the file as evidence",
    checks["nginx"]["detail"]["id"] == "update.verify.nginx.missing"
    and checks["nginx"]["evidence"] == ["config/irate-box.nginx"],
    checks["nginx"],
)
(src / "irate_box" / "bad.py").unlink()
py = next(c for c in H.verify_update(src, None, []) if c["id"] == "python")
check(
    "  Python that compiles: passed, with what compiling does not prove",
    py["ok"] and py["note"]["id"] == "update.verify.limited",
    py,
)
check(
    "  every check's words said in full",
    all("{" not in render(c[k]) for c in checks.values() for k in ("name", "detail", "fix") if c.get(k)),
)

(src / "config").mkdir()
(src / "config" / "irate-box.nginx").write_text((REPO / "config" / "irate-box.nginx").read_text())
nginx_permissions = []


def nginx_scratch_check(*cmd, **kwargs):
    scratch = Path(cmd[-1]).parent
    includes = list((scratch / "tls").glob("*.inc"))
    readable = bool(includes) and all(path.stat().st_mode & 0o004 for path in includes)
    traversable = all(path.stat().st_mode & 0o001 for path in (scratch, scratch / "tls"))
    nginx_permissions.append(readable and traversable)
    return subprocess.CompletedProcess(
        cmd, 0 if readable and traversable else 1, "", "[emerg] TLS include permission denied"
    )


previous_umask = os.umask(0o077)
try:
    with mock.patch.object(H, "run", nginx_scratch_check):
        nginx = H._nginx_check(src, [])
finally:
    os.umask(previous_umask)
check(
    "  nginx TLS includes stay readable to the unprivileged checker under umask 077",
    nginx["ok"] and nginx_permissions == [True],
    nginx,
)
with mock.patch.object(H, "run", return_value=subprocess.CompletedProcess([], 1, "", "[emerg] unknown directive")):
    rejected = H._nginx_check(src, [])
check(
    "nginx preflight rejection is advisory, not a security gate",
    rejected["warn"] and rejected["outcome"] == "rejected",
    rejected,
)
with mock.patch.object(H, "run", side_effect=FileNotFoundError("nginx")):
    inconclusive = H._nginx_check(src, [])
check(
    "an unavailable preflight is distinguished from a rejected candidate",
    inconclusive["warn"] and inconclusive["outcome"] == "inconclusive",
    inconclusive,
)

import shutil

from irate_box.root import nginx_front

render_values = nginx_front.settings_from_env()
rendered = nginx_front.render((REPO / "config/irate-box.nginx").read_text(), render_values)
check("target renderer fills every declared placeholder", not nginx_front.TOKENS.search(rendered))
check("target renderer uses real configured ports", f"https {render_values['TLS_PORT']}" in rendered)
check(
    "IPv6-disabled rendering drops only IPv6 listeners",
    nginx_front.render("listen [::]:80;\nlisten 80;\n", {}, ipv6=False) == "listen 80;\n",
)
for bad_values, bad_template in [
    (dict(render_values, PORT="0"), "listen @PORT@;"),
    (dict(render_values, STATIC="/tmp/unsafe;include /secret"), "root @STATIC@;"),
    (render_values, "include @FUTURE@;"),
]:
    try:
        nginx_front.render(bad_template, bad_values)
        check("invalid staging settings are refused", False)
    except ValueError:
        check("invalid staging settings are refused", True)
private_stage = T / "private-stage"
private_stage.mkdir(mode=0o700)
bundle = nginx_front.prepare(REPO / "config/irate-box.nginx", private_stage, render_values, "a" * 40)
manifest, site = nginx_front.read_bundle(bundle)
check(
    "staged bundle records protocol, full source and restrictive modes",
    manifest["protocol"] == 1 and manifest["source"] == "a" * 40 and site.stat().st_mode & 0o077 == 0,
)
site.write_text("changed")
try:
    nginx_front.read_bundle(bundle)
    check("changed staged site is refused", False)
except ValueError:
    check("changed staged site is refused", True)
linked_stage = T / "linked-stage"
linked_stage.symlink_to(private_stage, target_is_directory=True)
try:
    nginx_front.prepare(REPO / "config/irate-box.nginx", linked_stage, render_values, "a" * 40)
    check("symlinked staging directories are refused", False)
except ValueError:
    check("symlinked staging directories are refused", True)

candidate = "a" * 40
transaction = T / "nginx-transaction"
old_site = T / "old-site.conf"
old_site.write_text("old")
old_site.chmod(0o640)
old_link = T / "default-site"
old_link.symlink_to("owner-site")
new_file = T / "new-include.conf"
nginx_front.begin(transaction, [old_site, old_link, new_file])
old_site.write_text("new")
old_link.unlink()
new_file.write_text("new")
nginx_front.recover(transaction)
check(
    "interrupted activation restores content, modes, links and prior nonexistence",
    old_site.read_text() == "old"
    and old_site.stat().st_mode & 0o777 == 0o640
    and old_link.is_symlink()
    and not new_file.exists(),
)
check("transaction recovery is idempotent", nginx_front.recover(transaction) is False)

for failure in ("none", "validation", "reload", "health", "candidate", "rollback"):
    directory = T / ("activation-" + failure)
    nginx_front.begin(directory, [old_site])
    prepared = nginx_front.prepare(REPO / "config/irate-box.nginx", private_stage, render_values, candidate)
    old_site.write_bytes((prepared / "site.conf").read_bytes())
    nginx_front.seal(directory, prepared, old_site)
    commands = []
    probes = []

    def activate(*argv):
        commands.append(argv)
        failed = (
            (failure in ("validation", "rollback") and len(commands) == 1)
            or (failure == "reload" and argv[1] == "reload")
            or (failure == "rollback" and argv[1] == "reload-or-restart")
        )
        return subprocess.CompletedProcess(argv, int(failed), "", "injected failure" if failed else "")

    def probe(values):
        probes.append(values)
        if failure == "health" and len(probes) == 1:
            raise ValueError("injected health failure")

    if failure == "candidate":
        old_site.write_text("tampered")
    try:
        nginx_front.finish(directory, prepared, render_values, runner=activate, probe=probe)
        check(
            "activation succeeds only with an unchanged healthy candidate",
            failure == "none" and nginx_front.transaction_state(directory)["state"] == "committed",
        )
    except ValueError:
        state = nginx_front.transaction_state(directory)["state"]
        check(
            "activation failure restores the previous files: " + failure,
            failure != "none" and old_site.read_text() == "old",
        )
        check(
            "rollback outcome is explicit: " + failure,
            state == ("recovery-required" if failure == "rollback" else "restored"),
        )
    old_site.write_text("old")

pipeline_state = {"available": candidate[:7], "available_sha": candidate, "up_to_date": False, "branch": "main"}
pipeline_calls = []


def pipeline_check(req):
    pipeline_calls.append("check")
    H._write_update_state(pipeline_state)


def pipeline_fetch(req):
    pipeline_calls.append("download")
    H._write_update_state(dict(pipeline_state, checked_sha=candidate, verified_sha=candidate))
    return H.M("test.download", "Downloaded.")


with (
    mock.patch.object(H, "update_check", pipeline_check),
    mock.patch.object(H, "update_fetch", pipeline_fetch),
    mock.patch.object(H, "update_install", lambda req: pipeline_calls.append("install")),
    mock.patch.object(H, "_git", return_value=candidate),
):
    H.UPDATE_STATE.unlink(missing_ok=True)
    H.update_reach({"endpoint": "install"})
    check(
        "combined install checks, downloads and installs in order",
        pipeline_calls == ["check", "download", "install"],
        pipeline_calls,
    )
    pipeline_calls.clear()
    H.update_reach({"endpoint": "install", "candidate": candidate})
    check(
        "downloaded candidate installs without repeating completed stages",
        pipeline_calls == ["install"],
        pipeline_calls,
    )
    pipeline_calls.clear()
    try:
        H.update_reach({"endpoint": "install", "candidate": "b" * 40})
        check("changed candidate requires a fresh review", False)
    except H.Refused:
        check("changed candidate requires a fresh review", not pipeline_calls)
    H._write_update_state(dict(pipeline_state, checked_sha=candidate))
    try:
        H.update_reach({"endpoint": "install"})
        check("failed verification never silently retries or installs", False)
    except H.Refused:
        check("failed verification never silently retries or installs", not pipeline_calls)
    pipeline_calls.clear()
    H.UPDATE_STATE.unlink()
    with mock.patch.object(H, "update_check", lambda req: H._write_update_state(dict(pipeline_state, up_to_date=True))):
        H.update_reach({"endpoint": "install"})
    check("no update ends a combined operation without installation", not pipeline_calls)

if os.environ.get("IRATE_NGINX"):
    from irate_box.hub import access
    from irate_box.root import tls

    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (
                json.dumps({"proxied": "X-Forwarded-For" in self.headers, "version": "test"}).encode()
                if self.path == "/status"
                else b"<html>healthy</html>"
            )
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    try:
        for transport in ("http", "both", "discovery", "https"):
            live = T / ("real-" + transport)
            live.mkdir()
            values = dict(render_values)
            reserved = []
            for token in nginx_front.PORTS:
                listener = socket.socket()
                listener.bind(("127.0.0.1", 0))
                reserved.append(listener)
                values[token] = str(listener.getsockname()[1])
            for token in ("ACCESS", "ADDON_ACCESS", "FRONT", "HTPASSWD", "UNCLAIMED", "PAGELOG"):
                values[token] = str(live / token.lower())
            values["TLS"] = str(live / "tls")
            front = Path(values["TLS"])
            front.mkdir()
            Path(values["ACCESS"]).write_text(access.nginx_conf(access.defaults()))
            Path(values["ADDON_ACCESS"]).write_text(access.addon_nginx_conf(access.defaults(), []))
            Path(values["FRONT"]).write_text('proxy_set_header X-Irate-Front "test";\n')
            Path(values["HTPASSWD"]).write_text("admin:unused\n")
            certificate, key = live / "cert.pem", live / "key.pem"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "ec",
                    "-pkeyopt",
                    "ec_paramgen_curve:P-256",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=DNS:localhost,IP:127.0.0.1",
                    "-keyout",
                    str(key),
                    "-out",
                    str(certificate),
                ],
                check=True,
                capture_output=True,
            )
            names = {
                "main": "",
                "addons": "ADDON_",
                "notes": "NOTES_",
                "wiki": "WIKI_",
                "git": "GIT_",
                "notebook": "NOTEBOOK_",
                "admin": "ADMIN_",
                "draw": "DRAW_",
                "mermaid": "MERMAID_",
                "sync": "SYNC_",
            }
            with (
                mock.patch.multiple(
                    tls,
                    FRONT=front,
                    CHAIN=certificate,
                    KEY=key,
                    PLAIN_PORTS={name: int(values[prefix + "PORT"]) for name, prefix in names.items()},
                    TWINS={name: int(values[prefix + "TLS_PORT"]) for name, prefix in names.items()},
                ),
                mock.patch.object(tls, "settings", return_value={"transport": transport, "admin_http": "allow"}),
            ):
                generated = tls._plain_files(transport, "allow")
                if transport != "http":
                    generated.update(tls._front_files())
                for path, text in generated.items():
                    path.write_text(text)
                template = live / "template"
                template.write_text(
                    (REPO / "config/irate-box.nginx")
                    .read_text()
                    .replace("127.0.0.1:8000", f"127.0.0.1:{backend.server_port}")
                    .replace(
                        "/etc/nginx/fastcgi_params",
                        str(Path(os.environ.get("IRATE_NGINX_CONF_DIR", "/etc/nginx")) / "fastcgi_params"),
                    )
                )
                prior_bundle = nginx_front.prepare(template, private_stage, values, "old")
                deployed = live / "site.conf"
                deployed.write_bytes((prior_bundle / "site.conf").read_bytes())
                configuration = live / "nginx.conf"
                configuration.write_text(
                    f"user {pwd.getpwuid(os.getuid()).pw_name};\npid {live}/nginx.pid;\nerror_log {live}/error.log;\nevents {{}}\nhttp {{ access_log off; client_body_temp_path {live}/body; proxy_temp_path {live}/proxy; fastcgi_temp_path {live}/fastcgi; uwsgi_temp_path {live}/uwsgi; scgi_temp_path {live}/scgi; include {deployed}; }}\n"
                )
                command = [os.environ["IRATE_NGINX"], "-p", str(live), "-c", str(configuration)]

                def real_run(*arguments):
                    if arguments[:2] == ("systemctl", "is-active"):
                        return subprocess.CompletedProcess(arguments, 0, "", "")
                    options = list(arguments[1:]) if arguments[0] == "nginx" else ["-s", "reload"]
                    return subprocess.run(command + options, check=False, capture_output=True, text=True)

                for listener in reserved:
                    listener.close()
                started = subprocess.run(command, check=False, capture_output=True, text=True)
                check("real nginx starts in " + transport, started.returncode == 0, started.stderr)
                if started.returncode:
                    continue
                try:
                    for fault in ("none", "syntax", "health"):
                        journal = live / ("transaction-" + fault)
                        with mock.patch.object(nginx_front, "settings_from_env", return_value=dict(values)):
                            nginx_front.begin(journal, [deployed])
                        if fault == "syntax":
                            template.write_text(template.read_text() + "\ninvalid_directive;\n")
                        prepared = nginx_front.prepare(template, private_stage, values, "new-" + fault)
                        deployed.write_bytes((prepared / "site.conf").read_bytes())
                        nginx_front.seal(journal, prepared, deployed)
                        attempts = []

                        def live_probe(settings):
                            attempts.append(settings)
                            if fault == "health" and len(attempts) == 1:
                                raise ValueError("injected health failure")
                            nginx_front.health(settings)

                        try:
                            nginx_front.finish(journal, prepared, values, runner=real_run, probe=live_probe)
                            check("real nginx activates all origins in " + transport, fault == "none")
                        except ValueError as error:
                            check(
                                "real nginx rollback: " + transport + "/" + fault,
                                fault != "none" and nginx_front.transaction_state(journal)["state"] == "restored",
                                str(error),
                            )
                        if fault == "syntax":
                            template.write_text(template.read_text().replace("\ninvalid_directive;\n", ""))
                finally:
                    subprocess.run(command + ["-s", "quit"], check=False, capture_output=True)
    finally:
        backend.shutdown()
        backend.server_close()
else:
    print("SKIP real nginx activation tests (set IRATE_NGINX to an isolated nginx binary)")

# --- Item 148: a branch left behind (the Lyra followed a merged topic branch, 2026-10-19) ---
# A real repository: main A-B-C (C installed), a topic branch from A (diverged from C), "old" at B (behind C).
G = T / "branches"
GIT_ENV = dict(
    os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"
)


def git(*args, cwd=G):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=True
    ).stdout.strip()


G.mkdir()
git("init", "-q", "-b", "main")
for n in "ABC":
    git("commit", "-q", "--allow-empty", "-m", n)
    if n == "A":
        git("branch", "topic")
    if n == "B":
        git("branch", "old")
installed = git("rev-parse", "--short=7", "main")
git("switch", "-q", "topic")
git("commit", "-q", "--allow-empty", "-m", "T1")
git("switch", "-q", "main")
check(
    "how a commit stands against the installed one: same, behind, diverged; a newer one ahead",
    [H._relation(G, installed, r) for r in ("main", "old", "topic")] == ["same", "behind", "diverged"],
    [H._relation(G, installed, r) for r in ("main", "old", "topic")],
)
git("commit", "-q", "--allow-empty", "-m", "D")
check("  ahead", H._relation(G, installed, "main") == "ahead")
check("  unknown: the installed commit not in that history", H._relation(G, "deadbee", "main") == "unknown")

# The standing the poll records, from the repository's own history (fetched commits only, no files).
with mock.patch.object(H, "_source_protocols", lambda level=None: []):
    standing = {b: H._branch_standing(str(G), "main", b, installed) for b in ("main", "old", "topic")}
    merged_unknown = H._branch_standing(str(G), "main", "old", "1234567")
check(
    "the followed branch's standing: main ahead, old behind, topic elsewhere (main has the installed version)",
    standing == {"main": "ahead", "old": "behind", "topic": "elsewhere"},
    standing,
)
check(
    "  a branch main already contains, the installed commit unknown: merged", merged_unknown == "merged", merged_unknown
)

# The poll: the branches listed (the default first), the standing kept for the page and the doctor.
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\ntopic\n")
heads = "ref: refs/heads/main\tHEAD\nabc\tHEAD\nabc\trefs/heads/main\ndef\trefs/heads/topic\nfed\trefs/heads/old\n"
with (
    mock.patch.object(H, "_branch_standing", lambda repo, default, branch, inst: "elsewhere"),
    mock.patch.object(H, "_installed_commit", lambda: installed),
):
    said = poll(heads)
kept = json.loads(H.BRANCHES_STATE.read_text())
check(
    "polling a branch left behind: said, with the default to select",
    said["id"] == "update.target.poll.elsewhere"
    and said["args"]["default"] == "main"
    and said["args"]["branch"] == "topic",
    said,
)
check(
    "  kept for the page: the branches, the default first, the standing",
    kept["branches"] == ["main", "old", "topic"]
    and kept["default"] == "main"
    and kept["standing"] == "elsewhere"
    and kept["branch"] == "topic",
    kept,
)
fs = doctor()
check(
    "  the doctor says it as a problem, what to select, the Branch field to go to; no Install anyway",
    fs["target"]["status"] == "problem"
    and msg_args(fs["target"], "update.target.elsewhere") == {"branch": "topic", "default": "main"}
    and msg_args(fs["target"], "update.target.left.fix") == {"default": "main"}
    and fs["target"]["go"]["mark"] == "update-source-branch"
    and not fs["target"].get("actions"),
    fs["target"],
)
with mock.patch.object(H, "_installed_commit", lambda: installed):
    said = poll("ref: refs/heads/main\tHEAD\nabc\tHEAD\nabc\trefs/heads/main\n")
check(
    "  a branch the repository no longer has: gone, said, and the doctor says it",
    said["id"] == "update.target.poll.gone"
    and json.loads(H.BRANCHES_STATE.read_text())["standing"] == "gone"
    and doctor()["target"]["summary"]["id"] == "update.target.gone",
    said,
)
H.BRANCHES_STATE.unlink()

# The check: a branch behind or diverged said as that, not as an update.
for relation, mid in (("behind", "update.check.behind"), ("diverged", "update.check.diverged")):
    with mock.patch.object(
        H,
        "_check_for_update",
        lambda progress: (
            {
                "up_to_date": relation == "behind",
                "verified": None,
                "available": "abc1234",
                "branch": "topic",
                "changes": [],
                "changes_known": True,
                "relation": relation,
                "installed": installed,
            },
            [],
        ),
    ):
        said = H.update_check({})
    check(f"a check on a branch {relation}: said so, with where to change it", said["id"] == mid, said)

# Verification of an older version: its own reason, and Install anyway refuses to go back.
src = Path(os.environ["HUB_UPDATE_DIR"])
shutil.rmtree(src, ignore_errors=True)
git("clone", "-q", "-b", "old", str(G), str(src), cwd=T)
(src / "install.sh").write_text("#!/bin/bash\necho ok\n")
history = next(c for c in H.verify_update(src, installed, []) if c["id"] == "history")
check(
    "verifying a version older than the installed one: failed, said as going back, the cure the branch",
    not history["ok"]
    and history["detail"]["id"] == "update.verify.history.behind"
    and history["fix"]["id"] == "update.verify.history.behind.fix",
    history,
)
full = git("rev-parse", "HEAD", cwd=src)
with mock.patch.object(H, "_installed_commit", lambda: installed):
    why = H._force_refusal({"checked_sha": full, "checks": [history]})
    actions = H._force_actions({"checked_sha": full, "checks": [history]})
check(
    "  Install anyway refuses it, and the doctor offers no Install anyway",
    why["id"] == "update.force.older" and actions == [],
    why,
)
H.UPDATE_STATE.write_text(json.dumps({"checks": [history], "checked_sha": full}))
fs = doctor()
check(
    "  the last check's finding points at the branch, not at Install anyway",
    fs["last-check"]["fix"]["id"] == "update.last-check.fix.branch"
    and fs["last-check"]["go"]["mark"] == "update-source-branch",
    fs["last-check"],
)
H.UPDATE_STATE.unlink()
shutil.rmtree(src, ignore_errors=True)

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
