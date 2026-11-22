# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The clock that ran six days behind: the feed refuses to send a module sample against a synchronised non-module
reference (lyra-tft's real journal, tracking and sources), and the Security doctor reports apt's refused list with
the action that steps the clock. python3 tests/sim_clock_behind.py"""

import os
import re
import shutil
import sys
import tempfile
import types
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="clock-behind-"))
LISTS = T / "apt-lists"
LISTS.mkdir()
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    HUB_APT_LISTS=str(LISTS),
    HUB_UNIT_DIR=str(T / "systemd"),
)
sys.path.insert(0, str(REPO))
from irate_box import msg
from irate_box.root import clock, hub_control, secdoctor as sd
import irate_box.root as root_pkg

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


capture = (REPO / "tests/fixtures/clock-lyra-tft-2026-11-22.txt").read_text()
tracking = capture.split("### chronyc -n tracking (after the fix)\n", 1)[1].split("### chronyc -n sources", 1)[0]
sources = capture.split("### chronyc -n sources (after the fix)\n", 1)[1].split("### ", 1)[0]
check(
    "chrony's real tracking reads as synchronised, from the pool",
    (clock.tracking(tracking) or {})["synced"] and clock.tracking(tracking)["ref"] == "85.199.214.99",
    clock.tracking(tracking),
)
rows = {r["name"]: r for r in clock.parse_chronyc(sources) if r["mode"] == "refclock"}
check(
    "  its real RTC line is silent, not selected",
    rows["RTC"]["state"] == "not-answering" and rows["RTC"]["mark"] == "silent",
    rows,
)

# The feed, with the values from the journal: the system clock is 528 729 s ahead of the module.
NOW = 1795000000.0
sent, saved = [], []


class FakeSock:
    def sendto(self, data, path):
        sent.append((data, path))


fake_rtc = types.SimpleNamespace(
    load_config=lambda: {"chip": "ds3231", "mode": "user"},
    read_module=lambda cfg: (int(NOW - 528729), None, []),
    save=lambda: saved.append(1) or msg.M("rtc.test.saved", "The box set the clock module from the system clock."),
    chrony_reference=lambda: "85.199.214.99",
    ntp_synced=lambda: True,
)
real_socket = clock.socket.socket
clock.socket.socket = lambda *a: FakeSock()
real_time = clock.time.time
clock.time.time = lambda: NOW
sys.modules["irate_box.root.rtc"] = fake_rtc
real_rtc_attr = root_pkg.rtc
root_pkg.rtc = fake_rtc
try:
    clock.feed(once=True)
finally:
    clock.socket.socket = real_socket
    clock.time.time = real_time
    root_pkg.rtc = real_rtc_attr
    del sys.modules["irate_box.root.rtc"]
check(
    "a synchronised system clock more than 2 s from the module: the module is set first, no sample is sent",
    saved == [1] and not sent,
    (saved, sent),
)

sent.clear()
fake_rtc.chrony_reference = lambda: "RTC"
fake_rtc.ntp_synced = lambda: True
clock.socket.socket = lambda *a: FakeSock()
clock.time.time = lambda: NOW
sys.modules["irate_box.root.rtc"] = fake_rtc
real_rtc_attr = root_pkg.rtc
root_pkg.rtc = fake_rtc
try:
    clock.feed(once=True)
finally:
    clock.socket.socket = real_socket
    clock.time.time = real_time
    root_pkg.rtc = real_rtc_attr
    del sys.modules["irate_box.root.rtc"]
check(
    "  when chrony's reference is the module itself, the sample is sent as before",
    len(sent) == 1 and not saved[1:],
    sent,
)

# apt's real refusal, and an InRelease header with the same date.
log = T / "state" / "control" / "security-updates.log"
log.parent.mkdir(parents=True)
refusal = next(line for line in capture.splitlines() if "Not live until" in line)
log.write_text(refusal + "\n")
(LISTS / "deb.debian.org_debian_dists_trixie-security_InRelease").write_text(
    "-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\nDate: Sat, 21 Nov 2026 00:23:04 UTC\nSuite: stable\n"
)
behind = sd.clock_behind(now=1794000000)
check(
    "apt's real refused list and the InRelease date are both found while the clock is behind",
    len(behind) == 2 and any("Not live until 2026-11-21" in what for _, what in behind),
    behind,
)
check("  and once the clock is past them, neither is", sd.clock_behind(now=1796000000) == [])

with mock.patch.object(sd, "clock_behind", lambda: behind), mock.patch.object(sd, "_sc", lambda *a: "active\n"):
    found = {f["id"]: f for f in sd.step_clock({})}
check(
    "the doctor reports it with the action to step the clock",
    found["clock-behind"]["status"] == "warn"
    and [a["choice"] for a in found["clock-behind"]["actions"]] == ["clock-makestep"]
    and "clock.behind.why" in {m["id"] for m in (found["clock-behind"]["summary"], found["clock-behind"]["why"])},
    found,
)
with mock.patch.object(sd, "clock_behind", lambda: behind), mock.patch.object(sd, "_sc", lambda *a: "inactive\n"):
    found = {f["id"]: f for f in sd.step_clock({})}
check(
    "  without chrony, it points at the Clock page and offers no action",
    found["clock-behind"]["actions"] == [] and found["clock-behind"]["fix"]["id"] == "clock.behind.fix.clock-page",
    found,
)

calls = []


def fake_run(*cmd, **kw):
    calls.append(cmd)
    if cmd[:3] == ("systemctl", "is-active", "chrony.service"):
        return types.SimpleNamespace(stdout="active\n", stderr="", returncode=0)
    return types.SimpleNamespace(stdout="", stderr="", returncode=0)


with mock.patch.object(hub_control, "run", fake_run), mock.patch.object(sd, "clock_behind", lambda: behind):
    said = hub_control._security_clock_makestep()
check(
    "the action steps chrony, not the hub's clock by hand",
    ("chronyc", "makestep") in calls and msg.is_message(said) and said["id"] == "control.security.clock-stepped",
    calls,
)

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
