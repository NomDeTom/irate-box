# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The WiFi chips irate-box knows (hub/known_radios.json, hub/knownradios.py): the table well formed (each row
named, with a driver; IDs as vendor:product, lower case, none twice, no storage-mode ID that is also a WiFi ID;
a driver source's address https), a radio found by its USB ID or else its driver, a dongle still in storage mode
found among the USB devices, and netinv's facts for a radio. A stand-in /sys. python3 tests/sim_knownradios.py"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="knownradios-"))
os.environ["HUB_SYSFS"] = str(T / "sys")
sys.path.insert(0, str(REPO))
from irate_box.hub import knownradios, netinv

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


rows = json.loads((REPO / "irate_box/hub/known_radios.json").read_text())["radios"]
ID = re.compile(r"^[0-9a-f]{4}:[0-9a-f]{4}$")
wifi_ids = [usb_id for row in rows for usb_id in row.get("usb_ids", [])]
storage_ids = [usb_id for row in rows for usb_id in row.get("storage_ids", [])]
check("every row has a chip and a driver", all(row.get("chip") and row.get("driver") for row in rows))
check(
    "IDs are vendor:product, lower case",
    all(ID.match(usb_id) for usb_id in wifi_ids + storage_ids),
    wifi_ids + storage_ids,
)
check("no ID twice", len(set(wifi_ids)) == len(wifi_ids) and len(set(storage_ids)) == len(storage_ids))
check("no storage-mode ID is also a WiFi ID", not set(wifi_ids) & set(storage_ids))
check(
    "a driver source's address is https",
    all(not (row.get("source") or {}).get("url") or row["source"]["url"].startswith("https://") for row in rows),
)
check(
    "every test result says who, on what, when and what",
    all(all(result.get(k) for k in ("by", "on", "date", "result")) for row in rows for result in row.get("tested", [])),
)

check(
    "by USB ID: the AIC8800D80 (any case)", (knownradios.lookup("368B:8D81", "usb") or {}).get("chip") == "AIC8800D80"
)
check("by USB ID, a rebadged one: VIA's AR9271", (knownradios.lookup("040d:3801") or {}).get("chip") == "AR9271")
check(
    "by driver alone: the onboard Broadcom", "Broadcom" in (knownradios.lookup(None, "brcmfmac") or {}).get("chip", "")
)
check("by a family's drivers: mt76x0u", "MT7610U" in (knownradios.lookup(None, "mt76x0u") or {}).get("chip", ""))
check(
    "a driver with rows that have IDs is not matched by driver alone (an unknown aic8800 dongle)",
    knownradios.lookup("ffff:0001", "aic8800_fdrv") is None,
)
check("unknown: none", knownradios.lookup("ffff:ffff", "nonsense") is None)


def usb_device(port, vendor, product):
    folder = T / "sys/bus/usb/devices" / port
    folder.mkdir(parents=True)
    (folder / "idVendor").write_text(vendor + "\n")
    (folder / "idProduct").write_text(product + "\n")


usb_device("1-1.1.2.3", "a69c", "5721")
usb_device("1-1.1.2.4", "0bda", "8179")
usb_device("1-1.1", "0424", "2514")
check(
    "a dongle still in storage mode: found, named, by its port",
    knownradios.stuck() == [{"usb_id": "a69c:5721", "chip": "AIC8800D80", "port": "1-1.1.2.3"}],
    knownradios.stuck(),
)

facts = netinv.known_facts({"driver": "ath9k_htc", "usb": {"id": "040d:3801"}})
check(
    "netinv's facts for a radio: the chip, the kernel's driver, a hotspot, who tested it",
    facts["chip"] == "AR9271"
    and facts["in_kernel"] is True
    and facts["hotspot"] is True
    and "irate-box" in facts["tested_by"],
    facts,
)
check(
    "  none for a chip the table doesn't have",
    netinv.known_facts({"driver": "nonsense", "usb": {"id": "ffff:ffff"}}) is None,
)

sys.exit(1 if fails else 0)
