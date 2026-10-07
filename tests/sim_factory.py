# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The Firmware Factory (next-work plan step 36, git-ci-plan §4b), offline: a source's targets read
from platformio.ini and the files its extra_configs globs name (a fixture laid out like
meshtastic/firmware: families from variants/, through extends, display names); sources only the
owner's mirrors and private repositories; the queue (per-request limits, family order, moved up,
pause holding jobs out of the builder's queue, cancel); estimates from what builds used; a build
with PlatformIO stood in (its files kept, not the .elf; what it used recorded; each target's two
newest runs kept); the builder taking pushes first. python3 tests/sim_factory.py"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="factory-"))
os.environ.update(HUB_STATE_DIR=str(T / "state"), HUB_GIT_ROOT=str(T / "git"), HUB_CI_ROOT=str(T / "ci"),
                  HUB_GIT_PRIVATE=str(T / "git" / "private"), HUB_MIRROR_URLS=str(T / "git" / "mirror-urls.json"))
for d in ("state/library", "git/public", "git/private", "ci/queue", "ci/runs", "ci/work"):
    (T / d).mkdir(parents=True)
sys.path.insert(0, str(REPO))
from irate_box.hub import ci, factory  # noqa: E402
fails = 0
def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}")); fails += not cond
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
FILES = {
    "platformio.ini": "[platformio]\ndefault_envs = heltec-v3\nextra_configs =\n\tvariants/*/*.ini\n\tvariants/*/*/platformio.ini\n"
                      "\tsrc/graphics/niche/PlatformioConfig.ini\n\n[env]\nbuild_flags = -Wall ; a comment\n",
    "variants/esp32s3/esp32s3.ini": "[esp32s3_base]\nextends = esp32_common\n",
    "variants/esp32/esp32_common.ini": "[esp32_common]\nplatform = espressif32\n",
    "variants/esp32s3/heltec_v3/platformio.ini": "[env:heltec-v3]\ncustom_meshtastic_display_name = Heltec V3\ncustom_meshtastic_support_level = 1\n"
                                                 "extends = esp32s3_base\nboard_level = pr\n",
    "variants/nrf52840/nrf52840.ini": "[nrf52840_base]\nplatform = nordicnrf52\n",
    "variants/nrf52840/rak4631/platformio.ini": "[env:rak4631]\nextends = nrf52840_base\ncustom_meshtastic_display_name = RAK WisBlock 4631\n",
    "variants/native/portduino.ini": "[portduino_base]\nplatform = native\n",
    "variants/native/portduino/platformio.ini": "[native_base]\nextends = portduino_base\n\n[env:native]\nextends = native_base\nboard_level = extra\n",
    "src/graphics/niche/PlatformioConfig.ini": "[env:rak4631-inkhud]\nextends = env:rak4631\n",
    "variants/esp32s3/heltec_v3/notes.txt": "not a config\n",
}
def make(area, name, files):
    w = T / f"w-{name}"
    subprocess.run(["git", "init", "-q", "-b", "main", str(w)], check=True)
    for f, text in files.items():
        (w / f).parent.mkdir(parents=True, exist_ok=True); (w / f).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=w, check=True); subprocess.run(["git", "commit", "-qm", "c"], cwd=w, env=ENV, check=True)
    subprocess.run(["git", "tag", "v2.8.1.abcdef0"], cwd=w, check=True)
    bare = T / "git" / area / f"{name}.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(w), str(bare)], check=True)
    return bare
fw = make("public", "meshtastic-firmware", FILES)
fork = make("private", "my-fork", FILES)
guest = make("public", "guest-thing", FILES)
(T / "state" / "library" / "mirrors.json").write_text(json.dumps({"mirrors": [{"name": "meshtastic-firmware", "area": "public",
                                                                                "upstream": "https://github.com/meshtastic/firmware"}]}))
(T / "git" / "mirror-urls.json").write_text(json.dumps({"https://github.com/meshtastic/firmware": f"file://{fw}"}))

names = [s["name"] for s in factory.sources()]
check("sources: the owner's mirror and private repositories, not a public one guests can push to", names == ["meshtastic-firmware", "my-fork"], names)
for bad in ("guest-thing", "../etc"):
    try:
        factory.source(bad); check(f"source refused: {bad}", False)
    except ValueError:
        check(f"source refused: {bad}", True)
