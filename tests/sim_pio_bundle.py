# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""A per-family PlatformIO bundle and the release manifest (hub/pio_bundle.py), and the family
cache `ci._pio_deps` finds: a source's platforms, libraries and requirements read from
platformio.ini/requirements; a packed home and workspace; a verified, and a refused, zip.
python3 tests/sim_pio_bundle.py"""

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

T = Path(tempfile.mkdtemp(prefix="piobundle-"))
os.environ.update(HUB_FIRMWARE_ROOT=str(T / "firmware"))
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from irate_box.hub import ci, pio_bundle

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


src = T / "source"
src.mkdir()
(src / "platformio.ini").write_text(
    """[platformio]
default_envs = heltec-v3, rak4631

[env:heltec-v3]
platform = espressif32
board = heltec_wifi_lora_32_v3
framework = arduino
lib_deps =
    knolleary/PubSubClient@^2.8
    https://github.com/meshtastic/RadioLib.git
build_flags = -DHELTEC

[env:rak4631]
platform = nordicnrf52
board = rak4631
framework = arduino
"""
)
(src / "requirements_base.txt").write_text(
    "nanopb==0.4.9.1\nadafruit-nrfutil==0.5.3.post16\n-r requirements_esp32.txt\n"
)
(src / "requirements_esp32.txt").write_text("pioarduino==6.2.0\n")
(src / "requirements_extra.txt").write_text("platformio-check==1.0\n")
doc = pio_bundle.manifest(src)
check(
    "the manifest reads the default environments and the first one's platform, board and libraries",
    doc["default_envs"] == ["heltec-v3", "rak4631"]
    and doc["envs"]["heltec-v3"]["platform"] == "espressif32"
    and doc["envs"]["heltec-v3"]["board"] == "heltec_wifi_lora_32_v3"
    and doc["envs"]["heltec-v3"]["lib_deps"]
    == ["knolleary/PubSubClient@^2.8", "https://github.com/meshtastic/RadioLib.git"],
    doc,
)
check(
    "  and the Python requirements, the -r line left out",
    doc["requirements"]["requirements_base.txt"] == ["nanopb==0.4.9.1", "adafruit-nrfutil==0.5.3.post16"]
    and doc["requirements"]["requirements_esp32.txt"] == ["pioarduino==6.2.0"]
    and doc["requirements"]["requirements_extra.txt"] == ["platformio-check==1.0"],
    doc["requirements"],
)

home = T / "home" / ".platformio"
(home / "packages/framework-arduinoespressif32").mkdir(parents=True)
(home / "packages/framework-arduinoespressif32/package.json").write_text("{}")
(home / ".cache/downloads").mkdir(parents=True)
(home / ".cache/downloads/eb76").write_text("archive")
workspace = T / "workspace"
(workspace / "libdeps/heltec-v3/RadioLib").mkdir(parents=True)
(workspace / "libdeps/heltec-v3/RadioLib/library.json").write_text("{}")
out = T / "bundle.zip"
said = pio_bundle.bundle("heltec-v3", src, out, home=home, workspace=workspace)
with zipfile.ZipFile(out) as zf:
    names = set(zf.namelist())
check(
    "the bundle holds the packages, the download cache, the target's libraries and the manifest",
    said["env"] == "heltec-v3"
    and said["files"] == 3
    and "pio-deps-heltec-v3/packages/framework-arduinoespressif32/package.json" in names
    and "pio-deps-heltec-v3/core/.cache/downloads/eb76" in names
    and "pio-deps-heltec-v3/libdeps/heltec-v3/RadioLib/library.json" in names
    and "pio-deps-heltec-v3/build-manifest.json" in names,
    sorted(names),
)
check(
    "  verify returns its manifest and environment",
    (verified := pio_bundle.verify(out))["ok"] and verified["env"] == "heltec-v3" and verified["manifest"]["envs"],
    verified,
)
bad = T / "bad.zip"
with zipfile.ZipFile(bad, "w") as zf:
    zf.writestr("../etc/passwd", "x")
    zf.writestr("pio-deps-x/build-manifest.json", "{}")
refused = pio_bundle.verify(bad)
check("  a member that leaves the prefix is refused", not refused["ok"] and "unsafe" in refused["why"], refused)
missing = T / "missing.zip"
with zipfile.ZipFile(missing, "w") as zf:
    zf.writestr("pio-deps-x/packages/a", "x")
check("  a zip without a manifest is refused", not pio_bundle.verify(missing)["ok"], pio_bundle.verify(missing))

root = T / "firmware"
release = root / "2.8.1.8e6a88d"
(release / "pio-deps/native-tft").mkdir(parents=True)
(release / "pio-deps/esp32s3").mkdir(parents=True)
(release / "pio-deps/heltec-v3").mkdir(parents=True)
(root / "status.json").write_text(json.dumps({"cache": {"version": "2.8.1.8e6a88d"}}))
check(
    "ci finds a family cache when there is one, and the release's cache otherwise",
    ci._pio_deps("esp32s3") == release / "pio-deps/esp32s3"
    and ci._pio_deps("esp32s3", "heltec-v3") == release / "pio-deps/heltec-v3"
    and ci._pio_deps("nrf52840") == release / "pio-deps"
    and ci._pio_deps_tag(release / "pio-deps/esp32s3") == "2.8.1.8e6a88d",
    (ci._pio_deps("esp32s3"), ci._pio_deps("nrf52840")),
)

print(f"{fails} failure(s)" if fails else "ok")
sys.exit(1 if fails else 0)
