#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Item 76's mockup: the real /admin, with throwaway state and sample data. Starts a hub of this checkout on
# 127.0.0.1 with one source up to date, one with something newer and one whose check failed, for Tom to look at.
#
#   tests/browser/item76-mockup.sh
#   PORT=8876 tests/browser/item76-mockup.sh
#
# The state is made fresh under /tmp each run; nothing on the box or in the checkout is touched.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
port="${PORT:-8765}"
run_tmp="$(mktemp -d "${TMPDIR:-/tmp}/irate-box-item76.XXXXXX")"
state="$run_tmp/state"
mkdir -p "$state/library" "$state/git/public" "$state/git/private" "$state/holds" "$state/firmware" "$state/rk-images" "$state/zim"

python3 - "$state" "$repo" <<'PY'
import hashlib, json, os, subprocess, sys, time
from pathlib import Path
state, repo = Path(sys.argv[1]), Path(sys.argv[2])
now = time.time()
iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(s))

# Git repositories: three local repositories, so the Git page has cards.
for name, desc in [("meshtastic-firmware-notes", "Notes on the firmware"), ("docs", "Documentation drafts"), ("tools", "Small tools for the box")]:
    path = state / "git" / "public" / f"{name}.git"
    subprocess.run(["git", "init", "--quiet", "--bare", f"--initial-branch=main", str(path)], check=True)
    (path / "description").write_text(f"{desc}\n")

# Mirrors: one up to date, one with something newer, one whose check failed.
mirrors = {"mirrors": [
    {"name": "meshcore", "area": "public", "upstream": "https://github.com/meshcore-dev/MeshCore", "branches": ["main"],
     "groups": [{"kind": "release", "keep": 2}], "history": "shallow", "budget_mb": 2048, "submodules": False},
    {"name": "meshtastic-firmware", "area": "public", "upstream": "https://github.com/meshtastic/firmware", "branches": ["master"],
     "groups": [{"kind": "release", "keep": 2}, {"kind": "prerelease", "keep": 2}], "history": "shallow", "budget_mb": 4096, "submodules": True},
    {"name": "irate-box-docs", "area": "private", "upstream": "https://github.com/NomDeTom/irate-box", "branches": ["main"],
     "groups": [{"kind": "tags", "pattern": "v*", "keep": 3}], "history": "shallow", "budget_mb": 1024, "submodules": False},
]}
(state / "library" / "mirrors.json").write_text(json.dumps(mirrors, indent=2))
(state / "library" / "mirrors-status.json").write_text(json.dumps({
    "meshcore": {"checked": now - 900, "outcome": "up to date", "size": 18_400_000, "updated": now - 900, "wanted": ["refs/heads/main"], "missing": [], "extra": []},
    "meshtastic-firmware": {"checked": now - 1800, "outcome": "2 to download, 0 to remove", "size": 96_000_000, "updated": now - 86400, "wanted": ["refs/heads/master"], "missing": ["refs/heads/master"], "extra": []},
    "irate-box-docs": {"checked": now - 3600, "outcome": "error: could not reach the upstream: HTTP 503", "error": "could not reach the upstream: HTTP 503", "size": 0, "wanted": [], "missing": [], "extra": []},
}, indent=2))

