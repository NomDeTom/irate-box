# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Connections that netplan makes, handed over to NetworkManager at the owner's choice, and back.

Where NetworkManager runs a connection netplan describes, netplan writes its profile afresh into
/run/NetworkManager/system-connections at every boot, and that copy wins over any change made to
it in /etc with nmcli. A NetworkManager built with netplan's integration (Ubuntu's, Raspberry Pi
OS's: linked against libnetplan) writes changes back into netplan itself, so they last and nothing
here is needed; one without it (Debian's, Armbian's) cannot keep a per-connection change. Handing a
connection over makes it NetworkManager's own: its profile written as a keyfile in /etc, the same
uuid and password, and the netplan file that described it moved aside.

The unit is a netplan file: everything it makes moves together, as netplan's own tools cannot be
trusted to take one entry out of a file (netplan set 1.1.2 drops a WiFi password on rewriting it).
Before anything moves, netplan generate runs in a scratch root without that file: only that file's
NetworkManager profiles may go missing, and every other thing generated must come out byte for byte
as before, else it is refused. A WiFi country the file set (netplan's regulatory-domain, applied by
a unit netplan generates) is kept as cfg80211's module option and set at once; the udev lines netplan
writes to have NetworkManager manage the file's interfaces are kept in irate-box's own rule file.

After the move, NetworkManager's view of each profile (every setting, the secrets with it) must be
what it was; otherwise everything is put back at once. Undo puts the netplan file back and takes the
keyfiles away. Everything moved is kept in STATE/netplan-handover/<when>/; the record is
/etc/hub/netplan-handover.json. Uninstalling irate-box leaves a handed-over connection as it is (the
box stays on its network) and says how to put it back.

Root only; stdlib only.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ETC = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub"))
STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
RECORD = ETC / "netplan-handover.json"
BACKUP = STATE / "netplan-handover"
NETPLAN_DIRS = [Path(p) for p in os.environ.get("HUB_NETPLAN_DIRS", "/lib/netplan:/etc/netplan:/run/netplan").split(":")]
KEYFILES = Path(os.environ.get("HUB_NM_CONNECTIONS", "/etc/NetworkManager/system-connections"))
RUN_PROFILES = Path(os.environ.get("HUB_NM_RUN_CONNECTIONS", "/run/NetworkManager/system-connections"))
NM_BIN = os.environ.get("HUB_NM_BIN", "/usr/sbin/NetworkManager")
REGDOM_CONF = Path(os.environ.get("HUB_MODPROBE_DIR", "/etc/modprobe.d")) / "irate-box-regdom.conf"
UDEV_RULES = Path(os.environ.get("HUB_UDEV_RULES_DIR", "/etc/udev/rules.d")) / "90-irate-box-netplan-handover.rules"
NETPLAN_UDEV = "run/udev/rules.d/90-netplan.rules"
ALLOW_RE = re.compile(r'^SUBSYSTEM=="net", ACTION=="add\|change\|move", ENV\{ID_NET_NAME\}=="[A-Za-z0-9_.-]+", ENV\{NM_UNMANAGED\}="0"$')
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
REGDOM_RE = re.compile(r"^\s*regulatory-domain:\s*[\"']?([A-Za-z]{2})[\"']?\s*$", re.M)
# Settings that change with no change to the connection: left out when comparing before and after.
VOLATILE = ("connection.timestamp", "GENERAL.", "IP4.", "IP6.", "DHCP4.", "DHCP6.", "connection.read-only")


