# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's git repositories: what /admin's Git page lists, creates and deletes.

Two areas of bare repositories under $HUB_GIT_ROOT (install.sh: /var/lib/hub/git), both owned
by the hub user, who also runs git http-backend and cgit for them (irate-box-git.socket):

  public/   /git/          browse (cgit) and clone for everyone
  private/  /git-private/  everything behind the admin login

Who may push is each repository's own: its preset, kept in its config
as irate-box.write = everyone | users | admin | nobody. Reading follows the area (cgit lists a
whole area), so the presets are:

  public-everything    public   anyone pushes (the doctor warns)
  public-users-write   public   any account pushes, with its name and password
  public-admin-writes  public   the admin pushes (the default)
  public-read-only     public   nobody pushes: content arrives by publishing or mirroring
  private-to-admin     private  the admin pushes (the default)
  private-read-only    private  nobody pushes

nginx asks the hub on every push or fetch (auth_request to /internal/git-access, decide()
here): yes when anonymous is enough, or when the request's own basic-auth credentials are an
account's that may (accounts.py: a user where users push, an admin account where the admin
does, the private area included); otherwise the box's own admin login decides (satisfy any),
which the hub never sees. http.receivepack follows the level, so git itself refuses a push to a
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

from irate_box import confine
from irate_box.hub import refused
from irate_box.hub.refused import Refused
from irate_box.msg import M

ROOT = Path(os.environ.get("HUB_GIT_ROOT", Path(__file__).resolve().parents[2] / "git"))
AREAS = {"public": "/git/", "private": "/git-private/"}
# The Git add-on is the serving of the repositories (cgit, git http-backend); the repositories themselves
# are the box's storage and stay without it. Taking the add-on away (irate-box gitaddon remove, install.sh
# --no-git) leaves this file, which keeps it away through updates; install.sh --with-git takes the file away.
REMOVED = ROOT / ".git-add-on-removed"


def served():
    """Whether the Git add-on is on the box: the repositories can be browsed, cloned and pushed to."""
    return not REMOVED.exists()


GUEST_PUSH = ROOT / "guest-push"  # the old switch: read only as the default for repos from before
WRITE_LEVELS = ("everyone", "users", "admin", "nobody")
PRESETS = {
    "public": {
        "public-everything": "everyone",
        "public-users-write": "users",
        "public-admin-writes": "admin",
        "public-read-only": "nobody",
    },
    "private": {"private-to-admin": "admin", "private-read-only": "nobody"},
}
PRESET_TEXT = {
    "public-everything": "anyone on the network can browse, clone and push",
    "public-users-write": "anyone can browse and clone; the box's users push, with their account's name and password",
    "public-admin-writes": "Anyone can browse and clone. Pushing needs an administrator sign-in.",
    "public-read-only": "Anyone can browse and clone. Nobody can push.",
    "private-to-admin": "Browse, clone and push with an administrator sign-in.",
    "private-read-only": "Browse and clone with an administrator sign-in. Nobody can push.",
}
# The largest single push the web server takes (irate-box.nginx, the Caddyfile): said on the page.
MAX_PUSH = 64 * 2**20
# A name as typed, without ".git"; it becomes <name>.git, which is what URLs and cgit show.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_DESC = 200
DESCRIPTION_BAD = M(
    "git.description.invalid", "Enter a description of one line, up to {most} characters.", most=MAX_DESC
)
HOOKS = Path(__file__).resolve().parents[2] / "scripts" / "git-hooks"
# Public repositories' own: guests may not rewrite or delete, and a size cap.
HOOKS_PUBLIC = Path(__file__).resolve().parents[2] / "scripts" / "git-hooks-public"


