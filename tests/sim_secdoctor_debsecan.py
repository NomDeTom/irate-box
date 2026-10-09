# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor, stage 1 (next-work plan step 29, security-doctor-plan §4, §7.1–2),
offline: every finding in one shape; debsecan's output (recorded, --format summary) turned into
findings, a released fix a problem only when remotely exploitable or high urgency (Tom), unfixed
ones listed and not counted, a kit's cache flagged; the run with no data, and with a stand-in
debsecan on the installed packages and the kits' cache; the joint report merging what two sources
say about one package. The real debsecan was run on the Lyra (next-work-plan, step 29).
python3 tests/sim_secdoctor_debsecan.py"""
import json, os, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="secdoc-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_KITS_ROOT=str(T / "kits"), HUB_OS_RELEASE=str(T / "os-release"))
sys.path.insert(0, str(REPO))
from irate_box.root import secdoctor as sd  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

f = sd.F("x", "t", "warn", "d")
check("every finding has the shared shape: source, about, accepted", f["source"] == "doctor" and f["about"] is None and f["accepted"] is None
      and set(f) == {"id", "title", "status", "detail", "fix", "ref", "source", "about", "accepted"})

# debsecan's --format summary, as it prints it (recorded on the Lyra, 2026-10-06, and two made up).
INSTALLED = """CVE-2026-12725 dnsmasq-base (fixed, remotely exploitable)
CVE-2026-12969 dnsmasq-base (fixed)
CVE-2026-41991 gzip (fixed, low urgency)
CVE-2026-50001 libc6 (fixed, high urgency)
CVE-2025-0001 libc6
CVE-2024-9999 perl (low urgency)
"""
CACHED = """CVE-2026-60001 tcpdump (fixed)
CVE-2026-60002 python3.13
"""
fake = T / "debsecan"
fake.write_text("import sys\nargs = sys.argv[1:]\nprint(open(%r if '--status' in args else %r).read(), end='')\n" % (str(T / "cached.txt"), str(T / "installed.txt")))
(T / "installed.txt").write_text(INSTALLED); (T / "cached.txt").write_text(CACHED)
(T / "os-release").write_text("VERSION_CODENAME=trixie\n")
got = sd._debsecan([sys.executable, str(fake)], {}, "trixie")
check("the summary is read: each CVE, fixed or not, remote, urgency", got["dnsmasq-base"][0] == ("CVE-2026-12725", {"fixed": True, "remote": True, "urgency": ""})
      and got["gzip"][0][1]["urgency"] == "low" and got["libc6"][1] == ("CVE-2025-0001", {"fixed": False, "remote": False, "urgency": ""}), got)
fs = {x["id"]: x for x in sd.debsecan_findings(got, sd._debsecan([sys.executable, str(fake)], {}, "trixie", T / "x"), {"tcpdump": ["capture", "debug"]}, time.time())}
check("a released fix that is remotely exploitable: a problem", fs["debsecan-dnsmasq-base"]["status"] == "problem"
      and "CVE-2026-12725 (remotely exploitable)" in fs["debsecan-dnsmasq-base"]["detail"])
check("  one of high urgency: a problem", fs["debsecan-libc6"]["status"] == "problem" and "(high urgency)" in fs["debsecan-libc6"]["detail"])
check("  otherwise a warning", fs["debsecan-gzip"]["status"] == "warn")
check("  about Debian's security updates (one item on the page with the Security page's), from debsecan, with the fix",
      fs["debsecan-gzip"]["about"] == {"kind": "setting", "key": "security-updates"}
      and fs["debsecan-gzip"]["source"] == "debsecan" and "security updates" in fs["debsecan-gzip"]["fix"])
check("unfixed ones: listed in one line that isn't counted against the box", "debsecan-perl" not in fs and fs["debsecan-unfixed"]["status"] == "ok"
      and "1 installed packages have CVEs Debian hasn't fixed yet (listed, not counted): perl." in fs["debsecan-unfixed"]["detail"],
      fs["debsecan-unfixed"]["detail"])
check("a kit's cache with a version since fixed: a warning on each kit that holds it", fs["debsecan-kit-capture"]["status"] == "warn"
      and fs["debsecan-kit-debug"]["about"] == {"kind": "kit", "key": "debug"} and "Refresh the kit" in fs["debsecan-kit-capture"]["fix"])
check("  an unfixed one in the cache flags nothing", not any("python3.13" in x["detail"] for k, x in fs.items() if k.startswith("debsecan-kit")))

# One finding per source package.
two = {"perl": [("CVE-1", {"fixed": True, "remote": False, "urgency": ""})], "perl-base": [("CVE-1", {"fixed": True, "remote": False, "urgency": ""})],
       "libperl5.40": [("CVE-2", {"fixed": True, "remote": True, "urgency": ""})]}
g = [x for x in sd.debsecan_findings(two, {}, {}, None, {"perl-base": "perl", "libperl5.40": "perl"}) if x["id"].startswith("debsecan-perl")]
check("binaries of one source package: one finding, naming them, its CVEs together, the worst status", len(g) == 1
      and g[0]["detail"].startswith("In libperl5.40, perl, perl-base. 2 fixed: CVE-1, CVE-2 (remotely exploitable).")
      and g[0]["status"] == "problem" and g[0]["about"] == {"kind": "setting", "key": "security-updates"}, g)
sd._sources = lambda: {}
# The step itself: no data yet; no debsecan anywhere; a stand-in in place of the real one.
out = sd.step_debsecan({})
check("no tracker data yet: a warning saying how it comes", len(out) == 1 and out[0]["id"] == "debsecan-data" and out[0]["status"] == "warn")
(sd.DEBSECAN_FEED).mkdir(parents=True); (sd.DEBSECAN_FEED / "trixie").write_bytes(b"x")
real = sd._debsecan_command
sd._debsecan_command = lambda work: (None, "debsecan is not on the box, and the security kit's cache doesn't hold it")
out = sd.step_debsecan({})
check("no debsecan: a warning, pointing at the security kit", out[0]["id"] == "debsecan-tool" and "Security kit" in out[0]["fix"])
sd._debsecan_command = lambda work: ([sys.executable, str(fake)], {})
(T / "kits" / "manifests").mkdir(parents=True)
(T / "kits" / "manifests" / "capture.json").write_text(json.dumps({"packages": [{"name": "tcpdump", "file": "tcpdump_1_armhf.deb", "size": 1, "sha256": "0" * 64}]}))
(T / "kits" / "manifests" / "capture.previous.json").write_text(json.dumps({"packages": [{"name": "tcpdump", "file": "tcpdump_1_armhf.deb", "size": 1, "sha256": "0" * 64}]}))
(T / "kits" / "kits-status").write_text("Package: tcpdump\n")
out = {x["id"]: x for x in sd.step_debsecan({})}
check("with debsecan: the installed packages and the kits' cache, the previous sets not counted twice",
      "debsecan-dnsmasq-base" in out and out["debsecan-kit-capture"]["detail"].startswith("Cached versions of tcpdump") and "debsecan-kit-capture.previous" not in out, sorted(out))
sd._debsecan_command = real
# Unpacking from the cache refuses a damaged file (it runs as root).
(T / "kits" / "pool").mkdir()
for n in ("debsecan_0.4.20.1_all.deb", "python3-apt_3.0.0_armhf.deb", "python-apt-common_3.0.0_all.deb"):
    (T / "kits" / "pool" / n).write_text("not what was fetched")
(T / "kits" / "manifests" / "security.json").write_text(json.dumps({"id": "security", "packages": [
    {"name": "debsecan", "file": "debsecan_0.4.20.1_all.deb", "size": 1, "sha256": "0" * 64}]}))
if not Path("/usr/bin/debsecan").exists():
    cmd, why = sd._debsecan_command(T / "work")
    check("a security kit file that fails its check is not run", cmd is None and "fails its check" in why, why)

# The joint report: two sources about one package become one item.
steps = [{"findings": [sd.F("a", "nginx is old", "warn", "", "upgrade", about={"kind": "package", "key": "nginx"}),
                       sd.F("ok1", "fine", "ok", "")]},
         {"findings": [sd.F("debsecan-nginx", "nginx: a fix", "problem", "", "install updates", source="debsecan", about={"kind": "package", "key": "nginx"}),
                       sd.F("debsecan-unfixed", "unfixed", "ok", "", source="debsecan")]}]
j = sd.joint(steps)
check("joint: one item for the package, both sources, the worst status and its fix", len(j["items"]) == 1 and j["items"][0]["sources"] == ["doctor", "debsecan"]
      and j["items"][0]["status"] == "problem" and j["items"][0]["fix"] == "install updates", j)
check("  each source's counts", j["sources"] == {"doctor": {"problem": 0, "warn": 1, "ok": 1}, "debsecan": {"problem": 1, "warn": 0, "ok": 1}}, j["sources"])
check("the step is in the doctor's list", "debsecan" in [x[0] for x in sd.STEPS])
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
