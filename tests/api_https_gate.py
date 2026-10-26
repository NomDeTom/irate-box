# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What only an administrator on HTTPS may ask the hub to have root do (hub/transport.py, review 2026-10-23 F1), against
a hub it starts with a front secret: the update source, installing an update, the signing level, an add-on, a local
add-on refused over plain HTTP, with nothing queued for root; the same over HTTPS queued and marked "https"; another
request over plain HTTP still queued, marked "http"; reading still answered. A local process claiming to be the
front (X-Forwarded-Proto: https without the front's second secret, review V2 L10) is not believed, and the security
doctor says whether the hub believes it. And every security-policy action of root's (hub_control.POLICY_ACTIONS) but
admin-gate is on the list, read from the source.
python3 tests/api_https_gate.py"""

import ast
import http.client
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import transport

state = Path(tempfile.mkdtemp(prefix="https-gate-"))
port = 20000 + int.from_bytes(os.urandom(2), "big") % 20000
SECRET = "e" * 64
VIA = "f" * 64
env = dict(
    os.environ,
    HUB_FRONT_SECRET=SECRET,
    HUB_VIA_SECRET=VIA,
    HUB_STATE_DIR=str(state),
    HUB_ETC_DIR=str(state),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


# root's policy actions, from its source: each but admin-gate (it takes nothing from the request) needs HTTPS.
tree = ast.parse((REPO / "irate_box/root/hub_control.py").read_text())
policy = next(
    {e.value for e in node.value.args[0].elts}
    for node in ast.walk(tree)
    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "POLICY_ACTIONS"
)
check(
    "every root security-policy action but admin-gate and a guest's share-allow needs HTTPS; installing an update and add-ons too",
    policy - {"admin-gate", "share-allow"} <= transport.HTTPS_ONLY
    and {"update-install", "addon"} <= transport.HTTPS_ONLY,
    sorted(policy - {"admin-gate", "share-allow"} - transport.HTTPS_ONLY),
)

hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
requests = state / "control" / "requests"
try:
    for _ in range(300):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)

    def go(method, path, proto, body=None, via=VIA):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        h = {
            "Host": f"127.0.0.1:{port}",
            "X-Irate-Front": SECRET,
            "X-Irate-Via": via,
            "X-Irate-Admin": "1",
            "X-Forwarded-For": "192.168.4.20",
            "X-Forwarded-Proto": proto,
            "Content-Type": "application/json",
        }
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None, headers=h)
        r = c.getresponse()
        data = r.read()
        try:
            return r.status, json.loads(data or b"null")
        except ValueError:
            return r.status, None

    def queued():
        out = [json.loads(p.read_text()) for p in sorted(requests.glob("*.json"))] if requests.is_dir() else []
        for p in requests.glob("*.json") if requests.is_dir() else ():
            p.unlink()
        return out

    gated = [
        (
            "/admin/update",
            {"action": "set-source", "repo": "https://github.com/x/irate-box", "branch": "main", "confirmed": True},
        ),
        ("/admin/update", {"action": "install"}),
        ("/admin/update", {"action": "signing", "level": "off", "signers": ""}),
        ("/admin/local-addons", {"action": "add", "id": "x"}),
    ]
    for path, body in gated:
        status, said = go("POST", path, "http", body)
        check(
            f"{path} {body['action']} over plain HTTP: refused, said, nothing queued",
            status == 403 and (said or {}).get("message", {}).get("id") == "hub.https-only" and not queued(),
            (status, said),
        )
    for path, body in gated[:3]:
        status, _ = go("POST", path, "https", body)
        q = queued()
        check(
            f"{path} {body['action']} over HTTPS: queued for root, marked https",
            status == 202 and len(q) == 1 and q[0].get("over") == "https",
            (status, q),
        )
    status, _ = go("POST", "/admin/health", "http", {"action": "scan"})
    q = queued()
    check(
        "another request for root over plain HTTP (the box doctor's scan): queued, marked http",
        status == 202 and len(q) == 1 and q[0].get("over") == "http" and q[0].get("action") == "health-scan",
        (status, q),
    )
    status, _ = go("GET", "/admin/update", "http")
    check("reading over plain HTTP still answered", status == 200, status)
    for via, which in (("", "without the front's second secret"), ("0" * 64, "with a wrong one")):
        status, said = go("POST", gated[0][0], "https", gated[0][1], via=via)
        check(
            f"X-Forwarded-Proto: https {which}: taken as plain HTTP, refused, nothing queued",
            status == 403 and (said or {}).get("message", {}).get("id") == "hub.https-only" and not queued(),
            (status, said),
        )
        status, said = go("GET", "/status", "https", via=via)
        check(f"X-Forwarded-For {which}: /status says not proxied", status == 200 and said["proxied"] is False, said)
    status, said = go("GET", "/status", "https")
    check("X-Forwarded-For with the secret: /status says proxied", status == 200 and said["proxied"] is True, said)
    from irate_box.root import secdoctor

    secdoctor._hub_port = lambda: port
    secdoctor.ETC = state
    seen = {f["id"]: f["status"] for f in secdoctor.step_admin_gate({})}
    check("the security doctor: forwarded headers ignored without the secret", seen.get("front-proof") == "ok", seen)
    # The secret in the system log (Caddy's packaged "run --environ" printed it): a problem; not there, nothing.
    # The fixture as text of its own ("e" * 64 is SECRET, "f" * 64 VIA): a test's stand-in, not a stored secret.
    (state / "front-secret.env").write_text("HUB_FRONT_SECRET=" + "e" * 64 + "\nHUB_VIA_SECRET=" + "f" * 64 + "\n")
    (state / "front-secret.env").chmod(0o600)
    journal = {"text": ""}
    real_cmd = secdoctor._cmd
    secdoctor._cmd = lambda *argv, **kw: (
        subprocess.CompletedProcess(argv, 0, journal["text"], "") if argv[0] == "journalctl" else real_cmd(*argv, **kw)
    )
    seen = {f["id"]: f["status"] for f in secdoctor.step_admin_gate({})}
    check("  the secret not in the system log: no finding", "admin-secret-logged" not in seen, seen)
    journal["text"] = f"HOME=/var/lib/caddy\nHUB_FRONT_SECRET={SECRET}\n"
    seen = {f["id"]: f["status"] for f in secdoctor.step_admin_gate({})}
    check("  the secret in the system log: a problem", seen.get("admin-secret-logged") == "problem", seen)
    secdoctor._cmd = real_cmd
    (state / "front-secret.env").unlink()
finally:
    hub.terminate()
    hub.wait(10)

# A hub without the second secret (as before the installer made it): the doctor says so.
port += 1
bare = subprocess.Popen(
    [str(REPO / "irate-box"), "server"],
    env={k: v for k, v in env.items() if k != "HUB_VIA_SECRET"} | {"PORT": str(port)},
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
try:
    for _ in range(300):
        try:
            http.client.HTTPConnection("127.0.0.1", port, timeout=1).request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)
    seen = {f["id"]: f["status"] for f in secdoctor.step_admin_gate({})}
    check(
        "  and a problem when the hub believes them without it",
        seen.get("front-proof") == "problem",
        seen,
    )
finally:
    bare.terminate()
    bare.wait(10)

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
