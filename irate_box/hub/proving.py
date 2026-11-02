# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Proving runs: at a new Meshtastic release, read what the tag needs, compare it with the box's
build tooling, and (when the owner allows it) build one representative target per family with the
network closed, from the cache alone.

A proving run is a job in ci.py's queue, {"kind": "proving", tag, version, family, env}: after any
push, Build now or Firmware Factory build the owner asked for, so it never delays real work. It
records the tag, the family, the cache's signature and the tool versions, so the Firmware Factory's
page can say "esp32s3: proven offline at v2.8.2 on 2026-11-03".

The comparison is also usable on its own (the Updates/Box doctor's Builds section): a tag's
`platformio.ini` and `requirements*.txt` against PlatformIO (Debian's and the wheelhouse's), the
Building kit's wheelhouse, and the family's cache under $HUB_FIRMWARE_ROOT/<version>/pio-deps/.
A gap is a tooling finding before anything is built. Nothing here downloads or runs PlatformIO;
ci.build_proving does the build, and proving.ensure_cache does the download the owner allowed.
Stdlib only.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from irate_box.finding import finding
from irate_box.msg import M, when

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
FIRMWARE = Path(os.environ.get("HUB_FIRMWARE_ROOT", STATE / "firmware"))
KITS = Path(os.environ.get("HUB_KITS_ROOT", "/var/cache/irate-box/kits"))
PROOFS = STATE / "library" / "proving.json"
MEMGUARD_STATUS = Path(os.environ.get("HUB_MEMGUARD_STATUS", "/run/irate-box-memguard/status.json"))
POWER = Path(os.environ.get("HUB_POWER_SUPPLY", "/sys/class/power_supply"))
TMP = STATE / "library" / "tmp"
BUNDLE_PREFIX = "pio-deps-"
SUMS_NAME = "pio-deps-sha256.json"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\.[0-9a-f]{7,}$")

# The administrator's settings (the Firmware page; stored with the firmware's, in firmware.json).
DEFAULTS = {
    "proving": False,
    "proving_releases": "stable",
    "proving_families": "chosen",
    "proving_when": "idle",
    "proving_tooling": "flag",
    "proving_keep": 3,
    "proving_window_start": "22:00",
    "proving_window_end": "06:00",
}
RELEASES = ("stable", "stable-beta", "all")
WHEN = ("idle", "night", "manual")
TOOLING = ("flag", "fetch", "install")
FAMILIES = ("chosen", "all")

# A representative target per family when the owner has not chosen a board: the same common boards
# factory.suggested() prefers, then this map (the plan's examples), then the family's first target.
DEFAULT_ENV = {
    "esp32": "tbeam",
    "esp32s2": "tbeam-s3-core",
    "esp32s3": "heltec-v3",
    "esp32c3": "rak3172",
    "esp32c6": "t-echo",
    "esp32h2": "t-echo",
    "nrf52840": "rak4631",
    "rp2040": "picow",
    "native": "native",
}


def settings():
    """The proving settings, as the Firmware page keeps them."""
    try:
        from irate_box.library import firmware

        cfg = firmware.settings()
    except (OSError, ValueError, ImportError):
        cfg = {}
    return {key: cfg.get(key, default) for key, default in DEFAULTS.items()}


def set_settings(**changes):
    """Validate and keep the proving settings in the firmware's own file (firmware.set_settings)."""
    from irate_box.library import firmware

    return firmware.set_settings(**{k: v for k, v in changes.items() if k in DEFAULTS})


def report():
    """What the Firmware page and the doctor read: the settings, each family's newest proof, the
    tooling findings of the last comparison, and how many proving runs wait."""
    st = _read()
    return {
        "settings": settings(),
        "proofs": {family: (items[0] if items else None) for family, items in (st.get("proofs") or {}).items()},
        "history": st.get("proofs") or {},
        "tooling": st.get("tooling") or [],
        "checked": st.get("checked"),
        "queued": queued(),
    }


def queued():
    """How many proving jobs wait in the builder's queue."""
    from irate_box.hub import ci

    n = 0
    for p in ci.QUEUE.glob("*.json") if ci.QUEUE.is_dir() else []:
        try:
            n += json.loads(p.read_text()).get("kind") == "proving"
        except (OSError, ValueError):
            continue
    return n


# --- the comparison ---------------------------------------------------------------------------


def families(targets):
    """The families to prove, from the owner's setting and the release's targets."""
    cfg = settings()
    every = sorted({t["family"] for t in targets if t.get("family")})
    chosen = cfg["proving_families"]
    if chosen == "all":
        return every
    if isinstance(chosen, list):
        return [f for f in chosen if f in every]
    try:
        from irate_box.library import firmware

        boards = firmware.settings().get("boards")
    except (OSError, ValueError, ImportError):
        boards = "all"
    if boards == "all" or not boards:
        return every
    wanted = set(boards)
    return sorted({t["family"] for t in targets if t.get("env") in wanted and t.get("family")})


def representative(targets, family, flasher=()):
    """The family's target to prove: the owner's chosen board, a common one, the plan's default,
    then one the project supports best."""
    from irate_box.hub import factory

    ts = [t for t in targets if t.get("family") == family]
    if not ts:
        return None
    envs = {t["env"] for t in ts}
    pick = next((e for e in flasher if e in envs), None)
    if pick is None and DEFAULT_ENV.get(family) in envs:
        pick = DEFAULT_ENV[family]
    if pick is None:
        pick = factory.suggested(ts, flasher).get(family)
    return pick or ts[0]["env"]


def _platform_spec(value):
    """(name, version) from a PlatformIO platform value: espressif32, espressif32@6.10, a URL."""
    value = (value or "").strip()
    if not value:
        return None, None
    if "://" in value or value.startswith("file:"):
        name = value.rstrip("/").rsplit("/", 1)[-1]
        name = re.sub(r"\.git$", "", name)
        return name, None
    name, _, version = value.partition("@")
    return name.strip(), version.strip() or None


def _req_spec(line):
    """(name, operator, version) from a requirements line, or None for a link, option or VCS."""
    line = (line or "").split("#", 1)[0].strip()
    if not line or line.startswith(("-", "git+", "http:", "https:", "file:", ".", "/")):
        return None
    m = re.match(
        r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[[^\]]*\])?\s*(===|==|>=|<=|~=|!=|>|<)?\s*([A-Za-z0-9_.*+!-]+)?$",
        line,
    )
    return (m.group(1), m.group(2) or "", m.group(3) or "") if m else None


