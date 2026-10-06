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


def _git(*args, cwd=None, timeout=600):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def enqueue(stdin=sys.stdin, repo_dir=None):
    """post-receive: stdin is "<old> <new> <ref>" per ref pushed; cwd is the bare repository."""
    # Under git http-backend the environment is the web request's: PATH may be only git's own.
    os.environ["PATH"] = os.environ.get("PATH", "") + ":/usr/bin:/bin"
    repo = Path(repo_dir or os.environ.get("GIT_DIR", ".")).resolve()
    if repo.parent.name != "private":
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
                  started=started, state="running")
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
    for old in sorted((p for p in repo_runs.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))[:-KEEP_RUNS]:
        shutil.rmtree(old, ignore_errors=True)


def run_queue():
    """Every queued job, oldest first, until the queue is empty (more may arrive meanwhile)."""
    WORK.mkdir(exist_ok=True)
    # The repositories belong to the hub user and builds run as hubci, so git refuses them as
    # "dubious ownership". It has to be hubci's global config: git strips -c and GIT_CONFIG_*
    # from the upload-pack that a local clone starts.
    if _git("config", "--global", "--get-all", "safe.directory").stdout.split() != ["*"]:
        _git("config", "--global", "--replace-all", "safe.directory", "*")
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
            if job:
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
    return {"installed": QUEUE.is_dir(), "queued": queued, "runs": runs[:limit], "script": SCRIPT,
            "time_limit": TIME_LIMIT}


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