src = factory.source("meshtastic-firmware")
r = factory.refs(src)
check("refs: its tags and branches, with commits", [t["ref"] for t in r["tags"]] == ["v2.8.1.abcdef0"] and [b["ref"] for b in r["branches"]] == ["main"])
for bad in ("nope", "../x", "-x"):
    try:
        factory.targets(src, bad); check(f"a ref that isn't one: {bad}", False)
    except ValueError:
        check(f"a ref that isn't one: {bad}", True)
data = factory.targets(src, "v2.8.1.abcdef0")
by = {t["env"]: t for t in data["targets"]}
check("targets: every [env:] from the files the globs name", sorted(by) == ["heltec-v3", "native", "rak4631", "rak4631-inkhud"], sorted(by))
check("  the family from variants/, through extends where the section comes from elsewhere",
      (by["heltec-v3"]["family"], by["rak4631"]["family"], by["native"]["family"], by["rak4631-inkhud"]["family"]) == ("esp32s3", "nrf52840", "native", "nrf52840"),
      {e: t["family"] for e, t in by.items()})
check("  names and levels from the file", by["heltec-v3"]["name"] == "Heltec V3" and by["heltec-v3"]["level"] == "pr" and by["heltec-v3"]["support"] == "1"
      and by["native"]["name"] == "native")
check("  families counted, and cached by commit", data["families"] == {"esp32s3": 1, "native": 1, "nrf52840": 2}
      and (factory.CACHE / f"{data['commit']}.json").exists())

# The queue.
for bad, why in ((["nope"], "not targets"), ([], "one or more"), (["native"] * 60, "at most")):
    try:
        factory.queue("meshtastic-firmware", "v2.8.1.abcdef0", bad); check(f"queue refused: {why}", False)
    except ValueError as exc:
        check(f"queue refused: {why}", why in str(exc), str(exc))
real_free = factory.free_bytes
factory.free_bytes = lambda: 100 << 20
try:
    factory.queue("meshtastic-firmware", "main", ["native"]); check("queue refused: the card nearly full", False)
except ValueError as exc:
    check("queue refused: the card nearly full", "free" in str(exc), str(exc))
factory.free_bytes = real_free
out = factory.queue("meshtastic-firmware", "main", ["rak4631", "heltec-v3", "native", "rak4631-inkhud"])
jobs = factory._jobs()
check("queued: one job per target, ordered by family", out["queued"] == 4 and [j["env"] for j in jobs] == ["heltec-v3", "native", "rak4631", "rak4631-inkhud"],
      [j["env"] for j in jobs])
check("  each says what to build, from where", all(j["kind"] == "firmware" and j["repo"] == str(fw) and len(j["commit"]) == 40 for j in jobs))
check("the order: moved up first, then the family built last, then the oldest",
      [j["env"] for j in factory.order([{"env": "a", "family": "x", "queued": 1}, {"env": "b", "family": "y", "queued": 2},
                                        {"env": "c", "family": "x", "queued": 3, "up": 9}], "y")] == ["c", "b", "a"])
factory.move_up(jobs[3]["id"])
check("move up", factory._jobs()[0]["env"] == "rak4631-inkhud")
factory.cancel(jobs[1]["id"])
check("cancel", [j["env"] for j in factory._jobs()] == ["rak4631-inkhud", "heltec-v3", "rak4631"])
for bad in ("../x", "123-zzzzzz"):
    try:
        factory.cancel(bad); check(f"cancel refused: {bad}", False)
    except ValueError:
        check(f"cancel refused: {bad}", True)
factory.pause(True)
check("paused: the jobs held out of the builder's queue (its path unit would wake it for nothing)", not list(ci.QUEUE.glob("*.json"))
      and len(list(factory.HELD.glob("*.json"))) == 3 and factory.snapshot()["paused"] and len(factory.snapshot()["waiting"]) == 3)
factory.queue("meshtastic-firmware", "main", ["native"])
check("  queued while paused: held too", not list(ci.QUEUE.glob("*.json")) and len(list(factory.HELD.glob("*.json"))) == 4)
factory.pause(False)
check("resumed: back in the queue, moved up still first", len(list(ci.QUEUE.glob("*.json"))) == 4 and not list(factory.HELD.glob("*.json"))
      and factory._jobs()[0]["env"] == "rak4631-inkhud")
for p in ci.QUEUE.glob("*.json"):
    p.unlink()

