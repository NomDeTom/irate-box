# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Security page's offers for the inherited image (next-work plan step 8): the kernel's link
protections and its addresses and log, and login accounts in the docker and disk groups. Each is
found, fixed and undone against a fake /proc/sys, /etc/group and /etc/passwd, with a stand-in
gpasswd; uninstall's undo_all puts everything back. python3 tests/sim_security_image.py"""
import json, os, stat, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="sec-image-"))
PROC = T / "proc"
for key, v in {"fs/protected_symlinks": "0", "fs/protected_hardlinks": "0", "kernel/kptr_restrict": "0", "kernel/dmesg_restrict": "0"}.items():
    (PROC / key).parent.mkdir(parents=True, exist_ok=True)
    (PROC / key).write_text(v + "\n")
(T / "group").write_text("root:x:0:\ndisk:x:6:lyra\ndocker:x:990:lyra,svc\nsudo:x:27:lyra\n")
(T / "passwd").write_text("root:x:0:0::/root:/bin/bash\nlyra:x:1000:1000::/home/lyra:/bin/bash\n"
                          "svc:x:1001:1001::/srv:/usr/sbin/nologin\n")
BIN = T / "bin"; BIN.mkdir()
# gpasswd -d USER GROUP / -a USER GROUP, on the fake group file.
(BIN / "gpasswd").write_text(f"""#!/usr/bin/env python3
import sys
op, user, group = sys.argv[1:4]
p = "{T / 'group'}"
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
    "systemctl": f"import sys, os\nif sys.argv[1:] == ['restart', 'systemd-sysctl.service'] and os.path.exists('{T}/installed'):\n"
                 f"    [open('{PROC}/fs/' + k, 'w').write('1\\n') for k in ('protected_symlinks', 'protected_hardlinks')]",
}.items():
    (BIN / name).write_text("#!/usr/bin/env python3\n" + body + "\n"); (BIN / name).chmod(0o755)
(T / "root-pw").write_text("P")
(T / "firstrun").write_text("armbian first login pending\n")
os.environ.update(HUB_ARMBIAN_FIRSTRUN=str(T / "firstrun"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_PROC_SYS=str(PROC), HUB_GROUP_FILE=str(T / "group"),
                  HUB_PASSWD_FILE=str(T / "passwd"), HUB_SYSCTL_DROPIN=str(T / "sysctl.d" / "60-irate-box.conf"),
                  PATH=f"{BIN}:{os.environ['PATH']}")
sys.path.insert(0, str(REPO))
from irate_box.root import security  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def findings():
    rec = security.load_record()
    return {f["id"]: f for f in security.kernel_findings(rec) + security.group_findings(rec)}
def sysctl(key):
    return (PROC / key.replace(".", "/")).read_text().strip()

f = findings()
check("links off: a problem, with a button", f["kernel-links"]["status"] == "problem"
      and [a["choice"] for a in f["kernel-links"]["actions"]] == ["kernel-links-on"], f["kernel-links"])
check("addresses and log: a warning, with a button", f["kernel-info"]["status"] == "warn"
      and f["kernel-info"]["actions"][0]["choice"] == "kernel-info-on")
check("docker: lyra listed, not the nologin service account", f["group-docker"]["status"] == "warn"
      and "lyra" in f["group-docker"]["detail"] and "svc" not in f["group-docker"]["detail"]
      and [a["choice"] for a in f["group-docker"]["actions"]] == ["group-drop:lyra@docker"], f["group-docker"])
check("disk: lyra listed", f["group-disk"]["status"] == "warn")
check("sudo is not a group this page touches", "group-sudo" not in f)

print(security.fix("kernel-links-on", None))
check("links on: set now", sysctl("fs.protected_symlinks") == "1" and sysctl("fs.protected_hardlinks") == "1")
drop = Path(os.environ["HUB_SYSCTL_DROPIN"])
check("links on: and at every boot", drop.exists() and "fs.protected_symlinks = 1" in drop.read_text())
f = findings()
check("links on: ok, with Undo", f["kernel-links"]["status"] == "ok" and f["kernel-links"]["actions"][0]["choice"] == "kernel-links-undo")
print(security.fix("kernel-info-on", None))
check("both sets in the one file", "kernel.dmesg_restrict = 1" in drop.read_text() and "fs.protected_hardlinks = 1" in drop.read_text())
print(security.fix("kernel-links-undo", None))
check("links undone: the old values back, the file keeps the other set",
      sysctl("fs.protected_symlinks") == "0" and "protected_symlinks" not in drop.read_text() and "kptr_restrict" in drop.read_text())

(T / "apt-candidate").touch()
f = findings()
check("links off, Debian's defaults to be had: offered first, the hub's own two beside it",
      [a["choice"] for a in f["kernel-links"]["actions"]] == ["kernel-links-debian", "kernel-links-on"]
      and "linux-sysctl-defaults" in f["kernel-links"]["fix"] and f["kernel-links"]["actions"][0].get("confirm"), f["kernel-links"])
print(security.fix("kernel-links-debian", None))
check("Debian's defaults: installed, applied, on now", (T / "installed").exists() and sysctl("fs.protected_symlinks") == "1"
      and "install -y --no-install-recommends linux-sysctl-defaults" in (T / "apt-log").read_text())
check("  not in the hub's own file (Debian's carries them); the other set still there",
      "protected_symlinks" not in drop.read_text() and "kptr_restrict" in drop.read_text())
f = findings()
check("  ok, with Undo", f["kernel-links"]["status"] == "ok" and f["kernel-links"]["actions"][0]["choice"] == "kernel-links-undo")
print(security.fix("kernel-links-undo", None))
check("  undone: the package removed, the old values back",
      not (T / "installed").exists() and "remove -y linux-sysctl-defaults" in (T / "apt-log").read_text()
      and sysctl("fs.protected_symlinks") == "0")
(T / "apt-candidate").unlink()

# Root's own login.
def rootf():
    return {f["id"]: f for f in security.root_findings(security.load_record())}
f = rootf()
check("root with a password and Armbian's first login never run: a problem (the image's own), lock offered (lyra has sudo)",
      f["root-password"]["status"] == "problem" and "1234" in f["root-password"]["detail"]
      and [a["choice"] for a in f["root-password"]["actions"]] == ["root-lock"] and "lyra" in f["root-password"]["actions"][0]["confirm"], f["root-password"])
check("  first login never run: a problem, with the marker put aside offered", f["root-firstrun"]["status"] == "problem"
      and f["root-firstrun"]["actions"][0]["choice"] == "firstrun-off")
(T / "etc").mkdir(exist_ok=True)
print(security.fix("root-lock", None)); print(security.fix("firstrun-off", None))
f = rootf()
check("  locked, the marker aside (kept): both ok, each with its undo", (T / "root-pw").read_text() == "L" and not (T / "firstrun").exists()
      and (T / "etc" / "armbian-firstrun.kept").read_text() == "armbian first login pending\n"
      and f["root-password"]["status"] == "ok" and f["root-password"]["actions"][0]["choice"] == "root-lock-undo"
      and f["root-firstrun"]["status"] == "ok" and f["root-firstrun"]["actions"][0]["choice"] == "firstrun-undo", f)
print(security.fix("root-lock-undo", None)); print(security.fix("firstrun-undo", None))
check("  undone: the password as it was, the marker back", (T / "root-pw").read_text() == "P" and (T / "firstrun").exists()
      and not (T / "etc" / "armbian-firstrun.kept").exists())
(T / "firstrun").unlink()
check("root with a password of its own (first login done): a warning, lock offered", rootf()["root-password"]["status"] == "warn"
      and "root-firstrun" not in rootf())
(T / "root-pw").write_text("NP")
check("root with no password: a problem", rootf()["root-password"]["status"] == "problem" and "no password" in rootf()["root-password"]["detail"])
grp = (T / "group").read_text(); (T / "group").write_text(grp.replace("sudo:x:27:lyra", "sudo:x:27:"))
check("  nobody else with sudo: no lock offered, and said why", rootf()["root-password"]["actions"] == []
      and "add one first" in rootf()["root-password"]["fix"])
try:
    security.fix("root-lock", None); refused_ = False
except ValueError:
    refused_ = True
check("  and refused if asked anyway", refused_ and (T / "root-pw").read_text() == "NP")
(T / "group").write_text(grp); (T / "root-pw").write_text("L")

print(security.fix("group-drop:lyra@docker", None))
check("lyra out of docker", "lyra" not in (T / "group").read_text().split("docker:x:990:")[1].split("\n")[0])
f = findings()
check("docker: svc only (no login), so ok, with Put back", f["group-docker"]["status"] == "ok"
      and f["group-docker"]["actions"][0]["choice"] == "group-undo:lyra@docker", f["group-docker"])
for bad in ("group-drop:lyra@sudo", "group-drop:root;x@docker", "group-undo:lyra@disk", "kernel-nope-on"):
    try:
        security.fix(bad, None); refused = False
    except ValueError:
        refused = True
    check(f"refused: {bad}", refused)

print(security.undo_all())
check("undo_all: lyra back in docker", "lyra" in (T / "group").read_text().split("docker:x:990:")[1].split("\n")[0])
check("undo_all: kernel values back, the file gone", sysctl("kernel.kptr_restrict") == "0" and not drop.exists())
check("undo_all: no record left", not (T / "etc" / "security-changes.json").exists())
srv = (REPO / "irate_box" / "hub" / "server.py").read_text()
import re
rx = re.compile(re.search(r'SECURITY_CHOICE_RE = re.compile\(r"(.*?)"\)', srv).group(1))
check("the hub passes these choices to the helper", all(rx.match(c) for c in
      ("kernel-links-on", "kernel-links-debian", "kernel-info-undo", "root-lock", "firstrun-off", "firstrun-undo", "group-drop:lyra@docker", "group-undo:lyra@disk")))
# I3 (stance review 2026-10-08): automatic security updates judged by what would run, not by
# the binary being there. Armbian ships APT::Periodic::Enable "0".
uf = security.unattended_finding
check("unattended: not installed is a warning", uf(False, {}, None)["status"] == "warn")
check("  installed but apt's periodic work off (the image's setting): a warning that says so",
      uf(True, {"APT::Periodic::Enable": "0", "APT::Periodic::Unattended-Upgrade": "7"}, None)["status"] == "warn"
      and "never runs" in uf(True, {"APT::Periodic::Enable": "0", "APT::Periodic::Unattended-Upgrade": "7"}, None)["detail"])
check("  Unattended-Upgrade unset or 0: likewise", uf(True, {}, 1)["status"] == "warn" and uf(True, {"APT::Periodic::Unattended-Upgrade": "0"}, 1)["status"] == "warn")
check("  on, but never ran or ran long ago: a warning", uf(True, {"APT::Periodic::Unattended-Upgrade": "1"}, None)["status"] == "warn"
      and uf(True, {"APT::Periodic::Unattended-Upgrade": "1"}, 30)["status"] == "warn")
check("  on and ran this week: ok", uf(True, {"APT::Periodic::Unattended-Upgrade": "1"}, 2)["status"] == "ok")

# I6 (stance review 2026-10-08): SSH forwarding, found from sshd -T's words and offered off.
sf = lambda s, rec={}: {f["id"]: f for f in security.ssh_findings(s, ["lyra"], rec)}  # noqa: E731
on = sf({"permitrootlogin": "no", "passwordauthentication": "no", "allowtcpforwarding": "yes", "allowagentforwarding": "no", "x11forwarding": "yes"})
check("ssh forwarding on (the image's default): a warning naming which, with the offer",
      on["ssh-forwarding"]["status"] == "warn" and "TCP, X11" in on["ssh-forwarding"]["detail"]
      and on["ssh-forwarding"]["actions"][0]["choice"] == "ssh-forwarding-off", on["ssh-forwarding"])
off = sf({"permitrootlogin": "no", "passwordauthentication": "no", "allowtcpforwarding": "no", "allowagentforwarding": "no", "x11forwarding": "no"}, {"ssh": {"forwarding": "2026-10-08"}})
check("  off by this page: ok, with Undo", off["ssh-forwarding"]["status"] == "ok" and off["ssh-forwarding"]["actions"][0]["choice"] == "ssh-forwarding-undo")
check("  undo_all knows it, fix() takes it", "ssh-forwarding-undo" in (REPO / "irate_box/root/security.py").read_text().split("def undo_all")[1]
      and '"ssh-forwarding-off", "ssh-forwarding-undo"' in (REPO / "irate_box/root/security.py").read_text())

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
