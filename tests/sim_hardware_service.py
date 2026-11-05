# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The display service (root/hardware_service.py), offline: one panel opened for its lifetime, on, off,
card, draw and test over its socket, its state published, a bad request refused, and a stop and start
that leave no socket behind. No spidev or GPIO is touched. python3 tests/sim_hardware_service.py"""

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="hardware-service-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_HARDWARE_RUN=str(T / "run"))
sys.path.insert(0, str(REPO))
from irate_box.root import hardware_service, lcdpanel

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


class FakePanel:
    made = 0
    closed = 0
    fail_open = False

    def __init__(self):
        if FakePanel.fail_open:
            raise OSError("spidev busy")
        FakePanel.made += 1
        self.calls = []

    def init(self):
        self.calls.append("init")

    def card(self):
        self.calls.append("card")

    def off(self):
        self.calls.append("off")

    def run(self, seconds):
        self.calls.append(("run", seconds))
        time.sleep(seconds)

    def close(self):
        FakePanel.closed += 1
        self.calls.append("close")


published = []
hardware_service.publish = lambda on: published.append(bool(on))
lcdpanel.Panel = FakePanel
hardware_service.SOCKET = T / "run" / "control.sock"
hardware_service.SOCKET.parent.mkdir(parents=True, exist_ok=True)


def wait_answer(timeout=5):
    """Wait until the service is listening as well as bound: the socket file appears first."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        answer = hardware_service.request("status")
        if answer and answer.get("ok"):
            return answer
        time.sleep(0.02)
    return None


stop = threading.Event()
thread = threading.Thread(target=hardware_service.serve, kwargs={"stop": stop}, daemon=True)
thread.start()
ready = wait_answer()
check("the service makes its socket and answers", hardware_service.SOCKET.exists() and ready is not None, ready)

answer = hardware_service.request("card")
check("card: the panel initialised once and the card drawn", answer and answer["ok"] and answer["on"] is True)
check("  its state is published", published == [True], published)
answer = hardware_service.request("off")
check("off: the panel blanked", answer and answer["ok"] and answer["on"] is False and published[-1] is False)
answer = hardware_service.request("draw")
check("draw: the test card again", answer and answer["ok"] and answer["on"] is True)
before = time.monotonic()
answer = hardware_service.request("test", seconds=3)
elapsed = time.monotonic() - before
check(
    "test: a run longer than the old two-second wait still answers",
    answer and answer["ok"] and answer["on"] is False and elapsed >= 3,
    (answer, elapsed),
)
check("  one panel for the whole lifetime: no second spidev open", FakePanel.made == 1, FakePanel.made)
answer = hardware_service.request("nonsense")
check("an unknown operation is refused, and the service stays", answer and answer["ok"] is False)

stop.set()
thread.join(timeout=5)
check(
    "stop: the socket gone and the panel closed once",
    not hardware_service.SOCKET.exists() and FakePanel.closed == 1 and not thread.is_alive(),
    (hardware_service.SOCKET.exists(), FakePanel.closed, thread.is_alive()),
)

# Start again: a fresh service and a fresh panel, and the socket comes back.
done = threading.Event()
thread2 = threading.Thread(target=hardware_service.serve, kwargs={"stop": done}, daemon=True)
thread2.start()
wait_answer()
answer = hardware_service.request("status")
check("start again: the socket is back and answers", answer and answer["ok"], answer)
answer = hardware_service.request("card")
check(
    "  its first operation opens a fresh panel",
    answer and answer["ok"] and FakePanel.made == 2,
    (answer, FakePanel.made),
)
done.set()
thread2.join(timeout=5)
check("  and stops cleanly", not hardware_service.SOCKET.exists() and FakePanel.closed == 2)

# A panel that cannot open answers the request and leaves the service alive.
FakePanel.fail_open = True
stop_bad = threading.Event()
thread_bad = threading.Thread(target=hardware_service.serve, kwargs={"stop": stop_bad}, daemon=True)
thread_bad.start()
wait_answer()
answer = hardware_service.request("card")
check(
    "a panel that cannot open: refused, and the service still answers status",
    answer and answer["ok"] is False and "spidev busy" in answer["message"] and hardware_service.request("status"),
    answer,
)
FakePanel.fail_open = False
answer = hardware_service.request("card")
check("  the next request opens it, and works", answer and answer["ok"] is True, answer)
stop_bad.set()
thread_bad.join(timeout=5)

# A request that is not JSON gets an answer, not a crash.
stop3 = threading.Event()
thread3 = threading.Thread(target=hardware_service.serve, kwargs={"stop": stop3}, daemon=True)
thread3.start()
wait_answer()
import socket

with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
    client.connect(str(hardware_service.SOCKET))
    client.sendall(b"not json\n")
    reply = json.loads(client.recv(4096).decode())
check(
    "a request that is not JSON: refused, the service still answering",
    reply["ok"] is False and hardware_service.request("status"),
)
stop3.set()
thread3.join(timeout=5)

# When the service is there, lcdpanel asks it instead of opening the device a second time.
asked = []
real_request = hardware_service.request
hardware_service.request = lambda op, **fields: asked.append((op, fields)) or {"ok": True}
opened = FakePanel.made
lcdpanel.show_card()
lcdpanel.off()
lcdpanel.run_test(2)
check(
    "lcdpanel goes through the service when it is running, and opens nothing itself",
    FakePanel.made == opened and asked == [("card", {}), ("off", {}), ("test", {"seconds": 2})],
    (FakePanel.made, opened, asked),
)
hardware_service.request = lambda op, **fields: (_ for _ in ()).throw(
    hardware_service.ServiceTimeout("The display service did not answer in time.")
)
opened = FakePanel.made
try:
    lcdpanel.run_test(1)
    failed = None
except RuntimeError as exc:
    failed = str(exc)
check(
    "a service that does not answer: refused, and the device is not opened behind it",
    failed == "The display service did not answer in time." and FakePanel.made == opened,
    (failed, FakePanel.made, opened),
)
hardware_service.request = real_request

print(f"failures: {fails}")
sys.exit(1 if fails else 0)