def _vtuple(value):
    return tuple(int(x) for x in re.findall(r"\d+", str(value or "")))


def _satisfies(have, op, want):
    if not op:
        return True
    if not have:
        return False
    if want.endswith(".*"):
        return _vtuple(have)[: len(_vtuple(want[:-2]))] == _vtuple(want[:-2])
    a, b = _vtuple(have), _vtuple(want)
    return {
        "===": a == b,
        "==": a == b,
        "!=": a != b,
        ">=": a >= b,
        ">": a > b,
        "<=": a <= b,
        "<": a < b,
        "~=": a[: max(1, len(b) - 1)] == b[: max(1, len(b) - 1)],
    }.get(op, True)


def _name(value):
    return re.sub(r"[-_.]+", "-", str(value or "")).lower()


def wheelhouse_packages():
    """{name: version} from the Building kit's wheelhouse, or None when it is not on this box."""
    root = KITS / "wheelhouse"
    if not root.is_dir():
        return None
    out = {}
    for path in sorted(root.iterdir()):
        if path.suffix == ".whl":
            # A wheel: distribution-version-python-abi-platform.whl (dashes only separate the parts).
            m = re.match(r"^([^-]+)-([^-]+)-.*\.whl$", path.name)
        else:
            m = re.match(r"^(.+?)-([0-9][A-Za-z0-9.!+_-]*)\.(tar\.gz|zip)$", path.name)
        if m:
            out.setdefault(_name(m.group(1)), m.group(2))
    return out


def _command_version(path):
    try:
        out = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=30)  # noqa: TID251
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+(?:\.\d+)+)", out.stdout + out.stderr)
    return m.group(1) if m and out.returncode == 0 else None


