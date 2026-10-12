# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's unit sandboxing: a root unit missing a line is reported; a line a unit goes without by
design (the root helper, the terminal, the audit and crash watch seeing every process) is not, and is listed with
its reason. python3 tests/sim_secdoctor_sandbox.py"""
import os, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="secdoc-sb-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box.root import secdoctor as sd  # noqa: E402

fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond


FULL = {"LoadState": "loaded", "User": "", "ProtectSystem": "strict", "NoNewPrivileges": "yes", "PrivateTmp": "yes",
        "ProtectHome": "yes", "ProtectProc": "invisible"}
BARE = {"LoadState": "loaded", "User": "", "ProtectSystem": "no", "NoNewPrivileges": "no", "PrivateTmp": "no",
        "ProtectHome": "no", "ProtectProc": "default"}

def run(units):
    return {f["id"]: f for f in sd.step_units({"units": units})}

out = run({"irate-box-firewall.service": FULL})
check("a root unit with every line: ok, nothing exempt listed", out["units-root"]["status"] == "ok" and "units-exempt" not in out)
out = run({"irate-box-uplink.service": dict(FULL, ProtectProc="default")})
check("a root unit missing a line: reported", out["units-root"]["status"] == "warn" and "ProtectProc" in out["units-root"]["detail"])
out = run({"ttyd.service": BARE, "irate-box-crashwatch.service": dict(FULL, ProtectProc="default"),
           "irate-box-secdoctor.service": dict(FULL, ProtectProc="default")})
check("the terminal, crash watch and the audit without their exempt lines: ok", out["units-root"]["status"] == "ok", out["units-root"])
check("  each listed with its reason", out["units-exempt"]["status"] == "ok" and "every process" in out["units-exempt"]["detail"]
      and "ttyd without NoNewPrivileges" in out["units-exempt"]["detail"], out.get("units-exempt"))
out = run({"irate-box-control.service": BARE})
check("the root helper: only its exempt lines let off", out["units-root"]["status"] == "warn"
      and "NoNewPrivileges" in out["units-root"]["detail"] and "ProtectSystem" not in out["units-root"]["detail"], out["units-root"])
import shutil  # noqa: E402
shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