def run(*cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not installed"
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    return p.returncode, (p.stdout + p.stderr).strip()


def record():
    try:
        data = json.loads(RECORD.read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(rec):
    from irate_box.root import safeio
    ETC.mkdir(parents=True, exist_ok=True)
    safeio.write(RECORD, json.dumps(rec, indent=1), 0o600)


def integrated():
    """NetworkManager built with netplan's integration: it keeps changes in netplan itself."""
    code, out = run("ldd", NM_BIN, timeout=15)
    return code == 0 and "libnetplan" in out


def _netplan_files():
    """{name: path} as netplan reads them: the same name in a later directory shadows an earlier one."""
    by_name = {}
    for d in NETPLAN_DIRS:
        if d.is_dir():
            for f in sorted(d.glob("*.yaml")):
                by_name[f.name] = f
    return by_name


def _profiles():
    """[{name, uuid, type, file}] of NetworkManager's connections."""
    code, out = run("nmcli", "-t", "-f", "NAME,UUID,TYPE,FILENAME", "con", "show")
    rows = []
    for line in out.splitlines() if code == 0 else []:
        parts = re.split(r"(?<!\\):", line)
        if len(parts) >= 4:
            rows.append({"name": parts[0].replace("\\:", ":"), "uuid": parts[1], "type": parts[2], "file": parts[3].replace("\\:", ":")})
    return rows


def _generate(files):
    """netplan generate in a scratch root holding these netplan files: {relative path: bytes} of what it made."""
    root = Path(tempfile.mkdtemp(prefix="netplan-check-"))
    try:
        for name, src in files.items():
            rel = src.parent.relative_to("/") if src.is_absolute() else src.parent
            dest = root / rel / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            os.chmod(dest, 0o600)
        code, out = run("netplan", "generate", "--root-dir", str(root), timeout=60)
        if code != 0:
            raise ValueError(f"netplan generate failed: {out[-200:]}")
        made = {}
        run_dir = root / "run"
        if run_dir.is_dir():
            for f in run_dir.rglob("*"):
                if f.is_file():
                    made[str(f.relative_to(root))] = f.read_bytes()
        return made
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _uuid_of(keyfile_bytes):
    m = re.search(rb"^uuid=([0-9a-f-]{36})\s*$", keyfile_bytes, re.M)
    return m.group(1).decode() if m else None


def _with_uuid(keyfile_bytes, uuid):
    """The keyfile with uuid= in its [connection] section (netplan's have none)."""
    if _uuid_of(keyfile_bytes):
        return keyfile_bytes
    out, done = [], False
    for line in keyfile_bytes.split(b"\n"):
        out.append(line)
        if not done and line.strip() == b"[connection]":
            out.append(b"uuid=" + uuid.encode())
            done = True
    if not done:
        out = [b"[connection]", b"uuid=" + uuid.encode(), *out]
    return b"\n".join(out)


def plan(name):
    """What handing over the netplan file `name` would do, or ValueError saying why not."""
    files = _netplan_files()
    if name not in files:
        raise ValueError(f"no netplan file {name}")
    if not re.search(r"^\s+(ethernets|wifis|bridges|bonds|vlans|modems|nm-devices):", files[name].read_text(errors="replace"), re.M):
        raise ValueError(f"{name} makes no NetworkManager connection")
    if integrated():
        raise ValueError("this box's NetworkManager keeps its settings in netplan itself, so changes made to a "
                         "connection last already: nothing to hand over")
    before = _generate(files)
    without = _generate({n: p for n, p in files.items() if n != name})
    gone = sorted(set(before) - set(without))
    changed = sorted(k for k in set(before) & set(without) if before[k] != without[k])
    added = sorted(set(without) - set(before))
    # netplan's udev lines that have NetworkManager manage this file's interfaces: kept by irate-box instead.
    keep_udev = []
    if NETPLAN_UDEV in gone or NETPLAN_UDEV in changed:
        had = before[NETPLAN_UDEV].decode(errors="replace").splitlines()
        left = set(without.get(NETPLAN_UDEV, b"").decode(errors="replace").splitlines())
        lost = [l for l in had if l not in left]
        rules = [l for l in lost if not l.startswith("#")]
        if rules and all(ALLOW_RE.match(l) for l in rules):
            keep_udev = lost
            gone = [k for k in gone if k != NETPLAN_UDEV]
            changed = [k for k in changed if k != NETPLAN_UDEV]
    profiles = [k for k in gone if k.startswith("run/NetworkManager/system-connections/") and k.endswith(".nmconnection")]
    # Besides its profiles, a file may take with it only the unit that sets its WiFi country (kept another way).
    regdom = [k for k in gone if "netplan-regdom" in k]
    other = [k for k in gone if k not in profiles and k not in regdom]
    if changed or added or other:
        raise ValueError(f"{name} makes more than NetworkManager connections, or changes what other files make "
                         f"({', '.join((changed + added + other)[:4])}), so it is left as it is")
    if not profiles:
        raise ValueError(f"{name} makes no NetworkManager connection")
    # netplan's profiles carry no uuid line: NetworkManager makes one up from the file's path in /run, and a
    # copy elsewhere would get another. The uuid in use now is written into the keyfile.
    in_use = {p["file"]: p["uuid"] for p in _profiles()}
    text = files[name].read_text(errors="replace")
    m = REGDOM_RE.search(text)
    return {"file": str(files[name]), "name": name, "regdom": m.group(1).upper() if m else None, "udev": keep_udev,
            "profiles": [{"generated": Path(k).name, "bytes": before[k],
                          "uuid": _uuid_of(before[k]) or in_use.get(str(RUN_PROFILES / Path(k).name))} for k in profiles]}


def candidates():
    """The netplan files that make NetworkManager connections here, with those connections (names and
    uuids, nothing secret), whether each can be handed over, and why not if not."""
    if not any(d.is_dir() for d in NETPLAN_DIRS):
        return {"netplan": False, "integrated": False, "files": []}
    if integrated():
        return {"netplan": True, "integrated": True, "files": []}
    known = {p["uuid"]: p["name"] for p in _profiles()}
    out = []
    for name in sorted(_netplan_files()):
        try:
            pl = plan(name)
        except ValueError as exc:
            if "makes no NetworkManager connection" not in str(exc):
                out.append({"name": name, "ok": False, "why": str(exc), "connections": []})
            continue
        out.append({"name": name, "ok": True, "regdom": pl["regdom"],
                    "connections": [{"name": known.get(p["uuid"], p["generated"].removesuffix(".nmconnection")), "uuid": p["uuid"]}
                                    for p in pl["profiles"]]})
    return {"netplan": True, "integrated": False, "files": out, "done": [{k: v for k, v in r.items() if k != "moved_etc"} for r in record()]}


def _managed():
    """The devices NetworkManager manages now."""
    code, out = run("nmcli", "-t", "-f", "DEVICE,STATE", "dev")
    return sorted(l.split(":", 1)[0] for l in out.splitlines() if ":" in l and not l.split(":", 1)[1].startswith("unmanaged")) if code == 0 else []


def _settings(uuid):
    """NetworkManager's view of a connection, secrets included, less what changes on its own."""
    code, out = run("nmcli", "-s", "-t", "con", "show", "uuid", uuid)
    if code != 0:
        return None
    return sorted(l for l in out.splitlines() if not l.startswith(VOLATILE) and not l.startswith("connection.filename"))


def handover(name, sleep=time.sleep):
    """Hand the netplan file `name` over to NetworkManager. One line saying what was done."""
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]{1,128}\.yaml", str(name or "")):
        raise ValueError("not a netplan file's name")
    pl = plan(name)
    BACKUP.mkdir(parents=True, exist_ok=True)
    back = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=BACKUP))   # 0700, unique
    if any(not p["uuid"] for p in pl["profiles"]):
        raise ValueError("NetworkManager does not show every connection this file makes; nothing changed")
    before = {p["uuid"]: _settings(p["uuid"]) for p in pl["profiles"]}
    if any(v is None for v in before.values()):
        raise ValueError("NetworkManager does not show every connection this file makes; nothing changed")
    src = Path(pl["file"])
    entry = {"at": time.time(), "name": name, "file": str(src), "backup": str(back), "regdom": pl["regdom"],
             "connections": [], "moved_etc": [], "regdom_written": False, "udev_written": False}
    managed_before = _managed()
    written = []
    try:
        # Keyfiles in /etc with the same uuid (a change once made with nmcli) would be a second copy: kept aside.
        uuids = {p["uuid"] for p in pl["profiles"]}
        for f in sorted(KEYFILES.glob("*.nmconnection")) if KEYFILES.is_dir() else []:
            try:
                if _uuid_of(f.read_bytes()) in uuids:
                    shutil.move(str(f), back / f.name)
                    entry["moved_etc"].append(f.name)
            except OSError:
                continue
        KEYFILES.mkdir(parents=True, exist_ok=True)
        for p in pl["profiles"]:
            dest = KEYFILES / p["generated"]
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(_with_uuid(p["bytes"], p["uuid"]))
            written.append(dest)
            entry["connections"].append({"uuid": p["uuid"], "keyfile": str(dest)})
        shutil.copy2(src, back / src.name)
        src.unlink()
        if pl["regdom"] and not REGDOM_CONF.exists():
            REGDOM_CONF.parent.mkdir(parents=True, exist_ok=True)
            REGDOM_CONF.write_text(f"# irate-box: the WiFi country the netplan file {name} set, kept when it was handed over\n"
                                   f"options cfg80211 ieee80211_regdom={pl['regdom']}\n")
            entry["regdom_written"] = True
        if pl["udev"]:
            old = UDEV_RULES.read_text() if UDEV_RULES.exists() else "# irate-box: netplan's lines for handed-over connections' interfaces (NetworkManager manages them)\n"
            UDEV_RULES.parent.mkdir(parents=True, exist_ok=True)
            UDEV_RULES.write_text(old + f"# from {name}\n" + "\n".join(pl["udev"]) + "\n")
            entry["udev_written"] = True
        code, out = run("netplan", "generate", timeout=90)
        if code != 0:
            raise ValueError(f"netplan generate: {out[-160:]}")
        if pl["udev"]:
            run("udevadm", "control", "--reload", timeout=30)
        code, out = run("nmcli", "con", "reload", timeout=60)
        if code != 0:
            raise ValueError(f"nmcli con reload: {out[-160:]}")
        sleep(2)
        for p in pl["profiles"]:
            now = _settings(p["uuid"])
            if now != before[p["uuid"]]:
                diff = sorted(set(before[p["uuid"]] or []) ^ set(now or []))
                keys = sorted({d.split(":", 1)[0] for d in diff})
                raise ValueError(f"NetworkManager's view of the connection changed ({', '.join(keys[:5])})")
        lost = sorted(set(managed_before) - set(_managed()))
        if lost:
            raise ValueError(f"NetworkManager stopped managing {', '.join(lost)}")
        if pl["regdom"]:
            run("iw", "reg", "set", pl["regdom"], timeout=15)
    except (OSError, ValueError) as exc:
        _put_back(entry, written)
        raise ValueError(f"not handed over, everything put back: {exc}")
    rec = record()
    rec.append(entry)
    _save(rec)
    names = ", ".join(sorted(x["generated"].removesuffix(".nmconnection") for x in pl["profiles"]))
    return (f"{names} handed over to NetworkManager: {name} kept in {back}"
            + (f"; the WiFi country ({pl['regdom']}) kept in {REGDOM_CONF}" if entry["regdom_written"] else ""))


