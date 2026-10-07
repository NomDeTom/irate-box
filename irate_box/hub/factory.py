# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Firmware Factory (next-work plan step 36, git-ci-plan §4b): choose a source, a ref and
targets; queue them; the builder (ci.py, as hubci) builds one at a time.

Sources: the owner's mirrors (library/mirrors.json: meshtastic/firmware, its forks) and private
repositories, any of them holding a platformio.ini. Never a public repository a guest can push to.

Targets: a source's PlatformIO environments at a ref, read from its platformio.ini and the files
its `extra_configs` globs name (meshtastic/firmware: variants/*/*/platformio.ini and the rest),
all read in one `git cat-file --batch`, and cached per commit. Each target has its family (the
folder under variants/ its section, or the section it extends, comes from: esp32s3, nrf52840,
native …) and what the file says of it (custom_meshtastic_display_name, support level,
board_level).

The queue is ci.py's: one job per target, {"kind": "firmware", repo, ref, commit, env, family,
batch, queued}. The builder takes them one at a time, a target moved up first, then the family it
built last (so a toolchain stays warm), then the oldest. Pausing stops the builder taking
firmware jobs (other builds go on: its jobs wait in HELD meanwhile); cancelling removes a waiting
job. Each build records what it
used (ci.py: resources), and the estimates here come from those records. Stdlib only.
"""

import configparser
import fnmatch
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

from irate_box.hub import ci, gitrepos

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CACHE = STATE / "factory-targets"            # {commit}.json: the targets at that commit
# Paused, the factory's waiting jobs wait here, out of the builder's queue: the queue's path unit
# starts the builder whenever the queue is not empty, so jobs it may not take must not be in it.
PAUSED = STATE / "factory-paused"
HELD = STATE / "factory-held"
RUNS_NAME = "firmware-factory"              # ci.RUNS/firmware-factory/<n>: the factory's runs
MIRRORS = STATE / "library" / "mirrors.json"
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]{0,99}$")
ENV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
MAX_PER_REQUEST = 50     # a sanity cap until the tracking says more (Tom: limits "played by ear")
MAX_FILES = 2000
MAX_BYTES = 8 << 20


def _git(*args, cwd=None, inp=None, timeout=120):
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=cwd, input=inp, capture_output=True,
                          timeout=timeout)


def sources():
    """[{name, area, path, mirror}] the factory may build from: mirrors, then private repositories."""
    out, seen = [], set()
    try:
        mirrors = json.loads(MIRRORS.read_text()).get("mirrors", [])
    except (OSError, ValueError, AttributeError):
        mirrors = []
    for m in mirrors:
        name, area = str(m.get("name", "")), m.get("area", "public")
        path = gitrepos.ROOT / area / f"{name}.git"
        if gitrepos.NAME_RE.match(name) and area in gitrepos.AREAS and (path / "HEAD").exists():
            out.append({"name": name, "area": area, "path": str(path), "mirror": True, "upstream": m.get("upstream", "")})
            seen.add(str(path))
    private = gitrepos.ROOT / "private"
    for p in sorted(private.glob("*.git")) if private.is_dir() else []:
        if str(p) not in seen and gitrepos.NAME_RE.match(p.name[:-4]) and (p / "HEAD").exists():
            out.append({"name": p.name[:-4], "area": "private", "path": str(p), "mirror": False, "upstream": ""})
    return out


def source(name):
    """The source called `name`, or ValueError: only a mirror or a private repository."""
    for s in sources():
        if s["name"] == name:
            return s
    raise ValueError("source: a mirror or a private repository on this box")


def refs(src):
    """Its tags (newest first) and branches, each with its commit: what a build can use."""
    out = _git("for-each-ref", "--sort=-creatordate", "--format=%(refname)%09%(objectname)%09%(*objectname)",
               "refs/tags", "refs/heads", cwd=src["path"])
    tags, branches = [], []
    for line in out.stdout.decode("utf-8", "replace").splitlines():
        ref, obj, peeled = (line.split("\t") + ["", ""])[:3]
        commit = peeled or obj
        if ref.startswith("refs/tags/"):
            tags.append({"ref": ref[10:], "commit": commit})
        elif ref.startswith("refs/heads/"):
            branches.append({"ref": ref[11:], "commit": commit})
    return {"tags": tags, "branches": branches}


def resolve(src, ref):
    if not REF_RE.match(ref or "") or ".." in ref:
        raise ValueError("ref: a tag or a branch")
    for kind in ("tags", "heads"):
        out = _git("rev-parse", "--verify", "--quiet", f"refs/{kind}/{ref}^{{commit}}", cwd=src["path"])
        if out.returncode == 0:
            return out.stdout.decode().strip()
    raise ValueError(f"{src['name']} keeps no tag or branch {ref}")


def _seg_match(pattern, path):
    """A glob from extra_configs against a path, segment by segment ('*' never crosses a '/')."""
    p, q = pattern.strip("/").split("/"), path.split("/")
    return len(p) == len(q) and all(fnmatch.fnmatchcase(b, a) for a, b in zip(p, q))


def _read_blobs(repo, commit, paths):
    """{path: text} for the given paths at commit, in one `git cat-file --batch`."""
    out = _git("cat-file", "--batch", cwd=repo, inp="".join(f"{commit}:{p}\n" for p in paths).encode(), timeout=300).stdout
    texts, i = {}, 0
    for p in paths:
        nl = out.index(b"\n", i)
        head = out[i:nl].split()
        i = nl + 1
        if len(head) == 3 and head[1] == b"blob":
            size = int(head[2])
            texts[p] = out[i:i + size].decode("utf-8", "replace")
            i += size + 1
    return texts


def _family(sec, where, parser, depth=0):
    """The folder under variants/ the section (or what it extends) comes from, or None."""
    f = where.get(sec, "")
    m = re.match(r"^variants/([A-Za-z0-9_-]+)/", f)
    if m:
        return m.group(1)
    if depth > 12 or not parser.has_section(sec):
        return None
    ext = parser.get(sec, "extends", fallback="").split(",")[0].strip()
    return _family(ext, where, parser, depth + 1) if ext else None


def targets(src, ref):
    """{commit, families: {family: n}, targets: [{env, family, name, level, support, file}]}, cached per commit."""
    commit = resolve(src, ref)
    cached = CACHE / f"{commit}.json"
    try:
        return json.loads(cached.read_text())
    except (OSError, ValueError):
        pass
    names = _git("ls-tree", "-r", "--name-only", commit, cwd=src["path"]).stdout.decode("utf-8", "replace").splitlines()
    if "platformio.ini" not in names:
        raise ValueError(f"{src['name']} has no platformio.ini at {ref}")
    root = _read_blobs(src["path"], commit, ["platformio.ini"]).get("platformio.ini", "")
    top = configparser.ConfigParser(interpolation=None, strict=False, inline_comment_prefixes=(";",))
    top.read_string(root)
    globs = [g.strip() for g in top.get("platformio", "extra_configs", fallback="").splitlines() if g.strip()]
    files = [n for n in names if any(_seg_match(g, n) for g in globs)][:MAX_FILES]
    texts = _read_blobs(src["path"], commit, files)
    parser = configparser.ConfigParser(interpolation=None, strict=False, inline_comment_prefixes=(";",))
    where = {}
    for f in ["platformio.ini"] + files:
        text = root if f == "platformio.ini" else texts.get(f, "")
        if len(text) > MAX_BYTES:
            continue
        one = configparser.ConfigParser(interpolation=None, strict=False, inline_comment_prefixes=(";",))
        try:
            one.read_string(text)
        except configparser.Error:
            continue
        for sec in one.sections():
            where.setdefault(sec, f)
            if not parser.has_section(sec):
                parser.add_section(sec)
            for k, v in one.items(sec, raw=True):
                parser.set(sec, k, v)
    out = []
    for sec in parser.sections():
        if not sec.startswith("env:") or not ENV_RE.match(sec[4:]):
            continue
        g = lambda k: parser.get(sec, k, fallback="").strip()  # noqa: E731
        out.append({"env": sec[4:], "family": _family(sec, where, parser) or "other", "file": where.get(sec, ""),
                    "name": g("custom_meshtastic_display_name") or sec[4:], "level": g("board_level"),
                    "support": g("custom_meshtastic_support_level")})
    out.sort(key=lambda t: (t["family"], t["env"]))
    fams = {}
    for t in out:
        fams[t["family"]] = fams.get(t["family"], 0) + 1
    data = {"source": src["name"], "ref": ref, "commit": commit, "families": fams, "targets": out}
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_name(cached.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, cached)
    return data


# --- the queue -------------------------------------------------------------------------------

def _files():
    return [p for d in (ci.QUEUE, HELD) if d.is_dir() for p in sorted(d.glob("*.json"))]


def _put(job):
    """Into the builder's queue, or held while paused."""
    if not PAUSED.exists():
        ci._put(job)
        return
    HELD.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{secrets.token_hex(3)}.json"
    tmp = HELD / f".{name}"
    tmp.write_text(json.dumps(job))
    os.replace(tmp, HELD / name)


