# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Security page's offers for the inherited image: the kernel's link
protections and its addresses and log, and login accounts in the docker and disk groups. Each is
found, fixed and undone against a fake /proc/sys, /etc/group and /etc/passwd, with a stand-in
gpasswd; uninstall's undo_all puts everything back. python3 tests/sim_security_image.py
"""

import os
import re
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="sec-image-"))
PROC = T / "proc"
for key, v in {
    "fs/protected_symlinks": "0",
    "fs/protected_hardlinks": "0",
    "kernel/kptr_restrict": "0",
    "kernel/dmesg_restrict": "0",
}.items():
    (PROC / key).parent.mkdir(parents=True, exist_ok=True)
    (PROC / key).write_text(v + "\n")
(T / "group").write_text("root:x:0:\ndisk:x:6:lyra\ndocker:x:990:lyra,svc\nsudo:x:27:lyra\n")
(T / "passwd").write_text(
    "root:x:0:0::/root:/bin/bash\nlyra:x:1000:1000::/home/lyra:/bin/bash\nsvc:x:1001:1001::/srv:/usr/sbin/nologin\n"
)
BIN = T / "bin"
BIN.mkdir()
# gpasswd -d USER GROUP / -a USER GROUP, on the fake group file.
(BIN / "gpasswd").write_text(f"""#!/usr/bin/env python3
import sys
op, user, group = sys.argv[1:4]
p = "{T / "group"}"
lines = open(p).read().splitlines()
for i, l in enumerate(lines):
    f = l.split(":")
    if f[0] == group:
        m = [x for x in f[3].split(",") if x]
        m = [x for x in m if x != user] if op == "-d" else m + [user]
        f[3] = ",".join(m); lines[i] = ":".join(f)
