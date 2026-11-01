# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""HTTPS for the box, offline with the real
openssl: the CA and its name constraints (a certificate it signs for any other name or address
fails openssl verify), the server certificate, renewal at two-thirds of its life and when the box
gets a new address, an address outside the CA's subnet said, the keys private.
python3 tests/sim_tls.py"""

import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="tls-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.msg import Refused
from irate_box.root import safeio, tls

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


safeio.write = lambda path, text, mode=0o644: (
    Path(path).parent.mkdir(parents=True, exist_ok=True),
    Path(path).write_text(text),
)
ADDRS = [
    {"iface": "wlan0", "address": "192.168.1.181", "network": "192.168.1.0/24"},
    {"iface": "wlan1", "address": "192.168.4.1", "network": "192.168.4.0/24"},
    {"iface": "tailscale0", "address": "100.101.102.103", "network": "100.64.0.0/10"},
]
tls.addresses = lambda: list(ADDRS)
reloads = []
nginx_ok = {"ok": True}
tls._nginx_check_and_reload = lambda: (
    reloads.append(sorted(p.name for p in tls.FRONT.glob("*.conf"))),
    (nginx_ok["ok"], "stood in"),
)[1]
tls.hostname = lambda: "lyra"
tls.initialise()
check(
    "installer listener ports are persisted for later helper invocations",
    json.loads((tls.TLS / "ports.json").read_text())["http"] == tls.PLAIN_PORTS,
)
rec = tls.make_ca()
check(
    "the CA: its names, and the networks it may vouch for (the hotspot, the LAN's subnet; not Tailscale's)",
    rec["ca"]["names"] == ["irate.home.arpa", ".irate.home.arpa", "lyra.local"]
    and rec["ca"]["networks"] == ["192.168.4.1/32", "192.168.1.0/24"],
    rec,
)
text = tls.openssl("x509", "-in", str(tls.CA_CERT), "-noout", "-text")
check(
    "  constrained, critical, a CA of its own and no deeper",
    "X509v3 Name Constraints: critical" in text
    and "CA:TRUE, pathlen:0" in text
    and "Permitted:" in text
    and "DNS:irate.home.arpa" in text
    and "IP:192.168.1.0/255.255.255.0" in text,
    text[-600:],
)
check(
    "  the CA's key root's only (0700 folder, 0600 file)",
    stat.S_IMODE(tls.CA_DIR.stat().st_mode) == 0o700 and stat.S_IMODE(tls.CA_KEY.stat().st_mode) == 0o600,
)
leaf = T / "leaf.crt"
leaf.write_text(tls.CHAIN.read_text().split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n")
v = subprocess.run(
    ["openssl", "verify", "-CAfile", str(tls.CA_CERT), str(leaf)],
    capture_output=True,
    text=True,
)
check(
    "the server certificate verifies against the CA",
    v.returncode == 0,
    v.stdout + v.stderr,
)
san = tls.openssl("x509", "-in", str(leaf), "-noout", "-ext", "subjectAltName")
check(
    "  for the box's names and its addresses inside the constraints",
    all(
        x in san
        for x in (
            "DNS:irate.home.arpa",
            "DNS:lyra.local",
            "IP Address:192.168.4.1",
            "IP Address:192.168.1.181",
        )
    )
    and "100.101.102.103" not in san,
    san,
)
days = (rec_c := json.loads(tls.RECORD.read_text())["cert"])["expires"] - rec_c["made"]
check(
    "  397 days, the key readable by the front, not by all",
    days == 397 * 86400 and stat.S_IMODE(tls.KEY.stat().st_mode) == 0o640,
)


def rogue(name_ext):
    d = Path(tempfile.mkdtemp(dir=T))
    tls.openssl(
        "genpkey",
        "-algorithm",
        "EC",
        "-pkeyopt",
        "ec_paramgen_curve:P-256",
        "-out",
        str(d / "k"),
    )
    tls.openssl(
        "req",
        "-new",
        "-key",
        str(d / "k"),
        "-out",
        str(d / "c.csr"),
        "-subj",
        "/CN=rogue",
    )
    (d / "e").write_text(f"subjectAltName={name_ext}\n")
    tls.openssl(
        "x509",
        "-req",
        "-in",
        str(d / "c.csr"),
        "-CA",
        str(tls.CA_CERT),
        "-CAkey",
        str(tls.CA_KEY),
        "-CAcreateserial",
        "-out",
        str(d / "c.crt"),
        "-days",
        "30",
        "-extfile",
        str(d / "e"),
    )
    r = subprocess.run(
        ["openssl", "verify", "-CAfile", str(tls.CA_CERT), str(d / "c.crt")],
        capture_output=True,
        text=True,
    )
    return r.returncode, r.stdout + r.stderr


for ext, why in (
    ("DNS:www.example.com", "another site's name"),
    ("DNS:evil.local", "another .local name"),
    ("IP:192.168.2.1", "an address outside the subnet"),
    ("IP:8.8.8.8", "a public address"),
):
    rc, out = rogue(ext)
    check(
        f"the CA cannot vouch for {why}, even with its own key",
        rc != 0 and "subtree" in out,
        out,
    )
rc, _ = rogue("DNS:notes.irate.home.arpa")
check("  but can for a name under the box's (names for the origins, later)", rc == 0)
check("fresh: nothing due", tls.due() is None)
now = time.time()
check(
    "two-thirds of its life gone: due",
    tls.due(now + 265 * 86400) == "two-thirds of its life gone" and tls.due(now + 260 * 86400) is None,
)
ADDRS[0] = {"iface": "wlan0", "address": "192.168.1.77", "network": "192.168.1.0/24"}
check(
    "a new address in the subnet: due, and re-made to cover it",
    "192.168.1.77" in (tls.due() or "")
    and (said := tls.renew())["id"] == "https.renew.address"
    and said["args"]["addresses"] == ["192.168.1.77"]
    and "192.168.1.77" in json.loads(tls.RECORD.read_text())["cert"]["addresses"],
)
ADDRS[0] = {"iface": "wlan0", "address": "10.0.0.5", "network": "10.0.0.0/24"}
st = tls.status()
check(
    "moved to another subnet: said (a new CA is needed), not re-made under a CA that can't vouch for it",
    st["outside"] == ["10.0.0.5"] and tls.due() is None,
    st,
)
pub = json.loads((tls.PUBLIC / "status.json").read_text())
check(
    "the hub's copy: the status and the CA's public certificate, never a key",
    pub["set_up"]
    and (tls.PUBLIC / "ca.crt").read_text() == tls.CA_CERT.read_text()
    and not any("PRIVATE KEY" in f.read_text() for f in tls.PUBLIC.iterdir()),
)
# The front: making the CA turned HTTPS on; each server block's TLS
# twin written, nginx checked and reloaded; off takes them away; a refused config is undone.
names = sorted(p.name for p in tls.FRONT.glob("*.conf"))
check(
    "making the CA turned HTTPS on: each origin's twin written, nginx reloaded",
    names
    == [
        "nginx-addons.conf",
        "nginx-admin-site.conf",
        "nginx-draw.conf",
        "nginx-git.conf",
        "nginx-main.conf",
        "nginx-mermaid.conf",
        "nginx-notebook.conf",
        "nginx-notes.conf",
        "nginx-sync.conf",
        "nginx-wiki.conf",
    ]
    and reloads
    and reloads[0] == names
    and tls.status()["on"],
    (names, reloads),
)
main = (tls.FRONT / "nginx-main.conf").read_text()
check(
    "  443 for the hub, the certificate and key, TLS 1.2 and 1.3 only",
    "listen 443 ssl default_server;" in main
    and f"ssl_certificate {tls.CHAIN};" in main
    and f"ssl_certificate_key {tls.KEY};" in main
    and "ssl_protocols TLSv1.2 TLSv1.3;" in main
    and "listen 8491 ssl;" in (tls.FRONT / "nginx-notes.conf").read_text(),
)
# A box upgraded from before the notebook TLS twin existed has its HTTPS setting and
# certificate, but lacks that include.  Installation must bring every TLS listener forward.
(tls.FRONT / "nginx-notebook.conf").unlink()
(tls.TLS / "ports.json").unlink()
tls.initialise()
check(
    "installer restores a missing TLS origin include on upgrade",
    "listen 8494 ssl;" in (tls.FRONT / "nginx-notebook.conf").read_text()
    and json.loads((tls.TLS / "ports.json").read_text())["https"] == tls.TWINS,
)
# Without its certificate the includes would point at nothing and fail the installer's check.
(tls.FRONT / "nginx-notebook.conf").unlink()
chain_text = tls.CHAIN.read_text()
tls.CHAIN.unlink()
tls.initialise()
check(
    "installer leaves TLS includes alone when the certificate is missing",
    not (tls.FRONT / "nginx-notebook.conf").exists(),
)
tls.CHAIN.write_text(chain_text)
tls.initialise()
reloads.clear()
ADDRS[0] = {"iface": "wlan0", "address": "192.168.1.90", "network": "192.168.1.0/24"}
tls.renew()
check(
    "a renewed certificate: nginx reloaded to read it",
    reloads and "nginx-main.conf" in reloads[0],
)
# Without --allow-admin-http (install-options), turning HTTPS off or opening the administration pages over plain HTTP
# is refused: either would send an administrator's session in clear (review 2026-10-23, F1).
(tls.ETC / "install-options").unlink(missing_ok=True)
refusals = []
for args in (("http", "allow"), ("both", "allow"), ("both", "warning")):
    try:
        tls.configure(*args)
        refusals.append(None)
    except Refused as exc:
        refusals.append(exc.message["id"])
check(
    "no --allow-admin-http: HTTPS off and plain HTTP for the administration pages refused, and said",
    refusals == ["https.settings.off-install", "https.settings.admin-http-install", "https.settings.admin-http-install"]
    and tls.status()["on"],
    refusals,
)
(tls.ETC / "install-options").write_text("--repo\nx\n--allow-admin-http\n")
print(tls.front(False))
check(
    "off: the twins gone, nginx reloaded, the CA and certificate kept",
    not list(tls.FRONT.glob("*.conf")) and tls.CA_CERT.exists() and tls.CHAIN.exists() and not tls.status()["on"],
)
nginx_ok["ok"] = False
try:
    tls.front(True)
    check("nginx refusing the twins: undone, and said", False)
except Refused as exc:
    check(
        "nginx refusing the twins: undone, and said",
        not list(tls.FRONT.glob("*.conf")) and exc.message["id"] == "https.settings.refused" and not tls.status()["on"],
        str(exc),
    )
nginx_ok["ok"] = True
tls.configure("https", "block")
tls.make_ca()
check(
    "replacing the CA preserves restrictive transport and administrator policy",
    tls.settings() == {"transport": "https", "admin_http": "block"},
)
tls.configure("both", "allow")
# Under Caddy: a site of its own per twin, importing the plain site's snippet (Caddy refuses TLS on a
# block whose other port is plain); the admin redirect by a protocol matcher. With $CADDY naming
# Caddy binaries (2.6, Debian's, and a newer one), the rendered Caddyfile is validated with each.
tls.front(False)
os.environ["HUB_WEB_SERVER"] = "caddy"
tls.front(True)
sites = sorted(p.name for p in tls.FRONT.glob("caddy-site-*.caddy"))
main_site = (tls.FRONT / "caddy-site-main.caddy").read_text()
check(
    "under Caddy: a twin per origin (the hub, add-ons, notes, books, cgit, administration, Excalidraw, Mermaid, Syncthing), each importing its snippet, with the certificate",
    sites
    == [
        "caddy-site-addons.caddy",
        "caddy-site-admin.caddy",
        "caddy-site-draw.caddy",
        "caddy-site-git.caddy",
        "caddy-site-main.caddy",
        "caddy-site-mermaid.caddy",
        "caddy-site-notes.caddy",
        "caddy-site-sync.caddy",
        "caddy-site-wiki.caddy",
    ]
    and all(
        f"import irate_box_{n}" in (tls.FRONT / f"caddy-site-{n}.caddy").read_text()
        for n in ("notes", "wiki", "git", "admin", "draw", "mermaid", "sync")
    )
    and "https://:443 {" in main_site
    and f"tls {tls.CHAIN} {tls.KEY}" in main_site
    and "import irate_box_hub" in main_site
    and "import irate_box_addons" in (tls.FRONT / "caddy-site-addons.caddy").read_text()
    and not list(tls.FRONT.glob("nginx-*.conf")),
    sites,
)
tls.admin_only(True)
check(
    "  admin over HTTPS only, by a protocol matcher",
    "protocol http" in (tls.FRONT / "caddy-policy-main.inc").read_text()
    and "https://{host}:443{uri}" in (tls.FRONT / "caddy-policy-main.inc").read_text(),
)
caddies = [c for c in os.environ.get("CADDY", "").split(":") if c]
cf = (
    (REPO / "config" / "Caddyfile")
    .read_text()
    .replace("$2a$14$REPLACE_ME_WITH_CADDY_HASH_PASSWORD_OUTPUT", "$2a$14$" + "a" * 53)
)
for c in caddies:
    ver = subprocess.run([c, "version"], capture_output=True, text=True).stdout
    text = cf.replace("basic_auth", "basicauth") if ver.lstrip("v").startswith("2.6") else cf
    (T / "Caddyfile").write_text(text)
    for state, files in (("with HTTPS on", True), ("with HTTPS off", False)):
        if not files:
            for f in tls.FRONT.glob("caddy-*.caddy"):
                f.unlink()
        r = subprocess.run(
            [c, "validate", "--adapter", "caddyfile", "--config", str(T / "Caddyfile")],
            capture_output=True,
            text=True,
            env=dict(
                os.environ,
                HUB_TLS_FRONT=str(tls.FRONT),
                HUB_ACCESS_DIR=str(T / "access"),
            ),
        )
        check(
            f"  Caddy {ver.split()[0]} validates the box's Caddyfile {state}",
            r.returncode == 0,
            (r.stderr or r.stdout)[-400:],
        )
    tls.front(True)
    tls.admin_only(True)
    for transport in tls.TRANSPORTS:
        for admin_http in tls.ADMIN_HTTP:
            if transport == "http" and admin_http == "block":
                continue
            tls.configure(transport, admin_http)
            r = subprocess.run(
                [
                    c,
                    "validate",
                    "--adapter",
                    "caddyfile",
                    "--config",
                    str(T / "Caddyfile"),
                ],
                capture_output=True,
                text=True,
                env=dict(
                    os.environ,
                    HUB_TLS_FRONT=str(tls.FRONT),
                    HUB_ACCESS_DIR=str(T / "access"),
                ),
            )
            check(
                f"  Caddy {ver.split()[0]} validates {transport}/{admin_http}",
                r.returncode == 0,
                (r.stderr or r.stdout)[-400:],
            )
if not caddies:
    print("SKIP the Caddyfile with real Caddy: set CADDY=/path/to/caddy[:/path/to/another]")
tls.front(False)
os.environ["HUB_WEB_SERVER"] = "nginx"
from irate_box.hub import access

csp = access.addon_csp({"capabilities": {"connect": ["ws://{box}/mqtt", "https://api.github.com"]}})
check(
    "an add-on's ws:// also as wss:// (the MQTT explorer over HTTPS)",
    "connect-src 'self' ws://$host/mqtt wss://$host/mqtt https://api.github.com;" in csp,
    csp,
)
local_csp = access.addon_csp({"capabilities": {"storage": True, "local": True}})
check(
    "an add-on that keeps its work in the page may read data: and blob: URLs",
    "connect-src 'self' data: blob:;" in local_csp
    and "worker-src 'self' blob:;" in local_csp
    and "script-src 'self' 'unsafe-inline' blob:;" in local_csp,
    local_csp,
)
# Through the hub's own form of an added add-on, as the web server's map is built from it (found on box 181: JS
# Paint's autosave failed after a reload because manifests.local() dropped capabilities.local).
from irate_box.hub import manifests

jspaint = json.loads((REPO / "addons" / "jspaint.json").read_text())
added_csp = access.addon_csp(manifests.local(jspaint))
check(
    "an added add-on keeps capabilities.local on its way to the policy (JS Paint, from the catalogue)",
    "connect-src 'self' data: blob:;" in added_csp,
    added_csp,
)
check(
    "  and capabilities.local is part of what adding it agrees to",
    manifests.agreed_part(jspaint).get("local") is True
    and manifests.agreed_part(dict(jspaint, capabilities={"storage": True})).get("local") is False,
    manifests.agreed_part(jspaint),
)
# The hub's public routes: /certificate.json says only public facts, /certificate/ca.crt is
# the CA's certificate as a download, never a key.
tls.front(True)
s_ = socket.socket()
s_.bind(("127.0.0.1", 0))
port = s_.getsockname()[1]
s_.close()
env = dict(
    os.environ,
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
)
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"],
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)


def get(path):
    for _ in range(100):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), b""
        except OSError:
            time.sleep(0.1)


try:
    code, _, body = get("/certificate.json")
    pub = json.loads(body)
    check(
        "/certificate.json: on, the fingerprint, the names and addresses it covers, the expiry; nothing else",
        code == 200
        and pub["on"]
        and pub["fingerprint"] == tls.status()["ca"]["fingerprint"]
        and set(pub) == {"set_up", "on", "port", "fingerprint", "names", "addresses", "expires"}
        and pub["addresses"] == tls.status()["cert"]["addresses"],
        pub,
    )
    code, headers, body = get("/certificate/ca.crt")
    check(
        "/certificate/ca.crt: the CA's certificate, as one to install",
        code == 200
        and body.decode() == tls.CA_CERT.read_text()
        and headers.get("Content-Type") == "application/x-x509-ca-cert"
        and b"PRIVATE" not in body,
    )
    code, headers, _ = get("/certificate")
    check("/certificate: to its page", code in (200, 302))
finally:
    hub.terminate()
    hub.wait()
# The security doctor.
from irate_box.root import secdoctor

answers = {"https": (200, {"Content-Type": "text/html"}), "http": (200, {})}
secdoctor._local_headers = lambda url: answers["https" if url.startswith("https") else "http"]
ADDRS[0] = {"iface": "wlan0", "address": "192.168.1.90", "network": "192.168.1.0/24"}
f = {x["id"]: x for x in secdoctor.step_tls({})}
check(
    "doctor: expiry, coverage, constraints, keys, no HSTS, port 80, all fine; about the tls setting",
    [
        f[k]["status"]
        for k in (
            "tls-expiry",
            "tls-covers",
            "tls-constraints",
            "tls-keys",
            "tls-hsts",
            "tls-plain",
        )
    ]
    == ["ok"] * 6
    and all(x["about"] == {"kind": "setting", "key": "tls"} for x in f.values()),
    {k: (v["status"], v["summary"], v["evidence"]) for k, v in f.items()},
)
answers["https"] = (200, {"Strict-Transport-Security": "max-age=1"})
answers["http"] = None
f = {x["id"]: x for x in secdoctor.step_tls({})}
check(
    "  HSTS sent, port 80 silent: both problems",
    f["tls-hsts"]["status"] == "problem" and f["tls-plain"]["status"] == "problem",
)
tls.configure("https", "allow")
f = {finding["id"]: finding for finding in secdoctor.step_tls({})}
check(
    "doctor: deliberately disabled HTTP is not a fault",
    f["tls-plain"]["status"] == "ok",
)
answers["http"] = (200, {})
f = {finding["id"]: finding for finding in secdoctor.step_tls({})}
check(
    "doctor: HTTP answering despite a disabled policy is a fault",
    f["tls-plain"]["status"] == "problem",
)
answers["http"] = None
tls.configure("both", "allow")
rec = json.loads(tls.RECORD.read_text())
rec["cert"]["expires"] = time.time() + 5 * 86400
tls.RECORD.write_text(json.dumps(rec))
check(
    "  five days left: a problem",
    {x["id"]: x for x in secdoctor.step_tls({})}["tls-expiry"]["status"] == "problem",
)
rec["cert"]["expires"] = time.time() + 20 * 86400
tls.RECORD.write_text(json.dumps(rec))
check(
    "  twenty days left: a warning",
    {x["id"]: x for x in secdoctor.step_tls({})}["tls-expiry"]["status"] == "warn",
)
ADDRS[0] = {"iface": "wlan0", "address": "10.0.0.5", "network": "10.0.0.0/24"}
cov = {x["id"]: x for x in secdoctor.step_tls({})}["tls-covers"]
check(
    "  moved to another subnet: a problem, saying to make a new CA",
    cov["status"] == "problem" and cov["fix"]["id"] == "tls.covers.outside.fix" and "10.0.0.5" in cov["evidence"],
    cov,
)
os.chmod(tls.CA_KEY, 0o644)
check(
    "  a readable CA key: a problem",
    {x["id"]: x for x in secdoctor.step_tls({})}["tls-keys"]["status"] == "problem",
)
os.chmod(tls.CA_KEY, 0o600)
check("  in the doctor's steps", any(st[0] == "tls" for st in secdoctor.STEPS))
# Bring your own: a stand-in public CA, an intermediate, a leaf for box.example.org.
ADDRS[0] = {"iface": "wlan0", "address": "192.168.1.90", "network": "192.168.1.0/24"}
P = T / "public"
P.mkdir()


def o(*a):
    return tls.openssl(*a)


def key(name):
    o(
        "genpkey",
        "-algorithm",
        "EC",
        "-pkeyopt",
        "ec_paramgen_curve:P-256",
        "-out",
        str(P / f"{name}.key"),
    )


key("root")
key("inter")
key("leaf")
key("other")
o(
    "req",
    "-x509",
    "-new",
    "-key",
    str(P / "root.key"),
    "-days",
    "3650",
    "-subj",
    "/CN=Stand-in public root",
    "-out",
    str(P / "root.crt"),
    "-addext",
    "basicConstraints=critical,CA:TRUE",
    "-addext",
    "keyUsage=critical,keyCertSign",
)
(P / "ca.ext").write_text("basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign\n")
o(
    "req",
    "-new",
    "-key",
    str(P / "inter.key"),
    "-subj",
    "/CN=Stand-in intermediate",
    "-out",
    str(P / "inter.csr"),
)
o(
    "x509",
    "-req",
    "-in",
    str(P / "inter.csr"),
    "-CA",
    str(P / "root.crt"),
    "-CAkey",
    str(P / "root.key"),
    "-CAcreateserial",
    "-days",
    "3000",
    "-extfile",
    str(P / "ca.ext"),
    "-out",
    str(P / "inter.crt"),
)
(P / "leaf.ext").write_text("subjectAltName=DNS:box.example.org,DNS:*.box.example.org\nextendedKeyUsage=serverAuth\n")
o(
    "req",
    "-new",
    "-key",
    str(P / "leaf.key"),
    "-subj",
    "/CN=box.example.org",
    "-out",
    str(P / "leaf.csr"),
)
o(
    "x509",
    "-req",
    "-in",
    str(P / "leaf.csr"),
    "-CA",
    str(P / "inter.crt"),
    "-CAkey",
    str(P / "inter.key"),
    "-CAcreateserial",
    "-days",
    "90",
    "-extfile",
    str(P / "leaf.ext"),
    "-out",
    str(P / "leaf.crt"),
)
# An expired one, by openssl ca with explicit dates (x509 -not_after needs OpenSSL 3.4; CI has 3.0).
(P / "db").mkdir()
(P / "db" / "index.txt").write_text("")
(P / "db" / "serial").write_text("1000\n")
(P / "ca.cnf").write_text(
    f"""[ca]\ndefault_ca = d\n[d]\ndatabase = {P}/db/index.txt\nserial = {P}/db/serial\nnew_certs_dir = {P}/db
