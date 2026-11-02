# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Proving runs (hub/proving.py): a tag's manifest read from a bare mirror; what it needs against the
family's cache and the wheelhouse; the settings; when a run may be queued; a proving job behind the
owner's builds; an offline proof with PlatformIO stood in; and the doctor's findings.
python3 tests/sim_proving.py"""

import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="proving-"))
os.environ.update(
    HUB_STATE_DIR=str(T / "state"),
    HUB_GIT_ROOT=str(T / "git"),
    HUB_CI_ROOT=str(T / "ci"),
    HUB_GIT_PRIVATE=str(T / "git" / "private"),
    HUB_MIRROR_URLS=str(T / "git" / "mirror-urls.json"),
    HUB_FIRMWARE_ROOT=str(T / "firmware"),
    HUB_KITS_ROOT=str(T / "kits"),
    HUB_MEMGUARD_STATUS=str(T / "memguard.json"),
    HOME=str(T / "home"),
)
(T / "home").mkdir()
for d in ("state/library", "git/public", "git/private", "ci/queue", "ci/runs", "ci/work", "kits/wheelhouse"):
    (T / d).mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import ci, factory, pio_bundle, proving
from irate_box.library import firmware
from irate_box.library.librarian import LibrarianError

fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
FILES = {
    "platformio.ini": "[platformio]\ndefault_envs = heltec-v3\nextra_configs =\n\tvariants/*/*.ini\n\tvariants/*/*/platformio.ini\n",
    "variants/esp32s3/esp32s3.ini": "[esp32s3_base]\nplatform = espressif32@6.10\n",
    "variants/esp32s3/heltec_v3/platformio.ini": "[env:heltec-v3]\nextends = esp32s3_base\n"
    "board = heltec_wifi_lora_32_v3\nframework = arduino\nlib_deps =\n    knolleary/PubSubClient@^2.8\n"
    "custom_meshtastic_display_name = Heltec V3\n",
    "variants/nrf52840/nrf52840.ini": "[nrf52840_base]\nplatform = nordicnrf52\n",
    "variants/nrf52840/rak4631/platformio.ini": "[env:rak4631]\nextends = nrf52840_base\n",
    "variants/native/portduino/platformio.ini": "[env:native]\nplatform = native\n",
    "requirements_esp32.txt": "nanopb==0.4.9.1\ngrpcio-tools==1.60.0\n",
}
w = T / "src"
subprocess.run(["git", "init", "-q", "-b", "main", str(w)], check=True)
for name, text in FILES.items():
    (w / name).parent.mkdir(parents=True, exist_ok=True)
    (w / name).write_text(text)
subprocess.run(["git", "add", "-A"], cwd=w, check=True)
subprocess.run(["git", "commit", "-qm", "c"], cwd=w, env=ENV, check=True)
subprocess.run(["git", "tag", "v2.8.2.abcdef0"], cwd=w, check=True)
bare = T / "git" / "public" / "meshtastic-firmware.git"
subprocess.run(["git", "clone", "-q", "--bare", str(w), str(bare)], check=True)
(T / "state" / "library" / "mirrors.json").write_text(
    json.dumps(
        {
            "mirrors": [
                {"name": "meshtastic-firmware", "area": "public", "upstream": "https://github.com/meshtastic/firmware"}
            ]
        }
    )
)
(T / "git" / "mirror-urls.json").write_text(json.dumps({"https://github.com/meshtastic/firmware": f"file://{bare}"}))

# The manifest from a tag, read with git, without a checkout.
doc = pio_bundle.manifest_at(bare, "v2.8.2.abcdef0")
check(
    "manifest_at: the tag's environments, platform and requirements, from a bare mirror",
    doc["envs"]["heltec-v3"]["platform"] == "espressif32@6.10"
    and doc["envs"]["heltec-v3"]["lib_deps"] == ["knolleary/PubSubClient@^2.8"]
    and doc["requirements"]["requirements_esp32.txt"] == ["nanopb==0.4.9.1", "grpcio-tools==1.60.0"]
    and doc["tag"] == "v2.8.2.abcdef0",
    doc,
)

# The Building kit's wheelhouse: PlatformIO and nanopb are there; grpcio-tools is not.
for wheel in ("platformio-6.1.19-py3-none-any.whl", "nanopb-0.4.9.1-py3-none-any.whl"):
    (T / "kits" / "wheelhouse" / wheel).write_bytes(b"x")
packages = proving.wheelhouse_packages()
check(
    "the wheelhouse's packages and versions",
    packages == {"platformio": "6.1.19", "nanopb": "0.4.9.1"},
    packages,
)

# A family cache for the tag, with the older platform and its libraries.
cache = T / "firmware" / "2.8.2.abcdef0" / "pio-deps" / "esp32s3"
(cache / "packages" / "platform-espressif32@6.9.0").mkdir(parents=True)
(cache / "packages" / "toolchain-xtensa-esp32s3").mkdir(parents=True)
(cache / "libdeps" / "heltec-v3" / "PubSubClient").mkdir(parents=True)
(cache / "core" / ".cache" / "downloads").mkdir(parents=True)
(cache / "core" / ".cache" / "downloads" / "archive").write_text("x")

found = proving.compare(bare, "v2.8.2.abcdef0", "esp32s3", "heltec-v3")
by_id = {f["id"]: f for f in found}
check(
    "compare: the older platform and the missing Python package, before anything is built",
    by_id["proving.platform.version"]["summary"]["args"]
    == {
        "version": "2.8.2.abcdef0",
        "name": "espressif32",
        "wanted": "6.10",
        "have": "6.9.0",
    }
    and by_id["proving.python.missing"]["summary"]["args"]["package"] == "grpcio-tools"
    and "proving.python.missing.nanopb" not in by_id,
    found,
)
check(
    "  a cache's signature names what it holds",
    proving.cache_signature(cache) and len(proving.cache_signature(cache)) == 16,
)
check(
    "  no cache at all: one finding, not a build",
    [f["id"] for f in proving.compare(bare, "v2.8.2.abcdef0", "nrf52840", "rak4631")] == ["proving.cache.missing"],
)

# The settings: off by default, then the owner's choices, validated.
check("proving is off by default", proving.settings()["proving"] is False)
cfg = firmware.set_settings(
    proving=True,
    proving_releases="all",
    proving_families=["esp32s3"],
    proving_when="night",
    proving_tooling="fetch",
    proving_keep=2,
    proving_window_start="23:00",
    proving_window_end="05:30",
)
check(
    "the proving settings are kept with the firmware's",
    cfg["proving"] is True and proving.settings()["proving_families"] == ["esp32s3"],
    proving.settings(),
)
for bad in (
    {"proving_releases": "sometimes"},
    {"proving_families": ["../x"]},
    {"proving_keep": 0},
    {"proving_window_start": "25:00"},
):
    try:
        firmware.set_settings(**bad)
        check(f"refused: {bad}", False)
    except LibrarianError:
        check(f"refused: {bad}", True)

# Families and representative targets.
targets = factory.targets(factory.source("meshtastic-firmware"), "v2.8.2.abcdef0")
check(
    "the families of the chosen boards, or every family",
    proving.families(targets["targets"]) == ["esp32s3"]
    and proving.representative(targets["targets"], "esp32s3") == "heltec-v3",
    (proving.families(targets["targets"]), [t["family"] for t in targets["targets"]]),
)

# When it may run: never on low memory or battery; the night window may cross midnight.
check(
    "no memory guard: nothing by itself, only Prove now",
    proving.can_queue(force=False) == (False, "the memory guard is not running") and proving.can_queue(force=True)[0],
)
(T / "memguard.json").write_text(json.dumps({"level": "act"}))
check("short of memory: no", proving.can_queue(force=True) == (False, "the box is short of memory"))
(T / "memguard.json").write_text(json.dumps({"level": "ok"}))
check("memory fine: yes", proving.can_queue(force=True)[0])
proving.POWER = T / "power"
(proving.POWER / "BAT0").mkdir(parents=True)
(proving.POWER / "BAT0" / "type").write_text("Battery\n")
(proving.POWER / "BAT0" / "status").write_text("Discharging\n")
check("on battery: no", proving.can_queue(force=True) == (False, "the box is on battery"))
(proving.POWER / "BAT0" / "status").write_text("Charging\n")
night = time.mktime((2026, 11, 2, 23, 30, 0, 0, 0, -1))
check(
    "the night window crosses midnight",
    proving.in_window({"proving_window_start": "22:00", "proving_window_end": "06:00"}, night),
)
cfg = proving.settings()
proving.can_queue(cfg, force=False, now=night)

# Queue: a proving job for the family, behind the owner's firmware job.
rel = {
    "tag": "v2.8.2.abcdef0",
    "version": "2.8.2.abcdef0",
    "name": "",
    "prerelease": False,
    "deps_all": {
        "esp32s3": {
            "url": "https://example.invalid/platformio-deps-esp32s3-2.8.2.abcdef0.zip",
            "size": 1,
            "name": "platformio-deps-esp32s3-2.8.2.abcdef0.zip",
            "sha256": "a" * 64,
        }
    },
}
made = proving.queue_release(rel, factory.source("meshtastic-firmware"), targets, force=True)
jobs = [json.loads(p.read_text()) for p in ci.QUEUE.glob("*.json")]
check(
    "queued: one proving job per family, with the target, tag and published bundle",
    made == 1
    and jobs[0]["kind"] == "proving"
    and (jobs[0]["family"], jobs[0]["env"], jobs[0]["tag"]) == ("esp32s3", "heltec-v3", "v2.8.2.abcdef0")
    and jobs[0]["bundle"]["sha256"] == "a" * 64,
    jobs,
)
for p in ci.QUEUE.glob("*.json"):
    p.unlink()

# A proving build, with PlatformIO stood in.
ci.FIRMWARE_SCRIPT = r"""set -eu
echo "offline=${IRATE_NO_NETWORK:-0} cache=${CI_PIO_DEPS:-none}"
test -n "${CI_PIO_DEPS:-}"
mkdir -p "$PLATFORMIO_WORKSPACE_DIR/build/$FW_ENV"
echo x > "$PLATFORMIO_WORKSPACE_DIR/build/$FW_ENV/firmware-$FW_ENV.bin"
echo "Compiling a.o"
"""
ci.OFFLINE = ["env", "IRATE_NO_NETWORK=1"]
ci.WORK.mkdir(parents=True, exist_ok=True)
job = {
    "kind": "proving",
    "repo": str(bare),
    "source": "meshtastic-firmware",
    "ref": "v2.8.2.abcdef0",
    "tag": "v2.8.2.abcdef0",
    "version": "2.8.2.abcdef0",
    "commit": targets["commit"],
    "env": "heltec-v3",
    "family": "esp32s3",
    "queued": time.time(),
}
ci._put(job)
ci.run_queue()
runs = sorted((ci.RUNS / "factory-proving").glob("*/status.json"))
st = json.loads(runs[-1].read_text())
log = (runs[-1].parent / "log.txt").read_text()
proof = proving.report()["proofs"]["esp32s3"]
check(
    "a proving run: offline, from the family's cache, passed, with the cache and tool versions kept",
    st["state"] == "passed"
    and "offline=1" in log
    and proof["state"] == "passed"
    and proof["tag"] == "v2.8.2.abcdef0"
    and proof["cache_sha256"] == proving.cache_signature(cache)
    and proof["env"] == "heltec-v3",
    (st, proof),
)
check(
    "  the doctor says the family is proved offline",
    any(
        f["id"] == "proving-esp32s3"
        and f["status"] == "ok"
        and (f["summary"].get("args") or {}).get("family") == "esp32s3"
        for f in proving.findings()
    ),
    proving.findings(),
)

# A tag with no cache: a tooling finding, not a build, when fetching is only flagged.
firmware.set_settings(proving_tooling="flag")
rel2 = dict(rel, tag="v2.8.3.abcdef1", version="2.8.3.abcdef1", deps_all={})
made = proving.queue_release(rel2, factory.source("meshtastic-firmware"), targets, force=True)
check("a new tag is queued once, not again once proved", made == 1)
for p in ci.QUEUE.glob("*.json"):
    p.unlink()
job2 = dict(job, tag="v2.8.3.abcdef1", version="2.8.3.abcdef1", ref="v2.8.3.abcdef1", bundle=None)
ci._put(job2)
ci.run_queue()
st2 = json.loads(sorted((ci.RUNS / "factory-proving").glob("*/status.json"))[-1].read_text())
check(
    "no cache and flag only: skipped, with the step, not a failed build",
    st2["state"] == "skipped" and st2["step"] == "tooling",
    st2,
)
check(
    "  the doctor reports it as not proved",
    any(f["id"] == "proving-esp32s3" and f["status"] == "warn" for f in proving.findings()),
    proving.findings(),
)

# A published bundle unpacks into the family's cache.
bundle = T / "bundle.zip"
with zipfile.ZipFile(bundle, "w") as zf:
    zf.writestr("pio-deps-heltec-v3/packages/platform-espressif32@6.10.0/x", "x")
    zf.writestr("pio-deps-heltec-v3/libdeps/heltec-v3/PubSubClient/library.json", "{}")
    zf.writestr("pio-deps-heltec-v3/build-manifest.json", json.dumps({"env": "heltec-v3"}))
check(
    "a release name that is not one: no cache path",
    proving.extract_bundle(bundle, "esp32s3", "../etc") is None,
)
extracted = proving.extract_bundle(bundle, "esp32s3", "2.8.4.abcdef2")
check(
    "a verified bundle becomes the family's cache",
    extracted == T / "firmware" / "2.8.4.abcdef2" / "pio-deps" / "esp32s3"
    and (extracted / "packages" / "platform-espressif32@6.10.0" / "x").is_file()
    and (extracted / "libdeps" / "heltec-v3" / "PubSubClient" / "library.json").is_file(),
    extracted,
)
# Download and install: wheels only, and a package with no wheel becomes a finding.
calls = []


def pip_run(cmd, **kw):
    calls.append(cmd)
    return subprocess.CompletedProcess(cmd, 0 if "nanopb" in " ".join(cmd) else 1, "", "")


with mock.patch("subprocess.run", pip_run):
    said = proving.install_requirements(["nanopb==0.4.9.1", "no-wheel==1.0"], "esp32s3", log=lambda *_: None)
tooling = {f["id"]: f for f in proving.report()["tooling"]}
check(
    "only wheels are downloaded, and a package with no wheel is a finding",
    "--only-binary=:all:" in calls[0]
    and said["done"] == ["nanopb==0.4.9.1"]
    and said["failed"] == ["no-wheel==1.0"]
    and tooling["proving.python.no-wheel"]["summary"]["args"]["package"] == "no-wheel",
    (calls, said, tooling),
)
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