open(p, "w").write("\\n".join(lines) + "\\n")
""")
(BIN / "gpasswd").chmod(0o755)
# apt and systemd for linux-sysctl-defaults: apt has it when T/apt-candidate exists; installing it
# marks it installed; systemd-sysctl then sets the link protections from it.
for name, body in {
    "apt-cache": f"import os\nprint('Candidate: ' + ('4.12.1' if os.path.exists('{T}/apt-candidate') else '(none)'))",
    "dpkg-query": f"import os\nprint('install ok installed' if os.path.exists('{T}/installed') else 'unknown ok not-installed', end='')",
    "apt-get": f"import sys, pathlib\nm = pathlib.Path('{T}/installed')\nopen('{T}/apt-log', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
    "m.touch() if 'install' in sys.argv else m.unlink(missing_ok=True)",
    "passwd": f"import sys, pathlib\nst = pathlib.Path('{T}/root-pw')\n"
    "if sys.argv[1] == '-S': print('root ' + st.read_text().strip() + ' 2026-10-07 0 99999 7 -1')\n"
    "elif sys.argv[1] == '-l': st.write_text('L')\n"
    "elif sys.argv[1] == '-u': st.write_text('P')\n",
    "systemctl": f"import sys, os\nopen('{T}/systemctl-log', 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
    f"if sys.argv[1:] == ['restart', 'systemd-sysctl.service'] and os.path.exists('{T}/installed'):\n"
    f"    [open('{PROC}/fs/' + k, 'w').write('1\\n') for k in ('protected_symlinks', 'protected_hardlinks')]",
    "sshd": f"import sys, os\nif '-T' in sys.argv: print('passwordauthentication ' + ('no' if os.path.exists('{T}/pw-off') else 'yes')); print('authorizedkeysfile %h/.ssh/keys_%u .ssh/authorized_keys')",
    "findmnt": f"import os\nprint('/dev/mmcblk0p1 ext4' if os.path.exists('{T}/log-on-card') else '/dev/zram1 ext4')",
}.items():
    (BIN / name).write_text("#!/usr/bin/env python3\n" + body + "\n")
    (BIN / name).chmod(0o755)
(T / "root-pw").write_text("P")
(T / "firstrun").write_text("armbian first login pending\n")
os.environ.update(HUB_ARMBIAN_FIRSTRUN=str(T / "firstrun"))
# The gates: sudoers, apt, ramlog and journald files of the fixture's own.
(T / "sudoers.d").mkdir()
(T / "sudoers.d" / "claude-temp").write_text("lyra ALL=(ALL) NOPASSWD: ALL\n")
(T / "sudoers").write_text("root ALL=(ALL:ALL) ALL\n%sudo ALL=(ALL:ALL) ALL\n@includedir /etc/sudoers.d\n")
(T / "apt" / "sources.list.d").mkdir(parents=True)
(T / "apt" / "trusted.gpg.d").mkdir()
(T / "keyrings").mkdir()
(T / "apt" / "sources.list.d" / "home:mPWRD:OS.list").write_text(
    "deb http://download.opensuse.org/repositories/home:/mPWRD:/OS/Debian_13/ /\n"
)
(T / "apt" / "trusted.gpg.d" / "home_mPWRD_OS.gpg").write_bytes(b"KEY")
(T / "apt" / "trusted.gpg.d" / "debian-archive-trixie-stable.asc").write_text("debian\n")
(T / "ramlog").write_text("# ramlog\nENABLED=true\nSIZE=50M\n")
(T / "log").mkdir()
os.environ.update(
    HUB_SUDOERS=str(T / "sudoers"),
    HUB_SUDOERS_DIR=str(T / "sudoers.d"),
    HUB_APT_DIR=str(T / "apt"),
    HUB_KEYRINGS_DIR=str(T / "keyrings"),
    HUB_RAMLOG_DEFAULT=str(T / "ramlog"),
    HUB_JOURNALD_DROPIN=str(T / "journald.conf.d" / "irate-box.conf"),
    HUB_LOG_DIR=str(T / "log"),
    HUB_SSHD_DROPIN=str(T / "sshd_config.d" / "01-irate-box.conf"),
)
os.environ.update(
    HUB_ETC_DIR=str(T / "etc"),
    HUB_PROC_SYS=str(PROC),
    HUB_GROUP_FILE=str(T / "group"),
    HUB_PASSWD_FILE=str(T / "passwd"),
    HUB_SYSCTL_DROPIN=str(T / "sysctl.d" / "60-irate-box.conf"),
    HUB_AUTOUPDATE_CONF=str(T / "apt.conf.d" / "52irate-box-autoupdate"),
    PATH=f"{BIN}:{os.environ['PATH']}",
)
sys.path.insert(0, str(REPO))
from irate_box import msg
from irate_box.finding import message_ids as said
from irate_box.msg import args_of
from irate_box.msg import ids as msg_ids
from irate_box.root import security

fails = 0


def check(name, cond, _info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name)
    fails += not cond


def findings():
    rec = security.load_record()
    return {f["id"]: f for f in security.kernel_findings(rec) + security.group_findings(rec)}


def sysctl(key):
    return (PROC / key.replace(".", "/")).read_text().strip()


f = findings()
check(
    "links off: a problem, with a button",
    f["kernel-links"]["status"] == "problem"
    and [a["choice"] for a in f["kernel-links"]["actions"]] == ["kernel-links-on"],
    f["kernel-links"],
)
check(
    "addresses and log: a warning, with a button",
    f["kernel-info"]["status"] == "warn" and f["kernel-info"]["actions"][0]["choice"] == "kernel-info-on",
)
check(
    "docker: lyra listed, not the nologin service account",
    f["group-docker"]["status"] == "warn"
    and f["group-docker"]["evidence"] == ["lyra"]
    and said(f["group-docker"]) == {"group.members", "group-docker.why", "group.fix"}
    # a setting per account: in the group (as now), or not
    and [(a["choice"], a["on"]) for a in f["group-docker"]["actions"]]
    == [("group-undo:lyra@docker", True), ("group-drop:lyra@docker", False)],
    f["group-docker"],
)
check("disk: lyra listed", f["group-disk"]["status"] == "warn")
check("sudo is not a group this page touches", "group-sudo" not in f)

print(security.fix("kernel-links-on", None))
check(
    "links on: set now",
    sysctl("fs.protected_symlinks") == "1" and sysctl("fs.protected_hardlinks") == "1",
)
drop = Path(os.environ["HUB_SYSCTL_DROPIN"])
check(
    "links on: and at every boot",
    drop.exists() and "fs.protected_symlinks = 1" in drop.read_text(),
)
f = findings()
check(
    "links on: ok, with Undo",
    f["kernel-links"]["status"] == "ok" and f["kernel-links"]["actions"][0]["choice"] == "kernel-links-undo",
)
print(security.fix("kernel-info-on", None))
check(
    "both sets in the one file",
    "kernel.dmesg_restrict = 1" in drop.read_text() and "fs.protected_hardlinks = 1" in drop.read_text(),
)
print(security.fix("kernel-links-undo", None))
check(
    "links undone: the old values back, the file keeps the other set",
    sysctl("fs.protected_symlinks") == "0"
    and "protected_symlinks" not in drop.read_text()
    and "kptr_restrict" in drop.read_text(),
)

(T / "apt-candidate").touch()
f = findings()
check(
    "links off, Debian's defaults to be had: offered first, the hub's own two beside it",
    [a["choice"] for a in f["kernel-links"]["actions"]] == ["kernel-links-debian", "kernel-links-on"]
    and "kernel-links.debian.fix" in said(f["kernel-links"])
    and f["kernel-links"]["notes"][0]["args"]["package"] == "linux-sysctl-defaults"
    and f["kernel-links"]["actions"][0].get("confirm"),
    f["kernel-links"],
)
print(security.fix("kernel-links-debian", None))
check(
    "Debian's defaults: installed, applied, on now",
    (T / "installed").exists()
    and sysctl("fs.protected_symlinks") == "1"
    and "install -y --no-install-recommends linux-sysctl-defaults" in (T / "apt-log").read_text(),
)
check(
    "  not in the hub's own file (Debian's carries them); the other set still there",
    "protected_symlinks" not in drop.read_text() and "kptr_restrict" in drop.read_text(),
)
f = findings()
check(
    "  ok, with Undo",
    f["kernel-links"]["status"] == "ok" and f["kernel-links"]["actions"][0]["choice"] == "kernel-links-undo",
)
print(security.fix("kernel-links-undo", None))
check(
    "  undone: the package removed, the old values back",
    not (T / "installed").exists()
    and "remove -y linux-sysctl-defaults" in (T / "apt-log").read_text()
    and sysctl("fs.protected_symlinks") == "0",
)
(T / "apt-candidate").unlink()


# Root's own login.
def rootf():
    return {f["id"]: f for f in security.root_findings(security.load_record())}


f = rootf()
check(
    "root with a password and Armbian's first login never run: a problem (the image's own), lock offered (lyra has sudo)",
    f["root-password"]["status"] == "problem"
    and "root-password.image" in said(f["root-password"])
    and [a["choice"] for a in f["root-password"]["actions"]] == ["root-lock"]
    and f["root-password"]["actions"][0]["confirm"]["args"]["accounts"] == ["lyra"],
    f["root-password"],
)
check(
    "  first login never run: a problem, with the marker put aside offered",
    f["root-firstrun"]["status"] == "problem" and f["root-firstrun"]["actions"][0]["choice"] == "firstrun-off",
)
(T / "etc").mkdir(exist_ok=True)
print(security.fix("root-lock", None))
print(security.fix("firstrun-off", None))
f = rootf()
check(
    "  locked, the marker aside (kept): both ok, each with its undo",
    (T / "root-pw").read_text() == "L"
    and not (T / "firstrun").exists()
    and (T / "etc" / "armbian-firstrun.kept").read_text() == "armbian first login pending\n"
    and f["root-password"]["status"] == "ok"
    and f["root-password"]["actions"][0]["choice"] == "root-lock-undo"
    and f["root-firstrun"]["status"] == "ok"
    and f["root-firstrun"]["actions"][0]["choice"] == "firstrun-undo",
    f,
)
print(security.fix("root-lock-undo", None))
print(security.fix("firstrun-undo", None))
check(
    "  undone: the password as it was, the marker back",
    (T / "root-pw").read_text() == "P"
    and (T / "firstrun").exists()
    and not (T / "etc" / "armbian-firstrun.kept").exists(),
)
(T / "firstrun").unlink()
check(
    "root with a password of its own (first login done): a warning, lock offered",
    rootf()["root-password"]["status"] == "warn" and "root-firstrun" not in rootf(),
)
(T / "root-pw").write_text("NP")
check(
    "root with no password: a problem",
    rootf()["root-password"]["status"] == "problem" and "root-password.none" in said(rootf()["root-password"]),
)
grp = (T / "group").read_text()
(T / "group").write_text(grp.replace("sudo:x:27:lyra", "sudo:x:27:"))
check(
    "  nobody else with sudo: no lock offered, and said why",
    rootf()["root-password"]["actions"] == [] and "root-lock.no-sudo" in said(rootf()["root-password"]),
)
try:
    security.fix("root-lock", None)
    refused_ = False
except ValueError:
    refused_ = True
check("  and refused if asked anyway", refused_ and (T / "root-pw").read_text() == "NP")
(T / "group").write_text(grp)
(T / "root-pw").write_text("L")

print(security.fix("group-drop:lyra@docker", None))
check(
    "lyra out of docker",
    "lyra" not in (T / "group").read_text().split("docker:x:990:")[1].split("\n")[0],
)
f = findings()
check(
    "docker: svc only (no login), so ok; lyra still listed, not in it, with In the group to put back",
    f["group-docker"]["status"] == "ok"
    and [(a["choice"], a["on"]) for a in f["group-docker"]["actions"]]
    == [("group-undo:lyra@docker", False), ("group-drop:lyra@docker", True)],
    f["group-docker"],
)
for bad in (
    "group-drop:lyra@sudo",
    "group-drop:root;x@docker",
    "group-undo:lyra@disk",
    "kernel-nope-on",
):
    try:
        security.fix(bad, None)
        refused = False
    except ValueError:
        refused = True
    check(f"refused: {bad}", refused)

print(security.undo_all())
check(
    "undo_all: lyra back in docker",
    "lyra" in (T / "group").read_text().split("docker:x:990:")[1].split("\n")[0],
)
check(
    "undo_all: kernel values back, the file gone",
    sysctl("kernel.kptr_restrict") == "0" and not drop.exists(),
)
check("undo_all: no record left", not (T / "etc" / "security-changes.json").exists())
srv = (REPO / "irate_box" / "hub" / "server.py").read_text()

rx = re.compile(re.search(r'SECURITY_CHOICE_RE\s*=\s*re.compile\(\s*r"(.*?)"\s*\)', srv).group(1))
check(
    "the hub passes these choices to the helper",
    all(
        rx.match(c)
        for c in (
            "kernel-links-on",
            "kernel-links-debian",
            "kernel-info-undo",
            "root-lock",
            "firstrun-off",
            "firstrun-undo",
            "group-drop:lyra@docker",
            "group-undo:lyra@disk",
        )
    ),
)
# Automatic security updates: the owner's choice on the Updates page, independent of the toolkit, as the
# update pattern: how often to look (Manual, 6 h, 24 h, weekly) and what to do with what is found (Flag,
# Fetch, Install); judged by what would run (Armbian
# ships APT::Periodic::Enable "0", so the binary being there means nothing).
uf = security.unattended_finding
nf = uf(False, {}, None)
check(
    "not chosen: a warning saying the choices, no buttons (the chips are on Updates), no toolkit in sight",
    nf["status"] == "warn"
    and nf["actions"] == []
    and said(nf) == {"unattended.unset", "unattended.unset.fix", "unattended.unset.can"},
    nf,
)
on_by_image = uf(True, {"APT::Periodic::Enable": "1", "APT::Periodic::Unattended-Upgrade": "1"}, 2)
check(
    "  not chosen here but already running (Debian's own package can switch it on): said so, fine",
    on_by_image["status"] == "ok" and "unattended.by-image" in said(on_by_image),
    on_by_image,
)
check(
    "manual, chosen: fine, an Undo offered",
    uf(False, {}, None, (0, 0))["status"] == "ok"
    and [a["choice"] for a in uf(False, {}, None, (0, 0))["actions"]] == ["autoupdate-undo"]
    and said(uf(False, {}, None, (0, 1))) == {"pattern.manual-fetch"},
)
check(
    "daily fetch, chosen and apt's periodic work on: fine; off again (another file wins): a warning",
    uf(False, {"APT::Periodic::Enable": "1"}, None, (24, 1))["status"] == "ok"
    and uf(False, {"APT::Periodic::Enable": "0"}, None, (24, 1))["status"] == "warn",
)
check(
    "install, chosen: a warning while it would not run (not installed, or the image's Enable 0)",
    uf(
        False,
        {"APT::Periodic::Enable": "1", "APT::Periodic::Unattended-Upgrade": "1"},
        None,
        (24, 2),
    )["status"]
    == "warn"
    and uf(
        True,
        {"APT::Periodic::Enable": "0", "APT::Periodic::Unattended-Upgrade": "1"},
        None,
        (6, 2),
    )["status"]
    == "warn",
)
check(
    "  running: fine; not run in a fortnight: a warning",
    uf(True, {"APT::Periodic::Unattended-Upgrade": "1"}, 2, (24, 2))["status"] == "ok"
    and uf(True, {"APT::Periodic::Unattended-Upgrade": "1"}, 30, (24, 2))["status"] == "warn",
)
ran = uf(True, {"APT::Periodic::Unattended-Upgrade": "1"}, 0.5, (24, 2), time.time() - 43200)
check(
    "  said in whole sentences, with when it last ran (apt's stamp, as the log in RAM is gone after a reboot)",
    said(ran) == {"pattern.day-install", "unattended.last-ran-at"}
    and ran["notes"][0]["args"]["n"] == 0
    and "time" in ran["notes"][0]["args"]["when"],
    ran,
)
check(
    "  never run: said so, and when it runs",
    "unattended.not-run" in said(uf(True, {"APT::Periodic::Unattended-Upgrade": "1"}, None, (24, 2))),
)
check(
    "  the restore button in plain words",
    [msg.render(a["label"]) for a in ran["actions"]] == ["Restore the image's settings"],
)
check(
    "the old three choices read as the pattern's",
    security.chosen_pattern({"autoupdate": {"level": "download"}}) == (24, 1)
    and security.chosen_pattern({"autoupdate": {"level": "off"}}) == (0, 0)
    and security.chosen_pattern({}) is None,
)
for bad in ("12-1", "24-3", "x", "24"):
    try:
        security.parse_pattern(bad)
        check(f"  {bad!r} refused", False)
    except ValueError:
        pass
check("  anything outside the grid refused", True)
check(
    "apt's lines: weekly flag looks every 7 days, downloads nothing; 6 h install leaves the timing to the timers",
    security.periodic_conf(168, 0)
    == 'APT::Periodic::Enable "1";\nAPT::Periodic::Update-Package-Lists "7";\n'
    'APT::Periodic::Download-Upgradeable-Packages "0";\nAPT::Periodic::Unattended-Upgrade "0";\n'
    and 'Unattended-Upgrade "always"' in security.periodic_conf(6, 2)
    and security.periodic_conf(0, 2) == 'APT::Periodic::Enable "0";\n',
)
conf = T / "apt.conf.d" / "52irate-box-autoupdate"
security.TIMER_DROPIN = T / "systemd"
six = T / "systemd" / "apt-daily.timer.d" / "52irate-box.conf"
(T / "systemctl-log").unlink(missing_ok=True)
(T / "apt-log").unlink(missing_ok=True)
said_ = security.fix("autoupdate-set:6-2", None)
check(
    "every 6 hours, install: unattended-upgrades installed, apt's periodic work on in our own file, the timers every 6 h",
    msg_ids(said_)[1:] == ["security-fix.unattended.installed", "pattern.6h-install"]
    and 'APT::Periodic::Enable "1";' in conf.read_text()
    and 'APT::Periodic::Unattended-Upgrade "always";' in conf.read_text()
    and "install -y unattended-upgrades" in (T / "apt-log").read_text()
    and "enable --now apt-daily-upgrade.timer" in (T / "systemctl-log").read_text()
    and "OnCalendar=*-*-* 00/6:00" in six.read_text()
    and (T / "systemd" / "apt-daily-upgrade.timer.d" / "52irate-box.conf").exists(),
    said_,
)
msg = security.fix("autoupdate-set:24-1", None)
rec = security.load_record()["autoupdate"]
check(
    "  every day, fetch: downloads only; the 6-hour drop-ins gone",
    'APT::Periodic::Download-Upgradeable-Packages "1";' in conf.read_text()
    and 'APT::Periodic::Unattended-Upgrade "0";' in conf.read_text()
    and (rec["often"], rec["act"]) == (24, 1)
    and not six.exists(),
    msg,
)
msg = security.fix("autoupdate-download", None)
check(
    "  the old choice names still understood",
    (
        security.load_record()["autoupdate"]["often"],
        security.load_record()["autoupdate"]["act"],
    )
    == (24, 1),
    msg,
)
security.fix("autoupdate-set:6-0", None)
msg = security.fix("autoupdate-undo", None)
check(
    "  undo: our file and drop-ins gone, the timers as they were (off here: disabled again), the package removed as it was installed here",
    not conf.exists()
    and not six.exists()
    and "disable --now apt-daily.timer" in (T / "systemctl-log").read_text()
    and "remove -y unattended-upgrades" in (T / "apt-log").read_text()
    and "autoupdate" not in security.load_record(),
    msg,
)
try:
    security.fix("autoupdate-undo", None)
    check("  undo twice: refused", False)
except ValueError:
    check("  undo twice: refused", True)
check(
    "  undo_all knows it",
    "autoupdate-undo" in (REPO / "irate_box/root/security.py").read_text().split("def undo_all")[1],
)
# Check now, Fetch and Install by hand: one log; with Fetch chosen, a check downloads what it finds.
security.fix("autoupdate-set:0-1", None)
sim = "Inst libssl3 [3.0.1] (3.0.2+deb13u1 Debian:13.7/stable [arm64])\nInst vim [1] (2 Other:1 [arm64])\n"
calls = []
real_run, real_sub = security.run, security.subprocess.run
security.run = lambda *c, **k: (
    calls.append(c) or __import__("types").SimpleNamespace(stdout=sim if "-s" in c else "", returncode=0, stderr="")
)
ram_cached = []  # each apt-get run: did it carry aptram's cache-in-RAM options


def without_ram_cache(argv):
    """argv without aptram's options (apt's cache in RAM), which every apt run here carries."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] == "-o" and i + 1 < len(argv) and argv[i + 1].startswith("Dir::Cache::"):
            i += 2
            continue
        out.append(argv[i])
        i += 1
    return tuple(out)


