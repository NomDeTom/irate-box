# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""HTTPS for the box: a small certificate authority made on the box, and the server certificate it signs. Root's; openssl does the work.

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

from irate_box.msg import M, Refused
from irate_box.root.nginx_front import locked

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
TLS = ETC / "tls"
CA_DIR = TLS / "ca"
CA_KEY, CA_CERT = CA_DIR / "ca.key", TLS / "ca.crt"
KEY, CHAIN = TLS / "server.key", TLS / "fullchain.crt"
RECORD = TLS / "made.json"  # what the CA and the certificate were made for
PUBLIC = STATE / "control" / "tls"  # what the hub may read
BOX_NAME = os.environ.get("HUB_TLS_NAME", "irate.home.arpa")
HOTSPOT = "192.168.4.1"
CERT_DAYS = 397
CA_DAYS = 3650
# Who besides root reads the server key: Caddy runs as its own user; nginx's master reads it as root.
FRONT = TLS / "front"


def _ports(kind, defaults):
    try:
        saved = json.loads((TLS / "ports.json").read_text()).get(kind, {})
    except (OSError, ValueError, AttributeError):
        saved = {}
    return {
        name: int(os.environ.get(variable, saved.get(name, default))) for name, (variable, default) in defaults.items()
    }


# Each origin's TLS twin (install.sh: TLS_PORT and the rest): the hub's own, then the add-ons',
# the notes', Kiwix's, cgit's and the notebooks', beside their plain ports.
TWINS = _ports(
    "https",
    {
        "main": ("HUB_TLS_PORT", 443),
        "addons": ("HUB_ADDON_TLS_PORT", 8490),
        "notes": ("HUB_NOTES_TLS_PORT", 8491),
        "wiki": ("HUB_WIKI_TLS_PORT", 8492),
        "git": ("HUB_GIT_TLS_PORT", 8493),
        "notebook": ("HUB_NOTEBOOK_TLS_PORT", 8494),
    },
)
FRONT_GROUP = os.environ.get("HUB_TLS_GROUP") or ("caddy" if os.environ.get("HUB_WEB_SERVER") == "caddy" else "")
PLAIN_PORTS = _ports(
    "http",
    {
        "main": ("HUB_PORT", 80),
        "addons": ("HUB_ADDON_PORT", 8090),
        "notes": ("HUB_NOTES_PORT", 8091),
        "wiki": ("HUB_WIKI_PORT", 8092),
        "git": ("HUB_GIT_PORT", 8093),
        "notebook": ("HUB_NOTEBOOK_PORT", 8094),
    },
)
TRANSPORTS = ("http", "both", "discovery", "https")
ADMIN_HTTP = ("allow", "warning", "block")
PROBES = (
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/generate_204",
    "/gen_204",
    "/connecttest.txt",
    "/ncsi.txt",
    "/api/captive",
)


def settings():
    rec = _record()
    return {
        "transport": rec.get("transport", "both" if rec.get("on") else "http"),
        "admin_http": rec.get("admin_http", "block" if rec.get("admin_only") else "allow"),
    }


def _admin_paths(name, state=None):
    from irate_box.hub import access

    paths = (
        ["/admin", "/term", "/sync", "/git", "/git-private"]
        if name == "main"
        else (["/git-private"] if name == "git" else [])
    )
    private = state if state is not None else access.read(ETC / "access.json")
    apps = (
        private.items()
        if name == "main"
        else (
            [(app, mode) for app, mode in private.items() if app not in access.ROUTED]
            if name == "addons"
            else [(name, private.get(name))]
        )
    )
    paths += [f"/{app}" for app, mode in apps if mode == "private" and re.fullmatch(r"[a-z0-9_-]+", app)]
    if name == "main":
        for app in ("drop", "themes"):
            if private.get(app) == "private":
                paths.append(f"/{app}.html")
        if private.get("drop") == "private":
            paths.append("/api/drop")
    return list(dict.fromkeys(paths))