# A build, with PlatformIO stood in.
ci.FIRMWARE_SCRIPT = """set -eu
mkdir -p ".pio/build/$FW_ENV"; cd ".pio/build/$FW_ENV"
echo x > "firmware-$FW_ENV-2.8.1.bin"; echo x > "firmware-$FW_ENV-2.8.1.factory.bin"; echo x > "firmware-$FW_ENV.uf2"
head -c 5000 /dev/zero > firmware.elf; echo x > output.map
echo "Compiling .pio/x.o"
"""
ci.WORK.mkdir(parents=True, exist_ok=True)
factory.queue("meshtastic-firmware", "v2.8.1.abcdef0", ["heltec-v3"])
ci.run_queue()
runs = factory.runs()
st = runs[0]
check("built: passed, its flash files kept (not the .elf or the map)", st["state"] == "passed" and st["artifacts"] == [
      "firmware-heltec-v3-2.8.1.bin", "firmware-heltec-v3-2.8.1.factory.bin", "firmware-heltec-v3.uf2"], st)
res = st.get("resources") or {}
check("  what it used: wall and CPU time, peak memory, disk, free space, heat, network", {"wall", "cpu", "peak_memory", "read_bytes", "written_bytes",
      "free_before", "free_after", "hottest", "received", "offline", "work_bytes"} <= set(res) and res["work_bytes"] > 5000, res)
check("  the source, ref, target and family on the run", (st["source"], st["ref"], st["env"], st["family"]) == ("meshtastic-firmware", "v2.8.1.abcdef0", "heltec-v3", "esp32s3"))
check("  the work folder gone", not (ci.WORK / ci.FACTORY).exists())
for _ in range(3):
    factory.queue("meshtastic-firmware", "v2.8.1.abcdef0", ["heltec-v3", "native"])
ci.run_queue()
envs = [s["env"] for s in factory.runs()]
check("each target's two newest runs kept", envs.count("heltec-v3") == 2 and envs.count("native") == 2, envs)
snap = factory.snapshot()
check("estimates from the last build of each family; readiness from what it needed", snap["estimates"]["esp32s3"]["seconds"] >= 0
      and snap["readiness"]["native"].startswith("built here") and "nrf52840" not in snap["readiness"], (snap["estimates"], snap["readiness"]))
factory.queue("meshtastic-firmware", "v2.8.1.abcdef0", ["native", "rak4631"])
w = factory.snapshot()["waiting"]
check("  a waiting build's start and finish, while every one before it has an estimate", w[0]["env"] == "native" and w[0]["start"] and w[0]["finish"]
      and w[1]["start"] and w[1]["finish"] is None, w)
for p in ci.QUEUE.glob("*.json"):
    p.unlink()
# The builder: pushes first; a job for a public repository that is not a mirror never built.
order = []
real_build, real_fw = ci.build, ci.build_firmware
ci.build = lambda job: order.append("push")
ci.build_firmware = lambda job: order.append(job["env"])
factory.queue("meshtastic-firmware", "main", ["rak4631"])
ci._put({"repo": str(fork), "branch": "main", "commit": "a" * 40, "queued": time.time() + 5})
ci.run_queue()
check("the builder: a push before the factory's", order == ["push", "rak4631"], order)
ci.build, ci.build_firmware = real_build, real_fw
n = len(factory.runs(200))
ci.build_firmware({"kind": "firmware", "repo": str(guest), "commit": "a" * 40, "env": "native", "family": "native", "ref": "main"})
check("a public repository that is not a mirror is never built", len(factory.runs(200)) == n)
# The front page's tile (the owner's choice): the view and the files a guest may have.
for p in ci.QUEUE.glob("*.json"):
    p.unlink()
ci.FIRMWARE_SCRIPT = """set -eu
mkdir -p ".pio/build/$FW_ENV"; cd ".pio/build/$FW_ENV"
for f in firmware-$FW_ENV.bin firmware-$FW_ENV.factory.bin firmware-$FW_ENV.zip firmware-$FW_ENV.uf2 meshtasticd; do echo x > "$f"; done
"""
factory.queue("meshtastic-firmware", "v2.8.1.abcdef0", ["heltec-v3", "rak4631", "native"])
ci.run_queue()
view = factory.public_view()
files = {b["env"]: b["files"] for b in view["built"]}
check("tile: an ESP32 build's images and zip; an nRF52's UF2 only; native's program never", files.get("heltec-v3") == [
      "firmware-heltec-v3.bin", "firmware-heltec-v3.factory.bin", "firmware-heltec-v3.uf2", "firmware-heltec-v3.zip"]
      and files.get("rak4631") == ["firmware-rak4631.uf2"] and "native" in files and files["native"] == ["firmware-native.uf2"], files)