# App store: three projects, one up to date, one with something newer, one whose check failed.
holders = {"enabled": True, "holders": [
    {"id": "meshtastic-android", "title": "Meshtastic (Android)", "provider": "github", "repo": "meshtastic/Meshtastic-Android",
     "holds": [{"label": "the release APK", "include": "^androidApp-google-release\\.apk$"}], "channels": {"stable": 2, "pre-release": 0},
     "extra_channels": [], "tag_pattern": r"^v?\d+(\.\d+)+", "min_age_days": 0, "budget_mb": 512, "visibility": "public", "companions": False},
    {"id": "obtainium", "title": "Obtainium", "provider": "github", "repo": "ImranR98/Obtainium",
     "holds": [{"label": "the phone APKs", "include": "^app-(arm64-v8a|armeabi-v7a)-release\\.apk$"}], "channels": {"stable": 1, "pre-release": 0},
     "extra_channels": [], "tag_pattern": r"^v?\d+(\.\d+)+", "min_age_days": 0, "budget_mb": 0, "visibility": "public", "companions": True},
    {"id": "syncthing-fork", "title": "Syncthing (Fork)", "provider": "github", "repo": "researchxxl/syncthing-android",
     "holds": [{"label": "the phone APKs", "include": "_release_v[0-9.]+_(arm64-v8a|armeabi-v7a)\\.apk$"}], "channels": {"stable": 1, "pre-release": 0},
     "extra_channels": [], "tag_pattern": r"^v?\d+(\.\d+)+", "min_age_days": 0, "budget_mb": 256, "visibility": "members", "companions": False},
]}
(state / "library" / "holders.json").write_text(json.dumps(holders, indent=2))
for hid, status in {
    "meshtastic-android": {"last_check": iso(now - 1200), "outcome": "up to date", "latest": [{"tag": "v2.7.14", "channel": "stable"}], "versions": {"v2.7.14": "stable"}},
    "obtainium": {"last_check": iso(now - 2400), "outcome": "newest: v1.2.0 (stable)", "latest": [{"tag": "v1.2.0", "channel": "stable"}], "versions": {"v1.1.9": "stable"}},
    "syncthing-fork": {"last_check": iso(now - 3600), "outcome": "error: HTTP 403: API rate limit exceeded", "error": "HTTP 403: API rate limit exceeded"},
}.items():
    folder = state / "holds" / hid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "status.json").write_text(json.dumps(status, indent=2))
(state / "holds" / "status.json").write_text(json.dumps({"last_check": iso(now - 1200), "outcomes": {}}, indent=2))

# Books: three sources, one up to date, one with something newer, one whose check failed.
sources = {"policy": {"keep_old": 1, "check_every_hours": 24, "min_free_mb": 512, "books_budget_mb": 4096, "auto_install": 1,
                      "hub_check_every_hours": 24, "hub_auto": 0, "hub_window_start": 2, "hub_window_end": 5,
                      "mirrors_every_hours": 24, "mirrors_auto": 1, "firmware_every_hours": 24, "firmware_auto": 1,
                      "holders_every_hours": 24, "holders_auto": 1, "kits_every_hours": 24, "podcasts_every_hours": 12, "podcasts_auto": 1},
           "sources": [
    {"name": "wikipedia_en_top_mini", "type": "kiwix", "kiwix_name": "wikipedia_en_top_mini"},
    {"name": "meshtastic-docs", "type": "nightly-link", "repo": "NomDeTom/meshtasticDocs", "workflow": "docs-zim.yml", "pattern": "*.zim"},
    {"name": "mermaid-docs", "type": "release", "repo": "NomDeTom/mermaid", "pattern": "*.zim"},
    *({"name": f"extra-book-{i}", "type": "release", "repo": f"example/docs-{i}", "pattern": "*.zim"} for i in range(1, 12)),
]}
(state / "library" / "sources.json").write_text(json.dumps(sources, indent=2))
(state / "library" / "status.json").write_text(json.dumps({
    "wikipedia_en_top_mini": {"last_check": iso(now - 600), "outcome": "up to date", "current": {"version": "2026-10", "label": "October 2026", "size": 48_000_000, "installed": "2026-10-28"}, "latest": {"version": "2026-10", "label": "October 2026", "size": 48_000_000}},
    "meshtastic-docs": {"last_check": iso(now - 1200), "outcome": "newer version found", "current": {"version": "2026-09-29", "label": "29 September", "size": 59_500_000, "installed": "2026-10-01"}, "latest": {"version": "2026-11-04", "label": "4 November", "size": 61_000_000}},
    "mermaid-docs": {"last_check": iso(now - 1800), "outcome": "error: HTTP 404", "error": "HTTP 404: the release asset was not found"},
    **{f"extra-book-{i}": {"last_check": iso(now - 600 - i * 300),
        "outcome": ("error: HTTP 404" if i == 3 else "up to date"),
        **({"error": "HTTP 404: the release asset was not found"} if i == 3 else {}),
        "current": {"version": "2026-10", "label": "October 2026", "size": 2_000_000 + i, "installed": "2026-10-28"},
        "latest": {"version": "2026-10", "label": "October 2026", "size": 2_000_000 + i}}
        for i in range(1, 12)},
}, indent=2))
(state / "zim" / "library.xml").write_text("<library>" + "".join(
    f'<book id="{i}" path="{name}.zim" title="{title}" language="eng" date="2026-10" description=""/>'
    for i, (name, title) in enumerate([("wikipedia_en_top_mini", "Wikipedia (top mini)"), ("meshtastic-docs", "Meshtastic docs"), ("mermaid-docs", "Mermaid docs"), ("handbook", "A handbook copied in by hand")] + [(f"extra-book-{j}", f"Extra book {j}") for j in range(1, 12)], 1)
) + "</library>")
for name in ["wikipedia_en_top_mini", "meshtastic-docs", "mermaid-docs", "handbook"] + [f"extra-book-{i}" for i in range(1, 12)]:
    (state / "zim" / f"{name}.zim").write_bytes(b"ZIM sample")
