# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A forged request does no more than the page offers (security review F14; next-work plan step
9): Security page choices only from root's last scan, root's own records read only if they are
still root's, the RTC set up only where a search found one, no linked book exported, no leading
"-" in a name that becomes an argument, the clock's floor from root's files only and no jump
years ahead. Runs as any user ("root's" is this user here). python3 tests/sim_forged_requests.py"""
import json, os, re, sys, tempfile, time
from pathlib import Path
from unittest import mock
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="forged-"))
STATE = T / "state"
for d in (STATE / "control" / "results", STATE / "control" / "requests", STATE / "zim", T / "etc"):
    d.mkdir(parents=True)
os.environ.update(HUB_STATE_DIR=str(STATE), HUB_ETC_DIR=str(T / "etc"), HUB_RUN_DIR=str(T / "run"))
sys.path.insert(0, str(REPO))
from irate_box.root import hub_control, health, rtc, safeio, usbstick  # noqa: E402
from irate_box.hub import uplink, manifests  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
def refused(fn, *a, exc=(ValueError, OSError)):
    try:
        fn(*a); return False
    except exc:
        return True

# Security choices: only what root's last scan offered.
scan = {"findings": [{"id": "x", "actions": [{"choice": "unit-off:cups.service"}, {"choice": "kernel-links-on"}]}]}
hub_control.SECURITY_STATE.write_text(json.dumps(scan))
with mock.patch.object(hub_control.security, "fix", return_value="done") as fx, \
     mock.patch.object(hub_control.security, "scan", return_value=scan):
    check("a choice the scan offered goes through", hub_control.security_fix({"choice": "unit-off:cups.service"}) == "done")
    check("one it did not (auditd) is refused", refused(hub_control.security_fix, {"choice": "unit-off:auditd.service"}))
    check("a leading '-' is refused", refused(hub_control.security_fix, {"choice": "unit-off:-x.service"}))
    hub_control.SECURITY_STATE.unlink()
    forged = T / "forged-scan.json"; forged.write_text(json.dumps({"findings": [{"actions": [{"choice": "unit-off:auditd.service"}]}]}))
    os.symlink(forged, hub_control.SECURITY_STATE)
    check("a scan file that is a link is not believed", refused(hub_control.security_fix, {"choice": "unit-off:auditd.service"}))
    check("…and nothing was carried out", fx.call_count == 1, fx.call_args_list)
hub_control.SECURITY_STATE.unlink()

# read_own: a link, or a file in a folder swapped for a link, is not read.
(STATE / "control" / "update.json").write_text(json.dumps({"verified": "abc1234"}))
check("update.json: root's own file is read", hub_control._read_update_state() == {"verified": "abc1234"})
(STATE / "control" / "update.json").unlink()
v = T / "forged-update.json"; v.write_text(json.dumps({"verified": "abc1234"}))
os.symlink(v, STATE / "control" / "update.json")
check("update.json that is a link: not believed (no 'verified')", hub_control._read_update_state() == {})
real = STATE / "control"; os.rename(real, STATE / "control.real")
fake = T / "fake-control"; fake.mkdir(); (fake / "update.json").write_text(json.dumps({"verified": "abc1234"}))
os.symlink(fake, real)
check("control/ swapped for a link: not believed", hub_control._read_update_state() == {})
os.unlink(real); os.rename(STATE / "control.real", real)

# RTC: only an address clocks use (the "found" check itself is in sim_rtc.py).
check("rtc-setup at an address no clock uses: refused", refused(rtc.setup, "ds3231", 1, "0x10", exc=(rtc.RtcError,)))

# The clock's floor: root's own files, never through a link; no jump years ahead.
target = T / "future"; target.write_text("x"); os.utime(target, (time.time() + 9e7, time.time() + 9e7))
link = T / "link"; os.symlink(target, link)
check("floor: a link's target time does not count", rtc.root_mtime(link) is None)
check("floor: a plain file of root's counts", rtc.root_mtime(target) is not None)
check("floor: the hub's clock.json is not a source", "clock.json" not in re.search(r"def floor_time.*?return", (REPO / "irate_box/root/rtc.py").read_text(), re.S).group(0))
with mock.patch.object(health, "clock_facts", return_value={"synced": False, "daemon": None}), \
     mock.patch.object(health, "latest_known_time", return_value=(time.time(), "x")), \
     mock.patch.object(health, "run") as run:
    check("clock-set: three years ahead is refused", refused(health.set_clock, int(time.time() + 3 * 365 * 86400)))
    check("…and the clock was not touched", not run.called)

# USB export: a book that is a link is not copied.
secret = T / "secret"; secret.write_text("root's")
os.symlink(secret, STATE / "zim" / "stolen.zim")
check("usb-export of a linked book: refused before any stick is touched",
      refused(usbstick.export_zim, "sdz1", "stolen", str(STATE / "zim")))
check("_copy refuses a source that is a link", refused(usbstick._copy, STATE / "zim" / "stolen.zim", T / "out"))

# Names that become arguments: no leading "-".
check("interface names: no leading '-'", not hub_control.IFACE_RE.match("-x") and hub_control.IFACE_RE.match("wlan0"))
check("uplink interface names: no leading '-'", not uplink.IFACE_RE.match("-x") and uplink.IFACE_RE.match("auto"))
check("unit names: no leading '-'", not hub_control.security.UNIT_RE.match("-x.service") and not manifests.UNIT_RE.match("-x.service"))
check("uplink: the reboot history is root's own record, not the status file's",
      'REBOOTS = ETC / "uplink-reboots.json"' in (REPO / "irate_box/hub/uplink.py").read_text())

# F11: an app bundle's source is its manifest's; a request chooses the branch at most.
from irate_box.library import librarian, firmware  # noqa: E402
got = librarian.validate_source({"kind": "app", "name": "draw", "type": "nightly-link", "repo": "evil/excalidraw",
                                 "workflow": "evil.yml", "pattern": "*", "branch": "dev"})
spec = librarian.APPS["draw"]["source"]
check("F11: a forged repo, workflow and pattern are replaced by the manifest's",
      (got["repo"], got["workflow"], got["pattern"]) == (spec["repo"], spec["workflow"], spec["pattern"]), got)
check("F11: the branch may be chosen", got["branch"] == "dev")
check("F11: but not one that is an option", refused(librarian.validate_source, {"kind": "app", "name": "draw", "type": "nightly-link", "branch": "-x"},
                                                    exc=(librarian.LibrarianError,)))
# F10: a deps-zip member cannot leave its folder.
check("F10: '//' in a member name is left out", firmware._subset("pio-deps-x//etc/cron.d/x", "whole") is None)
check("F10: '..' is left out", firmware._subset("pio-deps-x/packages/../../x", "whole") is None)
check("F10: a normal member is kept", firmware._subset("pio-deps-x/packages/tool/bin/gcc", "whole") == "packages/tool/bin/gcc")

# F22: books and code over encrypted transports only.
check("F22: a book url over http is refused", refused(librarian.validate_source, {"name": "b", "type": "url", "url": "http://x/b.zim"},
                                                     exc=(librarian.LibrarianError,)))
check("F22: over https it is taken", librarian.validate_source({"name": "b", "type": "url", "url": "https://x/b.zim"})["url"] == "https://x/b.zim")
(T / "etc" / "install-options").write_text("--repo\nhttp://example.invalid/irate-box\n--branch\nmain\n")
check("F22: updates from an http:// repository are refused before any fetch",
      refused(hub_control._check_for_update, hub_control.Progress("check", 2, path=T / "p.json")))
# F21: "verified" is matched on the whole hash.
src = (REPO / "irate_box/root/hub_control.py").read_text()
check("F21: install compares the whole hash that passed", 'state.get("verified_sha") != full' in src
      and 'old.get("verified_sha") == full' in src)

print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
