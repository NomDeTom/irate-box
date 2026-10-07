# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Builds made easier to follow (next-work plan step 33, git-ci-plan §3), offline: Build now and
its refusals; keep and delete through the queue (the runs are the builder's), never a run in
progress; pruning that spares kept runs and follows the setting; a run's view read from an offset
with its steps and artifacts; the templates using only what a build is given.
python3 tests/sim_ci_view.py"""
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="ciview-"))
os.environ.update(HUB_CI_ROOT=str(T / "ci"), HUB_GIT_PRIVATE=str(T / "git" / "private"), HUB_MIRROR_URLS=str(T / "git" / "mirror-urls.json"))
for d in ("ci/queue", "ci/runs", "git/private", "git/public", "library"):
    (T / d).mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import ci  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
def repo(area, name, script=True, branch="main"):
    w = T / f"w-{name}"
    subprocess.run(["git", "init", "-q", "-b", branch, str(w)], check=True)
    (w / ("." + "irate-ci.sh" if script else "README")).write_text("echo hi\n")
    subprocess.run(["git", "add", "-A"], cwd=w, check=True); subprocess.run(["git", "commit", "-qm", "c"], cwd=w, env=ENV, check=True)
    bare = T / "git" / area / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(w), str(bare)], check=True)
    return bare
fw = repo("private", "fw")
jobs = lambda: [json.loads(p.read_text()) for p in sorted(ci.QUEUE.glob("*.json"))]
out = ci.queue_build(fw)
check("Build now: the default branch's newest commit, queued as a push would be", out["branch"] == "main" and len(out["commit"]) == 40
      and jobs()[-1]["repo"] == str(fw.resolve()) and jobs()[-1]["by"] == "build-now", jobs())
for bad, why, args in ((repo("public", "pub"), "only private", ()), (repo("private", "noscript", script=False), "has no .irate-ci.sh", ()),
                       (fw, "no branch nope", ("nope",)), (fw, "not a branch name", ("--upload-pack=x",))):
    try:
        ci.queue_build(bad, *args); check(f"Build now refused: {why}", False)
    except ValueError as exc:
        check(f"Build now refused: {why}", why in str(exc), str(exc))
subprocess.run(["git", "config", "irate-box.ci", "off"], cwd=fw, check=True)
try:
    ci.queue_build(fw); check("Build now refused: switched off", False)
except ValueError as exc:
    check("Build now refused: switched off", "switched off" in str(exc))
subprocess.run(["git", "config", "--unset", "irate-box.ci"], cwd=fw, check=True)

# Runs: keep, delete, pruning, the view.
runs = ci.RUNS / "fw"
for n in range(1, 8):
    d = runs / str(n); (d / "artifacts").mkdir(parents=True)
    ci._write_status(d, repo="fw", branch="main", commit="a" * 40, state="passed", started=time.time() - 100 + n, finished=time.time())
(runs / "3" / "log.txt").write_text("== fw main aaaaaaa\nbuilding\n== building\nok\n== keeping the program\n")
(runs / "3" / "artifacts" / "meshtasticd").write_bytes(b"x" * 1234)
ci.queue_run_change("fw/2", "keep")
ci.queue_run_change("fw/1", "delete")
try:
    ci.queue_run_change("../etc", "delete"); check("a run that isn't one: refused", False)
except ValueError:
    check("a run that isn't one: refused", True)
ci._write_status(runs / "7", state="running")
ci.queue_run_change("fw/7", "delete")
for job in jobs():
    if job.get("change"):
        ci.change_run(job)
check("keep and delete are done by the builder, from the queue", json.loads((runs / "2" / "status.json").read_text())["keep"] is True and not (runs / "1").exists())
check("  a run in progress is never deleted", (runs / "7").exists())
(T / "library" / "ci.json").write_text(json.dumps({"keep_runs": 3}))
ci.prune(runs)
check("pruning: the newest of the setting's number, and the kept one", sorted(int(p.name) for p in runs.iterdir()) == [2, 5, 6, 7], sorted(p.name for p in runs.iterdir()))
(T / "library" / "ci.json").write_text(json.dumps({"keep_runs": 999}))
check("a silly setting falls back to the default", ci.settings()["keep_runs"] == ci.KEEP_RUNS)
d = runs / "6"
(d / "log.txt").write_text("== start\n" + "line\n" * 10 + "== building\n")
(d / "artifacts" / "fw.bin").write_bytes(b"y" * 4321)
os.symlink("/etc/passwd", d / "artifacts" / "sneaky")
v = ci.run_view("fw/6")
check("a run's view: its status, its steps (the == lines), its artifacts with sizes, no link", v["status"]["state"] == "passed"
      and v["steps"] == ["start", "building"] and v["artifacts"] == [{"name": "fw.bin", "size": 4321}], v)
v2 = ci.run_view("fw/6", v["next"] - 12)
check("  the log from an offset, for following it live", v2["log"] == "== building\n" and v2["next"] == v["size"], v2)
check("  not one: None", ci.run_view("../../etc/x/1") is None and ci.run_view("fw/99") is None)

# The templates use only what a build is given.
used = set()
for t in ci.TEMPLATES.values():
    used |= set(re.findall(r"\$\{?(CI_[A-Z_]+|HOME)\b", t["script"]))
check("the templates use only what a build gets", used and used <= set(ci.ENV_VARS), used - set(ci.ENV_VARS))
build_src = (REPO / "irate_box/hub/ci.py").read_text()
given = set(re.findall(r"\b(CI(?:_[A-Z_]+)?)=", build_src[build_src.index("def build(job)"):])) | {"CI_PIO_DEPS", "CI_PIO_DEPS_TAG", "PIP_NO_INDEX", "PIP_FIND_LINKS", "HOME"}
check("and what the page says a build gets is what build() sets", set(ci.ENV_VARS) <= given, set(ci.ENV_VARS) - given)
snap = ci.snapshot()
check("the snapshot says the limits, the runs' size, what is offline, and the templates", {"keep_runs", "runs_bytes", "memory", "mirrored", "pio_deps", "wheelhouse", "env", "templates"} <= set(snap)
      and snap["runs_bytes"] >= 4321, sorted(snap))
check("no wheelhouse yet: not offered", snap["wheelhouse"] is False)

# The Building kit's wheelhouse (toolkits-plan §5): once cached, a build gets PIP_NO_INDEX and
# PIP_FIND_LINKS, with no change to the template (pip reads them from its environment).
kits_root = T / "kits"; (kits_root / "wheelhouse").mkdir(parents=True)
(kits_root / "wheelhouse" / "platformio-6.1.0-py3-none-any.whl").write_bytes(b"x" * 10)
os.environ["HUB_KITS_ROOT"] = str(kits_root)
check("the wheelhouse is offered once cached", ci.snapshot()["wheelhouse"] is True)
w = T / "w-wh"
subprocess.run(["git", "init", "-q", "-b", "main", str(w)], check=True)
(w / ".irate-ci.sh").write_text('#!/bin/bash\necho -n "$PIP_NO_INDEX $PIP_FIND_LINKS" > "$CI_ARTIFACTS/env.txt"\n')
subprocess.run(["git", "add", "-A"], cwd=w, check=True); subprocess.run(["git", "commit", "-qm", "c"], cwd=w, env=ENV, check=True)
wh_bare = T / "git" / "private" / "wh.git"
subprocess.run(["git", "clone", "-q", "--bare", str(w), str(wh_bare)], check=True)
commit = subprocess.run(["git", "rev-parse", "main"], cwd=w, capture_output=True, text=True, check=True).stdout.strip()
ci.build({"repo": str(wh_bare), "branch": "main", "commit": commit, "queued": time.time()})
env_out = (ci.RUNS / "wh" / "1" / "artifacts" / "env.txt").read_text()
check("a build gets PIP_NO_INDEX and PIP_FIND_LINKS from the wheelhouse", env_out == f"1 {kits_root / 'wheelhouse'}", env_out)
# The firmware template seeds PlatformIO from the build cache (step 33c): only what is missing, the
# download cache without usage.db, the libraries of either layout; and builds the cache's release.
script = ci.TEMPLATES["meshtasticd"]["script"]
check("the template builds the cache's release, or develop", 'FW_REF=${CI_PIO_DEPS_TAG:-develop}' in script)
seed = script[script.index('if [ -n "${CI_PIO_DEPS:-}" ]'):script.index('case $PIO_FROM in')]
for layout in ("native", "whole"):
    cache, home, fwd = T / f"cache-{layout}", T / f"home-{layout}", T / f"fw-{layout}"
    libs = cache / "libdeps" / ("native-tft" if layout == "whole" else "")
    for f in ("core/.cache/downloads/eb76", "core/.cache/downloads/usage.db", "packages/framework-portduino/package.json"):
        (cache / f).parent.mkdir(parents=True, exist_ok=True); (cache / f).write_text("new")
    (libs / "RadioLib").mkdir(parents=True); (libs / "RadioLib" / "library.json").write_text("new")
    (libs / "Crypto").mkdir(parents=True); (libs / "Crypto" / "library.json").write_text("new")
    for ui in ("lvgl", "meshtastic-device-ui"):
        (libs / ui).mkdir(parents=True); (libs / ui / "library.json").write_text("ui")
    (home / ".platformio/packages/framework-portduino").mkdir(parents=True)
    (home / ".platformio/packages/framework-portduino/package.json").write_text("kept")
    fwd.mkdir(); (fwd / ".pio/libdeps/native/Crypto").mkdir(parents=True); (fwd / ".pio/libdeps/native/Crypto/library.json").write_text("kept")
    r = subprocess.run(["bash", "-euc", seed], cwd=fwd, env=dict(os.environ, HOME=str(home), CI_PIO_DEPS=str(cache), CI_PIO_DEPS_TAG="v2.8.1.8e6a88d", PIO_ENV="native", PIO_ENV_FAMILY="native"),
                       capture_output=True, text=True)
    pio = home / ".platformio"
    check(f"seeding ({layout} cache): the platform's archive in, usage.db not, what was there kept", r.returncode == 0
          and (pio / ".cache/downloads/eb76").read_text() == "new" and not (pio / ".cache/downloads/usage.db").exists()
          and (pio / "packages/framework-portduino/package.json").read_text() == "kept", r.stderr)
    check(f"  the touchscreen build's libraries left out (the Lyra's offline build failed on them)", not (fwd / ".pio/libdeps/native/lvgl").exists()
          and not (fwd / ".pio/libdeps/native/meshtastic-device-ui").exists())
    check(f"  the libraries in the project's libdeps, a library already there kept", (fwd / ".pio/libdeps/native/RadioLib/library.json").read_text() == "new"
          and (fwd / ".pio/libdeps/native/Crypto/library.json").read_text() == "kept", sorted(p.name for p in (fwd / ".pio/libdeps/native").iterdir()))
# Another family (a Firmware Factory build, step 36): the download cache, not native's packages or libraries.
home, fwd = T / "home-esp", T / "fw-esp"; fwd.mkdir()
r = subprocess.run(["bash", "-euc", seed], cwd=fwd, env=dict(os.environ, HOME=str(home), CI_PIO_DEPS=str(T / "cache-native"), CI_PIO_DEPS_TAG="v2.8.1.8e6a88d",
                   PIO_ENV="heltec-v3", PIO_ENV_FAMILY="esp32s3"), capture_output=True, text=True)
check("seeding another family: the download cache only", r.returncode == 0 and (home / ".platformio/.cache/downloads/eb76").exists()
      and not (home / ".platformio/packages").exists() and not (fwd / ".pio").exists(), r.stderr)
# Which PlatformIO (step 33c): Debian's by default; the wheelhouse's in a venv that sees Debian's
# protobuf when Debian's has none, is asked for, or stops before compiling; never a second build
# after one that compiled and failed.
pio_part = (script[script.index("PIO_FROM=${PIO:-auto}"):script.index("venv_pio() {")]
            + 'venv_pio() { echo "$HOME/venvpio"; }\n'
            + script[script.index("case $PIO_FROM in"):script.index('echo "== keeping the program"')])
def pio_case(name, debian, debian_out, debian_rc, mode="auto"):
    home = T / f"pio-{name}"; home.mkdir()
    def fake(path, ver, out, rc):
        path.write_text(f'#!/bin/bash\n[ "$1" = --version ] && {{ echo "PlatformIO Core, version {ver}"; exit 0; }}\n'
                        f'echo "$0 $*" >> "{home}/calls"; printf "{out}"; exit {rc}\n'); path.chmod(0o755)
    if debian:
        fake(home / "debpio", "6.1.10", debian_out, debian_rc)
    fake(home / "venvpio", "6.2.0", "Compiling .pio/x.o\\n", 0)
    r = subprocess.run(["bash", "-c", "set -euo pipefail\n" + pio_part.replace("PIO_DEBIAN=/usr/bin/pio", f"PIO_DEBIAN={home / 'debpio'}")],
                       env=dict(os.environ, HOME=str(home), PIO=mode, PIO_ENV="native"), capture_output=True, text=True)
    calls = [Path(c.split()[0]).name for c in (home / "calls").read_text().splitlines()] if (home / "calls").exists() else []
    return r.returncode, calls, r.stdout + r.stderr
rc, calls, out = pio_case("deb-ok", True, "Compiling .pio/x.o\\n", 0)
check("PlatformIO: Debian's when the kit has it", rc == 0 and calls == ["debpio"] and "building with PlatformIO Core, version 6.1.10" in out, (rc, calls, out))
rc, calls, out = pio_case("deb-early", True, "Error: platform needs PlatformIO 6.2\\n", 1)
check("  Debian's stops before compiling: the wheelhouse's builds it, and the log says so", rc == 0 and calls == ["debpio", "venvpio"]
      and "Debian's PlatformIO stopped before compiling" in out, (rc, calls, out))
rc, calls, out = pio_case("deb-late", True, "Compiling .pio/x.o\\nerror: x.cpp\\n", 1)
check("  Debian's fails while compiling: the build fails, no second try", rc != 0 and calls == ["debpio"], (rc, calls))
rc, calls, out = pio_case("no-deb", False, "", 0)
check("  no Debian PlatformIO: the wheelhouse's", rc == 0 and calls == ["venvpio"], (rc, calls))
rc, calls, out = pio_case("pip", True, "", 0, mode="pip")
check("  PIO=pip: the wheelhouse's even with Debian's there", rc == 0 and calls == ["venvpio"], (rc, calls))
from irate_box.library import firmware as FW  # noqa: E402
check("the template's left-out libraries are firmware.py's UI_LIBS", set(re.search(r"case .*?\) continue", script).group(0).split("in ", 1)[1].split(")")[0].split("|")) == FW.UI_LIBS)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