security.subprocess.run = lambda argv, **k: (
    ram_cached.append(argv[0] != "apt-get" or any(a.startswith("Dir::Cache::pkgcache=") for a in argv))
    or calls.append(without_ram_cache(argv))
    or __import__("types").SimpleNamespace(returncode=0)
)
log = T / "control-log"
got = security.fix("security-check", log)
check(
    "Check now, manual with Fetch: the lists afresh, then the one security update downloaded, not vim",
    msg_ids(got)[1:] == ["security-fix.lists.done", "security-fix.updates.downloaded"]
    and args_of(got, "security-fix.updates.downloaded") == {"n": 1}
    and ("apt-get", "install", "-y", "--only-upgrade", "--download-only", "libssl3") in calls
    and "$ apt-get update" in log.read_text(),
    got,
)
check("  every apt-get run with apt's cache in RAM (aptram)", ram_cached and all(ram_cached), ram_cached)
calls.clear()
got = security.fix("security-fetch", log)
check(
    "  Fetch by hand",
    got["id"] == "security-fix.updates.downloaded" and got["args"] == {"n": 1},
    got,
)
got = security.fix("security-updates", log)
check(
    "  Install by hand",
    got["id"] == "security-fix.updates.installed"
    and got["args"] == {"n": 1}
    and any("--force-confold" in " ".join(c) for c in calls if isinstance(c, tuple)),
)
calls.clear()
got = security.fix("security-reach-fetch", log)
check(
    "  Check and download by hand: the lists afresh, then the update downloaded, not installed, whatever the pattern",
    msg_ids(got)[1:] == ["security-fix.lists.done", "security-fix.updates.downloaded"]
    and ("apt-get", "install", "-y", "--only-upgrade", "--download-only", "libssl3") in calls
    and not any("--force-confold" in " ".join(c) for c in calls if isinstance(c, tuple)),
    got,
)
calls.clear()
got = security.fix("security-reach-install", log)
check(
    "  Check, download and install by hand: the lists afresh, then the update installed",
    msg_ids(got)[1:] == ["security-fix.lists.done", "security-fix.updates.installed"]
    and any("--force-confold" in " ".join(c) for c in calls if isinstance(c, tuple)),
    got,
)
security.fix("autoupdate-set:24-0", None)
got = security.fix("security-check", log)
check(
    "  Check now with Flag: only says what waits",
    msg_ids(got)[1:] == ["security-fix.lists.done", "security-fix.updates.waiting"]
    and args_of(got, "security-fix.updates.waiting") == {"n": 1},
    got,
)
apt_arch = T / "archives"
apt_arch.mkdir()
security.APT_ARCHIVES = apt_arch
check("  fetched counted from apt's archives", security.fetched_debs(sim) == 0)
(apt_arch / "libssl3_3.0.2+deb13u1_arm64.deb").write_bytes(b"")
check("  … one downloaded", security.fetched_debs(sim) == 1)
security.run, security.subprocess.run = real_run, real_sub
security.fix("autoupdate-undo", None)
hc = (REPO / "irate_box/root/hub_control.py").read_text()
check(
    "the root helper takes the pattern's choices without a scan offering them, and only the grid's",
    'choice.startswith("autoupdate-set:")' in hc and "security.parse_pattern" in hc,
)


