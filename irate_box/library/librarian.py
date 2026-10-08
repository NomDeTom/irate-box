#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The hub's librarian: keeps ZIM books in the Kiwix library up to date from their sources.

A source says where new versions of one book come from:

  release       a project's GitHub Releases: the newest release with an asset matching
                `pattern` (default *.zim). No token. The primary source, for a project that
                publishes its docs as ZIMs.
  actions       a GitHub Actions workflow's run artifacts (a fork building docs in CI):
                the newest successful run of `workflow`, optionally on `branch`, with an
                artifact matching `pattern`. Downloading artifacts needs a GitHub token.
  nightly-link  the same artifacts through nightly.link, which serves public artifacts
                without a token. For anyone running the hub without a GitHub account.
  url           a plain URL to a .zim (or a .zip holding one), versioned by its ETag or
                Last-Modified.

It also keeps the hub's web apps current (kind "app": the apps with a "source" in apps.d/),
from the forks' irate-box-bundle.yml artifacts or a git repository -- see "app bundles" below.
For a git app whose manifest pins a commit (its last known good), it tracks two versions, the
newest and the pinned one, and installs the one the source follows ("pinned" by default).

Each book keeps its file name, <name>.zim, across versions, so /wiki/content/<name>/ links
never change. A new version is downloaded beside the old one, checked (free space, the ZIM
magic number), and renamed over it; the previous one is hard-linked into the archive first,
so the book is never missing. kiwix-serve runs with --monitorLibrary, so rebuilding
library.xml is all it takes for the new version to be served -- no restart, no root.

State, all under $HUB_STATE_DIR/library: sources.json (sources and policy), status.json
(what is installed, last check, last error), archive/<name>/ (old versions, pruned to
policy.keep_old), github-token (optional, for actions), lock.

    librarian.py status
    librarian.py update [NAME ...]        check sources and install anything newer
    librarian.py update --scheduled       the timer: only sources whose check is due
    librarian.py check [NAME ...]         report the newest version without downloading
    librarian.py rebuild-library          library.xml again from the .zim files present (a book added by hand)
    librarian.py fetch [NAME ...]         download and check anything newer, ready to update
    librarian.py add --name N --type T [--repo R] [--workflow W] [--branch B]
                     [--pattern P] [--url U] [--prerelease]
    librarian.py remove NAME [--delete-book]
    librarian.py rollback NAME [VERSION]  back to an archived version (the newest by default)
    librarian.py policy [--keep-old N] [--check-every-hours H] [--min-free-mb M] [--auto-install 0-2]
                     [--hub-check-every-hours H] [--hub-auto 0-2] [--hub-window-start H] [--hub-window-end H]
    librarian.py hub-update               one step of the hub's own update, as the timer takes (selfupdate.py)
    librarian.py token [TOKEN]            set, or with no argument clear, the GitHub token
    librarian.py add-apps [APP ...]       add the default nightly.link source for each app
    librarian.py app-fetch APP            download and check APP's newest bundle; prints the
                                          zip's path (install.sh then has root unpack it)
    librarian.py firmware [--check]       the firmware mirror for the web flasher (firmware.py)

