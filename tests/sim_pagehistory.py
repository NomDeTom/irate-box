# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The pages' use (hub/pagehistory.py), offline: nginx's page loads read as they come (a half-written line left
for the next look), each counted against its page (the longest path it starts with), a failure a 5xx or a 404;
the log emptied by logrotate read from its start again; the hours kept to 72 days; the summary's cells blank for
an hour not used, all answered, or with failures. python3 tests/sim_pagehistory.py"""
import os
import sys
import tempfile
import time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="pagehist-"))
os.environ["HUB_STATE_DIR"] = str(T)
sys.path.insert(0, str(REPO))
from irate_box.hub import pagehistory as ph  # noqa: E402
fails = 0
def check(name, cond, _info=None):   # _info: for a debugger, never printed
    global fails
    print(("PASS " if cond else "FAIL ") + name); fails += not cond

PATHS = ["/draw/", "/flasher/", "/flasher/freeocd/", "/serial/"]
check("a path to its page: the longest that fits", ph.page_of("/flasher/freeocd/", PATHS) == "/flasher/freeocd/"
      and ph.page_of("/flasher/flasher.html", PATHS) == "/flasher/" and ph.page_of("/wiki/", PATHS) is None)
check("a failure: 5xx or 404; not a 401 (a login asked) or a 304", ph.failed(502) and ph.failed(404) and not ph.failed(401) and not ph.failed(304))
now = time.time()
hour = int(now // 3600) * 3600
log = T / "pagelog" / "pages.log"
log.parent.mkdir()
log.write_text(f"{hour + 10:.3f} 200 /draw/index.html\n{hour + 20:.3f} 200 /draw/\n{hour + 30:.3f} 404 /flasher/freeocd/\n"
               f"{hour + 40:.3f} 200 /wiki/\n{hour + 50:.3f} 200 /serial/ha")
h = {}
n = ph.read_new(h, PATHS, log)
check("the new lines counted, a page not watched passed over, the half-written one left", n == 3
      and h["pages"]["/draw/"]["u"] == [2] and h["pages"]["/flasher/freeocd/"]["e"] == [1] and "/serial/" not in h["pages"], h)
with open(log, "a") as fh:
    fh.write(f"lf.html\n{hour + 60:.3f} 500 /serial/\n")
ph.read_new(h, PATHS, log)
check("  the next look takes up where it stopped, the half line now whole", h["pages"]["/serial/"]["u"] == [2] and h["pages"]["/serial/"]["e"] == [1], h["pages"].get("/serial/"))
log.write_text(f"{hour + 70:.3f} 200 /draw/\n")
ph.read_new(h, PATHS, log)
check("emptied by logrotate: read from its start again", h["pages"]["/draw/"]["u"] == [3], h["pages"]["/draw/"])
ph.add(h, "/draw/", hour - 80 * 86400, False)
check("an hour older than kept: not counted", h["pages"]["/draw/"]["u"] == [3])
ph.add(h, "/draw/", hour + 100 * 86400, False)
check("  and the record held to 72 days as it grows", len(h["pages"]["/draw/"]["u"]) == ph.KEEP)
h2 = {}
for at, st, uri in ((hour - 7200, 200, "/draw/"), (hour + 5, 200, "/draw/"), (hour + 6, 502, "/draw/")):
    ph.add(h2, ph.page_of(uri, PATHS), at, ph.failed(st))
s = ph.summarize(h2, now)["pages"]["/draw/"]
check("the summary: 72 hours, the last with 2 loads and 1 failure; one hour blank between", len(s["hours"]) == 72
      and s["hours"][-1] == {"up": 0.5, "n": 2, "uses": 2, "errors": 1} and s["hours"][-2] is None
      and s["hours"][-3] == {"up": 1.0, "n": 1, "uses": 1, "errors": 0} and s["summary"] == {"uses": 3, "errors": 1}, s["hours"][-3:])
check("  with the columns the services' heatmaps use", set(ph.summarize(h2, now)) >= {"hour_cols", "hour_full", "month_days", "pages"})
sampler = ph.Sampler(lambda: PATHS)
check("the sampler writes the record when the log moved, not when it didn't", sampler.tick() and ph.FILE.exists() and not sampler.tick())
import shutil  # noqa: E402
shutil.rmtree(T, ignore_errors=True)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
