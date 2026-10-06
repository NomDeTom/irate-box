# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Toolkits (next-work plan step 26, toolkits-plan §1–3, §8), offline: apt, dpkg and systemctl
stood in by a small archive with dependencies. The fetch (resolved as if no kit were installed),
the pool and its index, current and previous versions and pruning, the budget, a tampered file,
the install with only the local repository and services kept stopped, what it added and the
removal of only that (a package two kits share stays), expiry, the hub's half and the doctor.
The real thing was tried on the Lyra (next-work-plan, step 26). python3 tests/sim_toolkits.py"""
import json, os, re, shutil, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="kits-"))
(T / "state" / "library").mkdir(parents=True); (T / "state" / "control").mkdir()
(T / "defs").mkdir()
for f in (REPO / "toolkits").glob("*.json"):
    shutil.copy(f, T / "defs" / f.name)
(T / "defs" / "small.json").write_text(json.dumps({"id": "small", "title": "Small", "summary": "s", "consent": "c",
                                                   "packages": ["gdb", "tcpdump", "strace", "fail2ban"], "remove_after_hours": 24}))
(T / "defs" / "cap.json").write_text(json.dumps({"id": "cap", "title": "Cap", "summary": "s", "consent": "c", "packages": ["tcpdump"]}))
(T / "defs" / "bad.json").write_text(json.dumps({"id": "bad", "title": "Bad", "packages": ["--option", "x"]}))
(T / "defs" / "notjson.json").write_text("{")
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_KITS_ROOT=str(T / "cache"), HUB_KITS_DEFS=str(T / "defs"),
                  HUB_DPKG_STATUS=str(T / "dpkg-status"), HUB_POLICY_RC=str(T / "policy-rc.d"))
sys.path.insert(0, str(REPO))
from irate_box.root import kits  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

# The stand-in archive: name -> (version, depends). libc6 and strace are on the box already.
ARCHIVE = {"gdb": ("16.3-1", ["libpython3.13", "libc6"]), "libpython3.13": ("3.13.5-2", ["libc6"]),  # libpython3.13's source: python3.13
           "tcpdump": ("4.99.5-2", ["libpcap0.8"]), "libpcap0.8": ("1.10.5-2", []), "strace": ("6.13-1", []),
           "fail2ban": ("1.1.0-8", ["python3-systemd"]), "python3-systemd": ("235-1", []), "libc6": ("2.41-12", [])}
installed = {"libc6", "strace"}
versions = {}  # installed versions that differ from the archive's
BOARD = {"arch": "armhf"}
(T / "dpkg-status").write_text("".join(f"Package: {p}\nStatus: install ok installed\nVersion: {ARCHIVE[p][0]}\n\n" for p in sorted(installed)))
calls = []
policy_seen = []
def opt(cmd, key):
    for i, a in enumerate(cmd):
        if a == "-o" and cmd[i + 1].startswith(key + "="):
            return cmd[i + 1].split("=", 1)[1]
    return None
def closure(names, have):
    out, todo = set(), list(names)
    while todo:
        p = todo.pop()
        if p in out or p in have:
            continue
        out.add(p); todo += ARCHIVE[p][1]
    return out
class R:
    def __init__(self, out="", rc=0): self.stdout, self.stderr, self.returncode = out, "", rc
def fake(cmd, timeout=0, check=True):
    calls.append(cmd)
    name = Path(cmd[0]).name
    if name == "dpkg-query" and cmd[1] == "-W":
        return R("".join(f"{p}\tii \t{versions.get(p, ARCHIVE[p][0])}\n" for p in sorted(installed)))
    if name == "dpkg-query" and cmd[1] == "-L":
        return R("/usr/bin/fail2ban-server\n/usr/lib/systemd/system/fail2ban.service\n" if cmd[2] == "fail2ban" else f"/usr/bin/{cmd[2]}\n")
    if name == "dpkg-deb":
        try:
            d = json.loads(Path(cmd[2]).read_text())
        except ValueError:
            return R("", 2)  # as dpkg-deb on a damaged file
        fields = cmd[3:] or list(d)
        return R("".join(f"{k}: {d[k]}\n" for k in fields if k in d))
    if name == "systemctl":
        return R()
    if name == "apt-cache" and cmd[1] == "show":
        return R(f"Package: {cmd[-1]}\nVersion: {ARCHIVE[cmd[-1]][0]}\n" if cmd[-1] in ARCHIVE else "", 0 if cmd[-1] in ARCHIVE else 100)
    if name == "dpkg" and cmd[1:] == ["--print-architecture"]:
        return R(BOARD["arch"] + "\n")
    if name == "apt-get":
        words = [a for i, a in enumerate(cmd[1:], 1) if not a.startswith("-") and cmd[i - 1] != "-o"]
        verb, pkgs = words[0], words[1:]
        if verb == "update":
            return R()
        if verb == "install" and "--download-only" in cmd:
            status = Path(opt(cmd, "Dir::State::status")).read_text()
            have = set(re.findall(r"^Package: (\S+)", status, re.M))
            dest = Path(opt(cmd, "Dir::Cache::archives"))
            for p in closure(pkgs, have):
                v = ARCHIVE[p][0]
                (dest / f"{p}_{v}_armhf.deb").write_text(json.dumps({"Package": p, "Version": v, "Architecture": "armhf", "Depends": ", ".join(ARCHIVE[p][1]),
                                                                    **({"Source": "python3.13"} if p == "libpython3.13" else {})}))
            return R()
        if verb == "install":
            assert opt(cmd, "Dir::Etc::sourceparts") == "-" and opt(cmd, "Acquire::http::Proxy") == kits.DEAD_PROXY, cmd
            policy_seen.append(Path(os.environ["HUB_POLICY_RC"]).exists())
            for pin in [x for x in pkgs if "=" in x]:
                versions[pin.split("=")[0]] = pin.split("=", 1)[1]
            pkgs = [x.split("=")[0] for x in pkgs]
            index = (Path(opt(cmd, "Dir::Etc::sourcelist")).read_text().split("file:")[1].split()[0])
            avail = set(re.findall(r"^Package: (\S+)", (Path(index) / "Packages").read_text(), re.M))
            need = closure(pkgs, installed)
            if need - avail:
                raise ValueError(f"apt-get install: unable to locate {sorted(need - avail)}")
            installed.update(need)
            return R()
        if verb == "purge":
            installed.difference_update(pkgs)
            return R()
    raise AssertionError(f"unexpected command {cmd}")
kits.run = fake

defs = kits.definitions()
check("the shipped kits load: debug, build, capture, security", {"debug", "build", "capture", "security"} <= set(defs), sorted(defs))
check("a kit naming an option, or a broken file, is left out", "bad" not in defs and "notjson" not in defs)
check("every shipped kit has a consent text and a summary", all(defs[k].get("consent") and defs[k].get("summary") for k in ("debug", "build", "capture", "security")))
check("the debug kit has valgrind and perf in it (Tom)", {"valgrind", "linux-perf"} <= set(defs["debug"]["packages"]))
check("the build kit stays until removed by default", defs["build"]["remove_after_hours"] is None and defs["debug"]["remove_after_hours"] == 24)

# Fetch.
line = kits.fetch("small", budget_mb=10, log=lambda *a: None)
man = kits.manifest("small")
names = sorted(p["name"] for p in man["packages"])
check("fetch: the kit's packages and what they need, not what the box has", names == ["fail2ban", "gdb", "libpcap0.8", "libpython3.13", "python3-systemd", "tcpdump"], names)
check("  what the box has already is said", man["on_box"] == ["strace"] and "already on the box: strace" in line, line)
check("  each with its sha256, in the pool", all((kits.POOL / p["file"]).is_file() and kits.sha256(kits.POOL / p["file"]) == p["sha256"] for p in man["packages"]))
idx = (kits.POOL / "Packages").read_text()
check("  the index names each file with its size and sha256", idx.count("Filename: ./") == 6 and "SHA256: " in idx and "Package: gdb" in idx)
check("  one apt source, the pool, trusted", (kits.ROOT / "kits.list").read_text() == f"deb [trusted=yes] file:{kits.POOL} ./\n")
check("  apt was asked for download only, against a status file of its own", any("--download-only" in c and opt(c, "Dir::State::status") for c in calls))
check("  nothing left staged", not list(kits.ROOT.glob("stage-*")))
check("unchanged on a second fetch", "unchanged" in kits.fetch("small", budget_mb=10, log=lambda *a: None) and kits.manifest("small", "previous") is None)
# A new gdb: the old set becomes previous; another new one prunes the oldest.
ARCHIVE["gdb"] = ("16.3-2", ARCHIVE["gdb"][1])
kits.fetch("small", budget_mb=10, log=lambda *a: None)
check("a newer version: current is new, previous is the old set", {p["version"] for p in kits.manifest("small")["packages"] if p["name"] == "gdb"} == {"16.3-2"}
      and {p["version"] for p in kits.manifest("small", "previous")["packages"] if p["name"] == "gdb"} == {"16.3-1"})
check("  both gdbs in the pool and its index", (kits.POOL / "gdb_16.3-1_armhf.deb").exists() and (kits.POOL / "gdb_16.3-2_armhf.deb").exists()
      and "Version: 16.3-1" in (kits.POOL / "Packages").read_text())
ARCHIVE["gdb"] = ("16.3-3", ARCHIVE["gdb"][1])
kits.fetch("small", budget_mb=10, log=lambda *a: None)
check("a third: the oldest is pruned", not (kits.POOL / "gdb_16.3-1_armhf.deb").exists() and (kits.POOL / "gdb_16.3-2_armhf.deb").exists())
# The budget.
before = sorted(f.name for f in kits.POOL.iterdir())
ARCHIVE["gdb"] = ("16.3-4", ARCHIVE["gdb"][1])
big = (T / "big"); big.write_text("x")
real_fake = kits.run
def fat(cmd, **kw):
    r = real_fake(cmd, **kw)
    if "--download-only" in cmd:
        d = Path(opt(cmd, "Dir::Cache::archives")) / "gdb_16.3-4_armhf.deb"
        d.write_text(d.read_text() + " " * (2 << 20))
    return r
kits.run = fat
try:
    kits.fetch("small", budget_mb=1, log=lambda *a: None); check("over the budget: refused", False)
except ValueError as exc:
    check(f"over the budget: refused, the pool as it was ({exc})", "over its 1 MB budget" in str(exc) and sorted(f.name for f in kits.POOL.iterdir()) == before)
kits.run = real_fake
ARCHIVE["gdb"] = ("16.3-3", ARCHIVE["gdb"][1])

# Install, with no internet: only the pool, services kept stopped.
line = kits.install("small", hours=24, log=lambda *a: None)
st = kits.installed_state()["small"]
check("install: what it added is recorded, not what was there", sorted(st["added"]) == ["fail2ban", "gdb", "libpcap0.8", "libpython3.13", "python3-systemd", "tcpdump"], st)
check("  nothing on the box changed version: nothing said", "brought up to date" not in line and st["upgraded"] == [], st)
check("  policy-rc.d in place during the install, gone after", policy_seen == [True] and not Path(os.environ["HUB_POLICY_RC"]).exists())
check("  a service it brought is disabled and stopped", st["units"] == ["fail2ban.service"]
      and ["systemctl", "disable", "--now", "fail2ban.service"] in calls)
small_def = json.loads((T / "defs" / "small.json").read_text())
check("  a service the kit is for is left running (the debug kit's core dumps)", json.loads((REPO / "toolkits" / "debug.json").read_text())["services"] == ["systemd-coredump.socket"])
check("  removed after 24 h by default", 23.9 * 3600 < st["remove_at"] - time.time() <= 24 * 3600 and "removed after 24 h" in line)
check("  every apt call for it had only the pool and a dead proxy", all(opt(c, "Dir::Etc::sourceparts") == "-" for c in calls
      if c[0] == "apt-get" and "--download-only" not in c and opt(c, "Dir::Etc::sourcelist")))
calls.clear()
kits.remove("small", log=lambda *a: None)
d = json.loads((T / "defs" / "small.json").read_text()); d["services"] = ["fail2ban.service", "../evil.service"]
(T / "defs" / "small.json").write_text(json.dumps(d))
kits.install("small", hours=24, log=lambda *a: None)
check("a kit's own service is enabled and started, not disabled; a bad name ignored", ["systemctl", "enable", "--now", "fail2ban.service"] in calls
      and ["systemctl", "disable", "--now", "fail2ban.service"] not in calls and not any("evil" in " ".join(c) for c in calls)
      and kits.installed_state()["small"]["units"] == [])
d.pop("services"); (T / "defs" / "small.json").write_text(json.dumps(d))
# A package the box has, brought up to the cached version by the install: said and recorded.
kits.remove("small", log=lambda *a: None)
versions["libc6"] = "2.41-11"
def upgrading(cmd, **kw):
    r = real_fake(cmd, **kw)
    if Path(cmd[0]).name == "apt-get" and "install" in cmd and "--download-only" not in cmd:
        versions.pop("libc6", None)
    return r
kits.run = upgrading
line = kits.install("small", hours=24, log=lambda *a: None)
kits.run = real_fake
check("an install that upgrades a package the box had says so, and records it", "libc6 2.41-11 → 2.41-12" in line
      and kits.installed_state()["small"]["upgraded"] == ["libc6 2.41-11 → 2.41-12"], line)
# Fetching an installed kit still caches it whole (resolved without what kits added).
calls.clear()
ARCHIVE["tcpdump"] = ("4.99.5-3", ["libpcap0.8"])
kits.fetch("small", budget_mb=10, log=lambda *a: None)
check("fetching while installed still caches every package", len(kits.manifest("small")["packages"]) == 6, kits.manifest("small")["packages"])
# A second kit sharing tcpdump: removing the first keeps it.
kits.fetch("cap", budget_mb=10, log=lambda *a: None)
check("a kit whose packages are all installed by another still caches them", [p["name"] for p in kits.manifest("cap")["packages"]] == ["libpcap0.8", "tcpdump"]
      or sorted(p["name"] for p in kits.manifest("cap")["packages"]) == ["libpcap0.8", "tcpdump"])
installed.difference_update({"tcpdump", "libpcap0.8"})  # as if cap went in first
kits.install("cap", hours=None, log=lambda *a: None)
installed.update({"tcpdump", "libpcap0.8"})
check("installed with no removal time: kept", kits.installed_state()["cap"]["remove_at"] is None)
line = kits.remove("small", log=lambda *a: None)
check("remove: what it added goes, what another kit added stays", "tcpdump" in installed and "gdb" not in installed and "fail2ban" not in installed
      and "strace" in installed and "libc6" in installed, sorted(installed))
check("  and it is no longer listed", "small" not in kits.installed_state())
try:
    kits.remove("small"); check("removing what isn't installed: refused", False)
except ValueError:
    check("removing what isn't installed: refused", True)
# Expiry.
kits.set_removal("cap", 1)
s = kits.installed_state(); s["cap"]["remove_at"] = time.time() - 1; kits._write(kits.INSTALLED, s)
out = kits.expire()
check("expire: a kit whose time is up is removed", len(out) == 1 and "cap" not in kits.installed_state() and "tcpdump" not in installed, out)
# Tampering, and policy-rc.d that is someone else's.
victim = kits.POOL / kits.manifest("small")["packages"][0]["file"]
victim.write_text(victim.read_text() + "x")
check("a changed file is found", any("has changed since it was fetched" in p for p in kits.verify()), kits.verify())
kits.write_index()
check("  and left out of the index apt reads", f"Filename: ./{victim.name}" not in (kits.POOL / "Packages").read_text())
try:
    kits.install("small"); check("a cache that fails its check installs nothing", False)
except ValueError as exc:
    check("a cache that fails its check installs nothing", "fails its check" in str(exc))
kits.fetch("small", budget_mb=10, log=lambda *a: None)
Path(os.environ["HUB_POLICY_RC"]).write_text("#!/bin/sh\nexit 0\n")
try:
    kits.install("small"); check("a policy-rc.d of the box's own: refused, and left", False)
except ValueError:
    check("a policy-rc.d of the box's own: refused, and left", Path(os.environ["HUB_POLICY_RC"]).read_text() == "#!/bin/sh\nexit 0\n")
Path(os.environ["HUB_POLICY_RC"]).unlink()
for bad in ("../x", "--all", "Debug"):
    try:
        kits._kit(bad); check(f"no kit named {bad!r}", False)
    except ValueError:
        check(f"no kit named {bad!r}", True)
try:
    kits.install("small", hours=0); check("hours 0 refused", False)
except ValueError:
    check("hours 0 refused", True)

# The root helper's actions, and control/kits.json after each.
from irate_box.root import hub_control  # noqa: E402
msg = hub_control.ACTIONS["kit-install"]({"kit": "small", "hours": 2})
ks = json.loads((T / "state" / "control" / "kits.json").read_text())
check("kit-install through the helper; control/kits.json says so", "installed" in msg and "small" in ks["installed"] and ks["kits"]["small"]["cached"]["packages"] == 6, msg)
for bad in ({"kit": "../etc"}, {"kit": ["small"]}, {"kit": "small", "hours": "24"}, {"kit": "small", "hours": 0}):
    try:
        hub_control.ACTIONS["kit-install"](bad); check(f"the helper refuses {bad}", False)
    except ValueError:
        check(f"the helper refuses {bad}", True)
try:
    hub_control.ACTIONS["kit-fetch"]({"kit": "small", "budget_mb": 1 << 20}); check("a silly budget refused", False)
except ValueError:
    check("a silly budget refused", True)
# The doctor.
f = hub_control.kits_findings()
check("the doctor: the cache matches its manifests", any(x["check"] == "Toolkits' cache" and x["status"] == "ok" for x in f), f)
s = kits.installed_state(); s["small"]["at"] = time.time() - 9 * 86400; s["debug"] = {"added": [], "at": time.time() - 8 * 86400, "remove_at": None}
kits._write(kits.INSTALLED, s)
f = hub_control.kits_findings()
check("the doctor: debug installed for over a week, and a kit with no removal time, are warnings",
      any("debug kit has been installed for 8 days" in x["detail"] for x in f) and any(x["status"] == "warn" for x in f), f)
victim = kits.POOL / kits.manifest("small")["packages"][1]["file"]; victim.write_text("tampered")
check("the doctor: a changed file is a problem", any(x["status"] == "problem" for x in hub_control.kits_findings()))

# The hub's half: settings, and the librarian's step.
from irate_box.library import toolkits, librarian  # noqa: E402
queued = []
librarian._queue_root = lambda req: (queued.append(req), "rid")[1]
def offline(*a, **k):
    raise librarian.LibrarianError("no network in the tests")
librarian._open = offline  # nothing here reaches the internet
cfg = toolkits.settings()
check("settings: 500 MB, every kit kept current, each kit's own removal time", cfg["budget_mb"] == 500 and cfg["kits"]["build"]["remove_after"] is None
      and cfg["kits"]["debug"]["remove_after"] == 24 and all(k["keep_current"] for k in cfg["kits"].values()))
for bad in ({"budget_mb": 5}, {"kits": {"nope": {}}}, {"kits": {"debug": {"remove_after": 0}}}, {"kits": {"debug": {"keep_current": "yes"}}}):
    try:
        toolkits.set_settings(bad); check(f"settings refuse {bad}", False)
    except librarian.LibrarianError:
        check(f"settings refuse {bad}", True)
toolkits.set_settings({"kits": {"debug": {"remove_after": 4}}})
check("install uses the kit's removal time unless given", toolkits.action({"action": "install", "kit": "debug"}) == "rid"
      and queued[-1] == {"action": "kit-install", "kit": "debug", "hours": 4})
check("the page names a kit, never packages", queued[-1].keys() == {"action", "kit", "hours"})
queued.clear()
s = kits.installed_state(); s["small"]["remove_at"] = time.time() - 5; kits._write(kits.INSTALLED, s)
(T / "state" / "control" / "kits.json").write_text(json.dumps(kits.status()))
line = toolkits.step({"check_every_hours": 24})
check("the librarian: an expired install is removed, and one due kit fetched", [q["action"] for q in queued] == ["kit-expire", "kit-fetch"], queued)
queued.clear()
toolkits.step({"check_every_hours": 24})
check("  the next run fetches the next kit, not the same one again", [q.get("kit") for q in queued if q["action"] == "kit-fetch"] != [None]
      and len({q.get("kit") for q in queued if q["action"] == "kit-fetch"}) == 1, queued)
toolkits.set_settings({"kits": {k: {"keep_current": False} for k in toolkits.definitions()}})
queued.clear()
check("nothing kept current: nothing fetched", toolkits.step({"check_every_hours": 24}, now=time.time()) in (None, "removing the kits whose time is up")
      and not any(q["action"] == "kit-fetch" for q in queued), queued)

# Roll back (step 28): the previous set becomes current; an installed kit's packages go back.
s_ = kits.installed_state(); s_.pop("debug", None); kits._write(kits.INSTALLED, s_)
kits.fetch("small", budget_mb=10, log=lambda *a: None)
cur_gdb = {p["version"] for p in kits.manifest("small")["packages"] if p["name"] == "gdb"}
prev_gdb = {p["version"] for p in kits.manifest("small", "previous")["packages"] if p["name"] == "gdb"}
calls.clear()
line = kits.rollback("small", log=lambda *a: None)
check("roll back: current and previous swap", {p["version"] for p in kits.manifest("small")["packages"] if p["name"] == "gdb"} == prev_gdb
      and {p["version"] for p in kits.manifest("small", "previous")["packages"] if p["name"] == "gdb"} == cur_gdb, line)
check("  an installed kit's packages are put back, offline, downgrades allowed", any("--allow-downgrades" in c and opt(c, "Dir::Etc::sourceparts") == "-" for c in calls)
      or "small" not in kits.installed_state(), calls[-3:])
kits.rollback("small", log=lambda *a: None)
check("rolling back twice is back where it was", {p["version"] for p in kits.manifest("small")["packages"] if p["name"] == "gdb"} == cur_gdb)
try:
    kits.rollback("cap"); check("no previous set: said", False)
except ValueError as exc:
    check("no previous set: said", "no previous version" in str(exc))
# Step 27, the security kit: debian-cis as a mirror, debsecan's data as a feed, the cached packages
# in dpkg's status format, and how fresh they are.
sec = toolkits.definitions()["security"]
check("the security kit: Lynis, debsecan, fail2ban, nmap and the rest; debian-cis from git; debsecan's feed; daily",
      {"lynis", "debsecan", "python3-apt", "fail2ban", "nmap", "tcpdump", "tshark", "strace", "socat", "python3-scapy"} == set(sec["packages"])
      and sec["git"][0]["upstream"] == "https://github.com/ovh/debian-cis" and sec["feeds"] == ["debsecan"] and sec["refresh_hours"] == 24)
ks = (kits.ROOT / "kits-status").read_text()
check("  with the source package where it differs (debsecan matches by source)", "Package: libpython3.13\nStatus: install ok installed\nSource: python3.13\n" in ks, ks)
check("kits-status lists every cached package as dpkg's status file would", "Package: gdb\nStatus: install ok installed\nVersion: 16.3-3\n" in ks
      and ks.count("Package: ") == len({(p["name"], p["version"]) for p in kits._referenced().values()}), ks[:200])
toolkits.set_settings({"kits": {"security": {"keep_current": True}}})
from irate_box.library import mirrors  # noqa: E402
from unittest import mock  # noqa: E402
added = []
with mock.patch.object(mirrors, "load", return_value=[]), mock.patch.object(mirrors, "add", side_effect=lambda m: added.append(m)):
    toolkits.ensure_git_sources()
check("debian-cis is mirrored once the security kit is kept current: shallow, master, public", len(added) == 1 and added[0]["name"] == "debian-cis"
      and added[0]["branches"] == ["master"] and added[0]["history"] == "shallow", added)
with mock.patch.object(mirrors, "load", return_value=[{"upstream": "https://github.com/OVH/debian-cis/"}]), mock.patch.object(mirrors, "add") as add:
    toolkits.ensure_git_sources()
check("  not again when a mirror of it exists", not add.called)
import io, zlib  # noqa: E402
(T / "os-release").write_text('NAME="Debian"\nVERSION_CODENAME=trixie\n')
toolkits.OS_RELEASE = str(T / "os-release")
good = zlib.compress(b"VERSION 1\nCVE-2026-0001,,x\n")
class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
fetched = []
with mock.patch.object(librarian, "_open", side_effect=lambda url, **k: (fetched.append(url), Resp(good))[1]):
    line = toolkits.refresh_feed()
check("debsecan's data: this suite's and GENERIC, from Debian's tracker", fetched == [toolkits.FEED_URL + "trixie", toolkits.FEED_URL + "GENERIC"]
      and (toolkits.FEED / "trixie").read_bytes() == good and "trixie" in line, fetched)
check("  and its source for debsecan, a file: URL", toolkits.feeds()["debsecan"]["source"] == f"file://{toolkits.FEED}/" and toolkits.feeds()["debsecan"]["fetched"])
with mock.patch.object(librarian, "_open", side_effect=lambda url, **k: Resp(zlib.compress(b"<html>captive portal</html>"))):
    try:
        toolkits.refresh_feed(); check("data in another format is refused, the last copy kept", False)
    except librarian.LibrarianError:
        check("data in another format is refused, the last copy kept", (toolkits.FEED / "trixie").read_bytes() == good)
(T / "os-release").write_text("NAME=x\n")
try:
    toolkits.refresh_feed(); check("no suite: said", False)
except librarian.LibrarianError as exc:
    check("no suite: said", "suite is unknown" in str(exc))
(T / "os-release").write_text('VERSION_CODENAME=trixie\n')
queued.clear(); fetched.clear()
with mock.patch.object(mirrors, "load", return_value=[{"upstream": "https://github.com/ovh/debian-cis"}]), \
     mock.patch.object(librarian, "_open", side_effect=lambda url, **k: (fetched.append(url), Resp(good))[1]):
    os.utime(toolkits.FEED / "trixie", (time.time() - 2 * 86400,) * 2)
    st_ = librarian._read_json(toolkits.STATE, {}); st_.pop("feed:debsecan", None); librarian._write_json(toolkits.STATE, st_)
    toolkits.step({"check_every_hours": 24})
    n = len(fetched)
    toolkits.step({"check_every_hours": 24})
check("the librarian refreshes the feed daily, not every run", n == 2 and len(fetched) == 2, fetched)
# Freshness, in the doctor: the security kit's cache and its data.
def man_at(days):
    m = kits.manifest("small"); m["id"] = "security"; m["fetched"] = time.time() - days * 86400
    kits._write(kits.MANIFESTS / "security.json", m)
real_state = hub_control.STATE
hub_control.STATE = T / "state"
man_at(10); os.utime(toolkits.FEED / "trixie", (time.time() - 40 * 86400,) * 2)
f = [x for x in hub_control.kits_findings() if x["check"] == "Security tools' freshness"]
check("the doctor: the security kit's cache over a week old is a warning", any(x["status"] == "warn" and "security kit's cache is 10 days" in x["detail"] for x in f), f)
man_at(40)
f = [x for x in hub_control.kits_findings() if x["check"] == "Security tools' freshness"]
check("  over a month, a problem", any(x["status"] == "problem" and "security kit's cache is 40 days" in x["detail"] for x in f), f)
hub_control.STATE = real_state
# 64-bit only (Tom, 2026-10-06): bpftrace, bcc and bpftool are left out on a 32-bit board.
check("the debug kit marks bpftrace, bcc and bpftool 64-bit only", set(kits.definitions()["debug"]["needs_64bit"]) == {"bpftrace", "bpfcc-tools", "bpftool"})
from irate_box.hub import kitdefs  # noqa: E402
dbg = kits.definitions()["debug"]
for arch_, gone in (("armhf", {"bpftrace", "bpfcc-tools", "bpftool"}), ("arm64", set()), ("amd64", set()), ("i386", {"bpftrace", "bpfcc-tools", "bpftool"})):
    keep, left = kitdefs.packages_for(dbg, arch_)
    check(f"  on {arch_}: {'left out' if gone else 'all kept'}", set(left) == gone and not (gone & set(keep)) and "gdb" in keep, (keep, left))
d = json.loads((T / "defs" / "small.json").read_text()); d["needs_64bit"] = ["gdb"]
(T / "defs" / "small.json").write_text(json.dumps(d))
ARCHIVE["gdb"] = ("16.3-9", ARCHIVE["gdb"][1])
calls.clear()
line = kits.fetch("small", budget_mb=10, log=lambda *a: None)
check("fetched on a 32-bit board: left out, and said", "left out on this armhf board (64-bit only): gdb" in line
      and "gdb" not in {p["name"] for p in kits.manifest("small")["packages"]} and kits.manifest("small")["left_out"] == ["gdb"]
      and not any("gdb" in c for c in calls if c[0] == "apt-get"), line)
check("  and the card is told", kits.status()["kits"]["small"]["cached"]["left_out"] == ["gdb"])
BOARD["arch"] = "arm64"
line = kits.fetch("small", budget_mb=10, log=lambda *a: None)
check("on a 64-bit board it comes", "gdb" in {p["name"] for p in kits.manifest("small")["packages"]} and not kits.manifest("small")["left_out"], line)
BOARD["arch"] = "armhf"
d.pop("needs_64bit"); (T / "defs" / "small.json").write_text(json.dumps(d))
kits.fetch("small", budget_mb=10, log=lambda *a: None)
(T / "defs" / "odd.json").write_text(json.dumps({"id": "odd", "title": "Odd", "packages": ["gdb"], "needs_64bit": ["notinkit"]}))
check("a kit marking a package it doesn't have is left out", "odd" not in kits.definitions())
(T / "defs" / "odd.json").unlink()
# Step 38: the owner's own kits, and extra tools in a shipped kit.
line = hub_control.ACTIONS["kit-define"]({"kit": {"id": "radio", "title": "Radio tools", "summary": "", "packages": ["gdb", "tcpdump", "gdb"],
                                                  "remove_after_hours": 4}})
own = kits.definitions().get("radio")
check("an own kit: kept by root, marked as the owner's, repeats dropped, its consent names the packages", own and own["owner"] and own["packages"] == ["gdb", "tcpdump"]
      and "gdb, tcpdump" in own["consent"] and "added" in line and (kits.ROOT / "owner" / "radio.json").stat().st_mode & 0o777 == 0o644, (line, own))
check("  and the hub sees it too (kitdefs, readable)", "radio" in toolkits.definitions() and toolkits.settings()["kits"]["radio"]["remove_after"] == 4)
for bad, why in (({"id": "debug", "title": "x", "packages": ["gdb"]}, "one of the box's own kits"), ({"id": "r2", "title": "x", "packages": ["no-such-pkg"]}, "not in this box's package lists"),
                 ({"id": "r2", "title": "x", "packages": ["--force"]}, "Debian package names"), ({"id": "r2", "title": "", "packages": ["gdb"]}, "title"),
                 ({"id": "../x", "title": "x", "packages": ["gdb"]}, "id"), ({"id": "extras", "title": "x", "packages": ["gdb"]}, "id"),
                 ({"id": "r2", "title": "x", "packages": []}, "at least one")):
    try:
        kits.define(bad); check(f"define refuses: {why}", False)
    except ValueError as exc:
        check(f"define refuses: {why}", why in str(exc), str(exc))
kits.fetch("radio", budget_mb=10, log=lambda *a: None)
check("an own kit is fetched like the others", kits.manifest("radio") and {p["name"] for p in kits.manifest("radio")["packages"]} >= {"gdb"})
kits.install("radio", hours=1, log=lambda *a: None)
try:
    kits.undefine("radio"); check("an installed own kit can't be deleted", False)
except ValueError:
    check("an installed own kit can't be deleted", True)
kits.remove("radio", log=lambda *a: None)
print_ = hub_control.ACTIONS["kit-undefine"]({"kit": "radio"})
check("deleted: its definition and its cache", "radio" not in kits.definitions() and not kits.manifest("radio")
      and json.loads((T / "state" / "control" / "kits.json").read_text())["kits"].get("radio") is None, print_)
try:
    kits.undefine("debug"); check("a shipped kit can't be deleted", False)
except ValueError:
    check("a shipped kit can't be deleted", True)
line = hub_control.ACTIONS["kit-extra"]({"kit": "capture", "packages": ["gdb", "tcpdump"]})
cap = kits.definitions()["capture"]
check("extra tools in a shipped kit: added to its packages, one it has already left out", cap["packages"] == ["tcpdump", "tshark", "gdb"] and cap["extra"] == ["gdb"], (line, cap))
hub_control.ACTIONS["kit-extra"]({"kit": "capture", "packages": []})
check("  and cleared", kits.definitions()["capture"]["packages"] == ["tcpdump", "tshark"] and "extra" not in kits.definitions()["capture"])
for bad in ({"kit": "radio", "packages": ["gdb"]}, {"kit": "capture", "packages": ["no-such-pkg"]}, {"kit": "capture", "packages": "gdb"}):
    try:
        hub_control.ACTIONS["kit-extra"](bad); check(f"extra refused: {bad}", False)
    except ValueError:
        check(f"extra refused: {bad}", True)
queued.clear()
toolkits.action({"action": "define", "kit": {"title": "Debug", "packages": "gdb, strace  tcpdump"}})
check("the page's define: an id from the name (my- when it would clash), packages split", queued[-1]["kit"]["id"] == "my-debug"
      and queued[-1]["kit"]["packages"] == ["gdb", "strace", "tcpdump"], queued[-1])
toolkits.action({"action": "extra", "kit": "capture", "packages": ["gdb"]})
check("  and extra", queued[-1] == {"action": "kit-extra", "kit": "capture", "packages": ["gdb"]})
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