def _git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=60)  # noqa: TID251


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
    mirror = mirror_of(path)
    return {
        "name": path.name[:-4],
        "area": area,
        "url": AREAS[area] + path.name + "/",
        "write": level,
        "preset": preset_of(area, level),
        "preset_text": PRESET_TEXT[preset_of(area, level)],
        "mirror_of": mirror,
        # Build on push (ci.py): offered only where only the admin pushes.
        "can_build": area == "private" and level == "admin" and not mirror,
        "build": build_on(path),
        "has_script": bool(dates) and _git("cat-file", "-e", "HEAD:.irate-ci.sh", cwd=path).returncode == 0,
        "description": desc,
        "size": _size(path),
        "branches": len(dates),
        "last_commit": int(dates[0]) if dates else None,
    }


def write_level(path):
    """A repository's push level: its own setting, else its area's default."""
    out = _git("config", "--get", "irate-box.write", cwd=path)
    level = out.stdout.strip() if out.returncode == 0 else ""
    if level in WRITE_LEVELS and (level not in ("everyone", "users") or path.parent.name == "public"):
        return level
    return "everyone" if path.parent.name == "public" and GUEST_PUSH.exists() else "admin"


def build_on(path):
    """The repository's build-on-push switch (irate-box.ci): on unless set off, so repositories
    that built before the switch existed still do. ci.py also needs it private,
    only the admin pushing, and a .irate-ci.sh in the pushed commit."""
    out = _git("config", "--get", "irate-box.ci", cwd=path)
    return not (out.returncode == 0 and out.stdout.strip() == "off")


def mirror_of(path):
    """The upstream a mirror is kept from (mirrors.py), or None for the box's own repository."""
    out = _git("config", "--get", "irate-box.mirror", cwd=path)
    return out.stdout.strip() or None if out.returncode == 0 else None


def _not_a_mirror(path):
    if mirror_of(path):
        raise Refused(
            M(
                "git.repo.mirror",
                "Mirror is read-only. The librarian keeps {name} up to date. To remove it, use Mirrors.",
                name=path.name,
            )
        )


# A refusal that names an area is one message per area: "public" and "private" are words to translate.
def _not_found(path):
    if path.parent.name == "public":
        return M(
            "git.repo.not-found-public",
            "Repository not found. The public area has no repository {name}.",
            name=path.name,
        )
    return M(
        "git.repo.not-found-private", "Repository not found. The private area has no repository {name}.", name=path.name
    )


def _exists(name, area):
    if area == "public":
        return M(
            "git.repo.exists-public", "Repository already exists. The public area has a repository {name}.", name=name
        )
    return M(
        "git.repo.exists-private", "Repository already exists. The private area has a repository {name}.", name=name
    )


def _preset_bad(area):
    if area == "public":
        return M(
            "git.preset.invalid-public",
            "Select a preset for public repositories: {presets}.",
            presets=", ".join(PRESETS["public"]),
        )
    return M(
        "git.preset.invalid-private",
        "Select a preset for private repositories: {presets}.",
        presets=", ".join(PRESETS["private"]),
    )


def preset_of(area, level):
    return next(name for name, lv in PRESETS[area].items() if lv == level)


def set_write_level(path, level):
    """The level, and http.receivepack to match (git's own refusal, if the front ever let a push by)."""
    if level not in PRESETS[path.parent.name].values():
        raise Refused(_preset_bad(path.parent.name))
    _git("config", "irate-box.write", level, cwd=path)
    _git("config", "http.receivepack", "false" if level == "nobody" else "true", cwd=path)


