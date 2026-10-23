# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What root copies and what it switches off (audit L21-L23): a USB copy refuses a file with a second name (a hard
link) or one that is not a file; install.sh leaves no setuid, setgid or sticky bit on the apps it copies; and
uninstall.sh switches off only the hub's own web server, never the service that gave up port 80 for it.
python3 tests/sim_root_copies.py"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import usbstick

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


T = Path(tempfile.mkdtemp(prefix="root-copies-"))

# L22
(T / "book.zim").write_bytes(b"z" * 100)
usbstick._copy(T / "book.zim", T / "copy.zim")
check("a plain file: copied", (T / "copy.zim").read_bytes() == b"z" * 100)
os.link(T / "book.zim", T / "second-name.zim")
for what, src in (("a file with a second name (a hard link)", T / "second-name.zim"), ("a FIFO", T / "fifo.zim")):
    if what == "a FIFO":
        os.mkfifo(src)
    try:
        usbstick._copy(src, T / "out.zim")
        refused = False
    except Refused as e:
        refused = e.message["id"] == "usb.copy.not-plain"
    check(f"{what}: refused, nothing copied", refused and not (T / "out.zim").exists())

# L21
inst = (REPO / "install.sh").read_text()
copies = [m.start() for m in re.finditer(r'cp -a "\$APPS_SRC/[^"]*" "\$(APPS|ROOM)', inst)]
check(
    "install.sh: each app copied from a tree loses its setuid, setgid and sticky bits",
    copies and all(re.search(r"chmod -R a-st", inst[i : i + 400]) for i in copies),
    len(copies),
)

# L23: the uninstaller's port-80 step, run with systemctl stood in for.
un = (REPO / "uninstall.sh").read_text()
block = re.search(r'\ttaken="\$\(python3.*?\n\tfi\n', un, re.S)
check("uninstall.sh reads the unit that gave up port 80 from what was recorded", block is not None)


def stopped(units):
    (T / "security-changes.json").write_text(json.dumps({"units": units}))
    script = (
        'systemctl() { echo "$@" >>"$T/calls"; }\nETC="$T"\nNGINX_OURS=1\n'
        + block.group(0)
        + 'cat "$T/calls" 2>/dev/null; rm -f "$T/calls"\n'
    )
    return subprocess.run(["bash", "-c", script], env=dict(os.environ, T=str(T)), capture_output=True, text=True).stdout


got = stopped({"lighttpd.service": {"reason": "install.sh --take-port-80"}})
check(
    "another service gave up port 80: the hub's nginx, which irate-box installed, is switched off", "nginx" in got, got
)
got = stopped({"nginx.service": {"reason": "install.sh --take-port-80"}})
check("nginx itself gave up port 80: left for the restore, not switched off", "nginx" not in got, got)
got = stopped({"cockpit.socket": {"reason": "from this page"}})
check("nothing gave up port 80: nothing switched off", got == "", got)

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