check("  nothing of the runs beyond what it shows", set(view) == {"running", "waiting", "next", "paused", "built"}
      and all(set(b) == {"run", "env", "name", "family", "ref", "finished", "files"} for b in view["built"]))
run_heltec = next(b["run"] for b in view["built"] if b["env"] == "heltec-v3")
run_rak = next(b["run"] for b in view["built"] if b["env"] == "rak4631")
check("  a file it lists: served", factory.public_file(run_heltec, "firmware-heltec-v3.factory.bin") is not None)
check("  one it doesn't (the nRF52's .bin, a log, a path): not", factory.public_file(run_rak, "firmware-rak4631.bin") is None
      and factory.public_file(run_heltec, "log.txt") is None and factory.public_file("../1", "x.bin") is None)
# The hub: the tile and its routes only while the owner shows it.
import socket, urllib.request, urllib.error  # noqa: E402
def serve(show):
    st = T / f"hub-{show}"; st.mkdir()
    (st / "settings.json").write_text(json.dumps({"factory_tile": show}))
    s_ = socket.socket(); s_.bind(("127.0.0.1", 0)); port = s_.getsockname()[1]; s_.close()
    env = dict(os.environ, HUB_STATE_DIR=str(st), HUB_ETC_DIR=str(st), PORT=str(port), HUB_BIND="127.0.0.1", HUB_CI_ROOT=str(T / "ci"))
    hub = subprocess.Popen([str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=1); break
        except OSError:
            time.sleep(0.1)
    return hub, port
def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
hub, port = serve(True)
try:
    code, home = get(port, "/")
    check("shown: the tile on the front page", code == 200 and b'id="factory-card"' in home)
    code, body = get(port, "/factory.json")
    check("  its view, to anyone", code == 200 and any(b["env"] == "heltec-v3" for b in json.loads(body)["built"]))
    code, body = get(port, f"/factory/file?run={run_heltec}&name=firmware-heltec-v3.uf2")
    check("  a file, as a download", code == 200 and body == b"x\n")
    check("  a log or another file: no", get(port, f"/factory/file?run={run_heltec}&name=log.txt")[0] == 404)
finally:
    hub.terminate(); hub.wait()
hub, port = serve(False)
try:
    code, home = get(port, "/")
    check("not shown (the default): no tile, and both routes 404", b'id="factory-card"' not in home and get(port, "/factory.json")[0] == 404
          and get(port, f"/factory/file?run={run_heltec}&name=firmware-heltec-v3.uf2")[0] == 404)
finally:
    hub.terminate(); hub.wait()
# The doctor (health.py, step 36): builds waiting over a day, a family that always fails, the disk.
from irate_box.root import health  # noqa: E402
health.STATE = T  # its ci/ is the builder's here
for p in ci.QUEUE.glob("*.json"):
    p.unlink()
ci._put({"kind": "firmware", "env": "x", "family": "esp32", "queued": time.time() - 2 * 86400})
for i, state in enumerate(("failed", "timed out")):
    d = ci.RUNS / ci.FACTORY / str(900 + i); d.mkdir(parents=True)
    (d / "status.json").write_text(json.dumps({"family": "esp32c6", "state": state}))
f = {x["id"]: x for x in health.check_builds()}
check("doctor: a build waiting over a day", f.get("builds-waiting", {}).get("status") == "warn" and "1 build has waited over a day" in f["builds-waiting"]["detail"], f.get("builds-waiting"))
check("  a family that has failed every time, not one that has passed", "builds-family:esp32c6" in f and "builds-family:esp32s3" not in f, sorted(f))
check("  what builds take, said", f.get("builds-disk", {}).get("status") in ("ok", "warn") and "MB" in f["builds-disk"]["detail"])
check("  in the scan", "check_builds" in (REPO / "irate_box/root/health.py").read_text().split("def scan")[1][:400])
print("ok" if not fails else f"{fails} failure(s)")
sys.exit(1 if fails else 0)
