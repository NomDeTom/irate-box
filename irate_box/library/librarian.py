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

TYPES = ("release", "actions", "nightly-link", "url")
DEFAULT_POLICY = {
    "keep_old": 1,            # archived previous versions per book; 0 deletes them
    "check_every_hours": 24,  # for the timer; 0 means only when asked
    "min_free_mb": 512,       # never let a download leave less than this free
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
POLICY_MAX = {"auto_install": 2, "hub_auto": 2, "hub_window_start": 23, "hub_window_end": 23}
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
            out.update(repo=spec["repo"], branch=branch, enabled=bool(src.get("enabled", True)))
            return out
        if out["type"] not in ("actions", "nightly-link"):
            raise LibrarianError("an app bundle comes from actions or nightly-link")
    if not NAME_RE.match(out["name"]) or out["name"] == "library":
        raise LibrarianError("name: letters, digits, '.', '_' and '-' only (it becomes <name>.zim)")
    if out["type"] not in TYPES:
        raise LibrarianError(f"type must be one of {', '.join(TYPES)}")
    pattern = str(src.get("pattern", "")).strip()
    if out["type"] == "url":
        url = str(src.get("url", "")).strip()
        if urllib.parse.urlparse(url).scheme not in ("http", "https"):
            raise LibrarianError("url must be http(s)://…")
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

APP_STAGING = LIB_DIR / "apps"
APP_MAX_BYTES = 400 << 20  # unpacked; the largest bundle (draw) is ~25 MB
APPS = manifests.installable()
BUNDLE_JSON = "irate-box-bundle.json"


def app_dir(name):
    return manifests.install_dir(APPS[name])


def default_app_source(name, via="nightly-link"):
    spec = APPS[name]["source"]
    if spec["type"] == "git":
        return {"name": name, "kind": "app", "type": "git", "repo": spec["repo"], "branch": spec.get("branch", "main")}
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
    """Hand a request to hub_control.py (as server.py's control_request does)."""
    control = STATE_DIR / "control"
    requests = control / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    rid = os.urandom(8).hex()
    tmp = control / f".{rid}.tmp"
    with open(tmp, "w") as fh:
        json.dump(dict(req, id=rid), fh)
    os.replace(tmp, requests / f"{rid}.json")
    return rid


def _git(*args, timeout=300):
    out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        lines = (out.stderr or out.stdout).strip().splitlines()
        raise LibrarianError(f"git {args[0]}: {lines[-1] if lines else 'failed'}")
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
        _git("clone", "-q", "--depth", "1", "--branch", src.get("branch", "main"), src["repo"], str(tree))
        commit = _git("-C", str(tree), "rev-parse", "HEAD").strip()
        shutil.rmtree(tree / ".git")
        adapt = APPS[name]["source"].get("adapt")
        if adapt:
            out = subprocess.run([str(CHECKOUT / "irate-box"), Path(adapt).stem, str(tree), str(CHECKOUT / "web")],
                                 capture_output=True, text=True, timeout=300)
            if out.returncode != 0:
                raise LibrarianError(f"{adapt} failed: {(out.stderr or out.stdout).strip()[-300:]}")
        (tree / BUNDLE_JSON).write_text(json.dumps({
            "app": name, "repository": src["repo"], "ref": src.get("branch", "main"),
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
    rid = _queue_root({"action": "app-install", "app": src["name"], "zip": str(staged)})
    entry["pending"] = rid
    entry.pop("install_result", None)
    entry.pop("fetched", None)
    return f"installing {fetched['commit']} ({cand['label']})"


def default_apps():
    """Apps kept current by "Keep all apps current": the core ones, and any already installed."""
    return [n for n, m in APPS.items() if m.get("source") and (m.get("core") or app_dir(n).is_dir())]


def apps_snapshot():
    return {name: {"title": m["install"].get("title", name), "installed": installed_app(name),
                   "source_type": m.get("source", {}).get("type")}
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
    (APP_STAGING / f"{name}.zip").unlink(missing_ok=True)
    if delete_book and name not in APPS:
        with Lock():
            (ZIM_DIR / f"{name}.zim").unlink(missing_ok=True)
            shutil.rmtree(ARCHIVE_DIR / name, ignore_errors=True)
            rebuild_library()


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

def _request(url, auth=None, method="GET"):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    if auth and urllib.parse.urlparse(url).hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {auth}"
    return urllib.request.Request(url, headers=headers, method=method)


def _open(url, auth=None, method="GET", timeout=60):
    try:
        return urllib.request.urlopen(_request(url, auth, method), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise LibrarianError(f"{url}: 401, a GitHub token is needed (or a valid one)")
        if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
            raise LibrarianError("GitHub API rate limit reached (60/hour without a token)")
        raise LibrarianError(f"{url}: HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise LibrarianError(f"cannot reach {urllib.parse.urlparse(url).hostname}: {reason}")


def _api(path, auth=None):
    with _open(f"{API}{path}", auth) as resp:
        return json.load(resp)


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


def _newest_artifact(src, auth):
    query = "status=success&per_page=10"
    if src.get("branch"):
        query += "&branch=" + urllib.parse.quote(src["branch"], safe="")
    runs = _api(f"/repos/{src['repo']}/actions/workflows/{src['workflow']}/runs?{query}", auth)
    for run in runs.get("workflow_runs", []):
        arts = _api(f"/repos/{src['repo']}/actions/runs/{run['id']}/artifacts", auth)
        for art in arts.get("artifacts", []):
            if not art.get("expired") and fnmatch.fnmatch(art["name"], src["pattern"]):
                return run, art
    where = f" on {src['branch']}" if src.get("branch") else ""
    raise LibrarianError(
        f"no successful {src['workflow']} run{where} in {src['repo']} has an unexpired "
        f"artifact matching {src['pattern']} (artifacts expire after 90 days)")


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


RESOLVERS = {"release": _resolve_release, "actions": _resolve_actions,
             "nightly-link": _resolve_nightly, "url": _resolve_url, "git": _resolve_git}


def resolve(src):
    return RESOLVERS[src["type"]](src, token())


# --- installing --------------------------------------------------------------

def _download(url, dest, auth=None, name=None, expected=0):
    """Stream url to dest. While it runs, progress.json says how far it has got (written at
    most once a second), so /admin can show a 60 MB book arriving at hotspot speed."""
    started = time.monotonic()
    done, last = 0, 0.0
    try:
        with _open(url, auth, timeout=120) as resp, open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0) or expected
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


def rebuild_library():
    """library.xml from the .zim files actually present, as install.sh does. kiwix-serve's
    --monitorLibrary notices the new file and serves the new versions."""
    manage = shutil.which("kiwix-manage")
    if not manage:
        raise LibrarianError("kiwix-manage is not installed (kiwix-tools)")
    new = ZIM_DIR / "library.xml.new"
    new.unlink(missing_ok=True)
    for zim in sorted(ZIM_DIR.glob("*.zim")):
        subprocess.run([manage, str(new), "add", str(zim)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if new.exists():
        os.replace(new, LIBRARY_XML)


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
    return ZIM_DIR / f".{name}.zim.fetched"


def fetch_book(src, cand, policy, status_entry):
    """Download cand and check it into staged_book(); readers see no change until install."""
    name = src["name"]
    need = cand["size"] * (2 if cand["zip"] else 1) + policy["min_free_mb"] * 2**20
    free = _free_bytes(ZIM_DIR)
    if cand["size"] and free < need:
        raise LibrarianError(f"not enough space: {free >> 20} MB free, {need >> 20} MB needed")

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

    rebuild_library()
    archived = _prune_archive(name, policy["keep_old"])
    status_entry["current"] = {"version": cand["version"], "label": cand["label"],
                               "size": book.stat().st_size, "installed": now_iso()}
    status_entry["archive"] = archived


def rollback(name, version=None):
    src = next((s for s in load_config()["sources"] if s["name"] == name), {})
    if src.get("kind") == "app":
        if not (installed_app(name) or {}).get("has_previous"):
            raise LibrarianError(f"no previous {name} bundle to go back to")
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
        rebuild_library()
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
    with Lock():
        status = load_status()
        for src in cfg["sources"]:
            name = src["name"]
            if names and name not in names:
                continue
            if not names and not src.get("enabled", True):
                continue
            entry = status.setdefault(name, {})
            if scheduled and not _due(entry, policy["check_every_hours"]):
                continue
            entry["last_check"] = now_iso()
            try:
                cand = resolve(src)
                entry["latest"] = {"version": cand["version"], "label": cand["label"], "size": cand["size"]}
                if src.get("kind") == "app":
                    log(f"{name}: checking the app bundle")
                    outcome = update_app(src, cand, entry, mode)
                elif entry.get("current", {}).get("version") == cand["version"] and (ZIM_DIR / f"{name}.zim").exists():
                    outcome = f"up to date: {cand['label']}"
                    entry.pop("fetched", None)
                    staged_book(name).unlink(missing_ok=True)
                elif _fetched(name, cand, entry) and mode != "update":
                    outcome = f"fetched {cand['label']}; ready to update"
                elif mode == "check":
                    outcome = f"newer available: {cand['label']}"
                elif mode == "fetch":
                    log(f"{name}: fetching {cand['label']}")
                    fetch_book(src, cand, policy, entry)
                    outcome = f"fetched {cand['label']}; ready to update"
                else:
                    log(f"{name}: installing {cand['label']}")
                    install(src, cand, policy, entry)
                    outcome = f"installed {cand['label']}"
                entry.pop("error", None)
            except LibrarianError as exc:
                entry["error"] = str(exc)
                outcome = f"error: {exc}"
            entry["outcome"] = outcome
            results[name] = outcome
            log(f"{name}: {outcome}")
            save_status(status)
        # The firmware mirror (firmware.py), on the same schedule and under the same lock.
        if not names or "firmware" in names:
            from irate_box.library import firmware
            if firmware.settings()["enabled"] and (not scheduled or firmware.due(policy["check_every_hours"])):
                results["firmware"] = firmware.sync(check_only=(mode == "check"), log=log)
                log(f"firmware: {results['firmware']}")
        # The hub's own updates (selfupdate.py): one step per run of the timer.
        if scheduled and not names:
            from irate_box.library import selfupdate
            results["hub"] = selfupdate.step(policy, log=log)
            log(f"hub: {results['hub']}")
    return results


def _hub_update_state():
    from irate_box.library import selfupdate
    return selfupdate.load_state()


def snapshot():
    """Everything the admin page shows."""
    cfg = load_config()
    return {"policy": cfg["policy"], "sources": cfg["sources"], "status": load_status(),
            "token_set": bool(token()), "running": is_running(), "types": list(TYPES),
            "progress": progress(), "apps": apps_snapshot(), "hub_update": _hub_update_state(),
            "free_mb": _free_bytes(ZIM_DIR) >> 20 if ZIM_DIR.exists() else None}


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
            results = update(args.names, scheduled=args.scheduled)
            # The timer exits 0 regardless: a failing source is recorded in status.json and
            # shown on /admin, rather than leaving a failed unit on the dashboard every hour.
            failed = any(v.startswith("error") for v in results.values())
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
                path, meta = fetch_app(src, resolve(src))
            print(path)
            print(f"{args.app}: {meta.get('commit', '')[:7]} ({meta.get('ref')}, built {meta.get('built')})", file=sys.stderr)
    except LibrarianError as exc:
        print(f"librarian: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
