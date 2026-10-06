# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Mirrored repositories (next-work plan step 12), offline: a local "upstream" with tags and
branches, a mirror kept to its policy (tag groups by version, GitHub's release/prerelease flag
from a stand-in list, a pin), pruning when the upstream moves on, shallow against full history,
the budget, and the mirror read-only on the box. python3 tests/sim_mirrors.py"""
import json, os, subprocess, sys, tempfile
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="mirrors-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_GIT_ROOT=str(T / "state" / "git"), HUB_MIRRORS_TEST_FILE="1",
                  HUB_MIRROR_URLS=str(T / "state" / "git" / "mirror-urls.json"), HUB_CI_ROOT=str(T / "state" / "ci"))
for a in ("public", "private"):
    (T / "state" / "git" / a).mkdir(parents=True)
(T / "state" / "library").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.library import mirrors, librarian  # noqa: E402
from irate_box.hub import gitrepos  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
def g(*a, cwd=None):
    return subprocess.run(["git", *a], cwd=cwd, env=ENV, capture_output=True, text=True)
def refs(path):
    return sorted(g("for-each-ref", "--format=%(refname)", cwd=path).stdout.split())

# An upstream: main with 12 commits, a tag on most, an annotated one, a dev branch.
UP = T / "up"
g("init", "-q", "-b", "main", str(UP))
for i in range(1, 13):
    (UP / "f").write_text(f"{i}\n" * 2000)
    g("add", "f", cwd=UP); g("commit", "-qm", f"c{i}", cwd=UP)
    if i in (2, 4, 6, 8):
        g("tag", f"v2.{i}.0", cwd=UP)
    if i in (9, 11):
        g("tag", f"v2.{i}.0-alpha", cwd=UP)
g("tag", "-a", "v2.10.0", "-m", "annotated", cwd=UP)  # on c12: version order puts it above v2.8.0
g("branch", "dev", "HEAD~3", cwd=UP)
URL = f"file://{UP}"
base = {"name": "fw", "area": "public", "upstream": URL, "branches": ["main"], "follow": "latest", "pin": "",
        "history": "shallow", "budget_mb": 100,
        "groups": [{"name": "stable", "kind": "tags", "pattern": "v*.*.0", "keep": 2},
                   {"name": "alpha", "kind": "tags", "pattern": "v*-alpha", "keep": 1}]}
m = dict(base)
with mock.patch.object(mirrors, "load", return_value=[m]):
    line = mirrors.sync(m)
path = mirrors.repo_path(m)
check("first update fetches what the policy names (main and three tags)", line == "4 fetched, 0 dropped", line)
check("kept: main, the two newest stable by version (v2.10.0 above v2.8.0), the newest alpha",
      refs(path) == ["refs/heads/main", "refs/tags/v2.10.0", "refs/tags/v2.11.0-alpha", "refs/tags/v2.8.0"], refs(path))
check("shallow: a kept tip has no parents here", g("rev-list", "--count", "refs/heads/main", cwd=path).stdout.strip() == "1",
      g("rev-list", "--count", "refs/heads/main", cwd=path).stdout)
check("the annotated tag is kept as a tag object", g("cat-file", "-t", "refs/tags/v2.10.0", cwd=path).stdout.strip() == "tag")
check("read-only on the box", g("config", "--get", "irate-box.write", cwd=path).stdout.strip() == "nobody"
      and g("config", "--get", "http.receivepack", cwd=path).stdout.strip() == "false")
check("it says it is a mirror", (path / "description").read_text().startswith(f"Mirror of {URL}"))
check("the Git page sees it as public-read-only", next(r for r in gitrepos.snapshot()["repos"] if r["name"] == "fw")["preset"] == "public-read-only")
check("a second update: nothing to do", mirrors.sync(m) == "up to date")

# The upstream moves on: a new stable tag; the oldest kept one drops out, and its objects with it.
(UP / "f").write_text("13\n"); g("commit", "-qam", "c13", cwd=UP); g("tag", "v2.13.0", cwd=UP)
check("a check sees the change", mirrors.sync(m, check_only=True).startswith("2 to fetch, 1 to drop"), mirrors.status()["fw"])
line = mirrors.sync(m)
check("the update fetches the new tag and main, and drops v2.8.0", line == "2 fetched, 1 dropped", line)
check("…v2.8.0 is gone", "refs/tags/v2.8.0" not in refs(path), refs(path))
old = g("rev-parse", "v2.8.0^{commit}", cwd=UP).stdout.strip()
check("…and its commit with it (pruned)", g("cat-file", "-e", old, cwd=path).returncode != 0)

# A pin: main held at an older commit; dev kept as well.
pinned = dict(base, name="fw-pinned", follow="pinned", pin="v2.4.0", branches=["main", "dev"], groups=[])
mirrors.sync(pinned)
pp = mirrors.repo_path(pinned)
check("pinned: main is the pin's commit", g("rev-parse", "refs/heads/main", cwd=pp).stdout.strip() == g("rev-parse", "v2.4.0^{commit}", cwd=UP).stdout.strip())
check("pinned: dev follows the upstream", g("rev-parse", "refs/heads/dev", cwd=pp).stdout.strip() == g("rev-parse", "dev", cwd=UP).stdout.strip())

# Full history: every commit behind main.
full = dict(base, name="fw-full", history="full", groups=[], area="private")
mirrors.sync(full)
check("full history: all 13 commits", g("rev-list", "--count", "refs/heads/main", cwd=mirrors.repo_path(full)).stdout.strip() == "13")
check("a private mirror is private-read-only", g("config", "--get", "irate-box.write", cwd=mirrors.repo_path(full)).stdout.strip() == "nobody")

# The budget.
tiny = dict(base, name="fw-tiny", budget_mb=1, history="full", groups=[])
with mock.patch.object(mirrors.gitrepos, "_size", return_value=5 << 20):
    line = mirrors.sync(tiny)
check("over its budget: said, and recorded", "over its 1 MB budget" in line and mirrors.status()["fw-tiny"]["over_budget"] is True, line)

# GitHub's release flag, from a stand-in list (newest first, as the API gives it).
rel = dict(base, groups=[{"name": "beta", "kind": "release", "pattern": "*", "keep": 2},
                         {"name": "alpha", "kind": "prerelease", "pattern": "*", "keep": 1}])
upref = {f"refs/tags/{t}": "0" * 40 for t in ("v2.13.0", "v2.10.0", "v2.6.0", "v2.11.0-alpha", "v2.9.0-alpha")}
upref["refs/heads/main"] = "1" * 40
w = mirrors.wanted(rel, upref, releases=[("v2.13.0", False), ("v2.11.0-alpha", True), ("v2.10.0", False),
                                         ("v2.9.0-alpha", True), ("v2.6.0", False), ("v3.0.0", False)])
check("release groups follow GitHub's flag and order, and only tags the upstream has",
      sorted(w) == ["refs/heads/main", "refs/tags/v2.10.0", "refs/tags/v2.11.0-alpha", "refs/tags/v2.13.0"], sorted(w))
# A revoked release (Meshtastic marks it in the name only) is left out, and the next older kept.
REVOKED = [["v2.13.0", False, False], ["v2.11.0-alpha", True, True], ["v2.10.0", False, False],
           ["v2.9.0-alpha", True, False], ["v2.6.0", False, False]]
w = mirrors.wanted(rel, upref, releases=REVOKED)
check("a revoked prerelease is left out; the next older one is kept instead",
      "refs/tags/v2.11.0-alpha" not in w and "refs/tags/v2.9.0-alpha" in w, sorted(w))
forensic = dict(rel, groups=[dict(g, skip_revoked=False) for g in rel["groups"]])
check("skip_revoked off keeps it", "refs/tags/v2.11.0-alpha" in mirrors.wanted(forensic, upref, releases=REVOKED))
check("the rule is the librarian's, shared with firmware.py",
      librarian.revoked({"name": "Meshtastic Firmware 2.8.0.47db0e3 Alpha (Revoked)"}) and not librarian.revoked({"name": "2.7.15 Beta"}))
# The release list is cached in the mirror's status; a refused API call is answered from it.
gh = dict(rel, name="fw-gh", upstream="https://github.com/meshtastic/firmware")
entry = {}
api = [{"tag_name": "v2.13.0", "name": "2.13.0 Beta", "prerelease": False},
       {"tag_name": "v2.11.0-alpha", "name": "2.11.0 Alpha (Revoked)", "prerelease": True},
       {"tag_name": "v2.12.0", "name": "draft", "prerelease": False, "draft": True}]
with mock.patch.object(librarian, "_api", return_value=api):
    got = mirrors._releases(gh, entry)
check("the list from GitHub: drafts out, revoked marked", got == [["v2.13.0", False, False], ["v2.11.0-alpha", True, True]], got)
check("and kept in the status", entry["releases"]["list"] == got and "releases_cached" not in entry, entry)
with mock.patch.object(librarian, "_api", side_effect=librarian.LibrarianError("GitHub API rate limit reached (60/hour without a token)")):
    again = mirrors._releases(gh, entry)
check("a refused call is answered from the cache, and says so", again == got and "rate limit" in entry["releases_cached"]["why"]
      and entry["releases_cached"]["since"] == entry["releases"]["fetched"], entry)
try:
    with mock.patch.object(librarian, "_api", side_effect=librarian.LibrarianError("refused")):
        mirrors._releases(gh, {})
    check("with no cache, a refused call is an error", False)
except librarian.LibrarianError:
    check("with no cache, a refused call is an error", True)
with mock.patch.object(librarian, "_api", return_value=api):
    mirrors._releases(gh, entry)
check("a later good call clears the cached mark", "releases_cached" not in entry, entry)
check("a mirror with tag groups only keeps no release list", mirrors._releases(base, {"releases": {}}) is None)

# The Git page's ordinary actions leave a mirror alone.
for body in ({"action": "delete"}, {"action": "preset", "preset": "public-everything"}, {"action": "move", "to": "private"}):
    code, out = gitrepos.action(dict(body, area="public", name="fw"))
    check(f"a mirror refuses {body['action']} from the Git page", code == 400 and "is a mirror" in out.get("error", ""), out)
check("gitrepos lists it as a mirror of its upstream", next(r for r in gitrepos.snapshot()["repos"] if r["name"] == "fw")["mirror_of"] == URL)

# Submodules: each kept ref's submodule commits, mirrored; a build then needs no upstream.
SUB = T / "sub"
g("init", "-q", "-b", "main", str(SUB))
subs = []
for i in (1, 2, 3):
    (SUB / "s").write_text(f"{i}\n"); g("add", "s", cwd=SUB); g("commit", "-qm", f"s{i}", cwd=SUB)
    subs.append(g("rev-parse", "HEAD", cwd=SUB).stdout.strip())
UP2 = T / "up2"
g("init", "-q", "-b", "main", str(UP2))
g("-c", "protocol.file.allow=always", "submodule", "add", "-q", f"file://{SUB}", "lib", cwd=UP2)
for i, tag in ((0, "v1.0.0"), (1, "v1.1.0"), (2, None)):
    g("-C", "lib", "checkout", "-q", subs[i], cwd=UP2); g("add", "lib", ".gitmodules", cwd=UP2)
    g("commit", "-qm", f"lib at s{i + 1}", cwd=UP2)
    if tag:
        g("tag", tag, cwd=UP2)
sm = dict(base, name="withsub", upstream=f"file://{UP2}", submodules=True,
          groups=[{"name": "r", "kind": "tags", "pattern": "v*", "keep": 1}])
with mock.patch.object(mirrors, "load", return_value=[sm]):
    line = mirrors.sync(sm)
    urls = json.loads((T / "state" / "git" / "mirror-urls.json").read_text())
check("submodules: mirrored, and said", line.endswith("1 submodule mirrored"), line)
sp = T / "state" / "git" / "public" / "withsub--lib.git"
kept = sorted(r.rsplit("/", 1)[-1] for r in refs(sp))
check("…exactly the commits the kept refs name (main's and v1.1.0's lib)", kept == sorted([subs[1], subs[2]]), kept)
check("…read-only, and a mirror of the submodule's URL", g("config", "--get", "irate-box.write", cwd=sp).stdout.strip() == "nobody"
      and g("config", "--get", "irate-box.mirror", cwd=sp).stdout.strip() == f"file://{SUB}")
from irate_box.hub import server  # noqa: E402
with mock.patch.object(mirrors, "load", return_value=[sm]):
    snap = server.git_snapshot()
sub_card = next(r for r in snap["repos"] if r["name"] == "withsub--lib")
check("the Git page knows a submodule mirror's parent (and leaves it out of its grid)", sub_card["submodule_of"] == "withsub"
      and next(r for r in snap["repos"] if r["name"] == "withsub")["submodule_of"] is None, sub_card)
check("a mirror is never offered builds", not sub_card["can_build"] and not next(r for r in snap["repos"] if r["name"] == "withsub")["can_build"])
check("the URL map names the mirror and its submodule", urls.get(f"file://{SUB}") == f"file://{sp}" and urls.get(f"file://{UP2}") == f"file://{mirrors.repo_path(sm)}", urls)
# ci.py, as hubci: the rewrites in its global config, then a checkout with the upstream gone.
from irate_box.hub import ci  # noqa: E402
os.environ["HOME"] = str(T / "hubci-home"); (T / "hubci-home").mkdir()
ci.use_mirrors()
HENV = dict(ENV, HOME=str(T / "hubci-home"))
gl = subprocess.run(["git", "config", "--global", "--get-regexp", "^url"], env=HENV, capture_output=True, text=True).stdout
check("ci: hubci's git config rewrites each mirrored URL", f"url.file://{sp}.insteadof file://{SUB}" in gl, gl)
import shutil
shutil.rmtree(SUB)  # the "internet" goes away
W = T / "build"
g("clone", "-q", "--no-checkout", str(mirrors.repo_path(sm)), str(W)); g("checkout", "-q", "v1.1.0", cwd=W)
r = subprocess.run(["git", "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive", "--depth", "1"],
                   cwd=W, env=dict(ENV, HOME=str(T / "hubci-home")), capture_output=True, text=True)
check("ci: with the submodule's upstream gone, a checkout gets it from the box", r.returncode == 0 and (W / "lib" / "s").read_text() == "2\n", r.stderr[-300:])
with mock.patch.object(mirrors, "load", return_value=[]):
    mirrors.write_urls()
ci.use_mirrors()
check("ci: a removed mirror's rewrite goes too", "insteadof" not in subprocess.run(["git", "config", "--global", "--get-regexp", "^url"],
      env=HENV, capture_output=True, text=True).stdout.lower())

# Changing a mirror's settings in place.
mirrors._write(mirrors.SETTINGS, {"mirrors": [mirrors.validate(dict(base, upstream="https://github.com/meshtastic/firmware"))]})
mirrors.change(dict(base, upstream="https://github.com/meshtastic/firmware", submodules=True))
check("change: submodules switched on in place", mirrors.load()[0]["submodules"] is True)
try:
    mirrors.change(dict(base, upstream="https://github.com/meshtastic/firmware", area="private")); moved = True
except librarian.LibrarianError:
    moved = False
check("change: the area stays", not moved)

# Settings are checked.
def bad(**kw):
    try:
        mirrors.validate({**base, "upstream": "https://github.com/meshtastic/firmware", **kw}); return False
    except librarian.LibrarianError:
        return True
check("an http:// upstream is refused", bad(upstream="http://example.com/x.git"))
check("a token in the URL is refused", bad(upstream="https://user:tok@github.com/x/y"))
check("a branch that is an option is refused", bad(branches=["--upload-pack=x"]))
check("release groups need github.com", bad(upstream="https://example.com/x.git", groups=[{"kind": "release", "keep": 1}]))
check("skip_revoked is a true or false", bad(groups=[{"kind": "release", "keep": 1, "skip_revoked": "no"}]))
check("keep is bounded", bad(groups=[{"kind": "tags", "pattern": "v*", "keep": 99}]))
ok = mirrors.validate(dict(base, upstream="https://github.com/meshtastic/firmware",
                           groups=[{"kind": "release", "keep": 2}, {"kind": "prerelease", "keep": 2}]))
check("Meshtastic's firmware, release 2 and prerelease 2, is a valid mirror", ok["groups"][1] == {"name": "prerelease", "kind": "prerelease", "pattern": "*", "keep": 2, "skip_revoked": True}, ok)
# The scheduled librarian (2026-10-06): its unit lacked hub.env, so the git root fell back to the
# read-only code folder and write_urls() raised, ending the whole run before the hub's own update.
ro = T / "ro"; ro.mkdir(); ro.chmod(0o555)
with mock.patch.object(mirrors, "URLS", ro / "sub" / "mirror-urls.json"), mock.patch.object(mirrors, "load", return_value=[m]):
    try:
        line = mirrors.sync(m)
        check("a mirror list it cannot write is said in the outcome, not raised", "not written" in line or os.geteuid() == 0, line)
    except OSError as exc:
        check("a mirror list it cannot write is said in the outcome, not raised", False, exc)
ro.chmod(0o755)
unit = (REPO / "install.sh").read_text()
unit = unit[unit.index("irate-box-librarian.service <<EOF"):]
unit = unit[:unit.index("\nEOF")]
check("the librarian's unit reads hub.env (the git and firmware roots)", "EnvironmentFile=$ETC/hub.env" in unit, unit)
# librarian main() on update()'s results, the mirrors' a dict of lines (it crashed on that from
# step 12 until 2026-10-06, failing the timer's unit after doing the work).
out = list(librarian.outcomes({"books": "up to date", "mirrors": {"fw": "error: x", "y": "up to date"}, "hub": None}))
check("every stage's outcome is read, a dict's too", out == ["up to date", "error: x", "up to date", "None"], out)
with mock.patch.object(librarian, "update", return_value={"mirrors": {"fw": "error: refused"}, "books": "up to date"}):
    rc_hand, rc_timer = librarian.main(["update"]), librarian.main(["update", "--scheduled"])
check("main(): a mirror's error fails a run by hand, never the timer's unit", rc_hand == 1 and rc_timer == 0, (rc_hand, rc_timer))
# Monitoring the librarian's runs (2026-10-06: every scheduled run failed for hours unseen).
import time as _t  # noqa: E402
def crash():
    raise AttributeError("'dict' object has no attribute 'startswith'")
try:
    librarian.record_run(crash, True)
except AttributeError:
    pass
rec = json.loads(librarian.LAST_RUN.read_text())
check("a crashed run is recorded, with where", rec["last_scheduled"]["ok"] is False and "AttributeError" in rec["last_scheduled"]["error"]
      and "crash" in rec["last_scheduled"]["where"] and rec["last_scheduled"]["finished"], rec)
from irate_box.root import hub_control  # noqa: E402
f = hub_control.scheduled_findings()
check("the updates doctor: a crashed run is a problem, saying where", any(x["status"] == "problem" and "crashed" in x["detail"] and "crash (" in x["detail"] for x in f), f)
check("  and that the hub's own update has not been reached", any("not reached the hub's own update stage yet" in x["detail"] for x in f), f)
librarian.record_run(lambda: {"books": "up to date", "mirrors": {"fw": "error: refused"}, "hub": "up to date"}, True)
f = hub_control.scheduled_findings()
check("a run that reaches the hub's update: ok, the sources' errors said", len(f) == 1 and f[0]["status"] == "ok" and "fw: error: refused" in f[0]["detail"], f)
check("  later than 3 h without reaching it: a problem", any(x["status"] == "problem" for x in hub_control.scheduled_findings(now=_t.time() + 4 * 3600)))
librarian.record_run(lambda: {"books": "up to date"}, False)
check("a run by hand doesn't count as the timer's", json.loads(librarian.LAST_RUN.read_text())["last_scheduled"]["stages"] == ["books", "hub", "mirrors"])
from irate_box.root import health  # noqa: E402
props = {"irate-box-librarian.timer": {"LoadState": "loaded", "ActiveState": "active", "Result": "success", "UnitFileState": "enabled"},
         "irate-box-librarian.service": {"LoadState": "loaded", "ActiveState": "inactive", "Result": "exit-code"}}
with mock.patch.object(health, "expected_units", return_value=[("irate-box-librarian.timer", "the librarian's schedule")]), \
     mock.patch.object(health, "unit_props", side_effect=lambda u: props.get(u, {"LoadState": "loaded", "ActiveState": "active", "Result": "success"})), \
     mock.patch.object(health, "journal_tail", return_value=["AttributeError: x"]), mock.patch.object(health, "run", return_value=mock.Mock(stdout="", returncode=0)):
    found = health.check_units()
svc = [x for x in found if x["id"] == "unit:irate-box-librarian.service"]
check("the services doctor: a timer running, its service's last run failed: a problem, with its log", svc and svc[0]["status"] == "problem"
      and "exit-code" in svc[0]["detail"] and "AttributeError" in svc[0]["detail"], found)
props["irate-box-librarian.service"]["Result"] = "success"
with mock.patch.object(health, "expected_units", return_value=[("irate-box-librarian.timer", "the librarian's schedule")]), \
     mock.patch.object(health, "unit_props", side_effect=lambda u: props.get(u, {"LoadState": "loaded", "ActiveState": "active", "Result": "success"})), \
     mock.patch.object(health, "run", return_value=mock.Mock(stdout="", returncode=0)):
    found = health.check_units()
check("  once it succeeds, nothing said about it", not [x for x in found if x["id"] == "unit:irate-box-librarian.service"])
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