def pio_versions():
    """{where: version} for the PlatformIO the box could use: Debian's, and the wheelhouse's."""
    out = {}
    debian = _command_version("/usr/bin/pio")
    if debian:
        out["debian"] = debian
    wheel = _command_version(Path(os.environ.get("HOME", "/root")) / "pio" / "bin" / "pio")
    if wheel:
        out["wheelhouse"] = wheel
    packages = wheelhouse_packages() or {}
    if packages.get("platformio") and "wheelhouse" not in out:
        out["wheelhouse"] = packages["platformio"]
    return out


def cache_dir(family, version):
    """The family's cache for a release version (its tag without the v), or None."""
    version = str(version or "").lstrip("v")
    if not version:
        return None
    base = FIRMWARE / version / "pio-deps"
    for path in (base / family, base if family == "native" else None):
        if path and path.is_dir():
            return path
    return None


def cache_packages(cache):
    """{package name: version} from a cache's PlatformIO packages (platform-x@1.2.3)."""
    out = {}
    for path in (cache / "packages").iterdir() if (cache / "packages").is_dir() else []:
        if not path.is_dir():
            continue
        name, _, version = path.name.partition("@")
        out[_name(name)] = version or None
    return out


def cache_signature(cache):
    """A stable name for what a cache holds: tool packages, their versions, and the download
    archives, so a proof says exactly which cache it used."""
    if not cache or not Path(cache).is_dir():
        return None
    parts = []
    for path in sorted((Path(cache) / "packages").glob("*")) if (Path(cache) / "packages").is_dir() else []:
        if path.is_dir():
            parts.append(path.name)
    downloads = Path(cache) / "core" / ".cache" / "downloads"
    for path in sorted(downloads.glob("*")) if downloads.is_dir() else []:
        if path.is_file():
            parts.append(path.name)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16] if parts else None


def compare(source, ref, family, env=None):
    """Tooling findings for one family at a tag: the platform, libraries and Python requirements
    the tag names against the family's cache and the Building kit."""
    from irate_box.hub import pio_bundle

    try:
        doc = pio_bundle.manifest_at(source, ref)
    except (OSError, ValueError) as exc:
        return [
            _finding(
                "proving.manifest.missing",
                family,
                "warn",
                M("proving.manifest.missing", "The build script could not be read at {ref}.", ref=ref),
                evidence=[str(exc)],
            )
        ]
    if env is None:
        envs = [e for e in doc.get("default_envs", []) if e in doc["envs"]]
        env = envs[0] if envs else next(iter(doc["envs"]), None)
    spec = doc.get("envs", {}).get(env) if env else None
    version = str(doc.get("tag") or ref or "").lstrip("v")
    out = []
    cache = cache_dir(family, version)
    if cache is None:
        return [
            _finding(
                "proving.cache.missing",
                family,
                "warn",
                M(
                    "proving.cache.missing",
                    "There is no {family} cache for {version}: the target cannot be built offline until one is fetched.",
                    family=family,
                    version=version or ref,
                ),
                repair="fetch",
                evidence=[str(FIRMWARE / version / "pio-deps" / family)],
            )
        ]
    if spec is None:
        out.append(
            _finding(
                "proving.target.missing",
                family,
                "warn",
                M("proving.target.missing", "The release has no target {env}.", env=env or ""),
            )
        )
        return out
    packages = cache_packages(cache)
    name, wanted = _platform_spec(spec.get("platform"))
    if name:
        have = next((v for k, v in packages.items() if k == _name(f"platform-{name}")), None)
        if have is None:
            out.append(
                _finding(
                    "proving.platform.missing",
                    family,
                    "warn",
                    M(
                        "proving.platform.missing",
                        "{version} needs the {name} platform. The {family} cache has none.",
                        version=version or ref,
                        name=name,
                        family=family,
                    ),
                    repair="fetch",
                )
            )
        elif wanted and _vtuple(have) != _vtuple(wanted):
            out.append(
                _finding(
                    "proving.platform.version",
                    family,
                    "warn",
                    M(
                        "proving.platform.version",
                        "{version} needs {name} {wanted}. The cache has {have}.",
                        version=version or ref,
                        name=name,
                        wanted=wanted,
                        have=have,
                    ),
                    repair="fetch",
                )
            )
    libdeps = cache / "libdeps" / (env or family)
    libraries = cache / "libdeps" / family
    if spec.get("lib_deps") and not (
        (libdeps.is_dir() and any(libdeps.iterdir())) or (libraries.is_dir() and any(libraries.iterdir()))
    ):
        out.append(
            _finding(
                "proving.libraries.missing",
                family,
                "warn",
                M(
                    "proving.libraries.missing",
                    "{version} needs libraries. The {family} cache has none for {env}.",
                    version=version or ref,
                    family=family,
                    env=env,
                ),
                repair="fetch",
            )
        )
    requirements = doc.get("requirements") or {}
    wheel = wheelhouse_packages()
    lines = [line for values in requirements.values() for line in values]
    if lines and wheel is None:
        out.append(
            _finding(
                "proving.wheelhouse.missing",
                family,
                "warn",
                M(
                    "proving.wheelhouse.missing",
                    "{version} needs Python packages. The Building kit's wheelhouse is not on this box.",
                    version=version or ref,
                ),
                repair="fetch",
            )
        )
    elif wheel is not None:
        for line in lines:
            spec_ = _req_spec(line)
            if not spec_:
                continue
            package, op, want = spec_
            have = wheel.get(_name(package))
            if have is None:
                out.append(
                    _finding(
                        "proving.python.missing",
                        family,
                        "warn",
                        M(
                            "proving.python.missing",
                            "{version} needs {package}{wanted}. The wheelhouse has no {package}.",
                            version=version or ref,
                            package=package,
                            wanted=(op + want) if op else "",
                        ),
                        repair="fetch",
                        evidence=[line],
                    )
                )
            elif not _satisfies(have, op, want):
                out.append(
                    _finding(
                        "proving.python.version",
                        family,
                        "warn",
                        M(
                            "proving.python.version",
                            "{version} needs {package}{wanted}. The wheelhouse has {have}.",
                            version=version or ref,
                            package=package,
                            wanted=(op + want) if op else want,
                            have=have,
                        ),
                        repair="fetch",
                        evidence=[line],
                    )
                )
    return out


