# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Mirrored git repositories (next-work plan step 12): a bare repository on the box that the
librarian keeps from an upstream, by a policy, as firmware.py keeps the firmware.

A mirror's settings ($HUB_STATE_DIR/library/mirrors.json, set on /admin's Git page):
  name, area       where it appears: git/public/<name>.git or git/private/<name>.git
  upstream         an https URL
  branches         which branches to keep (["main"] by default)
  groups           which tags to keep, newest first, as [{name, kind, pattern, keep}]:
                     kind "tags"        tags matching the glob `pattern`, by version order
                     kind "release"     GitHub releases (not pre-releases) whose tag matches
                     kind "prerelease"  GitHub pre-releases whose tag matches
                   e.g. Meshtastic's firmware: release, keep 2; prerelease, keep 2 (its tags
                   don't say which are alphas; only GitHub's flag does)
  follow, pin      the first branch at the upstream's newest commit, or pinned to `pin` (a
                   commit or a tag)
  history          "shallow" (each kept tag and branch tip at depth 1) or "full"
  budget_mb        a size cap: a mirror over it says so (and the doctor does)

Each update fetches exactly what the policy names, deletes refs that fell out of it, and lets
git drop their objects. It is read-only on the box (public-read-only or private-read-only:
gitrepos.py), and says it is a mirror in its description. Everything runs as the hub user, in
the hub's own folder: no root. Submodules are not mirrored yet.

    ./irate-box mirrors [check|update] [NAME ...]
"""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from irate_box.hub import gitrepos
from irate_box.library import librarian
from irate_box.library.librarian import LibrarianError

SETTINGS = librarian.LIB_DIR / "mirrors.json"
STATUS = librarian.LIB_DIR / "mirrors-status.json"
KINDS = ("tags", "release", "prerelease")
MAX_KEEP = 20
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
FETCH_TIMEOUT = 3 * 3600


def _git(*args, cwd=None, timeout=600):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))


def _read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def load():
    return _read(SETTINGS, {"mirrors": []}).get("mirrors", [])


def status():
    return _read(STATUS, {})


def _ref_ok(r):
    return isinstance(r, str) and REF_RE.match(r) and ".." not in r and not r.endswith((".", "/", ".lock"))


def validate(m):
    """A mirror's settings, normalised; LibrarianError on anything wrong."""
    if not isinstance(m, dict):
        raise LibrarianError("a mirror is an object")
    name = str(m.get("name", "")).strip()
    if name.endswith(".git"):
        name = name[:-4]
    if not gitrepos.NAME_RE.match(name):
        raise LibrarianError("name: letters, digits, '.', '_' and '-', up to 64")
    area = m.get("area", "public")
    if area not in gitrepos.AREAS:
        raise LibrarianError("area: public or private")
    upstream = str(m.get("upstream", "")).strip()
    u = urllib.parse.urlsplit(upstream)
    if u.scheme != "https" or not u.hostname or u.username or u.password or any(c in upstream for c in " \n\t\\"):
        raise LibrarianError("upstream: an https:// URL, with no user or token in it")
    branches = m.get("branches") or ["main"]
    if not isinstance(branches, list) or not 1 <= len(branches) <= 10 or not all(_ref_ok(b) for b in branches):
        raise LibrarianError("branches: 1 to 10 branch names")
    groups = []
    for g in m.get("groups") or []:
        if not isinstance(g, dict) or g.get("kind") not in KINDS:
            raise LibrarianError(f"groups: each has a kind ({', '.join(KINDS)})")
        keep = g.get("keep", 1)
        if type(keep) is not int or not 0 <= keep <= MAX_KEEP:
            raise LibrarianError(f"groups: keep is 0 to {MAX_KEEP}")
        pattern = str(g.get("pattern") or "*")
        if len(pattern) > 64 or not re.match(r"^[A-Za-z0-9*?._/\-\[\]]+$", pattern):
            raise LibrarianError("groups: a pattern is a tag glob, like v*.*.*")
        if g["kind"] != "tags" and not _github(upstream):
            raise LibrarianError("groups: release and prerelease need a github.com upstream")
        groups.append({"name": str(g.get("name") or g["kind"])[:32], "kind": g["kind"], "pattern": pattern, "keep": keep})
    follow = m.get("follow", "latest")
    pin = str(m.get("pin") or "").strip()
    if follow not in ("latest", "pinned") or (follow == "pinned" and not (re.fullmatch(r"[0-9a-f]{40}", pin) or _ref_ok(pin))):
        raise LibrarianError("follow: latest, or pinned with a pin (a commit or a tag)")
    history = m.get("history", "shallow")
    if history not in ("shallow", "full"):
        raise LibrarianError("history: shallow or full")
    budget = m.get("budget_mb", 2048)
    if type(budget) is not int or not 1 <= budget <= 1 << 20:
        raise LibrarianError("budget_mb: a number of MB")
    return {"name": name, "area": area, "upstream": upstream, "branches": branches, "groups": groups,
            "follow": follow, "pin": pin if follow == "pinned" else "", "history": history, "budget_mb": budget}


