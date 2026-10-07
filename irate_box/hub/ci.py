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
  CI_PIO_DEPS  (when the librarian carries one: firmware.py) PlatformIO's libdeps/, packages/
  and download cache (core/.cache/downloads) for the newest kept Meshtastic release, and
  CI_PIO_DEPS_TAG, that release's tag, for a build with no internet
  PIP_NO_INDEX, PIP_FIND_LINKS  (when the Building kit's wheelhouse is cached: root/kits.py) so
  `pip install platformio` needs no internet either (toolkits-plan §5)
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
import threading
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
    "CI_PIO_DEPS_TAG": "with CI_PIO_DEPS: the release it is for (v2.8.1.8e6a88d), the firmware a build can make with no internet",
    "PIP_NO_INDEX": "when the Building kit's wheelhouse is cached: 1, so pip never reaches the internet",
    "PIP_FIND_LINKS": "when the Building kit's wheelhouse is cached: its folder, so `pip install platformio` needs no internet",
    "HOME": "a folder of its own that stays between builds, so tool caches (~/.platformio, a venv) survive",
}
# The parts of a PlatformIO build the meshtasticd template and the Firmware Factory's builds share:
# which PlatformIO (Debian's, or the wheelhouse's as a fallback), seeding it from the build cache,
# and the build of $PIO_ENV (whose family is $PIO_ENV_FAMILY).
_PIO_PICK = """PIO_FROM=${PIO:-auto}
PIO_DEBIAN=/usr/bin/pio
venv_pio() {  # PlatformIO from the wheelhouse (or PyPI), in a venv that sees Debian's protobuf
  if ! grep -qs "include-system-site-packages = true" "$HOME/pio/pyvenv.cfg"; then
    rm -rf "$HOME/pio" && python3 -m venv --system-site-packages "$HOME/pio" >&2
  fi
  [ -x "$HOME/pio/bin/pio" ] || "$HOME/pio/bin/pip" install -q --ignore-installed platformio >&2
  echo "$HOME/pio/bin/pio"
}
"""
_PIO_SEED = """if [ -n "${CI_PIO_DEPS:-}" ]; then
  echo "== seeding PlatformIO from the build cache ($CI_PIO_DEPS_TAG)"
  # Only what is missing: what PlatformIO already has stays as it is. Its download cache gives it
  # the platform (by the URL's hash); usage.db is left out, as its old dates would expire the rest.
  mkdir -p "$HOME/.platformio/.cache/downloads"
  for f in "$CI_PIO_DEPS"/core/.cache/downloads/*; do
    [ "${f##*/}" = usage.db ] || [ -e "$HOME/.platformio/.cache/downloads/${f##*/}" ] || cp "$f" "$HOME/.platformio/.cache/downloads/"
  done
  # The cache's packages and libraries are the native build's: only a native build takes them.
  if [ "$PIO_ENV_FAMILY" = native ]; then
    LIBDEPS="${PLATFORMIO_WORKSPACE_DIR:-.pio}/libdeps/$PIO_ENV"
    mkdir -p "$HOME/.platformio/packages" "$LIBDEPS"
    for d in "$CI_PIO_DEPS"/packages/*/; do
      [ -e "$HOME/.platformio/packages/$(basename "$d")" ] || cp -r "$d" "$HOME/.platformio/packages/"
    done
    libs="$CI_PIO_DEPS/libdeps"; [ -d "$libs/native-tft" ] && libs="$libs/native-tft"
    for d in "$libs"/*/; do
      # Not the touchscreen build's libraries (the cache is native-tft's): PlatformIO's finder
      # compiles any library it sees, and meshtastic-device-ui needs headers native lacks (the
      # Lyra's offline build failed on them after 3 h 25 min, 2026-10-07). firmware.py UI_LIBS.
      case "$(basename "$d")" in lvgl|meshtastic-device-ui|SdFat|PNGdec|libdeflate) continue ;; esac
      [ -e "$LIBDEPS/$(basename "$d")" ] || cp -r "$d" "$LIBDEPS/"
    done
  fi
fi
"""
_PIO_BUILD = """pio_ver() { "$1" --version 2>/dev/null | grep -o '[0-9][0-9.]*' | head -1; }
case $PIO_FROM in
  pip) PIO_BIN=$(venv_pio) ;;
  debian) PIO_BIN=$PIO_DEBIAN ;;
  # Debian's first; but once the wheelhouse's has been installed (a fallback before) and is the
  # newer, that one. Debian's 6.1.10 removes a platform that needs a newer PlatformIO before it
  # refuses it (Meshtastic's espressif32 needs 6.1.19: found on the Lyra 2026-10-07), so going
  # to it first would download the platform again every build.
  *) if [ -x "$PIO_DEBIAN" ] && [ -x "$HOME/pio/bin/pio" ] \\
        && [ "$(printf '%s\\n%s\\n' "$(pio_ver "$PIO_DEBIAN")" "$(pio_ver "$HOME/pio/bin/pio")" | sort -V | tail -1)" != "$(pio_ver "$PIO_DEBIAN")" ]; then
       PIO_BIN=$HOME/pio/bin/pio
     elif [ -x "$PIO_DEBIAN" ]; then PIO_BIN=$PIO_DEBIAN; else PIO_BIN=$(venv_pio); fi ;;
esac
# FW_TOOLS_ONLY=1 (the Firmware Factory's "Fetch tools"): what the environment needs (platform,
# toolchain, framework, libraries) installed, nothing compiled.
if [ "${FW_TOOLS_ONLY:-}" = 1 ]; then PIO_DO=(pkg install -e "$PIO_ENV"); DOING="fetching the tools"; else PIO_DO=(run -e "$PIO_ENV" -j 1); DOING="building"; fi
echo "== $DOING with $("$PIO_BIN" --version) ($PIO_BIN)"
if ! "$PIO_BIN" "${PIO_DO[@]}" 2>&1 | tee "$HOME/pio-run.log"; then
  if [ "$PIO_FROM" = auto ] && [ "$PIO_BIN" = "$PIO_DEBIAN" ] && ! grep -q "^Compiling " "$HOME/pio-run.log"; then
    PIO_BIN=$(venv_pio)
    echo "== Debian's PlatformIO stopped before compiling; $DOING with $("$PIO_BIN" --version) from the wheelhouse"
    "$PIO_BIN" "${PIO_DO[@]}"
  else
    exit 1
  fi
fi
# What a build fetches by itself if it isn't there, which offline it can't: the tools Meshtastic's
# own build containers add per environment (meshtastic/gh-action-firmware, bin/pio_load_and_dedupe.sh).
if [ "${FW_TOOLS_ONLY:-}" = 1 ]; then
  EXTRA=(--tool platformio/tool-mklittlefs)
  case "${PIO_ENV_FAMILY:-}" in
    esp32*) EXTRA+=(--tool https://github.com/pioarduino/registry/releases/download/0.0.1/scons-4.11.1.zip) ;;
    *) EXTRA+=(--tool "platformio/tool-cppcheck@~1.21100.0") ;;
  esac
  echo "== and the tools a build fetches by itself: ${EXTRA[*]}"
  "$PIO_BIN" pkg install -e "$PIO_ENV" --no-save "${EXTRA[@]}"
fi
"""

