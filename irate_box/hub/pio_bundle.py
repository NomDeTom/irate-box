# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A per-family PlatformIO build cache, and the manifest of what a release needs to build it.

The Meshtastic release page carries prebuilt firmware and one native-tft dependency zip, not the
source or the device families' platforms, toolchains and libraries. A release-side job (or a box
with the network) can make the missing part: run PlatformIO's tools fetch for one environment,
then pack what it installed into `pio-deps-<env>/`:

  packages/                  PlatformIO's platform, framework and toolchain packages
  core/.cache/downloads/     the archives PlatformIO downloaded (the platform is there)
  libdeps/<env>/             the target's libraries
  build-manifest.json        this release's source, environments, libraries and Python requirements

`ci.py` seeds a family from this layout (`libdeps/<family>`), so a later offline build finds
everything it needs. `manifest()` reads the source's `platformio.ini` and `requirements*.txt`;
`bundle()` packs a PlatformIO home and a workspace after a tools fetch; `verify()` checks a
bundle before it is used. No network and no PlatformIO call is made here.

  irate-box pio_bundle manifest SOURCE
  irate-box pio_bundle bundle ENV SOURCE OUT.zip
  irate-box pio_bundle verify OUT.zip

Stdlib only.
"""

import configparser
import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

BUNDLE_PREFIX = "pio-deps-"
MANIFEST = "build-manifest.json"


def _git(source, *args):
    try:
        out = subprocess.run(  # noqa: TID251
            ["git", "-C", str(source), *args], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _read_requirements(source):
    out = {}
    for path in sorted(source.glob("requirements*.txt")):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        out[path.name] = [
            line.strip() for line in lines if line.strip() and not line.strip().startswith(("#", "-r", "-c", "--"))
        ]
    return out


def manifest(source):
    """The source's build manifest: its tag/commit, each PlatformIO environment's platform, board,
    framework, libraries and flags, and the Python requirements files. A source without a
    platformio.ini still gives its commit and requirements."""
    source = Path(source)
    cfg = configparser.ConfigParser(interpolation=None, strict=False, comment_prefixes=(";", "#"))
    cfg.optionxform = str
    cfg.read(source / "platformio.ini")
    envs = {}
    for section in cfg.sections():
        if not section.startswith("env:"):
            continue
        name = section[4:]
        env = {}
        for key in ("platform", "board", "framework", "build_flags", "extra_scripts", "lib_ldf_mode"):
            if key in cfg[section]:
                env[key] = cfg[section][key]
        libs = [
            line.strip()
            for line in (cfg[section].get("lib_deps") or "").splitlines()
            if line.strip() and not line.strip().startswith(";")
        ]
        if libs:
            env["lib_deps"] = libs
        envs[name] = env
    default = None
    if cfg.has_option("platformio", "default_envs"):
        default = [x.strip() for x in cfg.get("platformio", "default_envs").split(",") if x.strip()]
    return {
        "source": str(source),
        "commit": _git(source, "rev-parse", "HEAD"),
        "tag": _git(source, "describe", "--tags", "--always"),
        "default_envs": default or [],
        "envs": envs,
        "requirements": _read_requirements(source),
    }


def _safe(name):
    parts = Path(name).parts
    return bool(parts) and not any(part in ("", ".", "..") for part in parts) and not Path(name).is_absolute()


def _add(zf, base, prefix):
    if not base.is_dir():
        return []
    added = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(base)
        name = f"{prefix}/{rel.as_posix()}"
        if not _safe(name):
            continue
        zf.write(path, name)
        added.append(name)
    return added


def bundle(env, source, dest, home=None, workspace=None):
    """Pack what a tools fetch installed for `env`: PlatformIO's packages and download cache, the
    project's libdeps/<env>, and the source's build manifest. Returns the bundle and what it holds."""
    source = Path(source)
    dest = Path(dest)
    home = Path(home or os.environ.get("PLATFORMIO_HOME") or Path(os.environ.get("HOME", "/root")) / ".platformio")
    workspace = Path(workspace or os.environ.get("PLATFORMIO_WORKSPACE_DIR") or source / ".pio")
    prefix = f"{BUNDLE_PREFIX}{env}"
    doc = manifest(source)
    doc.update({"env": env, "created": time.time(), "platformio": str(home), "workspace": str(workspace)})
    dest.parent.mkdir(parents=True, exist_ok=True)
    added = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        added += _add(zf, home / "packages", f"{prefix}/packages")
        added += _add(zf, home / ".cache" / "downloads", f"{prefix}/core/.cache/downloads")
        added += _add(zf, workspace / "libdeps" / env, f"{prefix}/libdeps/{env}")
        zf.writestr(f"{prefix}/{MANIFEST}", json.dumps(doc, indent=2, sort_keys=True))
    return {
        "path": str(dest),
        "bytes": dest.stat().st_size,
        "env": env,
        "files": len(added),
        "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        "manifest": doc,
    }


def verify(path):
    """A bundle's manifest, or {"ok": False, "why": ...} for a file that is not one or whose members
    leave its prefix. Never raises for a bad file."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as zf:
            bad = [name for name in zf.namelist() if not _safe(name) or not name.startswith(BUNDLE_PREFIX)]
            if bad:
                return {"ok": False, "why": f"unsafe member: {bad[0]}"}
            names = [name for name in zf.namelist() if name.endswith(f"/{MANIFEST}")]
            if len(names) != 1:
                return {"ok": False, "why": "no build manifest beside the cache"}
            doc = json.loads(zf.read(names[0]))
            if not doc.get("env"):
                return {"ok": False, "why": "the manifest has no environment"}
            return {"ok": True, "env": doc["env"], "manifest": doc, "bytes": path.stat().st_size}
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return {"ok": False, "why": str(exc)[:200]}


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "manifest" and len(sys.argv) == 3:
        print(json.dumps(manifest(Path(sys.argv[2])), indent=2, sort_keys=True))
    elif command == "bundle" and len(sys.argv) == 5:
        print(json.dumps(bundle(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])), indent=2, sort_keys=True))
    elif command == "verify" and len(sys.argv) == 3:
        said = verify(Path(sys.argv[2]))
        print(json.dumps(said, indent=2, sort_keys=True))
        sys.exit(0 if said.get("ok") else 1)
    else:
        sys.exit("usage: irate-box pio_bundle manifest SOURCE | bundle ENV SOURCE OUT.zip | verify OUT.zip")