def _finding(fid, family, status, summary, why=None, fix=None, repair=None, evidence=()):
    return {
        "id": fid,
        "family": family,
        "status": status,
        "summary": summary,
        "why": why,
        "fix": fix,
        "repair": repair,
        "evidence": [str(e)[:300] for e in evidence],
    }


def as_finding(item):
    """A tooling finding in a doctor's shape (title + status + summary + repair)."""
    return finding(
        item["id"],
        M("proving.title", "Build tooling for {family}", family=item.get("family") or "?"),
        item.get("status") or "warn",
        item.get("summary"),
        item.get("why"),
        item.get("fix")
        or M(
            "proving.tooling.fix",
            "On the Firmware page, let a proving run fetch what is missing, or download it on a box with internet access.",
        ),
        evidence=item.get("evidence") or [],
    )


def findings():
    """The doctor's Builds section: proofs that are behind or failed, and missing tooling."""
    st = _read()
    cfg = settings()
    if not cfg["proving"]:
        return []
    out = []
    latest = st.get("tooling") or []
    for item in latest[:20]:
        if isinstance(item, dict) and item.get("status") != "ok":
            out.append(as_finding(item))
    for family, items in sorted((st.get("proofs") or {}).items()):
        proof = items[0] if items else None
        if not proof:
            out.append(
                finding(
                    f"proving-{family}-none",
                    M("proving.title", "Build tooling for {family}", family=family),
                    "note",
                    M("proving.none", "{family} has not been proved offline yet.", family=family),
                    fix=M("proving.none.fix", "On the Firmware Factory, select Prove now for this family."),
                )
            )
            continue
        if proof.get("state") == "passed":
            out.append(
                finding(
                    f"proving-{family}",
                    M("proving.title", "Build tooling for {family}", family=family),
                    "ok",
                    M(
                        "proving.proven",
                        "{family} builds offline at {tag} (by {env}).",
                        family=family,
                        tag=proof.get("tag") or "?",
                        env=proof.get("env") or "?",
                    ),
                    notes=[M("proving.proven.at", "Proved {time}.", time=when(proof["at"]))] if proof.get("at") else [],
                    evidence=[proof.get("cache_sha256") or ""],
                )
            )
        elif proof.get("state") == "skipped":
            out.append(
                finding(
                    f"proving-{family}",
                    M("proving.title", "Build tooling for {family}", family=family),
                    "warn",
                    M(
                        "proving.skipped",
                        "{family} was not proved at {tag}: {step}.",
                        family=family,
                        tag=proof.get("tag") or "?",
                        step=proof.get("step") or "the tooling is missing",
                    ),
                    M(
                        "proving.skipped.why",
                        "Without the family's cache the box cannot build it offline, and a build with the network open can still fail.",
                    ),
                    M(
                        "proving.skipped.fix",
                        "On the Firmware page, let proving fetch what is missing, or download the release's bundle on a box with internet access.",
                    ),
                    evidence=[proof.get("detail") or ""],
                )
            )
        elif proof.get("state") in ("failed", "timed out"):
            out.append(
                finding(
                    f"proving-{family}",
                    M("proving.title", "Build tooling for {family}", family=family),
                    "warn",
                    M(
                        "proving.failed",
                        "{family} did not build offline at {tag}: {step}.",
                        family=family,
                        tag=proof.get("tag") or "?",
                        step=proof.get("step") or "the build failed",
                    ),
                    M(
                        "proving.failed.why",
                        "A build of this family on this box would fail, or download what it needs when the network is open.",
                    ),
                    M("proving.failed.fix", "Open the proving run's log on the Firmware Factory."),
                    evidence=[proof.get("detail") or ""],
                )
            )
    return out