def _jobs():
    """The waiting firmware jobs (held ones too), in the order the builder would take them."""
    jobs = []
    for p in _files():
        try:
            j = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if j.get("kind") == "firmware":
            j["id"] = p.name[:-5]
            jobs.append(j)
    return order(jobs, last_family())


def order(jobs, last):
    """The builder's order: moved up first, then the family it built last, then the oldest."""
    return sorted(jobs, key=lambda j: (not j.get("up"), j.get("family") != last, j.get("queued", 0)))


def last_family():
    runs = ci.RUNS / RUNS_NAME
    best = None
    for st in runs.glob("*/status.json") if runs.is_dir() else []:
        try:
            s = json.loads(st.read_text())
        except (OSError, ValueError):
            continue
        if best is None or s.get("started", 0) > best.get("started", 0):
            best = s
    return (best or {}).get("family")


def free_bytes():
    try:
        return shutil.disk_usage(ci.ROOT).free
    except OSError:
        return 0


def queue(name, ref, envs, floor_mb=512):
    """Queue each of envs from source `name` at `ref`: one job per target, ordered by family."""
    src = source(name)
    if not isinstance(envs, list) or not envs:
        raise ValueError("targets: one or more")
    if len(envs) > MAX_PER_REQUEST:
        raise ValueError(f"at most {MAX_PER_REQUEST} targets in one request, for now")
    data = targets(src, ref)
    known = {t["env"]: t for t in data["targets"]}
    bad = [e for e in envs if e not in known]
    if bad:
        raise ValueError(f"not targets of {name} at {ref}: {', '.join(map(str, bad[:5]))}")
    if free_bytes() < floor_mb << 20:
        raise ValueError(f"the card has under {floor_mb} MB free: builds wait until there is room")
    ci.QUEUE.mkdir(parents=True, exist_ok=True)
    batch, now = secrets.token_hex(4), time.time()
    picked = sorted(dict.fromkeys(envs), key=lambda e: (known[e]["family"], e))
    for i, env in enumerate(picked):
        _put({"kind": "firmware", "repo": src["path"], "source": name, "ref": ref, "commit": data["commit"],
                 "env": env, "family": known[env]["family"], "name": known[env]["name"], "batch": batch,
                 "queued": now + i / 1000})
    return {"batch": batch, "queued": len(picked), "commit": data["commit"]}