TEMPLATES = {
    "meshtasticd": {"title": "Meshtastic firmware: meshtasticd (native)", "script": """#!/bin/bash
# .irate-ci.sh: build meshtasticd (PlatformIO env "native") on the box. The source is cloned from
# GitHub's URL, which this box rewrites to its own mirror (Library -> Mirrors), so no internet is
# needed once the mirror and PlatformIO are in place. Needs the Building kit: its PlatformIO is
# Debian's (/usr/bin/pio), with Debian's protobuf and protoc for nanopb, all signed like any
# package. PIO=debian or PIO=pip chooses; by default Debian's, and if that fails before compiling
# anything, one from the kit's wheelhouse in a venv of its own (PIP_NO_INDEX, PIP_FIND_LINKS).
# A first build on a small board takes hours (the Lyra: 2.8 h); later ones are incremental, as
# $HOME is kept.
set -euo pipefail
# What to build: the release the library's build cache is for, which needs no internet, or develop
# (anything it needs that the cache lacks is downloaded). Set FW_REF to choose.
FW_REF=${CI_PIO_DEPS_TAG:-develop}
""" + _PIO_PICK + """[ -d "$HOME/fw/.git" ] || git clone -q --depth 50 https://github.com/meshtastic/firmware "$HOME/fw"
cd "$HOME/fw"
git fetch -q --depth 50 origin "$FW_REF" && git checkout -q FETCH_HEAD
git submodule update -q --init --depth 1
PIO_ENV=native PIO_ENV_FAMILY=native
""" + _PIO_SEED + _PIO_BUILD + """echo "== keeping the program"
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


def _wheelhouse():
    """The Building kit's wheelhouse (root/kits.py: WHEELHOUSE), or None: PlatformIO's wheels,
    so a build's `pip install platformio` needs no internet."""
    path = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits")) / "wheelhouse"
    return path if path.is_dir() and any(path.iterdir()) else None


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
                env["CI_PIO_DEPS_TAG"] = "v" + deps.parent.name
                say(f"CI_PIO_DEPS={deps} (the librarian's build cache, for v{deps.parent.name})")
            wheelhouse = _wheelhouse()
            if wheelhouse:
                env["PIP_NO_INDEX"] = "1"
                env["PIP_FIND_LINKS"] = str(wheelhouse)
                say(f"PIP_FIND_LINKS={wheelhouse} (the Building kit's wheelhouse)")
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
    """Every queued job until the queue is empty (more may arrive meanwhile): pushes, Build now and
    run changes first, oldest first; then the Firmware Factory's, one at a time, in its order
    (factory.order: moved up, then the family built last, then the oldest)."""
    from irate_box.hub import factory  # it imports this module
    WORK.mkdir(exist_ok=True)
    prepare_git()
    while True:
        jobs = []
        for path in sorted(QUEUE.glob("*.json")):
            try:
                jobs.append((path, json.loads(path.read_text())))
            except (OSError, ValueError):
                path.unlink(missing_ok=True)
        if not jobs:
            return 0
        plain = [(p, j) for p, j in jobs if j.get("kind") != "firmware"]
        if plain:
            path, job = plain[0]
            path.unlink(missing_ok=True)
            if job.get("change"):
                change_run(job)
            else:
                build(job)
            continue
        firmware = factory.order([dict(j, _path=str(p)) for p, j in jobs], factory.last_family())
        job = firmware[0]
        Path(job.pop("_path")).unlink(missing_ok=True)
        build_firmware(job)


# --- the Firmware Factory's builds (factory.py queues them) ------------------------------------

FACTORY = "firmware-factory"     # runs/firmware-factory/<n>
# PlatformIO's build cache (PLATFORMIO_BUILD_CACHE_DIR, SCons' CacheDir): an object whose source and
# whole command line were compiled before, by any target, is copied rather than compiled again, so
# a run of builds goes faster than each alone (Tom, 2026-10-07). Meshtastic's flags are stable for a
# day (BUILD_EPOCH is midnight's) but name the target (APP_ENV), so most sharing is the same target
# again, and whatever of the framework and libraries compiles alike for boards of one family.
# Nothing prunes it, so the builder does, oldest first, past the limit. Never for an offline proof.
BUILD_CACHE_MAX = int(os.environ.get("HUB_CI_BUILD_CACHE_MB", 2048)) << 20


def build_cache():
    return Path(os.environ.get("HOME", "/nonexistent")) / "pio-build-cache"


def prune_build_cache(limit=None):
    """Oldest files out until the cache is under its limit. Returns (bytes before, bytes after)."""
    limit = BUILD_CACHE_MAX if limit is None else limit
    root = build_cache()
    files = [(f.stat().st_mtime, f.stat().st_size, f) for f in root.rglob("*") if f.is_file() and not f.is_symlink()] if root.is_dir() else []
    before = total = sum(sz for _, sz, _ in files)
    for _, sz, f in sorted(files):
        if total <= limit:
            break
        f.unlink(missing_ok=True)
        total -= sz
    return before, total
FACTORY_KEEP = 2                 # each target's newest runs kept (and any marked keep)
ENV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
FAMILY_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# What a target's build leaves that is worth keeping: flash images, update zips, UF2 and hex files,
# and native's program. Not the .elf or the map.
# .mt.json: the firmware's manifest of its files (bin/platformio-custom.py), what the web flasher reads.
ARTIFACT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*\.(bin|uf2|hex|zip|mt\.json)$|^(meshtasticd|program)$")
FIRMWARE_SCRIPT = """#!/bin/bash
# A Firmware Factory build (factory.py): one PlatformIO environment, $FW_ENV, of a source cloned
# from the box's own copy at the commit asked for; ci.py keeps its files and what it used.
set -euo pipefail
""" + _PIO_PICK + 'PIO_ENV=$FW_ENV PIO_ENV_FAMILY=$FW_FAMILY\n' + _PIO_SEED + _PIO_BUILD


# An offline build (the Firmware Factory's "Test offline"): the submodules and the build run in a
# network namespace of their own, with only loopback (an unprivileged user namespace: hubci's own
# uid, mapped to itself). Nothing can be downloaded, so a pass proves the box builds it offline.
OFFLINE = ["unshare", "--user", "--map-current-user", "--net", "--"]


def workspace(source):
    """A Factory source's kept PlatformIO workspace, in the builder's home."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", str(source or "unknown"))[:64] or "unknown"
    return Path(os.environ.get("HOME", "/nonexistent")) / "workspace" / name


def _mirror_paths():
    """The local copies the mirrors' URLs point at (mirror-urls.json): what the factory may build."""
    try:
        urls = json.loads(MIRROR_URLS.read_text())
    except (OSError, ValueError):
        return set()
    return {str(Path(u[7:]).resolve()) for u in urls.values() if isinstance(u, str) and u.startswith("file:///")}


class Usage(threading.Thread):
    """What one build used: wall and CPU time, peak memory (its unit's cgroup, sampled every few
    seconds), disk read and written, the card's free space before and after, the hottest the board
    got, and what the box received over the network meanwhile (its interfaces' counters, so
    everything the box received, not only the build: an upper bound). Not IPAccounting: on the Lyra's
    vendor kernel systemd cannot attach its cgroup programs (bpf-firewall, error 524), so it reads 0
    whatever a build downloads, and IPAddressDeny blocks nothing (found 2026-10-07). Whether a build
    ran offline is not inferred from this: an offline build runs with no network at all (OFFLINE)."""

    def __init__(self, every=5):
        super().__init__(daemon=True)
        self.every, self.stop_ev = every, threading.Event()
        self.peak, self.hottest = 0, None
        self.cg = self._cgroup()

    @staticmethod
    def _cgroup():
        try:
            for line in Path("/proc/self/cgroup").read_text().splitlines():
                if line.startswith("0::"):
                    f = Path("/sys/fs/cgroup") / line[3:].lstrip("/") / "memory.current"
                    return f if f.is_file() else None
        except OSError:
            pass
        return None

    @staticmethod
    def temperature():
        temps = []
        for z in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
            try:
                temps.append(int(z.read_text()) / 1000)
            except (OSError, ValueError):
                pass
        return max(temps) if temps else None

    @staticmethod
    def network():
        """Bytes the box's interfaces (not loopback) have received, or None."""
        try:
            lines = Path("/proc/net/dev").read_text().splitlines()[2:]
            return sum(int(l.split(":", 1)[1].split()[0]) for l in lines if l.split(":", 1)[0].strip() != "lo")
        except (OSError, ValueError, IndexError):
            return None

    def _sample(self):
        if self.cg:
            try:
                self.peak = max(self.peak, int(self.cg.read_text()))
            except (OSError, ValueError):
                pass
        t = self.temperature()
        if t is not None:
            self.hottest = t if self.hottest is None else max(self.hottest, t)

    def run(self):
        while not self.stop_ev.wait(self.every):
            self._sample()

    def __enter__(self):
        import resource
        self.r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        self.t0, self.free0, self.net0 = time.time(), shutil.disk_usage(ROOT).free, self.network()
        self._sample()
        self.start()
        return self

    def __exit__(self, *exc):
        import resource
        self.stop_ev.set()
        self._sample()
        r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        net1 = self.network()
        self.result = {
            "wall": round(time.time() - self.t0),
            "cpu": round((r1.ru_utime - self.r0.ru_utime) + (r1.ru_stime - self.r0.ru_stime)),
            "peak_memory": self.peak or r1.ru_maxrss * 1024,
            "read_bytes": (r1.ru_inblock - self.r0.ru_inblock) * 512,
            "written_bytes": (r1.ru_oublock - self.r0.ru_oublock) * 512,
            "free_before": self.free0, "free_after": shutil.disk_usage(ROOT).free,
            "hottest": self.hottest,
            "received": None if self.net0 is None or net1 is None else max(0, net1 - self.net0)}
        return False


def build_firmware(job):
    """One Firmware Factory target: the source's local copy (a mirror, or a private repository) at
    the commit, cloned with its submodules from the mirrors, PlatformIO run for the environment,
    its files kept, and what the build used recorded."""
    repo = Path(job.get("repo", "")).resolve()
    if not (str(repo) in _mirror_paths() or repo.parent == PRIVATE.resolve()):
        return  # only the owner's mirrors and private repositories: never a guest's
    env_name, family = job.get("env", ""), job.get("family", "")
    if not (ENV_RE.match(env_name) and FAMILY_RE.match(family) and re.fullmatch(r"[0-9a-f]{40}", job.get("commit", ""))):
        return
    runs = RUNS / FACTORY
    runs.mkdir(parents=True, exist_ok=True)
    run = runs / str(_next_number(runs))
    (run / "artifacts").mkdir(parents=True)
    started = time.time()
    keep = {k: job.get(k) for k in ("source", "ref", "commit", "env", "family", "name", "batch", "queued")}
    keep["tools_only"] = job.get("tools_only") is True
    offline = keep["offline"] = job.get("offline") is True and not keep["tools_only"]
    netns = OFFLINE if offline else []
    _write_status(run, kind="firmware", repo=FACTORY, branch=job.get("ref"), started=started, state="running", **keep)
    src = WORK / FACTORY
    shutil.rmtree(src, ignore_errors=True)
    state, usage, work_bytes = "failed", None, None
    with open(run / "log.txt", "w") as log:
        def say(text):
            log.write(f"== {text}\n")
            log.flush()
        say(f"{job.get('source')} {job.get('ref')} ({job['commit'][:7]}): {env_name}, family {family}, on {os.uname().nodename}")
        try:
            out = _git("clone", "--quiet", "--no-checkout", str(repo), str(src))
            if out.returncode != 0:
                raise RuntimeError(f"clone failed: {out.stderr.strip()}")
            out = _git("checkout", "--quiet", job["commit"], cwd=src)
            if out.returncode != 0:
                raise RuntimeError(f"checkout failed: {out.stderr.strip()}")
            if offline:
                probe = subprocess.run([*OFFLINE, "true"], capture_output=True, text=True)
                if probe.returncode != 0:
                    raise RuntimeError(f"cannot run without the network here (unshare: {probe.stderr.strip()[:200]})")
                say("offline: no network at all (a network namespace of its own), from the submodules on")
            out = subprocess.run([*netns, "git", "submodule", "update", "--init", "--recursive", "--depth", "1"], cwd=src,
                                 capture_output=True, text=True, timeout=3600)
            if offline and out.returncode != 0:
                raise RuntimeError(f"the submodules need the network: {out.stderr.strip()[-300:]}")
            # The project's .pio (its libraries, and what it built) kept outside the source, which is
            # removed after each run: a target's libraries stay for its next build, offline or not,
            # and a rebuild is incremental. As Meshtastic's build containers do (PLATFORMIO_WORKSPACE_DIR).
            ws = workspace(job.get("source"))
            ws.mkdir(parents=True, exist_ok=True)
            # The build folder is kept: the files an earlier build made there (another version's) are
            # not this one's, so they go first; its compiled objects stay.
            for f in (ws / "build" / env_name).iterdir() if (ws / "build" / env_name).is_dir() else []:
                if f.is_file() and ARTIFACT_RE.match(f.name):
                    f.unlink()
            if offline and (ws / "build" / env_name).exists():
                # An offline proof compiles everything: only the libraries and tools are taken as given.
                shutil.rmtree(ws / "build" / env_name, ignore_errors=True)
                say("offline: this target's earlier build output removed, so all of it is compiled now")
            env = dict(os.environ, CI="1", CI_REPO=FACTORY, CI_BRANCH=str(job.get("ref")), CI_COMMIT=job["commit"],
                       CI_ARTIFACTS=str(run / "artifacts"), FW_ENV=env_name, FW_FAMILY=family,
                       FW_TOOLS_ONLY="1" if keep["tools_only"] else "", PLATFORMIO_WORKSPACE_DIR=str(ws))
            env.pop("PLATFORMIO_BUILD_CACHE_DIR", None)
            if offline:
                say("offline: no build cache either, so every object is compiled here and now")
            elif not keep["tools_only"]:
                build_cache().mkdir(parents=True, exist_ok=True)
                env["PLATFORMIO_BUILD_CACHE_DIR"] = str(build_cache())
            deps = _pio_deps()
            if deps:
                env["CI_PIO_DEPS"] = str(deps)
                env["CI_PIO_DEPS_TAG"] = "v" + deps.parent.name
            wheelhouse = _wheelhouse()
            if wheelhouse:
                # Fetching tools is going online on purpose: the wheelhouse first, and PyPI for what
                # it lacks (Meshtastic's espressif32 makes its own Python environment with uv, which
                # the wheelhouse hasn't: refused with PIP_NO_INDEX on the Lyra, 2026-10-07).
                if not keep["tools_only"]:
                    env["PIP_NO_INDEX"] = "1"
                env["PIP_FIND_LINKS"] = str(wheelhouse)
            say(f"{'fetching the tools for' if keep['tools_only'] else 'building'} {env_name} (time limit {TIME_LIMIT // 60} min)")
            with Usage() as usage:
                proc = subprocess.run([*netns, "bash", "-c", FIRMWARE_SCRIPT], cwd=src, env=env, stdout=log, stderr=subprocess.STDOUT,
                                      stdin=subprocess.DEVNULL, timeout=TIME_LIMIT)
            state = "passed" if proc.returncode == 0 else "failed"
            built = ws / "build" / env_name
            kept = []
            for f in sorted(built.iterdir()) if built.is_dir() else []:
                if f.is_file() and not f.is_symlink() and ARTIFACT_RE.match(f.name):
                    shutil.copy2(f, run / "artifacts" / f.name)
                    kept.append(f.name)
            work_bytes = sum(f.stat().st_size for d in (ws / "build" / env_name, ws / "libdeps" / env_name) if d.is_dir()
                             for f in d.rglob("*") if f.is_file() and not f.is_symlink())
            say(f"PlatformIO exited with {proc.returncode}; kept {', '.join(kept) or 'nothing'}")
        except subprocess.TimeoutExpired:
            state = "timed out"
            say(f"stopped: over the time limit of {TIME_LIMIT // 60} min")
        except (RuntimeError, OSError) as exc:
            say(str(exc))
        finally:
            shutil.rmtree(src, ignore_errors=True)
    res = dict(usage.result, work_bytes=work_bytes) if usage and hasattr(usage, "result") else None
    if res is not None:
        # PlatformIO's own folder afterwards: the toolchains and platforms every build shares.
        pio_home = Path(os.environ.get("HOME", "/nonexistent")) / ".platformio"
        res["tools_bytes"] = sum(f.stat().st_size for f in pio_home.rglob("*") if f.is_file() and not f.is_symlink()) if pio_home.is_dir() else 0
        # The objects found in the build cache rather than compiled (PlatformIO says "Retrieved"),
        # and the cache's size after pruning.
        try:
            log_text = (run / "log.txt").read_text(errors="replace")
            res["compiled"] = log_text.count("\nCompiling ")
            res["from_cache"] = log_text.count("Retrieved `")
        except OSError:
            pass
        res["build_cache_bytes"] = prune_build_cache()[1]
    _write_status(run, state=state, finished=time.time(), duration=round(time.time() - started), resources=res,
                  artifacts=sorted(p.name for p in (run / "artifacts").iterdir() if p.is_file()))
    prune_factory(runs)


def prune_factory(runs):
    """Each target's newest FACTORY_KEEP runs, and any marked keep; never one under way."""
    by_env = {}
    for p in sorted((p for p in runs.iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
        by_env.setdefault(_status(p).get("env"), []).append(p)
    for group in by_env.values():
        loose = [p for p in group if not _status(p).get("keep") and _status(p).get("state") != "running"]
        for old in loose[:-FACTORY_KEEP]:
            shutil.rmtree(old, ignore_errors=True)


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
            "mirrored": mirrors, "pio_deps": bool(_pio_deps()), "wheelhouse": bool(_wheelhouse()), "env": ENV_VARS,
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