# --- when it runs -----------------------------------------------------------------------------


def memory_level():
    """The memory guard's level, or None when it is not running (a dev checkout, or before boot)."""
    try:
        return json.loads(MEMGUARD_STATUS.read_text()).get("level")
    except (OSError, ValueError, AttributeError):
        return None


def on_battery():
    """Whether a battery is discharging now."""
    for path in POWER.glob("*") if POWER.is_dir() else []:
        try:
            if (path / "type").read_text().strip() != "Battery":
                continue
            if (path / "status").read_text().strip() == "Discharging":
                return True
        except OSError:
            continue
    return False


def _minutes(value):
    m = re.fullmatch(r"([0-2]?[0-9]):([0-5][0-9])", str(value or ""))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def in_window(cfg, now=None):
    """Whether now is in the night window, which may cross midnight."""
    start, end = _minutes(cfg.get("proving_window_start")), _minutes(cfg.get("proving_window_end"))
    if start is None or end is None:
        return True
    t = time.localtime(now or time.time())
    minute = t.tm_hour * 60 + t.tm_min
    return start <= minute or minute < end if start > end else start <= minute < end


def can_queue(cfg=None, force=False, now=None):
    """Whether a proving run may be queued now: not while memory is short, on battery, or outside
    the window the owner chose. force is the page's Prove now (it still respects memory and battery)."""
    cfg = cfg or settings()
    if not cfg["proving"] and not force:
        return False, "proving is off"
    level = memory_level()
    if level is None and not force:
        return False, "the memory guard is not running"
    if level not in (None, "ok"):
        return False, "the box is short of memory"
    if on_battery():
        return False, "the box is on battery"
    if not force:
        when_ = cfg["proving_when"]
        if when_ == "manual":
            return False, "proving waits for Prove now"
        if when_ == "night" and not in_window(cfg, now):
            return False, "outside the night window"
    return True, None


# --- the queue --------------------------------------------------------------------------------


def _wanted_release(rel, cfg):
    name = (rel.get("name") or rel.get("title") or "").lower()
    if cfg["proving_releases"] == "all":
        return True
    if "alpha" in name:
        return False
    if cfg["proving_releases"] == "stable-beta":
        return True
    return "beta" not in name and not rel.get("prerelease")


def _job_exists(family, tag):
    from irate_box.hub import ci, factory

    for d in (ci.QUEUE, factory.HELD):
        for path in d.glob("*.json") if d.is_dir() else []:
            try:
                job = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if job.get("kind") == "proving" and job.get("family") == family and job.get("tag") == tag:
                return True
    return False