def _plain_files(transport, admin_http, access_state=None):
    out = {}
    for name, port in PLAIN_PORTS.items():
        target = f"https://$host:{TWINS[name]}$request_uri"
        paths = _admin_paths(name, access_state)
        path_re = "^(?:" + "|".join(re.escape(path) for path in paths) + ")(?:/|$)" if paths else "(?!)"
        nginx = ""
        if transport != "https":
            ds = " default_server" if name == "main" else ""
            nginx = f"listen {port}{ds};\n"
            if Path("/proc/net/if_inet6").exists():
                nginx += f"listen [::]:{port}{ds};\n"
        out[FRONT / f"nginx-http-{name}.inc"] = nginx
        guard = "set $irate_plain '';\nif ($scheme = http) { set $irate_plain P; }\n"
        if transport == "discovery":
            guard += "set $irate_probe '';\n"
            if name == "main":
                probe_re = "^(?:" + "|".join(re.escape(path) for path in PROBES) + ")$"
                guard += f"if ($uri ~ {probe_re}) {{ set $irate_probe P; }}\n"
            guard += 'set $irate_discovery "$irate_plain:$irate_probe";\n'
            guard += f'if ($irate_discovery = "P:") {{ return 302 {target}; }}\n'
        if admin_http != "allow" and paths:
            guard += f"if ($uri !~ {path_re}) {{ set $irate_plain ''; }}\n"
            if admin_http == "warning":
                guard += "if ($cookie_irate_http_ok = 1) { set $irate_plain ''; }\n"
                guard += "if ($irate_plain = P) { return 302 /http-admin-warning?next=$request_uri; }\n"
            else:
                guard += f"if ($irate_plain = P) {{ return 302 {target}; }}\n"
        guard += (
            "location = /http-admin-warning { proxy_set_header Host $http_host; proxy_pass http://127.0.0.1:8000; }\n"
        )
        guard += (
            "location = /http-admin-continue { proxy_set_header Host $http_host; proxy_pass http://127.0.0.1:8000; }\n"
        )
        out[FRONT / f"nginx-policy-{name}.inc"] = guard
        if name == "notebook":
            continue
        caddy = "# HTTP listener disabled.\n"
        if transport != "https":
            caddy = f"http://:{port} {{\n"
            if transport == "discovery":
                if name == "main":
                    caddy += (
                        "\t@probe path "
                        + " ".join(PROBES)
                        + "\n\thandle @probe {\n\t\treverse_proxy 127.0.0.1:8000\n\t}\n"
                    )
                caddy += f"\thandle {{\n\t\tredir https://{{host}}:{TWINS[name]}{{uri}} 302\n\t}}\n"
            else:
                caddy += f"\timport irate_box_{'hub' if name == 'main' else name}\n"
            caddy += "}\n"
        out[FRONT / f"caddy-http-{name}.inc"] = caddy
        policy = ""
        if admin_http != "allow" and paths:
            policy = (
                "@irate_admin_plain {\n\tprotocol http\n\tpath "
                + " ".join(path for base in paths for path in (base, base + "/*"))
                + "\n"
            )
            if admin_http == "warning":
                policy += "\tnot header Cookie *irate_http_ok=1*\n"
            policy += "}\nroute {\n"
            dest = (
                "/http-admin-warning?next={uri}"
                if admin_http == "warning"
                else f"https://{{host}}:{TWINS[name]}{{uri}}"
            )
            policy += f"\tredir @irate_admin_plain {dest} 302\n}}\n"
        policy += "handle /http-admin-warning {\n\treverse_proxy 127.0.0.1:8000\n}\nhandle /http-admin-continue {\n\treverse_proxy 127.0.0.1:8000\n}\n"
        out[FRONT / f"caddy-policy-{name}.inc"] = policy
    return out


