# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""mPWRD-OS's wifisync switched from the Network page (hub_control's "wifisync"), with systemctl stood in for: off and
on, the image's own state kept from before the first change, undo back to it, and nothing done where it isn't installed.
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
from irate_box.root import hub_control as hc  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond

state = {"unit": "enabled"}
calls = []
def fake_run(cmd, **kw):
    calls.append(cmd)
    if cmd[:1] == ["systemctl"] and cmd[1] in ("enable", "disable"):
        state["unit"] = "enabled" if cmd[1] == "enable" else "disabled"
    return mock.Mock(returncode=0, stdout="", stderr="")
def rec():
    return json.loads((hc.CONTROL / "wifisync.json").read_text())
with mock.patch.object(hc.netinv, "enabled", lambda unit: state["unit"]), mock.patch.object(hc.subprocess, "run", fake_run), \
        mock.patch.object(hc.netinv, "scan", lambda: {}), mock.patch.object(hc.netinv, "write", lambda *a: None):
    msg = hc.wifisync({"do": "off"})
    check("off: disabled and stopped, the image's state kept", calls[-1] == ["systemctl", "disable", "--now", "wifisync.service"]
          and state["unit"] == "disabled" and rec().get("was") == "enabled" and msg["id"] == "control.wifisync.off", (calls, rec(), msg))
    hc.wifisync({"do": "on"})
    hc.wifisync({"do": "off"})
    check("  on and off again: the image's state still the first one", rec().get("was") == "enabled" and rec().get("now") == "disabled", rec())
    hc.wifisync({"do": "undo"})
    check("undo: back as the image had it, the record cleared", state["unit"] == "enabled" and rec() == {}, (state, rec()))
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
    state["unit"] = None
    n = len(calls)
    try:
        hc.wifisync({"do": "off"})
        check("not installed: refused, nothing run", False)
    except ValueError as exc:
        check("not installed: refused, nothing run", len(calls) == n and exc.message["id"] == "control.wifisync.missing", str(exc))
check("the root helper knows it", "wifisync" in hc.ACTIONS if hasattr(hc, "ACTIONS") else '"wifisync": wifisync' in (REPO / "irate_box/root/hub_control.py").read_text())
import shutil  # noqa: E402
shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
