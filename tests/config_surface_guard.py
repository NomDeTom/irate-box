# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The shipped web server configs stay on the attack surface the box uses: no HTTP/2, HTTP/3, QUIC, resolver, mail or
stream, and Caddy's own servers on HTTP/1.1 only. A feature turned on here must be a deliberate act, with the plan that
asked for the guard updated too. python3 tests/config_surface_guard.py"""

import os
import re
import sys
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.root import tls

PLAN = "/home/user/notes-sync/irate-box/plans/cve-hardening-plan.md"
WHY = f"The feature widens the attack surface and must be turned on deliberately, with {PLAN} updated."

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# nginx directives that open a protocol or a resolver the box does not serve.
NGINX = {
    "HTTP/2": re.compile(r"(?m)^\s*http2\b"),
    "HTTP/3": re.compile(r"(?m)^\s*http3\b"),
    "QUIC": re.compile(r"(?m)^\s*quic\b"),
    "resolver": re.compile(r"(?m)^\s*resolver\b"),
    "mail": re.compile(r"(?m)^\s*mail\s*\{"),
    "stream": re.compile(r"(?m)^\s*stream\s*\{"),
    "PCRE JIT": re.compile(r"(?m)^\s*pcre_jit\s+on\b"),
}

configs = {"config/irate-box.nginx": (REPO / "config/irate-box.nginx").read_text()}
with mock.patch.dict(os.environ, {"HUB_WEB_SERVER": "nginx"}):
    configs.update({f"tls.py nginx twin {p.name}": text for p, text in tls._front_files().items()})
with mock.patch.dict(os.environ, {"HUB_WEB_SERVER": "caddy"}):
    configs.update({f"tls.py Caddy twin {p.name}": text for p, text in tls._front_files().items()})

for label, text in configs.items():
    found = [
        f"{label}: {name} at line {text[: m.start()].count(chr(10)) + 1}"
        for name, pattern in NGINX.items()
        if (m := pattern.search(text))
    ]
    check(f"{label}: no widened nginx feature", not found, f"{found}. {WHY}")

caddy = (REPO / "config/Caddyfile").read_text()
protocols = [
    (caddy[: m.start()].count("\n") + 1, m.group(1).split())
    for m in re.finditer(r"(?m)^\s*protocols\s+([^#\n{]+)$", caddy)
]
check(
    "the Caddyfile sets Caddy's servers to HTTP/1.1 only",
    protocols and all(parts == ["h1"] for _, parts in protocols),
    f"protocols at {protocols}; {WHY}",
)
check(
    "  and no Caddy global option opens HTTP/3 or QUIC",
    not re.search(r"(?mi)^\s*(?:http3|quic)\b", caddy),
    WHY,
)

nginx = configs["config/irate-box.nginx"]
check(
    "nginx's version is hidden once, at http level",
    len(re.findall(r"(?m)^server_tokens off;$", nginx)) == 1 and not re.search(r"(?m)^\s+server_tokens off;$", nginx),
    "server_tokens off should be at http level only; server blocks inherit it",
)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