(state / "library" / "firmware.json").write_text(json.dumps({
    "enabled": True, "boards": "all", "keep_alpha": 2, "keep_beta": 1, "cache": "discard", "configs": True,
    "proving": False, "proving_releases": "stable", "proving_families": "chosen", "proving_when": "idle",
    "proving_tooling": "flag", "proving_keep": 3, "proving_window_start": "22:00", "proving_window_end": "06:00",
}, indent=2))
(state / "firmware" / "status.json").write_text(json.dumps({
    "last_check": iso(now - 1500), "outcome": "3 releases, 900 MB",
    "versions": {"2.8.1": {"channel": "stable", "boards": ["rak4631", "t-deck"], "files": 12, "bytes": 300_000_000},
                 "2.8.0": {"channel": "stable", "boards": ["rak4631"], "files": 8, "bytes": 250_000_000},
                 "2.8.2": {"channel": "alpha", "boards": ["rak4631"], "files": 8, "bytes": 260_000_000}},
}, indent=2))

# Board images: one up to date, one with something newer, one whose check failed.
(state / "library" / "rk-images.json").write_text(json.dumps({
    "enabled": True, "mpwrd": True, "armbian": True,
    "armbian_boards": ["luckfox-lyra-plus", "luckfox-lyra-ultra-w", "luckfox-lyra-zero-w"],
    "check_every_hours": 24, "keep": 1,
}, indent=2))
(state / "rk-images" / "status.json").write_text(json.dumps({
    "last_check": iso(now - 2400), "outcome": "3 images kept; 2 newer", "checked": now - 2400,
}, indent=2))
for name, data in [("mPWRD-OS_1_Luckfox-lyra-zero-w_trixie_minimal.img.xz", b"mpwrd-zero"),
                   ("Armbian_2_Luckfox-lyra-plus_trixie_vendor_6.1_minimal.img.xz", b"armbian-plus"),
                   ("alpine-rescue_1_luckfox-lyra.img.xz", b"alpine-rescue")]:
    (state / "rk-images" / name).write_bytes(data)
    (state / "rk-images" / f"{name}.sha256").write_text(hashlib.sha256(data).hexdigest() + f"  {name}\n")
PY

cat <<EOF
Item 76 mockup: the real /admin, with sample data.
  Git                    http://127.0.0.1:$port/admin/#git
  Mirrors                http://127.0.0.1:$port/admin/#mirrors
  App store              http://127.0.0.1:$port/admin/#appstore
  Books (Kiwix)          http://127.0.0.1:$port/admin/#books
  Firmware               http://127.0.0.1:$port/admin/#firmware
  Board images           http://127.0.0.1:$port/admin/rockchip.html#rk-images-pane
State: $state (throwaway; removed when this stops)
Ctrl-C stops the hub.
EOF
trap 'rm -rf "$run_tmp"' EXIT INT TERM
HUB_STATE_DIR="$state" HUB_GIT_ROOT="$state/git" HUB_HOLDS_ROOT="$state/holds" \
PORT="$port" HUB_BIND=127.0.0.1 "$repo/irate-box" server
