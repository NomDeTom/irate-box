# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""HTTPS beside HTTP from the first install (tls initialise with HUB_TLS_DEFAULT, set by install.sh): a box with no
certificate yet gets its own CA and certificate, both transports, the administration pages open over either, and its
TLS includes written for the installer's web-server check, with no web server touched here. Running the installer
again makes nothing new; --no-https (HUB_TLS_DEFAULT=off) and a box that already chose HTTP only are left on HTTP.
Each case is a fresh box in a process of its own, with the real openssl. python3 tests/sim_tls_default.py"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# One installer run on a box: initialise, then what it left. The box's addresses and name stood in; the web server
# must not be reloaded (the installer checks and activates its configuration itself).
RUN = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from irate_box.root import safeio, tls
safeio.write = lambda path, text, mode=0o644: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_text(text))
tls.addresses = lambda: [{"iface": "eth0", "address": "192.168.1.26", "network": "192.168.1.0/24"}]
tls.hostname = lambda: "picompute"
reloaded = []
tls._nginx_check_and_reload = lambda: (reloaded.append(1), (True, ""))[1]
tls.initialise()
rec = tls._record()
print(json.dumps({"record": rec, "settings": tls.settings(), "reloaded": len(reloaded),
                  "front": sorted(p.name for p in tls.FRONT.glob("*")), "chain": tls.CHAIN.exists(), "key": tls.KEY.exists(),
                  "key_mode": oct(tls.KEY.stat().st_mode & 0o777) if tls.KEY.exists() else None,
                  "ca_public": (tls.PUBLIC / "ca.crt").exists()}))
"""


def install(box, default):
    env = dict(os.environ, HUB_ETC_DIR=str(box / "etc"), HUB_STATE_DIR=str(box / "state"), HUB_WEB_SERVER="nginx")
    env.pop("HUB_TLS_DEFAULT", None)
    if default is not None:
        env["HUB_TLS_DEFAULT"] = default
    out = subprocess.run([sys.executable, "-c", RUN, str(REPO)], env=env, capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1]), out.stderr


T = Path(tempfile.mkdtemp(prefix="tls-default-"))

# The first install, as install.sh runs it by default.
got, _ = install(T / "fresh", "both")
rec = got["record"]
check(
    "first install: the box's own CA and certificate, for its address and the hotspot's",
    got["chain"]
    and got["key"]
    and rec.get("ca")
    and "192.168.1.26" in rec["cert"]["addresses"]
    and "192.168.4.1" in rec["cert"]["addresses"],
    rec,
)
check(
    "  HTTP and HTTPS both on, the administration pages over HTTPS only (review 2026-10-23, F1)",
    got["settings"] == {"transport": "both", "admin_http": "block"}
    and rec.get("on") is True
    and rec.get("admin_only") is True,
    got["settings"],
)
check(
    "  the TLS includes written for the installer's check",
    any(n.startswith("nginx-") and n.endswith(".conf") for n in got["front"]),
    got["front"],
)
check("  no web server reloaded here (the installer activates it)", got["reloaded"] == 0, got["reloaded"])
check(
    "  the key private, the CA published for /certificate",
    got["key_mode"] == "0o640" and got["ca_public"],
    got["key_mode"],
)

# The installer again (an update): the same CA, nothing made anew.
again, _ = install(T / "fresh", "both")
check(
    "installer run again: the same CA and certificate, still both",
    again["record"]["ca"]["fingerprint"] == rec["ca"]["fingerprint"]
    and again["record"]["cert"]["made"] == rec["cert"]["made"]
    and again["settings"]["transport"] == "both",
    again["record"].get("ca"),
)

# --no-https: plain HTTP, no certificate.
off, _ = install(T / "plain", "off")
check(
    "--no-https: HTTP only, no certificate made, no TLS include",
    not off["chain"]
    and not off["record"]
    and off["settings"]["transport"] == "http"
    and not any(n.endswith(".conf") and "ssl" in n for n in off["front"]),
    off,
)

# An older installer (no HUB_TLS_DEFAULT): as before, HTTP only.
old, _ = install(T / "older", None)
check(
    "an installer without the setting: HTTP only, as before",
    not old["chain"] and old["settings"]["transport"] == "http",
    old,
)

# A box whose owner chose HTTP only after making a certificate: left as chosen.
box = T / "chose"
(box / "etc" / "tls").mkdir(parents=True)
(box / "etc" / "tls" / "made.json").write_text(json.dumps({"transport": "http", "admin_http": "allow", "on": False}))
chose, _ = install(box, "both")
check(
    "a box that chose HTTP only: left on HTTP, no certificate made",
    not chose["chain"] and chose["settings"]["transport"] == "http",
    chose,
)


# A box set up before the administration pages became HTTPS only (both transports, administration over either):
# moved to HTTPS only at its next install, and said; with --allow-admin-http recorded, left as it was.
def older_box(name, options=""):
    box = T / name
    first, _ = install(box, "both")  # its CA and certificate, as an older installer made them
    rec = dict(first["record"], admin_http="allow", admin_only=False)
    (box / "etc" / "tls" / "made.json").write_text(json.dumps(rec))
    if options:
        (box / "etc" / "install-options").write_text(options)
    return box


moved, said = install(older_box("before-https-only"), "both")
check(
    "a box from before HTTPS-only administration: moved to it at its next install, and said",
    moved["settings"] == {"transport": "both", "admin_http": "block"}
    and moved["record"]["admin_only"] is True
    and "--allow-admin-http" in said,
    (moved["settings"], said),
)
kept, _ = install(older_box("chose-plain-admin", "--repo\nx\n--allow-admin-http\n"), "both")
check(
    "  one installed with --allow-admin-http: left as the owner set it",
    kept["settings"] == {"transport": "both", "admin_http": "allow"},
    kept["settings"],
)
box = T / "no-cert"
(box / "etc" / "tls").mkdir(parents=True)
(box / "etc" / "tls" / "made.json").write_text(json.dumps({"transport": "both", "admin_http": "allow", "on": True}))
nocert, said = install(box, "both")
check(
    "  one with no certificate: left as it was (no way in over HTTPS yet), and said",
    nocert["settings"]["admin_http"] == "allow" and "no certificate" in said,
    (nocert["settings"], said),
)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
