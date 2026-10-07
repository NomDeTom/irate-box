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
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