@locked
def initialise():
    FRONT.mkdir(parents=True, exist_ok=True)
    _write(TLS / "ports.json", json.dumps({"http": PLAIN_PORTS, "https": TWINS}), 0o644)
    policy = settings()
    files = _plain_files(**policy)
    # An older installation may have enabled HTTPS before a new origin was added.  This
    # runs during an authorised install, before its full nginx validation, so bring its
    # TLS includes forward as well as the always-regenerated HTTP includes.
    if _record().get("on") and policy["transport"] != "http" and CHAIN.exists() and KEY.exists():
        files.update(_front_files())
    for path, text in files.items():
        _write(path, text, 0o644)


@locked
def configure(transport, admin_http):
    if transport not in TRANSPORTS or admin_http not in ADMIN_HTTP:
        raise Refused(
            M(
                "https.settings.invalid",
                "Select a transport and an administrator HTTP policy.",
            )
        )
    if (transport != "http" or admin_http == "block") and not (CHAIN.exists() and KEY.exists()):
        raise Refused(M("https.settings.no-cert", "Generate or import a certificate first."))
    if transport == "http" and admin_http == "block":
        raise Refused(
            M(
                "https.settings.conflict",
                "Enable HTTPS before blocking administrator access over HTTP.",
            )
        )
    FRONT.mkdir(parents=True, exist_ok=True)
    files = _plain_files(transport, admin_http)
    files.update({path: text if transport != "http" else None for path, text in _front_files().items()})
    files.update({FRONT / name: None for name in ("nginx-admin.conf", "caddy-admin.caddy")})
    before = {path: path.read_text() if path.exists() else None for path in files}
    record_before = RECORD.read_text() if RECORD.exists() else None

    def restore(contents):
        for path, text in contents.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                _write(path, text, 0o644)

    try:
        restore(files)
        ok, why = _nginx_check_and_reload()
        if not ok:
            raise Refused(
                M(
                    "https.settings.refused",
                    "The web server refused the change. The previous settings were restored. {detail}",
                    detail=why,
                )
            )
        rec = _record()
        rec.update(
            transport=transport,
            admin_http=admin_http,
            on=transport != "http",
            admin_only=admin_http == "block",
        )
        _write(RECORD, json.dumps(rec), 0o644)
        publish()
    except (OSError, subprocess.SubprocessError, Refused):
        restore(before)
        if record_before is None:
            RECORD.unlink(missing_ok=True)
        else:
            _write(RECORD, record_before, 0o644)
        _nginx_check_and_reload()
        publish()
        raise
    return M("https.settings.saved", "HTTP/HTTPS settings saved.")


def openssl(*args, inp=None):
    out = subprocess.run(["openssl", *args], input=inp, capture_output=True, text=True, timeout=60)  # noqa: TID251
    if out.returncode != 0:
        raise RuntimeError(f"openssl {args[0]}: {(out.stderr or out.stdout).strip().splitlines()[-1:]}")
    return out.stdout


def addresses():
    """The box's IPv4 addresses now (not loopback), from `ip`."""
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True).stdout  # noqa: TID251
    found = []
    for m in re.finditer(r"^\d+:\s+(\S+)\s+inet\s+([0-9.]+)/(\d+)", out, re.M):
        iface, addr, plen = m.groups()
        if not addr.startswith("127."):
            found.append(
                {
                    "iface": iface,
                    "address": addr,
                    "network": str(ipaddress.ip_network(f"{addr}/{plen}", strict=False)),
                }
            )
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