def _put_back(entry, written):
    back = Path(entry["backup"])
    src = Path(entry["file"])
    if not src.exists() and (back / src.name).exists():
        shutil.copy2(back / src.name, src)
        os.chmod(src, 0o600)
    for f in written:
        Path(f).unlink(missing_ok=True)
    for n in entry.get("moved_etc", []):
        if (back / n).exists() and not (KEYFILES / n).exists():
            shutil.move(str(back / n), KEYFILES / n)
    if entry.get("regdom_written"):
        REGDOM_CONF.unlink(missing_ok=True)
    if entry.get("udev_written") and UDEV_RULES.exists():
        kept, skip = [], False
        for line in UDEV_RULES.read_text().splitlines():
            if line.startswith("# from "):
                skip = line == f"# from {entry['name']}"
                if skip:
                    continue
            elif skip and (ALLOW_RE.match(line) or line.startswith("# netplan:")):
                continue
            else:
                skip = False
            kept.append(line)
        if [l for l in kept if not l.startswith("#")]:
            UDEV_RULES.write_text("\n".join(kept) + "\n")
        else:
            UDEV_RULES.unlink()
        run("udevadm", "control", "--reload", timeout=30)
    run("netplan", "generate", timeout=90)
    run("nmcli", "con", "reload", timeout=60)


