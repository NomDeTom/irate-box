#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Builds on push: a small CI for the hub's private git repositories.

A push to a private repository (/git-private/, behind the admin login) whose new commit has an
executable-or-not .irate-ci.sh at its top queues a build. Public repositories never build: guests
may be allowed to push there, and a build runs whatever the commit says.

  ci.py enqueue    the post-receive hook (git-hooks/post-receive), run by git http-backend as
                   the hub user inside the pushed repository: one job file per branch pushed,
                   into $HUB_CI_ROOT/queue/
  ci.py run        irate-box-ci.service, started by irate-box-ci.path when the queue is not
                   empty, as the unprivileged hubci user: each job in turn, oldest first

A build is a fresh clone of the pushed commit and `bash .irate-ci.sh` in it, with
  CI=1  CI_REPO  CI_BRANCH  CI_COMMIT  CI_ARTIFACTS (a folder: what the script leaves there is
  kept)  HOME (persistent, so tool caches such as ~/.platformio survive between builds)
  CI_PIO_DEPS  (when the librarian carries one: firmware.py) PlatformIO's libdeps/ and packages/
  for the newest kept Meshtastic release, for a build with no internet
and a time limit. Its log, status and artifacts go to runs/<repo>/<number>/; the newest
KEEP_RUNS per repository are kept. /admin's Git page lists them (snapshot()).

Stdlib only.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("HUB_CI_ROOT", "/var/lib/hub/ci"))
# Builds come only from here (F23): the queue is writable by the hub, and guests may push to public repos.
PRIVATE = Path(os.environ.get("HUB_GIT_PRIVATE", ROOT.parent / "git" / "private"))
# The librarian's mirrors (mirrors.py): upstream URL -> the local repository. Builds fetch from
# these instead, so a submodule (Meshtastic's protobufs, say) needs no internet.
MIRROR_URLS = Path(os.environ.get("HUB_MIRROR_URLS", PRIVATE.parent / "mirror-urls.json"))
QUEUE = ROOT / "queue"
RUNS = ROOT / "runs"
WORK = ROOT / "work"
SCRIPT = ".irate-ci.sh"
KEEP_RUNS = 5
# Per build. A first meshtasticd build on the Lyra (one job, ~900 files) took longer than 2 h
# and was heading past 6; later builds are incremental, since HOME keeps the objects.
TIME_LIMIT = int(os.environ.get("HUB_CI_TIME_LIMIT", 12 * 3600))
ZERO = "0" * 40
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[0-9]{1,6}$")


# The newest KEEP_RUNS per repository are kept, but runs marked keep; the owner sets the number
# (/admin, Builds) in $HUB_STATE_DIR/library/ci.json.
SETTINGS = Path(os.environ.get("HUB_CI_SETTINGS", ROOT.parent / "library" / "ci.json"))
# What a build gets in its environment (build() below): said on the page, and the templates use
# only these (tests/sim_ci_view.py checks).
ENV_VARS = {
    "CI": "1, so a script can tell it runs here",
    "CI_REPO": "the repository's name",
    "CI_BRANCH": "the branch pushed (or chosen with Build now)",
    "CI_COMMIT": "the commit, 40 hex digits",
    "CI_ARTIFACTS": "a folder: what the script leaves there is kept with the run",
    "CI_PIO_DEPS": "when the library keeps one: PlatformIO's packages for the newest Meshtastic release",
    "HOME": "a folder of its own that stays between builds, so tool caches (~/.platformio, a venv) survive",
}
TEMPLATES = {
    "meshtasticd": {"title": "Meshtastic firmware: meshtasticd (native)", "script": """#!/bin/bash
# .irate-ci.sh: build meshtasticd (PlatformIO env "native") on the box. The source is cloned from
# GitHub's URL, which this box rewrites to its own mirror (Library -> Mirrors), so no internet is
# needed once the mirror and PlatformIO are in place. Needs the Building kit. A first build on a
# small board takes hours (the Lyra: 2.8 h); later ones are incremental, as $HOME is kept.
set -eu
FW_REF=develop
[ -x "$HOME/pio/bin/pio" ] || { python3 -m venv "$HOME/pio" && "$HOME/pio/bin/pip" install -q platformio; }
[ -d "$HOME/fw/.git" ] || git clone -q --depth 50 https://github.com/meshtastic/firmware "$HOME/fw"
cd "$HOME/fw"
git fetch -q --depth 50 origin "$FW_REF" && git checkout -q FETCH_HEAD
git submodule update -q --init --depth 1
echo "== building"
"$HOME/pio/bin/pio" run -e native -j 1
echo "== keeping the program"
cp .pio/build/native/meshtasticd "$CI_ARTIFACTS/" 2>/dev/null || cp .pio/build/native/program "$CI_ARTIFACTS/meshtasticd"
"""},
    "make": {"title": "Run make", "script": """#!/bin/bash
# .irate-ci.sh: run make, and keep what it builds in out/. Needs the Building kit.
set -eu
echo "== building"
make -j"$(nproc)"
echo "== keeping the results"
[ -d out ] && cp -r out/. "$CI_ARTIFACTS/" || true
"""},
    "python-test": {"title": "Run a Python test", "script": """#!/bin/bash
# .irate-ci.sh: run the repository's tests with the standard library's unittest, and keep a report.
set -eu
echo "== testing ($CI_BRANCH at ${CI_COMMIT:0:7})"
python3 -m unittest discover -v 2>&1 | tee "$CI_ARTIFACTS/test-report.txt"
"""},
}