def _github(url):
    """OWNER/REPO for a github.com upstream, or None."""
    u = urllib.parse.urlsplit(url)
    m = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(\.git)?/?", u.path or "")
    return f"{m.group(1)}/{m.group(2)}" if u.hostname == "github.com" and m else None


def repo_path(m):
    return gitrepos.ROOT / m["area"] / f"{m['name']}.git"


def add(m):
    m = validate(m)
    mirrors = load()
    if any(x["name"] == m["name"] for x in mirrors) or any((gitrepos.ROOT / a / f"{m['name']}.git").exists() for a in gitrepos.AREAS):
        raise LibrarianError(f"{m['name']}.git already exists")
    mirrors.append(m)
    _write(SETTINGS, {"mirrors": mirrors})
    return m


def remove(name, delete_repo=True):
    mirrors = load()
    m = next((x for x in mirrors if x["name"] == name), None)
    if not m:
        raise LibrarianError(f"no mirror {name}")
    _write(SETTINGS, {"mirrors": [x for x in mirrors if x["name"] != name]})
    st = status()
    st.pop(name, None)
    _write(STATUS, st)
    if delete_repo and repo_path(m).is_dir():
        shutil.rmtree(repo_path(m))


# --- what the policy names -------------------------------------------------------------------

def _version_key(tag):
    """Natural order: v2.10.1 after v2.9.3; a tag's numbers, then its text."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", tag) if p]


def _remote_refs(upstream):
    out = _git("ls-remote", "--refs", upstream, timeout=300)
    if out.returncode != 0:
        raise LibrarianError(f"cannot read {upstream}: {(out.stderr.strip().splitlines() or ['git ls-remote failed'])[-1][:200]}")
    refs = {}
    for line in out.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        refs[ref] = sha
    return refs


def _github_releases(upstream):
    """[(tag, prerelease)], newest first, from GitHub's API (drafts left out)."""
    repo = _github(upstream)
    data = librarian._api(f"/repos/{repo}/releases?per_page=100", librarian.token())
    return [(r["tag_name"], bool(r.get("prerelease"))) for r in data if not r.get("draft")]


def wanted(m, refs=None, releases=None):
    """{ref: sha} the policy keeps."""
    refs = _remote_refs(m["upstream"]) if refs is None else refs
    tags = [r[len("refs/tags/"):] for r in refs if r.startswith("refs/tags/")]
    keep, rel = {}, None
    for g in m["groups"]:
        if g["kind"] == "tags":
            chosen = sorted((t for t in tags if fnmatch.fnmatchcase(t, g["pattern"])), key=_version_key, reverse=True)
        else:
            if rel is None:
                rel = _github_releases(m["upstream"]) if releases is None else releases
            chosen = [t for t, pre in rel if pre == (g["kind"] == "prerelease")
                      and fnmatch.fnmatchcase(t, g["pattern"]) and f"refs/tags/{t}" in refs]
        for t in chosen[:g["keep"]]:
            keep[f"refs/tags/{t}"] = refs[f"refs/tags/{t}"]
    for i, b in enumerate(m["branches"]):
        if i == 0 and m["follow"] == "pinned":
            pin = m["pin"]
            sha = pin if re.fullmatch(r"[0-9a-f]{40}", pin) else refs.get(f"refs/tags/{pin}") or refs.get(f"refs/heads/{pin}")
            if not sha:
                raise LibrarianError(f"the pin {pin} is not in {m['upstream']}")
            keep[f"refs/heads/{b}"] = sha
        elif f"refs/heads/{b}" in refs:
            keep[f"refs/heads/{b}"] = refs[f"refs/heads/{b}"]
        else:
            raise LibrarianError(f"{m['upstream']} has no branch {b}")
    return keep


