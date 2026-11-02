#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Build one representative target per PlatformIO family for a Meshtastic release tag, and publish the
# bundles as platformio-deps-<family>-<version>.zip: the name a proving run looks for. Run in a
# checkout of meshtastic/firmware at the tag, on a machine with the network and PlatformIO:
#
#   scripts/pio-deps-families.sh TAG OUT_DIR [PLATFORMIO_VERSION]
#
# A family whose representative target no longer exists is skipped with a warning: the next proving
# run still compares the tag with the cache, and says which target it wanted.
set -euo pipefail
tag=${1:?usage: pio-deps-families.sh TAG OUT_DIR [PLATFORMIO_VERSION]}
out=${2:?usage: pio-deps-families.sh TAG OUT_DIR [PLATFORMIO_VERSION]}
want_pio=${3:-6.1.19}
box=$(cd "$(dirname "$0")/.." && pwd)
version=${tag#v}
# family: representative target. The same defaults as hub/proving.py.
families=${FAMILIES:-"esp32:tbeam esp32s3:heltec-v3 esp32c3:esp32c3 nrf52840:rak4631 rp2040:picow native:native"}
mkdir -p "$out"
command -v pio >/dev/null || { echo "pio: install PlatformIO $want_pio first" >&2; exit 2; }
have=$(pio --version 2>/dev/null || true)
case "$have" in
	*"$want_pio"*) ;;
	*) echo "warning: wanted PlatformIO $want_pio, found: ${have:-none}" >&2 ;;
esac
for pair in $families; do
	family=${pair%%:*}
	env=${pair#*:}
	[[ "$family" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || { echo "not a plain family name: $family" >&2; exit 2; }
	[[ "$env" =~ ^[a-z0-9][a-z0-9_-]{0,63}$ ]] || { echo "not a plain environment name: $env" >&2; exit 2; }
	echo "== $family ($env) at $tag"
	if ! pio pkg install -e "$env" --no-save; then
		echo "warning: $env is not an environment in this release; skipping $family" >&2
		continue
	fi
	if [ ! -d ".pio/libdeps/$env" ]; then
		echo "warning: no .pio/libdeps/$env after the tools fetch; skipping $family" >&2
		continue
	fi
	zip="$out/platformio-deps-$family-$version.zip"
	"$box/irate-box" pio_bundle bundle "$env" . "$zip"
	"$box/irate-box" pio_bundle verify "$zip" >/dev/null
	printf '  %s\n' "$zip"
done
