# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""mPWRD-OS's wifisync switched from the Network page (hub_control's "wifisync"), with systemctl stood in for: off and
on, the image's own state kept from before the first change, undo back to it, and nothing done where it isn't installed.
Then the cases a box can show: disable that leaves the unit enabled (a link it doesn't own) masks it instead, and
Enable or Undo unmasks it; an image that masked it; and the Network page's finding read from enabled and running.
python3 tests/sim_wifisync.py"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="wifisync-"))
os.environ.update(HUB_STATE_DIR=str(T), HUB_ETC_DIR=str(T / "etc"))
(T / "control").mkdir()
sys.path.insert(0, str(REPO))
from irate_box.root import hub_control as hc

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


state = {"unit": "enabled", "running": True, "stuck": False}
calls = []


def fake_run(cmd, **kw):
    """systemctl as far as wifisync needs it. stuck: the image's own link, which disable leaves (is-enabled stays
    enabled, and it starts again at boot)."""
    calls.append(cmd)
    verb = cmd[1] if cmd[:1] == ["systemctl"] else None
    if verb == "enable":
        if state["unit"] == "masked":
            return mock.Mock(returncode=1, stdout="", stderr="Failed to enable unit: Unit file is masked.")
        state.update(unit="enabled", running=True)
    elif verb == "disable":
        state.update(unit="enabled" if state["stuck"] else "disabled", running=False)
    elif verb == "mask":
        state.update(unit="masked", running=False)
    elif verb == "unmask":
        state["unit"] = "enabled" if state["stuck"] else "disabled"
    return mock.Mock(returncode=0, stdout="", stderr="")


def verbs(since):
    return [c[1] for c in calls[since:] if c[:1] == ["systemctl"]]


def rec():
    return json.loads((hc.CONTROL / "wifisync.json").read_text())


with (
    mock.patch.object(hc.netinv, "enabled", lambda unit: state["unit"]),
    mock.patch.object(hc.netinv, "active", lambda unit: state["running"]),
    mock.patch.object(hc.subprocess, "run", fake_run),
    mock.patch.object(hc.netinv, "scan", lambda: {}),
    mock.patch.object(hc.netinv, "write", lambda *a: None),
):
    msg = hc.wifisync({"do": "off"})
    check(
        "off: disabled and stopped, the image's state kept",
        calls[-1] == ["systemctl", "disable", "--now", "wifisync.service"]
        and state["unit"] == "disabled"
        and rec().get("was") == "enabled"
        and msg["id"] == "control.wifisync.off",
        (calls, rec(), msg),
    )
    hc.wifisync({"do": "on"})
    hc.wifisync({"do": "off"})
    check(
        "  on and off again: the image's state still the first one",
        rec().get("was") == "enabled" and rec().get("now") == "disabled",
        rec(),
    )
    hc.wifisync({"do": "undo"})
    check(
        "undo: back as the image had it, the record cleared", state["unit"] == "enabled" and rec() == {}, (state, rec())
    )
    try:
        hc.wifisync({"do": "undo"})
        check("  undo with nothing changed: refused", False)
    except ValueError as exc:
        check("  undo with nothing changed: refused", exc.message["id"] == "control.wifisync.nothing-to-undo", str(exc))
    for bad in ({"do": "maybe"}, {}):
        try:
            hc.wifisync(bad)
            check(f"refused: {bad}", False)
        except ValueError:
            check(f"refused: {bad}", True)
    # disable leaves the image's own link: masked instead, and the way back unmasks it.
    state.update(unit="enabled", running=True, stuck=True)
    (hc.CONTROL / "wifisync.json").unlink(missing_ok=True)
    n = len(calls)
    msg = hc.wifisync({"do": "off"})
    check(
        "off, a link disable leaves: masked instead, and said so",
        verbs(n) == ["disable", "mask"]
        and state["unit"] == "masked"
        and rec().get("now") == "masked"
        and msg["id"] == "control.wifisync.masked",
        (verbs(n), rec(), msg),
    )
    n = len(calls)
    hc.wifisync({"do": "on"})
    check("  Enable: unmasked, then enabled", verbs(n) == ["unmask", "enable"] and state["unit"] == "enabled", verbs(n))
    hc.wifisync({"do": "off"})
    n = len(calls)
    hc.wifisync({"do": "undo"})
    check(
        "  Undo from masked: unmasked, enabled as the image had it, the record cleared",
        verbs(n) == ["unmask", "enable"] and state["unit"] == "enabled" and rec() == {},
        (verbs(n), state, rec()),
    )
    # The image masked it: Enable unmasks first, and Undo masks it again.
    state.update(unit="masked", running=False, stuck=False)
    n = len(calls)
    hc.wifisync({"do": "on"})
    check(
        "masked by the image: Enable unmasks, then enables",
        verbs(n) == ["unmask", "enable"] and state["unit"] == "enabled" and rec().get("was") == "masked",
        (verbs(n), rec()),
    )
    n = len(calls)
    hc.wifisync({"do": "undo"})
    check("  Undo: masked again, as the image had it", verbs(n) == ["mask"] and state["unit"] == "masked", verbs(n))
    state["unit"] = None
    n = len(calls)
    try:
        hc.wifisync({"do": "off"})
        check("not installed: refused, nothing run", False)
    except ValueError as exc:
        check(
            "not installed: refused, nothing run",
            len(calls) == n and exc.message["id"] == "control.wifisync.missing",
            str(exc),
        )
# The Network page's finding: off (ok, so the page offers Enable) only when neither enabled nor running.
from irate_box.hub import netinv

for unit_state, running, want in (
    ("enabled", True, "warn"),
    ("enabled", False, "warn"),
    ("disabled", True, "warn"),
    ("static", False, "warn"),
    ("disabled", False, "ok"),
    ("masked", False, "ok"),
):
    with (
        mock.patch.object(netinv, "enabled", lambda unit, u=unit_state: u if unit == "wifisync.service" else None),
        mock.patch.object(netinv, "active", lambda unit, r=running: r),
        mock.patch.object(netinv, "mesh_wifi_enabled", lambda: True),
    ):
        found = [f for f in netinv.findings({"radios": [], "stacks": {}}) if f["id"] == "wifisync"]
    check(
        f"the finding: {unit_state}, {'running' if running else 'stopped'} -> {want}",
        len(found) == 1
        and found[0]["status"] == want
        and found[0]["unit_state"] == unit_state
        and found[0]["running"] is running,
        found,
    )
check(
    "the root helper knows it",
    "wifisync" in hc.ACTIONS
    if hasattr(hc, "ACTIONS")
    else '"wifisync": wifisync' in (REPO / "irate_box/root/hub_control.py").read_text(),
)
import shutil

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