# SSH forwarding, found from sshd -T's words and offered off.
def sf(s, rec=None):
    return {f["id"]: f for f in security.ssh_findings(s, ["lyra"], rec if rec is not None else {})}


on = sf(
    {
        "permitrootlogin": "no",
        "passwordauthentication": "no",
        "allowtcpforwarding": "yes",
        "allowagentforwarding": "no",
        "x11forwarding": "yes",
    }
)
check(
    "ssh forwarding on (the image's default): a warning naming which, with the offer",
    on["ssh-forwarding"]["status"] == "warn"
    and on["ssh-forwarding"]["evidence"] == ["allowtcpforwarding yes", "x11forwarding yes"]
    and [(a["choice"], a["on"]) for a in on["ssh-forwarding"]["actions"]]
    == [("ssh-forwarding-on", True), ("ssh-forwarding-off", False)],
    on["ssh-forwarding"],
)
off = sf(
    {
        "permitrootlogin": "no",
        "passwordauthentication": "no",
        "allowtcpforwarding": "no",
        "allowagentforwarding": "no",
        "x11forwarding": "no",
    },
    {"ssh": {"forwarding": "2026-10-08"}},
)
check(
    "  off by this page: ok, with Undo",
    off["ssh-forwarding"]["status"] == "ok" and off["ssh-forwarding"]["actions"][0]["choice"] == "ssh-forwarding-undo",
)
off_image = sf(
    {
        "permitrootlogin": "no",
        "passwordauthentication": "no",
        "allowtcpforwarding": "no",
        "allowagentforwarding": "no",
        "x11forwarding": "no",
    }
)
check(
    "  off by the image: both values still offered, On setting it here (not a lone Undo, nor nothing)",
    [(a["choice"], a["on"]) for a in off_image["ssh-forwarding"]["actions"]]
    == [("ssh-forwarding-on", False), ("ssh-forwarding-off", True)]
    and [a["choice"] for a in off_image["ssh-password"]["actions"]] == ["ssh-password-on", "ssh-password-off"],
    off_image["ssh-forwarding"],
)
drop = Path(os.environ["HUB_SSHD_DROPIN"])
rec = {}
was_keys, security.keys_on_box = security.keys_on_box, lambda: ["lyra"]  # a key, so keys only is allowed
security._ssh(rec, "forwarding", "on")
security._ssh(rec, "password", True)
security.keys_on_box = was_keys
check(
    "  set on here: the drop-in says yes for forwarding and no for passwords, both recorded",
    drop.read_text().splitlines()[1:]
    == [
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        "AllowTcpForwarding yes",
        "AllowAgentForwarding yes",
        "X11Forwarding yes",
    ]
    and set(rec["ssh_on"]) == {"forwarding"}
    and set(rec["ssh"]) == {"password"},
    (drop.read_text(), rec),
)
security._ssh(rec, "forwarding", False)
security._ssh(rec, "password", False)
check("  and undone: no drop-in, nothing recorded", not drop.exists() and rec == {}, rec)
check(
    "  undo_all knows both, fix() takes on, off and undo",
    '"ssh_on"' in (REPO / "irate_box/root/security.py").read_text().split("def undo_all")[1]
    and 'for m in ("off", "on", "undo")' in (REPO / "irate_box/root/security.py").read_text(),
)

