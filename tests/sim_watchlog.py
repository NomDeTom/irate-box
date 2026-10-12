# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The watchers' one record (hub/watchlog.py), offline: lines added and read back by time, boot and
kind; trimmed to its days; the uplink watchdog's events shared into it before it acts; crash watch's
radio notes and the driver's own reset in it; why each boot happened; a filed radio failure and a
filed crash carrying its lines; the box doctor's card. python3 tests/sim_watchlog.py"""
import os, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="watchlog-"))
PROC = T / "proc"
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_ETC_DIR=str(T / "etc"), HUB_PROC=str(PROC), HUB_SYSFS=str(T / "sys"),
                  HUB_KMSG=str(T / "no-kmsg"))
(T / "etc").mkdir()
(PROC / "sys/kernel/random").mkdir(parents=True)
(PROC / "sys/kernel/random/boot_id").write_text("boot-one\n")
sys.path.insert(0, str(REPO))
from irate_box.hub import uplink, watchlog  # noqa: E402
from irate_box.root import crashwatch as cw, health  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}")); fails += not cond

now = time.time()
watchlog.add("watchdog", "down", "Link lost.", "wlan0", at=now - 300)
watchlog.add("crashwatch", "failed", "wlan0 is gone", "wlan0", at=now - 200)
watchlog.add("watchdog", "info", "Watching.", "wlan0", at=now - 100)
rows = watchlog.read()
check("lines added and read back in order, each with its boot and who wrote it",
      [r["kind"] for r in rows] == ["down", "failed", "info"] and all(r["boot"] == "boot-one" for r in rows) and rows[1]["by"] == "crashwatch", rows)
check("  by time, and by kind", [r["kind"] for r in watchlog.read(since=now - 250)] == ["failed", "info"]
      and [r["kind"] for r in watchlog.read(kinds=watchlog.NOTABLE)] == ["down", "failed"])
check("  one as a line of text: who, which interface, what", watchlog.line(rows[1]).endswith("crash watch wlan0 failed: wlan0 is gone"), watchlog.line(rows[1]))
old_max = watchlog.MAX_BYTES
watchlog.MAX_BYTES = 10
watchlog.add("box", "boot", "ancient", at=now - 30 * 86400)
watchlog.add("box", "boot", "new", at=now)
watchlog.MAX_BYTES = old_max
check("trimmed to its days once past its size", [r["text"] for r in watchlog.read()][-1] == "new"
      and "ancient" not in [r["text"] for r in watchlog.read()], [r["text"] for r in watchlog.read()])
watchlog.LOG.write_text(watchlog.LOG.read_text() + "not json\n")
check("  a broken line is passed over", len(watchlog.read()) >= 1)
watchlog.LOG.unlink()

# The uplink watchdog: its events shared before it acts (a reboot leaves no time after).
w = uplink.Watch(uplink.effective(dict(uplink.DEFAULT, pace="brisk", reach="reboot", on_wedge="reboot")))
for t in range(0, 300, 30):
    acts = w.tick(now + t, {"link": False, "drops": [], "can": {"reconnect", "restart", "radio", "reboot"}, "guests": 0, "uptime": 1e9,
                            "cause": "radio"})
    uplink.share(w, "wlan0", dry=False)
kinds = [(r["by"], r["kind"], r.get("step")) for r in watchlog.read()]
check("the watchdog's outage, its cause, the radio reset and the reboot: all in the record, as they happened",
      ("watchdog", "down", None) in kinds and ("watchdog", "cause", None) in kinds and ("watchdog", "repair", "radio") in kinds
      and ("watchdog", "repair", "reboot") in kinds and w.shared == [], kinds)
check("  the cause kept as its own field", [r.get("cause") for r in watchlog.read(kinds={"cause"})] == ["radio"])
uplink.share(w, "wlan0", dry=True)
check("  a dry run writes nothing", len(watchlog.read()) == len(kinds))

# Why the boot happened.
cw.RUN.parent.mkdir(parents=True, exist_ok=True)
check("boot reason: the first", cw.boot_reason(None) == "the first boot crash watch has seen")
check("  stopped without shutting down", "without shutting down" in cw.boot_reason({"boot": "boot-zero", "clean": False}))
check("  clean, after the watchdog's reboot: said, with its words",
      cw.boot_reason({"boot": "boot-one", "clean": True, "stopped": now + 300}).startswith("the uplink watchdog rebooted it: Reboot"),
      cw.boot_reason({"boot": "boot-one", "clean": True, "stopped": now + 300}))
check("  clean, nothing asked for it: by hand or the system", "cleanly" in cw.boot_reason({"boot": "boot-zero", "clean": True, "stopped": now}))
cw._write(cw.RUN, {"boot": "boot-one", "at": now - 1000, "clean": True, "stopped": now + 300})
(PROC / "sys/kernel/random/boot_id").write_text("boot-two\n")
cw.boot(now=now + 400)
b = watchlog.read(kinds={"boot"})
check("the next boot writes its reason into the record", b and b[-1]["boot"] == "boot-two" and "uplink watchdog rebooted it" in b[-1]["text"], b)
cw.shutdown()
check("  a clean stop notes when", cw._json(cw.RUN, {}).get("stopped"))

# Crash watch's notes, and a filed radio failure carrying the watchers' lines.
watchlog.add("crashwatch", "failed", "wlan0 is gone", "wlan0", at=now + 500)
watchlog.add("driver", "driver-reset", "the radio's driver reset it itself", at=now + 501)
rec = cw.file_radio(now + 510, "wlan0", "wlan0 is gone", ["[1.0] probe failed"])
filed = (cw.CRASHES / rec["dir"] / "watchers.log").read_text()
check("a filed radio failure carries the watchers' last ten minutes", "crash watch wlan0 failed" in filed and "the radio's driver driver-reset" in filed, filed)

f = {x["id"]: x for x in health.check_watchers(now=now + 600)}["watchers"]
check("the doctor: the last day in one line, starts and repairs counted, where it all is",
      "1 start of the box" in f["detail"] and "2 repairs by the watchdog" in f["detail"] and "watchers.jsonl" in f["fix"], f)
watchlog.LOG.unlink()
check("  nothing in the last day: said so", "Nothing in the last day" in health.check_watchers()[0]["detail"])

# The doctor's overview, from the hub: the last start and why, the last repair, the link now.
from irate_box.hub import server  # noqa: E402
watchlog.add("box", "boot", "Started: it was shut down or restarted cleanly (by hand, or by the system).", at=now + 700)
watchlog.add("watchdog", "repair", "Reconnect (2 min down).", "wlan0", at=now + 800, step="reconnect")
(watchlog.LOG.parent / "uplink.json").write_text('{"at": 1, "state": "down", "iface": "wlan0", "cause": "absent", "events": []}')
gl = server.watchers_glance()
check("the overview's watchers: the last start, the last repair, the link and its cause",
      gl["last_boot"]["text"].startswith("Started: it was shut down") and gl["last_repair"]["text"] == "Reconnect (2 min down)."
      and gl["link"] == {"at": 1, "state": "down", "iface": "wlan0", "cause": "absent"}, gl)

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
