# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
"""What each radio can do, in one record: the driver's own answer (read live), what the chip table knows
(hub/known_radios.json, read live), what a test found, and what the box saw happen.

The record is written here, by root, to STATE/radio-caps.json. `apmode.plan`, `ap.follow`, the uplink
watchdog and the radio tests read capabilities through this module, never from `ap-tried.json`,
`radio-tests.json` or the inventory's driver fields directly.

Two parts key the record:
  - the radio: a USB radio's USB id and port path (two identical dongles get two records), a built-in
    radio's MAC address;
  - the driver: its module and `version` where it has one, else its `srcversion` and the kernel release,
    so a kernel update starts a fresh record. The old one is kept, and `get()` says what it found.

One row per capability: {id, label, value, source, detail, at, test, testable, below}. Only `test` and
`learned` rows are stored; `driver` and `known` rows are read live at each look. Precedence is
test > learned > known > driver, and a lower source that disagrees stays in `below`.

`migrate()` reads the two old stores once, where it is called (the install's hotspot refresh in
root/ap.py, and the first read on a box without this store): `radio-tests.json` by model and driver
version, into every radio that matches; `ap-tried.json` (a phy number and a boolean, no driver) only
where the box has one radio that can run an access point. Both files are left in place, unread, for one
release.

Root only; stdlib only.
"""

import json
import os
import time
from pathlib import Path

STATE = Path(os.environ.get("HUB_STATE_DIR", "/var/lib/hub"))
STORE = STATE / "radio-caps.json"
OLD_TESTS = STATE / "radio-tests.json"
OLD_TRIED = Path(os.environ.get("HUB_ETC_DIR", "/etc/hub")) / "ap-tried.json"
SYS = Path(os.environ.get("HUB_SYSFS", "/sys"))
SYS_NET = Path("/sys/class/net")

# The rows, in the order the Hardware tab shows them. `test` is the experiment that can settle a row the
# driver cannot answer; `can` (given by the inventory) says whether it can run on this box now.
ROWS = (
    ("bands", "Bands", None),
    ("ap", "AP mode", None),
    ("ap-beside-link", "AP beside link", None),
    ("channels-at-once", "Channels at once", None),
    ("own-channel", "AP on own channel", "own-channel"),
    ("follows-by-itself", "AP follows link", "follows-roam"),
    ("csa", "Channel switch (CSA)", "csa"),
    ("sae", "WPA3 (SAE)", None),
    ("sched-scan", "Scheduled scan", None),
    ("country", "Regulatory domain", None),
    ("reset-recovery", "Radio reset", "radio-reset"),
    ("cmd-timeout-recovery", "Driver timeout recovery", "driver-reset"),
)
ROW = {rid: {"label": label, "test": test} for rid, label, test in ROWS}
ORDER = [rid for rid, _label, _test in ROWS]

# An experiment's answer as the row's value.
ANSWER_ROW = {
    "follows-roam": ("follows-by-itself", {"follows": "yes", "stays": "no", "doesn't": "no", "unknown": None}),
    "own-channel": ("own-channel", {"works": "yes", "doesn't": "no", True: "yes", False: "no"}),
    "radio-reset": ("reset-recovery", {"works": "yes", "doesn't": "no"}),
    "driver-reset": ("cmd-timeout-recovery", {"works": "yes", "doesn't": "no"}),
    "csa": ("csa", {"works": "yes", "doesn't": "no"}),
}


def _read(path, default=None):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return default


def _module_version(driver):
    stem = (driver or "").replace("-", "_")
    version = _read(SYS / "module" / stem / "version") if stem else None
    srcversion = _read(SYS / "module" / stem / "srcversion") if stem else None
    return version or None, srcversion or None


def kernel():
    return os.uname().release


def radio_identity(radio):
    """The radio itself: a USB radio's USB id and port path, a built-in one's MAC address."""
    usb = radio.get("usb") or {}
    if usb.get("id"):
        return f"usb:{usb['id']}@{usb.get('port') or ''}".rstrip("@")
    addr = radio.get("addr") or _read(SYS_NET / str(radio.get("iface") or "") / "address")
    if addr:
        return f"mac:{str(addr).lower()}"
    return f"iface:{radio.get('iface') or radio.get('phy') or '?'}"


