# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's deep audit, offline: debian-cis's --batch output and Lynis's report
(recorded on a real box) turned
into findings, failed CIS checks grouped by section, the accepted-by-design list applied to both;
debian-cis exported from a stand-in mirror and run with its paths in the environment; the report
kept and shown by every regular audit; the weekly schedule. python3 tests/sim_deepaudit.py"""
import json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="deep-"))
(T / "state" / "control").mkdir(parents=True); (T / "state" / "library").mkdir()
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_GIT_ROOT=str(T / "git"), HUB_KITS_ROOT=str(T / "kits"))
sys.path.insert(0, str(REPO))
from irate_box.root import deepaudit as da  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

CIS = """OK 1.1.1.1_disable_freevxfs  OK{freevxfs is not loaded} OK{freevxfs is not available in any kernel config}
KO 1.1.3_tmp_nodev  OK{/tmp is a partition} KO{/tmp has no option nodev in fstab!}
KO 1.1.6_var_partition  KO{/var is not a partition}
KO 2.2.10_disable_http_server  KO{nginx is installed}
KO 3.5.4.1.1_net_fw_default_policy_drop  KO{Policy set to ACCEPT for chain INPUT}
KO 5.2.10_disable_root_login  KO{PermitRootLogin is not set to no}
KO 5.2.7_sshd_maxauthtries  KO{MaxAuthTries is 6}
KO 4.1.1.1_install_auditd  KO{auditd is not installed}
OK 99.99_check_distribution  OK{Your distribution is debian and the version is supported}
find: '/etc/audit/rules.d': No such file or directory
AUDIT_SUMMARY PASSED_CHECKS:2 RUN_CHECKS:9 TOTAL_CHECKS_AVAIL:9 CONFORMITY_PERCENTAGE:22.22
"""
checks, summary = da.parse_cis(CIS)
check("debian-cis's batch lines read: status, check, and of a failed one what failed", len(checks) == 9 and checks[1] == ("KO", "1.1.3_tmp_nodev", ["/tmp has no option nodev in fstab!"])
      and summary["PASSED_CHECKS"] == "2" and summary["CONFORMITY_PERCENTAGE"] == "22.22", (checks[:2], summary))
fs = {f["id"]: f for f in da.cis_findings(checks, summary, 180)}
check("failed checks grouped by section, a warning each", fs["cis-5.2"]["status"] == "warn" and "1 check not met" in fs["cis-5.2"]["title"]
      and "sshd_maxauthtries" in fs["cis-5.2"]["detail"] and fs["cis-4.1"]["about"] == {"kind": "setting", "key": "cis-4.1"}, fs.get("cis-5.2"))
check("a check another source asks too (secdoctor_xref): a finding of its own, about the shared key", fs["cis-5.2.10_disable_root_login"]["about"] == {"kind": "setting", "key": "ssh-root-login"}
      and fs["cis-5.2.10_disable_root_login"]["title"] == "CIS 5.2.10: SSH: logging in as root")
check("  each keeps its checks one by one", [c["check"] for c in fs["cis-5.2"]["checks"]] == ["5.2.7_sshd_maxauthtries"]
      and fs["cis-5.2"]["checks"][0]["msgs"] == ["MaxAuthTries is 6"] and fs["cis-5.2.10_disable_root_login"]["checks"][0]["check"] == "5.2.10_disable_root_login",
      fs["cis-5.2"].get("checks"))
check("  sections in the benchmark's order, then the shared ones", [k for k in fs if k.startswith("cis-") and k[4].isdigit()] == ["cis-4.1", "cis-5.2", "cis-5.2.10_disable_root_login"], list(fs))
acc = [f for f in fs.values() if f["accepted"]]
check("the box's design accepted, not counted: one partition, the web server, no firewall yet",
      {f["accepted"] for f in acc} == {"one partition: the box runs from an SD card, with no separate /var, /tmp or /home",
                                        "the box is a web server: nginx (or Caddy) is the hub's front", "the box filters the hotspot alone (the floor, Security page): its other interfaces are the owner's network, left as found"}
      and all(f["status"] == "ok" for f in acc) and not any(k in fs for k in ("cis-1.1", "cis-2.2", "cis-3.5")), [f["detail"] for f in acc])
check("the passes counted, and the time", "2 of 9 checks pass (22.22 %); 3.0 min." == fs["cis-summary"]["detail"], fs["cis-summary"]["detail"])

LYNIS = """# Lynis Report
lynis_version=3.1.4
warning[]=FIRE-4512|iptables module(s) loaded, but no rules active|-|-|
warning[]=SSH-7408|Root can log in over SSH|-|-|
suggestion[]=DEB-0280|Install libpam-tmpdir to set $TMP and $TMPDIR for PAM sessions|-|-|
suggestion[]=SSH-7408|Consider hardening SSH configuration|AllowTcpForwarding (set YES to NO)|-|
hardening_index=64
"""
rep = da.parse_lynis(LYNIS)
check("Lynis's report read", rep["index"] == "64" and rep["warnings"][1] == {"test": "SSH-7408", "text": "Root can log in over SSH", "details": "", "solution": ""}
      and len(rep["suggestions"]) == 2 and rep["suggestions"][1]["details"] == "AllowTcpForwarding (set YES to NO)", rep)
lf = {f["id"]: f for f in da.lynis_findings(rep, 281)}
check("a Lynis warning: a finding of its own, about what its test asks (SSH-7408: sshd's settings, CIS 5.2)", lf["lynis-SSH-7408"]["status"] == "warn"
      and lf["lynis-SSH-7408"]["source"] == "lynis" and lf["lynis-SSH-7408"]["about"] == {"kind": "setting", "key": "cis-5.2"} and not lf["lynis-SSH-7408"].get("tier"))
check("  the firewall one accepted, as for debian-cis", lf["lynis-FIRE-4512"]["status"] == "ok" and lf["lynis-FIRE-4512"]["accepted"].startswith("the box filters the hotspot alone"))
check("a suggestion the table knows: about that, as a suggestion; the rest listed, not counted; the hardening index said",
      lf["lynis-SSH-7408-2"]["about"]["key"] == "cis-5.2" and lf["lynis-SSH-7408-2"]["tier"] == "suggest"
      and lf["lynis-summary"]["status"] == "ok" and "Hardening index 64" in lf["lynis-summary"]["detail"]
      and "2 suggestions; 4.7 min. Not matched to an item (listed, not counted): DEB-0280: Install libpam-tmpdir" in lf["lynis-summary"]["detail"], lf["lynis-summary"]["detail"])

# Lynis's settings one by one (details[]) and its tests that ask what another source asks
# (secdoctor_xref): each about the same thing, so the joint report merges them.
LYNIS2 = """suggestion[]=KRNL-6000|One or more sysctl values differ from the scan profile and could be tweaked||Change sysctl value or disable test (skip-test=KRNL-6000:<sysctl-key>)|
details[]=KRNL-6000|sysctl|desc:Restrict access to kernel symbols;field:kernel.kptr_restrict;prefval:2;value:0;|
details[]=KRNL-6000|sysctl|desc:Disable magic SysRQ;field:kernel.sysrq;prefval:0;value:176;|
details[]=KRNL-6000|sysctl|desc:-;field:net.ipv4.conf.all.send_redirects;prefval:0;value:1;|
details[]=SSH-7408|sshd|desc:sshd option PermitRootLogin;field:PermitRootLogin;prefval:NO;value:YES;|
suggestion[]=AUTH-9262|Install a PAM module for password strength testing like pam_cracklib or pam_passwdqc or libpam-passwdqc|-|-|
suggestion[]=ACCT-9628|Enable auditd to collect audit information|-|-|
suggestion[]=FILE-6310|To decrease the impact of a full /home file system, place /home on a separate partition|-|-|
suggestion[]=PKGS-7392|Update your system with apt-get update, apt-get upgrade, apt-get dist-upgrade and/or unattended-upgrades|-|-|
hardening_index=61
"""
l2 = {f["id"]: f for f in da.lynis_findings(da.parse_lynis(LYNIS2))}
key = lambda i: (l2[i]["about"] or {}).get("key")  # noqa: E731
check("Lynis's sysctl keys one by one: each about its setting (kptr_restrict: kernel-info; send_redirects: CIS 3.2)",
      key("lynis-KRNL-6000-kernel.kptr_restrict") == "kernel-info" and key("lynis-KRNL-6000-net.ipv4.conf.all.send_redirects") == "cis-3.2"
      and l2["lynis-KRNL-6000-kernel.kptr_restrict"]["detail"] == "kernel.kptr_restrict is 0; Lynis prefers 2.", sorted(l2))
check("  the one known to nothing listed, and KRNL-6000's one line not repeated", "kernel.sysrq is 176" in l2["lynis-summary"]["detail"]
      and "lynis-KRNL-6000" not in l2, l2["lynis-summary"]["detail"])
check("  sshd's options too: PermitRootLogin is about SSH root login", key("lynis-SSH-7408-PermitRootLogin") == "ssh-root-login")
check("Lynis's tests by name onto CIS sections, tiered as the section is (5.3 a suggestion; 4.1 not for this board)",
      key("lynis-AUTH-9262") == "cis-5.3" and l2["lynis-AUTH-9262"]["tier"] == "suggest" and l2["lynis-ACCT-9628"]["tier"] == "not-here")
check("  and onto the Security page's: PKGS-7392 is Debian's security updates", key("lynis-PKGS-7392") == "security-updates")
check("  one partition accepted for Lynis as for debian-cis: listed, not a finding", "lynis-FILE-6310" not in l2
      and "FILE-6310: To decrease the impact of a full /home file system, place /home on a separate partition (accepted: the box's design)" in l2["lynis-summary"]["detail"])
from irate_box.root import secdoctor as sd0  # noqa: E402
doctor = [{"id": "kernel-info", "title": "Kernel addresses and log readable by everyone", "status": "warn", "detail": "kptr_restrict 0.", "fix": "",
           "source": "doctor", "about": {"kind": "setting", "key": "kernel-info"}}]
j0 = {i["key"]: i for i in sd0.joint([{"title": "Kernel", "findings": doctor}, {"title": "Lynis", "findings": list(l2.values())}])["items"]}
check("the joint report: Lynis's kptr_restrict merged with the doctor's kernel-info, a real item (not a suggestion)",
      j0["setting:kernel-info"]["sources"] == ["doctor", "lynis"] and j0["setting:kernel-info"]["tier"] is None, j0.get("setting:kernel-info"))
check("  Lynis alone on a CIS section: a suggestion; on auditd: not for this board", j0["setting:cis-5.3"]["tier"] == "suggest" and j0["setting:cis-4.1"]["tier"] == "not-here")

# Each check's own fix, from its script in debian-cis's source (the pinned commit's, at audit time).
src0 = T / "cis-info"
(src0 / "bin" / "hardening").mkdir(parents=True)
for name, body in {
    "disable_send_packet_redirects": "DESCRIPTION=\"Disable send packet redirects.\"\nSYSCTL_PARAMS='net.ipv4.conf.all.send_redirects=0 net.ipv4.conf.default.send_redirects=0'\n",
    "sshd_maxauthtries": "DESCRIPTION=\"Set SSH MaxAuthTries to 4.\"\nOPTIONS=''\nFILE='/etc/ssh/sshd_config'\nOPTIONS='MaxAuthTries=4'\n",
    "install_auditd": "DESCRIPTION=\"Install auditd.\"\nPACKAGE='auditd'\napply() {\n    apt_install \"$PACKAGE\"\n}\n",
    "dnsmasq_is_disabled": "DESCRIPTION=\"Ensure dnsmasq is not installed.\"\nPACKAGES='dnsmasq'\napply() {\n    apt-get purge \"$PACKAGES\" -y\n}\n",
    "disable_telnet_client": "DESCRIPTION=\"Ensure telnet client is not installed.\"\nPACKAGES='telnet'\napply() {\n    apt-get purge \"$PACKAGES\" -y\n}\n",
    "crontab_perm_ownership": "DESCRIPTION=\"Permissions on /etc/crontab.\"\nFILE='/etc/crontab'\nPERMISSIONS='600'\nUSER='root'\nGROUP='root'\n",
    "set_password_exp_days": "DESCRIPTION=\"Set password expiration days.\"\nPACKAGE='login'\nOPTIONS='PASS_MAX_DAYS=90'\nFILE='/etc/login.defs'\napply() {\n    apt_install \"$PACKAGE\"\n}\n",
    "logfile_sudo": "DESCRIPTION=\"Ensure sudo log files exists.\"\nPATTERN=\"$HOME\"\n",
}.items():
    (src0 / "bin" / "hardening" / f"{name}.sh").write_text("#!/bin/bash\nHARDENING_LEVEL=2\n" + body)
info = {n: da.cis_check_info(src0, "9.9_" + n) for n in ("disable_send_packet_redirects", "sshd_maxauthtries", "install_auditd", "dnsmasq_is_disabled",
                                                          "disable_telnet_client", "crontab_perm_ownership", "set_password_exp_days", "logfile_sudo")}
check("a check's what, from its DESCRIPTION", info["install_auditd"]["what"] == "Install auditd.")
check("sysctl keys: one file in sysctl.d, then sysctl --system", info["disable_send_packet_redirects"]["cmd"] ==
      "printf '%s\\n' 'net.ipv4.conf.all.send_redirects = 0' 'net.ipv4.conf.default.send_redirects = 0' | sudo tee /etc/sysctl.d/60-cis-disable-send-packet-redirects.conf && sudo sysctl --system",
      info["disable_send_packet_redirects"].get("cmd"))
check("sshd options: a file in sshd_config.d, checked by sshd -t before the reload (its last OPTIONS, not the empty one)", info["sshd_maxauthtries"]["cmd"] ==
      "printf '%s\\n' 'MaxAuthTries 4' | sudo tee /etc/ssh/sshd_config.d/60-cis-sshd-maxauthtries.conf && sudo sshd -t && sudo systemctl reload ssh",
      info["sshd_maxauthtries"].get("cmd"))
check("a package to install, or to remove", info["install_auditd"]["cmd"] == "sudo apt-get install auditd" and info["disable_telnet_client"]["cmd"] == "sudo apt-get purge telnet")
check("never the removal of what the hub uses (dnsmasq): said instead", "cmd" not in info["dnsmasq_is_disabled"] and "The hub uses dnsmasq" in info["dnsmasq_is_disabled"]["say"])
check("a file's owner and mode", info["crontab_perm_ownership"]["cmd"] == "sudo chown root:root /etc/crontab && sudo chmod 600 /etc/crontab")
# (Not printed on failure: CodeQL takes anything named for a password as a secret logged.)
check("another file's settings in words, not its prerequisite package", info["set_password_exp_days"] == {"what": "Set password expiration days.", "say": "In /etc/login.defs, set: PASS_MAX_DAYS=90."})
check("a value built from a variable: no command", info["logfile_sudo"] == {"what": "Ensure sudo log files exists."}, info["logfile_sudo"])
check("an unknown or odd check name: nothing", da.cis_check_info(src0, "9.9_nothing_here") == {} and da.cis_check_info(src0, "9.9_../../etc/passwd") == {})
withinfo = {f["id"]: f for f in da.cis_findings([("KO", "3.2.2_disable_send_packet_redirects", ["net.ipv4.conf.all.send_redirects was not set to 0"])], {},
                                                info=lambda c: da.cis_check_info(src0, c))}
check("  kept with the check in its section's finding", withinfo["cis-3.2"]["checks"][0]["cmd"].endswith("sudo sysctl --system")
      and withinfo["cis-3.2"]["checks"][0]["msgs"] == ["net.ipv4.conf.all.send_redirects was not set to 0"], withinfo["cis-3.2"])
check("the hotspot's dnsmasq and the floor's nftables: the box's design, accepted (whatever the check's number)",
      da.accepted("debian-cis", "2.2.99_dnsmasq_is_disabled") and da.accepted("debian-cis", "3.5.1.1_nftables_not_installed_with_iptables")
      and da.accepted("debian-cis", "9.9_disable_http_server"))

# debian-cis exported from its mirror and run with its paths in the environment.
check("no mirror of debian-cis: said, pointing at the security kit", da.run_cis(T)[0]["id"] == "cis-missing")
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
w = T / "cis-src"
(w / "bin").mkdir(parents=True); (w / "lib").mkdir(); (w / "etc").mkdir(); (w / "versions").mkdir()
(w / "bin" / "hardening.sh").write_text('#!/bin/bash\n[ "$1" = --audit-all ] && [ "$2" = --batch ] || exit 2\n'
                                        '[ -d "$CIS_LIB_DIR" ] && [ -d "$CIS_CONF_DIR" ] && [ -d "$CIS_VERSIONS_DIR" ] || { echo "no paths"; exit 1; }\n'
                                        'echo "KO 5.2.10_disable_root_login  KO{PermitRootLogin is not set to no}"\n'
                                        'echo "OK 1.1.1.1_disable_freevxfs  OK{fine}"\necho "AUDIT_SUMMARY PASSED_CHECKS:1 RUN_CHECKS:2 CONFORMITY_PERCENTAGE:50.00"\n')
(w / "lib" / ".keep").write_text(""); (w / "etc" / ".keep").write_text(""); (w / "versions" / ".keep").write_text("")
subprocess.run(["git", "init", "-q", "-b", "master", str(w)], check=True)
subprocess.run(["git", "add", "-A"], cwd=w, check=True); subprocess.run(["git", "commit", "-qm", "c"], cwd=w, env=ENV, check=True)
bare = T / "git" / "public" / "debian-cis.git"
bare.parent.mkdir(parents=True)
subprocess.run(["git", "clone", "-q", "--bare", str(w), str(bare)], check=True)
subprocess.run(["git", "--git-dir", str(bare), "config", "irate-box.mirror", "https://github.com/ovh/debian-cis"], check=True)
pinned = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "master"], capture_output=True, text=True).stdout.strip()
work = T / "work"; work.mkdir()
check("no pin from the kit, or a pin the mirror lacks: not run", da.run_cis(work, pin="")[0]["id"] in ("cis-unpinned", "cis-pin-missing")
      and da.run_cis(work, pin="0" * 40)[0]["id"] == "cis-pin-missing")
check("the shipped security kit pins a commit of debian-cis", re.fullmatch(r"[0-9a-f]{40}", da.cis_pin() or ""), da.cis_pin())
got = {f["id"]: f for f in da.run_cis(work, pin=pinned)}
check("from the mirror: exported, run with its paths, read", "cis-5.2.10_disable_root_login" in got and "1 of 2 checks pass (50.00 %)" in got["cis-summary"]["detail"], got)

# The deep audit: both, kept in control/security-deep.json, shown by every regular audit.
with mock.patch.object(da, "run_cis", return_value=da.cis_findings(checks, summary, 180)), \
     mock.patch.object(da, "run_lynis", return_value=da.lynis_findings(rep, 281)):
    line = da.deep()
kept = da.load()
check("the deep audit kept, with both sources and its time", set(kept["sources"]) == {"debian-cis", "lynis"} and "to look at: debian-cis 3, lynis 1" in line
      and not da.PROGRESS.exists(), line)
from irate_box.root import secdoctor as sd  # noqa: E402
steps = {sid: fn for sid, _, _, fn in sd.STEPS}
cis_step = steps["debian-cis"]({})
check("the regular audit shows them as steps, dated", any(f["id"] == "cis-5.2" and "(deep audit of " in f["detail"] for f in cis_step)
      and any(f["id"] == "lynis-SSH-7408" for f in steps["lynis"]({})))
j = sd.joint([{"findings": cis_step}])
check("  and they reach the joint report (accepted ones left out)", {i["about"]["key"] for i in j["items"]} == {"cis-4.1", "cis-5.2", "ssh-root-login"}, j["items"])
kept["sources"]["lynis"]["at"] -= 20 * 86400
(T / "state" / "control" / "security-deep.json").write_text(json.dumps(kept))
check("an old deep audit is a warning of its own", any(f["id"] == "lynis-old" and f["status"] == "warn" for f in steps["lynis"]({})))
(T / "state" / "control" / "security-deep.json").unlink()
check("none yet: said, not counted against the box", [f["status"] for f in steps["lynis"]({})] == ["ok"])

# Weekly, by the librarian, while the security kit is kept current.
from irate_box.library import librarian, toolkits  # noqa: E402
queued = []
librarian._queue_root = lambda req: (queued.append(req), "rid")[1]
librarian._open = lambda *a, **k: (_ for _ in ()).throw(librarian.LibrarianError("offline"))
with mock.patch.object(toolkits, "definitions", return_value={"security": {"id": "security", "packages": ["lynis"]}}):
    toolkits.step({"check_every_hours": 24})
    first = [q for q in queued if q["action"] == "security-deep-audit"]
    queued.clear(); toolkits.step({"check_every_hours": 24})
    again = [q for q in queued if q["action"] == "security-deep-audit"]
    queued.clear(); toolkits.step({"check_every_hours": 24}, now=time.time() + 8 * 86400)
    week = [q for q in queued if q["action"] == "security-deep-audit"]
check("the deep audit is asked for weekly, once", len(first) == 1 and not again and len(week) == 1, (first, again, week))
toolkits.set_settings({"deep_audit_days": 0})
queued.clear()
with mock.patch.object(toolkits, "definitions", return_value={"security": {"id": "security", "packages": ["lynis"]}}):
    toolkits.step({"check_every_hours": 24}, now=time.time() + 30 * 86400)
check("0 days: never on its own", not [q for q in queued if q["action"] == "security-deep-audit"])
from irate_box.root import hub_control  # noqa: E402
check("the root helper runs it", "security-deep-audit" in hub_control.ACTIONS)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
