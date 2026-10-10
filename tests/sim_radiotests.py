# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The box's experiments on its own radios (root/radiotests.py), offline with iw, nmcli and sysfs stood in:
refused while guests are on the hotspot or when there is nothing to try; the hotspot following, staying or
going down when the link moves channel, and the link moved back; a radio reset timed; the driver's own reset
seen by its count; answers kept per radio and driver version.
python3 tests/sim_radiotests.py"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="radiotests-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_SYSFS=str(T / "sys"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from irate_box.root import radiotests as R  # noqa: E402

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


params = T / "sys/module/aic8800_fdrv/parameters"
params.mkdir(parents=True)
(T / "sys/module/aic8800_fdrv/version").write_text("6.4.3.0\n")
(params / "recoveries").write_text("0\n")
(params / "fake_cmd_timeout").write_text("0\n")
radio = {"iface": "wlan0", "type": "managed", "phy": "phy0", "driver": "aic8800_fdrv",
         "usb": {"id": "a69c:88dc", "port": "1-1.1", "product": "AIC8800DC"},
         "profile": {"uuid": "0c3bb1fa-0f6a-3707-8c8d-4e23598253db"},
         "link": {"bssid": "04:95:e6:72:b6:d1", "channel": 11},
         "roaming": {"aps": [{"bssid": "04:95:e6:72:b6:d1", "channel": 11, "signal": 100}, {"bssid": "50:0f:f5:ad:15:e1", "channel": 6, "signal": 90}]}}
inv = {"radios": [radio, {"iface": "ap0", "type": "AP", "phy": "phy0"}]}
st = {"guests": 0, "ap": 11, "link": 11, "follow": True, "ap_dies": False, "reset": True}
ran = []
ran_locks = []


def fake_run(*cmd, timeout=60):
    ran.append(cmd)
    if cmd[:3] == ("iw", "dev", "ap0") and cmd[3:5] == ("station", "dump"):
        return 0, "Station aa:bb\n" * st["guests"]
    if cmd == ("iw", "dev"):
        return 0, ("Interface wlan0\n\tchannel %d (2462 MHz)\n" % st["link"]) + ("" if st["ap_dies"] else "Interface ap0\n\tchannel %d (2462 MHz)\n" % st["ap"])
    if cmd[:4] == ("iw", "dev", "wlan0", "link"):
        return 0, "Connected to 04:95:e6:72:b6:d1"
    if cmd[:4] == ("nmcli", "-g", "802-11-wireless.bssid", "con"):
        return 0, st.get("lock", "")
    if cmd[:3] == ("nmcli", "con", "modify"):
        st["lock"] = cmd[-1]
        ran_locks.append(cmd[-1])
        return 0, ""
    if cmd[:3] == ("nmcli", "con", "up"):
        st["link"] = 11 if st.get("ignore_lock") else (6 if st.get("lock") == "50:0f:f5:ad:15:e1" else 11)
        if st["follow"]:
            st["ap"] = st["link"]
        return 0, ""
    if cmd[:4] == ("iw", "dev", "wlan0", "scan"):
        if st["reset"] and (params / "fake_cmd_timeout").read_text().strip() == "1":
            (params / "recoveries").write_text(str(int((params / "recoveries").read_text()) + 1))
        return 0, ""
    return 0, ""


R.run = fake_run
nap = lambda s: None  # noqa: E731

st["guests"] = 1
try:
    R.check_ready(inv, "wlan0", "radio-reset"); check("refused with guests on the hotspot", False)
except ValueError as exc:
    check("refused with guests on the hotspot", "guests" in str(exc), str(exc))
st["guests"] = 0
alone = {"radios": [dict(radio, roaming={"aps": [radio["roaming"]["aps"][0]]}), inv["radios"][1]]}
for i, exp, why in ((alone, "follows-roam", "no access point on another channel"), ({"radios": [radio]}, "follows-roam", "no hotspot"),
                    (inv, "warp", "not an experiment"), (inv, "radio-reset", None)):
    try:
        R.check_ready(i, "wlan0", exp)
        check(f"ready: {exp}" if why is None else f"refused: {why}", why is None)
    except ValueError as exc:
        check(f"refused: {why}", why is not None and why in str(exc), str(exc))

said = R.experiment(inv, "wlan0", "follows-roam", sleep=nap)
key = R.radio_key(radio)
got = R.results()[key]["follows-roam"]
check("follows-roam: the hotspot moved with the link; kept per radio and driver version",
      got["answer"] == "follows" and "from channel 11 to 6" in got["detail"] and key == "a69c:88dc aic8800_fdrv 6.4.3.0", (key, got))
check("  the profile held to the target for the test, then given back what it had", ran_locks[-2:] == ["50:0f:f5:ad:15:e1", ""]
      and st["lock"] == "" and st["link"] == 11, ran_locks)
st.update(ignore_lock=True)
R.experiment(inv, "wlan0", "follows-roam", sleep=nap)
check("  a link that did not move: not counted as an answer", R.results()[key]["follows-roam"]["answer"] == "unknown"
      and "did not move" in R.results()[key]["follows-roam"]["detail"])
st.update(ignore_lock=False)
st.update(follow=False, ap=11)
R.experiment(inv, "wlan0", "follows-roam", sleep=nap)
check("  a hotspot that stays: said so", R.results()[key]["follows-roam"]["answer"] == "stays")
from irate_box.root import radio as radio_mod  # noqa: E402
restored = []
radio_mod.restore_hotspot = lambda *a, **k: restored.append(1) or "the hotspot started again"
st.update(ap_dies=True)
R.experiment(inv, "wlan0", "follows-roam", sleep=nap)
check("  a hotspot that went down: said so, and started again", R.results()[key]["follows-roam"]["answer"] == "doesn't" and restored)
st.update(ap_dies=False)

radio_mod.reset = lambda *a, **k: "USB 1-1.1 (AIC8800DC) reset at its port; wlan0 is back; the hotspot started again"
R.experiment(inv, "wlan0", "radio-reset", sleep=nap)
got = R.results()[key]["radio-reset"]
check("radio-reset: timed, the link and the hotspot back", got["answer"] == "works" and "the link back" in got["detail"] and "the hotspot back" in got["detail"], got)

R.experiment(inv, "wlan0", "driver-reset", sleep=nap)
got = R.results()[key]["driver-reset"]
check("driver-reset: the count moved, the link back", got["answer"] == "works" and "reset its device" in got["detail"], got)
st["reset"] = False
R.experiment(inv, "wlan0", "driver-reset", sleep=nap)
check("  a driver that didn't reset: said so", R.results()[key]["driver-reset"]["answer"] == "doesn't")
(params / "fake_cmd_timeout").unlink()
try:
    R.check_ready(inv, "wlan0", "driver-reset"); check("refused: a driver without a fake timeout", False)
except ValueError as exc:
    check("refused: a driver without a fake timeout", "no fake command timeout" in str(exc), str(exc))
check("the answers kept: three experiments for this radio", sorted(R.results()[key]) == ["driver-reset", "follows-roam", "radio-reset"])

shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