Stdlib only: it runs on the board's Python with nothing installed.
"""

import argparse
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from irate_box import confine
from irate_box.hub import manifests
from irate_box.library import zimcheck

CHECKOUT = Path(__file__).resolve().parents[2]  # irate_box/library/librarian.py → the checkout
STATE_DIR = Path(os.environ.get("HUB_STATE_DIR", CHECKOUT))
ZIM_DIR = STATE_DIR / "zim"
LIB_DIR = STATE_DIR / "library"
SOURCES_FILE = LIB_DIR / "sources.json"
STATUS_FILE = LIB_DIR / "status.json"
TOKEN_FILE = LIB_DIR / "github-token"
ARCHIVE_DIR = LIB_DIR / "archive"
TMP_DIR = LIB_DIR / "tmp"
LOCK_FILE = LIB_DIR / "lock"
PROGRESS_FILE = LIB_DIR / "progress.json"  # the download in flight, for /admin
LIBRARY_XML = ZIM_DIR / "library.xml"

TYPES = ("release", "actions", "nightly-link", "url", "kiwix")
DEFAULT_POLICY = {
    "keep_old": 1,            # archived previous versions per book; 0 deletes them
    "check_every_hours": 24,  # for the timer; 0 means only when asked
    "min_free_mb": 512,       # never let a download leave less than this free
    "books_budget_mb": 0,     # all the books together at most this (0: no cap, only min_free_mb)
    # What a scheduled check does with something newer, for books and apps: 0 only notes it,
    # 1 also downloads and checks it (ready to update), 2 also puts it in use.
    "auto_install": 2,
    # The hub's own updates (selfupdate.py): how often to check (0: only when asked on /admin),
    # and how far to go alone: 0 check only, 1 also fetch and verify, 2 also install, inside
    # the window (the box's local hours, start to end) with nobody on the hub.
    "hub_check_every_hours": 24,
    "hub_auto": 0,
    "hub_window_start": 2,
    "hub_window_end": 5,
}
POLICY_MAX = {"auto_install": 2, "hub_auto": 2, "hub_window_start": 23, "hub_window_end": 23, "books_budget_mb": 10 ** 7}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
API = "https://api.github.com"
USER_AGENT = "irate-box-librarian"
CHUNK = 1 << 20


class LibrarianError(Exception):
    """A failure worth showing the operator as-is."""


# --- config and status -------------------------------------------------------

def _read_json(path, default):
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_config():
    cfg = _read_json(SOURCES_FILE, {})
    policy = dict(DEFAULT_POLICY)
    for key, value in (cfg.get("policy") or {}).items():
        if key in DEFAULT_POLICY and isinstance(value, int) and 0 <= value <= POLICY_MAX.get(key, value):
            policy[key] = value
    sources = [s for s in cfg.get("sources", []) if isinstance(s, dict) and s.get("name")]
    return {"policy": policy, "sources": sources}


def save_config(cfg):
    _write_json(SOURCES_FILE, cfg)


def load_status():
    return _read_json(STATUS_FILE, {})


def save_status(status):
    _write_json(STATUS_FILE, status)


def token():
    try:
        return TOKEN_FILE.read_text().strip() or None
    except OSError:
        return os.environ.get("GITHUB_TOKEN") or None


def set_token(value):
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    if not value:
        TOKEN_FILE.unlink(missing_ok=True)
        return
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value.strip() + "\n")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Lock:
    """One librarian at a time: the timer and the admin page's "Check now" share this."""

    def __enter__(self):
        LIB_DIR.mkdir(parents=True, exist_ok=True)
        self.fh = open(LOCK_FILE, "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise LibrarianError("the librarian is already running")
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def is_running():
    try:
        with Lock():
            return False
    except LibrarianError:
        return True


# --- sources -----------------------------------------------------------------

def validate_source(src):
    """Normalise and check a source definition; raises LibrarianError on anything wrong."""
    out = {"name": str(src.get("name", "")).strip(), "type": str(src.get("type", "")).strip()}
    if src.get("kind") == "app":
        if out["name"] not in APPS or not APPS[out["name"]].get("source"):
            raise LibrarianError(f"app must be one of {', '.join(n for n in APPS if APPS[n].get('source'))}")
        out["kind"] = "app"
        if APPS[out["name"]]["source"]["type"] == "git":
            # A git app comes from the repository its manifest names, and nowhere else: the
            # adapt script runs on whatever is cloned.
            if out["type"] != "git":
                raise LibrarianError(f"{out['name']} comes from git")
            spec = APPS[out["name"]]["source"]
            branch = str(src.get("branch") or spec.get("branch", "main")).strip()
            if not re.match(r"^[A-Za-z0-9_./-]+$", branch) or branch.startswith("-"):
                raise LibrarianError("branch: a branch name")
            follow = str(src.get("follow") or _default_follow(out["name"]))
            if follow not in ("latest", "pinned") or (follow == "pinned" and not spec.get("pin")):
                raise LibrarianError("follow: latest, or pinned for an app whose manifest pins a commit")
            out.update(repo=spec["repo"], branch=branch, follow=follow, enabled=bool(src.get("enabled", True)))
            return out
        if out["type"] not in ("actions", "nightly-link"):
            raise LibrarianError("an app bundle comes from actions or nightly-link")
        # A bundle app comes from the repository, workflow and artifact its manifest names, and
        # nowhere else (F11): a request may choose only the branch. Otherwise a forged request
        # could repoint draw, the flasher or the room relay at someone else's build.
        spec = APPS[out["name"]]["source"]
        branch = str(src.get("branch") or spec.get("branch", "main")).strip()
        if not re.match(r"^[A-Za-z0-9_./-]+$", branch) or branch.startswith("-"):
            raise LibrarianError("branch: a branch name")
        out.update(repo=spec["repo"], workflow=spec["workflow"], pattern=spec["pattern"], branch=branch,
                   enabled=bool(src.get("enabled", True)))
        return out
    if not NAME_RE.match(out["name"]) or out["name"] == "library":
        raise LibrarianError("name: letters, digits, '.', '_' and '-' only (it becomes <name>.zim)")
    if out["type"] not in TYPES:
        raise LibrarianError(f"type must be one of {', '.join(TYPES)}")
    pattern = str(src.get("pattern", "")).strip()
    if out["type"] == "kiwix":
        # A book from Kiwix's catalogue (library.kiwix.org), kept current by its catalogue name:
        # each release has a new file name, so a plain url source would never move on.
        kname = str(src.get("kiwix_name", "")).strip()
        if not KIWIX_NAME_RE.match(kname):
            raise LibrarianError("kiwix_name: the book's name in Kiwix's catalogue, e.g. wikipedia_en_top_mini")
        flavour = str(src.get("flavour", "")).strip()
        if flavour and not KIWIX_NAME_RE.match(flavour):
            raise LibrarianError("flavour: letters, digits, '_' and '-'")
        out.update(kiwix_name=kname, flavour=flavour, enabled=bool(src.get("enabled", True)))
        return out
    if out["type"] == "url":
        url = str(src.get("url", "")).strip()
        if urllib.parse.urlparse(url).scheme != "https":
            # A book over plain HTTP can be swapped on the way, and its pages are served on the
            # hub's own origin (F22).
            raise LibrarianError("url must be https://…")
        out["url"] = url
    else:
        repo = str(src.get("repo", "")).strip()
        if not REPO_RE.match(repo):
            raise LibrarianError("repo must be OWNER/REPO")
        out["repo"] = repo
        out["pattern"] = pattern or ("*.zim" if out["type"] == "release" else "*")
    if out["type"] == "release":
        out["prerelease"] = bool(src.get("prerelease"))
    if out["type"] in ("actions", "nightly-link"):
        workflow = str(src.get("workflow", "")).strip()
        if not re.match(r"^[A-Za-z0-9_.-]+\.ya?ml$", workflow):
            raise LibrarianError("workflow must be the workflow's file name, e.g. docs-zim.yml")
        out["workflow"] = workflow
        branch = str(src.get("branch", "")).strip()
        if branch:
            out["branch"] = branch
    out["enabled"] = bool(src.get("enabled", True))
    return out


# --- app bundles -------------------------------------------------------------
# The prebuilt web apps are kept current like books. Which apps there are, where each comes
# from and what proves a bundle whole are in its manifest (apps.d/, manifests.py):
#   bundle  a fork's irate-box-bundle.yml artifact, through nightly.link or with a token;
#   git     a repository, cloned, run through the manifest's adapt script (adapt_tools.py
#           for the calculators), and packed the same way.
# The librarian fetches and checks a bundle as the hub user; installing it under
# /usr/share/hub needs root, so it hands the checked zip to hub_control.py (app-install),
# which checks it again and swaps it in, keeping the previous copy for a roll back.
# Each bundle carries irate-box-bundle.json: {"app", "repository", "ref", "commit", "built", "run"?}.
#
# Latest and pinned: a git app's manifest may pin a commit ("source": {"pin": SHA}), the last
# one its maintainers looked at and know to work (for ELIZA: third-party code and scripts that
# visitors are served, and an adapt script that patches the page). Every check then records
# both, "latest" (the branch head) and "pinned", in the app's status. Its source follows one:
#   pinned  (the default when there is a pin) installs the pinned commit; a newer one is only
#           reported, until the pin moves with the hub's code or the owner follows latest;
#   latest  installs the branch head; if that does not fetch, adapt or check, the box keeps
#           the build it has, or with none, installs the pinned one.
# Whichever it is, the commit is fetched by its hash, so what is installed is what was checked.

APP_STAGING = LIB_DIR / "apps"
APP_MAX_BYTES = 400 << 20  # unpacked; the largest bundle (draw) is ~25 MB
# The built-in apps and the local add-ons (manifests.py), which the owner adds and removes from
# /admin while the hub runs: reload_apps() reads them again.
APPS = manifests.installable(manifests.load_all())


def reload_apps():
    global APPS
    APPS = manifests.installable(manifests.load_all())
    return APPS
BUNDLE_JSON = "irate-box-bundle.json"


def app_dir(name):
    return manifests.install_dir(APPS[name])


def _default_follow(name):
    return "pinned" if APPS[name]["source"].get("pin") else "latest"


def _follow(src):
    """What a git app source installs: "latest" or "pinned" (a source saved before pins
    existed has no "follow", and gets its manifest's default)."""
    pin = APPS.get(src["name"], {}).get("source", {}).get("pin")
    follow = src.get("follow") or (_default_follow(src["name"]) if src["name"] in APPS else "latest")
    return follow if pin or follow == "latest" else "latest"


def _pinned_candidate(src):
    sha = APPS[src["name"]]["source"]["pin"]
    return {"version": f"git-{sha}", "url": src["repo"], "size": 0, "zip": False, "commit": sha,
            "label": f"pinned {sha[:7]}", "auth": None, "pinned": True}


def default_app_source(name, via="nightly-link"):
    spec = APPS[name]["source"]
    if spec["type"] == "git":
        return {"name": name, "kind": "app", "type": "git", "repo": spec["repo"], "branch": spec.get("branch", "main"),
                "follow": _default_follow(name)}
    return {"name": name, "kind": "app", "type": via, "repo": spec["repo"], "workflow": spec["workflow"],
            "branch": spec.get("branch", ""), "pattern": spec.get("pattern", f"irate-box-{name}-*")}


def installed_app(name):
    """The installed bundle's irate-box-bundle.json, {} for a hand-copied build, None if absent."""
    folder = app_dir(name)
    if not folder.is_dir():
        return None
    meta = _read_json(folder / BUNDLE_JSON, {})
    meta["has_previous"] = (folder.parent / f".{folder.name}.prev").is_dir()
    return meta


def _has_needed(names, pattern):
    if any(c in pattern for c in "*?["):
        return any(fnmatch.fnmatchcase(n, pattern) for n in names)
    return pattern in names


def check_bundle(zip_path, name):
    """Raise unless zip_path is a bundle of app `name` that is safe to unpack: no absolute
    paths, no '..', no symlinks, a sane size, its marker file and what its manifest needs.
    hub_control.py runs the same check again as root before unpacking."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            total = 0
            names = set()
            for info in zf.infolist():
                parts = Path(info.filename).parts
                if info.filename.startswith(("/", "\\")) or ".." in parts or ":" in info.filename:
                    raise LibrarianError(f"unsafe path in the bundle: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise LibrarianError(f"symlink in the bundle: {info.filename}")
                total += info.file_size
                names.add(info.filename)
            if total > APP_MAX_BYTES:
                raise LibrarianError(f"the bundle unpacks to {total >> 20} MB, over the {APP_MAX_BYTES >> 20} MB limit")
            if BUNDLE_JSON not in names:
                raise LibrarianError(f"not an Irate-Box bundle: no {BUNDLE_JSON}")
            meta = json.loads(zf.read(BUNDLE_JSON))
            if meta.get("app") != name:
                raise LibrarianError(f"this is a bundle of {meta.get('app')!r}, not {name}")
            needs = APPS[name]["install"]["needs"]
            if not _has_needed(names, needs):
                raise LibrarianError(f"the bundle has no {needs}")
            return meta
    except zipfile.BadZipFile:
        raise LibrarianError("the download is not a valid zip")
    except (ValueError, KeyError) as exc:
        raise LibrarianError(f"the bundle's {BUNDLE_JSON} is unreadable: {exc}")


def _queue_root(req):
    """Hand a request to hub_control.py, as server.py's control_request does: written in the
    hub's own folder and renamed in. Not in control/, which is root's (F3): writing there failed
    every app update that needed root, from the timer and from /admin alike (found 2026-10-06)."""
    requests = STATE_DIR / "control" / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    rid = os.urandom(8).hex()
    tmp = STATE_DIR / f".request-{rid}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(dict(req, id=rid), fh)
    os.replace(tmp, requests / f"{rid}.json")
    return rid


def _git(*args, timeout=300):
    out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        lines = (out.stderr or out.stdout).strip().splitlines()
        what = args[2] if args[0] == "-C" and len(args) > 2 else args[0]
        raise LibrarianError(f"git {what}: {lines[-1] if lines else 'failed'}")
    return out.stdout


def _resolve_git(src, auth):
    """The branch head of a git source, without cloning."""
    branch = src.get("branch", "main")
    head = _git("ls-remote", src["repo"], f"refs/heads/{branch}", timeout=60).split()
    if not head:
        raise LibrarianError(f"{src['repo']} has no branch {branch}")
    sha = head[0]
    return {"version": f"git-{sha}", "url": src["repo"], "size": 0, "zip": False, "commit": sha,
            "label": f"{branch} at {sha[:7]}", "auth": None}


def _pack_git(src, cand, part):
    """Clone, adapt, and zip a git source as a bundle at `part`."""
    name = src["name"]
    work = Path(tempfile.mkdtemp(prefix=f"{name}-", dir=APP_STAGING))
    try:
        tree = work / "tree"
        want = cand.get("commit")
        if want:  # by its hash: the commit that was resolved (or pinned), not whatever is newest now
            _git("init", "-q", str(tree))
            _git("-C", str(tree), "fetch", "-q", "--depth", "1", src["repo"], want)
            _git("-C", str(tree), "checkout", "-q", "FETCH_HEAD")
        else:
            _git("clone", "-q", "--depth", "1", "--branch", src.get("branch", "main"), src["repo"], str(tree))
        commit = _git("-C", str(tree), "rev-parse", "HEAD").strip()
        if want and commit != want:
            raise LibrarianError(f"asked {src['repo']} for {want[:7]} and got {commit[:7]}")
        shutil.rmtree(tree / ".git")
        # No links from the repository survive (F30): the adapt script and the zip below would
        # follow them, writing or packing files of the hub's own.
        for path in sorted(tree.rglob("*"), reverse=True):
            if path.is_symlink():
                path.unlink()
        adapt = APPS[name]["source"].get("adapt")
        if adapt:
            out = subprocess.run([str(CHECKOUT / "irate-box"), Path(adapt).stem, str(tree), str(CHECKOUT / "web")],
                                 capture_output=True, text=True, timeout=300)
            if out.returncode != 0:
                raise LibrarianError(f"{adapt} failed: {(out.stderr or out.stdout).strip()[-300:]}")
        (tree / BUNDLE_JSON).write_text(json.dumps({
            "app": name, "repository": src["repo"], "ref": "pinned" if cand.get("pinned") else src.get("branch", "main"),
            "commit": commit, "built": now_iso(), "source": "git"}))
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(tree.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    zf.write(path, path.relative_to(tree).as_posix())
    finally:
        shutil.rmtree(work, ignore_errors=True)


def fetch_app(src, cand):
    """Download (or clone and pack) and check a bundle into APP_STAGING; (path, meta)."""
    name = src["name"]
    APP_STAGING.mkdir(parents=True, exist_ok=True)
    need = cand["size"] * 3 + 64 * 2**20
    if _free_bytes(APP_STAGING) < need:
        raise LibrarianError(f"not enough space for the {name} bundle")
    dest = APP_STAGING / f"{name}.zip"
    part = APP_STAGING / f"{name}.zip.download"
    try:
        if src["type"] == "git":
            _pack_git(src, cand, part)
        else:
            _download(cand["url"], part, cand["auth"], name, cand["size"])
        meta = check_bundle(part, name)
        os.replace(part, dest)
    finally:
        part.unlink(missing_ok=True)
    return dest, meta


def _same_build(installed, cand):
    if cand.get("commit"):  # git: the commit is the version
        return installed.get("commit") == cand["commit"]
    run_id = cand["version"].split("/")[0].removeprefix("run-")
    return str(installed.get("run")) == run_id


def update_app(src, cand, entry, mode):
    """One app source's part of update(): compare builds; for "fetch", download and check
    the bundle into APP_STAGING; for "update", also queue it for the root helper (using the
    fetched one when it is still the newest)."""
    installed = installed_app(src["name"])
    meta = installed or {}
    entry["current"] = {
        "version": f"run-{meta['run']}" if meta.get("run") else (f"git-{meta['commit']}" if meta.get("commit") else None),
        "label": (f"{meta['commit'][:7]} ({meta.get('ref')}, built {str(meta.get('built', ''))[:10]})"
                  if meta.get("commit") else "a build copied in by hand" if installed is not None else "not installed")}
    staged = APP_STAGING / f"{src['name']}.zip"
    if _same_build(meta, cand):
        entry.pop("fetched", None)
        return f"up to date: {cand['label']}"
    fetched = entry.get("fetched") or {}
    if not (fetched.get("version") == cand["version"] and staged.exists()):
        if mode == "check":
            return f"newer available: {cand['label']}"
        path, bundle = fetch_app(src, cand)
        fetched = entry["fetched"] = {"version": cand["version"], "label": cand["label"],
                                      "commit": str(bundle.get("commit", ""))[:7]}
    if mode != "update":
        return f"fetched {fetched['commit']} ({cand['label']}); ready to update"
    if APPS[src["name"]].get("local"):
        # A local add-on is the hub's own to install: no root helper.
        message = install_local(src["name"], staged)
        entry.pop("pending", None)
        entry["install_result"] = {"ok": True, "message": message, "at": now_iso()}
        entry.pop("fetched", None)
        return f"installed {fetched['commit']} ({cand['label']})"
    rid = _queue_root({"action": "app-install", "app": src["name"], "zip": str(staged)})
    entry["pending"] = rid
    entry.pop("install_result", None)
    entry.pop("fetched", None)
    return f"installing {fetched['commit']} ({cand['label']})"


def install_local(name, zip_path):
    """A local add-on's bundle, unpacked into its folder in $HUB_STATE_DIR/addons as the hub user
    (the root helper's install_app, without root: the folder is the hub's, served by the add-on
    origin). Checked as every bundle is: no links, no absolute or '..' paths, its size, what it
    needs. The previous copy is kept beside it as .<name>.prev, which the add-on origin never
    serves (it serves only /<id>/, and ids start with a letter or digit)."""
    meta = check_bundle(zip_path, name)
    target = app_dir(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    new = target.parent / f".{name}.new"
    prev = target.parent / f".{name}.prev"
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(mode=0o755)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(new)
        for root, _dirs, files in os.walk(new):
            os.chmod(root, 0o755)
            for f in files:
                os.chmod(os.path.join(root, f), 0o644)
        if target.exists():
            shutil.rmtree(prev, ignore_errors=True)
            os.rename(target, prev)
        os.rename(new, target)
    finally:
        shutil.rmtree(new, ignore_errors=True)
    Path(zip_path).unlink(missing_ok=True)
    return f"{name}: installed {str(meta.get('commit', ''))[:7]} ({meta.get('ref')}, built {str(meta.get('built', ''))[:10]})"


def rollback_local(name):
    """A local add-on back to its previous copy (the current one becomes the previous)."""
    target = app_dir(name)
    prev = target.parent / f".{name}.prev"
    if not prev.is_dir():
        raise LibrarianError(f"no previous {name} to go back to")
    swap = target.parent / f".{name}.swap"
    shutil.rmtree(swap, ignore_errors=True)
    if target.exists():
        os.rename(target, swap)
    os.rename(prev, target)
    if swap.exists():
        os.rename(swap, prev)
    return f"{name}: back to the previous copy"


def update_app_source(src, cand, entry, mode, latest_error=None):
    """An app source's part of update(): update_app() on the build the source follows. cand is
    the newest (None if it could not be resolved: latest_error says why). With a pin, the
    status records both, and following latest falls back as the comment above says."""
    pin = APPS[src["name"]]["source"].get("pin") if src["type"] == "git" else None
    if not pin:
        if cand is None:
            raise LibrarianError(latest_error)
        return update_app(src, cand, entry, mode)
    pinned = _pinned_candidate(src)
    entry["pinned"] = {"version": pinned["version"], "label": pinned["label"]}
    entry["follow"] = _follow(src)
    if entry["follow"] == "pinned":
        out = update_app(src, pinned, entry, mode)
        if cand is None:
            return f"{out}; the newest is not known ({latest_error})"
        return out if cand["commit"] == pin else f"{out}; newer, not followed: {cand['label']}"
    if cand is not None:
        try:
            out = update_app(src, cand, entry, mode)
            entry.pop("latest_failed", None)
            return out
        except LibrarianError as exc:
            entry["latest_failed"] = {"version": cand["version"], "label": cand["label"], "error": str(exc)}
            why = f"the newest ({cand['label']}) failed: {exc}"
    else:
        why = f"the newest is not known ({latest_error})"
    inst = installed_app(src["name"])
    if inst and inst.get("commit"):
        return f"{why}; keeping {inst['commit'][:7]}"
    return f"{why}; {update_app(src, pinned, entry, mode)}"


def fetch_app_source(src):
    """install.sh's first install (app-fetch): the build the source follows, or the pinned one
    when following latest and the newest does not fetch, adapt or check."""
    pin = APPS[src["name"]]["source"].get("pin") if src["type"] == "git" else None
    if not pin:
        return fetch_app(src, resolve(src))
    if _follow(src) == "latest":
        try:
            return fetch_app(src, resolve(src))
        except LibrarianError as exc:
            print(f"{src['name']}: the newest failed ({exc}); using the pinned {pin[:7]}", file=sys.stderr)
    return fetch_app(src, _pinned_candidate(src))


def default_apps():
    """Apps kept current by "Keep all apps current": the core ones, and any already installed."""
    return [n for n, m in APPS.items() if m.get("source") and (m.get("core") or app_dir(n).is_dir())]


def apps_snapshot():
    return {name: {"title": m["install"].get("title", name), "installed": installed_app(name),
                   "source_type": m.get("source", {}).get("type"), "pin": m.get("source", {}).get("pin")}
            for name, m in APPS.items() if m.get("source")}


def add_source(src):
    src = validate_source(src)
    cfg = load_config()
    cfg["sources"] = [s for s in cfg["sources"] if s["name"] != src["name"]] + [src]
    save_config(cfg)
    return src


def remove_source(name, delete_book=False):
    cfg = load_config()
    if not any(s["name"] == name for s in cfg["sources"]):
        raise LibrarianError(f"no source named {name}")
    cfg["sources"] = [s for s in cfg["sources"] if s["name"] != name]
    save_config(cfg)
    status = load_status()
    status.pop(name, None)
    save_status(status)
    staged_book(name).unlink(missing_ok=True)
    confine.under(APP_STAGING, f"{name}.zip").unlink(missing_ok=True)
    if delete_book and name not in APPS:
        with Lock():
            confine.under(ZIM_DIR, f"{name}.zim").unlink(missing_ok=True)
            shutil.rmtree(confine.under(ARCHIVE_DIR, name), ignore_errors=True)
            library_drop(f"{name}.zim")


def set_policy(**changes):
    cfg = load_config()
    for key, value in changes.items():
        if value is None:
            continue
        if key not in DEFAULT_POLICY or not isinstance(value, int) or not 0 <= value <= POLICY_MAX.get(key, value):
            raise LibrarianError(f"bad policy value: {key}={value!r}")
        cfg["policy"][key] = value
    save_config(cfg)
    return cfg["policy"]


# --- HTTP --------------------------------------------------------------------

def _request(url, auth=None, method="GET", extra=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json", **(extra or {})}
    req = urllib.request.Request(url, headers=headers, method=method)
    if auth and urllib.parse.urlparse(url).hostname == "api.github.com":
        # Not carried on a redirect (F28): an artifact download answers with a 302 to GitHub's
        # blob storage, and urllib would hand the token to that host as well.
        req.add_unredirected_header("Authorization", f"Bearer {auth}")
    return req


class _NotModified(Exception):
    """A conditional request's 304: what was kept is still current."""

    def __init__(self, headers):
        super().__init__("304")
        self.headers = headers


def _open(url, auth=None, method="GET", timeout=60, extra=None):
    try:
        return urllib.request.urlopen(_request(url, auth, method, extra), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and (extra or {}).get("If-None-Match"):
            raise _NotModified(exc.headers)
        if exc.headers is not None:
            _note_rate(exc.headers)
        if exc.code == 401:
            raise LibrarianError(f"{url}: 401, a GitHub token is needed (or a valid one)")
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            raise LibrarianError("GitHub API rate limit reached (60/hour without a token)")
        raise LibrarianError(f"{url}: HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise LibrarianError(f"cannot reach {urllib.parse.urlparse(url).hostname}: {reason}")


# GitHub's API at hundreds of sources (next-work plan step 14). Without a token it allows 60 requests
# an hour, from this address. So: one request per address per run (books sharing a repository share
# its listing), the answer's ETag kept and sent back (a 304 saves the download always, and the
# hourly allowance only with a token: tried 2026-10-07, without one each 304 still counted), and
# what is left of the allowance recorded, so a scheduled run stops checking before it runs out and
# leaves the rest for the next one (update(), RESERVE).
API_CACHE = LIB_DIR / "api-cache"   # one file per address: {url, etag, body, at}
API_CACHE_MAX = 400
RATE_FILE = LIB_DIR / "github-rate.json"
RESERVE = 10
MEMO_SECONDS = 600  # the same address asked twice within this: answered from memory
_memo = {}          # path -> (when, body)
_rate = {}


def _note_rate(headers):
    try:
        _rate.update(limit=int(headers["X-RateLimit-Limit"]), remaining=int(headers["X-RateLimit-Remaining"]),
                     reset=int(headers["X-RateLimit-Reset"]), at=int(time.time()))
    except (KeyError, TypeError, ValueError):
        pass


def rate():
    """What is left of GitHub's hourly allowance, as last seen (by this process or the last run)."""
    return dict(_rate) or _read_json(RATE_FILE, {})


def _api(path, auth=None):
    got = _memo.get(path)
    if got and time.time() - got[0] < MEMO_SECONDS:
        return got[1]
    url = f"{API}{path}"
    kept = API_CACHE / f"{hashlib.sha1(url.encode()).hexdigest()}.json"
    hit = _read_json(kept, {})
    hit = hit if hit.get("url") == url and hit.get("etag") else None
    try:
        with _open(url, auth, extra={"If-None-Match": hit["etag"]} if hit else None) as resp:
            _note_rate(resp.headers)
            body = json.load(resp)
            etag = resp.headers.get("ETag")
    except _NotModified as nm:
        _note_rate(nm.headers)
        body, etag = hit["body"], hit["etag"]
    if etag:
        API_CACHE.mkdir(parents=True, exist_ok=True)
        _write_json(kept, {"url": url, "etag": etag, "body": body, "at": int(time.time())})
        files = sorted(API_CACHE.glob("*.json"), key=lambda f: f.stat().st_mtime)
        for old in files[:max(0, len(files) - API_CACHE_MAX)]:
            old.unlink(missing_ok=True)
    _memo[path] = (time.time(), body)
    return body


def revoked(rel):
    """A GitHub release its project has withdrawn. Meshtastic marks one in its name only
    ("… Alpha (Revoked)"); GitHub has no flag for it. Shared by firmware.py and mirrors.py."""
    return "revoked" in (rel.get("name") or "").lower()


# --- resolving the newest version ---------------------------------------------
# Each returns {"version", "url", "size", "zip", "label", "auth"}, or raises.

def _resolve_release(src, auth):
    releases = _api(f"/repos/{src['repo']}/releases?per_page=20", auth)
    for rel in releases:
        if rel.get("draft") or (rel.get("prerelease") and not src.get("prerelease")):
            continue
        for asset in rel.get("assets", []):
            if fnmatch.fnmatch(asset["name"], src["pattern"]):
                return {
                    "version": f"{rel['tag_name']}/{asset['id']}",
                    "url": asset["browser_download_url"],
                    "size": asset.get("size") or 0,
                    "zip": asset["name"].lower().endswith(".zip"),
                    "label": f"release {rel['tag_name']} ({asset['name']})",
                    "auth": None,
                }
    raise LibrarianError(f"no release of {src['repo']} has an asset matching {src['pattern']}")


# Which runs' artifacts a book or an app may come from (F30): a push to the repository, a run
# started by hand (workflow_dispatch) or on the workflow's own schedule. All three need write
# access to the repository, so they build what its owner has taken. Never a pull request's run,
# nor one from a fork. (Pushes only, until mermaid-docs' run started by hand on develop, the only
# one there, was refused: 2026-10-07.)
TRUSTED_EVENTS = ("push", "workflow_dispatch", "schedule")


def _newest_artifact(src, auth):
    query = "status=success&per_page=30"
    if src.get("branch"):
        query += "&branch=" + urllib.parse.quote(src["branch"], safe="")
    runs = _api(f"/repos/{src['repo']}/actions/workflows/{src['workflow']}/runs?{query}", auth)
    passed_over = 0
    for run in runs.get("workflow_runs", []):
        if run.get("event", "push") not in TRUSTED_EVENTS or \
                (run.get("head_repository") or {}).get("full_name", src["repo"]).lower() != src["repo"].lower():
            passed_over += 1
            continue
        arts = _api(f"/repos/{src['repo']}/actions/runs/{run['id']}/artifacts", auth)
        for art in arts.get("artifacts", []):
            if not art.get("expired") and fnmatch.fnmatch(art["name"], src["pattern"]):
                return run, art
    where = f" on {src['branch']}" if src.get("branch") else ""
    raise LibrarianError(
        f"no successful {src['workflow']} run{where} in {src['repo']} has an unexpired "
        f"artifact matching {src['pattern']} (artifacts expire after 90 days)"
        + (f"; {passed_over} run{'s' if passed_over != 1 else ''} from a pull request or a fork passed over" if passed_over else ""))


def _resolve_actions(src, auth):
    if not auth:
        raise LibrarianError("actions sources need a GitHub token; or use nightly-link instead")
    run, art = _newest_artifact(src, auth)
    return {
        "version": f"run-{run['id']}/{art['id']}",
        "url": art["archive_download_url"],
        "size": art.get("size_in_bytes") or 0,
        "zip": True,
        "label": f"run {run['id']} ({run.get('head_branch')}, {run.get('created_at', '')[:10]}), artifact {art['name']}",
        "auth": auth,
    }


def _resolve_nightly(src, auth):
    run, art = _newest_artifact(src, auth)
    name = urllib.parse.quote(art["name"], safe="")
    return {
        "version": f"run-{run['id']}/{art['id']}",
        "url": f"https://nightly.link/{src['repo']}/actions/runs/{run['id']}/{name}.zip",
        "size": art.get("size_in_bytes") or 0,
        "zip": True,
        "label": f"run {run['id']} ({run.get('head_branch')}, {run.get('created_at', '')[:10]}) via nightly.link",
        "auth": None,
    }


def _resolve_url(src, auth):
    with _open(src["url"], method="HEAD") as resp:
        headers = resp.headers
        final = resp.geturl()
    version = headers.get("ETag") or headers.get("Last-Modified")
    if not version:
        raise LibrarianError(f"{src['url']} sends neither ETag nor Last-Modified, so updates cannot be detected")
    return {
        "version": version.strip('"'),
        "url": src["url"],
        "size": int(headers.get("Content-Length") or 0),
        "zip": urllib.parse.urlparse(final).path.lower().endswith(".zip"),
        "label": src["url"],
        "auth": None,
    }


# --- Kiwix's catalogue (library.kiwix.org, OPDS) ---------------------------------------------------
KIWIX_OPDS = "https://opds.library.kiwix.org/catalog/v2/entries"
KIWIX_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_ATOM = "{http://www.w3.org/2005/Atom}"


def _opds(params):
    """The catalogue's entries for a query: [{name, title, summary, language, category, flavour,
    updated, size, articles, url}] and the total. The download is the .zim the .meta4 names."""
    import xml.etree.ElementTree as ET
    url = f"{KIWIX_OPDS}?{urllib.parse.urlencode(params)}"
    with _open(url, timeout=30) as resp:
        data = resp.read(4 << 20)
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise LibrarianError("Kiwix's catalogue sent something unreadable")
    out = []
    for e in root.findall(f"{_ATOM}entry"):
        g = lambda tag: (e.findtext(f"{_ATOM}{tag}") or "").strip()  # noqa: E731
        link = next((l for l in e.findall(f"{_ATOM}link") if l.get("type") == "application/x-zim"), None)
        href = (link.get("href") or "") if link is not None else ""
        if not href.startswith("https://"):
            continue
        out.append({"name": g("name"), "title": g("title"), "summary": g("summary"), "language": g("language"),
                    "category": g("category"), "flavour": g("flavour"), "updated": g("updated")[:10],
                    "size": int(link.get("length") or 0), "articles": int(g("articleCount") or 0),
                    "url": href[:-len(".meta4")] if href.endswith(".meta4") else href})
    try:
        total = int(root.findtext(f"{_ATOM}totalResults") or len(out))
    except ValueError:
        total = len(out)
    return out, total


def catalogue_search(q="", language="", category="", start=0, count=20):
    """A page of Kiwix's catalogue for the Books page (online only)."""
    params = {"count": max(1, min(int(count), 50)), "start": max(0, int(start))}
    if q:
        params["q"] = q[:100]
    if language:
        params["lang"] = language[:20]
    if category:
        params["category"] = category[:40]
    entries, total = _opds(params)
    return {"entries": entries, "total": total, "start": params["start"]}


def _resolve_kiwix(src, auth):
    entries, _ = _opds({"name": src["kiwix_name"], "count": 20})
    entries = [e for e in entries if e["name"] == src["kiwix_name"] and (e["flavour"] or "") == (src.get("flavour") or "")]
    if not entries:
        raise LibrarianError(f"Kiwix's catalogue has no {src['kiwix_name']}" + (f" ({src['flavour']})" if src.get("flavour") else ""))
    e = max(entries, key=lambda x: x["updated"])
    file = e["url"].rsplit("/", 1)[-1]
    return {"version": file, "url": e["url"], "size": e["size"], "zip": False,
            "label": f"{file} ({e['updated']}, from Kiwix's catalogue)", "auth": None}


RESOLVERS = {"release": _resolve_release, "actions": _resolve_actions, "kiwix": _resolve_kiwix,
             "nightly-link": _resolve_nightly, "url": _resolve_url, "git": _resolve_git}


def resolve(src):
    return RESOLVERS[src["type"]](src, token())


# --- installing --------------------------------------------------------------

def _download(url, dest, auth=None, name=None, expected=0, resume=False):
    """Stream url to dest. While it runs, progress.json says how far it has got (written at
    most once a second), so /admin can show a 60 MB book arriving at hotspot speed. With resume,
    a part already at dest (from a run the hub's restart cut off: the Lyra's 507 MB build cache
    stopped at 498 MB, 2026-10-06) is continued with a range request when the server allows one."""
    started = time.monotonic()
    done, last = 0, 0.0
    have = dest.stat().st_size if resume and expected and dest.is_file() and not dest.is_symlink() else 0
    if not 0 < have < expected:
        have = 0
    try:
        resp = _open(url, auth, timeout=120, extra={"Range": f"bytes={have}-"} if have else None)
        if urllib.parse.urlparse(url).scheme == "https" and urllib.parse.urlparse(getattr(resp, "geturl", lambda: url)()).scheme != "https":
            resp.close()  # a book is never taken over plain HTTP, even on a redirect (F22)
            raise LibrarianError(f"{urllib.parse.urlparse(url).hostname} redirected to plain HTTP: not taken")
        go_on = have and getattr(resp, "status", None) == 206  # a server that ignores Range sends it all (200)
        done = have if go_on else 0
        with resp, open(dest, "ab" if go_on else "wb") as out:
            total = (done + int(resp.headers.get("Content-Length") or 0)) or expected
            while True:
                chunk = resp.read(CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if name and now - last >= 1:
                    last = now
                    _write_json(PROGRESS_FILE, {"name": name, "done": done, "total": total,
                                                "seconds": round(now - started, 1)})
    finally:
        PROGRESS_FILE.unlink(missing_ok=True)


def progress():
    """The download in flight, or None. A file left by a crashed run is ignored."""
    data = _read_json(PROGRESS_FILE, {})
    return data if data and is_running() else None


def _extract_zim(zip_path, name, dest):
    """The .zim inside an artifact or release zip: <name>.zim if present, else the only one."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zims = [i for i in zf.infolist() if i.filename.lower().endswith(".zim")]
            pick = next((i for i in zims if Path(i.filename).name == f"{name}.zim"), None)
            if pick is None:
                if len(zims) != 1:
                    raise LibrarianError(f"the zip holds {len(zims)} .zim files and none is {name}.zim")
                pick = zims[0]
            with zf.open(pick) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out, CHUNK)
    except zipfile.BadZipFile:
        raise LibrarianError("the download is not a valid zip")


def _check_zim(path, what="the download"):
    """A book replaces the one in use only once it is whole and Kiwix can read it (zimcheck)."""
    why = zimcheck.problem(path)
    if why:
        raise LibrarianError(f"{what} was not put in place: it is {why}. The book in use is unchanged.")


def _free_bytes(path):
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free


def _safe(version):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", version)[:80]


# Kiwix's catalogue at hundreds of books (next-work plan step 14). Measured on the Lyra, 2026-10-07:
# one kiwix-manage run per book, each rewriting the whole library.xml, took over 10 minutes for 300
# small books, and ran again in full for every new one. One run takes many books (49 in 1.5 s),
# but a single unreadable one makes it write nothing: so a failing batch is halved until that book
# is alone, and left out (300 books: 18 s). A new or removed book changes only its own entry,
# spliced in as XML (_splice), since kiwix-manage reopens every book a library already lists.
KIWIX_BATCH = 100


def _kiwix(*args):
    manage = shutil.which("kiwix-manage")
    if not manage:
        raise LibrarianError("kiwix-manage is not installed (kiwix-tools)")
    return subprocess.run([manage, *map(str, args)], check=False, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode


def _kiwix_add(lib, zims):
    """Add zims to lib in as few runs as will go. Returns the books left out (unreadable)."""
    if not zims:
        return []
    if _kiwix(lib, "add", *zims) == 0:
        return []
    if len(zims) == 1:
        return list(zims)
    mid = len(zims) // 2
    return _kiwix_add(lib, zims[:mid]) + _kiwix_add(lib, zims[mid:])


def rebuild_library():
    """library.xml from the .zim files actually present, as install.sh does: in batches, the
    unreadable ones left out (returned). kiwix-serve's --monitorLibrary notices the new file."""
    new = ZIM_DIR / "library.xml.new"
    new.unlink(missing_ok=True)
    zims = sorted(ZIM_DIR.glob("*.zim"))
    left = []
    for i in range(0, len(zims), KIWIX_BATCH):
        left += _kiwix_add(new, zims[i:i + KIWIX_BATCH])
    if new.exists():
        os.replace(new, LIBRARY_XML)
    return left


def _splice(drop_name, add_from=None):
    """library.xml with the entries for file `drop_name` taken out and, if given, the <book> entries
    of the scratch library `add_from` put in: edited as XML, on a copy swapped in. Measured on the
    Lyra (2026-10-07, 300 books): kiwix-manage opens every book already in a library on each run,
    so even one `add` took 17 s; describing the new book alone, in an empty library beside the
    real one, and splicing its entry in takes a fraction of that. None if library.xml is unreadable."""
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(LIBRARY_XML)
    except (OSError, ET.ParseError):
        return None
    root = tree.getroot()
    for b in [b for b in root.findall("book") if Path(b.get("path", "")).name == drop_name]:
        root.remove(b)
    if add_from is not None:
        try:
            root.extend(ET.parse(add_from).getroot().findall("book"))
        except (OSError, ET.ParseError):
            return None
    new = ZIM_DIR / "library.xml.new"
    tree.write(new, encoding="utf-8", xml_declaration=True)
    os.replace(new, LIBRARY_XML)
    return True


def library_put(book):
    """One book added or replaced in Kiwix's catalogue: described by kiwix-manage in a library of
    its own (beside the real one, so its path is written the same way), then spliced in.
    Returns [book] if Kiwix can't read it (then left out). A missing or unreadable catalogue is
    rebuilt instead."""
    book = Path(book)
    if not LIBRARY_XML.exists():
        return rebuild_library()
    one = ZIM_DIR / f".{book.stem}.library-one.xml"
    one.unlink(missing_ok=True)
    try:
        left = _kiwix_add(one, [book]) if book.exists() else []
        if _splice(book.name, one if one.exists() else None) is None:
            return rebuild_library()
        return left
    finally:
        one.unlink(missing_ok=True)


def library_drop(filename):
    """A book's entries out of Kiwix's catalogue (its file already gone): no kiwix-manage at all."""
    if not LIBRARY_XML.exists() or _splice(filename) is None:
        return rebuild_library()
    return []


def _prune_archive(name, keep):
    folder = ARCHIVE_DIR / name
    if not folder.is_dir():
        return []
    old = sorted(folder.glob("*.zim"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in old[keep:]:
        path.unlink(missing_ok=True)
    return [p.stem for p in old[:keep]]


def staged_book(name):
    """A fetched version waiting to be swapped in: beside the book, on the same filesystem
    for an atomic rename, and not named *.zim, so Kiwix never lists it."""
    return confine.under(ZIM_DIR, f".{name}.zim.fetched")


def books_bytes():
    return sum(p.stat().st_size for p in ZIM_DIR.glob("*.zim")) if ZIM_DIR.is_dir() else 0


def over_budget(name, size, policy):
    """Why a book of `size` bytes would not fit the books' budget (it replaces <name>.zim), or None.
    Says which books are largest: what the owner would remove first to make room."""
    budget = policy.get("books_budget_mb", 0) << 20
    if not budget or not size:
        return None
    current = ZIM_DIR / f"{name}.zim"
    after = books_bytes() - (current.stat().st_size if current.exists() else 0) + size
    if after <= budget:
        return None
    largest = sorted(ZIM_DIR.glob("*.zim"), key=lambda p: p.stat().st_size, reverse=True)[:3]
    return (f"over the books' budget: it would make {after >> 20} MB of {budget >> 20} MB. Largest now: "
            + ", ".join(f"{p.stem} ({p.stat().st_size >> 20} MB)" for p in largest))


def fetch_book(src, cand, policy, status_entry):
    """Download cand and check it into staged_book(); readers see no change until install."""
    name = src["name"]
    need = cand["size"] * (2 if cand["zip"] else 1) + policy["min_free_mb"] * 2**20
    free = _free_bytes(ZIM_DIR)
    if cand["size"] and free < need:
        raise LibrarianError(f"not enough space: {free >> 20} MB free, {need >> 20} MB needed")
    over = over_budget(name, cand["size"], policy)
    if over:
        raise LibrarianError(over)

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    ZIM_DIR.mkdir(parents=True, exist_ok=True)
    part = TMP_DIR / f"{name}.download"
    zim_tmp = ZIM_DIR / f".{name}.zim.new"
    archived = ARCHIVE_DIR / name / f"{_safe(cand['version'])}.zim"
    try:
        if archived.exists():
            # Rolled back earlier, now rolling forward: the version is already on the card.
            shutil.copyfile(archived, zim_tmp)
        elif cand["zip"]:
            _download(cand["url"], part, cand["auth"], name, cand["size"])
            _extract_zim(part, name, zim_tmp)
            part.unlink(missing_ok=True)
        else:
            _download(cand["url"], part, cand["auth"], name, cand["size"])
            os.replace(part, zim_tmp)
        _check_zim(zim_tmp)
        os.replace(zim_tmp, staged_book(name))
    finally:
        part.unlink(missing_ok=True)
        zim_tmp.unlink(missing_ok=True)
    status_entry["fetched"] = {"version": cand["version"], "label": cand["label"],
                               "size": staged_book(name).stat().st_size}


def _fetched(name, cand, entry):
    return (entry.get("fetched") or {}).get("version") == cand["version"] and staged_book(name).exists()


def install(src, cand, policy, status_entry):
    """Swap cand in as <name>.zim with no gap, fetching it first unless it already is."""
    name = src["name"]
    if not _fetched(name, cand, status_entry):
        fetch_book(src, cand, policy, status_entry)
    staged = staged_book(name)
    archived = ARCHIVE_DIR / name / f"{_safe(cand['version'])}.zim"
    try:
        book = ZIM_DIR / f"{name}.zim"
        current = status_entry.get("current", {}).get("version")
        if book.exists() and policy["keep_old"] > 0:
            folder = ARCHIVE_DIR / name
            folder.mkdir(parents=True, exist_ok=True)
            label = _safe(current or f"untracked-{int(book.stat().st_mtime)}")
            target = folder / f"{label}.zim"
            target.unlink(missing_ok=True)
            os.link(book, target)  # a second name for the same data: no copy, no gap
        os.replace(staged, book)
        archived.unlink(missing_ok=True)  # current now, so not an old version
    finally:
        staged.unlink(missing_ok=True)
        status_entry.pop("fetched", None)

    library_put(book)
    archived = _prune_archive(name, policy["keep_old"])
    status_entry["current"] = {"version": cand["version"], "label": cand["label"],
                               "size": book.stat().st_size, "installed": now_iso()}
    status_entry["archive"] = archived


def rollback(name, version=None):
    src = next((s for s in load_config()["sources"] if s["name"] == name), {})
    if src.get("kind") == "app":
        if not (installed_app(name) or {}).get("has_previous"):
            raise LibrarianError(f"no previous {name} bundle to go back to")
        if APPS.get(name, {}).get("local"):
            return rollback_local(name)
        return "queued: " + _queue_root({"action": "app-rollback", "app": name})
    folder = ARCHIVE_DIR / name
    choices = sorted(folder.glob("*.zim"), key=lambda p: p.stat().st_mtime, reverse=True)
    if version:
        choices = [p for p in choices if p.stem == _safe(version)]
    if not choices:
        raise LibrarianError(f"no archived version of {name}" + (f" called {version}" if version else ""))
    pick = choices[0]
    with Lock():
        status = load_status()
        entry = status.setdefault(name, {})
        book = ZIM_DIR / f"{name}.zim"
        current = entry.get("current", {}).get("version")
        if book.exists() and current:
            keep = folder / f"{_safe(current)}.zim"
            keep.unlink(missing_ok=True)
            os.link(book, keep)
        tmp = ZIM_DIR / f".{name}.zim.new"
        shutil.copyfile(pick, tmp)
        try:
            _check_zim(tmp, f"the archived version {pick.stem}")
        except LibrarianError:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, book)
        pick.unlink()
        library_put(book)
        entry["current"] = {"version": pick.stem, "label": f"rolled back to {pick.stem}",
                            "size": book.stat().st_size, "installed": now_iso()}
        entry["archive"] = _prune_archive(name, max(load_config()["policy"]["keep_old"], 1))
        save_status(status)
        return pick.stem


# --- the run -----------------------------------------------------------------

def _due(entry, hours):
    if hours <= 0:
        return False
    last = entry.get("last_check")
    if not last:
        return True
    try:
        then = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - then).total_seconds() >= hours * 3600 - 300


def update_book(src, cand, entry, mode, policy, log):
    """A book source's part of update(), as update_app() is an app's."""
    name = src["name"]
    if entry.get("current", {}).get("version") == cand["version"] and (ZIM_DIR / f"{name}.zim").exists():
        entry.pop("fetched", None)
        staged_book(name).unlink(missing_ok=True)
        return f"up to date: {cand['label']}"
    if _fetched(name, cand, entry) and mode != "update":
        return f"fetched {cand['label']}; ready to update"
    if mode == "check":
        return f"newer available: {cand['label']}"
    if mode == "fetch":
        log(f"{name}: fetching {cand['label']}")
        fetch_book(src, cand, policy, entry)
        return f"fetched {cand['label']}; ready to update"
    log(f"{name}: installing {cand['label']}")
    install(src, cand, policy, entry)
    return f"installed {cand['label']}"


def update(names=None, scheduled=False, download=True, log=print, mode=None):
    """Check the chosen sources (all enabled ones by default) and act on anything newer:
    mode "check" only records it, "fetch" downloads and checks it without changing what is
    in use, "update" (the default; download=False means "check") puts it in use too.
    Returns {name: outcome}. One source failing never stops the others."""
    cfg = load_config()
    policy = cfg["policy"]
    # The timer goes as far as the owner chose; asked for by name or from /admin, it does what was asked.
    mode = mode or (("check", "fetch", "update")[policy["auto_install"]] if scheduled else "update" if download else "check")
    results = {}
    _memo.clear()  # a run asks afresh (ETags keep that cheap), then once per address
    with Lock():
        status = load_status()
        # A scheduled run takes the most overdue first, and stops asking GitHub while a few of
        # its hourly requests are left: the rest wait for the next run (spread across the day).
        order = sorted(cfg["sources"], key=lambda s: (status.get(s["name"]) or {}).get("last_check") or "") if scheduled else cfg["sources"]
        for src in order:
            name = src["name"]
            if names and name not in names:
                continue
            if not names and not src.get("enabled", True):
                continue
            entry = status.setdefault(name, {})
            if scheduled and not _due(entry, policy["check_every_hours"]):
                continue
            left = rate()
            if scheduled and src.get("type") in ("release", "actions", "nightly-link") and left.get("remaining") is not None \
                    and left["remaining"] < RESERVE and left.get("reset", 0) > time.time():
                entry["deferred"] = now_iso()
                results[name] = f"waiting: {left['remaining']} of GitHub's {left.get('limit')} requests left this hour"
                continue
            entry.pop("deferred", None)
            entry["last_check"] = now_iso()
            try:
                if src.get("kind") == "app":
                    # A pinned app still has its pin when the newest cannot be found.
                    try:
                        cand, latest_error = resolve(src), None
                        entry["latest"] = {"version": cand["version"], "label": cand["label"], "size": cand["size"]}
                    except LibrarianError as exc:
                        cand, latest_error = None, str(exc)
                    log(f"{name}: checking the app bundle")
                    outcome = update_app_source(src, cand, entry, mode, latest_error)
                else:
                    cand = resolve(src)
                    entry["latest"] = {"version": cand["version"], "label": cand["label"], "size": cand["size"]}
                    outcome = update_book(src, cand, entry, mode, policy, log)
                entry.pop("error", None)
            except (LibrarianError, OSError) as exc:
                entry["error"] = str(exc)
                outcome = f"error: {exc}"
            entry["outcome"] = outcome
            results[name] = outcome
            log(f"{name}: {outcome}")
            save_status(status)
        save_status(status)  # the deferrals too
        if _rate:
            _write_json(RATE_FILE, _rate)
        # The firmware mirror (firmware.py), on the same schedule and under the same lock.
        if not names or "firmware" in names:
            from irate_box.library import firmware
            fw = firmware.settings()
            if (fw["enabled"] or fw["configs"]) and (not scheduled or firmware.due(policy["check_every_hours"])):
                try:
                    results["firmware"] = firmware.sync(check_only=(mode == "check"), log=log)
                except (LibrarianError, OSError) as exc:
                    results["firmware"] = f"error: {exc}"
                log(f"firmware: {results['firmware']}")
        # Mirrored git repositories (mirrors.py), likewise.
        if not names or "mirrors" in names:
            from irate_box.library import mirrors
            if mirrors.load():
                # One stage failing doesn't stop the ones after it (the hub's own update among them).
                try:
                    results["mirrors"] = mirrors.sync_all(check_only=(mode == "check"), scheduled=scheduled,
                                                          hours=policy["check_every_hours"], log=log)
                except (LibrarianError, OSError) as exc:
                    results["mirrors"] = f"error: {exc}"
                    log(f"mirrors: error: {exc}")
        # Toolkits (toolkits.py): installs whose time is up removed; one due kit's cache refreshed.
        if (scheduled and not names) or (names and "toolkits" in names):
            from irate_box.library import toolkits
            try:
                line = toolkits.step(policy, log=log)
            except (LibrarianError, OSError) as exc:
                line = f"error: {exc}"
            if line:
                results["toolkits"] = line
                log(f"toolkits: {line}")
        # The hub's own updates (selfupdate.py): one step per run of the timer.
        if scheduled and not names:
            from irate_box.library import selfupdate
            results["hub"] = selfupdate.step(policy, log=log)
            log(f"hub: {results['hub']}")
    return results


LAST_RUN = LIB_DIR / "last-run.json"


def record_run(fn, scheduled):
    """Run update() and keep a record of it, for the page and both doctors: when it started and
    ended, whether it finished, the error if it crashed, which stages it reached, and (for the
    timer's runs) when one last reached the hub's own update. Before 2026-10-06 a crash was only
    in the journal, and every scheduled run failed for hours without a doctor noticing."""
    import traceback
    rec = _read_json(LAST_RUN, {})
    run = {"started": time.time(), "scheduled": scheduled, "finished": None, "ok": None, "error": None, "stages": []}
    try:
        results = fn()
        run.update(ok=not any(str(o).startswith("error") for o in outcomes(results)), stages=sorted(results),
                   errors=[f"{k}: {o}" for k, v in results.items() for o in ([v] if not isinstance(v, dict) else
                           [f"{n}: {x}" for n, x in v.items()]) if str(o).startswith("error") or ": error" in str(o)][:10])
        if scheduled and "hub" in results:
            rec["hub_stage_at"] = time.time()
        return results
    except BaseException as exc:
        run.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:400],
                   where=" < ".join(f"{f.name} ({Path(f.filename).name}:{f.lineno})" for f in reversed(traceback.extract_tb(exc.__traceback__)[-3:])))
        raise
    finally:
        run["finished"] = time.time()
        rec["last"] = run
        if scheduled:
            rec["last_scheduled"] = run
        try:
            _write_json(LAST_RUN, rec)
        except OSError:
            pass


def outcomes(results):
    """Every outcome line in update()'s results: a stage's own, or each one's of a stage that
    has several (the mirrors: {name: line}, which main() once took for a line and crashed on)."""
    for v in results.values():
        if isinstance(v, dict):
            yield from (str(x) for x in v.values())
        else:
            yield str(v)


def _hub_update_state():
    from irate_box.library import selfupdate
    return selfupdate.load_state()


def snapshot():
    """Everything the admin page shows."""
    cfg = load_config()
    return {"policy": cfg["policy"], "sources": cfg["sources"], "status": load_status(),
            "token_set": bool(token()), "running": is_running(), "types": list(TYPES),
            "progress": progress(), "apps": apps_snapshot(), "hub_update": _hub_update_state(), "github": rate(),
            "free_mb": _free_bytes(ZIM_DIR) >> 20 if ZIM_DIR.exists() else None}


# --- the books, a page at a time (next-work plan step 14) ---------------------------------------
# What the Books page lists: every book in Kiwix's catalogue (its title, language and date), every
# .zim in the folder (one Kiwix can't read has a row too), and every book source (one not installed
# yet has a row). A book with a source is "kept current"; one without came by hand or USB.
BOOK_STATES = ("ok", "newer", "failed", "not installed", "unreadable")
PER_PAGE = 50
_catalogue_cache = {"key": None, "rows": None}


def _catalogue():
    """{file name: {id, title, language, date, description, articles}} from library.xml, cached."""
    import xml.etree.ElementTree as ET
    try:
        key = LIBRARY_XML.stat().st_mtime_ns
    except OSError:
        return {}
    if _catalogue_cache["key"] != key:
        rows = {}
        try:
            for b in ET.parse(LIBRARY_XML).getroot().iter("book"):
                rows[Path(b.get("path", "")).name] = {"id": b.get("id"), "title": b.get("title") or "", "language": b.get("language") or "",
                                                     "date": b.get("date") or "", "description": b.get("description") or "",
                                                     "articles": int(b.get("articleCount") or 0)}
        except (OSError, ET.ParseError):
            rows = {}
        _catalogue_cache.update(key=key, rows=rows)
    return _catalogue_cache["rows"]


def book_rows():
    cat = _catalogue()
    cfg = load_config()
    status = load_status()
    sources = {s["name"]: s for s in cfg["sources"] if s.get("kind") != "app"}
    files = {p.stem: p for p in ZIM_DIR.glob("*.zim")} if ZIM_DIR.is_dir() else {}
    rows = []
    for name in sorted(set(files) | set(sources)):
        f = files.get(name)
        meta = cat.get(f"{name}.zim") or {}
        st = status.get(name) or {}
        cur, latest = st.get("current") or {}, st.get("latest") or {}
        state = ("not installed" if not f else "unreadable" if not meta else "failed" if st.get("error")
                 else "newer" if latest.get("version") and latest.get("version") != cur.get("version") else "ok")
        try:
            size = f.stat().st_size if f else 0
        except OSError:
            size = 0
        rows.append({"name": name, "title": meta.get("title") or name, "language": meta.get("language", ""), "date": meta.get("date", ""),
                     "description": meta.get("description", ""), "size": size, "state": state, "kept": name in sources})
    return rows, sources, status


def books_page(q="", language="", state="", kept="", sort="title", page=1, per_page=PER_PAGE, names_only=False):
    """One page of the books, filtered and sorted, with a summary of all of them."""
    rows, sources, status = book_rows()
    summary = {"count": len(rows), "bytes": sum(r["size"] for r in rows), "languages": {}, "states": {}, "kept": sum(r["kept"] for r in rows),
               "budget_mb": load_config()["policy"]["books_budget_mb"]}
    for r in rows:
        if r["language"]:
            summary["languages"][r["language"]] = summary["languages"].get(r["language"], 0) + 1
        summary["states"][r["state"]] = summary["states"].get(r["state"], 0) + 1
    q = (q or "").strip().lower()
    hit = [r for r in rows if (not q or q in r["title"].lower() or q in r["name"].lower() or q in r["description"].lower())
           and (not language or r["language"] == language) and (not state or r["state"] == state)
           and (kept not in ("yes", "no") or r["kept"] == (kept == "yes"))]
    key = {"title": lambda r: (r["title"].lower(), r["name"]), "size": lambda r: (-r["size"], r["name"]),
           "date": lambda r: (r["date"], r["name"])}.get(sort, lambda r: (r["title"].lower(), r["name"]))
    hit.sort(key=key, reverse=(sort == "date"))
    if names_only:
        return {"names": [r["name"] for r in hit], "matching": len(hit)}
    per_page = max(10, min(int(per_page or PER_PAGE), 200))
    pages = max(1, -(-len(hit) // per_page))
    page = max(1, min(int(page or 1), pages))
    out = hit[(page - 1) * per_page:page * per_page]
    for r in out:  # a page's books carry their source and status, for the card that opens on demand
        r["source"] = sources.get(r["name"])
        r["status"] = status.get(r["name"]) or {}
    return {"books": out, "page": page, "pages": pages, "per_page": per_page, "matching": len(hit), "summary": summary}


# --- CLI ---------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description="Keep the hub's ZIM books up to date.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    u = sub.add_parser("update")
    u.add_argument("names", nargs="*")
    u.add_argument("--scheduled", action="store_true")
    c = sub.add_parser("check")
    c.add_argument("names", nargs="*")
    f = sub.add_parser("fetch")
    f.add_argument("names", nargs="*")
    a = sub.add_parser("add")
    for opt in ("name", "type", "repo", "workflow", "branch", "pattern", "url"):
        a.add_argument(f"--{opt}")
    a.add_argument("--prerelease", action="store_true")
    r = sub.add_parser("remove")
    r.add_argument("name")
    r.add_argument("--delete-book", action="store_true")
    b = sub.add_parser("rollback")
    b.add_argument("name")
    b.add_argument("version", nargs="?")
    pol = sub.add_parser("policy")
    pol.add_argument("--keep-old", type=int)
    pol.add_argument("--check-every-hours", type=int)
    pol.add_argument("--min-free-mb", type=int)
    for k in ("auto-install", "hub-check-every-hours", "hub-auto", "hub-window-start", "hub-window-end"):
        pol.add_argument(f"--{k}", type=int)
    sub.add_parser("hub-update")
    t = sub.add_parser("token")
    t.add_argument("value", nargs="?", default="")
    aa = sub.add_parser("add-apps")
    aa.add_argument("apps", nargs="*")
    sub.add_parser("rebuild-library")
    af = sub.add_parser("app-fetch")
    af.add_argument("app")
    fw = sub.add_parser("firmware")
    fw.add_argument("--check", action="store_true")
    args = p.parse_args(argv)

    try:
        if args.cmd == "status":
            print(json.dumps(snapshot(), indent=2))
        elif args.cmd == "update":
            results = record_run(lambda: update(args.names, scheduled=args.scheduled), args.scheduled)
            # The timer exits 0 regardless: a failing source is recorded in status.json and
            # shown on /admin, rather than leaving a failed unit on the dashboard every hour.
            failed = any(str(o).startswith("error") for o in outcomes(results))
            return 1 if failed and not args.scheduled else 0
        elif args.cmd == "check":
            update(args.names, mode="check")
        elif args.cmd == "fetch":
            update(args.names, mode="fetch")
        elif args.cmd == "add":
            src = {k: getattr(args, k) for k in ("name", "type", "repo", "workflow", "branch", "pattern", "url")}
            src["prerelease"] = args.prerelease
            print(json.dumps(add_source({k: v for k, v in src.items() if v is not None}), indent=2))
        elif args.cmd == "remove":
            remove_source(args.name, args.delete_book)
        elif args.cmd == "rollback":
            print(f"{args.name}: now {rollback(args.name, args.version)}")
        elif args.cmd == "policy":
            print(json.dumps(set_policy(**{k: getattr(args, k) for k in DEFAULT_POLICY
                                           if getattr(args, k, None) is not None}), indent=2))
        elif args.cmd == "hub-update":
            from irate_box.library import selfupdate
            with Lock():
                print(selfupdate.step(load_config()["policy"]))
        elif args.cmd == "token":
            set_token(args.value)
            print("token " + ("set" if args.value else "cleared"))
        elif args.cmd == "add-apps":
            have = {s["name"] for s in load_config()["sources"]}
            for app in args.apps or default_apps():
                if app not in have:
                    add_source(default_app_source(app))
                    print(f"added {app}")
        elif args.cmd == "firmware":
            from irate_box.library import firmware
            with Lock():
                print(firmware.sync(check_only=args.check))
        elif args.cmd == "rebuild-library":
            with Lock():
                rebuild_library()
        elif args.cmd == "app-fetch":
            if args.app not in APPS:
                raise LibrarianError(f"app must be one of {', '.join(APPS)}")
            src = next((s for s in load_config()["sources"] if s["name"] == args.app),
                       validate_source(default_app_source(args.app)))
            with Lock():
                path, meta = fetch_app_source(src)
            print(path)
            print(f"{args.app}: {meta.get('commit', '')[:7]} ({meta.get('ref')}, built {meta.get('built')})", file=sys.stderr)
    except LibrarianError as exc:
        print(f"librarian: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