def queue_tools(name, ref, family):
    """Fetch a family's tools (git-ci-plan §4b item 7): PlatformIO installs what one of its targets
    needs (platform, toolchain, framework, libraries) with nothing compiled, so a later build of
    that family needs no internet; and whether PlatformIO has these tools for this board's processor
    at all is known in minutes, not after hours of compiling."""
    src = source(name)
    data = targets(src, ref)
    fam = [t for t in data["targets"] if t["family"] == family]
    if not fam:
        raise ValueError(f"no family {family} in {name} at {ref}")
    pick = next((t for t in fam if t["level"] == "pr"), fam[0])
    ci.QUEUE.mkdir(parents=True, exist_ok=True)
    _put({"kind": "firmware", "tools_only": True, "repo": src["path"], "source": name, "ref": ref, "commit": data["commit"],
          "env": pick["env"], "family": family, "name": f"{family}'s tools (by {pick['env']})", "batch": secrets.token_hex(4),
          "queued": time.time()})
    return {"queued": 1, "env": pick["env"], "commit": data["commit"]}


def _job_path(job_id):
    if not re.fullmatch(r"[0-9]{6,24}-[0-9a-f]{6}", job_id or ""):
        raise ValueError("not a waiting build")
    p = next((d / f"{job_id}.json" for d in (ci.QUEUE, HELD) if (d / f"{job_id}.json").exists()), ci.QUEUE / f"{job_id}.json")
    try:
        j = json.loads(p.read_text())
    except (OSError, ValueError):
        raise ValueError("not a waiting build (it may have started)")
    if j.get("kind") != "firmware":
        raise ValueError("not a firmware build")
    return p, j


