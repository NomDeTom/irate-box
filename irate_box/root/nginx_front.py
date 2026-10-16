# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Target-release nginx rendering and private, versioned preparation bundles."""

import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

PROTOCOL = 1
TOKENS = re.compile(r"@([A-Z_]+)@")
PORTS = {
    "PORT": "80", "ADDON_PORT": "8090", "NOTES_PORT": "8091",
    "NOTEBOOK_PORT": "8094", "WIKI_PORT": "8092", "GIT_PORT": "8093",
    "TLS_PORT": "443", "ADDON_TLS_PORT": "8490", "NOTES_TLS_PORT": "8491",
    "NOTEBOOK_TLS_PORT": "8494", "WIKI_TLS_PORT": "8492", "GIT_TLS_PORT": "8493",
}


def settings_from_env():
    code = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
    state = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
    etc = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
    values = {name: os.environ.get(f"HUB_{name}", default) for name, default in PORTS.items()}
    values.update({
        "STATIC": str(code / "web"),
        "APPS": os.environ.get("HUB_APPS_DIR", "/usr/share/hub/apps"),
        "GIT_ROOT": str(state / "git"), "FIRMWARE": str(state / "firmware"),
        "HTPASSWD": os.environ.get("HUB_NGINX_LOGINS", "/etc/nginx/irate-box.htpasswd"),
        "UNCLAIMED": os.environ.get("HUB_UNCLAIMED_FILE", "/etc/nginx/irate-box-unclaimed"),
        "ACCESS": str(etc / "nginx-access.conf"),
        "FRONT": os.environ.get("HUB_NGINX_FRONT", "/etc/nginx/irate-box-front.conf"),
        "ADDONS": str(state / "addons"), "ADDON_ACCESS": str(etc / "nginx-addons.conf"),
        "ADDON_GATES": str(etc / "nginx-addon-gates.conf.d"),
        "TLS": str(etc / "tls/front"), "PAGELOG": str(state / "pagelog/pages.log"),
    })
    return values


def render(template, values, ipv6=True):
    required = set(TOKENS.findall(template))
    missing = required - values.keys()
    if missing:
        raise ValueError("Unsupported nginx placeholders: " + ", ".join(sorted(missing)))
    for name in required:
        value = str(values[name])
        if name in PORTS:
            if not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 65535:
                raise ValueError(f"Invalid nginx port: {name}")
        elif not value.startswith("/") or re.search(r"[\s\x00-\x1f\x7f\"'`;{}$\\]", value):
            raise ValueError(f"Unsafe nginx path: {name}")
    text = TOKENS.sub(lambda match: str(values[match[1]]), template)
    if not ipv6:
        text = "".join(line for line in text.splitlines(keepends=True) if "listen [::]:" not in line)
    return text


def prepare(template, parent, values, identity, ipv6=True):
    parent = Path(parent)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any(path.is_symlink() for path in (parent, *parent.parents)) or parent.stat().st_uid != os.geteuid() or parent.stat().st_mode & 0o077:
        raise ValueError("nginx staging requires a private directory owned by the installer")
    text = render(Path(template).read_text(), values, ipv6)
    bundle = Path(tempfile.mkdtemp(prefix="nginx-", dir=parent))
    site = bundle / "site.conf"
    site.write_text(text)
    site.chmod(0o600)
    manifest = {"protocol": PROTOCOL, "source": identity, "template_sha256": hashlib.sha256(Path(template).read_bytes()).hexdigest(), "settings": values, "ipv6": ipv6,
                "files": {"site.conf": hashlib.sha256(text.encode()).hexdigest()}}
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    (bundle / "manifest.json").chmod(0o600)
    return bundle


def read_bundle(bundle):
    bundle = Path(bundle)
    if bundle.is_symlink() or bundle.stat().st_uid != os.geteuid() or bundle.stat().st_mode & 0o077:
        raise ValueError("Unsafe nginx bundle")
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Unsafe nginx manifest")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("protocol") != PROTOCOL or set(manifest.get("files", {})) != {"site.conf"}:
        raise ValueError("Unsupported nginx bundle protocol or file set")
    site = bundle / "site.conf"
    if site.is_symlink() or not site.is_file() or hashlib.sha256(site.read_bytes()).hexdigest() != manifest["files"]["site.conf"]:
        raise ValueError("The prepared nginx site changed")
    return manifest, site


def main():
    command, *args = sys.argv[1:]
    values = settings_from_env()
    if command == "render" and len(args) == 1:
        print(render(Path(args[0]).read_text(), values, Path("/proc/net/if_inet6").exists()), end="")
    elif command == "prepare" and len(args) == 3:
        print(prepare(args[0], args[1], values, args[2], Path("/proc/net/if_inet6").exists()))
    else:
        raise ValueError("Usage: nginx_front render TEMPLATE | prepare TEMPLATE PRIVATE_DIR SOURCE")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))