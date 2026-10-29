# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Lyra's BOOT key as a clean-shutdown button, held for three seconds.

U-Boot reads the BOOT key only at power-on (held, it gives Loader mode). While the box runs nothing uses it. On
the Lyra Zero W it is SARADC_IN1, whose reading falls from about 1019 to about 9 while pressed. This watches that
reading and, after a three-second hold, asks systemd to power the box off cleanly. A short press does nothing, and
holding the key at power-on still gives Loader mode because this runs only after the system is up.

  irate-box bootkey run              watch the key (the service)
  irate-box bootkey install-units    install and start the service
  irate-box bootkey uninstall-units  stop and remove it

A box without the ADC (a Pi) simply has no reading: the service does nothing. Stdlib only.
"""

import os
import sys
import time
from pathlib import Path

from irate_box.msg import M, Refused

ADC = Path(os.environ.get("HUB_BOOTKEY_ADC", "/sys/bus/iio/devices/iio:device0/in_voltage1_raw"))
THRESHOLD = int(os.environ.get("HUB_BOOTKEY_THRESHOLD", "500"))
HOLD = float(os.environ.get("HUB_BOOTKEY_HOLD", "3"))
POLL = float(os.environ.get("HUB_BOOTKEY_POLL", "0.2"))
UNIT = "irate-box-bootkey.service"
SYSTEMD = Path(os.environ.get("HUB_SYSTEMD_ETC", "/etc/systemd")) / "system"
CODE = Path(os.environ.get("HUB_CODE_DIR", "/opt/irate-box"))


def run(*cmd, timeout=60):
    import subprocess  # Fixed argv, trusted executables, no shell. # nosec B404

    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: TID251
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


def adc_present(path=None):
    try:
        return (Path(path) if path else ADC).is_file()
    except OSError:
        return False


def read_value(path=None):
    """The ADC reading, or None when the board has no such pin."""
    try:
        return int((Path(path) if path else ADC).read_text().strip())
    except (OSError, ValueError):
        return None


def watch(
    reader=read_value, clock=time.monotonic, poweroff=None, hold=HOLD, threshold=THRESHOLD, poll=POLL, stop_after=None
):
    """Wait for a hold of `hold` seconds below `threshold`, then power the box off. A press shorter than that is
    ignored. Testable with a reader, a clock and a limit on the reads stood in."""
    poweroff = poweroff or (lambda: run("systemctl", "poweroff", timeout=30))
    started = None
    reads = 0
    while True:
        if stop_after is not None and reads >= stop_after:
            return None
        reads += 1
        value = reader()
        now = clock()
        if value is not None and value < threshold:
            if started is None:
                started = now
            elif now - started >= hold:
                poweroff()
                return M(
                    "bootkey.shutdown", "The BOOT key was held for {seconds} seconds: shutting down.", seconds=hold
                )
        else:
            started = None
        time.sleep(poll)


def unit_text():
    return (
        "[Unit]\nDescription=Irate-Box: the BOOT key held for three seconds shuts down cleanly\n"
        "After=multi-user.target\n\n[Service]\nType=simple\n"
        f"ExecStart={CODE}/irate-box bootkey run\n"
        "Restart=on-failure\nRestartSec=5\n"
        "ProtectSystem=strict\nReadOnlyPaths=/sys\nPrivateTmp=yes\nNoNewPrivileges=yes\n\n"
        "[Install]\nWantedBy=multi-user.target\n"
    )


def installed():
    return (SYSTEMD / UNIT).exists()


def install_units(on=True):
    if not on:
        run("systemctl", "disable", "--now", UNIT)
        (SYSTEMD / UNIT).unlink(missing_ok=True)
        run("systemctl", "daemon-reload")
        return M("bootkey.off", "The BOOT key no longer shuts the box down.")
    if not adc_present():
        raise Refused(M("bootkey.no-adc", "This board has no BOOT key reading to watch."))
    SYSTEMD.mkdir(parents=True, exist_ok=True)
    (SYSTEMD / UNIT).write_text(unit_text())
    (SYSTEMD / UNIT).chmod(0o644)
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", UNIT)
    return M("bootkey.on", "Holding the BOOT key for three seconds now shuts the box down cleanly.")


def main(argv):
    command = argv[0] if argv else ""
    if command == "run":
        if os.geteuid() != 0:
            raise SystemExit("run as root")
        if not adc_present():
            return
        watch()
    elif command in ("install-units", "uninstall-units"):
        if os.geteuid() != 0:
            raise SystemExit("run as root")
        print(install_units(command == "install-units"))
    else:
        raise SystemExit("usage: bootkey run | install-units | uninstall-units")


if __name__ == "__main__":
    main(sys.argv[1:])
