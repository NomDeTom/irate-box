"""The hub's git repositories: what /admin's Git page lists, creates and deletes.

Two areas of bare repositories under $HUB_GIT_ROOT (install.sh: /var/lib/hub/git), both owned
by the hub user, who also runs git http-backend and cgit for them (irate-box-git.socket):

  public/   /git/          browse (cgit) and clone for everyone; push needs the admin login,
                           unless guest push is on
  private/  /git-private/  everything behind the admin login

The web server does the gating (irate-box.nginx, or the Caddyfile). Guest push is one flag
file, guest-push, which the web server checks on each push; this module sets and clears it.
Repositories are created with http.receivepack on, since the web server has already decided
who may push. Private ones also get the hub's own hooks (git-hooks/, core.hooksPath): a push
whose commit has a .irate-ci.sh queues a build (ci.py). Public ones never do, since guests may
be pushing there. Nothing here needs root.

Stdlib only.
"""

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(os.environ.get("HUB_GIT_ROOT", Path(__file__).parent / "git"))
AREAS = {"public": "/git/", "private": "/git-private/"}
GUEST_PUSH = ROOT / "guest-push"
# The largest single push the web server takes (irate-box.nginx, the Caddyfile): said on the page.
MAX_PUSH = 64 * 2**20
# A name as typed, without ".git"; it becomes <name>.git, which is what URLs and cgit show.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_DESC = 200
HOOKS = Path(__file__).parent / "git-hooks"


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)


def _size(path):
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    return total


def _repo_info(path, area):
    desc = ""
    try:
        desc = (path / "description").read_text().strip()
    except OSError:
        pass
    if desc.startswith("Unnamed repository"):
        desc = ""
    # The newest commit's own date (set by whoever made it), and how many branches there are.
    out = _git("for-each-ref", "--sort=-committerdate", "--format=%(committerdate:unix)", "refs/heads", cwd=path)
    dates = out.stdout.split() if out.returncode == 0 else []
    return {"name": path.name[:-4], "area": area, "url": AREAS[area] + path.name + "/",
            "description": desc, "size": _size(path), "branches": len(dates),
            "last_commit": int(dates[0]) if dates else None}


def snapshot():
    repos = []
    for area in AREAS:
        base = ROOT / area
        if base.is_dir():
            repos += [_repo_info(p, area) for p in sorted(base.glob("*.git"), key=lambda p: p.name.lower())
                      if (p / "HEAD").exists()]
    try:
        free = shutil.disk_usage(ROOT).free
    except OSError:
        free = None
    return {"installed": all((ROOT / a).is_dir() for a in AREAS), "repos": repos,
            "guest_push": GUEST_PUSH.exists(), "max_push": MAX_PUSH, "free": free,
            "now": int(time.time())}


def _target(payload):
    area, name = payload.get("area"), payload.get("name")
    if area not in AREAS:
        raise ValueError("area must be public or private")
    if not isinstance(name, str):
        raise ValueError("name a repository")
    name = name.strip()
    if name.endswith(".git"):
        name = name[:-4]
    if not NAME_RE.match(name):
        raise ValueError("a name is letters, digits, '.', '_' and '-', up to 64, starting with a letter or digit")
    return ROOT / area / f"{name}.git"


def action(payload):
    """One change from /admin's Git page. Returns (http status, body): the new snapshot, or
    an error."""
    try:
        what = payload.get("action")
        if what == "create":
            path = _target(payload)
            if path.exists():
                raise ValueError(f"{path.name} already exists in {path.parent.name}")
            desc = payload.get("description") or ""
            if not isinstance(desc, str) or len(desc) > MAX_DESC or "\n" in desc:
                raise ValueError(f"a description is one line, up to {MAX_DESC} characters")
            out = _git("init", "--quiet", "--bare", "--initial-branch=main", str(path))
            if out.returncode != 0:
                raise ValueError((out.stderr.strip().splitlines() or ["git init failed"])[-1])
            _git("config", "http.receivepack", "true", cwd=path)
            if path.parent.name == "private":
                _git("config", "core.hooksPath", str(HOOKS), cwd=path)
            (path / "description").write_text((desc.strip() or "") + "\n")
        elif what == "delete":
            path = _target(payload)
            if not (path / "HEAD").exists():
                raise ValueError(f"no repository {path.name} in {path.parent.name}")
            shutil.rmtree(path)
        elif what == "describe":
            path = _target(payload)
            desc = payload.get("description")
            if not (path / "HEAD").exists():
                raise ValueError(f"no repository {path.name} in {path.parent.name}")
            if not isinstance(desc, str) or len(desc) > MAX_DESC or "\n" in desc:
                raise ValueError(f"a description is one line, up to {MAX_DESC} characters")
            (path / "description").write_text(desc.strip() + "\n")
        elif what == "guest-push":
            if type(payload.get("on")) is not bool:
                raise ValueError("on must be true or false")
            if payload["on"]:
                GUEST_PUSH.write_text("guests may push to the public repositories (set on /admin)\n")
            else:
                GUEST_PUSH.unlink(missing_ok=True)
        else:
            raise ValueError("action must be create, delete, describe or guest-push")
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except OSError as exc:
        return 500, {"error": str(exc)}
    return 200, snapshot()
