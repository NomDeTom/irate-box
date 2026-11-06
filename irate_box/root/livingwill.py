# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The living will: the administrator's instructions to the rescue (C), written on the box while the
box is healthy.

The box keeps the master, its signers file and its own Ed25519 key under
/var/lib/irate-box-root/.  A copy goes into the NAND's state volume and onto the rescue USB
stick; C reads either copy.  The signature is made with ssh-keygen -Y sign over one canonical
JSON object, so C can verify it with the same tool.  It proves the file came from this box's
writer and was not damaged; it is not a proof of who on the box asked for it.

The settings are the plan's D1-D11 split into instructions: what C may do without asking, how
to restore the card, where findings and copies go, how C is reached, which roles are on, and
who confirms a destructive step.  The defaults are the safe ones: read-only triage and the
validated GPT repair only.

Stdlib only, plus ssh-keygen.
"""

import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404
import tempfile
import time
from pathlib import Path

from irate_box.msg import M, Refused
from irate_box.root import safeio

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CONTROL = STATE / "control"
ROOT = safeio.ROOT_STATE
MASTER = ROOT / "living-will.json"
SIGNERS = ROOT / "living-will-signers"
KEY = ROOT / "living-will-key"
PUBLIC = CONTROL / "living-will.json"
NAND_STATE = Path(os.environ.get("HUB_NAND_STATE", "/run/irate-box-nand-state"))
USB_STICK = Path(os.environ.get("HUB_RESCUE_USB", "/media/usb"))
NAND_COPY = NAND_STATE / "irate"
USB_COPY = USB_STICK / "irate"
IMAGES = STATE / "rk-images"
FORMAT = "irate-living-will"
VERSION = 1
NAMESPACE = "irate-living-will"
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PASSWORD_LENGTH = 20

DEFAULTS = {
    "auto": {"triage": True, "repair_gpt": True, "repair_fs": False, "reflash": False},
    "restore": {
        "method": "none",
        "other_slot": "auto",
        "image": {"source": "usb", "name": "", "url": "", "sha256": ""},
        "salvage": {"source": "usb", "name": ""},
    },
    "where": {"findings": "both", "backups": "usb", "salvage": "usb"},
    "reach": {
        "hotspot": True,
        "ssid": "irate-rescue",
        "password": "",  # nosec B105 - an empty default, not a secret
        "join_saved": True,
        "ssh": False,
    },
    "roles": {
        "boot": True,
        "fsck": True,
        "health": True,
        "salvage": True,
        "reflash": True,
        "reachability": True,
    },
    "consent": "page",
    "after": {"reboot_to_card": "ask"},
}

ENUMS = {
    ("restore", "method"): ("none", "other-slot", "image", "salvage"),
    ("restore", "other_slot"): ("auto", "a", "b"),
    ("restore", "image", "source"): ("usb", "box", "url"),
    ("restore", "salvage", "source"): ("usb", "nand"),
    ("where", "findings"): ("usb", "nand", "both"),
    ("where", "backups"): ("usb", "nand"),
    ("where", "salvage"): ("usb", "nand"),
    ("consent",): ("page", "ssh", "console"),
    ("after", "reboot_to_card"): ("ask", "never", "always"),
}


def defaults():
    return copy.deepcopy(DEFAULTS)


def _merge(value, default, path=()):
    if isinstance(default, dict):
        if not isinstance(value, dict):
            raise Refused(
                M(
                    "living-will.value",
                    "The living will has a value this box cannot use: {what}.",
                    what=".".join(path) or "instructions",
                )
            )
        return {
            key: _merge(value.get(key, copy.deepcopy(child)), child, path + (key,)) for key, child in default.items()
        }
    if isinstance(default, bool):
        if type(value) is not bool:
            raise _bad(path)
        return value
    if isinstance(default, str):
        if not isinstance(value, str):
            raise _bad(path)
        if path in ENUMS and value not in ENUMS[path]:
            raise _bad(path)
        if path == ("reach", "ssid") and not 1 <= len(value) <= 32:
            raise _bad(path)
        if path == ("reach", "password") and value and not 8 <= len(value) <= 63:
            raise _bad(path)
        if path == ("restore", "image", "sha256") and value and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise _bad(path)
        if path == ("restore", "image", "url") and value and not value.startswith("https://"):
            raise _bad(path)
        if (
            path == ("restore", "image", "name")
            and value
            and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,200}", value)
        ):
            raise _bad(path)
        if (
            path == ("restore", "salvage", "name")
            and value
            and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", value)
        ):
            raise _bad(path)
        return value
    return value


def _bad(path):
    return Refused(
        M("living-will.value", "The living will has a value this box cannot use: {what}.", what=".".join(path))
    )


def merge(value):
    return _merge(value or {}, DEFAULTS)


def canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def signed_part(data):
    return {k: data[k] for k in ("format", "version", "written", "box", "key", "instructions")}


def _principal():
    return f"irate-living-will@{_box_id()}"


def _box_id():
    try:
        return Path("/etc/machine-id").read_text().strip() or "box"
    except OSError:
        return "box"


def _box_info():
    serial = ""
    for path in (Path("/proc/device-tree/serial-number"), Path("/sys/firmware/devicetree/base/serial-number")):
        try:
            serial = path.read_bytes().rstrip(b"\0\n").decode(errors="replace")
            break
        except OSError:
            continue
    return {"id": _box_id(), "name": socket.gethostname(), "serial": serial[:64]}


def _ssh_keygen():
    tool = shutil.which("ssh-keygen")
    if not tool:
        raise Refused(
            M("living-will.no-ssh", "The ssh-keygen tool is not installed, so the box cannot sign the living will.")
        )
    return tool


def _ensure_key():
    if KEY.exists():
        return
    tool = _ssh_keygen()
    safeio.mkdir(ROOT, mode=0o700)
    result = subprocess.run(  # noqa: TID251 - fixed argv, ssh-keygen
        [tool, "-q", "-t", "ed25519", "-N", "", "-C", _principal(), "-f", str(KEY)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise Refused(
            M(
                "living-will.key-failed",
                "The box's signing key could not be made: {why}.",
                why=(result.stderr or result.stdout).strip()[-200:],
            )
        )
    try:
        os.chmod(KEY, 0o600)
        os.chmod(Path(str(KEY) + ".pub"), 0o644)
    except OSError:
        pass


def _public_key():
    try:
        return Path(str(KEY) + ".pub").read_text().strip()
    except OSError:
        tool = _ssh_keygen()
        result = subprocess.run([tool, "-y", "-f", str(KEY)], capture_output=True, text=True, timeout=60)  # noqa: TID251
        if result.returncode:
            raise Refused(M("living-will.key-read", "The box's signing key cannot be read.")) from None
        return f"{result.stdout.strip()} {_principal()}"


def _sign(payload):
    tool = _ssh_keygen()
    with tempfile.TemporaryDirectory(prefix="living-will-") as tmp:
        source = Path(tmp) / "payload"
        source.write_bytes(canonical(payload))
        result = subprocess.run(  # noqa: TID251 - fixed argv, ssh-keygen
            [tool, "-Y", "sign", "-f", str(KEY), "-n", NAMESPACE, str(source)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode:
            raise Refused(
                M(
                    "living-will.sign-failed",
                    "The living will could not be signed: {why}.",
                    why=(result.stderr or result.stdout).strip()[-200:],
                )
            )
        return Path(str(source) + ".sig").read_text()


def _verify(data):
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise Refused(M("living-will.file-invalid", "The living will on the box is not an irate living will."))
    if data.get("version") != VERSION:
        raise Refused(
            M(
                "living-will.file-version",
                "The living will is format version {version}, and this box reads version {mine}.",
                version=data.get("version"),
                mine=VERSION,
            )
        )
    if not SIGNERS.is_file():
        raise Refused(M("living-will.no-signers", "The living will's signers file is missing."))
    tool = _ssh_keygen()
    principal = (data.get("signature") or {}).get("principal")
    armored = (data.get("signature") or {}).get("armored")
    if not isinstance(principal, str) or not isinstance(armored, str):
        raise Refused(M("living-will.file-invalid", "The living will on the box is not an irate living will."))
    instructions = merge(data.get("instructions"))
    payload = {**signed_part(data), "instructions": instructions}
    with tempfile.TemporaryDirectory(prefix="living-will-") as tmp:
        signature = Path(tmp) / "will.sig"
        signature.write_text(armored)
        result = subprocess.run(  # noqa: TID251 - fixed argv, ssh-keygen
            [tool, "-Y", "verify", "-f", str(SIGNERS), "-I", principal, "-n", NAMESPACE, "-s", str(signature)],
            input=canonical(payload),
            capture_output=True,
            timeout=60,
        )
    if result.returncode:
        raise Refused(M("living-will.bad-signature", "The living will's signature does not match."))
    return instructions, data


def _fingerprint():
    try:
        result = subprocess.run(  # noqa: TID251 - fixed argv, ssh-keygen
            ["ssh-keygen", "-lf", "-"], input=_public_key() + "\n", capture_output=True, text=True, timeout=30
        )
        parts = result.stdout.split()
        return parts[1] if len(parts) > 1 else ""
    except (OSError, subprocess.SubprocessError, Refused):
        return ""


def _copies():
    try:
        master = MASTER.read_bytes()
    except OSError:
        master = None
    out = {}
    for name, folder in (("nand", NAND_COPY), ("usb", USB_COPY)):
        path = folder / "living-will.json"
        try:
            data = path.read_bytes()
        except OSError:
            out[name] = "missing"
            continue
        out[name] = "match" if master is not None and data == master else "stale"
    return out


def public(instructions, data=None, valid=True, source="master"):
    shown = copy.deepcopy(instructions)
    password = shown.get("reach", {}).get("password", "")
    shown.setdefault("reach", {})["password"] = ""  # nosec B105 - blanking the password for the hub's copy, not a secret
    return {
        "valid": valid,
        "version": data.get("version") if data else VERSION,
        "written": data.get("written") if data else None,
        "source": source,
        "key": {"type": "ssh-ed25519", "fingerprint": _fingerprint()} if data else None,
        "password_set": bool(password),
        "instructions": shown,
        "defaults": defaults(),
        "copies": _copies(),
    }


def _publish(view):
    try:
        safeio.mkdir(CONTROL, mode=0o750)
        safeio.write(PUBLIC, json.dumps(view, indent=2) + "\n")
    except OSError:
        pass


def show():
    """The public projection the settings page reads. Never raises for a will that cannot be read."""
    if not MASTER.is_file():
        view = public(defaults(), None, valid=False, source="defaults")
        _publish(view)
        return view
    try:
        data = json.loads(MASTER.read_text())
        instructions, data = _verify(data)
        view = public(instructions, data)
        _publish(view)
        return view
    except (OSError, ValueError, Refused) as exc:
        view = public(defaults(), None, valid=False, source="defaults")
        view["problem"] = str(exc)
        view["problem_id"] = exc.message["id"] if isinstance(exc, Refused) else ""
        _publish(view)
        return view


def save(value):
    """Validate and sign the instructions, keep the master and the public projection, and return the
    view and the password when one was made. It does not write the NAND or the stick; push() does."""
    instructions = merge(value)
    generated = ""
    if not instructions["reach"]["password"]:
        generated = "".join(secrets.choice(ALPHABET) for _ in range(PASSWORD_LENGTH))
        instructions["reach"]["password"] = generated
    _ensure_key()
    public_key = _public_key()
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "written": int(time.time()),
        "box": _box_info(),
        "key": {"type": "ssh-ed25519", "public": public_key},
        "instructions": instructions,
    }
    data = dict(payload)
    data["signature"] = {"namespace": NAMESPACE, "principal": _principal(), "armored": _sign(payload)}
    safeio.mkdir(ROOT, mode=0o700)
    safeio.write(MASTER, json.dumps(data, indent=2) + "\n", mode=0o600)
    safeio.write(SIGNERS, f"{_principal()} {public_key}\n", mode=0o644)
    view = public(instructions, data)
    safeio.mkdir(CONTROL, mode=0o750)
    safeio.write(PUBLIC, json.dumps(view, indent=2) + "\n")
    return view, generated


def _write_copy(folder, data):
    safeio.mkdir(folder, mode=0o700)
    safeio.write(folder / "living-will.json", data, mode=0o600)
    safeio.write(folder / "living-will-signers", SIGNERS.read_text(), mode=0o600)


def push(confirm):
    if confirm != "WRITE WILL":
        raise Refused(
            M(
                "living-will.confirm",
                "Type WRITE WILL to write the living will to the onboard flash and the USB drive.",
            )
        )
    try:
        data = MASTER.read_text()
        _verify(json.loads(data))
    except (OSError, ValueError, Refused) as exc:
        raise Refused(
            M("living-will.master-invalid", "The living will on the box cannot be read: {why}.", why=str(exc))
        ) from exc
    result = {}
    for name, folder in (("nand", NAND_COPY), ("usb", USB_COPY)):
        if not folder.parent.is_dir():
            result[name] = "absent"
            continue
        try:
            _write_copy(folder, data)
            result[name] = "written"
        except OSError:
            result[name] = "failed"
    show()
    return result


def image_problem(instructions):
    """The restore image's problem as {case, summary, why, fix, evidence}, or None. It reads only."""
    restore = instructions.get("restore", {})
    if restore.get("method") != "image":
        return None
    image = restore.get("image", {})
    name = image.get("name", "")
    source = image.get("source", "usb")
    if not name:
        return {
            "case": "none",
            "summary": M(
                "living-will.image.none", "The living will says to restore from an image, but it does not name one."
            ),
            "why": M("living-will.image.none.why", "The rescue would have nothing to write."),
            "fix": M("living-will.image.none.fix", "Select the image in the living will."),
            "evidence": [],
        }
    if not image.get("sha256"):
        return {
            "case": "sha",
            "summary": M(
                "living-will.image.sha",
                "The living will names the image {name} but no checksum, so the rescue cannot verify it.",
                name=name,
            ),
            "why": M("living-will.image.sha.why", "The rescue will not write an image it cannot check."),
            "fix": M("living-will.image.sha.fix", "Save the image's SHA-256 in the living will."),
            "evidence": [],
        }
    if source == "url":
        return None
    folder = USB_STICK if source == "usb" else IMAGES
    path = folder / name
    if not path.is_file():
        return {
            "case": "missing",
            "summary": M(
                "living-will.image.missing",
                "The living will names the image {name}, which is not on the box or the rescue USB drive.",
                name=name,
            ),
            "why": M("living-will.image.missing.why", "The rescue would have nothing to write."),
            "fix": M("living-will.image.missing.fix", "Put the image where the will says, or select another image."),
            "evidence": [str(path)],
        }
    digest = hashlib_sha256(path)
    if digest != image.get("sha256"):
        return {
            "case": "mismatch",
            "summary": M(
                "living-will.image.mismatch",
                "The image {name} does not match the checksum in the living will.",
                name=name,
            ),
            "why": M("living-will.image.mismatch.why", "The rescue would refuse to write it."),
            "fix": M(
                "living-will.image.mismatch.fix",
                "Replace the image, or save the will with the image's new checksum.",
            ),
            "evidence": [digest, image.get("sha256")],
        }
    return None


def salvage_problem(instructions):
    restore = instructions.get("restore", {})
    if restore.get("method") != "salvage":
        return None
    salvage = restore.get("salvage", {})
    name = salvage.get("name", "")
    if not name:
        return {
            "case": "none",
            "summary": M(
                "living-will.salvage.none",
                "The living will says to restore a salvage copy, but it does not name one.",
            ),
            "why": M("living-will.salvage.none.why", "The rescue would have nothing to restore."),
            "fix": M("living-will.salvage.none.fix", "Select a salvage copy in the living will."),
            "evidence": [],
        }
    folder = (USB_STICK if salvage.get("source") == "usb" else NAND_STATE) / "irate-salvage" / name
    if not folder.is_dir():
        return {
            "case": "missing",
            "summary": M(
                "living-will.salvage.missing",
                "The living will names the salvage copy {name}, which is not on the rescue USB drive or the onboard flash.",
                name=name,
            ),
            "why": M("living-will.salvage.missing.why", "The rescue would have nothing to restore."),
            "fix": M("living-will.salvage.missing.fix", "Make the salvage copy, or select another one."),
            "evidence": [str(folder)],
        }
    return None


def hashlib_sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 << 20), b""):
            value.update(chunk)
    return value.hexdigest()