def cancel(job_id):
    p, _ = _job_path(job_id)
    p.unlink(missing_ok=True)


def move_up(job_id):
    p, j = _job_path(job_id)
    j["up"] = time.time()
    tmp = p.with_name("." + p.name)
    tmp.write_text(json.dumps(j))
    os.replace(tmp, p)


def pause(on):
    """Paused: the waiting firmware jobs move to HELD (the build under way finishes). Resumed:
    they go back, and the queue's path unit wakes the builder."""
    if on:
        PAUSED.parent.mkdir(parents=True, exist_ok=True)
        PAUSED.write_text(str(int(time.time())))
        HELD.mkdir(parents=True, exist_ok=True)
        for p in sorted(ci.QUEUE.glob("*.json")) if ci.QUEUE.is_dir() else []:
            try:
                if json.loads(p.read_text()).get("kind") == "firmware":
                    os.replace(p, HELD / p.name)
            except (OSError, ValueError):
                continue
    else:
        PAUSED.unlink(missing_ok=True)
        for p in sorted(HELD.glob("*.json")) if HELD.is_dir() else []:
            try:
                job = json.loads(p.read_text())
            except (OSError, ValueError):
                p.unlink(missing_ok=True)
                continue
            ci._put(job)
            p.unlink(missing_ok=True)


# --- what the page shows ----------------------------------------------------------------------

def runs(limit=30):
    out = []
    base = ci.RUNS / RUNS_NAME
    for st in base.glob("*/status.json") if base.is_dir() else []:
        try:
            s = json.loads(st.read_text())
        except (OSError, ValueError):
            continue
        s["run"] = f"{RUNS_NAME}/{st.parent.name}"
        out.append(s)
    out.sort(key=lambda s: s.get("started", 0), reverse=True)
    return out[:limit]


def estimates(history):
    """Per family, from its newest finished build here: how long, and how much disk it used."""
    est = {}
    for s in history:
        f, res = s.get("family"), s.get("resources") or {}
        if f and f not in est and s.get("state") == "passed" and s.get("duration") is not None and not s.get("tools_only"):
            est[f] = {"seconds": s["duration"], "disk": res.get("work_bytes"), "peak_memory": res.get("peak_memory"),
                      "built": s.get("finished")}
    return est


def readiness(history):
    """Per family: "built here" once one has passed, else "untested on this box"; a family whose
    last build here downloaded nothing is "offline ready"; one whose tools were fetched (and not
    yet built) says so, with their size; one whose tools could not be fetched says that."""
    out, tools = {}, {}
    for s in history:
        f = s.get("family")
        if not f:
            continue
        if s.get("tools_only"):
            if f not in tools and s.get("state") in ("passed", "failed"):
                res = s.get("resources") or {}
                tools[f] = (f"tools fetched ({round((res.get('received') or 0) / 2 ** 20)} MB downloaded), not yet built"
                            if s["state"] == "passed" else "its tools could not be fetched here: see the log")
            continue
        if f in out or s.get("state") != "passed":
            continue
        out[f] = "offline ready" if (s.get("resources") or {}).get("offline") else "built here (needed the internet)"
    return {**tools, **out}


def snapshot():
    history = runs(200)
    jobs = _jobs()
    est = estimates(history)
    running = next((s for s in history if s.get("state") == "running"), None)
    # When each waiting build should start and finish: only while every build before it has an
    # estimate (a family built here before); after the first unknown one, nothing is promised.
    t, known = time.time(), True
    if running:
        e = est.get(running.get("family"), {}).get("seconds")
        t, known = (max(t, running.get("started", t) + e), True) if e is not None else (t, False)
    waiting = []
    for j in jobs:
        e = est.get(j.get("family"), {}).get("seconds")
        waiting.append({k: j.get(k) for k in ("id", "source", "ref", "commit", "env", "family", "name", "queued", "up", "batch")}
                       | {"start": round(t) if known else None, "finish": round(t + e) if known and e is not None else None})
        if known and e is not None:
            t += e
        else:
            known = False
    return {"sources": [{k: s[k] for k in ("name", "area", "mirror", "upstream")} for s in sources()],
            "paused": PAUSED.exists(), "running": running, "waiting": waiting, "runs": history[:30],
            "estimates": est, "readiness": readiness(history), "free": free_bytes(), "max_per_request": MAX_PER_REQUEST,
            "flasher": published()}


