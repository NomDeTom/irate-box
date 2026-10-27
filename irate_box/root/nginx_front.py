# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Target-release nginx rendering and private, versioned preparation bundles."""

import contextlib
import fcntl
import functools
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import ssl
import stat
import subprocess  # Fixed argv, trusted executable search and no shell. # nosec B404
import sys
import tempfile
import threading
import time
from pathlib import Path

PROTOCOL = 1
TOKENS = re.compile(r"@([A-Z_]+)@")
PORTS = {
    "PORT": "80",
    "ADDON_PORT": "8090",
    "NOTES_PORT": "8091",
    "NOTEBOOK_PORT": "8094",
    "ADMIN_PORT": "8095",
    "DRAW_PORT": "8096",
    "MERMAID_PORT": "8097",
    "SYNC_PORT": "8098",
    "WIKI_PORT": "8092",
    "GIT_PORT": "8093",
    "TLS_PORT": "443",
    "ADDON_TLS_PORT": "8490",
    "NOTES_TLS_PORT": "8491",
    "NOTEBOOK_TLS_PORT": "8494",
    "ADMIN_TLS_PORT": "8495",
    "DRAW_TLS_PORT": "8496",
    "MERMAID_TLS_PORT": "8497",
    "SYNC_TLS_PORT": "8498",
    "WIKI_TLS_PORT": "8492",
    "GIT_TLS_PORT": "8493",
}
_local = threading.local()
_mutex = threading.RLock()


@contextlib.contextmanager
def front_lock(blocking=True):
    with _mutex:
        if getattr(_local, "held", False):
            yield
            return
        path = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub")) / ".front.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        inherited = os.environ.get("HUB_FRONT_LOCK_FD", "")
        descriptor = None
        if inherited.isdecimal():
            try:
                candidate = int(inherited)
                if os.path.samestat(os.fstat(candidate), path.stat()):
                    descriptor = candidate
            except OSError:
                pass
        own = descriptor is None
        if own:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            _local.held = True
            yield
        finally:
            _local.held = False
            if own:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)


def locked(function):
    @functools.wraps(function)
    def invoke(*args, **kwargs):
        with front_lock():
            return function(*args, **kwargs)

    return invoke