def settings():
    try:
        s = json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        s = {}
    keep = s.get("keep_runs", KEEP_RUNS)
    return {"keep_runs": keep if type(keep) is int and 1 <= keep <= 50 else KEEP_RUNS}


def _job(name):
    return QUEUE / f"{time.time_ns()}-{secrets.token_hex(3)}.json", name


def _put(job):
    path, _ = _job("")
    tmp = QUEUE / f".{path.name}"
    tmp.write_text(json.dumps(job))
    tmp.chmod(0o640)
    os.replace(tmp, path)


def queue_build(repo, branch=None):
    """Build now: a private repository's branch (its default one if none), queued as a push would
    queue it, with the same refusals: public, switched off, no .irate-ci.sh at that commit."""
    repo = Path(repo).resolve()
    if repo.parent != PRIVATE.resolve() or not NAME_RE.match(repo.name[:-4]) or not (repo / "HEAD").exists():
        raise ValueError("only private repositories build")
    off = _git("config", "--get", "irate-box.ci", cwd=repo)
    if off.returncode == 0 and off.stdout.strip() == "off":
        raise ValueError("builds are switched off for this repository (Manage, Access)")
    if branch is None:
        branch = _git("symbolic-ref", "--short", "HEAD", cwd=repo).stdout.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", branch or "") or ".." in branch:
        raise ValueError("not a branch name")
    commit = _git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}", cwd=repo).stdout.strip()
    if not commit:
        raise ValueError(f"no branch {branch} in {repo.name}")
    if _git("cat-file", "-e", f"{commit}:{SCRIPT}", cwd=repo).returncode != 0:
        raise ValueError(f"{branch}'s newest commit has no {SCRIPT} at its top")
    QUEUE.mkdir(parents=True, exist_ok=True)
    _put({"repo": str(repo), "branch": branch, "commit": commit, "queued": time.time(), "by": "build-now"})
    return {"branch": branch, "commit": commit}


def queue_run_change(run, what):
    """Keep or delete a run: the runs are hubci's, so the change goes through the queue, done by
    the builder (after the build in progress, if one is)."""
    if not RUN_RE.match(run or "") or what not in ("keep", "unkeep", "delete"):
        raise ValueError("a run, and keep, unkeep or delete")
    QUEUE.mkdir(parents=True, exist_ok=True)
    _put({"run": run, "change": what, "queued": time.time()})