# The gates: a NOPASSWD rule for an account sshd lets in by
# password, a repository key trusted for everything, logs in RAM; each found, fixed, undone.
(T / "home" / "lyra" / ".ssh").mkdir(parents=True)
(T / "passwd").write_text(
    "root:x:0:0::/root:/bin/bash\nlyra:x:1000:1000::"
    + str(T / "home" / "lyra")
    + ":/bin/bash\nsvc:x:1001:1001::/srv:/usr/sbin/nologin\n"
)
security.PASSWD_FILE = T / "passwd"


def gate():
    rec = security.load_record()
    return {f["id"]: f for f in security.sudo_findings(rec) + security.apt_findings(rec) + security.log_findings(rec)}


g = gate()
check(
    "sudo: a NOPASSWD rule for a login account while sshd takes passwords: a problem, the file offered",
    g["sudo-nopasswd"]["status"] == "problem"
    and g["sudo-nopasswd"]["evidence"] == ["claude-temp: lyra"]
    and said(g["sudo-nopasswd"]) == {"sudo.rules", "sudo.rules.why-ssh", "sudo.fix-ssh"}
    and [a["choice"] for a in g["sudo-nopasswd"]["actions"] if not a.get("on")] == ["sudo-drop:claude-temp"],
    g["sudo-nopasswd"],
)
(T / "pw-off").touch()
check("  with passwords off: a warning", gate()["sudo-nopasswd"]["status"] == "warn")
(T / "pw-off").unlink()
print(security.fix("sudo-drop:claude-temp", None))
check(
    "  removed, kept under /etc/hub, ok with Undo",
    not (T / "sudoers.d" / "claude-temp").exists()
    and (T / "etc" / "sudoers-removed" / "claude-temp").is_file()
    and gate()["sudo-nopasswd"]["status"] == "ok"
    and gate()["sudo-nopasswd"]["actions"][0]["choice"] == "sudo-undo:claude-temp",
)
print(security.fix("sudo-undo:claude-temp", None))
check(
    "  undone: the file back",
    (T / "sudoers.d" / "claude-temp").read_text().startswith("lyra ALL")
    and gate()["sudo-nopasswd"]["status"] == "problem",
)
try:
    security.fix("sudo-drop:sudoers", None)
    check("  sudoers itself refused", False)
