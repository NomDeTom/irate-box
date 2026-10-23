# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Mirrored git repositories: a bare repository on the box that the
librarian keeps from an upstream, by a policy, as firmware.py keeps the firmware.

A mirror's settings ($HUB_STATE_DIR/library/mirrors.json, set on /admin's Git page):
  name, area       where it appears: git/public/<name>.git or git/private/<name>.git
  upstream         an https URL
  description      one line, up to gitrepos.MAX_DESC characters, which cgit shows; left empty, the
                   first heading of the upstream's README (read after each update), else the
                   upstream's name
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
  submodules       also mirror each submodule the kept refs name, at exactly those commits,
                   as a read-only repository beside it (<name>--<submodule>.git); builds on the
                   box then take them from here (URLS: ci.py rewrites the upstream URLs)

Each update fetches exactly what the policy names, deletes refs that fell out of it, and lets
git drop their objects. It is read-only on the box (public-read-only or private-read-only:
gitrepos.py). Everything runs as the hub user, in
the hub's own folder: no root. Nested submodules (a submodule's own) are not followed.

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

from irate_box import confine
from irate_box.hub import gitrepos
from irate_box.library import librarian
from irate_box.library.librarian import LibrarianError
from irate_box.msg import M, is_message

SETTINGS = librarian.LIB_DIR / "mirrors.json"
# upstream URL -> the local bare repository, for every mirror and submodule mirror: what a build
# on the box (ci.py, as hubci) fetches instead. In the git folder, which hubci can read.
URLS = gitrepos.ROOT / "mirror-urls.json"
STATUS = librarian.LIB_DIR / "mirrors-status.json"
KINDS = ("tags", "release", "prerelease")
MAX_KEEP = 20
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
FETCH_TIMEOUT = 3 * 3600
# Every upstream tag of each mirror, held apart from the mirror: the tag objects (with their signatures)
# and the commits they name, no files (a partial repository, --filter=tree:0, never served). A signed
# tag can be checked from here, and a tag moved upstream noticed, for a few KB a tag.
TAGS_DIR = librarian.LIB_DIR / "tags"


def _git(*args, cwd=None, timeout=600):
    return subprocess.run(  # noqa: TID251
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
    )


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


def _not_found(name):
    return M("mirrors.mirror.not-found", "Source not found: mirror {name}.", name=name)


def _ref_ok(r):
    return isinstance(r, str) and REF_RE.match(r) and ".." not in r and not r.endswith((".", "/", ".lock"))