def driver_identity(radio):
    """The driver and the thing that changes when it does: its version, or its srcversion and the kernel."""
    driver = radio.get("driver") or "?"
    version, srcversion = _module_version(driver)
    if version:
        return f"{driver} {version}"
    if srcversion:
        return f"{driver} {srcversion}+{kernel()}"
    return f"{driver} kernel-{kernel()}"


def key(radio):
    return f"{radio_identity(radio)}|{driver_identity(radio)}"


def _load():
    try:
        data = json.loads(STORE.read_text())
        return data if isinstance(data, dict) and isinstance(data.get("radios"), dict) else {"version": 1, "radios": {}}
    except (OSError, ValueError):
        return {"version": 1, "radios": {}}


def _save(data):
    from irate_box.root import safeio

    STATE.mkdir(parents=True, exist_ok=True)
    safeio.write(STORE, json.dumps(data, indent=1, sort_keys=True), 0o644)


def stored(radio):
    """The stored rows for this radio and driver: {id: {value, source, detail, at}}."""
    entry = _load()["radios"].get(key(radio)) or {}
    rows = entry.get("rows")
    return rows if isinstance(rows, dict) else {}


def previous(radio):
    """The newest record for this radio on another driver, so the page can say what was tested there."""
    who = radio_identity(radio)
    cur = key(radio)
    best = None
    for name, entry in _load()["radios"].items():
        if name == cur or not isinstance(entry, dict) or entry.get("radio") != who:
            continue
        if best is None or (entry.get("at") or 0) > (best.get("at") or 0):
            best = entry
    return best


def _put(radio, rid, value, source, detail, at=None, msg=None):
    known = _known_rows(radio)
    if rid in known and known[rid].get("value") is not None:
        return
    data = _load()
    name = key(radio)
    entry = data["radios"].setdefault(
        name,
        {
            "radio": radio_identity(radio),
            "driver": driver_identity(radio),
            "at": time.time(),
            "rows": {},
        },
    )
    entry["driver"] = driver_identity(radio)
    entry["at"] = time.time()
    rank = {"driver": 1, "known": 2, "learned": 3, "test": 4}
    row = entry.setdefault("rows", {}).get(rid) or {}
    was, stamp = row.get("source"), at if at is not None else time.time()
    # A test and what the box saw are both facts from a moment: the newer one is the answer. A stored row
    # of either kind stays above a driver's answer.
    newer = stamp >= (row.get("at") or 0)
    if was is None:
        keep = True
    elif was in ("test", "learned") and source in ("test", "learned"):
        keep = newer
    else:
        keep = rank.get(source, 0) >= rank.get(was, 0)
    if keep:
        entry["rows"][rid] = {
            "value": value,
            "source": source,
            "detail": detail or "",
            "at": stamp,
            "msg": msg,
        }
    _save(data)


def record_test(radio, exp, answer, detail, at=None, msg=None):
    """A test's answer as its row. `answer` is the experiment's own word (follows, works, ...); `msg` keeps the
    answer as a message, so the page can say it in the reader's language."""
    rid, values = ANSWER_ROW.get(exp, (exp, {}))
    value = values.get(answer)
    if value is None and answer not in (None, "unknown"):
        value = answer
    _put(radio, rid, value, "test", detail, at, msg)


def record_learned(radio, rid, value, detail, at=None, msg=None):
    """What the box saw happen, on a row no test settles."""
    _put(radio, rid, value, "learned", detail, at, msg)


def _driver_rows(radio):
    bands = radio.get("bands") or []
    modes = radio.get("modes") or []
    reg = radio.get("reg") or {}
    rows = {
        "bands": {
            "value": list(bands),
            "detail": ", ".join(bands) if bands else "",
        },
        "ap": {
            "value": "yes" if radio.get("ap") else "no",
            "detail": "" if radio.get("ap") else "The driver's modes do not include AP.",
        },
        "ap-beside-link": {
            "value": "yes" if radio.get("ap_beside_client") else "no",
            "detail": "The driver allows an access point beside a client link."
            if radio.get("ap_beside_client")
            else "The driver allows an access point or a client link, not both.",
        },
        "channels-at-once": {
            "value": radio.get("channels_at_once", 1),
            "detail": f"The driver allows up to {radio.get('channels_at_once', 1)} at once.",
        },
        "csa": {
            "value": "yes" if "channel_switch" in (radio.get("commands") or []) else "no",
            "detail": "The driver declares channel switch."
            if "channel_switch" in (radio.get("commands") or [])
            else "The driver does not declare channel switch.",
        },
        "sae": {
            "value": "yes" if radio.get("sae") else "no",
            "detail": "" if radio.get("sae") else "The driver does not announce SAE.",
        },
        "sched-scan": {
            "value": "yes" if radio.get("sched_scan") else "no",
            "detail": "",
        },
        "country": {
            "value": "self-managed" if reg.get("self_managed") else "follows the box",
            "detail": f"Country {reg['country']}." if reg.get("country") else "",
        },
    }
    if not modes:
        rows["ap"]["detail"] = ""
    return rows


