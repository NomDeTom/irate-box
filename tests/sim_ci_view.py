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
given = set(re.findall(r"\b(CI(?:_[A-Z_]+)?)=", build_src[build_src.index("def build(job)"):])) | {"CI_PIO_DEPS", "HOME"}
check("and what the page says a build gets is what build() sets", set(ci.ENV_VARS) <= given, set(ci.ENV_VARS) - given)
snap = ci.snapshot()
check("the snapshot says the limits, the runs' size, what is offline, and the templates", {"keep_runs", "runs_bytes", "memory", "mirrored", "pio_deps", "env", "templates"} <= set(snap)
      and snap["runs_bytes"] >= 4321, sorted(snap))
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
