# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The launcher (./irate-box) turns on faulthandler for every module it runs: a hub started through it, with
throwaway state, prints each thread's stack to stderr on SIGUSR1 and keeps running. python3 tests/sim_faulthandler.py"""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


T = Path(tempfile.mkdtemp(prefix="faulthandler-"))
(T / "state").mkdir()
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
err = open(T / "stderr.log", "wb")
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"],
    env=dict(
        os.environ, HUB_STATE_DIR=str(T / "state"), PORT=str(port), HUB_BIND="127.0.0.1", HUB_FRONT_SECRET="x" * 48
    ),
    stdout=subprocess.DEVNULL,
    stderr=err,
)
try:
    up = False
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1).read()
            up = True
            break
        except OSError:
            time.sleep(0.1)
    check("the hub, started through the launcher, answers", up, (T / "stderr.log").read_text(errors="replace")[-400:])
    hub.send_signal(signal.SIGUSR1)
    time.sleep(1)
    text = (T / "stderr.log").read_text(errors="replace")
    check(
        "SIGUSR1: every thread's stack printed, newest call first",
        "most recent call first" in text and text.count("Thread 0x") >= 1,
        text[-600:],
    )
    check("  and the hub still running", hub.poll() is None)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=3) as r:
        check("  still answering", r.status == 200)
finally:
    hub.terminate()
    hub.wait(10)
    err.close()

sys.exit(1 if fails else 0)