def _known_rows(radio):
    known = radio.get("known") or {}
    if not known:
        return {}
    if known.get("hotspot_channel") == "link":
        return {
            "own-channel": {"value": "no", "detail": "The firmware stalls on a second channel."},
            "follows-by-itself": {"value": "yes", "detail": "The hotspot follows the link's channel."},
            "channels-at-once": {"value": 1, "detail": "The firmware stalls on a second channel."},
        }
    if known.get("hotspot") is False:
        return {"ap": {"value": "no", "detail": "The chip table says this chip has no hotspot mode."}}
    return {}


def rows(radio, stored_rows=None, can=()):
    """The merged rows for one radio, in display order. `stored_rows`: what this module stored; `can`:
    the experiments that can run here now."""
    stored_rows = stored_rows if isinstance(stored_rows, dict) else {}
    driver, known = _driver_rows(radio), _known_rows(radio)
    out = {}
    for rid in ORDER:
        label, test = ROW[rid]["label"], ROW[rid]["test"]
        candidates = []
        for source, source_rows in (("driver", driver), ("known", known), ("stored", stored_rows)):
            row = source_rows.get(rid)
            if row is None or row.get("value") is None:
                continue
            candidates.append(
                {
                    "source": row.get("source") or ("test" if source == "stored" else source),
                    "value": row["value"],
                    "detail": row.get("detail") or "",
                    "at": row.get("at"),
                    "msg": row.get("msg"),
                }
            )
        # The chip list's answer is the record's answer wherever it has one: a test or what the box saw is
        # kept below it, so a firmware's known limit cannot be overwritten by one try that happened to hold.
        known_row = known.get(rid)
        if known_row is not None and known_row.get("value") is not None:
            top = {
                "source": "known",
                "value": known_row["value"],
                "detail": known_row.get("detail") or "",
                "at": None,
                "msg": known_row.get("msg"),
            }
            below = [r for r in candidates if r["value"] != top["value"]]
        else:
            rank = {"driver": 1, "known": 2, "learned": 3, "test": 4}
            candidates.sort(key=lambda r: rank.get(r["source"], 0))
            top = (
                candidates[-1] if candidates else {"source": None, "value": None, "detail": "", "at": None, "msg": None}
            )
            below = [r for r in candidates[:-1] if r["value"] != top["value"]]
        out[rid] = {
            "id": rid,
            "label": label,
            "value": top["value"],
            "source": top["source"],
            "detail": top["detail"],
            "at": top["at"],
            "msg": top.get("msg"),
            "test": test,
            "testable": bool(test and test in set(can or ())),
            "below": below,
        }
    return out


def get(radio, can=()):
    """One radio's whole record for the page: its key, the radio and driver it names, its rows, and the
    newest record from another driver for the same radio."""
    version, srcversion = _module_version(radio.get("driver"))
    return {
        "key": key(radio),
        "radio": radio_identity(radio),
        "driver": {
            "name": radio.get("driver"),
            "version": version,
            "srcversion": srcversion,
            "kernel": kernel(),
        },
        "at": _load()["radios"].get(key(radio), {}).get("at"),
        "rows": rows(radio, stored(radio), can),
        "previous": previous(radio),
    }