def run_view(run, offset=0, limit=64 * 1024):
    """One run for /admin: its status, its log from `offset` (a page at a time, for following it
    live), the steps (its == lines), and its artifacts with their sizes."""
    if not RUN_RE.match(run or ""):
        return None
    base = RUNS / run
    try:
        status = json.loads((base / "status.json").read_text())
    except (OSError, ValueError):
        return None
    log = base / "log.txt"
    size = log.stat().st_size if log.is_file() and not log.is_symlink() else 0
    offset = max(0, min(int(offset or 0), size))
    text, steps = "", []
    if size:
        with open(log, "rb") as fh:
            fh.seek(offset)
            text = fh.read(limit).decode("utf-8", "replace")
            fh.seek(0)
            steps = [l.decode("utf-8", "replace")[3:].strip()[:200] for l in fh if l.startswith(b"== ")][:200]
    arts = []
    adir = base / "artifacts"
    for p in sorted(adir.iterdir()) if adir.is_dir() else []:
        if p.is_file() and not p.is_symlink():
            arts.append({"name": p.name, "size": p.stat().st_size})
    return {"run": run, "status": status, "log": text, "offset": offset, "next": offset + len(text.encode("utf-8")), "size": size,
            "steps": steps, "artifacts": arts}


def _git(*args, cwd=None, timeout=600):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def use_mirrors():
    """hubci's global url.<local>.insteadOf <upstream> for each mirrored URL, and none left over
    from a mirror since removed. Global config, for the reason safe.directory is (above)."""
    try:
        urls = json.loads(MIRROR_URLS.read_text())
    except (OSError, ValueError):
        urls = {}
    have = _git("config", "--global", "--get-regexp", r"^url\..*\.insteadof$").stdout.splitlines()
    for line in have:
        key, _, upstream = line.partition(" ")
        local = key[len("url."):-len(".insteadof")]
        if local.startswith("file://") and urls.get(upstream) != local:
            _git("config", "--global", "--unset-all", key, f"^{re.escape(upstream)}$")
    for upstream, local in urls.items():
        if isinstance(local, str) and local.startswith("file://") and f"url.{local}.insteadof {upstream}" not in have:
            _git("config", "--global", "--add", f"url.{local}.insteadOf", upstream)
    return urls


def enqueue(stdin=sys.stdin, repo_dir=None):
    """post-receive: stdin is "<old> <new> <ref>" per ref pushed; cwd is the bare repository."""
    # Under git http-backend the environment is the web request's: PATH may be only git's own.
    os.environ["PATH"] = os.environ.get("PATH", "") + ":/usr/bin:/bin"
    repo = Path(repo_dir or os.environ.get("GIT_DIR", ".")).resolve()
    if repo.parent.name != "private":
        return 0
    # The repository's build switch (gitrepos.build_on, /admin's Git page): on unless set off.
    off = _git("config", "--get", "irate-box.ci", cwd=repo)
    if off.returncode == 0 and off.stdout.strip() == "off":
        return 0
    for line in stdin:
        parts = line.split()
        if len(parts) != 3:
            continue
        _, new, ref = parts
        if new == ZERO or not ref.startswith("refs/heads/"):
            continue
        if _git("cat-file", "-e", f"{new}:{SCRIPT}", cwd=repo).returncode != 0:
            continue
        job = {"repo": str(repo), "branch": ref[len("refs/heads/"):], "commit": new, "queued": time.time()}
        name = f"{time.time_ns()}-{secrets.token_hex(3)}.json"
        tmp = QUEUE / f".{name}"
        tmp.write_text(json.dumps(job))
        tmp.chmod(0o640)
        os.replace(tmp, QUEUE / name)
        print(f"irate-box: build queued for {repo.name[:-4]} {job['branch']} at {new[:7]} (see /admin, Git)")
    return 0


def _pio_deps():
    """The build cache the librarian carries (firmware.py), or None: PlatformIO's libraries and
    packages for the newest kept Meshtastic release, so a build can run with no internet."""
    root = Path(os.environ.get("HUB_FIRMWARE_ROOT", "/var/lib/hub/firmware"))
    try:
        cache = json.loads((root / "status.json").read_text()).get("cache") or {}
    except (OSError, ValueError):
        return None
    path = root / str(cache.get("version", "")) / "pio-deps"
    return path if cache.get("version") and path.is_dir() else None


