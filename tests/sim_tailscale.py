# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Remote access (Tailscale): the hub reads Tailscale's own state while it runs (server.tailscale_backend), so a
running tailscaled that was never logged in to a Tailscale network is said as such, not as "running". No tailscale
is run. python3 tests/sim_tailscale.py"""
import json
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.hub import server as S  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def ask(out, rc=0):
    S._tailscale_cache["at"] = -1e9
    return S.tailscale_backend(run=lambda *a, **k: subprocess.CompletedProcess(a, rc, out if isinstance(out, str) else json.dumps(out), ""))
check("never logged in: needs-login, with the box's name", ask({"BackendState": "NeedsLogin", "Self": {"HostName": "lyra"}})
      == {"state": "needs-login", "name": "lyra", "tailnet": None, "ip": None})
check("logged in: connected, its name on the tailnet, the tailnet and its address",
      ask({"BackendState": "Running", "Self": {"DNSName": "lyra.tail1.ts.net.", "HostName": "lyra", "TailscaleIPs": ["100.64.0.5", "fd7a::5"]},
           "CurrentTailnet": {"Name": "owner@"}}) == {"state": "connected", "name": "lyra.tail1.ts.net", "tailnet": "owner@", "ip": "100.64.0.5"})
check("another of its states: said as it says it", ask({"BackendState": "Starting"})["state"] == "starting")
check("not running (the CLI fails), or not its JSON: not known", ask("", rc=1) is None and ask("not json") is None)
S._tailscale_cache["at"] = -1e9
check("no tailscale at all: not known", S.tailscale_backend(run=lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("tailscale"))) is None)
calls = []
S._tailscale_cache["at"] = -1e9
S.tailscale_backend(run=lambda *a, **k: calls.append(1) or subprocess.CompletedProcess(a, 0, '{"BackendState": "NeedsLogin"}', ""))
S.tailscale_backend(run=lambda *a, **k: calls.append(1) or subprocess.CompletedProcess(a, 0, "{}", ""))
check("asked once per cache spell, as the services are", calls == [1])
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
