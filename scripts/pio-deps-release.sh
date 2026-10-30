#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Build a per-family PlatformIO dependency bundle for a Meshtastic release tag. Run in a checkout
# of meshtastic/firmware at the tag, on a machine with the network and PlatformIO:
#
#   scripts/pio-deps-release.sh TAG "heltec-v3,rak4631" [PLATFORMIO_VERSION] [OUT_DIR]
#
# The tools are installed for each environment, its .pio/libdeps must appear, and
# irate_box.hub.pio_bundle packs and verifies platformio-deps-<env>-<version>.zip, the asset
# name the firmware mirror already looks for. The workflow .github/workflows/pio-deps.yml runs
# the same steps in CI.
set -euo pipefail
tag=${1:?usage: pio-deps-release.sh TAG "ENV,ENV" [PLATFORMIO_VERSION] [OUT_DIR]}
envs=${2:?usage: pio-deps-release.sh TAG "ENV,ENV" [PLATFORMIO_VERSION] [OUT_DIR]}
pio_version=${3:-6.1.19}
out=${4:-pio-deps}
box=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$out"
command -v pio >/dev/null || { echo "pio: install PlatformIO $pio_version first" >&2; exit 2; }
have=$(pio --version 2>/dev/null || true)
case "$have" in
	*"$pio_version"*) ;;
	*) echo "warning: wanted PlatformIO $pio_version, found: ${have:-none}" >&2 ;;
esac
IFS=',' read -r -a list <<<"$envs"
for raw in "${list[@]}"; do
	env=$(printf '%s' "$raw" | tr -d '[:space:]')
	[ -n "$env" ] || continue
	echo "== $env ($tag)"
	pio pkg install -e "$env" --no-save
	[ -d ".pio/libdeps/$env" ] || {
		echo "no .pio/libdeps/$env after the tools fetch: this environment's libraries were not installed" >&2
		exit 1
	}
	version=${tag#v}
	zip="$out/platformio-deps-$env-$version.zip"
	"$box/irate-box" pio_bundle bundle "$env" . "$zip"
	"$box/irate-box" pio_bundle verify "$zip" >/dev/null
	echo "   $zip"
done
