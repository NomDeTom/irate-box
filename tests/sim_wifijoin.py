# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Networks the box may join, added on the Network page's access tab (root/wifijoin.py): checked,
written as a root-only keyfile (the password never on a command line), loaded into NetworkManager below the
owner's own profiles; joined at once only when asked, and back on the network it was on when that fails;
forgotten only when added here; kept, and said, at uninstall. python3 tests/sim_wifijoin.py"""
import json, os, stat, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="wifijoin-"))
os.environ.update(HUB_ETC_DIR=str(T / "etc"), HUB_NM_CONNECTIONS=str(T / "nm"))
sys.path.insert(0, str(REPO))
from irate_box.root import wifijoin as W  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
calls = []
up_fails = set()
def fake(*cmd, timeout=60):
    calls.append(cmd)
    if cmd[:4] == ("nmcli", "-t", "-f", "RUNNING"):
        return 0, "running"
    if cmd[:3] == ("nmcli", "connection", "up") and cmd[4] in up_fails:
        return 4, "Error: Connection activation failed: Secrets were required, but not provided."
    if "--active" in cmd:
        return 0, "1111aaaa-0000-4000-8000-000000000001:802-11-wireless\n2222:loopback"
    return 0, ""
W.run = fake
for bad, why in ((("", "wpa-psk", "password1", False), "no name"), (("x" * 33, "wpa-psk", "password1", False), "too long"),
                 (("Café\x07", "wpa-psk", "password1", False), "a control character"), ((" Home", "wpa-psk", "password1", False), "a space first"),
                 (("Home", "wep", "password1", False), "WEP"), (("Home", "wpa-psk", "short", False), "a short password"),
                 (("Home", "open", "password1", False), "a password on an open network"), (("Home", "sae", "password1", "yes"), "hidden not a bool")):
    try:
        W.validate(*bad); check(f"refused: {why}", False)
    except ValueError as exc:
        check(f"refused: {why}", "password1" not in str(exc))
W.validate("Café Wi-Fi ☕", "wpa-psk", "a" * 64 if False else "correct horse", False)
check("a name in any language, with spaces inside", True)
msg = W.join("Home Net", "wpa-psk", "s3cret-pass;x", hidden=True)
files = list((T / "nm").glob("irate-box-*.nmconnection"))
text = files[0].read_text() if files else ""
check("added: one keyfile, root's alone (0600), the network, its password, hidden, below the owner's own", len(files) == 1
      and stat.S_IMODE(files[0].stat().st_mode) == 0o600 and "ssid=Home Net" in text and "psk=s3cret-pass;x" in text
      and "hidden=true" in text and "key-mgmt=wpa-psk" in text and "autoconnect-priority=-10" in text)
check("  loaded into NetworkManager, the password on no command line", any(c[:3] == ("nmcli", "connection", "load") for c in calls)
      and not any("s3cret" in " ".join(c) for c in calls))
check("  said, without the password; not joined now", "Home Net added" in msg and "s3cret" not in msg and not any(c[:3] == ("nmcli", "connection", "up") for c in calls))
rec = W.record()
uid = next(iter(rec))
check("  recorded (root's, 0600), without the password", rec[uid]["ssid"] == "Home Net" and "s3cret" not in json.dumps(rec)
      and stat.S_IMODE(W.RECORD.stat().st_mode) == 0o600)
check("  the page's list has no password", "s3cret" not in json.dumps(W.public()) and W.public()[0]["ssid"] == "Home Net")
try:
    W.join("Home Net", "wpa-psk", "another-pass"); check("the same name again: refused (forget it first)", False)
except ValueError:
    check("the same name again: refused (forget it first)", True)
calls.clear()
up_fails.clear()
import uuid as _u
real = _u.uuid4
_u.uuid4 = lambda: _u.UUID("33333333-3333-4333-8333-333333333333")
up_fails.add("33333333-3333-4333-8333-333333333333")
msg = W.join("Cafe", "sae", "wrong-password", now=True, sleep=lambda s: None)
_u.uuid4 = real
ups = [c[4] for c in calls if c[:3] == ("nmcli", "connection", "up")]
check("join now, and it fails: back up on the network it was on, said so, kept to join when in reach",
      ups == ["33333333-3333-4333-8333-333333333333", "1111aaaa-0000-4000-8000-000000000001"] and "back on the network it was on" in msg
      and "wrong-password" not in msg, ups)
up_fails.clear()
msg = W.join("Open Cafe", "open", "", now=True)
check("open, joined now: no security section, on it", "[wifi-security]" not in [f.read_text() for f in (T / "nm").glob("*.nmconnection") if "Open Cafe" in f.read_text()][0]
      and "on it now" in msg)
try:
    W.forget("1111aaaa-0000-4000-8000-000000000001"); check("forget the owner's own network: refused", False)
except ValueError as exc:
    check("forget the owner's own network: refused", "added on this page" in str(exc))
calls.clear()
msg = W.forget(uid)
check("forget one added here: deleted in NetworkManager, its keyfile gone, off the record", ("nmcli", "connection", "delete", "uuid", uid) in calls
      and not any("Home Net" in f.read_text() for f in (T / "nm").glob("*.nmconnection")) and uid not in W.record())
check("uninstall keeps the rest, and says how to remove them", "Cafe" in W.kept_on_uninstall() and "nmcli connection delete uuid" in W.kept_on_uninstall())
os.environ["HUB_ETC_DIR"] = str(T / "etc2")
srv = (REPO / "irate_box/hub/server.py").read_text()
check("the hub passes join and forget to the root helper, the password nowhere else", '"action": "wifi-join"' in srv and '"action": "wifi-forget"' in srv)
hc = (REPO / "irate_box/root/hub_control.py").read_text()
check("the root helper knows both", '"wifi-join": wifi_join' in hc and '"wifi-forget": wifi_forget' in hc)
check("uninstall.sh says what it kept", "wifijoin kept" in (REPO / "uninstall.sh").read_text())
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
