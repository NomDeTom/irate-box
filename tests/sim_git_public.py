# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Public repositories' pre-receive hook, with real
pushes: a guest may push but not force-push or delete; the owner (REMOTE_USER set, as the web
server does after the admin login) may; nobody may push past the size cap.
python3 tests/sim_git_public.py"""
import os, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="git-public-"))
PUBLIC = T / "git" / "public"; PUBLIC.mkdir(parents=True)
BARE = PUBLIC / "demo.git"
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def git(*a, cwd=None, env=None):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
             GIT_PROJECT_ROOT=str(PUBLIC), **(env or {}))
    if not (env or {}).get("REMOTE_USER"):
        e.pop("REMOTE_USER", None)  # a guest: the web server sets it only after the admin login
    return subprocess.run(["git", *a], cwd=cwd, env=e, capture_output=True, text=True)

git("init", "-q", "--bare", "-b", "main", str(BARE))
git("config", "core.hooksPath", str(REPO / "scripts" / "git-hooks-public"), cwd=BARE)
git("config", "receive.fsckObjects", "true", cwd=BARE)
W = T / "work"; git("init", "-q", "-b", "main", str(W))
(W / "a").write_text("1"); git("add", "a", cwd=W); git("commit", "-qm", "one", cwd=W)
r = git("push", "-q", str(BARE), "main", cwd=W)
check("a guest's first push", r.returncode == 0, r.stderr)
(W / "a").write_text("2"); git("commit", "-qam", "two", cwd=W)
r = git("push", "-q", str(BARE), "main", cwd=W)
check("a guest's fast-forward push", r.returncode == 0, r.stderr)
git("reset", "-q", "--hard", "HEAD~1", cwd=W); (W / "b").write_text("x"); git("add", "b", cwd=W); git("commit", "-qm", "other", cwd=W)
r = git("push", "-q", "--force", str(BARE), "main", cwd=W)
check("a guest's force push: refused", r.returncode != 0 and "may not rewrite" in r.stderr, r.stderr)
git("push", "-q", str(BARE), "main:side", cwd=W)
r = git("push", "-q", str(BARE), ":side", cwd=W)
check("a guest deleting a branch: refused", r.returncode != 0 and "may not delete" in r.stderr, r.stderr)
r = git("push", "-q", "--force", str(BARE), "main", cwd=W, env={"REMOTE_USER": "admin"})
check("the owner's force push (logged in)", r.returncode == 0, r.stderr)
r = git("push", "-q", str(BARE), ":side", cwd=W, env={"REMOTE_USER": "admin"})
check("the owner deleting a branch", r.returncode == 0, r.stderr)
(T / "git" / "public-quota-mb").write_text("0\n")
(W / "c").write_text("y" * 4096); git("add", "c", cwd=W); git("commit", "-qm", "three", cwd=W)
r = git("push", "-q", str(BARE), "main", cwd=W, env={"REMOTE_USER": "admin"})
check("past the size cap: refused, even for the owner", r.returncode != 0 and "past the box's 0 MB" in r.stderr, r.stderr)
head = git("rev-parse", "main", cwd=BARE).stdout.strip()
check("…and the refused commit is not there", head != git("rev-parse", "HEAD", cwd=W).stdout.strip())
src = (REPO / "irate_box" / "hub" / "gitrepos.py").read_text()
check("gitrepos sets the hook and fsckObjects on new public repositories",
      'str(HOOKS_PUBLIC)' in src and '"receive.fsckObjects", "true"' in src)
inst = (REPO / "install.sh").read_text()
check("install.sh sets them on existing ones", 'config core.hooksPath "$CODE/scripts/git-hooks-public"' in inst)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