def by_phy(inv, can_by_phy=None):
    """The record for each phy in an inventory, for the Hardware tab: {phy: get(radio)}."""
    out = {}
    for radio in inv.get("radios") or []:
        phy = radio.get("phy")
        if not phy or phy in out or radio.get("type") not in ("managed", "AP"):
            continue
        if any(r.get("phy") == phy and r.get("type") == "managed" for r in inv.get("radios") or []):
            if radio.get("type") != "managed":
                continue
        out[phy] = get(radio, (can_by_phy or {}).get(phy) or ())
    return out


def stored_by_phy(inv):
    """{phy: stored rows}, what `apmode.plan` reads. A phy with two interfaces takes its client's record."""
    out = {}
    for radio in inv.get("radios") or []:
        phy = radio.get("phy")
        if not phy or phy in out or radio.get("type") not in ("managed", "AP"):
            continue
        if any(r.get("phy") == phy and r.get("type") == "managed" for r in inv.get("radios") or []):
            if radio.get("type") != "managed":
                continue
        out[phy] = stored(radio)
    return out


def _old_key(radio):
    """The key radio-tests.json used: USB id (or driver), driver, and its module version."""
    driver = radio.get("driver") or "?"
    version, _srcversion = _module_version(driver)
    who = (radio.get("usb") or {}).get("id") or driver
    return f"{who} {driver} {version or ''}".strip()


def _ap_capable(radio):
    return radio.get("type") in ("managed", "AP") and radio.get("ap")


def migrate(inv=None, force=False):
    """Fill the record from the two old stores, once. `inv`: the inventory the old keys are matched
    against. `ap-tried.json`'s phy number is stale after a driver reload; where the box has one radio that can
    be an access point, its one entry is that radio's, whatever the number (more than one entry is dropped).
    Returns whether anything was written."""
    if STORE.exists() and not force:
        return False
    if inv is None:
        return False
    data = _load()
    changed = False
    try:
        tests = json.loads(OLD_TESTS.read_text())
    except (OSError, ValueError):
        tests = {}
    if isinstance(tests, dict):
        for radio in inv.get("radios") or []:
            if radio.get("type") not in ("managed", "AP") or not radio.get("driver"):
                continue
            found = tests.get(_old_key(radio))
            if not isinstance(found, dict):
                continue
            name = key(radio)
            entry = data["radios"].setdefault(
                name,
                {"radio": radio_identity(radio), "driver": driver_identity(radio), "at": time.time(), "rows": {}},
            )
            entry["radio"], entry["driver"] = radio_identity(radio), driver_identity(radio)
            known = _known_rows(radio)
            for exp, answer in found.items():
                rid, values = ANSWER_ROW.get(exp, (None, {}))
                if not rid or not isinstance(answer, dict) or known.get(rid, {}).get("value") is not None:
                    continue
                value = values.get(answer.get("answer"))
                if value is None and answer.get("answer") not in (None, "unknown"):
                    value = answer.get("answer")
                entry["rows"].setdefault(
                    rid,
                    {
                        "value": value,
                        "source": "test",
                        "detail": answer.get("detail") or "",
                        "at": answer.get("at") or time.time(),
                        "msg": answer.get("msg"),
                    },
                )
                changed = True
    try:
        tried = json.loads(OLD_TRIED.read_text())
    except (OSError, ValueError):
        tried = {}
    capable = {}
    for radio in inv.get("radios") or []:
        if not _ap_capable(radio):
            continue
        phy = radio.get("phy") or radio.get("iface")
        if phy not in capable or radio.get("type") == "managed":
            capable[phy] = radio
    capable = list(capable.values())
    if isinstance(tried, dict) and len(capable) == 1 and len(tried) == 1:
        radio = capable[0]
        # The old file keys its one answer by a phy number that a driver reload renumbers (lyra-tft: phy0,
        # its radio on phy6). With one radio that can be an access point, its one answer is that radio's.
        value = next(iter(tried.values()))
        if isinstance(value, bool) and _known_rows(radio).get("own-channel", {}).get("value") is None:
            name = key(radio)
            entry = data["radios"].setdefault(
                name,
                {"radio": radio_identity(radio), "driver": driver_identity(radio), "at": time.time(), "rows": {}},
            )
            entry["rows"].setdefault(
                "own-channel",
                {
                    "value": "yes" if value else "no",
                    "source": "test",
                    "detail": "Tested before the capability record existed.",
                    "at": time.time(),
                },
            )
            changed = True
    if changed or not STORE.exists():
        _save(data)
    return changed