def _proven(family, tag):
    for proof in (_read().get("proofs") or {}).get(family) or []:
        if proof.get("tag") == tag and proof.get("state") == "passed":
            return True
    return False


def queue_release(rel, source, targets, families_list=None, force=False, cwd_flasher=()):
    """Queue one proving job per family for a release at a tag already read. `targets` is the shape
    factory.targets() returns, or its list. Returns how many."""
    from irate_box.hub import ci

    ts = targets.get("targets") if isinstance(targets, dict) else targets
    commit = targets.get("commit") if isinstance(targets, dict) else None
    cfg = settings()
    if families_list is None:
        families_list = families(ts)
    batch, now, made = os.urandom(4).hex(), time.time(), 0
    ci.QUEUE.mkdir(parents=True, exist_ok=True)
    flasher = cwd_flasher or _flasher_boards()
    for i, family in enumerate(sorted(families_list)):
        env = representative(ts, family, flasher)
        if not env or _job_exists(family, rel["tag"]) or _proven(family, rel["tag"]):
            continue
        if not force:
            allowed, _why = can_queue(cfg)
            if not allowed:
                break
        deps = (rel.get("deps_all") or {}).get(family) or (rel.get("deps_all") or {}).get("native-tft")
        ci._put(
            {
                "kind": "proving",
                "repo": source["path"],
                "source": source["name"],
                "ref": rel["tag"],
                "tag": rel["tag"],
                "version": rel["version"],
                "commit": commit,
                "env": env,
                "family": family,
                "bundle": deps if isinstance(deps, dict) else None,
                "batch": batch,
                "queued": now + i / 1000,
            }
        )
        made += 1
    return made


def _flasher_boards():
    try:
        from irate_box.library import firmware

        boards = firmware.settings().get("boards")
        return boards if isinstance(boards, list) else []
    except (OSError, ValueError, ImportError):
        return []


def _source_and_targets(tag=None):
    from irate_box.hub import factory
    from irate_box.library import firmware

    mirror = firmware.source_mirror()
    if not mirror:
        raise ValueError(M("proving.source.missing", "This box has no mirror of meshtastic/firmware."))
    source = factory.source(mirror["name"])
    ref = tag or next((t["ref"] for t in factory.refs(source)["tags"] if t["ref"].startswith("v")), None)
    if not ref:
        raise ValueError(M("proving.tag.missing", "The mirror has no release tag to prove."))
    return source, ref, factory.targets(source, ref)


def check_now(log=print):
    """Compare the newest kept release with the tooling now, without building: what the release
    check does first. Returns the findings."""
    source, ref, targets = _source_and_targets()
    found = []
    for family in families(targets["targets"]):
        env = representative(targets["targets"], family, _flasher_boards())
        found += compare(source["path"], ref, family, env)
    st = _read()
    st["tooling"] = found
    st["checked"] = time.time()
    st["tag"] = ref
    _save(st)
    log(f"proving: {len(found)} tooling finding(s) at {ref}")
    return found


def on_release_check(kept, log=print):
    """The librarian's new-tag check: compare, and queue proving jobs for tags not seen before.
    Called by firmware.sync; never raises into it."""
    cfg = settings()
    if not cfg["proving"]:
        return {"queued": 0}
    from irate_box.hub import factory
    from irate_box.library import firmware

    mirror = firmware.source_mirror()
    if not mirror:
        return {"queued": 0}
    try:
        source = factory.source(mirror["name"])
    except ValueError:
        return {"queued": 0}
    st = _read()
    seen = set(st.get("seen") or [])
    wanted = [r for r in kept if _wanted_release(r, cfg)]
    new = [r for r in wanted if r.get("tag") not in seen]
    if not new:
        return {"queued": 0}
    made, recent, seen_now = 0, [], set()
    for rel in new:
        try:
            targets = factory.targets(source, rel["tag"])
        except (OSError, ValueError):
            continue  # the mirror has not fetched this tag yet; the next check tries again
        recent += compare_families(source["path"], rel["tag"], targets["targets"], rel["version"])
        fams = families(targets["targets"])
        made_rel = queue_release(rel, source, targets, force=False)
        made += made_rel
        # Remember a tag only once its runs were queued or every family was already proved: while
        # the box is short of memory or outside its window, the next check tries again.
        if made_rel or all(_proven(family, rel["tag"]) for family in fams):
            seen_now.add(rel["tag"])
    st = _read()
    st["tooling"] = recent
    st["checked"] = time.time()
    st["tag"] = new[0]["tag"]
    st["seen"] = sorted((seen | seen_now))[-20:]
    _save(st)
    if made:
        log(f"proving: queued {made} run(s) for {', '.join(r['tag'] for r in new)}")
    return {"queued": made}