def decide(uri, method="GET", git_mode="public", account=None):
    """For nginx's auth_request on the smart-HTTP paths (/git/ and /git-private/): may this go
    ahead without the box's own login? True: yes. False: only with that login (nginx then asks
    for it). `git_mode` is the git app's access switch (public, users, private; off never reaches
    here). account: {name, role} when the request's basic-auth credentials are an account's
    (accounts.check_basic), else None."""
    from urllib.parse import parse_qs, unquote, urlsplit

    parts = urlsplit(uri or "")
    path = unquote(parts.path)
    m = re.match(r"^/git(-private)?/([^/]+)\.git/(.*)$", path)
    if not m or not NAME_RE.match(m.group(2)):
        return False
    admin = account is not None and account.get("role") == "admin"
    if m.group(1) or git_mode == "private":
        return admin  # the private area, or the git app kept for the admin: the admin's alone
    if git_mode == "users" and account is None:
        return False
    service = (parse_qs(parts.query).get("service") or [""])[0]
    writing = m.group(3).endswith("git-receive-pack") or service == "git-receive-pack"
    if not writing:
        return True
    repo = confine.under(ROOT, "public", f"{m.group(2)}.git")
    if not (repo / "HEAD").exists():
        return False
    level = write_level(repo)
    return level == "everyone" or (level == "users" and account is not None) or (level == "admin" and admin)


def snapshot():
    repos = []
    for area in AREAS:
        base = ROOT / area
        if base.is_dir():
            repos += [
                _repo_info(p, area)
                for p in sorted(base.glob("*.git"), key=lambda p: p.name.lower())
                if (p / "HEAD").exists()
            ]
    try:
        free = shutil.disk_usage(ROOT).free
    except OSError:
        free = None
    return {
        "installed": all((ROOT / a).is_dir() for a in AREAS),
        "served": served(),
        "repos": repos,
        "presets": {
            a: [{"name": n, "write": lv, "text": PRESET_TEXT[n]} for n, lv in ps.items()] for a, ps in PRESETS.items()
        },
        "max_push": MAX_PUSH,
        "free": free,
        "now": int(time.time()),
    }


def _target(payload):
    if not isinstance(payload, dict):
        raise Refused(M("git.target.missing", "Select an area and enter a repository name."))
    area, name = payload.get("area"), payload.get("name")
    if area not in AREAS:
        raise Refused(M("git.area.invalid", "Select destination area: public or private."))
    if not isinstance(name, str):
        raise Refused(M("git.name.missing", "Enter a repository name."))
    name = name.strip()
    if name.endswith(".git"):
        name = name[:-4]
    if not NAME_RE.match(name):
        raise Refused(
            M(
                "git.name.invalid",
                "Invalid repository name. Use letters, digits, '.', '_' and '-', up to 64 characters. "
                "Start with a letter or digit.",
            )
        )
    return confine.under(ROOT, area, f"{name}.git")