except ValueError:
    check("  sudoers itself refused", True)
check(
    "keys where sshd looks (AuthorizedKeysFile with %h and %u): none yet",
    security.keys_on_box() == [],
)
(T / "home" / "lyra" / ".ssh" / "keys_lyra").write_text("ssh-ed25519 AAAA test\n")
check("  a key in the file sshd names: found", security.keys_on_box() == ["lyra"])
check(
    "apt: a key in trusted.gpg.d with a source of its name: warned, the tie offered",
    g["apt-trust"]["status"] == "warn"
    and "home_mPWRD_OS.gpg" in g["apt-trust"]["evidence"]
    and not any(e.startswith("debian-archive") for e in g["apt-trust"]["evidence"])
    and [(a["group"], a["choice"], a["on"]) for a in g["apt-trust"]["actions"]][:2]
    == [
        ("home_mPWRD_OS.gpg", "apt-any:home_mPWRD_OS.gpg", True),
        ("home_mPWRD_OS.gpg", "apt-signedby:home_mPWRD_OS.gpg", False),
    ],
    g["apt-trust"],
)
print(security.fix("apt-signedby:home_mPWRD_OS.gpg", None))
lst = (T / "apt" / "sources.list.d" / "home:mPWRD:OS.list").read_text()
check(
    "  the key moved to keyrings and named in the source; trusted.gpg.d without it; ok with Undo",
    lst.startswith("deb [signed-by=" + str(T / "keyrings" / "home_mPWRD_OS.gpg") + "] http://")
    and (T / "keyrings" / "home_mPWRD_OS.gpg").read_bytes() == b"KEY"
    and not (T / "apt" / "trusted.gpg.d" / "home_mPWRD_OS.gpg").exists()
    and gate()["apt-trust"]["status"] == "ok",
    lst,
)
check(
    "  its choice now: Only its own source, with Any repository to put it back",
    [(a["choice"], a["on"]) for a in gate()["apt-trust"]["actions"]]
    == [("apt-undo:home_mPWRD_OS.gpg", False), ("apt-tied:home_mPWRD_OS.gpg", True)],
    gate()["apt-trust"]["actions"],
)
print(security.fix("apt-undo:home_mPWRD_OS.gpg", None))
check(
    "  undone: both back as they were",
    (T / "apt" / "sources.list.d" / "home:mPWRD:OS.list").read_text().startswith("deb http://")
    and (T / "apt" / "trusted.gpg.d" / "home_mPWRD_OS.gpg").exists()
    and not (T / "keyrings" / "home_mPWRD_OS.gpg").exists(),
)


def opt(f, choice):
    return next((a for a in f["actions"] if a["choice"] == choice), None)


check(
    "logs in RAM (zram on /var/log): warned, In RAM the choice now, the card offered",
    g["logs-ram"]["status"] == "warn"
    and [(a["label"]["id"], a["on"]) for a in g["logs-ram"]["actions"]] == [("logs.ram", True), ("logs.card", False)]
    and opt(g["logs-ram"], "logs-card"),
)
print(security.fix("logs-card", None))
check(
    "  ramlog off and the journal kept, from the next boot",
    "ENABLED=false" in (T / "ramlog").read_text()
    and "Storage=persistent" in (T / "journald.conf.d" / "irate-box.conf").read_text()
    and [(a["choice"], a["on"]) for a in gate()["logs-ram"]["actions"]] == [("logs-undo", False), ("logs-card", True)],
)
(T / "log-on-card").touch()
check("  on the card: ok", gate()["logs-ram"]["status"] == "ok")
(T / "log-on-card").unlink()
print(security.fix("logs-undo", None))
check(
    "  undone",
    "ENABLED=true" in (T / "ramlog").read_text() and not (T / "journald.conf.d" / "irate-box.conf").exists(),
)
security.fix("sudo-drop:claude-temp", None)
security.fix("apt-signedby:home_mPWRD_OS.gpg", None)
security.fix("logs-card", None)
done = security.undo_all()
check(
    "undo_all puts the three back",
    (T / "sudoers.d" / "claude-temp").exists()
    and (T / "apt" / "trusted.gpg.d" / "home_mPWRD_OS.gpg").exists()
    and not (T / "journald.conf.d" / "irate-box.conf").exists()
    and not security.load_record(),
    done,
)


# Process visibility: the box's /proc (PID 1's, not this unit's own under ProtectProc), hidden by a remount there and an
# /etc/fstab line for each boot; Undo puts the line back as it was and shows every process again.
security.INIT_MOUNTS, security.FSTAB = T / "init-mounts", T / "fstab"
plain = "proc /proc proc rw,nosuid,nodev,noexec,relatime 0 0\n"
security.INIT_MOUNTS.write_text("/dev/mmcblk0p1 / ext4 rw 0 0\n" + plain)
FSTAB0 = "UUID=abcd / ext4 defaults,noatime 0 1\ntmpfs /tmp tmpfs defaults,nosuid 0 0\n"
security.FSTAB.write_text(FSTAB0)
mounts, remount_rc, real_run = [], [0], security.run


def fake_run(*c, **k):
    if c[:4] == ("nsenter", "--target", "1", "--mount"):
        mounts.append(c)
        if remount_rc[0] == 0:
            n = c[-2].split("hidepid=")[1]
            security.INIT_MOUNTS.write_text(
                "/dev/mmcblk0p1 / ext4 rw 0 0\n"
                + plain.replace("relatime", "relatime" + ("" if n == "0" else f",hidepid={n}"))
            )
        return __import__("types").SimpleNamespace(
            stdout="",
            stderr="" if remount_rc[0] == 0 else "mount: /proc: permission denied.",
            returncode=remount_rc[0],
        )
    return real_run(*c, **k)


