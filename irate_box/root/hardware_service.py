# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The display service: one process owns the SPI panel and takes on, off, card, test and draw.

irate-box-hardware.service runs this as root (`hardware_service run`). It opens /dev/spidev1.0 and the
D/C and RST lines once and keeps them for its lifetime, so a second caller never opens the device while
it is in use. The root helper and the pin page talk to it over a Unix socket, one JSON request and one
JSON answer per line:

  {"op": "on"} | {"op": "off"} | {"op": "card"} | {"op": "draw"} | {"op": "test", "seconds": 60}
  {"op": "status"}

An operation also records the panel's state through lcdtouch and publishes the hardware status, so the
page reads the same display section wherever the change came from. If the service is not running,
lcdpanel falls back to opening the device itself, as before. Stdlib only.
"""

import json
import os
import signal
import socket
import threading
from pathlib import Path

RUN = Path(os.environ.get("HUB_HARDWARE_RUN", "/run/irate-box-hardware"))
SOCKET = RUN / "control.sock"
MAX_REQUEST = 64 * 1024


def publish(on):
    """Record the panel's state where the page reads it: lcdtouch's state and control/hardware.json."""
    from irate_box.root import hardware, lcdtouch

    lcdtouch.record_display(on)
    hardware.write_status()


def handle(panel, request):
    """One operation on the panel the service owns. Returns {ok, message, on}."""
    op = request.get("op")
    if op in ("on", "card", "draw"):
        panel.init()
        panel.card()
        publish(True)
        return {"ok": True, "message": "The test card is on the display.", "on": True}
    if op == "off":
        panel.off()
        publish(False)
        return {"ok": True, "message": "The display is off.", "on": False}
    if op == "test":
        try:
            seconds = max(1, min(int(request.get("seconds", 60)), 300))
        except (TypeError, ValueError):
            return {"ok": False, "message": "The test's time is not a number.", "on": None}
        try:
            panel.init()
            panel.run(seconds)
        finally:
            publish(False)
        return {"ok": True, "message": f"The {seconds}-second test ran. The display is off.", "on": False}
    if op == "status":
        return {"ok": True, "message": "The display service is running.", "on": None}
    return {"ok": False, "message": f"The display service has no operation {op!r}.", "on": None}


class ServiceTimeout(Exception):
    """The socket is there but the service did not answer in time: do not open the device behind it."""


def request(op, **fields):
    """Ask a running service; None when there is none, so the caller can open the device itself.

    A test runs for the whole sequence before it answers, so its wait is as long as that; a socket that
    exists but does not answer raises ServiceTimeout rather than letting the caller open the device.
    """
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(max(2, int(fields.get("seconds", 60)) + 15) if op == "test" else 2)
            client.connect(str(SOCKET))
            client.sendall((json.dumps({"op": op, **fields}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = client.recv(4096)
                if not chunk:
                    break
                data += chunk
    except (BrokenPipeError, ConnectionRefusedError, FileNotFoundError):
        return None
    except socket.timeout:
        raise ServiceTimeout("The display service did not answer in time.") from None
    except OSError:
        return None
    try:
        answer = json.loads(data.decode())
    except (ValueError, UnicodeDecodeError):
        return None
    return answer if isinstance(answer, dict) else None


def _answer(conn, panel, request):
    try:
        answer = handle(panel, request)
    except Exception as exc:  # noqa: BLE001 - the panel driver says why, and the service stays up
        answer = {"ok": False, "message": f"The display could not be changed: {str(exc)[:160]}", "on": None}
    try:
        conn.sendall((json.dumps(answer) + "\n").encode())
    except OSError:
        pass


def serve(stop=None, socket_path=None):
    """The service: own the panel and answer requests until it is stopped."""
    from irate_box.root.lcdpanel import Panel

    stop = stop or threading.Event()
    path = Path(socket_path or SOCKET)
    path.parent.mkdir(parents=True, exist_ok=True)
    serv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        path.unlink(missing_ok=True)
        serv.bind(str(path))
        os.chmod(path, 0o600)
        serv.listen(8)
        serv.settimeout(1)
        panel = None
        stopping = {"now": False}

        def on_stop(_signum=None, _frame=None):
            stopping["now"] = True

        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, on_stop)
            signal.signal(signal.SIGINT, on_stop)
        while not (stopping["now"] or stop.is_set()):
            try:
                conn, _ = serv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(5)
                data = b""
                while not data.endswith(b"\n") and len(data) < MAX_REQUEST:
                    try:
                        chunk = conn.recv(4096)
                    except OSError:
                        chunk = b""
                    if not chunk:
                        break
                    data += chunk
                try:
                    request = json.loads(data.decode() or "{}")
                except (ValueError, UnicodeDecodeError):
                    request = None
                if not isinstance(request, dict):
                    try:
                        conn.sendall(b'{"ok": false, "message": "The request is not valid.", "on": null}\n')
                    except OSError:
                        pass
                    continue
                if request.get("op") == "close":
                    break
                if request.get("op") != "status" and panel is None:
                    try:
                        panel = Panel()
                    except Exception as exc:  # noqa: BLE001 - a missing or busy device must not kill the service
                        try:
                            conn.sendall(
                                (
                                    json.dumps(
                                        {
                                            "ok": False,
                                            "message": f"The display could not be opened: {str(exc)[:160]}",
                                            "on": None,
                                        }
                                    )
                                    + "\n"
                                ).encode()
                            )
                        except OSError:
                            pass
                        continue
                _answer(conn, panel, request)
        if panel is not None:
            panel.close()
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        serv.close()
    return 0


def main(argv):
    if argv == ["run"]:
        return serve()
    if argv == ["status"]:
        print(json.dumps({"socket": str(SOCKET), "running": SOCKET.exists()}, indent=1))
        return 0
    print("Usage: irate-box hardware_service run | status", file=os.sys.stderr)
    return 2


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