@locked
def make_ca(nets=None):
    """The CA, made once (and again only when asked: a new CA must be installed again)."""
    previous = settings() if _record().get("cert") else None
    nets = (
        nets
        if nets is not None
        else [a["network"] for a in addresses() if a["address"] != HOTSPOT and not a["address"].startswith("100.")]
    )
    names, ips, constraints = _permitted(nets)
    CA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CA_DIR, 0o700)
    TLS.mkdir(parents=True, exist_ok=True)
    os.chmod(TLS, 0o755)
    with tempfile.TemporaryDirectory() as tmp:
        key = Path(tmp) / "ca.key"
        openssl(
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-out",
            str(key),
        )
        cert = Path(tmp) / "ca.crt"
        openssl(
            "req",
            "-x509",
            "-new",
            "-key",
            str(key),
            "-sha256",
            "-days",
            str(CA_DAYS),
            "-out",
            str(cert),
            "-subj",
            f"/O=Irate-Box/CN=Irate-Box {hostname()} local CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE,pathlen:0",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
            "-addext",
            f"nameConstraints=critical,{constraints}",
        )
        _write(CA_KEY, key.read_text(), 0o600)
        _write(CA_CERT, cert.read_text(), 0o644)
    rec = dict(previous or {})
    rec["ca"] = {
        "made": int(time.time()),
        "names": names,
        "networks": ips,
        "fingerprint": fingerprint(CA_CERT),
    }
    _write(RECORD, json.dumps(rec), 0o644)
    make_cert()
    configure(**previous) if previous else front(True)
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
        raise Refused(
            M(
                "https.cert.no-ca",
                "The box has no certificate authority yet. Create one first.",
            )
        )
    names, ips = _covered(rec)
    san = ",".join([f"DNS:{n}" for n in names] + [f"IP:{i}" for i in ips])
    with tempfile.TemporaryDirectory() as tmp:
        key, csr, cert = (
            Path(tmp) / "server.key",
            Path(tmp) / "server.csr",
            Path(tmp) / "server.crt",
        )
        openssl(
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-out",
            str(key),
        )
        openssl(
            "req",
            "-new",
            "-key",
            str(key),
            "-out",
            str(csr),
            "-subj",
            f"/O=Irate-Box/CN={names[0]}",
        )
        ext = Path(tmp) / "ext.cnf"
        ext.write_text(
            f"basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
            f"extendedKeyUsage=serverAuth\nsubjectAltName={san}\n"
        )
        openssl(
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(CA_CERT),
            "-CAkey",
            str(CA_KEY),
            "-CAcreateserial",
            "-out",
            str(cert),
            "-days",
            str(CERT_DAYS),
            "-sha256",
            "-extfile",
            str(ext),
        )
        _write(KEY, key.read_text(), 0o640, FRONT_GROUP or None)
        _write(CHAIN, cert.read_text() + CA_CERT.read_text(), 0o644)
    now = int(time.time())
    rec["cert"] = {
        "made": now,
        "expires": now + CERT_DAYS * 86400,
        "names": names,
        "addresses": ips,
        "own": False,
    }
    _write(RECORD, json.dumps(rec), 0o644)
    publish()
    return rec


def _server():
    return "caddy" if os.environ.get("HUB_WEB_SERVER") == "caddy" else "nginx"


