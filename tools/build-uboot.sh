#!/bin/bash -p
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
#
# Build the Lyra's U-Boot from the pinned commit, with the card's environment fragment. It fetches, checks out,
# merges the fragment, builds, reads the fragment back from .config, and records the commit, toolchain and checksum.
# A dry run prints the commands and changes nothing.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
repo="" commit="" defconfig="" fragment="$here/uboot/lyra-card.fragment" out="$PWD/uboot-out"
cross="arm-linux-gnueabihf-" jobs=$(nproc 2>/dev/null || echo 1) dry=0
usage() {
	echo "usage: $0 --repo URL --commit HASH --defconfig NAME [--fragment FILE] [--out DIR] [--cross PREFIX] [--jobs N] [--dry-run]"
	exit 2
}
while [ $# -gt 0 ]; do
	case "$1" in
	--repo) repo=${2:-}; shift 2 ;;
	--commit) commit=${2:-}; shift 2 ;;
	--defconfig) defconfig=${2:-}; shift 2 ;;
	--fragment) fragment=${2:-}; shift 2 ;;
	--out) out=${2:-}; shift 2 ;;
	--cross) cross=${2:-}; shift 2 ;;
	--jobs) jobs=${2:-}; shift 2 ;;
	--dry-run) dry=1; shift ;;
	*) usage ;;
	esac
done
[ -n "$repo" ] && [ -n "$commit" ] && [ -n "$defconfig" ] || usage
[ -r "$fragment" ] || { echo "build-uboot: no fragment at $fragment" >&2; exit 1; }
src="$out/src" build="$out/build"
say() { echo "build-uboot: $*"; }
doit() {
	if [ "$dry" = 1 ]; then printf '  %s\n' "$*"; else "$@"; fi
}
if [ "$dry" = 0 ]; then
	command -v git >/dev/null || { echo "build-uboot: git is not installed" >&2; exit 1; }
	command -v make >/dev/null || { echo "build-uboot: make is not installed" >&2; exit 1; }
	command -v "${cross}gcc" >/dev/null || { echo "build-uboot: ${cross}gcc is not installed" >&2; exit 1; }
fi
say "repository $repo at $commit"
say "defconfig $defconfig; fragment $fragment"
say "output $out; cross ${cross}gcc; jobs $jobs"
if [ "$dry" = 1 ]; then
	doit git clone "$repo" "$src"
	doit git -C "$src" checkout "$commit"
	doit git -C "$src" rev-parse HEAD
	doit env SOURCE_DATE_EPOCH="<commit date>" make -C "$src" O="$build" "$defconfig"
	doit "$src/scripts/kconfig/merge_config.sh" -O "$build" "$build/.config" "$fragment"
	doit make -C "$src" O="$build" olddefconfig
	doit make -C "$src" O="$build" -j"$jobs" CROSS_COMPILE="$cross"
	doit cp "$build/u-boot-rockchip.bin" "$out/u-boot-rockchip.bin"
	doit sha256sum "$out/u-boot-rockchip.bin"
	exit 0
fi
mkdir -p "$out"
if [ ! -d "$src/.git" ]; then
	git clone "$repo" "$src"
fi
git -C "$src" fetch --tags origin
git -C "$src" checkout --detach "$commit"
epoch=$(git -C "$src" show -s --format=%ct HEAD)
stamp=$(date -u -d "@$epoch" '+%Y-%m-%dT%H:%M:%SZ')
export SOURCE_DATE_EPOCH="$epoch" KBUILD_BUILD_TIMESTAMP="$stamp"
rm -rf "$build"
mkdir -p "$build"
make -C "$src" O="$build" "$defconfig"
"$src/scripts/kconfig/merge_config.sh" -O "$build" "$build/.config" "$fragment"
make -C "$src" O="$build" olddefconfig
make -C "$src" O="$build" -j"$jobs" CROSS_COMPILE="$cross"
cp "$build/u-boot-rockchip.bin" "$out/u-boot-rockchip.bin"
while IFS= read -r line; do
	case "$line" in ""|\#*) continue ;; esac
	name=${line%%=*}
	if [ "${line##*=}" = "n" ]; then
		grep -qx "# $name is not set" "$build/.config" || { echo "build-uboot: $name is not off in .config" >&2; exit 1; }
	else
		grep -qx "$line" "$build/.config" || { echo "build-uboot: $line is not in .config" >&2; exit 1; }
	fi
done <"$fragment"
{
	echo "commit $(git -C "$src" rev-parse HEAD)"
	echo "source_date_epoch $epoch"
	echo "toolchain $("${cross}gcc" --version | head -1)"
	echo "sha256 $(sha256sum "$out/u-boot-rockchip.bin" | cut -d' ' -f1)"
} | tee "$out/BUILD.txt"