def validate(m):
    """A mirror's settings, normalised; LibrarianError on anything wrong."""
    if not isinstance(m, dict):
        raise LibrarianError(M("mirrors.settings.not-object", "Invalid source settings. A mirror is an object."))
    name = str(m.get("name", "")).strip()
    if name.endswith(".git"):
        name = name[:-4]
    if not gitrepos.NAME_RE.match(name):
        raise LibrarianError(
            M(
                "mirrors.name.invalid",
                "Invalid mirror name. Use letters, digits, '.', '_' and '-', up to 64 characters.",
            )
        )
    area = m.get("area", "public")
    if area not in gitrepos.AREAS:
        raise LibrarianError(M("mirrors.area.invalid", "Invalid destination area. Select public or private."))
    upstream = str(m.get("upstream", "")).strip()
    u = urllib.parse.urlsplit(upstream)
    if u.scheme != "https" or not u.hostname or u.username or u.password or any(c in upstream for c in " \n\t\\"):
        raise LibrarianError(
            M(
                "mirrors.upstream.invalid",
                "Invalid source settings. Enter an https:// URL for the upstream, with no user name or token in it.",
            )
        )
    description = m.get("description") or ""
    if not isinstance(description, str) or len(description.strip()) > gitrepos.MAX_DESC or "\n" in description.strip():
        raise LibrarianError(gitrepos.DESCRIPTION_BAD)
    branches = m.get("branches") or ["main"]
    if not isinstance(branches, list) or not 1 <= len(branches) <= 10 or not all(_ref_ok(b) for b in branches):
        raise LibrarianError(M("mirrors.branches.invalid", "Invalid branches. Enter 1 to 10 branch names."))
    groups = []
    for g in m.get("groups") or []:
        if not isinstance(g, dict) or g.get("kind") not in KINDS:
            raise LibrarianError(
                M(
                    "mirrors.group.kind-invalid",
                    "Invalid group kind. Use one of these kinds: {kinds}.",
                    kinds=list(KINDS),
                )
            )
        keep = g.get("keep", 1)
        if type(keep) is not int or not 0 <= keep <= MAX_KEEP:
            raise LibrarianError(
                M(
                    "mirrors.group.keep-invalid",
                    "Invalid source settings. In groups, keep must be from 0 to {max}.",
                    max=MAX_KEEP,
                )
            )
        pattern = str(g.get("pattern") or "*")
        if len(pattern) > 64 or not re.match(r"^[A-Za-z0-9*?._/\-\[\]]+$", pattern):
            raise LibrarianError(
                M(
                    "mirrors.group.pattern-invalid",
                    "Invalid source settings. In groups, a pattern is a tag glob, for example v*.*.*.",
                )
            )
        if g["kind"] != "tags" and not _github(upstream):
            raise LibrarianError(
                M(
                    "mirrors.group.needs-github",
                    "Invalid source settings. In groups, release and prerelease need a github.com upstream.",
                )
            )
        skip = g.get("skip_revoked", True)
        if type(skip) is not bool:
            raise LibrarianError(
                M(
                    "mirrors.group.skip-revoked-invalid",
                    "Invalid source settings. In groups, skip_revoked must be true or false.",
                )
            )
        group = {"name": str(g.get("name") or g["kind"])[:32], "kind": g["kind"], "pattern": pattern, "keep": keep}
        if g["kind"] != "tags":
            group["skip_revoked"] = skip
        groups.append(group)
    follow = m.get("follow", "latest")
    pin = str(m.get("pin") or "").strip()
    if follow not in ("latest", "pinned") or (
        follow == "pinned" and not (re.fullmatch(r"[0-9a-f]{40}", pin) or _ref_ok(pin))
    ):
        raise LibrarianError(
            M(
                "mirrors.follow.invalid",
                "Invalid source settings. Follow must be latest, or pinned with a pin (a commit or a tag).",
            )
        )
    history = m.get("history", "shallow")
    if history not in ("shallow", "full"):
        raise LibrarianError(M("mirrors.history.invalid", "Invalid source settings. History must be shallow or full."))
    if type(m.get("submodules", False)) is not bool:
        raise LibrarianError(
            M("mirrors.submodules.invalid", "Invalid source settings. Submodules must be true or false.")
        )
    budget = m.get("budget_mb", 2048)
    if type(budget) is not int or not 1 <= budget <= 1 << 20:
        raise LibrarianError(
            M("mirrors.budget.invalid", "Invalid storage limit. Enter a number of MB from 1 to 1048576.")
        )
    return {
        "name": name,
        "area": area,
        "upstream": upstream,
        "description": description.strip(),
        "branches": branches,
        "groups": groups,
        "follow": follow,
        "pin": pin if follow == "pinned" else "",
        "history": history,
        "budget_mb": budget,
        "submodules": bool(m.get("submodules", False)),
    }


def _github(url):
    """OWNER/REPO for a github.com upstream, or None."""
    u = urllib.parse.urlsplit(url)
    m = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(\.git)?/?", u.path or "")
    return f"{m.group(1)}/{m.group(2)}" if u.hostname == "github.com" and m else None


def repo_path(m):
    return gitrepos.ROOT / m["area"] / f"{m['name']}.git"


def tags_path(m):
    return confine.under(TAGS_DIR, f"{m['name']}.git")


