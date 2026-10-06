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
ARCHIVE = {"gdb": ("16.3-1", ["libpython3.13", "libc6"]), "libpython3.13": ("3.13.5-2", ["libc6"]),
           "tcpdump": ("4.99.5-2", ["libpcap0.8"]), "libpcap0.8": ("1.10.5-2", []), "strace": ("6.13-1", []),
           "fail2ban": ("1.1.0-8", ["python3-systemd"]), "python3-systemd": ("235-1", []), "libc6": ("2.41-12", [])}
installed = {"libc6", "strace"}
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
        return R("".join(f"{p}\tii \n" for p in sorted(installed)))
    if name == "dpkg-query" and cmd[1] == "-L":
        return R("/usr/bin/fail2ban-server\n/usr/lib/systemd/system/fail2ban.service\n" if cmd[2] == "fail2ban" else f"/usr/bin/{cmd[2]}\n")
    if name == "dpkg-deb":
        d = json.loads(Path(cmd[2]).read_text())
        fields = cmd[3:] or list(d)
        return R("".join(f"{k}: {d[k]}\n" for k in fields if k in d))
    if name == "systemctl":
        return R()
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
                (dest / f"{p}_{v}_armhf.deb").write_text(json.dumps({"Package": p, "Version": v, "Architecture": "armhf", "Depends": ", ".join(ARCHIVE[p][1])}))
            return R()
        if verb == "install":
            assert opt(cmd, "Dir::Etc::sourceparts") == "-" and opt(cmd, "Acquire::http::Proxy") == kits.DEAD_PROXY, cmd
            policy_seen.append(Path(os.environ["HUB_POLICY_RC"]).exists())
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
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
