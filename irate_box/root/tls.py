# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""HTTPS for the box (next-work plan step 15, plans/certificates-plan): a small certificate
authority made on the box, and the server certificate it signs. Root's; openssl does the work.

The CA (EC P-256, 10 years) is name-constrained: it can vouch only for the box's own names
(irate.home.arpa and names under it, <hostname>.local) and addresses (the hotspot's 192.168.4.1
and the LAN subnet the box was on when the CA was made), so a phone that installs it trusts it
for nothing else, even if its key were stolen. Moving to another subnet needs a new CA (said by
status()). Its key never leaves /etc/hub/tls/ca/ (0700, root).

The server certificate (EC P-256) covers those names and the box's current addresses within the
constraints, for 397 days at most (Apple's limit for installed CAs too). renew() re-makes it at
two-thirds of its life, or as soon as the box has an address it does not cover.

Files:
  /etc/hub/tls/ca/ca.key           the CA's key: root, 0600
  /etc/hub/tls/ca.crt              the CA's certificate (public)
  /etc/hub/tls/server.key          root:<front group>, 0640 (Caddy reads it as its own user)
  /etc/hub/tls/fullchain.crt       the server certificate and the CA's, for the front
  /etc/hub/tls/front/nginx-*.conf  each server block's TLS twin (listen … ssl, the certificate),
                                   included by config/irate-box.nginx; present only while HTTPS is on
  $HUB_STATE_DIR/control/tls/      for the hub: status.json, and ca.crt for /certificate
Stdlib only (and the openssl command).
"""

import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
TLS = ETC / "tls"
CA_DIR = TLS / "ca"
CA_KEY, CA_CERT = CA_DIR / "ca.key", TLS / "ca.crt"
KEY, CHAIN = TLS / "server.key", TLS / "fullchain.crt"
RECORD = TLS / "made.json"            # what the CA and the certificate were made for
PUBLIC = STATE / "control" / "tls"    # what the hub may read
BOX_NAME = os.environ.get("HUB_TLS_NAME", "irate.home.arpa")
HOTSPOT = "192.168.4.1"
CERT_DAYS = 397
CA_DAYS = 3650
# Who besides root reads the server key: Caddy runs as its own user; nginx's master reads it as root.
FRONT = TLS / "front"
# Each origin's TLS twin (install.sh: TLS_PORT and the rest): the hub's own, then the add-ons',
# the notes', Kiwix's and cgit's, beside their plain ports.
TWINS = {"main": int(os.environ.get("HUB_TLS_PORT", "443")), "addons": int(os.environ.get("HUB_ADDON_TLS_PORT", "8490")),
         "notes": int(os.environ.get("HUB_NOTES_TLS_PORT", "8491")), "wiki": int(os.environ.get("HUB_WIKI_TLS_PORT", "8492")),
         "git": int(os.environ.get("HUB_GIT_TLS_PORT", "8493"))}
FRONT_GROUP = os.environ.get("HUB_TLS_GROUP") or ("caddy" if os.environ.get("HUB_WEB_SERVER") == "caddy" else "")


def openssl(*args, inp=None):
    out = subprocess.run(["openssl", *args], input=inp, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(f"openssl {args[0]}: {(out.stderr or out.stdout).strip().splitlines()[-1:]}")
    return out.stdout


def addresses():
    """The box's IPv4 addresses now (not loopback), from `ip`."""
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True).stdout
    found = []
    for m in re.finditer(r"^\d+:\s+(\S+)\s+inet\s+([0-9.]+)/(\d+)", out, re.M):
        iface, addr, plen = m.groups()
        if not addr.startswith("127."):
            found.append({"iface": iface, "address": addr, "network": str(ipaddress.ip_network(f"{addr}/{plen}", strict=False))})
    return found


def hostname():
    return re.sub(r"[^a-z0-9-]", "", socket.gethostname().split(".")[0].lower()) or "irate-box"


def _permitted(nets):
    """The CA's name constraints: the box's names, the hotspot, and the given networks."""
    host = hostname()
    names = [BOX_NAME, f".{BOX_NAME}", f"{host}.local"]
    ips = [ipaddress.ip_network(f"{HOTSPOT}/32")] + [ipaddress.ip_network(n) for n in nets]
    out = [f"permitted;DNS:{n}" for n in names]
    out += [f"permitted;IP:{n.network_address}/{n.netmask}" for n in dict.fromkeys(ips)]
    return names, [str(n) for n in dict.fromkeys(ips)], ",".join(out)