def _next_number(repo_runs):
    nums = [int(p.name) for p in repo_runs.iterdir() if p.name.isdigit()] if repo_runs.is_dir() else []
    return max(nums, default=0) + 1


def _write_status(run, **fields):
    path = run / "status.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.update(fields)
    tmp = run / ".status.json"
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)


def build(job):
    repo = Path(job["repo"])
    name = repo.name[:-4]
    if not NAME_RE.match(name) or not re.fullmatch(r"[0-9a-f]{40}", job.get("commit", "")):
        return
    if repo.resolve().parent != PRIVATE.resolve():
        return  # a public (guest-pushed) repository, or anywhere else: never built
    repo_runs = RUNS / name
    repo_runs.mkdir(parents=True, exist_ok=True)
    run = repo_runs / str(_next_number(repo_runs))
    (run / "artifacts").mkdir(parents=True)
    started = time.time()
    _write_status(run, repo=name, branch=job["branch"], commit=job["commit"], queued=job["queued"],
                  started=started, state="running", **({"by": job["by"]} if job.get("by") in ("build-now",) else {}))
    src = WORK / name
    shutil.rmtree(src, ignore_errors=True)
    with open(run / "log.txt", "w") as log:
        def say(text):
            log.write(f"== {text}\n")
            log.flush()
        say(f"{name} {job['branch']} {job['commit'][:7]}, on {os.uname().nodename}")
        state = "failed"
        try:
            out = _git("clone", "--quiet", "--no-checkout", str(repo), str(src))
            if out.returncode != 0:
                raise RuntimeError(f"clone failed: {out.stderr.strip()}")
            out = _git("checkout", "--quiet", job["commit"], cwd=src)
            if out.returncode != 0:
                raise RuntimeError(f"checkout failed: {out.stderr.strip()}")
            _git("submodule", "update", "--init", "--recursive", "--depth", "1", cwd=src, timeout=3600)
            say(f"running {SCRIPT} (time limit {TIME_LIMIT // 60} min)")
            env = dict(os.environ, CI="1", CI_REPO=name, CI_BRANCH=job["branch"], CI_COMMIT=job["commit"],
                       CI_ARTIFACTS=str(run / "artifacts"))
            deps = _pio_deps()
            if deps:
                env["CI_PIO_DEPS"] = str(deps)
                say(f"CI_PIO_DEPS={deps} (the librarian's build cache)")
            proc = subprocess.run(["bash", SCRIPT], cwd=src, env=env, stdout=log, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, timeout=TIME_LIMIT)
            state = "passed" if proc.returncode == 0 else "failed"
            say(f"{SCRIPT} exited with {proc.returncode}")
        except subprocess.TimeoutExpired:
            state = "timed out"
            say(f"stopped: over the time limit of {TIME_LIMIT // 60} min")
        except (RuntimeError, OSError) as exc:
            say(str(exc))
        finally:
            shutil.rmtree(src, ignore_errors=True)
    _write_status(run, state=state, finished=time.time(), duration=round(time.time() - started),
                  artifacts=sorted(p.name for p in (run / "artifacts").iterdir() if p.is_file()))
    prune(repo_runs)


