# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A release's per-family `platformio-deps-<family>` asset, kept beside the native-tft cache
(firmware._extract_extra): the packages and libdeps/<family> land under pio-deps/<family>, where
ci._pio_deps finds them. The download is stood in. python3 tests/sim_firmware_bundle.py"""

import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

T = Path(tempfile.mkdtemp(prefix="fwbundle-"))
for d in ("firmware", "state", "tmp"):
    (T / d).mkdir(parents=True)
import os

os.environ.update(HUB_FIRMWARE_ROOT=str(T / "firmware"), HUB_STATE_DIR=str(T / "state"))
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.library import firmware, librarian

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


src = T / "asset.zip"
with zipfile.ZipFile(src, "w") as zf:
    zf.writestr("pio-deps-esp32s3/packages/framework-arduinoespressif32/package.json", "{}")
    zf.writestr("pio-deps-esp32s3/core/.cache/downloads/eb76", "archive")
    zf.writestr("pio-deps-esp32s3/libdeps/esp32s3/RadioLib/library.json", "{}")
asset = {
    "url": "https://example.invalid/deps.zip",
    "size": src.stat().st_size,
    "name": "deps.zip",
    "digest": "",
    "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
}
dest = firmware.ROOT / "2.8.1.8e6a88d" / "pio-deps" / "esp32s3"
with (
    mock.patch.object(librarian, "_download", lambda url, tmp, **kw: shutil.copyfile(src, tmp)),
    mock.patch.object(librarian, "_free_bytes", lambda path: 1 << 40),
):
    said = firmware._extract_extra(asset, dest, lambda *a: None)
check(
    "a per-family asset lands under pio-deps/<family> with its packages and libdeps",
    (dest / "packages/framework-arduinoespressif32/package.json").is_file()
    and (dest / "libdeps/esp32s3/RadioLib/library.json").is_file()
    and (dest / "core/.cache/downloads/eb76").is_file()
    and said["path"] == str(dest),
    said,
)
check(
    "  and is marked as a whole cache",
    (dest / ".irate-box-cache").read_text().strip() == "whole",
)

# B5: a bundle with no SHA-256 from either the release's own record or GitHub is not unpacked and run.
unverified = {**asset, "name": "unverified.zip", "sha256": "", "digest": ""}
with (
    mock.patch.object(librarian, "_download", lambda url, tmp, **kw: shutil.copyfile(src, tmp)),
    mock.patch.object(librarian, "_free_bytes", lambda path: 1 << 40),
):
    try:
        firmware._extract_extra(unverified, firmware.ROOT / "2.8.1.8e6a88d" / "pio-deps" / "nrf52840", lambda *a: None)
        refused = ""
    except librarian.LibrarianError as exc:
        refused = exc.message["id"]
check("  a bundle with no SHA-256 is refused, naming it", refused == "firmware.cache.unverified", refused)

sums = T / "pio-deps-sha256.json"
sums.write_text(json.dumps({"files": {"platformio-deps-esp32s3-2.8.1.zip": "a" * 64}}))
names = {
    "pio-deps-sha256.json": {"browser_download_url": "https://example.invalid/sums.json", "size": sums.stat().st_size}
}
with mock.patch.object(librarian, "_download", lambda url, tmp, **kw: shutil.copyfile(sums, tmp)):
    got = firmware._bundle_sums(names)
check(
    "  the release's pio-deps-sha256.json is read, and only 64-hex digests are kept",
    got == {"platformio-deps-esp32s3-2.8.1.zip": "a" * 64},
    got,
)

release = {
    "tag_name": "v2.8.1.8e6a88d",
    "published_at": "2026-01-01T00:00:00Z",
    "assets": [
        {"name": "firmware-2.8.1.8e6a88d.json", "browser_download_url": "u", "size": 1, "digest": ""},
        {"name": "platformio-deps-..-2.8.1.8e6a88d.zip", "browser_download_url": "u1", "size": 1, "digest": ""},
        {"name": "platformio-deps-esp32s3-2.8.1.8e6a88d.zip", "browser_download_url": "u2", "size": 1, "digest": ""},
    ],
}
with mock.patch.object(librarian, "_api", lambda *a, **k: [release]):
    releases = firmware._releases({"keep_alpha": 2, "keep_beta": 1})
check(
    "a bundle name that would be a path (..) is left out of the release",
    releases and ".." not in releases[0]["deps_all"] and "esp32s3" in releases[0]["deps_all"],
    releases[0]["deps_all"] if releases else releases,
)

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
