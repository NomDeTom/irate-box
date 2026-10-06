# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Each repository's push preset (next-work plan step 10): the hub's decision table for nginx's
auth_request (gitrepos.decide: a fetch or push × each level × the git app's switch), the presets
an area takes, and http.receivepack following the level. python3 tests/sim_git_levels.py"""
import os, subprocess, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="git-levels-"))
os.environ["HUB_GIT_ROOT"] = str(T)
sys.path.insert(0, str(REPO))
from irate_box.hub import gitrepos  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
for a in ("public", "private"):
    (T / a).mkdir()
def act(**kw):
    return gitrepos.action(kw)
def cfg(area, name, key):
    return subprocess.run(["git", "config", "--get", key], cwd=T / area / f"{name}.git", capture_output=True, text=True).stdout.strip()

for name in ("open", "admins", "ro"):
    code, _ = act(action="create", area="public", name=name)
    check(f"create public {name}", code == 200)
act(action="create", area="private", name="secret")
check("a new public repository is public-admin-writes", cfg("public", "admins", "irate-box.write") == "admin"
      and cfg("public", "admins", "http.receivepack") == "true")
check("a new private repository is private-to-admin", cfg("private", "secret", "irate-box.write") == "admin")
check("public-everything", act(action="preset", area="public", name="open", preset="public-everything")[0] == 200)
check("public-read-only", act(action="preset", area="public", name="ro", preset="public-read-only")[0] == 200)
check("read-only turns http.receivepack off", cfg("public", "ro", "http.receivepack") == "false")
check("a private repository cannot be public-everything", act(action="preset", area="private", name="secret", preset="public-everything")[0] == 400)
check("an unknown preset is refused", act(action="preset", area="public", name="open", preset="everyone-and-their-dog")[0] == 400)

D = gitrepos.decide
cases = [
    # uri, git app mode, may go ahead without a login?
    ("/git/open.git/info/refs?service=git-upload-pack", "public", True),
    ("/git/admins.git/info/refs?service=git-upload-pack", "public", True),
    ("/git/ro.git/objects/ab/cdef", "public", True),
    ("/git/open.git/info/refs?service=git-receive-pack", "public", True),
    ("/git/open.git/git-receive-pack", "public", True),
    ("/git/admins.git/info/refs?service=git-receive-pack", "public", False),
    ("/git/admins.git/info/refs?service=git%2Dreceive-pack", "public", False),
    ("/git/admins.git/git-receive-pack", "public", False),
    ("/git/ro.git/git-receive-pack", "public", False),
    ("/git/nosuch.git/git-receive-pack", "public", False),
    ("/git/open.git/info/refs?service=git-upload-pack", "private", False),
    ("/git/open.git/git-receive-pack", "private", False),
    ("/git/../private/secret.git/git-receive-pack", "public", False),
    ("/git-private/secret.git/info/refs?service=git-upload-pack", "public", False),
    ("", "public", False),
]
for uri, mode, want in cases:
    check(f"decide {uri or '(empty)'} with git {mode}: {'no login' if want else 'login'}", D(uri, "GET", mode) is want)

snap = gitrepos.snapshot()
pre = {r["name"]: r["preset"] for r in snap["repos"]}
check("the page sees each repository's preset", pre == {"open": "public-everything", "admins": "public-admin-writes",
                                                       "ro": "public-read-only", "secret": "private-to-admin"}, pre)
check("the page gets the presets each area offers", [p["name"] for p in snap["presets"]["private"]] == ["private-to-admin", "private-read-only"])
check("the old guest-push action is gone", act(action="guest-push", on=True)[0] == 400)
(T / "guest-push").write_text("old switch\n")
subprocess.run(["git", "config", "--unset", "irate-box.write"], cwd=T / "public" / "admins.git")
check("a repository from before, with guest push on, reads as public-everything", gitrepos.write_level(T / "public" / "admins.git") == "everyone")
check("…but never a private one", gitrepos.write_level(T / "private" / "secret.git") == "admin")
# Move between the areas: the level and hooks follow the new area.
(T / "guest-push").unlink()
check("move public-everything to private", act(action="move", area="public", name="open", to="private")[0] == 200)
check("…it is private-to-admin there (anyone-may-push is public only)", cfg("private", "open", "irate-box.write") == "admin"
      and cfg("private", "open", "core.hooksPath").endswith("scripts/git-hooks") and not (T / "public" / "open.git").exists())
check("move it back: public-admin-writes, the public hook", act(action="move", area="private", name="open", to="public")[0] == 200
      and cfg("public", "open", "irate-box.write") == "admin" and cfg("public", "open", "core.hooksPath").endswith("git-hooks-public"))
check("move read-only keeps read-only", act(action="move", area="public", name="ro", to="private")[0] == 200
      and cfg("private", "ro", "irate-box.write") == "nobody" and cfg("private", "ro", "http.receivepack") == "false")
act(action="move", area="private", name="ro", to="public")
check("a move onto an existing name is refused", (act(action="create", area="private", name="open"), act(action="move", area="public", name="open", to="private"))[1][0] == 400)
# Publish: the hub copies branches from one repository into another, read-only or not.
W = T / "work"
env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
subprocess.run(["git", "init", "-q", "-b", "main", str(W)], env=env)
(W / "f").write_text("x"); subprocess.run(["git", "add", "f"], cwd=W, env=env); subprocess.run(["git", "commit", "-qm", "x"], cwd=W, env=env)
subprocess.run(["git", "push", "-q", str(T / "public" / "admins.git"), "main", "main:dev"], cwd=W, env=env)
subprocess.run(["git", "tag", "v1"], cwd=W, env=env); subprocess.run(["git", "push", "-q", str(T / "public" / "admins.git"), "v1"], cwd=W, env=env)
code, body = act(action="publish", **{"from": {"area": "public", "name": "admins"}}, to={"area": "public", "name": "ro"}, refs=["main", "v1"])
check("publish main and a tag into a read-only repository", code == 200, body)
heads = subprocess.run(["git", "for-each-ref", "--format=%(refname)"], cwd=T / "public" / "ro.git", capture_output=True, text=True).stdout.split()
check("…they are there, and only they", heads == ["refs/heads/main", "refs/tags/v1"], heads)
check("publish a branch that does not exist: refused", act(action="publish", **{"from": {"area": "public", "name": "admins"}},
      to={"area": "public", "name": "ro"}, refs=["nope"])[0] == 400)
check("publish to itself: refused", act(action="publish", **{"from": {"area": "public", "name": "admins"}},
      to={"area": "public", "name": "admins"}, refs=["main"])[0] == 400)
check("a ref name that is an option: refused", act(action="publish", **{"from": {"area": "public", "name": "admins"}},
      to={"area": "public", "name": "ro"}, refs=["--upload-pack=x"])[0] == 400)
nginx = (REPO / "config" / "irate-box.nginx").read_text()
check("nginx asks the hub, with the admin login as the other way in",
      "auth_request /_irate_git_access;" in nginx and "satisfy any;" in nginx and "guest-push" not in nginx)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
