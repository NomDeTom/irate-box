# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Every service unit install.sh writes meets the sandbox baseline. A unit that runs as root, or one that can't take a line, says why below; nothing else is
let off. $HUB_SANDBOX is expanded as install.sh expands it. python3 tests/unit_guard.py"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


BASELINE = ["ProtectSystem=strict", "NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectHome=yes", "PrivateDevices=yes",
            "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectKernelLogs=yes", "ProtectControlGroups=yes",
            "ProtectClock=yes", "RestrictSUIDSGID=yes", "RestrictNamespaces=yes", "RestrictRealtime=yes", "LockPersonality=yes",
            "SystemCallArchitectures=native", "CapabilityBoundingSet=", "RestrictAddressFamilies="]
# Root's units: they act for the owner on the system itself (packages, units, the network, the
# clock), so the baseline would stop them working. Each says what needs root.
ROOT = {
    "irate-box-control.service": "the root helper: installs updates and packages, writes the front's config, starts units",
    "irate-box-uplink.service": "the network watchdog: reconnects, restarts the network, resets the radio",
    "irate-box-crashwatch.service": "crash watch: reads /dev/kmsg, resets a failing radio (USB unbind), restarts the box, sets the hang settings",
    "irate-box-tailscale.service": "applies Tailscale's settings (tailscale up/down)",
    "irate-box-tailscale-boot.service": "starts tailscaled at boot when the owner chose it",
    "ttyd.service": "the owner's root shell behind the admin login, which is its whole point",
    "irate-box-visitors-switch.service": "starts and stops the visitor-counting helper (systemctl) as the owner's switch says",
    "irate-box-secdoctor.service": "the daily security doctor: reads /etc/shadow, sudoers, every unit's properties and root-only folders (read-only, sandboxed otherwise)",
}
# Lines a unprivileged unit may leave out, and why.
EXEMPT = {
    "irate-box-ci.service": {"PrivateDevices=yes": "a build may need a board's serial port later (flashing from the box)",
                             "RestrictAddressFamilies=": "a build's tools reach the network as they choose (offline builds are kept so by IPAddressDeny, not this)",
                             "ProtectKernelLogs=yes": "", "ProtectClock=yes": "", "SystemCallArchitectures=native": "",
                             "CapabilityBoundingSet=": "",
                             "RestrictNamespaces=yes": "user and net only (RestrictNamespaces=user net): an offline build runs in a network namespace of its own", "RestrictSUIDSGID=yes": "",
                             "RestrictRealtime=yes": "", "LockPersonality=yes": "", "ProtectKernelTunables=yes": "",
                             "ProtectKernelModules=yes": "", "ProtectControlGroups=yes": ""},
}
# The builder's exemptions marked "" are not reasons: they are the lines it is still to get, once
# tried with a build. Listed so the test names them; remove each as it is added.

text = (REPO / "install.sh").read_text()
sandbox = re.search(r'^HUB_SANDBOX="(.*?)"$', text, re.S | re.M).group(1)
units = {}
for m in re.finditer(r"cat >/etc/systemd/system/([^ \n]+?\.service) <<'?EOF'?\n(.*?)\nEOF", text, re.S):
    units[m.group(1)] = m.group(2).replace("$HUB_SANDBOX", sandbox)
check("install.sh's service units found", len(units) >= 10, sorted(units))
for name, body in sorted(units.items()):
    lines = [l.strip() for l in body.splitlines()]
    root = not re.search(r"^(User=|DynamicUser=yes)", body, re.M)
    if root:
        check(f"{name}: runs as root, and says why", name in ROOT, "add it to ROOT with what it needs root for, or give it a User=")
        continue
    exempt = EXEMPT.get(name, {})
    missing = [b for b in BASELINE if not any(l.startswith(b) if b.endswith("=") else l == b for l in lines) and b not in exempt]
    check(f"{name}: the sandbox baseline", not missing, missing)
for name in ROOT:
    check(f"{name}: in the root list, and still a root unit", name in units and not re.search(r"^(User=|DynamicUser=yes)", units[name], re.M))
todo = sorted(k for k, v in EXEMPT.get("irate-box-ci.service", {}).items() if not v)
print(f"note: the builder still to get: {', '.join(todo)}" if todo else "note: the builder has the whole baseline")
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