def prune(repo_runs):
    """The newest keep_runs of a repository's runs, plus any marked keep."""
    runs = sorted((p for p in repo_runs.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
    loose = [p for p in runs if not _status(p).get("keep")]
    for old in loose[:-settings()["keep_runs"]]:
        shutil.rmtree(old, ignore_errors=True)


def _status(run):
    try:
        return json.loads((run / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def change_run(job):
    """A queued keep, unkeep or delete (queue_run_change)."""
    if not RUN_RE.match(job.get("run", "")):
        return
    run = RUNS / job["run"]
    if not (run / "status.json").exists() or _status(run).get("state") == "running":
        return
    if job.get("change") == "delete":
        shutil.rmtree(run, ignore_errors=True)
    elif job.get("change") in ("keep", "unkeep"):
        _write_status(run, keep=job["change"] == "keep")


def prepare_git():
    """hubci's global git config for building: every repository trusted, file:// allowed for
    submodules, and the mirrors' rewrites. Returns the mirrors' URLs."""
    # The repositories belong to the hub user and builds run as hubci, so git refuses them as
    # "dubious ownership". It has to be hubci's global config: git strips -c and GIT_CONFIG_*
    # from the upload-pack that a local clone starts.
    if _git("config", "--global", "--get-all", "safe.directory").stdout.split() != ["*"]:
        _git("config", "--global", "--replace-all", "safe.directory", "*")
    # The mirrors are file:// URLs (use_mirrors), and git refuses those for submodules unless told
    # (protocol.file.allow, CVE-2022-39253: a cloned repository reading the local files it links
    # to). A build runs whatever its script says as this user anyway, so that guard protects
    # nothing here, and without it a submodule never comes from the mirror (the Lyra, 2026-10-06:
    # "fatal: transport 'file' not allowed").
    if _git("config", "--global", "--get", "protocol.file.allow").stdout.strip() != "always":
        _git("config", "--global", "protocol.file.allow", "always")
    return use_mirrors()


def run_queue():
    """Every queued job, oldest first, until the queue is empty (more may arrive meanwhile)."""
    WORK.mkdir(exist_ok=True)
    prepare_git()
    while True:
        jobs = sorted(p for p in QUEUE.glob("*.json"))
        if not jobs:
            return 0
        for path in jobs:
            try:
                job = json.loads(path.read_text())
            except (OSError, ValueError):
                job = None
            path.unlink(missing_ok=True)
            if job and job.get("change"):
                change_run(job)
            elif job:
                build(job)


def snapshot(limit=20):
    """The newest runs across repositories, for /admin."""
    runs = []
    if RUNS.is_dir():
        for status in RUNS.glob("*/*/status.json"):
            try:
                data = json.loads(status.read_text())
            except (OSError, ValueError):
                continue
            data["run"] = f"{status.parent.parent.name}/{status.parent.name}"
            runs.append(data)
    runs.sort(key=lambda r: r.get("started", 0), reverse=True)
    queued = len(list(QUEUE.glob("*.json"))) if QUEUE.is_dir() else 0
    used = 0
    for f in RUNS.rglob("*") if RUNS.is_dir() else []:
        try:
            if f.is_file() and not f.is_symlink():
                used += f.stat().st_size
        except OSError:
            pass
    # systemd lists properties in its own order, not the order asked: read them by name.
    props = dict(l.split("=", 1) for l in subprocess.run(["systemctl", "show", "-p", "MemoryMax", "-p", "MemoryHigh", "irate-box-ci.service"],
                                                         capture_output=True, text=True).stdout.splitlines() if "=" in l)
    try:
        mirrors = sorted(json.loads(MIRROR_URLS.read_text()))
    except (OSError, ValueError):
        mirrors = []
    return {"installed": QUEUE.is_dir(), "queued": queued, "runs": runs[:limit], "script": SCRIPT,
            "time_limit": TIME_LIMIT, "keep_runs": settings()["keep_runs"], "runs_bytes": used,
            "memory": {"max": props.get("MemoryMax"), "high": props.get("MemoryHigh")},
            "mirrored": mirrors, "pio_deps": bool(_pio_deps()), "env": ENV_VARS,
            "templates": {k: {"title": v["title"], "script": v["script"]} for k, v in TEMPLATES.items()}}


def run_file(run, name):
    """A run's log or one of its artifacts, for /admin: the path, or None."""
    if not RUN_RE.match(run or ""):
        return None
    base = RUNS / run
    if name == "log.txt":
        path = base / "log.txt"
    elif NAME_RE.match(name or ""):
        path = base / "artifacts" / name
    else:
        return None
    # Never through a link the build left (F23): /admin would serve its target, read as the hub.
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(RUNS.resolve()):
        return None
    return path


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "enqueue":
        sys.exit(enqueue())
    if cmd == "run":
        sys.exit(run_queue())
    print("usage: ci.py enqueue|run", file=sys.stderr)
    sys.exit(2)