def _sync_tags(m, refs):
    """Every tag the upstream has, into the mirror's tags repository: {count, signed, moved: [names whose commit
    or tag object changed upstream since the last look]}. Tags gone upstream go here too (pruned)."""
    path = tags_path(m)
    if not (path / "HEAD").exists():
        path.mkdir(parents=True, exist_ok=True)
        _git("init", "--quiet", "--bare", str(path))
        _git("config", "remote.origin.promisor", "true", cwd=path)
        _git("config", "remote.origin.partialclonefilter", "tree:0", cwd=path)
    _git("config", "remote.origin.url", m["upstream"], cwd=path)
    before = {r: sha for r, sha in _local_refs(path).items() if r.startswith("refs/tags/")}
    upstream = {r: sha for r, sha in refs.items() if r.startswith("refs/tags/")}
    moved = sorted(r[len("refs/tags/") :] for r, sha in before.items() if r in upstream and upstream[r] != sha)
    if upstream:
        out = _git(
            "fetch",
            "--quiet",
            "--prune",
            "--no-write-fetch-head",
            "--filter=tree:0",
            "--depth",
            "1",
            "origin",
            "+refs/tags/*:refs/tags/*",
            cwd=path,
            timeout=FETCH_TIMEOUT,
        )
        if out.returncode != 0:
            raise LibrarianError(
                M(
                    "mirrors.tags.fetch-failed",
                    "Tag download failed: {output}",
                    output=(out.stderr.strip().splitlines() or ["git fetch failed"])[-1][:200],
                )
            )
    else:
        for r in before:
            _git("update-ref", "-d", r, cwd=path)
    listed = _git("for-each-ref", "refs/tags", "--format=%(objecttype)\t%(contents:signature)", cwd=path).stdout
    rows = listed.splitlines()  # a signature runs on over lines of its own, which start with neither word
    count = sum(1 for ln in rows if ln.startswith(("tag\t", "commit\t")))
    signed = sum(1 for ln in rows if ln.startswith("tag\t-----BEGIN"))
    return {"count": count, "signed": signed, "moved": moved}


def add(m):
    m = validate(m)
    mirrors = load()
    if any(x["name"] == m["name"] for x in mirrors) or any(
        confine.under(gitrepos.ROOT, a, f"{m['name']}.git").exists() for a in gitrepos.AREAS
    ):
        raise LibrarianError(M("mirrors.repo.exists", "Repository already exists: {name}.git.", name=m["name"]))
    mirrors.append(m)
    _write(SETTINGS, {"mirrors": mirrors})
    return m


def change(m):
    """A mirror's settings replaced (same name and area: those are where it lives)."""
    m = validate(m)
    mirrors = load()
    old = next((x for x in mirrors if x["name"] == m["name"]), None)
    if not old:
        raise LibrarianError(_not_found(m["name"]))
    if old["area"] != m["area"]:
        raise LibrarianError(
            M(
                "mirrors.area.fixed",
                "The area of a mirror cannot change. Remove the mirror and add it again in the other area.",
            )
        )
    _write(SETTINGS, {"mirrors": [m if x["name"] == m["name"] else x for x in mirrors]})
    return m


def remove(name, delete_repo=True):
    mirrors = load()
    m = next((x for x in mirrors if x["name"] == name), None)
    if not m:
        raise LibrarianError(_not_found(name))
    _write(SETTINGS, {"mirrors": [x for x in mirrors if x["name"] != name]})
    st = status()
    subs = (st.pop(name, None) or {}).get("submodules", {})
    _write(STATUS, st)
    if delete_repo:
        for p in [repo_path(m), tags_path(m)] + [gitrepos.ROOT / m["area"] / f"{s['repo']}.git" for s in subs.values()]:
            if p.is_dir():
                shutil.rmtree(p)
    write_urls()


# --- what the policy names -------------------------------------------------------------------


def _version_key(tag):
    """Natural order: v2.10.1 after v2.9.3; a tag's numbers, then its text."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", tag) if p]


def _remote_refs(upstream):
    out = _git("ls-remote", "--refs", upstream, timeout=300)
    if out.returncode != 0:
        raise LibrarianError(
            M(
                "mirrors.upstream.unreadable",
                "Source not found. The box cannot read {upstream}: {output}",
                upstream=upstream,
                output=(out.stderr.strip().splitlines() or ["git ls-remote failed"])[-1][:200],
            )
        )
    refs = {}
    for line in out.stdout.splitlines():
        sha, _, ref = line.partition("\t")
        refs[ref] = sha
    return refs


def _github_releases(upstream):
    """[[tag, prerelease, revoked]], newest first, from GitHub's API (drafts left out)."""
    repo = _github(upstream)
    data = librarian._api(f"/repos/{repo}/releases?per_page=100", librarian.token())
    return [[r["tag_name"], bool(r.get("prerelease")), librarian.revoked(r)] for r in data if not r.get("draft")]


def _releases(m, entry):
    """The release list for a mirror with release groups, kept in its status so that a refused
    API call (60 an hour without a token) is answered from the last one, its age said."""
    if not any(g["kind"] != "tags" for g in m["groups"]):
        entry.pop("releases", None)
        return None
    try:
        rel = _github_releases(m["upstream"])
        entry["releases"] = {"list": rel, "fetched": time.time()}
        entry.pop("releases_cached", None)
        return rel
    except LibrarianError as exc:
        cached = entry.get("releases") or {}
        if not cached.get("list"):
            raise
        entry["releases_cached"] = {"since": cached.get("fetched"), "why": str(exc)[:200]}
        return cached["list"]