def compare_families(source_path, ref, targets, version=None):
    out = []
    for family in families(targets):
        env = representative(targets, family, _flasher_boards())
        for item in compare(source_path, ref, family, env):
            out.append(item)
    return out


def prove_now(family=None, tag=None):
    """The Firmware Factory's Prove now: queue the chosen family (or every chosen family)."""
    source, ref, targets = _source_and_targets(tag)
    rel = {"tag": ref, "version": ref.lstrip("v"), "name": "", "prerelease": False, "deps_all": {}}
    families_list = [family] if family else families(targets["targets"])
    if family and family not in {t["family"] for t in targets["targets"]}:
        raise ValueError(M("proving.family.missing", "The release has no family {family}.", family=family))
    made = queue_release(rel, source, targets, families_list, force=True)
    return {"queued": made, "tag": ref}


# --- online refresh (the owner's Flag / Fetch / Install setting) ------------------------------


def ensure_cache(job, log=print):
    """The family's cache for this tag, downloading the release's published bundle first. Returns
    the cache path, or None. Never raises: a failed refresh is a finding, not a failed build."""
    from irate_box.hub import pio_bundle

    version = str(job.get("version") or "").lstrip("v")
    if not VERSION_RE.match(version):
        log("proving: the release name is not one a cache can be filed under")
        return None
    cache = cache_dir(job.get("family"), version)
    if cache:
        return cache
    bundle = job.get("bundle")
    if not isinstance(bundle, dict) or not bundle.get("url"):
        return None
    digest = bundle.get("sha256") or (bundle.get("digest") or "").replace("sha256:", "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", digest or ""):
        log("proving: the published bundle has no SHA-256; refusing to run its tools")
        return None
    tmp = TMP / str(bundle.get("name") or f"{BUNDLE_PREFIX}{job.get('family')}.zip")
    try:
        TMP.mkdir(parents=True, exist_ok=True)
        log(f"proving: downloading the {job.get('family')} cache for {job.get('version')}")
        from irate_box.library import librarian

        librarian._download(
            bundle["url"], tmp, name=f"proving cache {job.get('version')}", expected=bundle.get("size") or 0
        )
        librarian._verify_sha256(tmp, digest, str(bundle.get("name") or "proving cache"))
        if not pio_bundle.verify(tmp).get("ok"):
            log("proving: the published bundle is not a cache")
            return None
        from irate_box.library import firmware

        dest = FIRMWARE / version / "pio-deps" / job["family"]
        firmware._unpack_deps(tmp, dest, str(bundle.get("name") or "proving cache"), log)
        return dest
    except Exception as exc:  # noqa: BLE001 - a refresh failure is a finding, not the build's end
        log(f"proving: the published cache could not be used ({str(exc)[:200]})")
        return None
    finally:
        tmp.unlink(missing_ok=True)


def extract_bundle(path, family, version, log=print):
    """Unpack a verified bundle into the family's cache, with firmware's own space and path checks.
    Returns the cache, or None."""
    from irate_box.hub import pio_bundle
    from irate_box.library import firmware

    version = str(version or "").lstrip("v")
    if not VERSION_RE.match(version):
        log("proving: the release name is not one a cache can be filed under")
        return None
    if not pio_bundle.verify(path).get("ok"):
        return None
    dest = FIRMWARE / version / "pio-deps" / family
    try:
        firmware._unpack_deps(path, dest, Path(path).name, log)
        return dest
    except (OSError, ValueError) as exc:
        log(f"proving: the bundle could not be unpacked ({str(exc)[:200]})")
        return None