def undo(name):
    """Put a handed-over netplan file back: netplan makes those connections again."""
    rec = record()
    entry = next((r for r in reversed(rec) if r["name"] == name), None)
    if entry is None:
        raise ValueError(f"{name} was not handed over here")
    if Path(entry["file"]).exists():
        raise ValueError(f"{entry['file']} is there again already; nothing put back")
    if not (Path(entry["backup"]) / Path(entry["file"]).name).exists():
        raise ValueError(f"the kept copy of {name} is missing from {entry['backup']}")
    entry = {**entry, "moved_etc": []}   # a second copy kept aside stays aside: it is what made the trouble
    _put_back(entry, [c["keyfile"] for c in entry["connections"]])
    rec.remove(next(r for r in reversed(rec) if r["name"] == name))
    _save(rec)
    return f"{name} put back: netplan makes its connections again (they were kept in {entry['backup']})"


def kept_on_uninstall():
    """uninstall.sh's line: handed-over connections stay NetworkManager's, and how to put them back."""
    rec = record()
    if not rec:
        return ""
    return "\n".join(f"{r['name']}: its connections stay NetworkManager's own; to put the netplan file back: "
                     f"cp {r['backup']}/{Path(r['file']).name} {r['file']} && rm "
                     + " ".join(c["keyfile"] for c in r["connections"]) + " && netplan generate && nmcli con reload"
                     for r in rec)


def main(argv):
    if argv[:1] == ["list"]:
        print(json.dumps({k: v for k, v in candidates().items()}, indent=1))
    elif argv[:1] == ["handover"] and len(argv) == 2:
        print(handover(argv[1]))
    elif argv[:1] == ["undo"] and len(argv) == 2:
        print(undo(argv[1]))
    elif argv[:1] == ["kept"]:
        said = kept_on_uninstall()
        if said:
            print(said)
    else:
        print("usage: nmhandover.py list | handover NAME.yaml | undo NAME.yaml | kept")
        return 2
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
