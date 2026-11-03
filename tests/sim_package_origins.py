# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Updates doctor's package checks (root/pkgorigins.py): where each installed package comes from and the
warning for one the hub or a toolkit relies on from outside Debian; the record kept for a day; Debian's required,
important and standard packages that are missing, with the System toolkit named where it carries one; and the
board's own packages, installed and waiting. apt, dpkg and the package lists stood in; each case by message id.
python3 tests/sim_package_origins.py"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="origins-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_PACKAGE_ORIGINS=str(T / "origins.json"))
sys.path.insert(0, str(REPO))
from irate_box.finding import message_args as msg_args
from irate_box.finding import message_ids as msg_ids
from irate_box.root import pkgorigins

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


class R:
    def __init__(self, rc=0, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


INSTALLED = [
    "bash",
    "dpkg",
    "platformio",
    "tailscale",
    "meshtasticd",
    "linux-image-vendor-rockchip",
    "linux-u-boot-luckfox-lyra",
    "armbian-bsp-cli-luckfox-lyra-zero-w",
]
POLICY = """
bash:
  Installed: 5.2.37-2
  Candidate: 5.2.37-2
  Version table:
 *** 5.2.37-2 500
        500 http://deb.debian.org/debian trixie/main armhf Packages
        100 /var/lib/dpkg/status

platformio:
  Installed: 6.1.16
  Candidate: 6.1.16
  Version table:
 *** 6.1.16 500
        500 https://download.opensuse.org/repositories/network:/Meshtastic:/beta/Debian_13  Packages
        100 /var/lib/dpkg/status

tailscale:
  Installed: 1.80.0
  Candidate: 1.80.0
  Version table:
 *** 1.80.0 500
        500 https://pkgs.tailscale.com/stable/debian trixie/main armhf Packages
        100 /var/lib/dpkg/status

meshtasticd:
  Installed: 2.5.0
  Candidate: 2.5.0
  Version table:
 *** 2.5.0 500
        500 https://download.opensuse.org/repositories/network:/Meshtastic:/beta/Debian_13  Packages

dpkg:
  Installed: 1.22.21
  Candidate: 1.22.21
  Version table:
 *** 1.22.21 500
        500 http://deb.debian.org/debian trixie/main armhf Packages
"""
policy_calls = []


def run(*cmd, timeout=120):
    policy_calls.append(list(cmd))
    if cmd[0] == "dpkg-query":
        return R(out="".join(f"{n}\tinstall ok installed\n" for n in INSTALLED))
    if cmd[0] == "dpkg" and cmd[1] == "--print-architecture":
        return R(out="armhf\n")
    if cmd[0] == "apt-cache" and cmd[1] == "policy":
        wanted = set(cmd[2:])
        blocks = [b for b in POLICY.split("\n\n") if b.strip() and b.strip().split(":")[0] in wanted]
        return R(out="\n\n".join(blocks) + "\n")
    return R()


DUMP = """Package: bash
Architecture: armhf
Priority: required

Package: e2fsprogs
Architecture: armhf
Priority: required

Package: dpkg
Architecture: armhf
Priority: required

Package: nftables
Architecture: armhf
Priority: important

Package: less
Architecture: armhf
Priority: standard

Package: linux-image-otherboard
Architecture: arm64
Priority: important
"""

patches = [
    mock.patch.object(pkgorigins, "run", run),
    mock.patch.object(pkgorigins, "_dumpavail", lambda: iter(DUMP.splitlines(True))),
]
for p in patches:
    p.start()
try:
    found = {f["id"]: f for f in pkgorigins.origin_findings()}
    org = found["package-origins"]
    check(
        "a package the toolkits rely on from outside Debian (platformio from the OBS): a warning naming it",
        org["status"] == "warn"
        and msg_args(org, "origins.outside")["n"] == 3
        and "platformio: openSUSE Build Service" in org["evidence"]
        and not any(e.startswith("bash:") for e in org["evidence"]),
        org,
    )
    check(
        "  the record is kept where the next run reads it",
        json.loads((T / "origins.json").read_text())["packages"]["platformio"]["source"] == "other",
    )
    before = len(policy_calls)
    pkgorigins.origins()
    check(
        "  a second run the same day does not ask apt-cache again", len(policy_calls) == before, policy_calls[before:]
    )

    pri = {f["id"]: f for f in pkgorigins.priority_findings()}
    check(
        "required missing: a problem, naming the package",
        pri["package-priority-required"]["status"] == "problem"
        and pri["package-priority-required"]["evidence"] == ["e2fsprogs"],
        pri.get("package-priority-required"),
    )
    check(
        "important missing where the System toolkit carries it: a warning with that cure",
        pri["package-priority-important"]["status"] == "warn"
        and "priorities.missing.fix-kit" in msg_ids(pri["package-priority-important"])
        and msg_args(pri["package-priority-important"], "priorities.missing.fix-kit") == {"names": "nftables"},
        pri.get("package-priority-important"),
    )
    check(
        "standard missing: a warning, and a package for another architecture is not counted",
        pri["package-priority-standard"]["status"] == "warn"
        and pri["package-priority-standard"]["evidence"] == ["less"],
        pri.get("package-priority-standard"),
    )

    simulated = (
        "Inst linux-image-vendor-rockchip (26.8.3 armhf [armbian]) []\n"
        "Inst linux-u-boot-luckfox-lyra (2024.10 armhf [armbian]) []\n"
        "Inst bash (5.2.37-2 armhf [debian]) []\n"
    )
    board = {f["id"]: f for f in pkgorigins.board_findings(simulated)}["board-packages"]
    check(
        "the board's packages: a warning for the kernel and bootloader waiting, with both warnings said",
        board["status"] == "warn"
        and msg_args(board, "board-packages.waiting") == {"n": 2}
        and "board-packages.kernel" in msg_ids(board)
        and "board-packages.bootloader" in msg_ids(board)
        and any(e.startswith("kernel: linux-image-vendor-rockchip waits") for e in board["evidence"]),
        board,
    )
    clean = {f["id"]: f for f in pkgorigins.board_findings("")}["board-packages"]
    check(
        "nothing waiting: ok, the installed board packages named",
        clean["status"] == "ok" and any(e.startswith("kernel: linux-image") for e in clean["evidence"]),
        clean,
    )
finally:
    for p in patches:
        p.stop()

# A plain Debian box: nothing from outside Debian that the hub relies on, and no board packages.
INSTALLED[:] = ["bash", "dpkg"]
policy_calls.clear()
patches = [mock.patch.object(pkgorigins, "run", run)]
for p in patches:
    p.start()
try:
    org = {f["id"]: f for f in pkgorigins.origin_findings(pkgorigins.origins(refresh=True))}["package-origins"]
    check("plain Debian: ok, nothing outside", org["status"] == "ok" and "origins.ok" in msg_ids(org), org)
finally:
    for p in patches:
        p.stop()

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