# --- the front page's tile (the owner's choice: settings factory_tile) -------------------------------
# What a guest may download: an ESP32 build's images and update zip, and any UF2 (nRF52, RP2040:
# dragged onto the board's USB drive). Not native's program, nor a log.
PUBLIC_RE = {"esp32": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*\.(bin|zip)$"), "any": re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*\.uf2$")}


def public_files(run):
    fam = str(run.get("family") or "")
    return [n for n in run.get("artifacts") or []
            if PUBLIC_RE["any"].match(n) or (fam.startswith("esp32") and PUBLIC_RE["esp32"].match(n))]


def public_view(limit=12):
    """The tile's view: what is building and how far along, what is waiting, and each target's newest
    good build with only what a guest may download. Nothing else of the runs (no logs, no sources
    beyond the release or branch name)."""
    history = runs(200)
    est = estimates(history)
    running = next((s for s in history if s.get("state") == "running"), None)
    jobs = _jobs()
    built, seen = [], set()
    for s in history:
        if s.get("state") != "passed" or s.get("env") in seen:
            continue
        seen.add(s.get("env"))
        files = public_files(s)
        if files:
            built.append({"run": s["run"].split("/")[1], "env": s.get("env"), "name": s.get("name") or s.get("env"),
                          "family": s.get("family"), "ref": s.get("ref"), "finished": s.get("finished"), "files": files})
        if len(built) >= limit:
            break
    now_ = None
    if running:
        e = est.get(running.get("family"), {}).get("seconds")
        now_ = {"name": running.get("name") or running.get("env"), "env": running.get("env"), "family": running.get("family"),
                "ref": running.get("ref"), "started": running.get("started"),
                "done": round(min(0.99, (time.time() - running["started"]) / e), 2) if e and running.get("started") else None}
    return {"running": now_, "waiting": len(jobs), "next": [{"name": j.get("name") or j.get("env"), "family": j.get("family")} for j in jobs[:5]],
            "paused": PAUSED.exists(), "built": built}


def public_file(run_number, name):
    """A built file a guest may download, or None."""
    if not re.fullmatch(r"[0-9]{1,6}", run_number or ""):
        return None
    run = ci.RUNS / RUNS_NAME / run_number
    try:
        st = json.loads((run / "status.json").read_text())
    except (OSError, ValueError):
        return None
    if st.get("state") != "passed" or name not in public_files(st):
        return None
    f = run / "artifacts" / name
    return f if f.is_file() and not f.is_symlink() else None


# --- the web flasher (git-ci-plan §4b item 5: the owner's choice, build by build) --------------------
# A passed build's manifest (firmware-<env>-<version>.mt.json, which the firmware's own
# bin/platformio-custom.py writes) and the files it names are copied to <firmware root>/<version>-built/,
# laid out as release.meshtastic.org's folders are, with that folder's board list
# (firmware-<version>-built.json). flasher.py lists each such folder as a release built on this box,
# beside the librarian's. The suffix keeps it clear of the librarian, which removes only the
# version-shaped folders it no longer keeps, and tells the two apart in the flasher's list.
FIRMWARE = Path(os.environ.get("HUB_FIRMWARE_ROOT", "/var/lib/hub/firmware"))
BUILT = "-built"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9a-f]{7,40}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,127}$")


def _run(run_number):
    if not re.fullmatch(r"[0-9]{1,6}", str(run_number or "")):
        raise ValueError("run: a factory run's number")
    run = ci.RUNS / RUNS_NAME / str(run_number)
    try:
        return run, json.loads((run / "status.json").read_text())
    except (OSError, ValueError):
        raise ValueError("no such run")


def _write(path, data):
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, indent=1))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _board_list(folder):
    try:
        return json.loads((folder / f"firmware-{folder.name}.json").read_text())
    except (OSError, ValueError):
        return {"version": folder.name, "targets": []}


