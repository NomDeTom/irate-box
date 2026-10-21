# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The WiFi chips irate-box knows (known_radios.json): how to recognise each, what it can do, where its driver
comes from when the kernel has none, and test results, ours and others'.

  lookup(usb_id, driver)  the row for a radio: by its USB ID first, else by its driver alone (a row with no IDs)
  storage_mode()          {usb_id: row} for dongles that first show as a USB drive (the AIC8800D80's a69c:5721)
  stuck()                 such dongles plugged in now, still in storage mode: they have no WiFi interface yet

netinv attaches the row to each radio it finds, and lists the stuck dongles. Stdlib only.
"""

import functools
import json
import os
from pathlib import Path

TABLE = Path(__file__).with_name("known_radios.json")
USB_DEVICES = Path(os.environ.get("HUB_SYSFS", "/sys")) / "bus/usb/devices"


@functools.lru_cache(maxsize=1)
def radios():
    try:
        return json.loads(TABLE.read_text())["radios"]
    except (OSError, ValueError, KeyError):
        return []


def lookup(usb_id=None, driver=None):
    usb_id = (usb_id or "").lower()
    if usb_id:
        for row in radios():
            if usb_id in row.get("usb_ids", []):
                return row
    if driver:
        for row in radios():
            if not row.get("usb_ids") and driver in (row.get("drivers") or [row.get("driver")]):
                return row
    return None


def storage_mode():
    return {usb_id: row for row in radios() for usb_id in row.get("storage_ids", [])}


def _read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def stuck():
    """The known WiFi dongles plugged in and still in USB storage mode: [{usb_id, chip, port}]."""
    known, out = storage_mode(), []
    try:
        devices = sorted(USB_DEVICES.iterdir())
    except OSError:
        return out
    for device in devices:
        usb_id = f"{_read(device / 'idVendor')}:{_read(device / 'idProduct')}".lower()
        if usb_id in known:
            out.append({"usb_id": usb_id, "chip": known[usb_id]["chip"], "port": device.name})
    return out
