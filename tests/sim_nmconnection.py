# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""One WiFi connection's own settings (root/nmconnection.py), offline with nmcli stood in: only where a
change lasts (NetworkManager's own keyfile, or a NetworkManager that keeps them in netplan); each value
checked; the value from before recorded once, for undo; a value set back as it was no longer counted;
the hotspot's profile refused; a channel needing a band.
python3 tests/sim_nmconnection.py"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="nmconnection-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.msg import Refused  # noqa: E402
from irate_box.root import nmconnection as C  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


HOME, AP, NP = (
    "0c3bb1fa-0f6a-3707-8c8d-4e23598253db",
    "11111111-1111-1111-1111-111111111111",
    "22222222-2222-2222-2222-222222222222",
)
conns = {
    HOME: {"file": "/etc/NetworkManager/system-connections/home.nmconnection", "mode": "infrastructure", "id": "Home"},
    AP: {
        "file": "/etc/NetworkManager/system-connections/irate-box-ap.nmconnection",
        "mode": "ap",
        "id": "irate-box-ap",
    },
    NP: {
        "file": "/run/NetworkManager/system-connections/netplan-wlan0-Cafe.nmconnection",
        "mode": "infrastructure",
        "id": "netplan-wlan0-Cafe",
    },
}
values = {u: {k: d for _n, (k, _c, d) in C.SETTINGS.items()} for u in conns}
values[HOME]["802-11-wireless.powersave"] = "default"
ran = []


def fake_run(*cmd, timeout=60):
    ran.append(cmd)
    if cmd[:6] == ("nmcli", "-t", "-f", "UUID,FILENAME", "con", "show"):
        return 0, "\n".join(f"{u}:{c['file']}" for u, c in conns.items())
    if cmd[:3] == ("nmcli", "-t", "-f") and cmd[4:6] == ("con", "show"):
        u = cmd[7]
        if u not in conns:
            return 10, "no such connection"
        rows = [f"connection.id:{conns[u]['id']}", f"802-11-wireless.mode:{conns[u]['mode']}"]
        rows += [f"{k}:{v.replace(':', chr(92) + ':')}" for k, v in values[u].items()]
        return 0, "\n".join(rows)
    if cmd[:3] == ("nmcli", "con", "modify"):
        u, args = cmd[4], cmd[5:]
        for k, v in zip(args[::2], args[1::2], strict=False):
            values[u][k] = v
        return 0, ""
    return 0, ""


C.run = fake_run

said = C.change(HOME, {"auth_retries": "0", "metered": "yes", "cloned_mac": "stable"})
rec = json.loads(C.RECORD.read_text())
check(
    "set: written to the profile, said, and taking effect next time",
    values[HOME]["connection.auth-retries"] == "0"
    and values[HOME]["connection.metered"] == "yes"
    and said["id"] == "nm-conn.changed"
    and said["args"] == {"changes": ["auth_retries 0", "metered yes", "cloned_mac stable"], "name": "Home"},
    said,
)
check(
    "  the values from before recorded",
    rec[HOME]["old"] == {"auth_retries": "-1", "metered": "unknown", "cloned_mac": ""},
    rec,
)
C.change(HOME, {"auth_retries": "5"})
check(
    "  changed again: the value from before still the first one",
    json.loads(C.RECORD.read_text())[HOME]["old"]["auth_retries"] == "-1",
)
C.change(HOME, {"cloned_mac": "default"})
check(
    "  set back to NetworkManager's own: no longer counted as changed",
    "cloned_mac" not in json.loads(C.RECORD.read_text())[HOME]["old"]
    and values[HOME]["802-11-wireless.cloned-mac-address"] == "",
)
check(
    "  public: names only, nothing secret",
    C.public() == {HOME: {"name": "Home", "changed": ["auth_retries", "metered"]}},
    C.public(),
)
said = C.undo(HOME)
check(
    "undo: put back as they were, the record gone",
    values[HOME]["connection.auth-retries"] == "-1"
    and values[HOME]["connection.metered"] == "unknown"
    and not C.RECORD.exists()
    and said["id"] == "nm-conn.undo.done",
    said,
)

for bad, why in ((NP, "nm-conn.netplan"), (AP, "nm-conn.hotspot")):
    try:
        C.change(bad, {"metered": "yes"})
        check(f"refused: {why}", False)
    except Refused as exc:
        check(f"refused: {why}", exc.message["id"] == why, exc.message)
C.change(NP, {"metered": "yes"}, integrated=True)
check(
    "a netplan connection where NetworkManager keeps it in netplan: allowed", values[NP]["connection.metered"] == "yes"
)
for changes, why, args in (
    ({"band": "c"}, "nm-conn.setting.one-of", {"setting": "band", "values": ["(none)", "a", "bg"]}),
    ({"priority": "1000"}, "nm-conn.setting.range", {"lo": -999, "hi": 999, "setting": "priority"}),
    ({"bssid": "nope"}, "nm-conn.setting.bssid", None),
    ({"channel": "6"}, "nm-conn.channel-band", None),
    ({"bogus": "1"}, "nm-conn.request.setting", {"setting": "bogus"}),
    ({}, "nm-conn.request.empty", None),
):
    try:
        C.change(HOME, changes)
        check(f"refused: {changes}", False)
    except Refused as exc:
        check(
            f"refused: {changes}",
            exc.message["id"] == why and (args is None or exc.message["args"] == args),
            exc.message,
        )
C.change(HOME, {"band": "bg", "channel": "6", "bssid": "04:95:E6:72:B6:D1"})
check(
    "a channel with its band; an access point's address in lower case",
    values[HOME]["802-11-wireless.channel"] == "6" and values[HOME]["802-11-wireless.bssid"] == "04:95:e6:72:b6:d1",
)
check(
    "undo-all, in English for uninstall.sh",
    "restored the earlier settings of Home" in C.undo_all()
    and not C.RECORD.exists()
    and values[HOME]["802-11-wireless.band"] == "",
)

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
