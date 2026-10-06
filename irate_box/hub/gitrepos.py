# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's git repositories: what /admin's Git page lists, creates and deletes.

Two areas of bare repositories under $HUB_GIT_ROOT (install.sh: /var/lib/hub/git), both owned
by the hub user, who also runs git http-backend and cgit for them (irate-box-git.socket):

  public/   /git/          browse (cgit) and clone for everyone
  private/  /git-private/  everything behind the admin login

Who may push is each repository's own: its preset (next-work plan step 10), kept in its config
as irate-box.write = everyone | admin | nobody. Reading follows the area (cgit lists a whole
area), so the presets are:

  public-everything    public   anyone pushes (security review F17, F18: the doctor warns)
  public-admin-writes  public   the admin pushes (the default)
  public-read-only     public   nobody pushes: content arrives by publishing or mirroring
  private-to-admin     private  the admin pushes (the default)
  private-read-only    private  nobody pushes

nginx asks the hub on every push to /git/ (auth_request to /internal/git-access, decide()
here): yes when anonymous is enough, otherwise its admin login decides (satisfy any). The hub
never sees a password. http.receivepack follows the level, so git itself refuses a push to a
nobody-writes repository even with the login. (The old guest-push flag file was one switch for
every public repository; install.sh turns it into public-everything on each.) Private ones also get the hub's own hooks (git-hooks/, core.hooksPath): a push
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

ROOT = Path(os.environ.get("HUB_GIT_ROOT", Path(__file__).resolve().parents[2] / "git"))
AREAS = {"public": "/git/", "private": "/git-private/"}
GUEST_PUSH = ROOT / "guest-push"  # the old switch: read only as the default for repos from before
WRITE_LEVELS = ("everyone", "admin", "nobody")
PRESETS = {
    "public": {"public-everything": "everyone", "public-admin-writes": "admin", "public-read-only": "nobody"},
    "private": {"private-to-admin": "admin", "private-read-only": "nobody"},
}
PRESET_TEXT = {
    "public-everything": "anyone on the network can browse, clone and push",
    "public-admin-writes": "anyone can browse and clone; pushing needs the admin login",
    "public-read-only": "anyone can browse and clone; nobody can push",
    "private-to-admin": "browse, clone and push with the admin login",
    "private-read-only": "browse and clone with the admin login; nobody can push",
}
# The largest single push the web server takes (irate-box.nginx, the Caddyfile): said on the page.
MAX_PUSH = 64 * 2**20
# A name as typed, without ".git"; it becomes <name>.git, which is what URLs and cgit show.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_DESC = 200
HOOKS = Path(__file__).resolve().parents[2] / "scripts" / "git-hooks"
# Public repositories' own: guests may not rewrite or delete, and a size cap (F18).
HOOKS_PUBLIC = Path(__file__).resolve().parents[2] / "scripts" / "git-hooks-public"


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
    level = write_level(path)
    return {"name": path.name[:-4], "area": area, "url": AREAS[area] + path.name + "/",
            "write": level, "preset": preset_of(area, level), "preset_text": PRESET_TEXT[preset_of(area, level)],
            "description": desc, "size": _size(path), "branches": len(dates),
            "last_commit": int(dates[0]) if dates else None}


def write_level(path):
    """A repository's push level: its own setting, else its area's default."""
    out = _git("config", "--get", "irate-box.write", cwd=path)
    level = out.stdout.strip() if out.returncode == 0 else ""
    if level in WRITE_LEVELS and (level != "everyone" or path.parent.name == "public"):
        return level
    return "everyone" if path.parent.name == "public" and GUEST_PUSH.exists() else "admin"


def preset_of(area, level):
    return next(name for name, lv in PRESETS[area].items() if lv == level)


def set_write_level(path, level):
    """The level, and http.receivepack to match (git's own refusal, if the front ever let a push by)."""
    if level not in PRESETS[path.parent.name].values():
        raise ValueError(f"{path.parent.name} repositories take: {', '.join(PRESETS[path.parent.name])}")
    _git("config", "irate-box.write", level, cwd=path)
    _git("config", "http.receivepack", "false" if level == "nobody" else "true", cwd=path)


