# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A second copy of the box's settings and keys, and a check of them at every boot.

Box 190's card lost 55 files, each read back whole as 0xFF, with its size and mtime kept. This is the mend the plan
asks for (notes: box-doctor-hardening-plan, Part 2, step 4): a copy of the protected files, taken only from files
that read back right, and a boot check that puts a damaged one back before the network and the hub start.

  irate-box retain sweep             take the copy (the timer, and after an install)
  irate-box retain boot-check        check every protected file, restore what is damaged
  irate-box retain install-units     the boot check and the sweep's timer
  irate-box retain uninstall-units   remove them

A file whose checksum changed while its mtime moved is a change the owner made, not damage: it is left alone and the
next sweep copies it. A file that is missing, reads back all 0xFF or all zero, or changed with its mtime kept, is
damage: the copy is put back with the same code the Housecall doctor uses (restore.from_copy). A repair at two boots
running is the card verdict. Stdlib only.
"""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from irate_box import durable
from irate_box.msg import M, Refused
from irate_box.root import restore

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))
ROOT = Path(os.environ.get("HUB_ROOT_STATE_DIR") or STATE.parent / "irate-box-root")
STORE = ROOT / "protected"
MANIFEST = STORE / "manifest.json"
FINDINGS = ROOT / "protected-findings.json"
SYSTEMD = Path(os.environ.get("HUB_SYSTEMD_ETC", "/etc/systemd")) / "system"
SERVICE = "irate-box-protected.service"
SWEEP_SERVICE = "irate-box-protected-sweep.service"
TIMER = "irate-box-protected.timer"
DAMAGED = STORE / "damaged"
STREAK = ROOT / "protected-streak.json"
NGINX = Path(os.environ.get("HUB_NGINX_CONF_DIR", "/etc/nginx"))
NM = Path(os.environ.get("HUB_NM_DIR", "/etc/NetworkManager/system-connections"))
JOURNALD = Path(os.environ.get("HUB_JOURNALD_CONF", "/etc/systemd/journald.conf"))
FF, ZERO = b"\xff", b"\x00"
KEEP = 50


def protected():
    """The files from the plan's step 4 table that are small, known and the box's own: its settings and keys, the
    front's files, the WiFi credentials, and the journal's configuration. Records, histories and the owner's data are
    not here (their writes are frequent; the plan gives them a budget of their own)."""
    out = []
    for name in (
        "admin-password",
        "front-secret.env",
        "hub.env",
        "access.json",
        "install-options",
        "firewall.nft",
        "update-signing.json",
        "nginx-access.conf",
        "nginx-addons.conf",
    ):
        out.append(ETC / name)
    for name in ("ca/ca.key", "ca.crt", "server.key", "fullchain.crt", "made.json"):
        out.append(ETC / "tls" / name)
    for folder in (ETC / "tls" / "front", ETC / "nginx-access.conf.d", ETC / "nginx-addon-gates.conf.d"):
        if folder.is_dir():
            out += sorted(p for p in folder.rglob("*") if p.is_file() and not p.is_symlink())
    for name in ("conf.d/irate-box.conf", "irate-box-front.conf", "irate-box-via.conf", "irate-box.htpasswd"):
        out.append(NGINX / name)
    out.append(JOURNALD)
    if NM.is_dir():
        out += sorted(p for p in NM.iterdir() if p.is_file() and not p.is_symlink())
    for name in (
        "settings.json",
        "accounts.json",
        "tiles.json",
        "visibility.json",
        "locked_as.json",
        "folders.json",
        "status_tiles.json",
        "hotspot.json",
        "addons-consent.json",
        "addons-catalogue.json",
    ):
        out.append(STATE / name)
    for folder in (STATE / "apps.d", STATE / "addons"):
        if folder.is_dir():
            out += sorted(p for p in folder.glob("*.json") if p.is_file() and not p.is_symlink())
    return [p for p in out if p.is_file() and not p.is_symlink()]


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _bad(data):
    """All 0xFF or all zero: what a card returns for data it did not keep. An empty file is not damage: an empty
    include is a legitimate file."""
    return bool(data) and (data.count(FF[0]) == len(data) or data.count(ZERO[0]) == len(data))


def _read(path):
    return durable._read_back(path, sync=False)


def _ident(path):
    return hashlib.sha1(str(path).encode(), usedforsecurity=False).hexdigest()[:16]


def _copy(path):
    return STORE / _ident(path)


def _prev(path):
    return STORE / (_ident(path) + ".prev")


def _load_manifest():
    try:
        data = json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("entries"), dict) else {}


def _findings(items, when, boot=None):
    try:
        old = json.loads(FINDINGS.read_text())
    except (OSError, ValueError):
        old = []
    if not isinstance(old, list):
        old = []
    old += [dict(item, at=item.get("at", time.time()), when=when, boot=boot or _boot_id()) for item in items]
    if not ROOT.exists():
        ROOT.mkdir(parents=True, mode=0o700)
    durable.write_checked(FINDINGS, json.dumps(old[-KEEP:], indent=1), 0o600)
    return old[-KEEP:]


def _boot_id():
    said = os.environ.get("HUB_BOOT_ID")
    if said:
        return said
    for path in (Path("/proc/sys/kernel/random/boot_id"), STATE / "boot_id"):
        try:
            text = path.read_text().strip()
        except OSError:
            continue
        if text:
            return text
    return ""


def _entry(path, data, copy):
    metadata = path.stat()
    return {
        "size": len(data),
        "mtime_ns": metadata.st_mtime_ns,
        "sha256": _sha(data),
        "copy": copy.name,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": metadata.st_mode & 0o777,
    }


def _streak(current, repaired):
    """How many boots in a row the check repaired or lost a file. Reset by a boot with none."""
    try:
        state = json.loads(STREAK.read_text())
    except (OSError, ValueError):
        state = {}
    if state.get("boot") != current:
        streak = (state.get("streak", 0) + 1) if state.get("repaired") and repaired else (1 if repaired else 0)
        state = {"boot": current, "streak": streak, "repaired": repaired}
        durable.write_checked(STREAK, json.dumps(state), 0o600)
    return state.get("streak", 0) if state.get("boot") == current else 0


def streak():
    try:
        state = json.loads(STREAK.read_text())
    except (OSError, ValueError):
        return 0
    return int(state.get("streak", 0)) if state.get("boot") == _boot_id() else 0


def sweep():
    """The copy: every protected file that reads back right, written beside the last one (which is kept as .prev).
    A file that is already in the copy unchanged is not written again. A file that reads back damaged keeps its
    previous entry and copy, so the boot check can still restore it; a file the owner removed leaves the manifest,
    and its copy is removed with it."""
    STORE.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous = _load_manifest().get("entries", {})
    entries, bad = {}, []
    for path in protected():
        try:
            data = _read(path)
        except OSError:
            continue
        if _bad(data):
            bad.append({"path": str(path), "action": "not copied: it reads back as 0xFF or zero"})
            if str(path) in previous:
                entries[str(path)] = previous[str(path)]
            continue
        copy = _copy(path)
        try:
            have = _read(copy)
        except OSError:
            have = None
        if have != data:
            if copy.exists():
                os.replace(copy, _prev(path))
            durable.write_checked(copy, data, 0o600)
        entries[str(path)] = _entry(path, data, copy)
    durable.write_checked(
        MANIFEST, json.dumps({"version": 1, "at": time.time(), "entries": entries}, indent=1, sort_keys=True), 0o600
    )
    keep = {entry.get("copy") for entry in entries.values() if entry.get("copy")}
    for old in STORE.iterdir():
        if old.name in ("manifest.json", "damaged") or not old.is_file():
            continue
        if old.name.endswith(".prev"):
            continue
        if old.name not in keep:
            old.unlink(missing_ok=True)
    if bad:
        _findings(bad, "sweep")
    return bad


def boot_check():
    """Every protected file, read past the cache and compared with the manifest. A file the owner removed is left
    removed. A change the owner made (its mtime moved) is left alone. Damage is put back from the copy, then from
    the copy's .prev, with the file's own owner and mode; the damaged file is kept outside the folders a web server
    includes by glob. When neither copy checks out, a finding says what is lost."""
    manifest = _load_manifest()
    if not manifest:
        return []
    current = _boot_id()
    repairs, lost = [], []
    for path_str, entry in manifest["entries"].items():
        path = Path(path_str)
        wanted = entry.get("sha256")
        try:
            data = _read(path)
        except OSError:
            data = None
        if data is None:
            continue  # removed by the owner or the hub: the next sweep drops it, and it is not a card's damage
        if not _bad(data) and _sha(data) == wanted:
            continue
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if not _bad(data) and mtime != entry.get("mtime_ns"):
            continue  # the owner changed it: the next sweep copies it
        done = False
        for source in (_copy(path), _prev(path)):
            try:
                if source.is_file() and _sha(_read(source)) == wanted:
                    DAMAGED.mkdir(parents=True, exist_ok=True, mode=0o700)
                    keep_at = DAMAGED / f"{_ident(path)}.{int(time.time())}"
                    restore.from_copy(
                        path,
                        source,
                        sha256=wanted,
                        mode=entry.get("mode"),
                        owner=(entry.get("uid"), entry.get("gid")),
                        keep_at=keep_at,
                    )
                    repairs.append({"path": str(path), "action": "restored from " + source.name})
                    done = True
                    break
            except (OSError, Refused):
                continue
        if not done:
            lost.append({"path": str(path), "action": "damaged, and no good copy: reinstall or restore it by hand"})
    _streak(current, bool(repairs or lost))
    if repairs or lost:
        _findings(repairs + lost, "boot", boot=current)
    return repairs + lost


def unit_text(name):
    code, etc, state, root = CODE, ETC, STATE, ROOT
    sandbox = (
        "ProtectSystem=strict\n"
        f"ReadWritePaths={etc} {state} {root} {NGINX} /etc/systemd {NM}\n"
        "PrivateTmp=yes\nNoNewPrivileges=yes\nProtectHome=yes\nProtectProc=invisible\n"
    )
    if name == SERVICE:
        return (
            "[Unit]\nDescription=Irate-Box: the settings and keys, checked at boot\n"
            "DefaultDependencies=no\nAfter=local-fs.target nand-state.service\n"
            "Before=network.target irate-box.service nginx.service caddy.service\n\n"
            "[Service]\nType=oneshot\n"
            f"ExecStart={code}/irate-box retain boot-check\n" + sandbox + "\n[Install]\nWantedBy=multi-user.target\n"
        )
    if name == SWEEP_SERVICE:
        return (
            "[Unit]\nDescription=Irate-Box: the second copy of the settings and keys\n"
            "After=local-fs.target nand-state.service\n\n[Service]\nType=oneshot\n"
            f"ExecStart={code}/irate-box retain sweep\nNice=19\nIOSchedulingClass=idle\n" + sandbox
        )
    if name == TIMER:
        return (
            "[Unit]\nDescription=Irate-Box: the second copy of the settings and keys\n\n[Timer]\n"
            "OnBootSec=45min\nOnUnitActiveSec=6h\nRandomizedDelaySec=15min\n"
            f"Unit={SWEEP_SERVICE}\n\n[Install]\nWantedBy=timers.target\n"
        )
    raise ValueError(f"no unit text for {name}")


def installed():
    return (SYSTEMD / SERVICE).exists()


def install_units(on=True):
    SYSTEMD.mkdir(parents=True, exist_ok=True)
    if on:
        for name in (SERVICE, SWEEP_SERVICE, TIMER):
            durable.write_checked(SYSTEMD / name, unit_text(name), 0o644)
        restore.run("systemctl", "daemon-reload")
        restore.run("systemctl", "enable", SERVICE)
        restore.run("systemctl", "enable", "--now", TIMER)
        return M("retain.on", "The box now keeps a copy of its settings and keys and checks them at every boot.")
    restore.run("systemctl", "disable", "--now", TIMER)
    restore.run("systemctl", "disable", "--now", SERVICE)
    for name in (TIMER, SERVICE, SWEEP_SERVICE):
        (SYSTEMD / name).unlink(missing_ok=True)
    restore.run("systemctl", "daemon-reload")
    return M("retain.off", "The copy is gone, and the boot check with it.")


def main(argv):
    command = argv[0] if argv else ""
    if command == "sweep":
        if os.geteuid() != 0:
            sys.exit("run as root")
        sweep()
    elif command == "boot-check":
        if os.geteuid() != 0:
            sys.exit("run as root")
        boot_check()
    elif command in ("install-units", "uninstall-units"):
        if os.geteuid() != 0:
            sys.exit("run as root")
        print(install_units(command == "install-units"))
    else:
        sys.exit("usage: retain sweep | boot-check | install-units | uninstall-units")


if __name__ == "__main__":
    main(sys.argv[1:])
