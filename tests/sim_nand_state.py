# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box's often-written state onto the onboard flash (tools/nand-logs.sh state), offline: what "state plan"
says against a stood-in hub folder: the hub's small state to the flash; the named large folders (books, firmware,
kits, git, …) and any other past 8 MiB left on the card; the hub's mounts in the order the boot unit makes them (the
card's hub exposed first, the flash's over it, the large folders bound back from the card's copy), none of them in
fstab; the system's folders in fstab, each nofail; nothing changed by a plan. python3 tests/sim_nand_state.py"""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import housecall

T = Path(tempfile.mkdtemp(prefix="nand-state-"))
hub = T / "lib" / "hub"
for d in ("control", "notes", "zim", "firmware", "bigdir", "holds"):
    (hub / d).mkdir(parents=True)
(hub / "settings.json").write_text("{}\n")
(hub / "card-history.jsonl").write_text("{}\n" * 100)
(hub / "control" / "netinv.json").write_text("x" * 4000)
(hub / "notes" / "a.md").write_text("note\n")
(hub / "zim" / "book.zim").write_bytes(b"z" * 4096)
(hub / "bigdir" / "big.bin").write_bytes(os.urandom(9 << 20))
(hub / "holds" / "app.apk").write_bytes(b"apk")
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
listed = [
    line.strip()
    for line in (REPO / "irate_box" / "large_folders.txt").read_text().splitlines()
    if line.strip() and not line.lstrip().startswith("#")
]
check(
    "the hub's large folders are one list, read by nand-logs.sh and the Housecall doctor",
    list(housecall.LARGE) == listed,
    (housecall.LARGE, listed),
)
script = (REPO / "tools" / "nand-logs.sh").read_text()
builtin = re.search(r"HUB_LARGE_BUILTIN=\(([^)]*)\)", script)
check(
    "  the built-in lists agree with that file too",
    builtin and builtin.group(1).split() == listed and list(housecall.LARGE_BUILTIN) == listed,
    (builtin.group(1).split() if builtin else None, list(housecall.LARGE_BUILTIN), listed),
)
check(
    "  what stays on the card: the named large folders there (firmware, zim) and one past 8 MiB (bigdir)",
    "stays on the card: bigdir firmware holds zim" in text,
    text,
)
check(
    "  what goes to the flash: the hub's small state, in MiB",
    "to the flash: " in text and "MiB (the hub's state" in text,
    text,
)
boot = [ln.strip() for ln in text.split("at boot (nand-state.service):", 1)[-1].split("fstab:", 1)[0].splitlines()]
boot = [ln for ln in boot if ln]
lines = [ln.strip() for ln in text.split("fstab:", 1)[-1].splitlines() if ln.strip()]
card = str(T / "lib" / "hub.card")
check(
    "  the hub's mounts made by the boot unit in order: the card's hub exposed (private), the flash's over it",
    boot[:2] == [f"bind {hub} {card} (private)", f"bind /var/log.nand/hub {hub}"],
    boot,
)
check(
    "  then each large folder bound back from the card's copy",
    boot[2:] == [f"bind {card}/{d} {hub}/{d}" for d in ("bigdir", "firmware", "holds", "zim")],
    boot[2:],
)
check(
    "  no fstab line for the hub (a bind of its own folder: an ordering cycle at boot)",
    not any(str(hub) in ln for ln in lines),
    lines,
)
check(
    "  the system's folders that exist, each from the flash; never /var/lib/systemd (read early in boot)",
    any(ln.endswith("/var/tmp none bind,nofail,x-systemd.requires-mounts-for=/var/log.nand 0 0") for ln in lines)
    and not any(" /var/lib/systemd " in ln for ln in lines),
    lines,
)
check(
    "  nothing written: the fstab and the folders as they were",
    fstab.read_text() == "/dev/root / ext4 defaults 0 1\n"
    and sorted(str(p.relative_to(T)) for p in T.rglob("*")) == before,
)
missing_env = dict(env, NAND_LARGE_FILE=str(T / "no-large-folders.txt"))
out = subprocess.run(
    ["bash", str(REPO / "tools/nand-logs.sh"), "state", "plan"], capture_output=True, text=True, env=missing_env
)
check(
    "a missing large-folder list falls back to the built-in one, and says so",
    out.returncode == 0 and "built-in list" in out.stdout and "holds" in out.stdout,
    (out.returncode, out.stdout, out.stderr),
)
out = subprocess.run(["bash", str(REPO / "tools/nand-logs.sh"), "state", "on"], capture_output=True, text=True, env=env)
check("state on: root only", out.returncode != 0 and "run as root" in out.stderr, out.stderr) if os.geteuid() else None