security.run = fake_run


def pf():
    return {f["id"]: f for f in security.proc_findings(security.load_record())}.get("proc-hidden")


f = pf()
check(
    "process visibility: every process shown to every account is a warning, Each account sees only its own offered with a confirmation",
    f["status"] == "warn"
    and [(a["choice"], a["on"], "confirm" in a) for a in f["actions"]]
    == [("proc-every-now", True, False), ("proc-hide", False, True)],
    f,
)
said_hide = security.fix("proc-hide", None)
check(
    "  hidden: /proc remounted in PID 1's mount namespace, with hidepid=2",
    mounts[-1]
    == (
        "nsenter",
        "--target",
        "1",
        "--mount",
        "mount",
        "-o",
        "remount,hidepid=2",
        "/proc",
    )
    and said_hide["id"] == "security-fix.proc.hidden",
    (mounts, said_hide),
)
check(
    "  and at each boot: /etc/fstab gains its line, every other line kept as it was",
    security.FSTAB.read_text() == FSTAB0 + security.PROC_MARK + "\n" + security.PROC_LINE + "\n",
    security.FSTAB.read_text(),
)
check(
    "  its line keeps the options systemd mounts /proc with (nosuid, nodev, noexec), never defaults, which turn them off at boot",
    security.PROC_LINE.split()[3].split(",") == ["nosuid", "nodev", "noexec", "hidepid=2"],
    security.PROC_LINE,
)
f = pf()
check(
    "  ok now, said as set here, with Undo offered",
    f["status"] == "ok"
    and f["notes"][0]["id"] == "proc.set-here"
    and [(a["choice"], a["on"]) for a in f["actions"]] == [("proc-undo", False), ("proc-hide", True)],
    f,
)
check(
    "  pressed again: nothing changed, said so",
    security.fix("proc-hide", None)["id"] == "security-fix.proc.already" and len(mounts) == 1,
)
security.fix("proc-undo", None)
check(
    "  undone: /etc/fstab exactly as it was, every process shown again (hidepid=0)",
    security.FSTAB.read_text() == FSTAB0
    and mounts[-1][-2] == "remount,hidepid=0"
    and pf()["status"] == "warn"
    and "proc" not in security.load_record(),
    security.FSTAB.read_text(),
)
withproc = FSTAB0 + "proc /proc proc defaults 0 0\n"
security.FSTAB.write_text(withproc)
security.fix("proc-hide", None)
check(
    "an /etc/fstab with its own /proc line: hidepid added to that line, no second line",
    security.FSTAB.read_text() == FSTAB0 + "proc /proc proc defaults,hidepid=2 0 0\n",
    security.FSTAB.read_text(),
)
security.fix("proc-undo", None)
check(
    "  undone: the line exactly as it was",
    security.FSTAB.read_text() == withproc,
    security.FSTAB.read_text(),
)
remount_rc[0] = 1
try:
    security.fix("proc-hide", None)
    refused = None
except ValueError as exc:
    refused = exc.message["id"]
check(
    "a remount that fails: refused, saying what mount said; /etc/fstab and the record unchanged",
    refused == "security-fix.proc.remount-failed"
    and security.FSTAB.read_text() == withproc
    and "proc" not in security.load_record(),
    refused,
)
remount_rc[0] = 0
security.fix("proc-hide", None)
security.undo_all()
check(
    "undo_all puts process visibility back too",
    security.FSTAB.read_text() == withproc and "hidepid" not in security.INIT_MOUNTS.read_text(),
)
security.INIT_MOUNTS.unlink()
check(
    "the box's mounts unreadable: no finding (nothing said that cannot be known)",
    pf() is None,
)
security.run = real_run


# Security updates: Debian's stable fixes count, as the security archive's do; others' don't.
SIM = """Inst base-files [26.05.0-trunk-13.8+deb13u6-trixie] (26.8.3-13.8+deb13u6-trixie Armbian:trixie [armhf])
Inst bash [5.2.37-2+b9] (5.2.37-2+b10 Debian:13.7/stable [armhf])
Inst gzip [1.13-1] (1.13-1+deb13u1 Debian:13.7/stable [armhf])
Inst libglib2.0-0t64 [2.84.4-3~deb13u3] (2.84.4-3~deb13u5 Debian:13.7/stable [armhf])
Inst openssl [3.5.1-1] (3.5.1-1+deb13u2 Debian-Security:13/stable-security [armhf])
Inst meshtasticd [2.8.0.696~obs5f198c4~unstable] (2.8.1.752~obs790944a~unstable network:Meshtastic:daily:download.opensuse.org [armhf])
Inst tailscale [1.98.9] (1.104.1 Tailscale:pkgs.tailscale.com [armhf])"""
check(
    "security updates: the security archive's and Debian's point-release fixes (+debNuM), not Armbian's, a rebuild's or others'",
    security.pending_security(SIM) == ["gzip", "libglib2.0-0t64", "openssl"],
    security.pending_security(SIM),
)


# The hub's own backend off loopback is a problem; on loopback, or any other listener of the box, it is not.
def listeners_of(*rows):
    """Security's listener findings for ss lines (address, port, unit), as parse_ss reads them."""
    text = "".join(f'tcp LISTEN 0 128 {addr}:{port} 0.0.0.0:* users:(("prog",pid=5,fd=3))\n' for addr, port, _ in rows)
    found = security.parse_ss(text)
    units = {port: unit for _, port, unit in rows}
    for f in found:
        f.update(unit=units[f["port"]], name=security.named("prog"))
    return {f["id"]: f for f in security.listener_findings(found, {})}


BACKEND = "irate-box.service"
for label, addr in (
    ("a wildcard IPv4 address", "0.0.0.0"),
    ("a wildcard IPv6 address", "[::]"),
    ("a LAN address", "192.168.1.5"),
):
    got = listeners_of((addr, 8000, BACKEND))["port-tcp-8000"]
    check(
        f"the hub's backend on {label}: a problem, said, the port as evidence",
        got["status"] == "problem" and "port.hub-backend" in said(got) and "TCP 8000" in got["evidence"],
        got,
    )