def _record():
    try:
        return json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {}


def _write(path, text, mode, group=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    if group and os.geteuid() == 0:
        try:
            shutil.chown(tmp, group=group)
        except (LookupError, OSError):
            pass
    os.replace(tmp, path)


def make_ca(nets=None):
    """The CA, made once (and again only when asked: a new CA must be installed again)."""
    nets = nets if nets is not None else [a["network"] for a in addresses() if a["address"] != HOTSPOT and not a["address"].startswith("100.")]
    names, ips, constraints = _permitted(nets)
    CA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CA_DIR, 0o700)
    TLS.mkdir(parents=True, exist_ok=True)
    os.chmod(TLS, 0o755)
    with tempfile.TemporaryDirectory() as tmp:
        key = Path(tmp) / "ca.key"
        openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(key))
        cert = Path(tmp) / "ca.crt"
        openssl("req", "-x509", "-new", "-key", str(key), "-sha256", "-days", str(CA_DAYS), "-out", str(cert),
                "-subj", f"/O=Irate-Box/CN=Irate-Box {hostname()} local CA",
                "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext", "keyUsage=critical,keyCertSign,cRLSign",
                "-addext", f"nameConstraints=critical,{constraints}")
        _write(CA_KEY, key.read_text(), 0o600)
        _write(CA_CERT, cert.read_text(), 0o644)
    rec = {"ca": {"made": int(time.time()), "names": names, "networks": ips, "fingerprint": fingerprint(CA_CERT)}}
    _write(RECORD, json.dumps(rec), 0o644)
    make_cert()
    front(True)  # making the box's CA is the owner's choice to have HTTPS (certificates-plan, question 2)
    return _record()


def fingerprint(cert):
    out = openssl("x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256")
    return out.strip().split("=", 1)[-1]


def _covered(rec):
    """The names and addresses the server certificate should cover: the CA's names, and the
    box's addresses that the CA's constraints allow."""
    nets = [ipaddress.ip_network(n) for n in rec["ca"]["networks"]]
    ips = [HOTSPOT] + [a["address"] for a in addresses() if any(ipaddress.ip_address(a["address"]) in n for n in nets)]
    names = [n for n in rec["ca"]["names"] if not n.startswith(".")]
    return names, list(dict.fromkeys(ips))