def wanted(m, refs=None, releases=None):
    """{ref: sha} the policy keeps."""
    refs = _remote_refs(m["upstream"]) if refs is None else refs
    tags = [r[len("refs/tags/") :] for r in refs if r.startswith("refs/tags/")]
    keep, rel = {}, None
    for g in m["groups"]:
        if g["kind"] == "tags":
            chosen = sorted((t for t in tags if fnmatch.fnmatchcase(t, g["pattern"])), key=_version_key, reverse=True)
        else:
            if rel is None:
                rel = _github_releases(m["upstream"]) if releases is None else releases
            chosen = [
                r[0]
                for r in rel
                if r[1] == (g["kind"] == "prerelease")
                and not (g.get("skip_revoked", True) and len(r) > 2 and r[2])
                and fnmatch.fnmatchcase(r[0], g["pattern"])
                and f"refs/tags/{r[0]}" in refs
            ]
        for t in chosen[: g["keep"]]:
            keep[f"refs/tags/{t}"] = refs[f"refs/tags/{t}"]
    for i, b in enumerate(m["branches"]):
        if i == 0 and m["follow"] == "pinned":
            pin = m["pin"]
            sha = (
                pin
                if re.fullmatch(r"[0-9a-f]{40}", pin)
                else refs.get(f"refs/tags/{pin}") or refs.get(f"refs/heads/{pin}")
            )
            if not sha:
                raise LibrarianError(
                    M(
                        "mirrors.pin.not-found",
                        "Source not found. The pin {pin} is not in {upstream}.",
                        pin=pin,
                        upstream=m["upstream"],
                    )
                )
            keep[f"refs/heads/{b}"] = sha
        elif f"refs/heads/{b}" in refs:
            keep[f"refs/heads/{b}"] = refs[f"refs/heads/{b}"]
        else:
            raise LibrarianError(
                M(
                    "mirrors.branch.not-found",
                    "Source not found. {upstream} has no branch {branch}.",
                    upstream=m["upstream"],
                    branch=b,
                )
            )
    return keep


# --- keeping it --------------------------------------------------------------------------------


def _ensure_repo(m, path=None, upstream=None, what=None):
    path = path or repo_path(m)
    upstream = upstream or m["upstream"]
    if not (path / "HEAD").exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        out = _git("init", "--quiet", "--bare", f"--initial-branch={m['branches'][0]}", str(path))
        if out.returncode != 0:
            raise LibrarianError(
                M(
                    "library.git.failed",
                    "Git {what} failed: {output}",
                    what="init",
                    output=(out.stderr.strip().splitlines() or [""])[-1],
                )
            )
    # Read-only on the box, whatever the area: a mirror is the upstream's, not anyone's here.
    gitrepos.set_write_level(path, "nobody")
    _git("config", "irate-box.mirror", upstream, cwd=path)
    _git("symbolic-ref", "HEAD", f"refs/heads/{m['branches'][0]}", cwd=path)
    if what:
        (path / "description").write_text(f"{what} of {upstream} (kept by the box; read-only here)\n")
    elif not (path / "description").is_file():
        (path / "description").write_text(description_of(m, path) + "\n")
    return path


README_NAMES = ("README.md", "README.rst", "README")
RST_RULE_RE = re.compile(r"^([=\-~^\"'`#*+._:])\1{2,}\s*$")


