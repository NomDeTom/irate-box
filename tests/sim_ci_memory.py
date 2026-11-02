# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Builds inside the build limits, offline: the CI unit install.sh writes runs in build.slice with no memory cap of
its own; a build's record counts the processes the kernel killed for memory and the memory and swap it was allowed
(the smallest limits on its cgroup's path), from a stand-in cgroup tree shaped like a Lyra's.
python3 tests/sim_ci_memory.py"""

import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="cimem-"))
os.environ.update(HUB_CI_ROOT=str(T / "ci"))
(T / "ci" / "runs").mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import ci

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


# The unit as install.sh writes it.
text = (REPO / "install.sh").read_text()
unit = text[text.index("cat >/etc/systemd/system/irate-box-ci.service <<EOF") :]
unit = unit[: unit.index("\nEOF\n")]
lines = [ln for ln in unit.splitlines() if not ln.lstrip().startswith("#")]
check("the CI unit runs in build.slice", "Slice=build.slice" in lines, lines)
check(
    "  with no memory or swap cap of its own: the owner's build limits are the ones",
    not [ln for ln in lines if re.match(r"Memory(High|Max|SwapMax)=", ln)],
    lines,
)

# A cgroup tree: build.slice with the default limits, the CI unit below it without its own.
root = T / "cgroup"
service = root / "build.slice" / "irate-box-ci.service"
service.mkdir(parents=True)
(root / "build.slice" / "memory.max").write_text(f"{220 * 2**20}\n")
(root / "build.slice" / "memory.swap.max").write_text(f"{64 * 2**20}\n")
(service / "memory.max").write_text("max\n")
(service / "memory.swap.max").write_text("max\n")
(service / "memory.current").write_text(f"{150 * 2**20}\n")
events = service / "memory.events"
events.write_text("low 0\nhigh 12\nmax 3\noom 1\noom_kill 1\noom_group_kill 0\n")


class FakeUsage(ci.Usage):
    CGROUP_ROOT = root

    @classmethod
    def _cgroup(cls):
        return service / "memory.current"


with FakeUsage(every=60) as usage:
    events.write_text("low 0\nhigh 40\nmax 9\noom 3\noom_kill 3\noom_group_kill 0\n")
res = usage.result
check("the kills counted are this build's: 2, not the cgroup's 3", res.get("memory_killed") == 2, res)
check(
    "  the limits are build.slice's: 220 MB, 64 MB swap",
    res.get("memory_limit") == 220 * 2**20 and res.get("swap_limit") == 64 * 2**20,
    res,
)
check("  the peak from the unit's own counter", res.get("peak_memory") == 150 * 2**20, res)

with FakeUsage(every=60) as usage:
    pass
check("a build the kernel left alone: 0 killed", usage.result.get("memory_killed") == 0, usage.result)

events.unlink()
with FakeUsage(every=60) as usage:
    pass
check("  no memory.events (cgroup v1): nothing claimed", "memory_killed" not in usage.result, usage.result)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