def _sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _journal(directory, data):
    directory = Path(directory)
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as output:
        json.dump(data, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(output.name, directory / "journal.json")
    _sync_directory(directory)


def _copy_owned(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    elif source.is_file():
        shutil.copy2(source, destination)
    else:
        raise ValueError("Unsupported nginx transaction file type")
    paths = [source, *source.rglob("*")] if source.is_dir() and not source.is_symlink() else [source]
    for path in paths:
        target = destination / path.relative_to(source) if path != source else destination
        metadata = path.lstat()
        os.chown(target, metadata.st_uid, metadata.st_gid, follow_symlinks=False)
        if stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode):
            descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def _private(directory):
    directory = Path(directory)
    if (
        any(path.is_symlink() for path in (directory, *directory.parents))
        or directory.stat().st_uid != os.geteuid()
        or directory.stat().st_mode & 0o077
    ):
        raise ValueError("Unsafe nginx transaction directory")
    return directory


def transaction_state(directory):
    directory = _private(directory)
    journal = directory / "journal.json"
    if journal.is_symlink():
        raise ValueError("Unsafe nginx transaction journal")
    return json.loads(journal.read_text())


def begin(directory, paths, runner=None):
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _private(directory)
    if (directory / "journal.json").exists():
        recover(directory)
    snapshot = directory / "previous"
    if snapshot.exists():
        shutil.rmtree(snapshot)
    snapshot.mkdir(mode=0o700)
    entries = []
    for index, value in enumerate(paths):
        path = Path(value)
        if not path.is_absolute() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError("Unsafe nginx transaction destination")
        exists = path.exists() or path.is_symlink()
        if exists:
            _copy_owned(path, snapshot / str(index))
        entries.append({"path": str(path), "exists": exists, "snapshot": str(index)})
    _sync_directory(snapshot)
    previous_values = settings_from_env()
    ports = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub")) / "tls/ports.json"
    if ports.exists():
        saved = json.loads(ports.read_text())
        for origin, prefix in (
            ("main", ""),
            ("addons", "ADDON_"),
            ("notes", "NOTES_"),
            ("wiki", "WIKI_"),
            ("git", "GIT_"),
            ("notebook", "NOTEBOOK_"),
            ("admin", "ADMIN_"),
            ("draw", "DRAW_"),
            ("mermaid", "MERMAID_"),
            ("sync", "SYNC_"),
        ):
            for scheme, suffix in (("http", "PORT"), ("https", "TLS_PORT")):
                if origin in saved.get(scheme, {}):
                    previous_values[prefix + suffix] = str(saved[scheme][origin])
    running = runner("systemctl", "is-active", "--quiet", "nginx").returncode == 0 if runner else None
    _journal(
        directory,
        {
            "protocol": PROTOCOL,
            "state": "prepared",
            "entries": entries,
            "previous_values": previous_values,
            "was_running": running,
        },
    )


def recover(directory, finalize=True):
    data = transaction_state(directory)
    if data.get("protocol") != PROTOCOL:
        raise ValueError("Unsupported nginx transaction protocol")
    if data["state"] in ("committed", "restored"):
        return False
    _journal(directory, dict(data, state="rollback-needed"))
    for entry in data["entries"]:
        path = Path(entry["path"])
        if not path.is_absolute() or any(parent.is_symlink() for parent in path.parents):
            raise ValueError("Unsafe nginx rollback destination")
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        if entry["exists"]:
            _copy_owned(Path(directory) / "previous" / entry["snapshot"], path)
        if path.parent.exists():
            _sync_directory(path.parent)
    _journal(directory, dict(data, state="restored" if finalize else "rollback-needed"))
    return True


def fingerprint(path):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return None
    digest = hashlib.sha256()
    paths = [path, *sorted(path.rglob("*"))] if path.is_dir() and not path.is_symlink() else [path]
    for item in paths:
        metadata = item.lstat()
        digest.update(
            json.dumps([str(item.relative_to(path)), metadata.st_mode, metadata.st_uid, metadata.st_gid]).encode()
        )
        if item.is_symlink():
            digest.update(os.readlink(item).encode())
        elif item.is_file():
            digest.update(hashlib.sha256(item.read_bytes()).digest())
        if stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode):
            descriptor = os.open(item, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    _sync_directory(path.parent)
    return digest.hexdigest()


def seal(directory, bundle, destination):
    manifest, site = read_bundle(bundle)
    if Path(destination).is_symlink() or Path(destination).read_bytes() != site.read_bytes():
        raise ValueError("The installed nginx site differs from its prepared bundle")
    data = transaction_state(directory)
    if data["state"] != "prepared":
        raise ValueError("No prepared nginx transaction")
    data["candidate"] = {entry["path"]: fingerprint(entry["path"]) for entry in data["entries"]}
    data["bundle"] = str(Path(bundle).absolute())
    data["generation"] = manifest["generation"]
    _journal(directory, data)


def system(*argv):
    if argv[0] not in ("nginx", "systemctl"):
        raise ValueError("Unexpected frontend command")
    executable = shutil.which(argv[0], path="/usr/sbin:/usr/bin:/sbin:/bin")
    if not executable:
        raise FileNotFoundError(argv[0])
    return subprocess.run(  # noqa: TID251
        [executable, *argv[1:]],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"},
    )  # Reviewed fixed command family; no shell. # nosec B603


def _health_once(settings):
    from irate_box.root import tls

    policy = tls.settings()
    secure = policy["transport"] != "http"
    context = None
    if secure:
        context = ssl.create_default_context(cafile=str(tls.CHAIN))
        context.check_hostname = False
        context.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    port = int(settings["TLS_PORT"] if secure else settings["PORT"])
    connection = (
        http.client.HTTPSConnection("127.0.0.1", port, timeout=10, context=context)
        if secure
        else http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    )
    try:
        connection.connect()
        if secure:
            certificate = tls.CHAIN.read_text().split("-----END CERTIFICATE-----", 1)[0] + "-----END CERTIFICATE-----\n"
            if connection.sock.getpeercert(binary_form=True) != ssl.PEM_cert_to_DER_cert(certificate):
                raise ValueError("The frontend served an unexpected certificate")

        def request(path):
            connection.request("GET", path, headers={"Host": "localhost"})
            response = connection.getresponse()
            return response.status, response.read(262144)

        status, body = request("/")
        if status != 200 or b"<html" not in body.lower():
            raise ValueError("The frontend did not serve the hub page")
        if settings.get("GENERATION"):
            status, body = request("/_irate_front_ready")
            if status != 200 or body.decode().strip() != settings["GENERATION"]:
                raise ValueError("nginx has not activated the prepared generation")
        status, body = request("/status")
        payload = json.loads(body)
        if status != 200 or payload.get("proxied") is not True or "version" not in payload:
            raise ValueError("The frontend did not reach the hub backend")
        if not Path(settings["UNCLAIMED"]).exists():
            status, body = request("/admin/")
            if status not in (301, 302, 401, 403):
                raise ValueError("Anonymous administration was not refused")
    finally:
        connection.close()
    if settings.get("GENERATION"):
        for prefix in ("ADDON_", "NOTES_", "NOTEBOOK_", "WIKI_", "GIT_", "ADMIN_", "DRAW_", "MERMAID_", "SYNC_"):
            port = int(settings[prefix + ("TLS_PORT" if secure else "PORT")])
            sibling = (
                http.client.HTTPSConnection("127.0.0.1", port, timeout=3, context=context)
                if secure
                else http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            )
            try:
                sibling.request("GET", "/_irate_front_ready", headers={"Host": "localhost"})
                response = sibling.getresponse()
                if response.status != 200 or response.read(256).decode().strip() != settings["GENERATION"]:
                    raise ValueError("An nginx app origin has not activated the prepared generation")
            finally:
                sibling.close()


def health(settings):
    deadline = time.monotonic() + 20
    while True:
        try:
            _health_once(settings)
            return
        except (OSError, ValueError, http.client.HTTPException):
            if time.monotonic() >= deadline:
                raise
            threading.Event().wait(0.25)


def rollback(directory, runner=system, probe=health):
    data = transaction_state(directory)
    if data["state"] in ("committed", "restored"):
        return False
    try:
        recover(directory, finalize=False)
        _journal(directory, dict(data, state="recovery-required"))
        if runner("nginx", "-t", "-q").returncode:
            raise ValueError("The restored nginx configuration did not validate")
        operation = "reload-or-restart" if data.get("was_running") is not False else "stop"
        if runner("systemctl", operation, "nginx").returncode:
            raise ValueError("The restored nginx service could not be activated")
        if data.get("was_running") is not False:
            probe(data["previous_values"])
    except (OSError, ValueError, subprocess.SubprocessError, http.client.HTTPException):
        _journal(directory, dict(data, state="recovery-required"))
        raise ValueError("Frontend rollback needs console recovery; the private journal is " + str(directory)) from None
    _journal(directory, dict(data, state="restored"))
    return True


def finish(directory, bundle, values, runner=system, probe=health):
    manifest, _site = read_bundle(bundle)
    if manifest["settings"] != values:
        raise ValueError("Frontend settings changed after preparation")
    data = transaction_state(directory)
    if data["state"] != "prepared":
        raise ValueError("No prepared nginx transaction")
    if data.get("bundle") != str(Path(bundle).absolute()) or not data.get("candidate"):
        raise ValueError("The nginx candidate has not been sealed")
    _journal(directory, dict(data, state="activating"))
    try:
        if any(fingerprint(path) != expected for path, expected in data["candidate"].items()):
            raise ValueError("Frontend files changed after preparation")
        tested = runner("nginx", "-t", "-q")
        if tested.returncode:
            raise ValueError("The complete nginx configuration failed validation")
        running = runner("systemctl", "is-active", "--quiet", "nginx").returncode == 0
        activated = runner("systemctl", "reload" if running else "start", "nginx")
        if activated.returncode:
            raise ValueError("nginx activation failed")
        probe(dict(values, GENERATION=data["generation"]))
    except (OSError, ValueError, subprocess.SubprocessError, http.client.HTTPException):
        rollback(directory, runner, probe)
        raise
    _journal(directory, dict(data, state="committed"))


def settings_from_env():
    code = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
    state = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
    etc = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
    values = {name: os.environ.get(f"HUB_{name}", default) for name, default in PORTS.items()}
    values.update(
        {
            "STATIC": str(code / "web"),
            "APPS": os.environ.get("HUB_APPS_DIR", "/usr/share/hub/apps"),
            "GIT_ROOT": str(state / "git"),
            "FIRMWARE": str(state / "firmware"),
            "HTPASSWD": os.environ.get("HUB_NGINX_LOGINS", "/etc/nginx/irate-box.htpasswd"),
            "UNCLAIMED": os.environ.get("HUB_UNCLAIMED_FILE", "/etc/nginx/irate-box-unclaimed"),
            "ACCESS": str(etc / "nginx-access.conf"),
            "FRONT": os.environ.get("HUB_NGINX_FRONT", "/etc/nginx/irate-box-front.conf"),
            "VIA": os.environ.get("HUB_NGINX_VIA", "/etc/nginx/irate-box-via.conf"),
            "ADDONS": str(state / "addons"),
            "ADDON_ACCESS": str(etc / "nginx-addons.conf"),
            "ADDON_GATES": str(etc / "nginx-addon-gates.conf.d"),
            "TLS": str(etc / "tls/front"),
            # In RAM (/run): a line a page load, read by the hub every five minutes; a reboot starts it empty.
            "PAGELOG": os.environ.get("HUB_PAGELOG", "/run/irate-box/pagelog/pages.log"),
        }
    )
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
    if (
        any(path.is_symlink() for path in (parent, *parent.parents))
        or parent.stat().st_uid != os.geteuid()
        or parent.stat().st_mode & 0o077
    ):
        raise ValueError("nginx staging requires a private directory owned by the installer")
    text = render(Path(template).read_text(), values, ipv6)
    generation = hashlib.sha256((identity + text + secrets.token_hex(16)).encode()).hexdigest()
    text = re.sub(
        r"(?m)^server \{",
        lambda match: (
            match[0]
            + '\n\tlocation = /_irate_front_ready { default_type text/plain; return 200 "'
            + generation
            + '\\n"; }'
        ),
        text,
    )
    bundle = Path(tempfile.mkdtemp(prefix="nginx-", dir=parent))
    site = bundle / "site.conf"
    site.write_text(text)
    site.chmod(0o600)
    manifest = {
        "protocol": PROTOCOL,
        "source": identity,
        "generation": generation,
        "template_sha256": hashlib.sha256(Path(template).read_bytes()).hexdigest(),
        "settings": values,
        "ipv6": ipv6,
        "files": {"site.conf": hashlib.sha256(text.encode()).hexdigest()},
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    (bundle / "manifest.json").chmod(0o600)
    return bundle


def read_bundle(bundle):
    bundle = _private(bundle)
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Unsafe nginx manifest")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("protocol") != PROTOCOL or set(manifest.get("files", {})) != {"site.conf"}:
        raise ValueError("Unsupported nginx bundle protocol or file set")
    site = bundle / "site.conf"
    if (
        site.is_symlink()
        or not site.is_file()
        or hashlib.sha256(site.read_bytes()).hexdigest() != manifest["files"]["site.conf"]
    ):
        raise ValueError("The prepared nginx site changed")
    return manifest, site


def main():
    command, *args = sys.argv[1:]
    values = settings_from_env()
    if command == "recover-if-idle" and len(args) == 1:
        if (Path(args[0]) / "journal.json").exists():
            try:
                with front_lock(blocking=False):
                    recover(args[0])
            except BlockingIOError:
                pass
    elif command == "begin" and len(args) >= 2:
        with front_lock():
            directory = Path(args[0])
            if (directory / "journal.json").exists():
                rollback(directory)
            begin(directory, args[1:], runner=system)
    elif command == "rollback" and len(args) == 1:
        with front_lock():
            rollback(args[0])
    elif command == "recover" and len(args) == 1:
        with front_lock():
            recover(args[0])
    elif command == "seal" and len(args) == 3:
        with front_lock():
            seal(*args)
    elif command == "finish" and len(args) == 2:
        with front_lock():
            finish(args[0], args[1], values)
    elif command == "render" and len(args) == 1:
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