check(
    "  on loopback: no listener to report",
    listeners_of(("127.0.0.1", 8000, BACKEND), ("[::1]", 8000, BACKEND)) == {},
)
got = listeners_of(
    ("0.0.0.0", 80, "nginx.service"),
    ("0.0.0.0", 443, "caddy.service"),
    ("0.0.0.0", 8765, BACKEND),
    ("0.0.0.0", 8000, "irate-box-librarian.service"),
    ("0.0.0.0", 8001, "other.service"),
)
check(
    "  the web server's front listeners and the box's other services are unchanged",
    got["port-tcp-80"]["status"] == "ok"
    and "port.hub-server" in said(got["port-tcp-80"])
    and got["port-tcp-443"]["status"] == "ok"
    and "port.hub-server" in said(got["port-tcp-443"])
    and got["port-tcp-8765"]["status"] == "ok"
    and "port.ours" in said(got["port-tcp-8765"])
    and got["port-tcp-8000"]["status"] == "ok"
    and "port.ours" in said(got["port-tcp-8000"])
    and "port.hub-backend" not in said(got["port-tcp-8001"]),
    {k: (v["status"], sorted(said(v))) for k, v in got.items()},
)
(T / "etc").mkdir(exist_ok=True)
(T / "etc" / "hub.env").write_text("PORT=8123\nHUB_BIND=127.0.0.1\n")
got = listeners_of(("0.0.0.0", 8123, BACKEND), ("0.0.0.0", 8000, BACKEND))
check(
    "  found by the configured port: hub.env's PORT, not a fixed 8000",
    got["port-tcp-8123"]["status"] == "problem" and got["port-tcp-8000"]["status"] == "ok",
    {k: v["status"] for k, v in got.items()},
)
(T / "etc" / "hub.env").unlink()

# Cockpit: one setting, always there where the image has it, with its three values; the value it had
# before this page changed it is the one that undoes. systemd stood in for by a small state.
ck = {"enabled": True, "active": True}
security.UNIT_DIR = T / "units"
ck_drop = security.UNIT_DIR / "cockpit.socket.d" / "irate-box.conf"
was_run, was_sc, was_listeners = security.run, security._systemctl, security.listeners


class _Out:
    def __init__(self, out):
        self.returncode, self.stdout, self.stderr = 0, out, ""


def _ck_run(*cmd, **kw):
    if cmd[:2] == ("systemctl", "show"):
        return _Out("loaded\n")
    if cmd[:2] == ("systemctl", "is-active"):
        return _Out("active\n" if ck["active"] else "inactive\n")
    if cmd[:2] == ("systemctl", "is-enabled"):
        return _Out("enabled\n" if ck["enabled"] else "disabled\n")
    return was_run(*cmd, **kw) if cmd[0] != "systemctl" else _Out("")


def _ck_sc(*args):
    if args == ("disable", "--now", "cockpit.socket"):
        ck.update(enabled=False, active=False)
    elif args == ("enable", "cockpit.socket"):
        ck["enabled"] = True
    elif args == ("restart", "cockpit.socket"):
        ck["active"] = True


def _ck_found():
    on_net = ck["active"] and not ck_drop.exists()
    return [{"proto": "tcp", "port": 9090, "addr": "0.0.0.0", "unit": "cockpit.socket", "pid": 1}] if on_net else []


security.run, security._systemctl, security.listeners = _ck_run, _ck_sc, _ck_found


def ck_chips(rec):
    (f,) = security.cockpit_findings(_ck_found(), rec)
    return f["status"], [(a["choice"], a["on"]) for a in f["actions"]]


rec = {}
check(
    "Cockpit on the network (the image's way): a problem, its three values offered",
    ck_chips(rec) == ("problem", [("cockpit-network", True), ("cockpit-loopback", False), ("cockpit-off", False)]),
    ck_chips(rec),
)
security._cockpit(rec, "loopback")
check(
    "  set to loopback here: still there, loopback marked, On the network now the undo",
    ck_drop.exists()
    and ck_chips(rec) == ("ok", [("cockpit-undo", False), ("cockpit-loopback", True), ("cockpit-off", False)]),
    ck_chips(rec),
)
security._cockpit(rec, "off")
check(
    "  then off: still there, off marked, how it was first kept for undo",
    rec["cockpit"]["before"] == "network"
    and ck_chips(rec) == ("ok", [("cockpit-undo", False), ("cockpit-loopback", False), ("cockpit-off", True)]),
    (rec, ck_chips(rec)),
)
security._cockpit(rec, "undo")
check(
    "  undone: on the network again, no drop-in, nothing recorded",
    not ck_drop.exists() and "cockpit" not in rec and ck_chips(rec)[1][0] == ("cockpit-network", True),
    (rec, ck),
)
ck.update(enabled=False, active=False)
check(
    "Cockpit off by the image: still a setting, off marked, the others to set here",
    ck_chips(rec) == ("ok", [("cockpit-network", False), ("cockpit-loopback", False), ("cockpit-off", True)]),
    ck_chips(rec),
)
security._cockpit(rec, "network")
security._cockpit(rec, "undo")
check("  opened here, then undone: off again, not running", ck == {"enabled": False, "active": False}, ck)
security.run, security._systemctl, security.listeners = was_run, was_sc, was_listeners
# The Network listeners table's Belongs to column: the package of a listener's program, from dpkg-query -S.
(BIN / "dpkg-query").write_text(
    "#!/usr/bin/env python3\nimport sys\n"
    "known = {'/usr/sbin/rpcbind': 'rpcbind: /usr/sbin/rpcbind', '/usr/bin/two': 'a, b: /usr/bin/two'}\n"
    "line = known.get(sys.argv[-1])\nprint(line) if line else sys.exit(1)\n"
)
cache = {}
check(
    "owner: a program's package; a file in two packages, the first; none for a file no package owns",
    security._package_of("/usr/sbin/rpcbind", cache) == "rpcbind"
    and security._package_of("/usr/bin/two", cache) == "a"
    and security._package_of("/opt/old/mystery", cache) is None
    and security._package_of(None, cache) is None,
    cache,
)
check(
    "  an app's port is the app's; the hub's units are the hub's",
    security._owner({"unit": "x.service"}, {"name": "Meshtastic"}, {}) == {"kind": "app", "name": "Meshtastic"}
    and security._owner({"unit": "nginx.service"}, None, {}) == {"kind": "hub", "name": "irate-box"},
)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