certificate = {P}/inter.crt\nprivate_key = {P}/inter.key\ndefault_md = sha256\npolicy = p\ncopy_extensions = none\n[p]\ncommonName = supplied\n"""
)
o(
    "ca",
    "-batch",
    "-config",
    str(P / "ca.cnf"),
    "-in",
    str(P / "leaf.csr"),
    "-startdate",
    "20200101000000Z",
    "-enddate",
    "20210101000000Z",
    "-extfile",
    str(P / "leaf.ext"),
    "-notext",
    "-out",
    str(P / "expired.crt"),
)
tls.TRUST = str(P / "root.crt")
r = lambda f: (P / f).read_text()  # noqa: E731


def refused(chain, k, why):
    try:
        tls.import_own(chain, k)
        check(f"own certificate refused: {why}", False)
    except Refused as exc:
        check(
            f"own certificate refused: {why}",
            exc.message["id"] == f"https.import.{why}",
            exc.message,
        )
        return exc.message


before = tls.CHAIN.read_text()
m = refused(r("leaf.crt"), r("leaf.key"), "chain")
check("  saying what openssl said", bool(m["args"]["detail"]), m)
refused(r("leaf.crt") + r("inter.crt"), r("other.key"), "mismatch")
refused(r("expired.crt") + r("inter.crt"), r("leaf.key"), "expired")
refused("not a certificate", r("leaf.key"), "no-cert")
refused(r("leaf.crt") + r("inter.crt"), "", "key-count")
check("  and the box's own still in use after each", tls.CHAIN.read_text() == before)
reloads.clear()
out = tls.import_own(r("leaf.crt") + r("inter.crt"), r("leaf.key"))
c = tls.status()["cert"]
check(
    "a good chain: in use, its names, its expiry, marked the owner's, nginx reloaded",
    c["own"]
    and c["names"] == ["box.example.org", "*.box.example.org"]
    and 89 < (c["expires"] - time.time()) / 86400 < 91
    and out["id"] == "https.import.done"
    and out["args"]["names"] == ["box.example.org", "*.box.example.org"]
    and reloads
    and r("leaf.crt").strip() in tls.CHAIN.read_text(),
    (out, c),
)
check(
    "  the key private to root and the front",
    stat.S_IMODE(tls.KEY.stat().st_mode) == 0o640,
)
check(
    "  never replaced by the box: not due, renew leaves it",
    tls.due(time.time() + 80 * 86400) is None
    and tls.renew()["id"] == "https.renew.current"
    and tls.status()["cert"]["own"],
)
print(tls.use_box_own())
check(
    "back to the box's own: its CA's certificate again",
    not tls.status()["cert"]["own"] and "irate.home.arpa" in tls.status()["cert"]["names"],
)
hc = (REPO / "irate_box/root/hub_control.py").read_text()
check(
    "the helper reads the staged files without following a link, and removes them",
    'safeio.read_request(staged / "chain.pem"' in hc
    and "(staged / f).unlink(missing_ok=True)" in hc
    and '"tls-import": tls_import' in hc,
)
# A request that cannot be done is refused, not answered as done (R300).
from irate_box.root import hub_control


def refused_id(fn, req):
    try:
        fn(req)
    except Refused as exc:
        return exc.message["id"]


check(
    "an import with nothing staged: refused, nothing changed",
    refused_id(hub_control.tls_import, {}) == "control.https.import.none",
)
real_status, tls.status = tls.status, lambda: {"set_up": False}
check(
    "HTTPS switched before it is set up: refused, nothing changed",
    refused_id(hub_control.tls_switch, {"on": True}) == "control.https.not-set-up",
)
tls.status = real_status
# /admin over HTTPS only.
tls.front(True)
reloads.clear()
print(tls.admin_only(True))
adm = (tls.FRONT / "nginx-policy-main.inc").read_text()
check(
    "admin over HTTPS only: plain requests sent to the HTTPS twin, nginx reloaded",
    "return 302 https://$host:443$request_uri;" in adm and reloads and tls.status()["admin_only"],
)
tls.front(False)
check(
    "  HTTPS off takes it away too (never a redirect to a dead port)",
    not (tls.FRONT / "nginx-admin.conf").exists() and not tls.status()["admin_only"],
)
try:
    tls.admin_only(True)
    check("  not while HTTPS is off", False)
except Refused as exc:
    check(
        "  not while HTTPS is off",
        exc.message["id"] == "https.admin-only.https-off",
        exc.message,
    )
tls.front(True)
# Every transport mode is generated at the root-owned boundary.
for transport in tls.TRANSPORTS:
    tls.configure(transport, "allow")
    plain = (tls.FRONT / "nginx-http-main.inc").read_text()
    caddy_plain = (tls.FRONT / "caddy-http-main.inc").read_text()
    check(
        f"transport {transport}: the HTTP listener follows the mode",
        bool(plain) == (transport != "https") and caddy_plain.startswith("http://") == (transport != "https"),
    )
    check(
        f"transport {transport}: TLS follows the mode",
        (tls.FRONT / "nginx-main.conf").exists() == (transport != "http"),
    )
tls.configure("discovery", "warning")
check(
    "discovery mode has only probes and redirects on Caddy HTTP",
    "@probe path /hotspot-detect.html" in (tls.FRONT / "caddy-http-main.inc").read_text()
    and "import irate_box_hub" not in (tls.FRONT / "caddy-http-main.inc").read_text(),
)
check(
    "warning mode precedes admin routes, not the public hub",
    "/http-admin-warning?next=" in (tls.FRONT / "nginx-policy-main.inc").read_text()
    and "/term" in (tls.FRONT / "caddy-policy-main.inc").read_text(),
)
# The administration origin (its own port, 8095 and the twin 8495): all of it is administrators' pages, so the
# administrator HTTP choice applies to every path, and each transport mode is generated for it as for the rest.
tls.configure("both", "block")
adm_policy = (tls.FRONT / "nginx-policy-admin.inc").read_text()
adm_caddy = (tls.FRONT / "caddy-policy-admin.inc").read_text()
check(
    "admin origin, administrator HTTP blocked: every plain path goes to its twin (nginx and Caddy)",
    "if ($uri !~ ^/) { set $irate_plain ''; }" in adm_policy
    and "if ($irate_plain = P) { return 302 https://$host:8495$request_uri; }" in adm_policy
    and "protocol http\n\tpath /*" in adm_caddy
    and "redir @irate_admin_plain https://{host}:8495{uri} 302" in adm_caddy,
    (adm_policy, adm_caddy),
)
tls.configure("both", "warning")
check(
    "  warning: every plain path asks first, unless the answer was given",
    "/http-admin-warning?next=" in (tls.FRONT / "nginx-policy-admin.inc").read_text()
    and "$cookie_irate_http_ok" in (tls.FRONT / "nginx-policy-admin.inc").read_text()
    and "path /*" in (tls.FRONT / "caddy-policy-admin.inc").read_text(),
)
# The warning page and its answer are on the admin origin too; the guard must let them through, or /admin/ over HTTP
# redirects to the warning, and the warning to itself (found on the Pi 3B, 2026-10-21: too many redirects).
adm_policy = (tls.FRONT / "nginx-policy-admin.inc").read_text()
adm_caddy = (tls.FRONT / "caddy-policy-admin.inc").read_text()
check(
    "  warning: the warning page and its answer are never sent to the warning (nginx and Caddy)",
    adm_policy.index("if ($uri ~ ^/http-admin-(?:warning|continue)$) { set $irate_plain ''; }")
    < adm_policy.index("if ($irate_plain = P) { return 302 /http-admin-warning")
    and "\tnot path /http-admin-warning /http-admin-continue\n" in adm_caddy,
    (adm_policy, adm_caddy),
)
for transport in tls.TRANSPORTS:
    tls.configure(transport, "allow")
    plain = (tls.FRONT / "nginx-http-admin.inc").read_text()
    twin = tls.FRONT / "nginx-admin-site.conf"
    check(
        f"admin origin, transport {transport}: the plain listener and the twin follow the mode",
        ("listen 8095;" in plain) == (transport != "https")
        and twin.exists() == (transport != "http")
        and (transport == "http" or "listen 8495 ssl;" in twin.read_text()),
    )
tls.configure("discovery", "allow")
check(
    "  discovery: a plain request to it is sent to the twin whatever the path",
    'if ($irate_discovery = "P:") { return 302 https://$host:8495$request_uri; }'
    in (tls.FRONT / "nginx-policy-admin.inc").read_text(),
)
tls.configure("discovery", "warning")  # as the checks that follow expect
private_state = {
    **access.defaults(),
    "drop": "private",
    "themes": "private",
    "sample-addon": "private",
}
private_files = tls._plain_files("both", "block", private_state)
check(
    "private aliases and local add-ons have HTTP guards on their own origins",
    all(
        path in private_files[tls.FRONT / "caddy-policy-main.inc"]
        for path in ("/drop.html", "/api/drop", "/themes.html", "/git")
    )
    and "/sample-addon" in private_files[tls.FRONT / "caddy-policy-addons.inc"],
)
previous = {path: path.read_bytes() for path in tls.FRONT.iterdir()}
nginx_ok["ok"] = False
try:
    tls.configure("https", "block")
    refused = False
except Refused:
    refused = True
check(
    "a refused mode restores every prior include and the recorded settings",
    refused
    and previous == {path: path.read_bytes() for path in tls.FRONT.iterdir()}
    and tls.settings()["transport"] == "discovery",
)
nginx_ok["ok"] = True
tls.configure("both", "allow")
# The hub: turned on only from an admin page that came over HTTPS; the own-certificate upload staged privately.
nginx = os.environ.get("NGINX")
if nginx:
    import re

    saved_plain, saved_twins = tls.PLAIN_PORTS, tls.TWINS
    tls.PLAIN_PORTS = {name: port if port > 1024 else port + 18000 for name, port in saved_plain.items()}
    tls.TWINS = {name: port if port > 1024 else port + 18000 for name, port in saved_twins.items()}
    values = {
        "PORT": "80",
        "TLS": str(tls.FRONT),
        "ACCESS": str(T / "access.conf"),
        "ADDON_ACCESS": str(T / "addons.conf"),
        "FRONT": str(T / "front.conf"),
        "STATIC": str(REPO / "web"),
    }
    for name, port in tls.PLAIN_PORTS.items():
        values[(name.upper() + "_PORT") if name != "main" else "PORT"] = str(port)
    for name, port in tls.TWINS.items():
        values[(name.upper() + "_TLS_PORT") if name != "main" else "TLS_PORT"] = str(port)
    values["ADDON_PORT"], values["ADDON_TLS_PORT"] = (
        values["ADDONS_PORT"],
        values["ADDONS_TLS_PORT"],
    )
    (T / "access.conf").write_text(access.nginx_conf(access.defaults()))
    (T / "addons.conf").write_text(access.addon_nginx_conf(access.defaults(), []))
    (T / "front.conf").write_text('proxy_set_header X-Irate-Front "test";\n')
    (T / "fastcgi_params").write_text("")
    template = (
        (REPO / "config" / "irate-box.nginx")
        .read_text()
        .replace("/etc/nginx/fastcgi_params", str(T / "fastcgi_params"))
    )
    rendered = re.sub(
        r"@([A-Z_]+)@",
        lambda match: values.get(match[1], str(T / match[1].lower())),
        template,
    )
    temps = "".join(f"{name}_temp_path {T}/{name};\n" for name in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
    (T / "nginx.conf").write_text(
        f"pid {T}/nginx.pid;\nerror_log stderr;\nevents {{}}\nhttp {{ access_log off;\n{temps}{rendered}\n}}\n"
    )
    for transport in tls.TRANSPORTS:
        for admin_http in tls.ADMIN_HTTP:
            if transport == "http" and admin_http == "block":
                continue
            tls.configure(transport, admin_http)
            result = subprocess.run(
                [nginx, "-t", "-q", "-e", "stderr", "-c", str(T / "nginx.conf")],
                capture_output=True,
                text=True,
                env={key: value for key, value in os.environ.items() if key != "NGINX"},
            )
            check(
                f"nginx validates {transport}/{admin_http}",
                result.returncode == 0,
                result.stderr[-500:],
            )
    tls.PLAIN_PORTS, tls.TWINS = saved_plain, saved_twins
    tls.configure("both", "allow")
else:
    print("SKIP the nginx template with real nginx: set NGINX=/path/to/nginx")
import http.client

SECRET = "e" * 64
s_ = socket.socket()
s_.bind(("127.0.0.1", 0))
port = s_.getsockname()[1]
s_.close()
env = dict(
    os.environ,
    HUB_FRONT_SECRET=SECRET,
    HUB_STATE_DIR=str(T / "state"),
    HUB_ETC_DIR=str(T / "etc"),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
)
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"],
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)


def post(body, proto):
    for _ in range(100):
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request(
                "POST",
                "/admin/tls",
                body=json.dumps(body),
                headers={
                    "X-Irate-Front": SECRET,
                    "X-Irate-Admin": "1",
                    "X-Forwarded-For": "192.168.4.20",
                    "X-Forwarded-Proto": proto,
                    "Content-Type": "application/json",
                    "Host": f"127.0.0.1:{port}",
                },
            )
            r = c.getresponse()
            return r.status, r.read()
        except OSError:
            time.sleep(0.1)


try:
    for transport, admin_http in (
        ("discovery", "allow"),
        ("https", "allow"),
        ("both", "block"),
    ):
        code, _ = post(
            {"action": "settings", "transport": transport, "admin_http": admin_http},
            "http",
        )
        check(f"hub: restrictive {transport}/{admin_http} refused over HTTP", code == 409)
        code, _ = post(
            {"action": "settings", "transport": transport, "admin_http": admin_http},
            "https",
        )
        check(
            f"hub: restrictive {transport}/{admin_http} accepted over HTTPS",
            code == 202,
        )
    code, _ = post({"action": "settings", "transport": "bad", "admin_http": "allow"}, "https")
    check("hub: unknown settings refused", code == 400)
    code, body = post({"action": "admin-only", "on": True}, "http")
    check(
        "hub: HTTPS only refused from a plain-HTTP admin page, saying why",
        code == 409 and b"Open /admin over HTTPS first" in body,
        (code, body),
    )
    code, _ = post({"action": "admin-only", "on": True}, "https")
    check("  accepted from an HTTPS one", code == 202)
    code, body = post({"action": "admin-only", "on": False}, "http")
    check(
        "  turning it off: not from plain HTTP (a copied session would open the pages to plain HTTP), said",
        code == 403 and b"hub.https-only" in body,
        (code, body),
    )
    code, _ = post({"action": "admin-only", "on": False}, "https")
    check("  from HTTPS: asked of root (which allows it only with --allow-admin-http)", code == 202)
    code, _ = post({"action": "import", "chain": "C", "key": "K"}, "https")
    staged = T / "state" / "tls-import"
    check(
        "  an own certificate staged for the helper, private to the hub",
        code == 202
        and stat.S_IMODE((staged / "key.pem").stat().st_mode) == 0o600
        and stat.S_IMODE(staged.stat().st_mode) == 0o700,
    )
finally:
    hub.terminate()
    hub.wait()
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
