# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box's often-written state onto the onboard flash (tools/nand-logs.sh state), offline: what "state plan"
says against a stood-in hub folder: the hub's small state to the flash; the named large folders (books, firmware,
kits, git, …) and any other past 8 MiB left on the card; the fstab lines in the order systemd must mount them (the
card's hub exposed first, the flash's over it after it, the large folders bound back from the card's copy, each
nofail); nothing changed by a plan. python3 tests/sim_nand_state.py"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="nand-state-"))
hub = T / "lib" / "hub"
for d in ("control", "notes", "zim", "firmware", "bigdir"):
    (hub / d).mkdir(parents=True)
(hub / "settings.json").write_text("{}\n")
(hub / "card-history.jsonl").write_text("{}\n" * 100)
(hub / "control" / "netinv.json").write_text("x" * 4000)
(hub / "notes" / "a.md").write_text("note\n")
(hub / "zim" / "book.zim").write_bytes(b"z" * 4096)
(hub / "bigdir" / "big.bin").write_bytes(os.urandom(9 << 20))
fstab = T / "fstab"
fstab.write_text("/dev/root / ext4 defaults 0 1\n")
before = sorted(str(p.relative_to(T)) for p in T.rglob("*"))
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


env = dict(
    os.environ,
    NAND_HUB=str(hub),
    NAND_HUB_CARD=str(T / "lib" / "hub.card"),
    NAND_FSTAB=str(fstab),
    NAND_STATE_FILE=str(T / "nand-logs.conf"),
)
out = subprocess.run(
    ["bash", str(REPO / "tools/nand-logs.sh"), "state", "plan"], capture_output=True, text=True, env=env
)
text = out.stdout
check("state plan: runs as any user, changes nothing", out.returncode == 0, out.stderr)
check(
    "  what stays on the card: the named large folders there (firmware, zim) and one past 8 MiB (bigdir)",
    "stays on the card: bigdir firmware zim" in text,
    text,
)
check(
    "  what goes to the flash: the hub's small state, in MiB",
    "to the flash: " in text and "MiB (the hub's state" in text,
    text,
)
lines = [ln.strip() for ln in text.split("fstab:", 1)[-1].splitlines() if ln.strip()]
card = str(T / "lib" / "hub.card")
esc = subprocess.run(["systemd-escape", "-p", "--suffix=mount", card], capture_output=True, text=True).stdout.strip()
check(
    "  the fstab lines in order: the card's hub exposed, then the flash's over it after that, nofail",
    lines[0] == f"{hub} {card} none bind 0 0"
    and lines[1].startswith(f"/var/log.nand/hub {hub} none bind,nofail,")
    and f"x-systemd.after={esc}" in lines[1],
    lines[:2],
)
check(
    "  then each large folder bound back from the card's copy, after both",
    [ln.split()[0] for ln in lines[2:5]] == [f"{card}/bigdir", f"{card}/firmware", f"{card}/zim"]
    and all(
        f"x-systemd.requires-mounts-for={hub}" in ln
        and f"x-systemd.requires-mounts-for={card}" in ln
        and "nofail" in ln
        for ln in lines[2:5]
    ),
    lines[2:5],
)
check(
    "  the system's folders that exist, each from the flash",
    any(
        ln.endswith("/var/lib/systemd none bind,nofail,x-systemd.requires-mounts-for=/var/log.nand 0 0")
        for ln in lines[5:]
    )
    or not Path("/var/lib/systemd").is_dir(),
    lines[5:],
)
check(
    "  nothing written: the fstab and the folders as they were",
    fstab.read_text() == "/dev/root / ext4 defaults 0 1\n"
    and sorted(str(p.relative_to(T)) for p in T.rglob("*")) == before,
)
out = subprocess.run(["bash", str(REPO / "tools/nand-logs.sh"), "state", "on"], capture_output=True, text=True, env=env)
check("state on: root only", out.returncode != 0 and "run as root" in out.stderr, out.stderr) if os.geteuid() else None

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
