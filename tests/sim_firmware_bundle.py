# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A release's per-family `platformio-deps-<family>` asset, kept beside the native-tft cache
(firmware._extract_extra): the packages and libdeps/<family> land under pio-deps/<family>, where
ci._pio_deps finds them. The download is stood in. python3 tests/sim_firmware_bundle.py"""

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
asset = {"url": "https://example.invalid/deps.zip", "size": src.stat().st_size, "name": "deps.zip", "digest": ""}
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

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