# --- keeping it --------------------------------------------------------------------------------

def _ensure_repo(m):
    path = repo_path(m)
    if not (path / "HEAD").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        out = _git("init", "--quiet", "--bare", f"--initial-branch={m['branches'][0]}", str(path))
        if out.returncode != 0:
            raise LibrarianError((out.stderr.strip().splitlines() or ["git init failed"])[-1])
    # Read-only on the box, whatever the area: a mirror is the upstream's, not anyone's here.
    gitrepos.set_write_level(path, "nobody")
    _git("config", "irate-box.mirror", m["upstream"], cwd=path)
    _git("symbolic-ref", "HEAD", f"refs/heads/{m['branches'][0]}", cwd=path)
    (path / "description").write_text(f"Mirror of {m['upstream']} (kept by the box; read-only here)\n")
    return path


def _local_refs(path):
    out = _git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags", cwd=path)
    return dict(line.split(" ", 1) for line in out.stdout.splitlines() if " " in line)


def sync(m, check_only=False, log=print):
    """One mirror brought to its policy (or, check_only, only looked at). Returns a line."""
    st = status()
    entry = st.setdefault(m["name"], {})
    entry["checked"] = time.time()
    try:
        refs = _remote_refs(m["upstream"])
        keep = wanted(m, refs)
        path = repo_path(m)
        have = _local_refs(path) if (path / "HEAD").exists() else {}
        missing = {r: sha for r, sha in keep.items() if have.get(r) != sha}
        extra = [r for r in have if r not in keep]
        entry.update(wanted=sorted(keep), missing=sorted(missing), extra=sorted(extra))
        if check_only:
            line = (f"{len(missing)} to fetch, {len(extra)} to drop" if missing or extra else "up to date")
        else:
            path = _ensure_repo(m)
            depth = ["--depth", "1"] if m["history"] == "shallow" else []
            for ref, sha in sorted(missing.items()):
                # By name where the upstream has it under that name, by commit for a pin.
                src = ref if refs.get(ref) == sha else sha
                log(f"mirror {m['name']}: fetching {ref}")
                out = _git("fetch", "--quiet", "--no-tags", "--no-write-fetch-head", *depth, m["upstream"],
                           f"+{src}:{ref}", cwd=path, timeout=FETCH_TIMEOUT)
                if out.returncode != 0:
                    raise LibrarianError(f"fetching {ref}: {(out.stderr.strip().splitlines() or ['git fetch failed'])[-1][:200]}")
            for ref in extra:
                _git("update-ref", "-d", ref, cwd=path)
            if extra:
                _git("reflog", "expire", "--expire=now", "--all", cwd=path)
                _git("gc", "--quiet", "--prune=now", cwd=path, timeout=FETCH_TIMEOUT)
            entry.update(missing=[], extra=[], updated=time.time())
            line = f"{len(missing)} fetched, {len(extra)} dropped" if missing or extra else "up to date"
        if (path / "HEAD").exists():
            size = gitrepos._size(path)
            entry["size"] = size
            over = size > m["budget_mb"] << 20
            entry["over_budget"] = over
            if over:
                line += f"; {size >> 20} MB is over its {m['budget_mb']} MB budget"
        entry.pop("error", None)
    except (LibrarianError, OSError, subprocess.SubprocessError) as exc:
        entry["error"] = str(exc)
        line = f"error: {exc}"
    entry["outcome"] = line
    _write(STATUS, st)
    return line


def sync_all(names=None, check_only=False, scheduled=False, hours=24, log=print):
    out = {}
    for m in load():
        if names and m["name"] not in names:
            continue
        if scheduled and not librarian._due(status().get(m["name"], {}), hours):
            continue
        out[m["name"]] = sync(m, check_only, log)
        log(f"mirror {m['name']}: {out[m['name']]}")
    return out


def snapshot():
    st = status()
    return [dict(m, status=st.get(m["name"], {})) for m in load()]


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    mode = args.pop(0) if args and args[0] in ("check", "update") else "update"
    with librarian.Lock():
        for name, line in sync_all(args or None, check_only=(mode == "check")).items():
            print(f"{name}: {line}")