def prune(runs):
    """Each family's newest proofs' runs, and any marked keep, from the builder's proving runs."""
    by_family = {}
    for path in sorted((p for p in runs.iterdir() if p.name.isdigit()), key=lambda p: int(p.name)):
        try:
            status = json.loads((path / "status.json").read_text())
        except (OSError, ValueError):
            continue
        by_family.setdefault(status.get("family"), []).append(path)
    keep = settings().get("proving_keep")
    keep = keep if type(keep) is int and 1 <= keep <= 10 else DEFAULTS["proving_keep"]
    for group in by_family.values():
        loose = [p for p in group if not json.loads((p / "status.json").read_text()).get("keep")]
        for old in loose[:-keep]:
            shutil.rmtree(old, ignore_errors=True)


def install_requirements(requirements, family=None, log=print):
    """Fetch the tag's missing Python packages into the Building kit's wheelhouse (Download and
    install). Only wheels: a package with no wheel is not built here, and becomes a finding."""
    if not requirements:
        return []
    root = KITS / "wheelhouse"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return []
    done, failed = [], []
    for line in requirements:
        spec = _req_spec(line)
        if not spec:
            continue
        name, op, version = spec
        try:
            out = subprocess.run(  # noqa: TID251
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "download",
                    "--only-binary=:all:",
                    "--dest",
                    str(root),
                    f"{name}{op}{version}",
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (OSError, subprocess.SubprocessError):
            failed.append(line)
            continue
        if out.returncode == 0:
            done.append(line)
        else:
            failed.append(line)
            _record_tooling(
                _finding(
                    "proving.python.no-wheel",
                    family or "?",
                    "warn",
                    M(
                        "proving.python.no-wheel",
                        "{package} has no wheel for this box, so the Building kit does not install it.",
                        package=name,
                    ),
                    why=M(
                        "proving.python.no-wheel.why",
                        "A source package would run its own build code on this box. The box does not build one silently.",
                    ),
                    fix=M(
                        "proving.python.no-wheel.fix",
                        "Install the package by hand on the box, or use a release whose requirements have wheels for this architecture.",
                    ),
                    repair="fetch",
                    evidence=[line],
                )
            )
    if done:
        log(f"proving: fetched {len(done)} Python package(s) into the wheelhouse")
    return {"done": done, "failed": failed}


def _record_tooling(item):
    """Add one tooling finding to the report the Updates doctor reads, without losing the others."""
    st = _read()
    items = [
        i
        for i in (st.get("tooling") or [])
        if not (isinstance(i, dict) and i.get("id") == item.get("id") and i.get("family") == item.get("family"))
    ]
    items.insert(0, item)
    st["tooling"] = items[:20]
    _save(st)


# --- the record -------------------------------------------------------------------------------


def _read():
    try:
        data = json.loads(PROOFS.read_text())
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save(data):
    PROOFS.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROOFS.with_name(PROOFS.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    os.replace(tmp, PROOFS)


def record(proof):
    """Keep one proof: each family's newest few, as the owner chose."""
    if not isinstance(proof, dict) or not proof.get("family"):
        return
    st = _read()
    family = proof["family"]
    items = [p for p in (st.setdefault("proofs", {}).get(family) or []) if p.get("run") != proof.get("run")]
    items.insert(0, proof)
    keep = settings().get("proving_keep")
    keep = keep if type(keep) is int and 1 <= keep <= 10 else DEFAULTS["proving_keep"]
    st["proofs"][family] = items[:keep]
    st["updated"] = time.time()
    _save(st)


def proof_for(job, state, **extra):
    """The record of one proving run, from its job and status."""
    return {
        "family": job.get("family"),
        "env": job.get("env"),
        "tag": job.get("tag") or job.get("ref"),
        "version": job.get("version") or str(job.get("tag") or "").lstrip("v"),
        "state": state,
        "at": extra.get("at") or time.time(),
        "run": extra.get("run"),
        "cache_sha256": extra.get("cache_sha256"),
        "cache": str(extra["cache"]) if extra.get("cache") else None,
        "tool_versions": extra.get("tool_versions") or {},
        "step": extra.get("step"),
        "detail": extra.get("detail"),
    }