def decide(uri, method="GET", git_mode="public"):
    """For nginx's auth_request on /git/'s smart-HTTP paths: may this go ahead without a login?
    True: yes. False: only with the admin login (nginx then asks for it). `git_mode` is the git
    app's access switch (public, private; off never reaches here)."""
    from urllib.parse import unquote, urlsplit, parse_qs
    parts = urlsplit(uri or "")
    path = unquote(parts.path)
    m = re.match(r"^/git/([^/]+)\.git/(.*)$", path)
    if not m or git_mode != "public" or not NAME_RE.match(m.group(1)):
        return False
    service = (parse_qs(parts.query).get("service") or [""])[0]
    writing = m.group(2).endswith("git-receive-pack") or service == "git-receive-pack"
    if not writing:
        return True
    repo = ROOT / "public" / f"{m.group(1)}.git"
    return (repo / "HEAD").exists() and write_level(repo) == "everyone"


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
            "presets": {a: [{"name": n, "write": lv, "text": PRESET_TEXT[n]} for n, lv in ps.items()]
                        for a, ps in PRESETS.items()},
            "max_push": MAX_PUSH, "free": free,
            "now": int(time.time())}


def _target(payload):
    if not isinstance(payload, dict):
        raise ValueError("name a repository: area and name")
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
            set_write_level(path, "admin")  # the area's default; the page changes it
            if path.parent.name == "private":
                _git("config", "core.hooksPath", str(HOOKS), cwd=path)
            else:
                _git("config", "core.hooksPath", str(HOOKS_PUBLIC), cwd=path)
            _git("config", "receive.fsckObjects", "true", cwd=path)  # no malformed objects stored
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
        elif what == "move":
            path = _target(payload)
            if not (path / "HEAD").exists():
                raise ValueError(f"no repository {path.name} in {path.parent.name}")
            to = payload.get("to")
            if to not in AREAS or to == path.parent.name:
                raise ValueError("to: the other area, public or private")
            dest = ROOT / to / path.name
            if dest.exists():
                raise ValueError(f"{path.name} already exists in {to}")
            level = write_level(path)
            os.rename(path, dest)
            # Its hooks and level as the new area has them: anyone-may-push is public-only, and
            # builds run only for private repositories.
            _git("config", "core.hooksPath", str(HOOKS if to == "private" else HOOKS_PUBLIC), cwd=dest)
            set_write_level(dest, "admin" if level == "everyone" else level)
        elif what == "publish":
            src, dst = _target(payload.get("from") or {}), _target(payload.get("to") or {})
            if src == dst:
                raise ValueError("publish to another repository")
            for r in (src, dst):
                if not (r / "HEAD").exists():
                    raise ValueError(f"no repository {r.name} in {r.parent.name}")
            refs = payload.get("refs") or ["main"]
            if not isinstance(refs, list) or not refs or len(refs) > 20 or not all(
                    isinstance(r, str) and re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$", r) and ".." not in r for r in refs):
                raise ValueError("refs: up to 20 branch or tag names")
            specs = []
            for r in refs:
                if _git("show-ref", "--verify", "--quiet", f"refs/heads/{r}", cwd=src).returncode == 0:
                    specs.append(f"+refs/heads/{r}:refs/heads/{r}")
                elif _git("show-ref", "--verify", "--quiet", f"refs/tags/{r}", cwd=src).returncode == 0:
                    specs.append(f"+refs/tags/{r}:refs/tags/{r}")
                else:
                    raise ValueError(f"{src.name} has no branch or tag {r}")
            # The hub's own copy between its own repositories: not a push, so the destination's
            # preset (read-only, say) does not apply; the owner chose this on /admin.
            out = _git("fetch", "--quiet", "--no-write-fetch-head", str(src), *specs, cwd=dst)
            if out.returncode != 0:
                raise ValueError((out.stderr.strip().splitlines() or ["git fetch failed"])[-1])
        elif what == "preset":
            path = _target(payload)
            if not (path / "HEAD").exists():
                raise ValueError(f"no repository {path.name} in {path.parent.name}")
            preset = payload.get("preset")
            if preset not in PRESETS[path.parent.name]:
                raise ValueError(f"{path.parent.name} repositories take: {', '.join(PRESETS[path.parent.name])}")
            set_write_level(path, PRESETS[path.parent.name][preset])
        else:
            raise ValueError("action must be create, delete, describe, preset, move or publish")
    except ValueError as exc:
        return 400, {"error": str(exc)}
    except OSError as exc:
        return 500, {"error": str(exc)}
    return 200, snapshot()
