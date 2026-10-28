# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Restoring one file (root/restore.py; notes: housecall-doctor-plan step 5): a copy, bytes a caller made, and
irate-box's git checkout, each checked before it is written; the old file kept as name.damaged; a source whose
SHA-256 does not match refused; a read-back that differs puts the old file back; the package reinstall's command,
and a conffile moved aside for --force-confmiss. python3 tests/sim_restore.py"""

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="restore-"))
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import restore

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def refused(fn, *args, **kw):
    try:
        fn(*args, **kw)
    except Refused as exc:
        return exc.message["id"]
    return ""


target = T / "settings.json"
target.write_text("damaged\n")
said = restore.from_bytes(target, b"good\n", expect=hashlib.sha256(b"good\n").hexdigest())
check(
    "bytes restored; the old file kept beside it, the new read back the same",
    target.read_bytes() == b"good\n"
    and (T / "settings.json.damaged").read_bytes() == b"damaged\n"
    and said.get("id") == "restore.done.kept",
    said,
)
before = target.read_bytes()
check(
    "a source with the wrong SHA-256: refused, nothing written",
    refused(restore.from_bytes, target, b"other\n", expect="0" * 64) == "restore.source-mismatch"
    and target.read_bytes() == before,
)
source = T / "copy.json"
source.write_bytes(b"from the copy\n")
restore.from_copy(target, source, hashlib.sha256(b"from the copy\n").hexdigest())
check(
    "a copy restored, and the damaged one kept again under a second name",
    target.read_bytes() == b"from the copy\n" and (T / "settings.json.damaged.2").exists(),
)

# A read-back that differs must not leave the new file in place: the old one goes back.
real_sha_file = restore.sha_file
restore.sha_file = lambda path: "0" * 64
target.write_bytes(b"old\n")
try:
    restore.from_bytes(target, b"new\n")
    check("a read-back that differs: refused", False)
except Refused:
    check("a read-back that differs: refused", True)
restore.sha_file = real_sha_file
check(
    "  and the old file is back, nothing half-written",
    target.read_bytes() == b"old\n" and not (T / "settings.json.damaged.3").exists(),
)

# irate-box's own file from the updater's checkout.
repo = T / "src"
repo.mkdir()
subprocess.run(["git", "init", "-q", str(repo)], check=True)
(repo / "front.conf").write_bytes(b"server { listen 80; }\n")
subprocess.run(["git", "-C", str(repo), "add", "front.conf"], check=True)
subprocess.run(
    ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=t@example.invalid", "commit", "-qm", "one"],
    check=True,
)
commit = subprocess.run(
    ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
).stdout.strip()
target = T / "front.conf"
target.write_bytes(b"\xff" * 17)
restore.from_git(target, repo, commit, "front.conf")
check(
    "irate-box's file restored from the git checkout at its commit", target.read_bytes() == b"server { listen 80; }\n"
)
check(
    "  a file not in that commit: refused",
    refused(restore.from_git, target, repo, commit, "no-such-file") == "restore.git",
)


# The package reinstall: non-conffile and conffile, with the tools stood in for.
class Result:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


calls = []
state = {"conffile": False, "target": ""}


def fake_run(*cmd, **kw):
    calls.append(cmd)
    if "dpkg" in cmd and "-S" in cmd:
        return Result(out=f"pkg: {state['target']}\n")
    if "dpkg-query" in cmd:
        return Result(out=(f" {state['target']} abc\n" if state["conffile"] else ""))
    if "apt-get" in cmd:
        return Result()
    return Result(rc=127)


target = T / "x"
target.write_text("damaged\n")
state["target"] = str(target)
restore.run = fake_run
said = restore.from_package(target)
check(
    "a package file: apt-get install --reinstall, no conffile option",
    "--reinstall" in calls[-1]
    and "Dpkg::Options::=--force-confmiss" not in calls[-1]
    and said.get("id") == "restore.package.done",
    calls,
)
state["conffile"] = True
target.write_text("edited or damaged\n")
said = restore.from_package(target, "pkg")
check(
    "  a conffile: moved aside, and --force-confmiss passed to dpkg",
    "Dpkg::Options::=--force-confmiss" in calls[-1] and (T / "x.damaged").exists(),
    calls,
)
restore.run = lambda *cmd, **kw: Result(rc=1, err="no")
check(
    "  dpkg does not know the file: refused",
    refused(restore.from_package, T / "unknown") == "restore.no-package",
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
