# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security doctor's unit sandboxing: a root unit missing a line is reported; a line a unit goes without by
design (the root helper's writes across the system, the terminal) is not, and is listed with its reason. The root
units install.sh writes have every other line. python3 tests/sim_secdoctor_sandbox.py"""

import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="secdoc-sb-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(REPO))
from irate_box.finding import message_ids as msg_ids
from irate_box.root import secdoctor as sd

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


FULL = {
    "LoadState": "loaded",
    "User": "",
    "ProtectSystem": "strict",
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
    "ProtectHome": "yes",
    "ProtectProc": "invisible",
}
BARE = {
    "LoadState": "loaded",
    "User": "",
    "ProtectSystem": "no",
    "NoNewPrivileges": "no",
    "PrivateTmp": "no",
    "ProtectHome": "no",
    "ProtectProc": "default",
}


def run(units):
    return {f["id"]: f for f in sd.step_units({"units": units})}


def exempt(f):
    """The exempt lines said on a finding (notes units.exempt.one): (unit, line, the reason's message ID)."""
    return {
        (n["args"]["unit"], n["args"]["line"], n["args"]["reason"]["id"])
        for n in f["notes"]
        if n["id"] == "units.exempt.one"
    }


out = run({"irate-box-firewall.service": FULL})
check(
    "a root unit with every line: ok, nothing exempt listed",
    out["units-root"]["status"] == "ok" and not exempt(out["units-root"]),
    out["units-root"],
)
out = run({"irate-box-uplink.service": dict(FULL, ProtectProc="default")})
check(
    "a root unit missing a line: reported",
    out["units-root"]["status"] == "warn"
    and out["units-root"]["evidence"] == ["irate-box-uplink: ProtectProc=invisible"]
    and "units.root.weak" in msg_ids(out["units-root"]),
    out["units-root"],
)
out = run({"ttyd.service": BARE})
check("the terminal without its exempt lines: ok", out["units-root"]["status"] == "ok", out["units-root"])
check(
    "  listed with its reason (notes on units-root)",
    "units-exempt" not in out and ("ttyd", "NoNewPrivileges", "units.exempt.ttyd") in exempt(out["units-root"]),
    exempt(out["units-root"]),
)
# Root keeps CAP_SYS_PTRACE under ProtectProc=invisible (seen on the Lyra: every process, and another account's
# command line), so the audit and crash watch are held to it like any root unit.
out = run(
    {
        "irate-box-crashwatch.service": dict(FULL, ProtectProc="default"),
        "irate-box-secdoctor.service": dict(FULL, ProtectProc="default"),
    }
)
check(
    "crash watch and the audit without ProtectProc: reported",
    out["units-root"]["status"] == "warn"
    and sorted(out["units-root"]["evidence"])
    == ["irate-box-crashwatch: ProtectProc=invisible", "irate-box-secdoctor: ProtectProc=invisible"],
    out["units-root"],
)
out = run({"irate-box-control.service": BARE})
check(
    "the root helper: only its exempt lines let off",
    out["units-root"]["status"] == "warn"
    and any("NoNewPrivileges" in e for e in out["units-root"]["evidence"])
    and not any("ProtectSystem" in e for e in out["units-root"]["evidence"])
    and {(u, ln) for u, ln, _ in exempt(out["units-root"])}
    == {("irate-box-control", "ProtectSystem=strict"), ("irate-box-control", "ProtectHome")},
    out["units-root"],
)

# The root units as install.sh writes them, its shared sandbox expanded: the doctor finds no line missing.
import re

inst = (REPO / "install.sh").read_text()
shared = {name: body for name, body in re.findall(r'^([A-Z_]+_SANDBOX)="(.*?)"$', inst, re.S | re.M)}
units = {}
for name, body in re.findall(
    r"^\s*cat >/etc/systemd/system/([\w.@-]+\.service) <<'?EOF'?\n(.*?)^\s*EOF$", inst, re.S | re.M
):
    for var, text in shared.items():
        body = body.replace(f"${var}", text)
    props = dict(line.strip().split("=", 1) for line in body.splitlines() if re.match(r"^\s*[A-Za-z]+=", line))
    if props.get("User", "root") in ("", "root") and "DynamicUser" not in props:
        units[name] = dict(props, LoadState="loaded", User="")
out = run(units)
check(
    f"install.sh's root units ({len(units)}): no sandbox line missing",
    len(units) >= 5 and "irate-box-control.service" in units and out["units-root"]["status"] == "ok",
    out["units-root"].get("evidence"),
)

# Who can see which processes: the box's /proc as PID 1 has it. Under ProtectProc the unit's own /proc/mounts always
# says hidepid (seen on the Lyra), so it is not the box's.
proc = T / "proc"
(proc / "1").mkdir(parents=True)
(proc / "mounts").write_text("proc /proc proc rw,nosuid,nodev,noexec,relatime,hidepid=invisible 0 0\n")
sd.PROC = proc
for box, want in (("rw,nosuid,nodev,noexec,relatime", "warn"), ("rw,nosuid,nodev,noexec,relatime,hidepid=2", "ok")):
    (proc / "1" / "mounts").write_text(f"proc /proc proc {box} 0 0\n")
    f = next(f for f in sd.step_cmdlines({}) if f["id"] == "cmdlines-proc")
    check(
        f"process visibility read from PID 1's mounts, not the unit's own: {box.rpartition(',')[2]} is {want}",
        f["status"] == want,
        f,
    )
# Builds off the hub on loopback: install.sh's nft table counts, since IPAddressDeny does nothing on vendor kernels.
ci = {"irate-box-ci.service": {"LoadState": "loaded", "User": "hubci", "IPAddressDeny": "", "PrivateNetwork": "no"}}
sd._hub_ask = lambda *a, **k: 403
sd._ci_nft = lambda: None
check(
    "builds can reach the hub on loopback (no rule loaded): a warning", run(ci)["units-ci-loopback"]["status"] == "warn"
)
sd._ci_nft = lambda: 'table inet irate_box_ci {\n\tchain out {\n\t\tmeta skuid "hubci" oif "lo" drop\n\t}\n}\n'
f = run(ci)["units-ci-loopback"]
check(
    "  with the nft rule loaded: ok, said so in the evidence",
    f["status"] == "ok" and f["evidence"] == ["nft: inet irate_box_ci (hubci to lo: DNS only)"],
    f,
)
inst_ci = (REPO / "install.sh").read_text()
check(
    "  install.sh writes the rule (hubci to lo dropped, DNS to the stub kept) and loads it before each build",
    'meta skuid "hubci" oif "lo" ip daddr 127.0.0.53 meta l4proto { tcp, udp } th dport 53 accept' in inst_ci
    and 'meta skuid "hubci" oif "lo" drop' in inst_ci
    and "ExecStartPre=-+/usr/sbin/nft -f $ETC/ci-loopback.nft" in inst_ci,
)
from irate_box.root import secdoctor_xref as xr

check(
    "  the doctor's finding joins the Security page's fix (proc-hidden), and is no longer information only",
    xr.about("doctor", "cmdlines-proc") == {"kind": "setting", "key": "proc-hidden"}
    and xr.about("security-page", "proc-hidden") == {"kind": "setting", "key": "proc-hidden"}
    and xr.info("doctor", "cmdlines-proc") is None,
)
import shutil

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