def action(payload):
    """One change from /admin's Git page. Returns (http status, body): the new snapshot, or
    an error."""
    try:
        what = payload.get("action")
        if what == "create":
            path = _target(payload)
            if path.exists():
                raise Refused(_exists(path.name, path.parent.name))
            desc = payload.get("description") or ""
            if not isinstance(desc, str) or len(desc) > MAX_DESC or "\n" in desc:
                raise Refused(DESCRIPTION_BAD)
            out = _git("init", "--quiet", "--bare", "--initial-branch=main", str(path))
            if out.returncode != 0:
                raise Refused(
                    M(
                        "git.init.failed",
                        "Git cannot make the repository ({detail}).",
                        detail=(out.stderr.strip().splitlines() or ["git init failed"])[-1],
                    )
                )
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
                raise Refused(_not_found(path))
            _not_a_mirror(path)
            shutil.rmtree(path)
        elif what == "describe":
            path = _target(payload)
            desc = payload.get("description")
            if not (path / "HEAD").exists():
                raise Refused(_not_found(path))
            if not isinstance(desc, str) or len(desc) > MAX_DESC or "\n" in desc:
                raise Refused(DESCRIPTION_BAD)
            (path / "description").write_text(desc.strip() + "\n")
        elif what == "move":
            path = _target(payload)
            if not (path / "HEAD").exists():
                raise Refused(_not_found(path))
            _not_a_mirror(path)
            to = payload.get("to")
            if to not in AREAS or to == path.parent.name:
                raise Refused(M("git.move.area", "Select destination area. A repository moves to the other area."))
            dest = confine.under(ROOT, to, path.name)
            if dest.exists():
                raise Refused(_exists(path.name, to))
            level = write_level(path)
            os.rename(path, dest)
            # Its hooks and level as the new area has them: anyone-may-push is public-only, and
            # builds run only for private repositories.
            _git("config", "core.hooksPath", str(HOOKS if to == "private" else HOOKS_PUBLIC), cwd=dest)
            set_write_level(dest, "admin" if level == "everyone" else level)
        elif what == "publish":
            # "create_to" {area, name, description} in place of "to": the destination is made first, once the
            # branches and tags are known to be there, so a refusal leaves no empty repository behind.
            creating = isinstance(payload.get("create_to"), dict)
            src, dst = (
                _target(payload.get("from") or {}),
                _target(payload["create_to"] if creating else payload.get("to") or {}),
            )
            if src == dst:
                raise Refused(M("git.publish.same", "Select a different destination repository."))
            for r in (src,) if creating else (src, dst):
                if not (r / "HEAD").exists():
                    raise Refused(_not_found(r))
            refs = payload.get("refs") or ["main"]
            if (
                not isinstance(refs, list)
                or not refs
                or len(refs) > 20
                or not all(
                    isinstance(r, str) and re.match(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$", r) and ".." not in r
                    for r in refs
                )
            ):
                raise Refused(M("git.publish.refs", "Select up to 20 references (branch or tag names)."))
            specs = []
            for r in refs:
                if _git("show-ref", "--verify", "--quiet", f"refs/heads/{r}", cwd=src).returncode == 0:
                    specs.append(f"+refs/heads/{r}:refs/heads/{r}")
                elif _git("show-ref", "--verify", "--quiet", f"refs/tags/{r}", cwd=src).returncode == 0:
                    specs.append(f"+refs/tags/{r}:refs/tags/{r}")
                else:
                    raise Refused(
                        M(
                            "git.publish.ref-not-found",
                            "Reference not found. {repo} has no branch or tag {ref}.",
                            repo=src.name,
                            ref=r,
                        )
                    )
            if creating:
                code, body = action(dict(payload["create_to"], action="create"))
                if code != 200:
                    return code, body
            # The hub's own copy between its own repositories: not a push, so the destination's
            # preset (read-only, say) does not apply; the owner chose this on /admin.
            out = _git("fetch", "--quiet", "--no-write-fetch-head", str(src), *specs, cwd=dst)
            if out.returncode != 0:
                raise Refused(
                    M(
                        "git.publish.failed",
                        "Git cannot copy the references ({detail}).",
                        detail=(out.stderr.strip().splitlines() or ["git fetch failed"])[-1],
                    )
                )
        elif what == "preset":
            path = _target(payload)
            if not (path / "HEAD").exists():
                raise Refused(_not_found(path))
            _not_a_mirror(path)
            preset = payload.get("preset")
            if preset not in PRESETS[path.parent.name]:
                raise Refused(_preset_bad(path.parent.name))
            set_write_level(path, PRESETS[path.parent.name][preset])
        elif what == "build":
            path = _target(payload)
            if not (path / "HEAD").exists():
                raise Refused(_not_found(path))
            _not_a_mirror(path)
            on = payload.get("on")
            if type(on) is not bool:
                raise Refused(M("git.build.on", "The build switch must be true or false."))
            if on and (path.parent.name != "private" or write_level(path) != "admin"):
                raise Refused(
                    M(
                        "git.build.access",
                        "Build access restriction. Builds need a private repository where only administrators can push.",
                    )
                )
            _git("config", "irate-box.ci", "on" if on else "off", cwd=path)
        else:
            raise Refused(
                M("git.action.invalid", "The action must be create, delete, describe, preset, move, publish or build.")
            )
    except ValueError as exc:
        return 400, refused.answer(exc)
    except OSError as exc:
        return 500, {"error": str(exc)}
    return 200, snapshot()