def make_cert():
    rec = _record()
    if not rec.get("ca") or not CA_KEY.exists():
        raise RuntimeError("no CA yet")
    names, ips = _covered(rec)
    san = ",".join([f"DNS:{n}" for n in names] + [f"IP:{i}" for i in ips])
    with tempfile.TemporaryDirectory() as tmp:
        key, csr, cert = Path(tmp) / "server.key", Path(tmp) / "server.csr", Path(tmp) / "server.crt"
        openssl("genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(key))
        openssl("req", "-new", "-key", str(key), "-out", str(csr), "-subj", f"/O=Irate-Box/CN={names[0]}")
        ext = Path(tmp) / "ext.cnf"
        ext.write_text(f"basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                       f"extendedKeyUsage=serverAuth\nsubjectAltName={san}\n")
        openssl("x509", "-req", "-in", str(csr), "-CA", str(CA_CERT), "-CAkey", str(CA_KEY), "-CAcreateserial",
                "-out", str(cert), "-days", str(CERT_DAYS), "-sha256", "-extfile", str(ext))
        _write(KEY, key.read_text(), 0o640, FRONT_GROUP or None)
        _write(CHAIN, cert.read_text() + CA_CERT.read_text(), 0o644)
    now = int(time.time())
    rec["cert"] = {"made": now, "expires": now + CERT_DAYS * 86400, "names": names, "addresses": ips, "own": False}
    _write(RECORD, json.dumps(rec), 0o644)
    publish()
    return rec


def _nginx_check_and_reload():
    """nginx -t, then a reload; (ok, why). Stood in by the tests."""
    t = subprocess.run(["nginx", "-t", "-q"], capture_output=True, text=True, timeout=60)
    if t.returncode != 0:
        return False, ((t.stderr or t.stdout).strip().splitlines() or ["nginx -t failed"])[-1]
    r = subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stderr or "").strip()


def front(on):
    """HTTPS on or off at the front: each server block's include, then nginx checked and reloaded.
    A check that fails takes the includes away again, so the box serves what it did before."""
    server = os.environ.get("HUB_WEB_SERVER", "nginx")
    if server != "nginx":
        return f"HTTPS is served by nginx only so far ({server} here): the certificate is made, not yet served"
    FRONT.mkdir(parents=True, exist_ok=True)
    os.chmod(FRONT, 0o755)
    for name, port in TWINS.items():
        f = FRONT / f"nginx-{name}.conf"
        if on:
            ds = " default_server" if name == "main" else ""
            _write(f, f"# Written by irate-box (root/tls.py): this server block's TLS twin.\nlisten {port} ssl{ds};\nlisten [::]:{port} ssl{ds};\n"
                      f"ssl_certificate {CHAIN};\nssl_certificate_key {KEY};\nssl_protocols TLSv1.2 TLSv1.3;\n", 0o644)
        else:
            f.unlink(missing_ok=True)
    ok, why = _nginx_check_and_reload()
    if not ok and on:
        for name in TWINS:
            (FRONT / f"nginx-{name}.conf").unlink(missing_ok=True)
        _nginx_check_and_reload()
        rec = _record()
        rec["on"] = False
        _write(RECORD, json.dumps(rec), 0o644)
        publish()
        raise RuntimeError(f"nginx refused the HTTPS server blocks, so they were taken away again: {why}")
    rec = _record()
    rec["on"] = on
    _write(RECORD, json.dumps(rec), 0o644)
    publish()
    return f"HTTPS on: port {TWINS['main']}, and each origin's twin" if on else "HTTPS off (the CA and certificate are kept)"


def due(now=None):
    """Why the server certificate should be re-made now, or None."""
    now = time.time() if now is None else now
    rec = _record()
    if not rec.get("ca"):
        return None
    c = rec.get("cert")
    if not c or not KEY.exists() or not CHAIN.exists():
        return "no server certificate"
    if c.get("own"):
        return None  # the owner's own: never replaced by ours
    if now >= c["made"] + (c["expires"] - c["made"]) * 2 / 3:
        return "two-thirds of its life gone"
    _, ips = _covered(rec)
    missing = [i for i in ips if i not in c["addresses"]]
    if missing:
        return f"the box has an address it does not cover: {', '.join(missing)}"
    return None


def renew():
    why = due()
    if not why:
        publish()
        return "the certificate is current"
    make_cert()
    if _record().get("on"):
        front(True)  # nginx reads the new certificate on a reload
    return f"certificate re-made ({why})"


# --- bring your own (certificates-plan stage 4) -----------------------------------------------------
# A certificate for a domain the owner holds, minted elsewhere (DNS-01: the box has no public
# address) and pasted in on /admin. The only route with no warning anywhere and nothing to install,
# and what passkeys and the https:// captive-portal API need. Checked before it is used: the key
# matches, it has not expired, its chain reaches a CA the system trusts, and which names it covers.
# The box never replaces it on its own (due() is None for it); the owner can go back to the box's.
TRUST = os.environ.get("HUB_TLS_TRUST")  # a CA bundle for the chain check (tests); the system's by default
PEM_CERT = re.compile(r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----")
PEM_KEY = re.compile(r"-----BEGIN (?:EC |RSA )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]+?-----END (?:EC |RSA )?PRIVATE KEY-----")


def _not_after(cert_file):
    out = openssl("x509", "-in", str(cert_file), "-noout", "-enddate").strip().split("=", 1)[1]
    import calendar
    return calendar.timegm(time.strptime(re.sub(r"\s+", " ", out), "%b %d %H:%M:%S %Y %Z"))


def import_own(chain_pem, key_pem):
    """The owner's certificate chain and key, checked, then served in place of the box's own."""
    certs = PEM_CERT.findall(chain_pem or "")
    keys = PEM_KEY.findall(key_pem or "")
    if not certs:
        raise ValueError("no certificate in what was pasted (-----BEGIN CERTIFICATE----- …)")
    if len(keys) != 1:
        raise ValueError("one private key, unencrypted (-----BEGIN PRIVATE KEY----- …)")
    with tempfile.TemporaryDirectory() as tmp:
        leaf, rest, key = Path(tmp) / "leaf.pem", Path(tmp) / "rest.pem", Path(tmp) / "key.pem"
        leaf.write_text(certs[0] + "\n")
        rest.write_text("\n".join(certs[1:]) + "\n")
        key.write_text(keys[0] + "\n")
        os.chmod(key, 0o600)
        try:
            if openssl("x509", "-in", str(leaf), "-noout", "-pubkey") != openssl("pkey", "-in", str(key), "-pubout"):
                raise ValueError("the key is not this certificate's")
        except RuntimeError as exc:
            raise ValueError(f"unreadable: {exc}")
        if subprocess.run(["openssl", "x509", "-in", str(leaf), "-noout", "-checkend", "0"], capture_output=True).returncode != 0:
            raise ValueError("the certificate has expired")
        args = ["openssl", "verify"] + (["-CAfile", TRUST] if TRUST else []) + (["-untrusted", str(rest)] if len(certs) > 1 else []) + [str(leaf)]
        v = subprocess.run(args, capture_output=True, text=True)
        if v.returncode != 0:
            why = (v.stderr or v.stdout).strip().splitlines()[-1:] or ["openssl verify failed"]
            raise ValueError(f"its chain does not reach a CA this box trusts (paste the intermediates too): {why[0][:200]}")
        san = subprocess.run(["openssl", "x509", "-in", str(leaf), "-noout", "-ext", "subjectAltName"], capture_output=True, text=True).stdout
        names = re.findall(r"DNS:([^,\s]+)", san)
        if not names:
            raise ValueError("the certificate names no DNS name (subjectAltName)")
        expires = _not_after(leaf)
        TLS.mkdir(parents=True, exist_ok=True)
        _write(KEY, keys[0] + "\n", 0o640, FRONT_GROUP or None)
        _write(CHAIN, "\n".join(certs) + "\n", 0o644)
    rec = _record()
    rec["cert"] = {"made": int(time.time()), "expires": expires, "names": names, "addresses": [], "own": True}
    _write(RECORD, json.dumps(rec), 0o644)
    front(True)
    return f"your certificate for {', '.join(names)} is in use, until {time.strftime('%Y-%m-%d', time.gmtime(expires))}"


def use_box_own():
    """Back from the owner's certificate to the box's own (its CA's)."""
    rec = _record()
    if not rec.get("ca"):
        raise ValueError("the box has no CA of its own to go back to: make one first")
    make_cert()
    front(True)
    return "the box's own certificate is in use again"


def status():
    """What the hub and the doctor see: whether HTTPS is set up, the CA's fingerprint and what it
    may vouch for, the certificate's names, addresses and expiry, and the box's addresses the CA
    cannot cover (a new subnet: a new CA is needed)."""
    rec = _record()
    if not rec.get("ca") and not (rec.get("cert") or {}).get("own"):
        return {"set_up": False}
    if not rec.get("ca"):  # only the owner's own certificate
        return {"set_up": True, "on": bool(rec.get("on")), "ports": TWINS, "ca": None, "cert": rec.get("cert"), "outside": [], "due": None,
                "keys_private": True}
    nets = [ipaddress.ip_network(n) for n in rec["ca"]["networks"]]
    outside = [a["address"] for a in addresses() if a["address"] != HOTSPOT and not a["address"].startswith("100.")
               and not any(ipaddress.ip_address(a["address"]) in n for n in nets)]
    return {"set_up": True, "on": bool(rec.get("on")), "ports": TWINS, "ca": rec["ca"], "cert": rec.get("cert"), "outside": outside, "due": due(),
            "keys_private": CA_KEY.exists() and (CA_KEY.stat().st_mode & 0o077) == 0}


def publish():
    """The hub's copy: status.json, and the CA's public certificate for /certificate."""
    PUBLIC.mkdir(parents=True, exist_ok=True)
    from irate_box.root import safeio
    safeio.write(PUBLIC / "status.json", json.dumps(status()))
    if CA_CERT.exists():
        safeio.write(PUBLIC / "ca.crt", CA_CERT.read_text())


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "make":
        print(json.dumps(make_ca(), indent=1))
    elif cmd == "renew":
        print(renew())
    elif cmd in ("on", "off"):
        print(front(cmd == "on"))
    elif cmd == "status":
        print(json.dumps(status(), indent=1))
    else:
        print("usage: tls.py make|renew|on|off|status", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