def _plain(text):
    """A heading without its markup: images and tags gone, a link as its words, emphasis marks and
    a closing run of # removed."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"[`*_]+", "", text.replace("\\", ""))
    return re.sub(r"\s+", " ", text.strip().rstrip("#")).strip()


def readme_title(text):
    """The first heading of a README: a Markdown # line, or a line with a setext or reStructuredText
    underline (and overline); None when there is none in its first 200 lines."""
    lines = text.splitlines()[:200]
    for i, line in enumerate(lines):
        rule = lines[i + 1] if i + 1 < len(lines) else ""
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+)$", line)
        if heading:
            found = _plain(heading.group(1))
        elif RST_RULE_RE.match(line) and i + 2 < len(lines) and lines[i + 2].strip() == line.strip() and rule.strip():
            found = _plain(rule)  # an overline too: the line between two rules
        elif line.strip() and not RST_RULE_RE.match(line) and RST_RULE_RE.match(rule) and len(rule.strip()) >= 3:
            found = _plain(line)
        else:
            continue
        if found:
            return found[: gitrepos.MAX_DESC]
    return None


def description_of(m, path):
    """What cgit shows for a mirror: its own description, else the README's first heading at the
    default branch's head (read in place, no checkout), else the upstream's name."""
    if m.get("description"):
        return m["description"]
    for name in README_NAMES:
        out = _git("show", f"HEAD:{name}", cwd=path)
        if out.returncode == 0:
            title = readme_title(out.stdout)
            if title:
                return title
    return (
        urllib.parse.urlsplit(m["upstream"]).path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or m["upstream"]
    )[: gitrepos.MAX_DESC]


def _sub_name(m, sub_path):
    """<mirror>--<submodule's last path part>, within a repository name's 64 characters."""
    tail = re.sub(r"[^A-Za-z0-9._-]", "-", sub_path.rstrip("/").rsplit("/", 1)[-1]).strip(".-") or "sub"
    return f"{m['name']}--{tail}"[:64].rstrip(".-")


def _submodules_at(path, ref):
    """{submodule path: (url, commit)} that `ref` names (its .gitmodules and its gitlinks)."""
    urls = {}
    out = _git("config", "--blob", f"{ref}:.gitmodules", "--get-regexp", r"^submodule\..*\.(path|url)$", cwd=path)
    entries = {}
    for line in out.stdout.splitlines():
        key, _, value = line.partition(" ")
        name, field = key[len("submodule.") :].rsplit(".", 1)
        entries.setdefault(name, {})[field] = value
    for e in entries.values():
        if "path" in e and "url" in e:
            urls[e["path"]] = e["url"]
    out = _git("ls-tree", "-r", ref, cwd=path)
    found = {}
    for line in out.stdout.splitlines():
        meta, _, sub_path = line.partition("\t")
        mode, kind, sha = meta.split()
        if mode == "160000" and sub_path in urls:
            found[sub_path] = (urls[sub_path], sha)
    return found


def _sync_submodules(m, path, kept_refs, log):
    """Each submodule the kept refs name, at exactly those commits, in a mirror of its own."""
    want = {}
    for ref in kept_refs:
        for sub_path, (url, sha) in _submodules_at(path, ref).items():
            want.setdefault(sub_path, {"url": url, "shas": set()})["shas"].add(sha)
    depth = ["--depth", "1"] if m["history"] == "shallow" else []
    out = {}
    for sub_path, w in sorted(want.items()):
        u = urllib.parse.urlsplit(w["url"])
        # https only (file:// in tests, where the "internet" is a folder).
        if (
            (u.scheme != "https" and not (u.scheme == "file" and os.environ.get("HUB_MIRRORS_TEST_FILE")))
            or u.username
            or u.password
        ):
            log(f"mirror {m['name']}: submodule {sub_path} is not an https URL ({w['url']}); left out")
            continue
        name = _sub_name(m, sub_path)
        spath = _ensure_repo(
            m,
            gitrepos.ROOT / m["area"] / f"{name}.git",
            w["url"],
            f"Submodule {sub_path} of mirror {m['name']}, a mirror",
        )
        have = {r.rsplit("/", 1)[-1] for r in _local_refs(spath) if r.startswith("refs/kept/")}
        for sha in sorted(w["shas"] - have):
            log(f"mirror {m['name']}: submodule {sub_path} at {sha[:7]}")
            r = _git(
                "fetch",
                "--quiet",
                "--no-tags",
                "--no-write-fetch-head",
                *depth,
                w["url"],
                f"+{sha}:refs/kept/{sha}",
                cwd=spath,
                timeout=FETCH_TIMEOUT,
            )
            if r.returncode != 0:
                raise LibrarianError(
                    M(
                        "mirrors.submodule.fetch-failed",
                        "Download failed for submodule {path} at {commit}: {output}",
                        path=sub_path,
                        commit=sha[:7],
                        output=(r.stderr.strip().splitlines() or ["git fetch failed"])[-1][:200],
                    )
                )
        stale = have - w["shas"]
        for sha in stale:
            _git("update-ref", "-d", f"refs/kept/{sha}", cwd=spath)
        if stale:
            _git("reflog", "expire", "--expire=now", "--all", cwd=spath)
            _git("gc", "--quiet", "--prune=now", cwd=spath, timeout=FETCH_TIMEOUT)
        out[sub_path] = {"repo": name, "url": w["url"], "kept": sorted(w["shas"]), "size": gitrepos._size(spath)}
    return out


def write_urls():
    """mirror-urls.json: each mirrored upstream URL (with and without .git) -> its local repository."""
    st, urls = status(), {}
    for m in load():
        pairs = [(m["upstream"], repo_path(m))]
        pairs += [
            (s["url"], gitrepos.ROOT / m["area"] / f"{s['repo']}.git")
            for s in st.get(m["name"], {}).get("submodules", {}).values()
        ]
        for url, local in pairs:
            if (local / "HEAD").exists():
                base = url[:-4] if url.endswith(".git") else url
                for u in (base, base + ".git"):
                    urls[u] = f"file://{local}"
    _write(URLS, urls)
    return urls


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
        keep = wanted(m, refs, _releases(m, entry))
        path = repo_path(m)
        have = _local_refs(path) if (path / "HEAD").exists() else {}
        missing = {r: sha for r, sha in keep.items() if have.get(r) != sha}
        extra = [r for r in have if r not in keep]
        entry.update(wanted=sorted(keep), missing=sorted(missing), extra=sorted(extra))
        if check_only:
            line = f"{len(missing)} to download, {len(extra)} to remove" if missing or extra else "up to date"
        else:
            path = _ensure_repo(m)
            depth = ["--depth", "1"] if m["history"] == "shallow" else []
            for ref, sha in sorted(missing.items()):
                # By name where the upstream has it under that name, by commit for a pin.
                src = ref if refs.get(ref) == sha else sha
                log(f"mirror {m['name']}: fetching {ref}")
                out = _git(
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    "--no-write-fetch-head",
                    *depth,
                    m["upstream"],
                    f"+{src}:{ref}",
                    cwd=path,
                    timeout=FETCH_TIMEOUT,
                )
                if out.returncode != 0:
                    raise LibrarianError(
                        M(
                            "mirrors.ref.fetch-failed",
                            "Download failed for {ref}: {output}",
                            ref=ref,
                            output=(out.stderr.strip().splitlines() or ["git fetch failed"])[-1][:200],
                        )
                    )
            for ref in extra:
                _git("update-ref", "-d", ref, cwd=path)
            if extra:
                _git("reflog", "expire", "--expire=now", "--all", cwd=path)
                _git("gc", "--quiet", "--prune=now", cwd=path, timeout=FETCH_TIMEOUT)
            shown = description_of(m, path)
            (path / "description").write_text(shown + "\n")
            entry["described"] = shown
            entry.update(missing=[], extra=[], updated=time.time())
            line = f"{len(missing)} downloaded, {len(extra)} removed" if missing or extra else "up to date"
            try:
                tags = _sync_tags(m, refs)
                was = entry.get("tags", {}).get("moved", [])
                entry["tags"] = dict(tags, moved=sorted(set(was) | set(tags["moved"])), checked=time.time())
                if tags["moved"]:
                    line += f". Upstream tag moved since the last check: {', '.join(tags['moved'][:5])}"
                entry.pop("tags_error", None)
            except (LibrarianError, OSError, subprocess.SubprocessError) as exc:
                entry["tags_error"] = str(exc)
            if m.get("submodules"):
                entry["submodules"] = _sync_submodules(m, path, sorted(keep), log)
                line += f". {len(entry['submodules'])} submodule{'s' if len(entry['submodules']) != 1 else ''} mirrored"
            else:
                entry.pop("submodules", None)
        if (path / "HEAD").exists():
            size = gitrepos._size(path) + sum(s.get("size", 0) for s in entry.get("submodules", {}).values())
            entry["size"] = size
            over = size > m["budget_mb"] << 20
            entry["over_budget"] = over
            if over:
                line += f". {size >> 20} MB exceeds the {m['budget_mb']} MB limit"
        entry.pop("error", None)
        entry.pop("error_message", None)
    except (LibrarianError, OSError, subprocess.SubprocessError) as exc:
        entry["error"] = str(exc)
        if is_message(getattr(exc, "message", None)):
            entry["error_message"] = exc.message
        else:
            entry.pop("error_message", None)
        line = f"error: {exc}"
    entry["outcome"] = line
    _write(STATUS, st)
    if not check_only:
        try:
            write_urls()
        except OSError as exc:
            line = f"{line}. Mirror updated, but the build-mirror list was not written: {exc}"
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
