# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
"""Board images for the Rockchip page (library/rkimages.py), offline: one image per Lyra board from the newest
mPWRD-OS and Armbian releases, chosen from GitHub's assets, the publisher's SHA-256 from the asset's digest, a sidecar
beside each, check-only, off, a changed release replacing the same board's image, and a hash that does not match.
python3 tests/sim_rkimages.py"""

import hashlib
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
T = Path(tempfile.mkdtemp(prefix="rkimages-"))
(T / "state" / "rk-images").mkdir(parents=True)
(T / "library").mkdir()
os.environ.update(HUB_STATE_DIR=str(T / "state"))
sys.path.insert(0, str(REPO))
from irate_box.library import librarian, rkimages

librarian.LIB_DIR = T / "library"
rkimages.SETTINGS = librarian.LIB_DIR / "rk-images.json"
rkimages.ROOT = T / "state" / "rk-images"
rkimages.STATUS = rkimages.ROOT / "status.json"
fails = 0


def check(name, cond, info=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  {info!r}"))
    fails += not cond


def asset(name, data=b"image"):
    return {
        "name": name,
        "size": len(data),
        "browser_download_url": f"https://example.invalid/{name}",
        "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
    }


def release(tag, assets):
    return {"tag_name": tag, "assets": assets}


MP = release(
    "v1",
    [
        asset("mPWRD-OS_1_Luckfox-lyra-plus_trixie_minimal.img.xz", b"plus"),
        asset("mPWRD-OS_1_Luckfox-lyra-ultra-w_trixie_minimal.img.xz", b"ultra"),
        asset("mPWRD-OS_1_Luckfox-lyra-zero-w_trixie_minimal.img.xz", b"zero"),
        asset("mPWRD-OS_1_Rpi4b_trixie_minimal.img.xz", b"pi"),
    ],
)
AR = release(
    "v2",
    [
        asset("Armbian_2_Luckfox-lyra-plus_trixie_vendor_6.1_minimal.img.xz", b"a-plus"),
        asset("Armbian_2_Luckfox-lyra-plus_resolute_vendor_6.1_xfce_desktop.img.xz", b"a-xfce"),
        asset("Armbian_2_Luckfox-lyra-ultra-w_trixie_vendor_6.1_minimal.img.xz", b"a-ultra"),
        asset("Armbian_2_Luckfox-lyra-zero-w_trixie_vendor_6.1_minimal.img.xz", b"a-zero"),
    ],
)
calls = []


def api(path, auth=None):
    calls.append(path)
    if "mPWRD-OS" in path:
        return MP
    if "armbian" in path:
        return AR
    raise AssertionError(path)


downloads = []


contents = {
    "mPWRD-OS_1_Luckfox-lyra-plus_trixie_minimal.img.xz": b"plus",
    "mPWRD-OS_1_Luckfox-lyra-ultra-w_trixie_minimal.img.xz": b"ultra",
    "mPWRD-OS_1_Luckfox-lyra-zero-w_trixie_minimal.img.xz": b"zero",
    "Armbian_2_Luckfox-lyra-plus_trixie_vendor_6.1_minimal.img.xz": b"a-plus",
    "Armbian_2_Luckfox-lyra-ultra-w_trixie_vendor_6.1_minimal.img.xz": b"a-ultra",
    "Armbian_2_Luckfox-lyra-zero-w_trixie_vendor_6.1_minimal.img.xz": b"a-zero",
}


def download(url, dest, auth=None, name=None, expected=0, resume=False):
    downloads.append(name)
    Path(dest).write_bytes(contents.get(name, name.encode()))


librarian._api, librarian._download = api, download
rkimages.set_settings(enabled=True, armbian_boards=["luckfox-lyra-plus", "luckfox-lyra-ultra-w", "luckfox-lyra-zero-w"])
line = rkimages.sync(check_only=True, log=lambda *_: None)
check(
    "check only: three mPWRD-OS and three Armbian images chosen, one per board, nothing downloaded",
    line.startswith("6 images") and not downloads and not list(rkimages.ROOT.glob("*.img*")),
    (line, downloads),
)
line = rkimages.sync(log=lambda *_: None)
check(
    "each image kept with its publisher's SHA-256 beside it",
    line.startswith("6 images")
    and len(downloads) == 6
    and all((rkimages.ROOT / n).is_file() and (rkimages.ROOT / f"{n}.sha256").is_file() for n in downloads)
    and (rkimages.ROOT / "mPWRD-OS_1_Luckfox-lyra-zero-w_trixie_minimal.img.xz.sha256").read_text().split()[0]
    == hashlib.sha256(b"zero").hexdigest(),
    (line, downloads),
)
check(
    "  the status names both releases and no error",
    rkimages.status()["releases"] == {"mpwrd": "v1", "armbian": "v2"} and not rkimages.status()["errors"],
    rkimages.status(),
)

# A release with one board's image changed: only that one is fetched; the old one is gone.
MP["assets"][2] = asset("mPWRD-OS_1_Luckfox-lyra-zero-w_trixie_minimal.img.xz", b"zero2")
contents["mPWRD-OS_1_Luckfox-lyra-zero-w_trixie_minimal.img.xz"] = b"zero2"
downloads.clear()
line = rkimages.sync(log=lambda *_: None)
check(
    "a changed image replaces only its own board's, and the old file is removed",
    downloads == ["mPWRD-OS_1_Luckfox-lyra-zero-w_trixie_minimal.img.xz"]
    and (rkimages.ROOT / "mPWRD-OS_1_Luckfox-lyra-zero-w_trixie_minimal.img.xz").read_bytes() == b"zero2",
    downloads,
)
# An owner's image and the rescue image survive a sync; a publisher whose check fails keeps its images.
owner = rkimages.ROOT / "own.img"
rescue = rkimages.ROOT / "alpine-rescue.img"
for path in (owner, rescue):
    path.write_bytes(b"x")
    (rkimages.ROOT / f"{path.name}.sha256").write_text("0" * 64 + f"  {path.name}\n")
downloads.clear()
rkimages.sync(log=lambda *_: None)
check(
    "an owner's image and the Alpine rescue survive, with their sidecars",
    owner.is_file()
    and (rkimages.ROOT / "own.img.sha256").is_file()
    and rescue.is_file()
    and (rkimages.ROOT / "alpine-rescue.img.sha256").is_file(),
    downloads,
)
api_real = librarian._api


def api_mpwrd_down(path, auth=None):
    if "mPWRD-OS" in path:
        raise librarian.LibrarianError("down")
    return AR


librarian._api = api_mpwrd_down
downloads.clear()
rkimages.sync(log=lambda *_: None)
check(
    "a publisher's check failing keeps its earlier images",
    (rkimages.ROOT / "mPWRD-OS_1_Luckfox-lyra-zero-w_trixie_minimal.img.xz").is_file(),
    downloads,
)
librarian._api = api_real
free_real = librarian._free_bytes
(rkimages.ROOT / "Armbian_2_Luckfox-lyra-plus_trixie_vendor_6.1_minimal.img.xz").unlink()
(rkimages.ROOT / "Armbian_2_Luckfox-lyra-plus_trixie_vendor_6.1_minimal.img.xz.sha256").unlink()
librarian._free_bytes = lambda path: 1
line = rkimages.sync(log=lambda *_: None)
check(
    "no room for an image: said, and nothing written",
    any("not enough space" in e for e in rkimages.status()["errors"]),
    rkimages.status()["errors"],
)
librarian._free_bytes = free_real
# keep 2 across three releases of one board: the two newest stay, the oldest file, sidecar and record go.
for path in list(rkimages.ROOT.iterdir()):
    path.unlink()
rkimages.STATUS.unlink(missing_ok=True)
rkimages.set_settings(enabled=True, mpwrd=True, armbian=False, keep=2)
names = [
    "mPWRD-OS_3_Luckfox-lyra-zero-w_trixie_minimal.img.xz",
    "mPWRD-OS_4_Luckfox-lyra-zero-w_trixie_minimal.img.xz",
    "mPWRD-OS_5_Luckfox-lyra-zero-w_trixie_minimal.img.xz",
]
for n in names:
    MP["assets"] = [asset(n, n.encode())]
    MP["tag_name"] = n.split("_")[1]
    rkimages.sync(log=lambda *_: None)
check(
    "keep 2: the two newest stay, the oldest's file and sidecar go, and nothing unrecorded is left",
    (rkimages.ROOT / names[1]).is_file()
    and (rkimages.ROOT / names[2]).is_file()
    and not (rkimages.ROOT / names[0]).exists()
    and not (rkimages.ROOT / f"{names[0]}.sha256").exists()
    and names[0] not in rkimages.status()["managed"],
    (sorted(p.name for p in rkimages.ROOT.iterdir()), rkimages.status().get("managed")),
)
# A check must not prune: the old image stays while the new one is only looked at.
MP["assets"] = [asset("mPWRD-OS_6_Luckfox-lyra-zero-w_trixie_minimal.img.xz", b"6")]
oldest = "mPWRD-OS_4_Luckfox-lyra-zero-w_trixie_minimal.img.xz"
rkimages.sync(check_only=True, log=lambda *_: None)
check("a check leaves the images on disk alone", (rkimages.ROOT / oldest).is_file(), oldest)
# A failed download leaves the old image: the prune counts only images on disk.
rkimages.set_settings(keep=1)
MP["assets"] = [asset("mPWRD-OS_7_Luckfox-lyra-zero-w_trixie_minimal.img.xz", b"7")]


def download_fail(url, dest, auth=None, name=None, expected=0, resume=False):
    raise librarian.LibrarianError("down")


real_download = download
librarian._download = download_fail
newest = "mPWRD-OS_5_Luckfox-lyra-zero-w_trixie_minimal.img.xz"
rkimages.sync(log=lambda *_: None)
check("a failed download does not lose the old image", (rkimages.ROOT / newest).is_file(), newest)
librarian._download = real_download
check(
    "  off: nothing checked or downloaded",
    not rkimages.set_settings(enabled=False)["enabled"] and rkimages.sync() == "off",
)
print(f"failures: {fails}")
sys.exit(1 if fails else 0)