# A named large folder the flash still holds from a move made before the list named it: state adopt puts it back
# on the card, records it, and empties the flash's copy. It writes, so it runs as root (or in a user namespace).
flash = T / "flash"
(flash / "hub" / "holds" / "app").mkdir(parents=True)
(flash / "hub" / "holds" / "app" / "new.apk").write_bytes(b"new")
(hub / "holds" / "old.apk").write_bytes(b"stale")
state_file = Path(env["NAND_STATE_FILE"])
state_file.write_text("mtd=0\ncrc=1\nstate=1\nstate_large=bigdir firmware zim\n")
adopt_env = dict(env, NAND_MNT=str(flash))
adopt = ["bash", str(REPO / "tools/nand-logs.sh"), "state", "adopt"]
can_unshare = os.geteuid() == 0 or subprocess.run(["unshare", "-Ur", "true"], capture_output=True).returncode == 0
if can_unshare:
    if os.geteuid():
        adopt = ["unshare", "-Ur"] + adopt
    out = subprocess.run(adopt, capture_output=True, text=True, env=adopt_env)
    check("state adopt: runs", out.returncode == 0, out.stderr)
    check(
        "  the flash's named folder is on the card, its stale copy replaced",
        (hub / "holds" / "app" / "new.apk").read_text() == "new" and not (hub / "holds" / "old.apk").exists(),
        sorted(p.name for p in (hub / "holds").rglob("*")),
    )
    check(
        "  the flash's copy is emptied, the folder kept as the mount point",
        (flash / "hub" / "holds").is_dir() and not any((flash / "hub" / "holds").iterdir()),
        list((flash / "hub" / "holds").iterdir()),
    )
    recorded = next(
        (ln.split("=", 1)[1].split() for ln in state_file.read_text().splitlines() if ln.startswith("state_large=")), []
    )
    check(
        "  it is recorded with the names already there",
        {"holds", "bigdir", "firmware", "zim"} <= set(recorded),
        recorded,
    )
    # An empty named folder is still kept on the card: both sides made, and the name recorded.
    T2 = T / "empty-holds"
    hub2 = T2 / "lib" / "hub"
    hub2.mkdir(parents=True)
    flash2 = T2 / "flash"
    (flash2 / "hub" / "holds").mkdir(parents=True)
    state2 = T2 / "nand-logs.conf"
    state2.write_text("mtd=0\ncrc=1\nstate=1\nstate_large=zim\n")
    env2 = dict(env, NAND_HUB=str(hub2), NAND_HUB_CARD=str(T2 / "lib" / "hub.card"), NAND_MNT=str(flash2))
    env2["NAND_STATE_FILE"] = str(state2)
    cmd = ["bash", str(REPO / "tools/nand-logs.sh"), "state", "adopt"]
    if os.geteuid():
        cmd = ["unshare", "-Ur"] + cmd
    out = subprocess.run(cmd, capture_output=True, text=True, env=env2)
    recorded2 = next(
        (ln.split("=", 1)[1].split() for ln in state2.read_text().splitlines() if ln.startswith("state_large=")), []
    )
    check(
        "state adopt: an empty named folder is made on both sides and recorded",
        out.returncode == 0
        and (hub2 / "holds").is_dir()
        and (flash2 / "hub" / "holds").is_dir()
        and "holds" in recorded2,
        (out.stderr, recorded2),
    )
else:
    print("SKIP state adopt: no root and no user namespace")

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
