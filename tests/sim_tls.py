# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""HTTPS for the box (next-work plan step 15, certificates-plan stage 1), offline with the real
openssl: the CA and its name constraints (a certificate it signs for any other name or address
fails openssl verify), the server certificate, renewal at two-thirds of its life and when the box
gets a new address, an address outside the CA's subnet said, the keys private.
python3 tests/sim_tls.py"""
import json, os, stat, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="tls-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.root import tls, safeio  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
safeio.write = lambda path, text, mode=0o644: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_text(text))
ADDRS = [{"iface": "wlan0", "address": "192.168.1.181", "network": "192.168.1.0/24"},
         {"iface": "wlan1", "address": "192.168.4.1", "network": "192.168.4.0/24"},
         {"iface": "tailscale0", "address": "100.101.102.103", "network": "100.64.0.0/10"}]
tls.addresses = lambda: list(ADDRS)
reloads = []
nginx_ok = {"ok": True}
tls._nginx_check_and_reload = lambda: (reloads.append(sorted(p.name for p in tls.FRONT.glob("*.conf"))), (nginx_ok["ok"], "stood in"))[1]
tls.hostname = lambda: "lyra"
rec = tls.make_ca()
check("the CA: its names, and the networks it may vouch for (the hotspot, the LAN's subnet; not Tailscale's)",
      rec["ca"]["names"] == ["irate.home.arpa", ".irate.home.arpa", "lyra.local"] and rec["ca"]["networks"] == ["192.168.4.1/32", "192.168.1.0/24"], rec)
text = tls.openssl("x509", "-in", str(tls.CA_CERT), "-noout", "-text")
check("  constrained, critical, a CA of its own and no deeper", "X509v3 Name Constraints: critical" in text and "CA:TRUE, pathlen:0" in text
      and "Permitted:" in text and "DNS:irate.home.arpa" in text and "IP:192.168.1.0/255.255.255.0" in text, text[-600:])
check("  the CA's key root's only (0700 folder, 0600 file)", stat.S_IMODE(tls.CA_DIR.stat().st_mode) == 0o700 and stat.S_IMODE(tls.CA_KEY.stat().st_mode) == 0o600)
leaf = T / "leaf.crt"
leaf.write_text(tls.CHAIN.read_text().split("-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n")
v = subprocess.run(["openssl", "verify", "-CAfile", str(tls.CA_CERT), str(leaf)], capture_output=True, text=True)
check("the server certificate verifies against the CA", v.returncode == 0, v.stdout + v.stderr)
san = tls.openssl("x509", "-in", str(leaf), "-noout", "-ext", "subjectAltName")
check("  for the box's names and its addresses inside the constraints", all(x in san for x in ("DNS:irate.home.arpa", "DNS:lyra.local", "IP Address:192.168.4.1",
      "IP Address:192.168.1.181")) and "100.101.102.103" not in san, san)
days = (rec_c := json.loads(tls.RECORD.read_text())["cert"])["expires"] - rec_c["made"]
check("  397 days, the key readable by the front, not by all", days == 397 * 86400 and stat.S_IMODE(tls.KEY.stat().st_mode) == 0o640)
def rogue(name_ext):
    d = Path(tempfile.mkdtemp(dir=T))
    tls.openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(d / "k"))
    tls.openssl("req", "-new", "-key", str(d / "k"), "-out", str(d / "c.csr"), "-subj", "/CN=rogue")
    (d / "e").write_text(f"subjectAltName={name_ext}\n")
    tls.openssl("x509", "-req", "-in", str(d / "c.csr"), "-CA", str(tls.CA_CERT), "-CAkey", str(tls.CA_KEY), "-CAcreateserial",
                "-out", str(d / "c.crt"), "-days", "30", "-extfile", str(d / "e"))
    r = subprocess.run(["openssl", "verify", "-CAfile", str(tls.CA_CERT), str(d / "c.crt")], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr
for ext, why in (("DNS:www.example.com", "another site's name"), ("DNS:evil.local", "another .local name"),
                 ("IP:192.168.2.1", "an address outside the subnet"), ("IP:8.8.8.8", "a public address")):
    rc, out = rogue(ext)
    check(f"the CA cannot vouch for {why}, even with its own key", rc != 0 and "subtree" in out, out)
rc, _ = rogue("DNS:notes.irate.home.arpa")
check("  but can for a name under the box's (names for the origins, later)", rc == 0)
check("fresh: nothing due", tls.due() is None)
now = time.time()
check("two-thirds of its life gone: due", tls.due(now + 265 * 86400) == "two-thirds of its life gone" and tls.due(now + 260 * 86400) is None)
ADDRS[0] = {"iface": "wlan0", "address": "192.168.1.77", "network": "192.168.1.0/24"}
check("a new address in the subnet: due, and re-made to cover it", "192.168.1.77" in (tls.due() or "") and "re-made" in tls.renew()
      and "192.168.1.77" in json.loads(tls.RECORD.read_text())["cert"]["addresses"])
ADDRS[0] = {"iface": "wlan0", "address": "10.0.0.5", "network": "10.0.0.0/24"}
st = tls.status()
check("moved to another subnet: said (a new CA is needed), not re-made under a CA that can't vouch for it",
      st["outside"] == ["10.0.0.5"] and tls.due() is None, st)
pub = json.loads((tls.PUBLIC / "status.json").read_text())
check("the hub's copy: the status and the CA's public certificate, never a key", pub["set_up"] and (tls.PUBLIC / "ca.crt").read_text() == tls.CA_CERT.read_text()
      and not any("PRIVATE KEY" in f.read_text() for f in tls.PUBLIC.iterdir()))
# The front (certificates-plan stage 2): making the CA turned HTTPS on; each server block's TLS
# twin written, nginx checked and reloaded; off takes them away; a refused config is undone.
names = sorted(p.name for p in tls.FRONT.glob("*.conf"))
check("making the CA turned HTTPS on: each origin's twin written, nginx reloaded", names == ["nginx-addons.conf", "nginx-git.conf", "nginx-main.conf",
      "nginx-notes.conf", "nginx-wiki.conf"] and reloads and reloads[0] == names and tls.status()["on"], (names, reloads))
main = (tls.FRONT / "nginx-main.conf").read_text()
check("  443 for the hub, the certificate and key, TLS 1.2 and 1.3 only", "listen 443 ssl default_server;" in main and f"ssl_certificate {tls.CHAIN};" in main
      and f"ssl_certificate_key {tls.KEY};" in main and "ssl_protocols TLSv1.2 TLSv1.3;" in main and "listen 8491 ssl;" in (tls.FRONT / "nginx-notes.conf").read_text())
reloads.clear()
ADDRS[0] = {"iface": "wlan0", "address": "192.168.1.90", "network": "192.168.1.0/24"}
tls.renew()
check("a renewed certificate: nginx reloaded to read it", reloads and "nginx-main.conf" in reloads[0])
print(tls.front(False))
check("off: the twins gone, nginx reloaded, the CA and certificate kept", not list(tls.FRONT.glob("*.conf")) and tls.CA_CERT.exists() and tls.CHAIN.exists()
      and not tls.status()["on"])
nginx_ok["ok"] = False
try:
    tls.front(True); check("nginx refusing the twins: undone, and said", False)
except RuntimeError as exc:
    check("nginx refusing the twins: undone, and said", not list(tls.FRONT.glob("*.conf")) and "taken away again" in str(exc) and not tls.status()["on"], str(exc))
nginx_ok["ok"] = True
os.environ["HUB_WEB_SERVER"] = "caddy"
check("under Caddy: made, not yet served, and said so", "nginx only so far" in tls.front(True))
os.environ["HUB_WEB_SERVER"] = "nginx"
from irate_box.hub import access  # noqa: E402
csp = access.addon_csp({"capabilities": {"connect": ["ws://{box}/mqtt", "https://api.github.com"]}})
check("an add-on's ws:// also as wss:// (the MQTT explorer over HTTPS)", "connect-src 'self' ws://$host/mqtt wss://$host/mqtt https://api.github.com;" in csp, csp)
# The hub's public routes (stage 3): /certificate.json says only public facts, /certificate/ca.crt is
# the CA's certificate as a download, never a key.
import socket, urllib.request, urllib.error  # noqa: E402
tls.front(True)
s_ = socket.socket(); s_.bind(("127.0.0.1", 0)); port = s_.getsockname()[1]; s_.close()
env = dict(os.environ, HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), PORT=str(port), HUB_BIND="127.0.0.1")
hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    check("/certificate.json: on, the fingerprint, the names, the expiry; nothing else", code == 200 and pub["on"] and pub["fingerprint"] == tls.status()["ca"]["fingerprint"]
          and set(pub) == {"set_up", "on", "port", "fingerprint", "names", "expires"}, pub)
    code, headers, body = get("/certificate/ca.crt")
    check("/certificate/ca.crt: the CA's certificate, as one to install", code == 200 and body.decode() == tls.CA_CERT.read_text()
          and headers.get("Content-Type") == "application/x-x509-ca-cert" and b"PRIVATE" not in body)
    code, headers, _ = get("/certificate")
    check("/certificate: to its page", code in (200, 302))
finally:
    hub.terminate(); hub.wait()
# The security doctor (stage 6).
from irate_box.root import secdoctor  # noqa: E402
answers = {"https": (200, {"Content-Type": "text/html"}), "http": (200, {})}
secdoctor._local_headers = lambda url: answers["https" if url.startswith("https") else "http"]
ADDRS[0] = {"iface": "wlan0", "address": "192.168.1.90", "network": "192.168.1.0/24"}
f = {x["id"]: x for x in secdoctor.step_tls({})}
check("doctor: expiry, coverage, constraints, keys, no HSTS, port 80, all fine; about the tls setting",
      [f[k]["status"] for k in ("tls-expiry", "tls-covers", "tls-constraints", "tls-keys", "tls-hsts", "tls-plain")] == ["ok"] * 6
      and all(x["about"] == {"kind": "setting", "key": "tls"} for x in f.values()), {k: (v["status"], v["detail"]) for k, v in f.items()})
answers["https"] = (200, {"Strict-Transport-Security": "max-age=1"})
answers["http"] = None
f = {x["id"]: x for x in secdoctor.step_tls({})}
check("  HSTS sent, port 80 silent: both problems", f["tls-hsts"]["status"] == "problem" and f["tls-plain"]["status"] == "problem")
rec = json.loads(tls.RECORD.read_text()); rec["cert"]["expires"] = time.time() + 5 * 86400; tls.RECORD.write_text(json.dumps(rec))
check("  five days left: a problem", {x["id"]: x for x in secdoctor.step_tls({})}["tls-expiry"]["status"] == "problem")
rec["cert"]["expires"] = time.time() + 20 * 86400; tls.RECORD.write_text(json.dumps(rec))
check("  twenty days left: a warning", {x["id"]: x for x in secdoctor.step_tls({})}["tls-expiry"]["status"] == "warn")
ADDRS[0] = {"iface": "wlan0", "address": "10.0.0.5", "network": "10.0.0.0/24"}
check("  moved to another subnet: a problem, saying to make a new CA", "Make a new CA" in {x["id"]: x for x in secdoctor.step_tls({})}["tls-covers"]["fix"])
os.chmod(tls.CA_KEY, 0o644)
check("  a readable CA key: a problem", {x["id"]: x for x in secdoctor.step_tls({})}["tls-keys"]["status"] == "problem")
os.chmod(tls.CA_KEY, 0o600)
check("  in the doctor's steps", any(st[0] == "tls" for st in secdoctor.STEPS))
# Bring your own (stage 4): a stand-in public CA, an intermediate, a leaf for box.example.org.
ADDRS[0] = {"iface": "wlan0", "address": "192.168.1.90", "network": "192.168.1.0/24"}
P = T / "public"; P.mkdir()
def o(*a):
    return tls.openssl(*a)
def key(name):
    o("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(P / f"{name}.key"))
key("root"); key("inter"); key("leaf"); key("other")
o("req", "-x509", "-new", "-key", str(P / "root.key"), "-days", "3650", "-subj", "/CN=Stand-in public root", "-out", str(P / "root.crt"),
  "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign")
(P / "ca.ext").write_text("basicConstraints=critical,CA:TRUE,pathlen:0\nkeyUsage=critical,keyCertSign\n")
o("req", "-new", "-key", str(P / "inter.key"), "-subj", "/CN=Stand-in intermediate", "-out", str(P / "inter.csr"))
o("x509", "-req", "-in", str(P / "inter.csr"), "-CA", str(P / "root.crt"), "-CAkey", str(P / "root.key"), "-CAcreateserial", "-days", "3000",
  "-extfile", str(P / "ca.ext"), "-out", str(P / "inter.crt"))
(P / "leaf.ext").write_text("subjectAltName=DNS:box.example.org,DNS:*.box.example.org\nextendedKeyUsage=serverAuth\n")
o("req", "-new", "-key", str(P / "leaf.key"), "-subj", "/CN=box.example.org", "-out", str(P / "leaf.csr"))
o("x509", "-req", "-in", str(P / "leaf.csr"), "-CA", str(P / "inter.crt"), "-CAkey", str(P / "inter.key"), "-CAcreateserial", "-days", "90",
  "-extfile", str(P / "leaf.ext"), "-out", str(P / "leaf.crt"))
# An expired one, by openssl ca with explicit dates (x509 -not_after needs OpenSSL 3.4; CI has 3.0).
(P / "db").mkdir(); (P / "db" / "index.txt").write_text(""); (P / "db" / "serial").write_text("1000\n")
(P / "ca.cnf").write_text(f"""[ca]\ndefault_ca = d\n[d]\ndatabase = {P}/db/index.txt\nserial = {P}/db/serial\nnew_certs_dir = {P}/db
certificate = {P}/inter.crt\nprivate_key = {P}/inter.key\ndefault_md = sha256\npolicy = p\ncopy_extensions = none\n[p]\ncommonName = supplied\n""")
o("ca", "-batch", "-config", str(P / "ca.cnf"), "-in", str(P / "leaf.csr"), "-startdate", "20200101000000Z", "-enddate", "20210101000000Z",
  "-extfile", str(P / "leaf.ext"), "-notext", "-out", str(P / "expired.crt"))
tls.TRUST = str(P / "root.crt")
r = lambda f: (P / f).read_text()  # noqa: E731
def refused(chain, k, why):
    try:
        tls.import_own(chain, k); check(f"own certificate refused: {why}", False)
    except ValueError as exc:
        check(f"own certificate refused: {why}", True, str(exc))
        return str(exc)
before = tls.CHAIN.read_text()
msg = refused(r("leaf.crt"), r("leaf.key"), "the intermediate missing")
check("  saying to paste the intermediates", "intermediates" in msg, msg)
refused(r("leaf.crt") + r("inter.crt"), r("other.key"), "a key that isn't its own")
refused(r("expired.crt") + r("inter.crt"), r("leaf.key"), "expired")
refused("not a certificate", r("leaf.key"), "no certificate")
refused(r("leaf.crt") + r("inter.crt"), "", "no key")
check("  and the box's own still in use after each", tls.CHAIN.read_text() == before)
reloads.clear()
out = tls.import_own(r("leaf.crt") + r("inter.crt"), r("leaf.key"))
c = tls.status()["cert"]
check("a good chain: in use, its names, its expiry, marked the owner's, nginx reloaded", c["own"] and c["names"] == ["box.example.org", "*.box.example.org"]
      and 89 < (c["expires"] - time.time()) / 86400 < 91 and "box.example.org" in out and reloads and r("leaf.crt").strip() in tls.CHAIN.read_text(), (out, c))
check("  the key private to root and the front", stat.S_IMODE(tls.KEY.stat().st_mode) == 0o640)
check("  never replaced by the box: not due, renew leaves it", tls.due(time.time() + 80 * 86400) is None and "current" in tls.renew()
      and tls.status()["cert"]["own"])
print(tls.use_box_own())
check("back to the box's own: its CA's certificate again", not tls.status()["cert"]["own"] and "irate.home.arpa" in tls.status()["cert"]["names"])
hc = (REPO / "irate_box/root/hub_control.py").read_text()
check("the helper reads the staged files without following a link, and removes them", 'safeio.read_request(staged / "chain.pem"' in hc
      and '(staged / f).unlink(missing_ok=True)' in hc and '"tls-import": tls_import' in hc)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
