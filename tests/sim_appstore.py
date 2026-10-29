# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""The App store's hub side (hub/appstore.py) against a hub it starts: a holder made to hold, its files then
listed, downloaded and resumed; what the page says; a holder for members only, hidden from a guest and given
to an account; nothing outside a holder's own recorded files served; the app's switch closing every address.
python3 tests/sim_appstore.py"""

import hashlib
import http.client
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))
from freeport import free_port

state = Path(tempfile.mkdtemp(prefix="appstore-"))
holds = state / "holds"
port = free_port()
SECRET = "e" * 64
KEY = secrets.token_bytes(32)
(state / "admin-seen.key").write_bytes(KEY)
env = dict(
    os.environ,
    HUB_FRONT_SECRET=SECRET,
    HUB_STATE_DIR=str(state),
    HUB_ETC_DIR=str(state),
    HUB_HOLDS_ROOT=str(holds),
    PORT=str(port),
    HUB_BIND="127.0.0.1",
)
hub = subprocess.Popen(
    [str(REPO / "irate-box"), "server"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info}"))
    fails += not cond


def request(method, path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request(method, path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()


sys.path.insert(0, str(REPO))
os.environ.update(HUB_STATE_DIR=str(state), HUB_HOLDS_ROOT=str(holds))
from irate_box.hub import server as hub_server
from irate_box.library import holder

ADMIN_COOKIE = "irate_admin_seen=" + hub_server.admin_seen_value()

try:
    for _ in range(150):
        try:
            request("GET", "/status")
            break
        except OSError:
            time.sleep(0.1)

    # A holder whose folder the keeper published: two versions, their files real bytes on disk, their
    # digests in each sha256.json (what the keeper writes), and the settings naming it.
    APK = b"not a real apk, but a held file of some length" * 40
    OLD = APK[:-7]
    tag, other = "v9.9.9", "v9.9.8"
    name = "androidApp-fdroid-universal-release.apk"
    folder = holds / "demo"
    for where, data in ((tag, APK), (other, OLD)):
        (folder / where).mkdir(parents=True)
        (folder / where / name).write_bytes(data)
        (folder / where / "sha256.json").write_text(
            json.dumps({name: [len(data), 123, hashlib.sha256(data).hexdigest()]})
        )
    (folder / "index.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "title": "Demo app",
                "repo": "meshtastic/Meshtastic-Android",
                "provider": "github",
                "visibility": "public",
                "updated": "2026-10-28T00:00:00Z",
                "versions": [
                    {
                        "tag": tag,
                        "channel": "stable",
                        "published": "2026-10-20T00:00:00Z",
                        "notes": "Nine, **bold** in text <only>",
                        "files": [{"name": name, "size": len(APK), "sha256": hashlib.sha256(APK).hexdigest()}],
                    },
                    {
                        "tag": other,
                        "channel": "stable",
                        "published": "2026-10-01T00:00:00Z",
                        "notes": "",
                        "files": [{"name": name, "size": len(OLD), "sha256": hashlib.sha256(OLD).hexdigest()}],
                    },
                ],
            }
        )
    )
    settings = state / "library" / "holders.json"
    settings.parent.mkdir(parents=True, exist_ok=True)

    def set_holders(visibility):
        settings.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "holders": [
                        {
                            "id": "demo",
                            "title": "Demo app",
                            "repo": "meshtastic/Meshtastic-Android",
                            "holds": [{"label": "x", "include": "apk$"}],
                            "visibility": visibility,
                        }
                    ],
                }
            )
        )

    set_holders("public")

    def page(headers=None):
        return request("GET", "/appstore/", headers)

    st, head, body = page()
    text = body.decode()
    check("the page opens", st == 200 and "text/html" in head.get("content-type", ""), (st, head))
    check(
        "  nosniff, no-cache",
        head.get("x-content-type-options") == "nosniff" and "no-cache" in head.get("cache-control", ""),
    )
    check("  the holder and its file are named", "Demo app" in text and name in text)
    check("  both versions, newest first", 0 <= text.index(tag) < text.index(other))
    check("  release notes as text, never HTML", "<strong>" not in text and "**bold**" in text)
    check("  a bracket in them is escaped", "<only>" not in text)
    check("  plain links, no script needed to fetch", f"/appstore/files/demo/{tag}/{name}" in text)
    check(
        "  sizes and digests shown",
        "1 KB" in text or "2 KB" in text,
    )
    check("  the digest behind a toggle", hashlib.sha256(APK).hexdigest() in text and "<details" in text)
    check("  the QR holds the page's own address", f'data-url="http://127.0.0.1:{port}/appstore/"' in text)
    check("  the sideloading line is on the page", "install" in text)

    st, head, body = request("GET", f"/appstore/files/demo/{tag}/{name}")
    check("a held file downloads", st == 200 and body == APK, st)
    check(
        "  as an APK attachment, nosniff, no guessing",
        head.get("content-type") == "application/vnd.android.package-archive"
        and "attachment" in head.get("content-disposition", "")
        and head.get("x-content-type-options") == "nosniff",
        head,
    )
    check(
        "  ranges accepted, the digest its ETag",
        head.get("accept-ranges") == "bytes" and head.get("etag") == f'"{hashlib.sha256(APK).hexdigest()}"',
    )

    st, head, body = request("GET", f"/appstore/files/demo/{tag}/{name}", {"Range": f"bytes={len(APK) - 100}-"})
    check("a part of it (a phone resuming)", st == 206 and body == APK[-100:], st)
    check("  the range said back", head.get("content-range") == f"bytes {len(APK) - 100}-{len(APK) - 1}/{len(APK)}")
    st, _, _ = request("GET", f"/appstore/files/demo/{tag}/{name}", {"Range": f"bytes={len(APK) + 5}-"})
    check("  one beyond the end refused", st == 416, st)
    st, _, _ = request("GET", f"/appstore/files/demo/{tag}/other.apk")
    check("a file the version never recorded: 404", st == 404, st)
    (folder / tag / "secret.txt").write_text("not in sha256.json")
    st, _, _ = request("GET", f"/appstore/files/demo/{tag}/secret.txt")
    check("  even one that is really there", st == 404, st)
    st, _, _ = request("GET", "/appstore/files/demo/nope/" + name)
    check("  and one under a version not kept", st == 404, st)
    st, _, _ = request("GET", f"/appstore/files/demo/..%2F..%2Fstatus.json/{name}")
    check("nothing outside the holder's own folder", st == 404, st)
    st, _, _ = request("GET", f"/appstore/files/demo/{tag}/")
    check("never a directory listing", st == 404, st)
    st, _, _ = request("GET", f"/appstore/files/elsewhere/{tag}/{name}")
    check("  nor a holder the settings do not name", st == 404, st)

    st, head, _ = request("GET", f"/appstore/latest/demo/{name}")
    check(
        "the stable link goes to the newest version that has the file",
        st == 302 and head.get("location") == f"/appstore/files/demo/{tag}/{name}",
        (st, head.get("location")),
    )
    st, head, body = request("GET", head.get("location"))
    check("  and that is the newer bytes", st == 200 and body == APK)
    (folder / "index.json").write_text(
        json.dumps(
            {
                **json.loads((folder / "index.json").read_text()),
                "versions": [json.loads((folder / "index.json").read_text())["versions"][1]],
            }
        )
    )
    st, head, _ = request("GET", f"/appstore/latest/demo/{name}")
    check(
        "  which follows the newest: after the newer went, the older",
        head.get("location") == f"/appstore/files/demo/{other}/{name}",
        head.get("location"),
    )
    (folder / "index.json").write_text(json.dumps({**json.loads((folder / "index.json").read_text()), "versions": []}))
    st, _, _ = request("GET", f"/appstore/latest/demo/{name}")
    check("  and nothing once none are kept", st == 404, st)

    # index.json and the Atom feed, both newest first.
    (folder / "index.json").write_text(
        json.dumps(
            {
                "id": "demo",
                "title": "Demo app",
                "repo": "meshtastic/Meshtastic-Android",
                "provider": "github",
                "visibility": "public",
                "updated": "2026-10-28T00:00:00Z",
                "versions": [
                    {
                        "tag": tag,
                        "channel": "stable",
                        "published": "2026-10-20T00:00:00Z",
                        "notes": "",
                        "files": [{"name": name, "size": len(APK), "sha256": hashlib.sha256(APK).hexdigest()}],
                    },
                    {
                        "tag": other,
                        "channel": "stable",
                        "published": "2026-10-01T00:00:00Z",
                        "notes": "",
                        "files": [{"name": name, "size": len(OLD), "sha256": hashlib.sha256(OLD).hexdigest()}],
                    },
                ],
            }
        )
    )
    st, head, body = request("GET", "/appstore/index.json")
    data = json.loads(body)
    check(
        "index.json: the holder, two versions, the addresses",
        st == 200
        and data["holders"][0]["id"] == "demo"
        and len(data["holders"][0]["versions"]) == 2
        and data["holders"][0]["versions"][0]["files"][0]["url"] == f"/appstore/files/demo/{tag}/{name}",
        (st, body[:200]),
    )
    st, head, body = request("GET", "/appstore/feed.xml")
    xml = body.decode()
    check(
        "the Atom feed: each held file, newest first, with size and type",
        st == 200
        and "atom" in head.get("content-type", "")
        and xml.count("<entry>") == 2
        and f'length="{len(APK)}"' in xml
        and "application/vnd.android.package-archive" in xml
        and xml.index(f"/appstore/files/demo/{tag}/") < xml.index(f"/appstore/files/demo/{other}/"),
        (st, xml[:200]),
    )

    # A holder for members: not a guest's, and its file cannot be fetched by its address alone.
    set_holders("members")
    st, _, body = page()
    check("a members holder: not on a guest's page", "Demo app" not in body.decode() and st == 200, st)
    st, _, _ = request("GET", f"/appstore/files/demo/{tag}/{name}")
    check("  its file is no use by address alone", st == 404, st)
    st, _, _ = request("GET", f"/appstore/latest/demo/{name}")
    check("  nor through the stable link", st == 404, st)
    st, _, body = request("GET", "/appstore/index.json")
    check("  and not in the lists", "demo" not in body.decode())
    st, _, body = page({"Cookie": ADMIN_COOKIE})
    check("  to the administrator it is there", "Demo app" in body.decode(), st)
    st, head, body = request("GET", f"/appstore/files/demo/{tag}/{name}", {"Cookie": ADMIN_COOKIE})
    check("  and the file follows", st == 200 and body == APK, st)
    set_holders("public")

    # The app's own switch.
    (state / "control").mkdir(exist_ok=True)

    def switched(mode):
        (state / "control" / "access.json").write_text(json.dumps({"appstore": mode}))

    switched("off")
    for path in ("/appstore/", "/appstore/index.json", "/appstore/feed.xml", f"/appstore/files/demo/{tag}/{name}"):
        st, _, _ = request("GET", path)
        check(f"switched off: {path} answers 404", st == 404, st)
    st, _, body = request("GET", "/")
    check("  and the tile leaves the home page", "/appstore/" not in body.decode())
    switched("private")
    st, _, _ = page()
    check("private: a guest is not shown the page", st == 404, st)
    st, _, body = page({"Cookie": ADMIN_COOKIE})
    check("  the administrator is", st == 200 and "Demo app" in body.decode(), st)
    switched("public")
    st, _, _ = page()
    check("and open again", st == 200, st)
    st, _, body = request("GET", "/")
    check("the tile is on the home page", "/appstore/" in body.decode())

    # --- the firmware mirror: a read-only entry that follows the flasher's access ---
    fwroot = state / "firmware"
    fwroot.mkdir()
    FW_VERSIONS = {
        "2.8.2.abc1234": ("beta", ["rak4631", "heltec-v3"], 1792886401),
        "2.8.1.def5678": ("alpha", ["rak4631"], 1792886400),
    }

    def firmware_version(version, channel, boards, epoch):
        vdir = fwroot / version
        vdir.mkdir()
        (vdir / f"firmware-{version}.json").write_text(
            json.dumps({"version": version, "targets": [{"board": b, "platform": "x"} for b in boards]})
        )
        digests = {}
        info = {
            "channel": channel,
            "boards": boards,
            "files": 0,
            "bytes": 0,
            "fetched": "2026-10-28T00:00:00Z",
            "checks": {},
        }
        for board in boards:
            if board == "rak4631":
                names = [
                    (f"firmware-{board}-{version}.uf2", None),
                    (f"firmware-{board}-{version}.hex", None),
                    (f"firmware-{board}-{version}-ota.zip", None),
                    (f"firmware-{board}-{version}.elf", None),
                ]
            else:
                names = [
                    (f"firmware-{board}-{version}.factory.bin", None),
                    (f"firmware-{board}-{version}.bin", "app0"),
                    (f"littlefs-{board}-{version}.bin", "spiffs"),
                    ("mt-esp32s3-ota.bin", "app1"),
                ]
            files = []
            for name, part in names:
                data = f"{version}:{name}".encode()
                (vdir / name).write_bytes(data)
                digests[name] = [len(data), 1, hashlib.sha256(data).hexdigest()]
                entry = {"name": name, "bytes": len(data), "md5": hashlib.md5(data).hexdigest()}
                if part:
                    entry["part_name"] = part
                files.append(entry)
                info["files"] += 1
                info["bytes"] += len(data)
            (vdir / f"firmware-{board}-{version}.mt.json").write_text(
                json.dumps(
                    {
                        "version": version,
                        "displayName": "RAK WisBlock 4631" if board == "rak4631" else "Heltec V3",
                        "architecture": "nrf52840" if board == "rak4631" else "esp32-s3",
                        "build_epoch": epoch,
                        "files": files,
                    }
                )
            )
        (vdir / "sha256.json").write_text(json.dumps(digests))
        return info

    fwstatus = {"versions": {}, "last_check": "2026-10-28T00:00:00Z"}
    for version, (channel, boards, epoch) in FW_VERSIONS.items():
        fwstatus["versions"][version] = firmware_version(version, channel, boards, epoch)
    (fwroot / "status.json").write_text(json.dumps(fwstatus))
    (fwroot / "index.json").write_text(
        json.dumps(
            {
                "releases": {
                    "stable": [{"id": "v2.8.2.abc1234", "release_notes": "Beta notes as text."}],
                    "alpha": [{"id": "v2.8.1.def5678", "release_notes": ""}],
                }
            }
        )
    )
    fwsettings = state / "library" / "firmware.json"
    fwsettings.write_text(json.dumps({"enabled": True}))
    st, _, body = page()
    fwpage = body.decode()
    check(
        "firmware: the entry is on the page, its boards folded, its files at the flasher's addresses",
        st == 200
        and "Meshtastic firmware" in fwpage
        and '<details class="appstore-board"' in fwpage
        and '<details class="appstore-board" open' not in fwpage
        and "/flasher/firmware/2.8.2.abc1234/firmware-rak4631-2.8.2.abc1234.uf2" in fwpage,
        st,
    )
    check(
        "  a role from the name: the UF2, the DFU, the OTA, the full flash",
        "UF2" in fwpage and "DFU" in fwpage and "Update (OTA)" in fwpage and "Full flash" in fwpage,
    )
    check("  the .elf never listed", ".elf" not in fwpage)
    st, head, _ = request("GET", "/appstore/latest/meshtastic-firmware/rak4631/uf2")
    check(
        "  the stable link goes to the newest version with that board and role",
        st == 302 and head.get("location") == "/flasher/firmware/2.8.2.abc1234/firmware-rak4631-2.8.2.abc1234.uf2",
        (st, head.get("location")),
    )
    st, head, _ = request("GET", "/appstore/latest/meshtastic-firmware/rak4631/uf2?channel=alpha")
    check(
        "  and the channel asked for",
        st == 302 and head.get("location") == "/flasher/firmware/2.8.1.def5678/firmware-rak4631-2.8.1.def5678.uf2",
        (st, head.get("location")),
    )
    st, _, _ = request("GET", "/appstore/latest/meshtastic-firmware/nope/uf2")
    check("  a board not kept: 404", st == 404, st)
    st, _, body = request("GET", "/appstore/index.json")
    fwrow = next((h for h in json.loads(body)["holders"] if h["id"] == "meshtastic-firmware"), None)
    check(
        "  index.json: the entry, with board, architecture and role on each file",
        fwrow is not None
        and fwrow["versions"][0]["files"][0]["board"] == "rak4631"
        and fwrow["versions"][0]["files"][0]["architecture"] == "nrf52840"
        and fwrow["versions"][0]["files"][0]["role"] == "uf2",
        fwrow and fwrow["versions"][0]["files"][0],
    )
    st, _, body = request("GET", "/appstore/feed.xml")
    check(
        "  the feed includes the firmware files and their board",
        st == 200 and "/flasher/firmware/" in body.decode() and 'term="rak4631"' in body.decode(),
        st,
    )
    fwsettings.write_text(json.dumps({"enabled": False}))
    st, _, body = page()
    check("  downloads off: the entry is not shown", st == 200 and "Meshtastic firmware" not in body.decode())
    fwsettings.write_text(json.dumps({"enabled": True}))
    (state / "control" / "access.json").write_text(json.dumps({"appstore": "public", "flasher": "off"}))
    st, _, body = page()
    check("  the flasher off: the entry is not shown", st == 200 and "Meshtastic firmware" not in body.decode())
    (state / "control" / "access.json").write_text(json.dumps({"appstore": "public", "flasher": "private"}))
    st, _, body = page()
    check("  the flasher private: a guest is not shown it", st == 200 and "Meshtastic firmware" not in body.decode())
    st, _, body = page({"Cookie": ADMIN_COOKIE})
    check("  the administrator is", st == 200 and "Meshtastic firmware" in body.decode())
    (state / "control" / "access.json").write_text(json.dumps({"appstore": "public", "flasher": "public"}))
    try:
        holder.validate_holder({"id": "meshtastic-firmware", "repo": "a/b", "holds": []})
        reserved = False
    except holder.LibrarianError as exc:
        reserved = getattr(exc, "message", {}).get("id") == "library.holder.reserved"
    check("  a holder cannot take the firmware's name", reserved)

    # Nothing kept yet: the page says so, and only the administrator is pointed at the setting.
    fwsettings.write_text(json.dumps({"enabled": False}))
    settings.write_text(json.dumps({"enabled": False, "holders": []}))
    st, _, body = page()
    check(
        "no holders: the page still opens, says there is nothing to install yet, with the confused krab",
        st == 200 and "Nothing to install yet." in body.decode() and "/art/krab-confused.webp" in body.decode(),
        st,
    )
    check("  no setting link for a guest", "/admin/#appstore" not in body.decode())
    text = body.decode()
    check(
        "  the phone's hint on two lines, the certificate linked on the second",
        text.count('class="setting-desc appstore-hint"') == 2
        and text.index("A phone needs permission") < text.index('<a href="/certificate">'),
    )
    check(
        "  the QR in its own block, said to be for another phone",
        'class="appstore-qr-block"' in text and "on another phone" in text,
    )
    st, _, body = page({"Cookie": ADMIN_COOKIE})
    check("  the administrator is offered it", "/admin/#appstore" in body.decode())
    # A holder whose last check failed: the administrator sees why it is empty, a guest does not.
    settings.write_text(
        json.dumps(
            {"enabled": True, "holders": [{"id": "failing", "title": "Failing", "repo": "x/y", "visibility": "public"}]}
        )
    )
    (holder.ROOT / "failing").mkdir(parents=True, exist_ok=True)
    (holder.ROOT / "failing" / "status.json").write_text(json.dumps({"error": "Insufficient storage for app.apk."}))
    _, _, body = page({"Cookie": ADMIN_COOKIE})
    check(
        "  an empty holder whose check failed: the administrator sees the error",
        "The last check failed: Insufficient storage for app.apk." in body.decode(),
        body.decode()[-600:],
    )
    _, _, body = page()
    check("  and a guest does not", "Insufficient storage" not in body.decode())
    st, _, _ = request("GET", "/appstore")
    check("/appstore is the hub's, and its page follows", st in (200, 301, 302), st)

    print("ok" if not fails else f"{fails} failures")
finally:
    hub.terminate()
    shutil.rmtree(state, ignore_errors=True)
sys.exit(1 if fails else 0)
