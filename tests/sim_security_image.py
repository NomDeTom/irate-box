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
      ("kernel-links-on", "kernel-info-undo", "group-drop:lyra@docker", "group-undo:lyra@disk")))
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
