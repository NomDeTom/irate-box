# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The update doctor (hub_control.doctor) in finding.py's shape, with the network, git, apt and the disk stood in:
each case said by its message ID, its commands, what it saw and the place to go as data, a place only for what is
not fine; then the checks of a downloaded version and the update actions' answers as messages, the result file
keeping the English for every reader that expects text. python3 tests/sim_update_doctor.py"""
import io
import json
import os
import ssl
import subprocess  # CompletedProcess mock results only. # nosec B404
import sys
import tempfile
import time
import urllib.parse
from email.utils import formatdate
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="updoctor-"))
for d in ("state/control/results", "state/control/requests", "etc", "code", "cache"):
    (T / d).mkdir(parents=True)
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_RUN_DIR=str(T / "run"), HUB_CODE_DIR=str(T / "code"),
                  HUB_UPDATE_DIR=str(T / "cache" / "src"), HUB_DOWNLOAD_CACHE=str(T / "cache" / "downloads"), HUB_WEB_SERVER="nginx")
sys.path.insert(0, str(REPO))
from irate_box.root import hub_control as H  # noqa: E402
from irate_box.finding import english, message_args as msg_args, message_ids as msg_ids  # noqa: E402
from irate_box.msg import is_message, render  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


class Resp(io.BytesIO):
    def __init__(self, body=b"", headers=None):
        super().__init__(body)
        self.headers = headers or {}


def world(*, addrs=("140.82.121.3",), mine=(), connect=True, date=None, tls_error=None, remaining=50, heads="abc1234def refs/heads/main\n",
          ls_code=0, apt=(0, "Hit:1 https://deb.debian.org/debian trixie InRelease\n"), free_mb=5000):
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
        return Resp(json.dumps({"resources": {"core": {"remaining": remaining, "limit": 60, "reset": time.time() + 1800}}}).encode())

    def run(*cmd, timeout=120, **kw):
        if cmd[:2] == ("git", "ls-remote"):
            return subprocess.CompletedProcess(cmd, ls_code, heads if ls_code == 0 else "", "fatal: repository not found\n")
        if cmd[0] == "apt-get":
            return subprocess.CompletedProcess(cmd, apt[0], apt[1], "")
        if cmd[0] == "git" and "status" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[0] == "git" and "get-url" in cmd:
            return subprocess.CompletedProcess(cmd, 0, "https://example.invalid/irate-box\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    usage = mock.Mock(free=free_mb << 20)
    return [mock.patch("socket.getaddrinfo", getaddrinfo), mock.patch("socket.create_connection", create_connection),
            mock.patch.object(H, "_tls_get", tls_get), mock.patch.object(H, "run", run),
            mock.patch.object(H, "_local_addresses", lambda: set(mine)), mock.patch.object(H.shutil, "disk_usage", lambda p: usage),
            mock.patch.object(H.kits, "verify", lambda: []), mock.patch.object(H.kits, "definitions", lambda: {}),
            mock.patch.object(H.kits, "installed_state", lambda: {}), mock.patch.object(H.kits, "pool_bytes", lambda: 0)]


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
        if not (is_message(f["title"]) and is_message(f["summary"])) or any("{" in s for s in said) \
                or (f["status"] == "ok" and f.get("go")) or not all(isinstance(e, str) for e in f["evidence"] + f["commands"]):
            bad.append(f["id"])
        english(f)
    return bad


OPTIONS = T / "etc" / "install-options"
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n")

# All well: each part fine, nowhere to go.
fs = doctor()
check("all well: every check fine, but the schedule (no run recorded yet)",
      {k for k, f in fs.items() if f["status"] != "ok"} == {"scheduled"}, {k: f["status"] for k, f in fs.items()})
check("  each said by its own message: options, names, connection, clock, certificate, GitHub, branch, space, cache, sources",
      msg_args(fs["options"], "update.options.ok") == {"branch": "main", "repo": "https://example.invalid/irate-box"}
      and "update.dns.ok" in msg_ids(fs["dns"]) and "update.connection.ok" in msg_ids(fs["connection"]) and "update.clock.ok" in msg_ids(fs["clock"])
      and "update.tls.ok" in msg_ids(fs["tls"]) and msg_args(fs["github-api"], "update.github-api.ok") == {"left": 50, "limit": 60}
      and msg_args(fs["repo"], "update.repo.ok") == {"branch": "main", "commit": "abc1234"} and "update.space.ok" in msg_ids(fs["space:system"])
      and "update.cache.empty" in msg_ids(fs["cache"]) and "update.sources.ok" in msg_ids(fs["sources"]), fs)
check("  no run of the schedule recorded: a warning, the log's command to copy, the librarian named as the place",
      "update.scheduled.none" in msg_ids(fs["scheduled"]) and fs["scheduled"]["commands"] == ["journalctl -u irate-box-librarian -n 80"]
      and fs["scheduled"]["go"]["section"] == "sources", fs["scheduled"])
check("  every finding well said, in the shared shape", not well_said(fs), well_said(fs))

# The hotspot: every name is the box's own; nothing answers; the card is nearly full; a lock left in the cache; apt fails.
(T / "cache" / "src" / ".git").mkdir(parents=True)
(T / "cache" / "src" / ".git" / "index.lock").write_text("")
apt_out = ("Err:5 https://dl.example.org/deb stable InRelease\n  The following signatures were invalid: EXPKEYSIG 1234\n"
           "E: The repository 'https://dl.example.org/deb stable InRelease' is not signed.\n")
H.UPDATE_STATE.write_text(json.dumps({"checks": [
    H._check("python", False, H.M("update.verify.python.title", "Python files compile"),
             H.M("update.verify.python.failed", {"one": "{n} Python file does not compile.", "other": "{n} Python files do not compile."}, n=1),
             evidence=["irate_box/x.py: invalid syntax (x.py, line 1)"]),
    {"name": "install.sh parses", "ok": False, "detail": "line 3: syntax error", "warn": False},   # kept from before the messages
    {"name": "A warning only", "ok": False, "detail": "", "warn": True}]}))
fs = doctor(addrs=("192.168.4.1",), mine=("192.168.4.1",), connect=False, free_mb=100, apt=(100, apt_out))
check("hotspot: the name leads to the box itself, said so, the address as evidence, Network the place to go",
      fs["dns"]["status"] == "problem" and "update.dns.self" in msg_ids(fs["dns"]) and fs["dns"]["evidence"] == ["192.168.4.1"]
      and fs["dns"]["go"]["section"] == "network", fs["dns"])
check("  the connection failed: said with what one failed attempt does not show, the error as evidence",
      {"update.connection.failed", "update.probe.note"} <= msg_ids(fs["connection"]) and fs["connection"]["evidence"] == ["timed out"], fs["connection"])
check("  nothing that needs the connection is claimed (certificate, GitHub, branch)", not {"tls", "github-api", "repo", "clock"} & set(fs), sorted(fs))
check("  space: both short, with how much is free and needed, and Books the place to make room",
      msg_args(fs["space:system"], "update.space.low") == {"mb": 100, "need": 300} and msg_args(fs["space:cache"], "update.space.low") == {"mb": 100, "need": 200}
      and fs["space:system"]["go"]["section"] == "books", fs["space:system"])
check("  a stale cache lock: counted, named, Maintenance the place to clear it",
      msg_args(fs["cache"], "update.cache.lock") == {"n": 1} and fs["cache"]["evidence"] == ["index.lock"] and fs["cache"]["go"]["section"] == "update-maint", fs["cache"])
check("  apt fails: the failing source named, a command to find its list file, keep a copy before removing it",
      "update.sources.failed" in msg_ids(fs["sources"]) and "update.sources.failed.fix" in msg_ids(fs["sources"])
      and fs["sources"]["commands"] == ["grep -rlF dl.example.org /etc/apt/sources.list /etc/apt/sources.list.d/"]
      and "https://dl.example.org/deb" in fs["sources"]["evidence"] and "safe place" in render(fs["sources"]["fix"]), fs["sources"])
lc = fs.get("last-check", {})
check("  the last download's failed checks: counted (a warning is not), each said, the new by its message, the old by its words",
      msg_args(lc, "update.last-check.failed") == {"n": 2} and any(is_message(n) and n["id"] == "update.last-check.item" for n in lc["notes"])
      and "install.sh parses: line 3: syntax error" in lc["notes"] and any("x.py" in e for e in lc["evidence"]) and lc["go"]["section"] == "updates", lc)
check("  every finding well said", not well_said(fs), well_said(fs))

# The certificate refused; GitHub's rate used up; the branch gone; the clock two hours fast.
H.UPDATE_STATE.unlink()
fs = doctor(tls_error=ssl.SSLError("CERTIFICATE_VERIFY_FAILED"), remaining=0, heads="")
check("a certificate refused: said, the possible causes a note, the clock the place to look first",
      {"update.tls.failed", "update.tls.failed.causes", "update.tls.failed.fix"} <= msg_ids(fs["tls"]) and fs["tls"]["go"]["section"] == "clock", fs["tls"])
check("  the rate limit used up: how long to wait, and the token's setting marked",
      (msg_args(fs["github-api"], "update.github-api.used-up.fix") or {}).get("n") in (29, 30)
      and fs["github-api"]["go"] == {"section": "sources", "where": H.GO_TOKEN["where"], "mark": "library-token"}, fs["github-api"])
check("  a branch that is not there: Branch not found, with the repository and the branch as data",
      msg_args(fs["repo"], "update.repo.no-branch") == {"repo": "https://example.invalid/irate-box", "branch": "main"}, fs["repo"])
fs = doctor(date=time.time() - 7200)
check("a clock two hours ahead: said with how far, the command to set it to copy, Clock the place",
      msg_args(fs["clock"], "update.clock.ahead")["host"] == "github.com"
      and abs(msg_args(fs["clock"], "update.clock.ahead")["gap"]["seconds"] - 7200) < 60 and fs["clock"]["commands"] == [H.CLOCK_SET]
      and fs["clock"]["go"]["section"] == "clock", fs["clock"])

# No install options, and no names at all.
OPTIONS.unlink()
fs = doctor(addrs=None)
check("no install options: said, with why and what to do", {"update.options.none", "update.options.none.why", "update.options.none.fix"} <= msg_ids(fs["options"]), fs["options"])
check("  no name lookup: said, with what one failure does not show", {"update.dns.failed", "update.probe.note"} <= msg_ids(fs["dns"])
      and fs["dns"]["evidence"] == ["Temporary failure in name resolution"], fs["dns"])
OPTIONS.write_text("--branch\nmain\n")
fs = doctor()
check("options with no repository: a problem of its own, not 'updates come from None'", "update.options.no-repo" in msg_ids(fs["options"])
      and fs["options"]["status"] == "problem", fs["options"])
OPTIONS.write_text("--repo\nhttps://example.invalid/irate-box\n--branch\nmain\n")

# The doctor's own answer, and its report.
patches = world()
for p in patches:
    p.start()
said = H.update_doctor({})
for p in patches:
    p.stop()
rep = json.loads(H.DOCTOR_STATE.read_text())
check("update_doctor: its answer a message, the report written with the findings in the shared shape",
      said["id"] in ("update.doctor.none", "update.doctor.problems") and all("summary" in f and "title" in f for f in rep["findings"]), (said, rep))

# The answers the update actions give: messages, the result file keeping the English beside them.
H.answer("r1", True, H.M("update.cache.cleared", "The update cache is cleared. The next check starts again from the beginning."))
r = json.loads((H.RESULTS / "r1.json").read_text())
check("a result: the English as message (every reader of results expects text), the message beside it",
      r["message"].startswith("The update cache is cleared.") and r["msg"]["id"] == "update.cache.cleared", r)
H.answer("r2", False, "a plain refusal")
r = json.loads((H.RESULTS / "r2.json").read_text())
check("  a plain string: as before, no msg", r["message"] == "a plain refusal" and "msg" not in r, r)
try:
    H.update_install({})
    refusal = None
except H.Refused as exc:
    refusal = exc
check("Install with nothing downloaded: refused by message, its English the exception's text",
      refusal and refusal.message["id"] == "update.install.none" and str(refusal) == render(refusal.message), refusal)
state = {"up_to_date": False, "verified": None, "available": "abc1234", "branch": "main", "changes": ["a", "b", "c"], "changes_known": True}
with mock.patch.object(H, "_check_for_update", lambda progress: (dict(state), [])):
    said = H.update_check({})
check("a check that finds an update: Update available, with the count of new commits as a plural",
      said["id"] == "update.check.new" and said["args"] == {"commit": "abc1234", "branch": "main", "n": 3}
      and render(said) == "Update available: abc1234 on main, with 3 new commits. Download it to verify it.", said)
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
check("verification: each check by its ID", {"parses:install.sh", "parses:uninstall.sh", "python", "nginx"} <= set(checks), sorted(checks))
check("  a script that parses, and one that does not, with the shell's line as evidence",
      checks["parses:install.sh"]["ok"] and not checks["parses:uninstall.sh"]["ok"] and checks["parses:uninstall.sh"]["evidence"]
      and checks["parses:uninstall.sh"]["detail"]["id"] == "update.verify.parses.failed", checks["parses:uninstall.sh"])
check("  a Python file that does not compile: counted, named in the evidence",
      not checks["python"]["ok"] and checks["python"]["detail"]["args"] == {"n": 1} and "irate_box/bad.py" in checks["python"]["evidence"][0], checks["python"])
check("  the nginx site missing from the update: said, the file as evidence",
      checks["nginx"]["detail"]["id"] == "update.verify.nginx.missing" and checks["nginx"]["evidence"] == ["config/irate-box.nginx"], checks["nginx"])
(src / "irate_box" / "bad.py").unlink()
py = next(c for c in H.verify_update(src, None, []) if c["id"] == "python")
check("  Python that compiles: passed, with what compiling does not prove", py["ok"] and py["note"]["id"] == "update.verify.limited", py)
check("  every check's words said in full", all("{" not in render(c[k]) for c in checks.values() for k in ("name", "detail", "fix") if c.get(k)))

(src / "config").mkdir()
(src / "config" / "irate-box.nginx").write_text((REPO / "config" / "irate-box.nginx").read_text())
nginx_permissions = []


def nginx_scratch_check(*cmd, **kwargs):
    scratch = Path(cmd[-1]).parent
    includes = list((scratch / "tls").glob("*.inc"))
    readable = bool(includes) and all(path.stat().st_mode & 0o004 for path in includes)
    traversable = all(path.stat().st_mode & 0o001 for path in (scratch, scratch / "tls"))
    nginx_permissions.append(readable and traversable)
    return subprocess.CompletedProcess(cmd, 0 if readable and traversable else 1, "", "[emerg] TLS include permission denied")


previous_umask = os.umask(0o077)
try:
    with mock.patch.object(H, "run", nginx_scratch_check):
        nginx = H._nginx_check(src, [])
finally:
    os.umask(previous_umask)
check("  nginx TLS includes stay readable to the unprivileged checker under umask 077",
      nginx["ok"] and nginx_permissions == [True], nginx)

import shutil  # noqa: E402
shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