def publish(run_number):
    """Offer one passed build in the web flasher. Returns {version (the flasher's release), env}."""
    run, st = _run(run_number)
    if st.get("state") != "passed" or st.get("tools_only"):
        raise ValueError("only a build that passed can go to the flasher")
    arts = set(st.get("artifacts") or [])
    names = [n for n in arts if n.endswith(".mt.json")]
    if len(names) != 1:
        raise ValueError("this build kept no manifest (.mt.json): a native build, or one from before the factory kept them")
    try:
        mt = json.loads((run / "artifacts" / names[0]).read_text())
    except (OSError, ValueError):
        raise ValueError("its manifest cannot be read")
    env, version = st.get("env"), str(mt.get("version", ""))
    if not VERSION_RE.match(version) or mt.get("platformioTarget") != env or not ENV_RE.match(env or ""):
        raise ValueError("its manifest is not this build's")
    # Each file the manifest names, as kept: the nRF52 update zip is named -ota.zip in the manifest
    # (bin/platformio-custom.py) but built as .zip. The .elf (debug only) and the bare program are
    # never kept, and leave the manifest.
    files, plan = [], []
    for entry in mt.get("files") or []:
        name = str(entry.get("name", ""))
        kept = name if name in arts else name[:-len("-ota.zip")] + ".zip" if name.endswith("-ota.zip") else None
        if not FILE_RE.match(name) or kept not in arts:
            continue
        data = (run / "artifacts" / kept).read_bytes()
        if entry.get("md5") and hashlib.md5(data).hexdigest() != entry["md5"]:
            raise ValueError(f"{kept} does not match its manifest's MD5")
        files.append(entry)
        plan.append((name, data))
    if not files:
        raise ValueError("none of the files its manifest names were kept")
    folder = FIRMWARE / (version + BUILT)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o755)
    for name, data in plan:
        tmp = folder / (name + ".part")
        tmp.write_bytes(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, folder / name)
    _write(folder / f"firmware-{env}-{folder.name}.mt.json", dict(mt, files=files, built_run=f"{RUNS_NAME}/{run.name}",
                                                                 built_ref=st.get("ref"), built_source=st.get("source")))
    board_list = _board_list(folder)
    board_list["targets"] = [t for t in board_list.get("targets", []) if t.get("board") != env] + \
                            [{"board": env, "platform": mt.get("architecture") or st.get("family")}]
    board_list["targets"].sort(key=lambda t: t["board"])
    _write(folder / f"firmware-{folder.name}.json", board_list)
    return {"version": folder.name, "env": env}


def unpublish(version, env):
    """Take one target out of a release built here; the release goes with its last target. A file
    another target's manifest also names (an ESP32's shared OTA loader) stays."""
    if not (version.endswith(BUILT) and VERSION_RE.match(version[:-len(BUILT)])) or not ENV_RE.match(env or ""):
        raise ValueError("not a release built here")
    folder = FIRMWARE / version
    mt_path = folder / f"firmware-{env}-{version}.mt.json"
    try:
        mine = {f.get("name") for f in json.loads(mt_path.read_text()).get("files", [])}
    except (OSError, ValueError):
        raise ValueError(f"{env} is not in {version}")
    mt_path.unlink()
    others = set()
    for m in folder.glob(f"firmware-*-{version}.mt.json"):
        try:
            others |= {f.get("name") for f in json.loads(m.read_text()).get("files", [])}
        except (OSError, ValueError):
            pass
    for name in mine - others:
        if isinstance(name, str) and FILE_RE.match(name):
            (folder / name).unlink(missing_ok=True)
    board_list = _board_list(folder)
    board_list["targets"] = [t for t in board_list.get("targets", []) if t.get("board") != env]
    if board_list["targets"]:
        _write(folder / f"firmware-{version}.json", board_list)
    else:
        shutil.rmtree(folder, ignore_errors=True)


def published():
    """The releases built here that the flasher offers, newest first: [{version, targets: [{board,
    platform, run, ref}]}]."""
    out = []
    for folder in FIRMWARE.glob("*" + BUILT) if FIRMWARE.is_dir() else []:
        if not (folder.is_dir() and VERSION_RE.match(folder.name[:-len(BUILT)])):
            continue
        targets = []
        for t in _board_list(folder).get("targets", []):
            try:
                mt = json.loads((folder / f"firmware-{t['board']}-{folder.name}.mt.json").read_text())
            except (OSError, ValueError, KeyError):
                continue
            targets.append(dict(t, run=mt.get("built_run"), ref=mt.get("built_ref"), epoch=mt.get("build_epoch")))
        if targets:
            out.append({"version": folder.name, "targets": targets, "changed": folder.stat().st_mtime})
    out.sort(key=lambda r: r["changed"], reverse=True)
    return out
