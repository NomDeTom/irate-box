# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The security manifest: what the box may reach, declared in one file and read from code.

security.json in the repository declares root's actions, every unit and its sandbox, the
confidential files and who may read them, what each web server origin serves, the hosts root
and the hub may contact, and the files that run as root. This module builds that declaration
from a tree (statically: nothing in the tree is imported or run), compares a tree with its
own manifest, and sorts two manifests' differences into widenings and narrowings.

Stdlib only. The same module is used by tests/security_manifest_guard.py in CI and by root's
verify_update() on a fetched tree, so the box never has to trust the fetched code's account
of itself.
"""

import ast
import fnmatch
import json
import re
from pathlib import Path

SECURITY_FILE = "security.json"
VERSION = 1
IGNORE_HOSTS = frozenset(
    {
        "",
        ".",
        "0.0.0.0",  # nosec B104
        "127",
        "127.0.0.1",
        "localhost",
        "unix",
        "irate_box_hub",
        "www.w3.org",
    }
)
OUTBOUND_REASONS = {
    "api.github.com": "the update check, the source's branches and releases",
    "box": "a name a box may answer to on its own network, in a message; never contacted",
    "deb.debian.org": "the packages Debian ships",
    "dl.cloudsmith.io": "Caddy's package key and repository",
    "example.invalid": "a placeholder in messages and tests; never contacted",
    "github.com": "the update source, the add-ons catalogue and their repositories",
    "registry.npmjs.org": "the add-ons' npm packages",
}
_SECRETS = {
    "/etc/hub/admin-password": {"owner": "root", "group": "root", "mode": "600", "readers": ["root"]},
    "/etc/hub/front-secret.env": {"owner": "root", "group": "root", "mode": "600", "readers": ["root"]},
    "/etc/hub/tls/ca/ca.key": {"owner": "root", "group": "root", "mode": "600", "readers": ["root"]},
    "/etc/hub/tls/server.key": {
        "owner": "root",
        "group": "the web server's group",
        "mode": "640",
        "readers": ["root", "the web server's group"],
    },
    "/etc/hub/update-signing.json": {"owner": "root", "group": "root", "mode": "600", "readers": ["root"]},
    "/etc/nginx/irate-box.htpasswd": {
        "owner": "root",
        "group": "the web server's group",
        "mode": "640",
        "readers": ["root", "the web server's group"],
    },
    "/etc/NetworkManager/system-connections/*": {
        "owner": "root",
        "group": "root",
        "mode": "600",
        "readers": ["root"],
    },
    "/var/lib/hub/accounts.json": {"owner": "hub", "group": "hub", "mode": "600", "readers": ["hub"]},
    "/var/lib/hub/hotspot.json": {"owner": "hub", "group": "hub", "mode": "600", "readers": ["hub"]},
    "/var/lib/irate-box-root/retain-key": {"owner": "root", "group": "root", "mode": "600", "readers": ["root"]},
}
_PRIVILEGED = [
    "install.sh",
    "uninstall.sh",
    "irate_box/root/**",
    "tools/*.sh",
    "config/irate-box.service",
]
_PATH_ACTIONS = {
    "pins": {"reads": [], "writes": ["control/pins.json"]},
}
_HUB_ACTIONS = {
    "https_only",
    "policy",
}
_SECRET_VARS = {
    "ETC": "/etc/hub",
    "STATE": "/var/lib/hub",
    "ROOT": "/var/lib/irate-box-root",
    "NGINX_LOGINS": "/etc/nginx/irate-box.htpasswd",
    "CODE": "/opt/irate-box",
}
_DIRECTIVES = (
    "User",
    "DynamicUser",
    "ProtectSystem",
    "ReadWritePaths",
    "ReadOnlyPaths",
    "PrivateTmp",
    "NoNewPrivileges",
    "ProtectHome",
    "ProtectProc",
    "PrivateDevices",
    "CapabilityBoundingSet",
    "RestrictAddressFamilies",
    "StateDirectory",
    "LogsDirectory",
    "CacheDirectory",
    "BindPaths",
)
_LIST_DIRECTIVES = frozenset(
    {
        "ReadWritePaths",
        "ReadOnlyPaths",
        "CapabilityBoundingSet",
        "RestrictAddressFamilies",
        "BindPaths",
        "StateDirectory",
        "LogsDirectory",
        "CacheDirectory",
    }
)
_UNIT_RE = re.compile(
    r"cat >/etc/systemd/system/([^\s]+?) <<-?'?([A-Za-z_][A-Za-z0-9_]*)'?\n"
    r"(.*?)\n[ \t]*\2[ \t]*(?:\n|$)",
    re.S,
)
_URL_RE = re.compile(r"https?://([A-Za-z0-9._~%+-]+)", re.I)
_LOCATION_RE = re.compile(r"^\s*location\s+(?:=\s*|\^~\s*|~\*?\s*)?(@?[^\s{]+)\s*\{", re.M)
_NGINX_ROOT_RE = re.compile(r"^\s*(?:root|alias)\s+([^;]+);", re.M)
_CADDY_HANDLE_RE = re.compile(r"^\s*(?:handle|handle_path|redir)\s+([^\s{]+)", re.M)
_CADDY_ROOT_RE = re.compile(r"^\s*root\s+\*\s+([^\s{]+)", re.M)
_CHMOD_RE = re.compile(r"^\s*chmod\s+([0-7]{3,4})\s+\"?(\S+?)\"?\s*$")
_CHOWN_RE = re.compile(r"^\s*chown\s+([^:\s]+):(\S+)\s+\"?(\S+?)\"?\s*$")
_INSTALL_RE = re.compile(r"^\s*install\b.*\s-m\s+([0-7]{3,4})\b.*$")


def _read(path):
    return Path(path).read_text(errors="replace")


def _tree(path):
    return ast.parse(_read(path), str(path))


def _assigned(tree, name):
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    raise KeyError(name)


def _literal_set(tree, name):
    node = _assigned(tree, name)
    if isinstance(node, ast.Call):
        node = node.args[0]
    return {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}


def _dict_keys(tree, name):
    node = _assigned(tree, name)
    if not isinstance(node, ast.Dict):
        raise ValueError(name)
    return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def _expand(text):
    for name, value in _SECRET_VARS.items():
        text = text.replace("${" + name + "}", value).replace("$" + name, value)
    return text


def root_actions(tree):
    tree = Path(tree)
    actions = _dict_keys(_tree(tree / "irate_box/root/hub_control.py"), "ACTIONS")
    https = _literal_set(_tree(tree / "irate_box/hub/transport.py"), "HTTPS_ONLY")
    policy = _literal_set(_tree(tree / "irate_box/root/hub_control.py"), "POLICY_ACTIONS")
    return {
        name: {
            "https_only": name in https,
            "policy": name in policy,
            "reads": list(_PATH_ACTIONS.get(name, {}).get("reads", [])),
            "writes": list(_PATH_ACTIONS.get(name, {}).get("writes", [])),
        }
        for name in sorted(actions)
    }


def _unit_directives(body):
    out = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name not in _DIRECTIVES:
            continue
        if name in _LIST_DIRECTIVES:
            out.setdefault(name, []).extend(part for part in value.split() if part)
        else:
            out[name] = value
    if "DynamicUser" not in out:
        out.setdefault("User", "root")
    return out


def units(tree):
    tree = Path(tree)
    text = _read(tree / "install.sh")
    found = re.search(r'^HUB_SANDBOX="(.*?)"$', text, re.S | re.M)
    sandbox = found.group(1) if found else ""
    out = {}
    for found in _UNIT_RE.finditer(text):
        name, _, body = found.groups()
        out[name] = {"source": "install.sh", **_unit_directives(body.replace("$HUB_SANDBOX", sandbox))}
    return dict(sorted(out.items()))


def secrets(tree):
    tree = Path(tree)
    declared = {path: dict(entry, readers=list(entry["readers"])) for path, entry in _SECRETS.items()}
    text = _read(tree / "install.sh")
    for raw in text.splitlines():
        line = _expand(raw)
        for match in _CHMOD_RE.finditer(line):
            mode, path = match.groups()
            if path in declared:
                declared[path]["mode"] = mode
        for match in _CHOWN_RE.finditer(line):
            owner, group, path = match.groups()
            if path in declared:
                declared[path]["owner"] = owner
                declared[path]["group"] = group
        match = _INSTALL_RE.match(line)
        if match and line.rstrip().endswith(tuple('"' + p + '"' for p in declared)):
            mode = match.group(1)
            for path in declared:
                if line.rstrip().endswith(f'"{path}"'):
                    declared[path]["mode"] = mode
    return dict(sorted(declared.items()))


def hub(tree):
    text = _read(Path(tree) / "install.sh")
    groups = set()
    for line in text.splitlines():
        if not re.search(r"\b(?:usermod|adduser|gpasswd)\b", line):
            continue
        if "HUB_USER" not in line:
            continue
        found = re.search(r"(?:-aG|-G)\s+\"?([A-Za-z0-9_,.-]+)", line)
        if found:
            groups.update(part for part in found.group(1).split(",") if part)
        found = re.search(r"gpasswd\s+-a\s+\S+\s+(\S+)", line)
        if found:
            groups.add(found.group(1).strip('"'))
    return {"groups": sorted(groups), "secrets": []}


def served(tree):
    tree = Path(tree)
    nginx = _read(tree / "config/irate-box.nginx")
    caddy = _read(tree / "config/Caddyfile")
    return {
        "caddy": {
            "locations": sorted(set(_CADDY_HANDLE_RE.findall(caddy))),
            "roots": sorted(set(_CADDY_ROOT_RE.findall(caddy))),
        },
        "nginx": {
            "locations": sorted(set(_LOCATION_RE.findall(nginx))),
            "roots": sorted(set(_NGINX_ROOT_RE.findall(nginx))),
        },
    }


def outbound(tree):
    tree = Path(tree)
    hosts = set()
    files = [
        *sorted((tree / "irate_box/root").rglob("*.py")),
        *sorted((tree / "irate_box/hub").rglob("*.py")),
        tree / "install.sh",
    ]
    for path in files:
        if not path.is_file():
            continue
        for host in _URL_RE.findall(_read(path)):
            host = host.lower()
            if host in IGNORE_HOSTS or host.startswith(("192.168.", "10.")):
                continue
            hosts.add(host)
    return {host: OUTBOUND_REASONS.get(host, "") for host in sorted(hosts)}


def privileged_files(tree):
    return list(_PRIVILEGED)


def build(tree, generated=None):
    tree = Path(tree)
    declared_units = units(tree)
    for name, text in sorted((generated or {}).items()):
        declared_units[name] = {"source": "code", **_unit_directives(text)}
    return {
        "version": VERSION,
        "root_actions": root_actions(tree),
        "units": declared_units,
        "secrets": secrets(tree),
        "hub": hub(tree),
        "served": served(tree),
        "outbound": outbound(tree),
        "privileged_files": privileged_files(tree),
    }


def load(tree):
    path = Path(tree) / SECURITY_FILE
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("version") == VERSION else None


def save(tree, manifest):
    (Path(tree) / SECURITY_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def is_privileged(path, manifest):
    return any(fnmatch.fnmatch(path, pattern) for pattern in manifest.get("privileged_files", []))


def _directives(entry):
    return {key: value for key, value in entry.items() if key != "source"}


def _pairs(where, declared, code):
    if declared != code:
        return [f"{where}: the manifest and the code disagree"]
    return []


def mismatches(declared, code):
    problems = []
    if not declared:
        return ["the repository has no security manifest"]
    problems += _pairs("the manifest's version", declared.get("version"), code.get("version"))

    want, have = declared.get("root_actions", {}), code.get("root_actions", {})
    for name in sorted(set(have) - set(want)):
        problems.append(f"the root action {name} is in the code, not in the manifest")
    for name in sorted(set(want) - set(have)):
        problems.append(f"the root action {name} is in the manifest, not in the code")
    for name in sorted(set(want) & set(have)):
        for key in _HUB_ACTIONS:
            if bool(want[name].get(key)) != bool(have[name].get(key)):
                problems.append(f"the root action {name}: {key} does not match the code")

    want, have = declared.get("units", {}), code.get("units", {})
    generated = {name for name, entry in want.items() if entry.get("source") == "code"}
    for name in sorted(set(have) - set(want)):
        problems.append(f"the unit {name} is in the code, not in the manifest")
    for name in sorted(set(want) - set(have) - generated):
        problems.append(f"the unit {name} is in the manifest, not in the code")
    for name in sorted(set(want) & set(have)):
        if want[name].get("source", "install.sh") != "install.sh":
            continue
        if _directives(want[name]) != _directives(have[name]):
            problems.append(f"the unit {name}'s directives do not match the code")

    want, have = declared.get("secrets", {}), code.get("secrets", {})
    for path in sorted(set(want) - set(have)):
        problems.append(f"the secret {path} is in the manifest, not in the code")
    for path in sorted(set(have)):
        if path not in want:
            problems.append(f"the secret {path} is in the code, not in the manifest")
            continue
        for key in ("owner", "group", "mode"):
            a, b = want[path].get(key), have[path].get(key)
            if a is not None and b is not None and a != b:
                problems.append(f"the secret {path}: {key} is {a} in the manifest, {b} in install.sh")

    want, have = declared.get("hub", {}), code.get("hub", {})
    for group in sorted(set(have.get("groups", [])) - set(want.get("groups", []))):
        problems.append(f"the hub user is added to the group {group}, which the manifest does not declare")
    for group in sorted(set(want.get("groups", [])) - set(have.get("groups", []))):
        problems.append(f"the manifest gives the hub user the group {group}, which the code does not")

    want, have = declared.get("served", {}), code.get("served", {})
    for server in sorted(set(have) | set(want)):
        a, b = want.get(server, {}), have.get(server, {})
        for key in ("locations", "roots"):
            gone = sorted(set(b.get(key, [])) - set(a.get(key, [])))
            extra = sorted(set(a.get(key, [])) - set(b.get(key, [])))
            for item in gone:
                problems.append(f"{server} {key[:-1]} {item} is in the code, not in the manifest")
            for item in extra:
                problems.append(f"{server} {key[:-1]} {item} is in the manifest, not in the code")

    want, have = declared.get("outbound", {}), code.get("outbound", {})
    for host in sorted(set(have) - set(want)):
        problems.append(f"the host {host} is in the code, not in the manifest")
    for host in sorted(set(want) - set(have)):
        problems.append(f"the host {host} is in the manifest, not in the code")

    if set(declared.get("privileged_files", [])) != set(code.get("privileged_files", [])):
        problems.append("the privileged files do not match the manifest")

    return problems


def _path_set(value):
    return set(value or [])


def _widening(items, toggle, text):
    items.append({"kind": "widening", "toggle": toggle, "text": text})


def _narrowing(items, text):
    items.append({"kind": "narrowing", "toggle": "", "text": text})


def _neutral(items, text):
    items.append({"kind": "neutral", "toggle": "", "text": text})


_RANK = {
    "ProtectSystem": {"": 0, "no": 0, "yes": 1, "full": 2, "strict": 3},
    "ProtectHome": {"": 0, "no": 0, "tmpfs": 1, "read-only": 2, "yes": 3},
    "ProtectProc": {"": 0, "default": 0, "hidden": 1, "invisible": 2},
}
_FLAGS = ("PrivateTmp", "NoNewPrivileges", "PrivateDevices", "DynamicUser")


def _unit_changes(name, old, new, widenings, narrowings, neutral):
    for flag in _FLAGS:
        a = (old.get(flag) or "").lower() == "yes"
        b = (new.get(flag) or "").lower() == "yes"
        if a and not b:
            _widening(widenings, "sandboxes", f"the unit {name} no longer sets {flag}")
        elif b and not a:
            _narrowing(narrowings, f"the unit {name} now sets {flag}")
    for directive, ranks in _RANK.items():
        a = ranks.get((old.get(directive) or "").lower(), 0)
        b = ranks.get((new.get(directive) or "").lower(), 0)
        if b < a:
            _widening(widenings, "sandboxes", f"the unit {name}'s {directive} is weaker")
        elif b > a:
            _narrowing(narrowings, f"the unit {name}'s {directive} is stricter")
    a, b = old.get("User", "root"), new.get("User", "root")
    if a != b:
        if b == "root" and a != "root":
            _widening(widenings, "sandboxes", f"the unit {name} now runs as root")
        elif a == "root" and b != "root":
            _narrowing(narrowings, f"the unit {name} no longer runs as root")
        else:
            _neutral(neutral, f"the unit {name} runs as {b}, not {a}")
    for directive in ("ReadWritePaths", "BindPaths", "StateDirectory", "LogsDirectory", "CacheDirectory"):
        added = _path_set(new.get(directive)) - _path_set(old.get(directive))
        removed = _path_set(old.get(directive)) - _path_set(new.get(directive))
        for path in sorted(added):
            _widening(widenings, "sandboxes", f"the unit {name} may write {path}")
        for path in sorted(removed):
            _narrowing(narrowings, f"the unit {name} may no longer write {path}")
    added = _path_set(new.get("ReadOnlyPaths")) - _path_set(old.get("ReadOnlyPaths"))
    removed = _path_set(old.get("ReadOnlyPaths")) - _path_set(new.get("ReadOnlyPaths"))
    for path in sorted(removed):
        _widening(widenings, "sandboxes", f"the unit {name} may now write {path}")
    for path in sorted(added):
        _narrowing(narrowings, f"the unit {name} now keeps {path} read-only")
    added = _path_set(new.get("CapabilityBoundingSet")) - _path_set(old.get("CapabilityBoundingSet"))
    removed = _path_set(old.get("CapabilityBoundingSet")) - _path_set(new.get("CapabilityBoundingSet"))
    for cap in sorted(added):
        _widening(widenings, "sandboxes", f"the unit {name} gains the capability {cap}")
    for cap in sorted(removed):
        _narrowing(narrowings, f"the unit {name} loses the capability {cap}")


def compare(old, new, changed=()):
    widenings, narrowings, neutral = [], [], []
    if not old:
        _widening(widenings, "root_code", "the installed box has no security manifest to compare against")
        return {"widenings": widenings, "narrowings": narrowings, "neutral": neutral}

    a, b = old.get("root_actions", {}), new.get("root_actions", {})
    for name in sorted(set(b) - set(a)):
        _widening(widenings, "root_actions", f"a new root action, {name}")
    for name in sorted(set(a) - set(b)):
        _narrowing(narrowings, f"the root action {name} is gone")
    for name in sorted(set(a) & set(b)):
        if a[name].get("https_only") and not b[name].get("https_only"):
            _widening(widenings, "root_actions", f"the root action {name} may now be used over plain HTTP")
        if not a[name].get("https_only") and b[name].get("https_only"):
            _narrowing(narrowings, f"the root action {name} now needs HTTPS")
        if a[name].get("policy") and not b[name].get("policy"):
            _widening(widenings, "root_actions", f"the root action {name} no longer needs the console")
        if not a[name].get("policy") and b[name].get("policy"):
            _narrowing(narrowings, f"the root action {name} now needs the console")
        for path in sorted(_path_set(b[name].get("writes")) - _path_set(a[name].get("writes"))):
            _widening(widenings, "root_actions", f"the root action {name} may now write {path}")
        for path in sorted(_path_set(b[name].get("reads")) - _path_set(a[name].get("reads"))):
            _widening(widenings, "root_actions", f"the root action {name} may now read {path}")
        for path in sorted(_path_set(a[name].get("writes")) - _path_set(b[name].get("writes"))):
            _narrowing(narrowings, f"the root action {name} may no longer write {path}")
        for path in sorted(_path_set(a[name].get("reads")) - _path_set(b[name].get("reads"))):
            _narrowing(narrowings, f"the root action {name} may no longer read {path}")

    a, b = old.get("units", {}), new.get("units", {})
    for name in sorted(set(b) - set(a)):
        _widening(widenings, "sandboxes", f"a new unit, {name}")
    for name in sorted(set(a) - set(b)):
        _narrowing(narrowings, f"the unit {name} is gone")
    for name in sorted(set(a) & set(b)):
        _unit_changes(name, _directives(a[name]), _directives(b[name]), widenings, narrowings, neutral)

    a, b = old.get("secrets", {}), new.get("secrets", {})
    for path in sorted(set(b) - set(a)):
        _widening(widenings, "secrets", f"a new secret, {path}")
    for path in sorted(set(a) - set(b)):
        _narrowing(narrowings, f"the secret {path} is gone")
    for path in sorted(set(a) & set(b)):
        for key in ("owner", "group"):
            if a[path].get(key) != b[path].get(key):
                _widening(
                    widenings,
                    "secrets",
                    f"{path}'s {key} is now {b[path].get(key)}, not {a[path].get(key)}",
                )
        old_mode = int(str(a[path].get("mode", "0")), 8)
        new_mode = int(str(b[path].get("mode", "0")), 8)
        if new_mode & ~old_mode:
            _widening(
                widenings, "secrets", f"{path} is more permissive: {b[path].get('mode')} from {a[path].get('mode')}"
            )
        elif old_mode & ~new_mode:
            _narrowing(narrowings, f"{path} is less permissive: {b[path].get('mode')} from {a[path].get('mode')}")
        for reader in sorted(_path_set(b[path].get("readers")) - _path_set(a[path].get("readers"))):
            _widening(widenings, "secrets", f"{path} is now readable by {reader}")
        for reader in sorted(_path_set(a[path].get("readers")) - _path_set(b[path].get("readers"))):
            _narrowing(narrowings, f"{path} is no longer readable by {reader}")

    a, b = old.get("hub", {}), new.get("hub", {})
    for group in sorted(_path_set(b.get("groups")) - _path_set(a.get("groups"))):
        _widening(widenings, "secrets", f"the hub user joins the group {group}")
    for path in sorted(_path_set(b.get("secrets")) - _path_set(a.get("secrets"))):
        _widening(widenings, "secrets", f"the hub user may read {path}")

    a, b = old.get("served", {}), new.get("served", {})
    for server in sorted(set(a) | set(b)):
        x, y = a.get(server, {}), b.get(server, {})
        for key, word in (("locations", "serves"), ("roots", "serves files from")):
            for item in sorted(_path_set(y.get(key)) - _path_set(x.get(key))):
                _widening(widenings, "served", f"{server} {word} {item}")
            for item in sorted(_path_set(x.get(key)) - _path_set(y.get(key))):
                _narrowing(narrowings, f"{server} no longer {word} {item}")

    a, b = old.get("outbound", {}), new.get("outbound", {})
    for host in sorted(set(b) - set(a)):
        _widening(widenings, "outbound", f"the box may contact {host}")
    for host in sorted(set(a) - set(b)):
        _narrowing(narrowings, f"the box may no longer contact {host}")

    a, b = set(old.get("privileged_files", [])), set(new.get("privileged_files", []))
    for pattern in sorted(a - b):
        _widening(widenings, "root_code", f"the manifest no longer treats {pattern} as privileged")
    for pattern in sorted(b - a):
        _narrowing(narrowings, f"the manifest now treats {pattern} as privileged")

    for path in sorted(changed):
        if is_privileged(path, new):
            _widening(widenings, "root_code", f"root code changed: {path}")

    return {"widenings": widenings, "narrowings": narrowings, "neutral": neutral}


def held(widenings, levels):
    return [item for item in widenings if levels.get(item["toggle"], "authorise") != "flag"]