def _nginx_check_and_reload():
    """The front's own check, then a reload; (ok, why). Stood in by the tests. (Named for nginx,
    its first; Caddy's is caddy validate on its live config.)"""
    if _server() == "caddy":
        t = subprocess.run(  # noqa: TID251
            [
                "caddy",
                "validate",
                "--adapter",
                "caddyfile",
                "--config",
                "/etc/caddy/Caddyfile",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if t.returncode != 0:
            return (
                False,
                ((t.stderr or t.stdout).strip().splitlines() or ["caddy validate failed"])[-1],
            )
        r = subprocess.run(  # noqa: TID251
            ["systemctl", "restart", "caddy"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return r.returncode == 0, (r.stderr or "").strip()
    t = subprocess.run(["nginx", "-t", "-q"], capture_output=True, text=True, timeout=60)  # noqa: TID251
    if t.returncode != 0:
        return (
            False,
            ((t.stderr or t.stdout).strip().splitlines() or ["nginx -t failed"])[-1],
        )
    r = subprocess.run(["systemctl", "reload", "nginx"], capture_output=True, text=True, timeout=60)  # noqa: TID251
    return r.returncode == 0, (r.stderr or "").strip()


def _front_files():
    """{file: text}: each site's TLS twin, for the web server in front.
    nginx: an include in each server block (listen … ssl, the certificate).
    Caddy: a site of its own per twin, importing the same snippet as the plain site (its routes).
    """
    if _server() == "caddy":
        return {
            FRONT / f"caddy-site-{name}.caddy": f"# Written by irate-box (root/tls.py): the {name} site's TLS twin.\n"
            f"https://:{TWINS[name]} {{\n\ttls {CHAIN} {KEY}\n\timport irate_box_{'hub' if name == 'main' else name}\n}}\n"
            for name in ("main", "addons", "notes", "wiki", "git")
        }
    out = {}
    for name, port in TWINS.items():
        ds = " default_server" if name == "main" else ""
        out[FRONT / f"nginx-{name}.conf"] = (
            f"# Written by irate-box (root/tls.py): this server block's TLS twin.\nlisten {port} ssl{ds};\n"
            f"listen [::]:{port} ssl{ds};\nssl_certificate {CHAIN};\nssl_certificate_key {KEY};\n"
            "ssl_protocols TLSv1.2 TLSv1.3;\n"
        )
    return out


@locked
def front(on):
    """HTTPS on or off at the front: each server block's include, then nginx checked and reloaded.
    A check that fails takes the includes away again, so the box serves what it did before.
    """
    current = settings()
    transport = current["transport"] if on and current["transport"] != "http" else ("both" if on else "http")
    configure(transport, current["admin_http"] if on else "allow")
    return (
        M(
            "https.front.on",
            "HTTPS is enabled on port {port} and on the HTTPS port of each web app.",
            port=TWINS["main"],
        )
        if on
        else M(
            "https.front.off",
            "HTTPS is disabled. The box keeps its certificate authority and its certificate.",
        )
    )


@locked
def admin_only(on):
    """/admin over HTTPS only: plain requests for it are sent to the
    HTTPS twin, so the admin password never crosses the network in clear. Only while HTTPS is on;
    the hub lets the owner turn it on only from an admin page opened over HTTPS (proof that their
    device trusts the box, so they can't lock themselves out)."""
    rec = _record()
    if on and not rec.get("on"):
        raise Refused(M("https.admin-only.https-off", "HTTPS is disabled. Enable HTTPS first."))
    configure(settings()["transport"], "block" if on else "allow")
    return (
        M("https.admin-only.on", "Administrator pages require HTTPS.")
        if on
        else M("https.admin-only.off", "Administrator pages also answer over HTTP.")
    )


def _due(now=None):
    """Why the server certificate should be re-made now, as (case, the addresses it does not cover), or None."""
    now = time.time() if now is None else now
    rec = _record()
    if not rec.get("ca"):
        return None
    c = rec.get("cert")
    if not c or not KEY.exists() or not CHAIN.exists():
        return "missing", []
    if c.get("own"):
        return None  # the owner's own: never replaced by ours
    if now >= c["made"] + (c["expires"] - c["made"]) * 2 / 3:
        return "old", []
    _, ips = _covered(rec)
    missing = [i for i in ips if i not in c["addresses"]]
    if missing:
        return "address", missing
    return None


def due(now=None):
    """Why the server certificate should be re-made now, or None: English, for status.json and the doctor."""
    why = _due(now)
    if not why:
        return None
    case, missing = why
    return {
        "missing": "no server certificate",
        "old": "two-thirds of its life gone",
        "address": f"the box has an address it does not cover: {', '.join(missing)}",
    }[case]


@locked
def renew():
    why = _due()
    if not why:
        publish()
        return M("https.renew.current", "The certificate is current.")
    make_cert()
    if _record().get("on"):
        front(True)  # nginx reads the new certificate on a reload
    case, missing = why
    if case == "missing":
        return M(
            "https.renew.missing",
            "The box made a server certificate, because it had none.",
        )
    if case == "old":
        return M(
            "https.renew.old",
            "The box renewed the certificate, because two-thirds of its life had gone.",
        )
    return M(
        "https.renew.address",
        {
            "one": "The box renewed the certificate to cover its new address {addresses}.",
            "other": "The box renewed the certificate to cover its new addresses {addresses}.",
        },
        n=len(missing),
        addresses=missing,
    )


# --- bring your own ------------------------------------------------------------------------------
# A certificate for a domain the owner holds, minted elsewhere (DNS-01: the box has no public
# address) and pasted in on /admin. The only route with no warning anywhere and nothing to install,
# and what passkeys and the https:// captive-portal API need. Checked before it is used: the key
# matches, it has not expired, its chain reaches a CA the system trusts, and which names it covers.
# The box never replaces it on its own (due() is None for it); the owner can go back to the box's.
TRUST = os.environ.get("HUB_TLS_TRUST")  # a CA bundle for the chain check (tests); the system's by default
PEM_CERT = re.compile(r"-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+?-----END CERTIFICATE-----")
PEM_KEY = re.compile(
    r"-----BEGIN (?:EC |RSA )?PRIVATE KEY-----\s+[A-Za-z0-9+/=\s]+?-----END (?:EC |RSA )?PRIVATE KEY-----"
)


def _not_after(cert_file):
    out = openssl("x509", "-in", str(cert_file), "-noout", "-enddate").strip().split("=", 1)[1]
    import calendar

    return calendar.timegm(time.strptime(re.sub(r"\s+", " ", out), "%b %d %H:%M:%S %Y %Z"))


@locked
def import_own(chain_pem, key_pem):
    """The owner's certificate chain and key, checked, then served in place of the box's own."""
    certs = PEM_CERT.findall(chain_pem or "")
    keys = PEM_KEY.findall(key_pem or "")
    if not certs:
        raise Refused(
            M(
                "https.import.no-cert",
                "The pasted text has no certificate. Paste PEM text that starts with {begin}",
                begin="-----BEGIN CERTIFICATE-----",
            )
        )
    if len(keys) != 1:
        raise Refused(
            M(
                "https.import.key-count",
                "Paste exactly one unencrypted private key in PEM format. It starts with {begin}",
                begin="-----BEGIN PRIVATE KEY-----",
            )
        )
    with tempfile.TemporaryDirectory() as tmp:
        leaf, rest, key = (
            Path(tmp) / "leaf.pem",
            Path(tmp) / "rest.pem",
            Path(tmp) / "key.pem",
        )
        leaf.write_text(certs[0] + "\n")
        rest.write_text("\n".join(certs[1:]) + "\n")
        key.write_text(keys[0] + "\n")
        os.chmod(key, 0o600)
        try:
            matches = openssl("x509", "-in", str(leaf), "-noout", "-pubkey") == openssl(
                "pkey", "-in", str(key), "-pubout"
            )
        except RuntimeError as exc:
            raise Refused(
                M(
                    "https.import.unreadable",
                    "The box cannot read the certificate or the key. openssl said: {detail}",
                    detail=str(exc),
                )
            ) from exc
        if not matches:
            raise Refused(
                M(
                    "https.import.mismatch",
                    "The certificate and the key do not match. Paste the key that belongs to the first certificate.",
                )
            )
        if (
            subprocess.run(  # noqa: TID251
                ["openssl", "x509", "-in", str(leaf), "-noout", "-checkend", "0"],
                capture_output=True,
            ).returncode
            != 0
        ):
            raise Refused(
                M(
                    "https.import.expired",
                    "The certificate has expired. Import a certificate that is still valid.",
                )
            )
        args = (
            ["openssl", "verify"]
            + (["-CAfile", TRUST] if TRUST else [])
            + (["-untrusted", str(rest)] if len(certs) > 1 else [])
            + [str(leaf)]
        )
        v = subprocess.run(args, capture_output=True, text=True)  # noqa: TID251
        if v.returncode != 0:
            why = (v.stderr or v.stdout).strip().splitlines()[-1:] or ["openssl verify failed"]
            raise Refused(
                M(
                    "https.import.chain",
                    "The certificate chain is incomplete, or this box does not trust it. "
                    "Paste the intermediate certificates after the certificate. openssl said: {detail}",
                    detail=why[0][:200],
                )
            )
        san = subprocess.run(  # noqa: TID251
            ["openssl", "x509", "-in", str(leaf), "-noout", "-ext", "subjectAltName"],
            capture_output=True,
            text=True,
        ).stdout
        names = re.findall(r"DNS:([^,\s]+)", san)
        if not names:
            raise Refused(
                M(
                    "https.import.no-dns",
                    "The certificate has no DNS name in its subjectAltName field. "
                    "Import a certificate with at least one DNS name.",
                )
            )
        expires = _not_after(leaf)
        TLS.mkdir(parents=True, exist_ok=True)
        _write(KEY, keys[0] + "\n", 0o640, FRONT_GROUP or None)
        _write(CHAIN, "\n".join(certs) + "\n", 0o644)
    rec = _record()
    rec["cert"] = {
        "made": int(time.time()),
        "expires": expires,
        "names": names,
        "addresses": [],
        "own": True,
    }
    _write(RECORD, json.dumps(rec), 0o644)
    front(True)
    return M(
        "https.import.done",
        "Your certificate for {names} is in use until {expires}. The box does not renew it.",
        names=names,
        expires=time.strftime("%Y-%m-%d", time.gmtime(expires)),
    )


@locked
def use_box_own():
    """Back from the owner's certificate to the box's own (its CA's)."""
    rec = _record()
    if not rec.get("ca"):
        raise Refused(
            M(
                "https.box.no-ca",
                "The box has no certificate authority of its own. Create one first.",
            )
        )
    make_cert()
    front(True)
    return M("https.box.done", "The certificate of the box is in use again.")


def status():
    """What the hub and the doctor see: whether HTTPS is set up, the CA's fingerprint and what it
    may vouch for, the certificate's names, addresses and expiry, and the box's addresses the CA
    cannot cover (a new subnet: a new CA is needed)."""
    rec = _record()
    if not rec.get("ca") and not (rec.get("cert") or {}).get("own"):
        return {"set_up": False, **settings()}
    if not rec.get("ca"):  # only the owner's own certificate
        return {
            "set_up": True,
            **settings(),
            "admin_only": settings()["admin_http"] == "block",
            "on": bool(rec.get("on")),
            "ports": TWINS,
            "ca": None,
            "cert": rec.get("cert"),
            "outside": [],
            "due": None,
            "keys_private": True,
        }
    nets = [ipaddress.ip_network(n) for n in rec["ca"]["networks"]]
    outside = [
        a["address"]
        for a in addresses()
        if a["address"] != HOTSPOT
        and not a["address"].startswith("100.")
        and not any(ipaddress.ip_address(a["address"]) in n for n in nets)
    ]
    return {
        "set_up": True,
        **settings(),
        "on": bool(rec.get("on")),
        "admin_only": bool(rec.get("admin_only")),
        "ports": TWINS,
        "ca": rec["ca"],
        "cert": rec.get("cert"),
        "outside": outside,
        "due": due(),
        "keys_private": CA_KEY.exists() and (CA_KEY.stat().st_mode & 0o077) == 0,
    }


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
    elif cmd == "initialise":
        initialise()
    else:
        print("usage: tls.py initialise|make|renew|on|off|status", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
