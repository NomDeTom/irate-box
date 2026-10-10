# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Handing netplan's connections over to NetworkManager (root/nmhandover.py), offline, with netplan and
nmcli stood in as they behave on a Debian NetworkManager without netplan's integration: netplan writes
a profile per connection into /run at every generate, and NetworkManager prefers it to one in /etc.
A file is handed over only when it makes nothing but NetworkManager connections; the keyfile is the
profile netplan made, byte for byte; an older copy in /etc goes aside; the WiFi country is kept;
NetworkManager's view checked and everything put back when it differs; undo; integration refused.
python3 tests/sim_nmhandover.py"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="nmhandover-"))
os.environ.update(HUB_ETC_DIR=str(T / "hub-etc"), HUB_STATE_DIR=str(T / "state"),
                  HUB_NETPLAN_DIRS=f"{T}/lib/netplan:{T}/etc/netplan:{T}/run/netplan",
                  HUB_NM_CONNECTIONS=str(T / "etc/nm"), HUB_NM_RUN_CONNECTIONS=str(T / "run/nm"),
                  HUB_MODPROBE_DIR=str(T / "modprobe.d"), HUB_UDEV_RULES_DIR=str(T / "udev"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.root import nmhandover as H  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def put(p, text, mode=0o600):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    os.chmod(p, mode)


def profile(name, uuid, psk="s3cret-pass"):
    """A keyfile; uuid None for one as netplan writes it (no uuid line)."""
    return (f"[connection]\nid={name}\n" + (f"uuid={uuid}\n" if uuid else "")
            + f"type=wifi\n\n[wifi]\nssid=Home\n\n[wifi-security]\nkey-mgmt=wpa-psk\npsk={psk}\n")


def made_up_uuid(path):
    """NetworkManager's uuid for a keyfile with none: from its path (here a fixed one per path)."""
    import hashlib
    h = hashlib.sha1(str(path).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


HOME = None   # set once netplan's profile is in /run: what NetworkManager makes up from its path
# A netplan file says what it makes in comments the stand-in reads: a profile, a country, or a networkd file.
put(T / "lib/netplan/00-default-use-network-manager.yaml", "network:\n  renderer: NetworkManager\n")
put(T / "etc/netplan/30-wifis-dhcp.yaml", "network:\n  wifis:\n    wlan0:\n      regulatory-domain: GB\n# makes: netplan-wlan0-Home -\n")
put(T / "etc/netplan/40-mixed.yaml", "network:\n  ethernets:\n    eth1: {}\n# makes: netplan-eth1 11111111-1111-1111-1111-111111111111\n# networkd: 10-netplan-eth2.network\n")
state = {"integrated": False, "settings_drift": False}
ran = []


def gen(root, prof_dir):
    for f in sorted(root.rglob("*.yaml")):
        text = f.read_text()
        for name, uuid in re.findall(r"^# makes: (\S+) (\S+)$", text, re.M):
            put(prof_dir / f"{name}.nmconnection", profile(name, None))
        for n in re.findall(r"^# networkd: (\S+)$", text, re.M):
            put(root / "run/systemd/network" / n if root != T else T / "run/systemd/network" / n, "[Match]\n")
        if "wifis:" in text:
            udev = (root if root != T else T) / "run/udev/rules.d/90-netplan.rules"
            udev.parent.mkdir(parents=True, exist_ok=True)
            with open(udev, "a") as fh:
                fh.write('# netplan: network.wifis.wlan0 (on NetworkManager allow-list)\n'
                         'SUBSYSTEM=="net", ACTION=="add|change|move", ENV{ID_NET_NAME}=="wlan0", ENV{NM_UNMANAGED}="0"\n')
        if "regulatory-domain:" in text:
            put((root if root != T else T) / "run/systemd/system/netplan-regdom.service", "[Service]\nExecStart=iw reg set GB\n")


def winning():
    """uuid: path, as NetworkManager picks: /run over /etc."""
    out = {}
    for d in (T / "etc/nm", T / "run/nm"):
        for f in sorted(d.glob("*.nmconnection")) if d.is_dir() else []:
            m = re.search(r"^uuid=(\S+)$", f.read_text(), re.M)
            out[m.group(1) if m else made_up_uuid(f)] = f
    return out


def fake_run(*cmd, timeout=60):
    ran.append(cmd)
    if cmd[0] == "ldd":
        return 0, ("libnetplan.so.1 => /lib/x\n" if state["integrated"] else "libc.so.6 => /lib/y\n")
    if cmd[:2] == ("netplan", "generate"):
        if "--root-dir" in cmd:
            root = Path(cmd[cmd.index("--root-dir") + 1])
            gen(root, root / "run/NetworkManager/system-connections")
        else:
            state["unmanaged_now"] = bool(state.get("unmanage"))
            for f in (T / "run/nm").glob("netplan-*.nmconnection"):
                f.unlink()
            shutil.rmtree(T / "run/systemd", ignore_errors=True)
            for d in ("lib/netplan", "etc/netplan", "run/netplan"):
                if (T / d).is_dir():
                    for f in (T / d).glob("*.yaml"):
                        text = f.read_text()
                        for name, uuid in re.findall(r"^# makes: (\S+) (\S+)$", text, re.M):
                            put(T / "run/nm" / f"{name}.nmconnection", profile(name, None))
        return 0, ""
    if cmd[:3] == ("nmcli", "con", "reload"):
        return 0, ""
    if cmd[:5] == ("nmcli", "-t", "-f", "DEVICE,STATE", "dev"):
        return 0, "wlan0:" + ("unmanaged" if state.get("unmanaged_now") else "connected") + "\nlo:connected (externally)"
    if cmd[:6] == ("nmcli", "-t", "-f", "NAME,UUID,TYPE,FILENAME", "con", "show"):
        rows = []
        for uuid, f in winning().items():
            name = re.search(r"^id=(.*)$", f.read_text(), re.M).group(1)
            rows.append(f"{name}:{uuid}:802-11-wireless:{f}")
        return 0, "\n".join(rows)
    if cmd[:4] == ("nmcli", "-s", "-t", "con") and cmd[4:6] == ("show", "uuid"):
        f = winning().get(cmd[6])
        if not f:
            return 10, "no such connection"
        lines = [l for l in f.read_text().splitlines() if "=" in l and not l.startswith("uuid=")] + [f"connection.uuid:{cmd[6]}"]
        if state["settings_drift"] and f.parent == T / "etc/nm":
            lines.append("wifi.cloned-mac-address=random")
        return 0, "\n".join(lines)
    if cmd[:3] == ("iw", "reg", "set"):
        return 0, ""
    return 0, ""


H.run = fake_run
fake_run("netplan", "generate")   # boot: netplan's profiles in /run
HOME = made_up_uuid(T / "run/nm/netplan-wlan0-Home.nmconnection")
put(T / "etc/nm/netplan-wlan0-Home-0c3bb1fa.nmconnection", profile("netplan-wlan0-Home", HOME, psk="s3cret-pass"))  # an older nmcli change

c = H.candidates()
by = {f["name"]: f for f in c["files"]}
check("candidates: the WiFi file can be handed over, with its connection and country",
      by["30-wifis-dhcp.yaml"]["ok"] and by["30-wifis-dhcp.yaml"]["regdom"] == "GB"
      and by["30-wifis-dhcp.yaml"]["connections"] == [{"name": "netplan-wlan0-Home", "uuid": HOME}], by)
check("  a file that makes something besides NetworkManager connections: refused, saying why",
      not by["40-mixed.yaml"]["ok"] and "more than NetworkManager connections" in by["40-mixed.yaml"]["why"], by["40-mixed.yaml"])
check("  a file that makes no connection (the renderer default) is not listed", "00-default-use-network-manager.yaml" not in by)

said = H.handover("30-wifis-dhcp.yaml", sleep=lambda s: None)
key = T / "etc/nm/netplan-wlan0-Home.nmconnection"
rec = H.record()
check("handover: the profile netplan made, now a keyfile in /etc with the uuid in use written in, root's alone",
      key.read_text() == profile("netplan-wlan0-Home", None).replace("[connection]\n", f"[connection]\nuuid={HOME}\n", 1) and oct(key.stat().st_mode & 0o777) == "0o600", said)
check("  the netplan file moved aside, kept", not (T / "etc/netplan/30-wifis-dhcp.yaml").exists()
      and (Path(rec[0]["backup"]) / "30-wifis-dhcp.yaml").exists(), rec)
check("  the older copy in /etc with the same uuid moved aside too", rec[0]["moved_etc"] == ["netplan-wlan0-Home-0c3bb1fa.nmconnection"]
      and not (T / "etc/nm/netplan-wlan0-Home-0c3bb1fa.nmconnection").exists(), rec)
check("  NetworkManager now uses the keyfile (netplan's /run copy is gone)", winning()[HOME] == key, winning())
check("  the WiFi country kept as cfg80211's option, and set at once",
      "ieee80211_regdom=GB" in H.REGDOM_CONF.read_text() and ("iw", "reg", "set", "GB") in ran, said)
check("  netplan's line having NetworkManager manage wlan0, kept in irate-box's own rule file",
      'ENV{ID_NET_NAME}=="wlan0", ENV{NM_UNMANAGED}="0"' in H.UDEV_RULES.read_text() and ("udevadm", "control", "--reload") in ran)
check("  the record holds no secret", "s3cret" not in H.RECORD.read_text())
check("  said what was done, and where the file is kept", "handed over to NetworkManager" in said and "30-wifis-dhcp.yaml kept in" in said, said)
check("  listed as done, not as a candidate", [d["name"] for d in H.candidates()["done"]] == ["30-wifis-dhcp.yaml"]
      and "30-wifis-dhcp.yaml" not in {f["name"] for f in H.candidates()["files"]})

said = H.undo("30-wifis-dhcp.yaml")
check("undo: the netplan file back, the keyfile gone, netplan's profile in use again, the country file gone",
      (T / "etc/netplan/30-wifis-dhcp.yaml").exists() and not key.exists() and winning()[HOME].parent == T / "run/nm"
      and not H.REGDOM_CONF.exists() and not H.UDEV_RULES.exists() and H.record() == [], said)
check("  the older second copy stays aside (it is what made the trouble)", not (T / "etc/nm/netplan-wlan0-Home-0c3bb1fa.nmconnection").exists())

state["settings_drift"] = True
try:
    H.handover("30-wifis-dhcp.yaml", sleep=lambda s: None)
    check("NetworkManager's view changing: refused", False)
except ValueError as exc:
    check("NetworkManager's view changing: everything put back at once, saying what changed",
          "put back" in str(exc) and "wifi.cloned-mac-address" in str(exc) and (T / "etc/netplan/30-wifis-dhcp.yaml").exists()
          and not key.exists() and not H.REGDOM_CONF.exists() and H.record() == [] and winning()[HOME].parent == T / "run/nm", str(exc))
state["settings_drift"] = False

for bad in ("../etc/passwd", "x.yml", "a b.yaml", ""):
    try:
        H.handover(bad, sleep=lambda s: None)
        check(f"a name like {bad!r} refused", False)
    except ValueError:
        check(f"a name like {bad!r} refused", True)

state["unmanage"] = True
try:
    H.handover("30-wifis-dhcp.yaml", sleep=lambda s: None)
    check("NetworkManager no longer managing an interface: refused", False)
except ValueError as exc:
    check("NetworkManager no longer managing an interface afterwards: everything put back",
          "stopped managing wlan0" in str(exc) and (T / "etc/netplan/30-wifis-dhcp.yaml").exists() and not H.UDEV_RULES.exists(), str(exc))
state["unmanage"] = state["unmanaged_now"] = False
state["integrated"] = True
c = H.candidates()
check("a NetworkManager with netplan's integration: nothing offered, said why", c["integrated"] and c["files"] == [], c)
try:
    H.handover("30-wifis-dhcp.yaml", sleep=lambda s: None)
    check("  and handing over refused", False)
except ValueError as exc:
    check("  and handing over refused", "last already" in str(exc), str(exc))
state["integrated"] = False

H.handover("30-wifis-dhcp.yaml", sleep=lambda s: None)
kept = H.kept_on_uninstall()
check("uninstall's line: the connection stays NetworkManager's, and how to put the file back",
      "stay NetworkManager's own" in kept and "netplan generate" in kept and str(key) in kept, kept)

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
