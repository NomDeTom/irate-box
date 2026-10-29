#!/bin/bash -p
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
#
# Build the Lyra's U-Boot from the pinned commit, with the card's environment fragment. It fetches, checks out,
# merges the fragment, builds, reads the fragment back from .config, and records the commit, toolchain and checksum.
# A dry run prints the commands and changes nothing.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
pin="$here/uboot/lyra-pin.txt" repo="" commit="" defconfig="" dts=""
fragment="$here/uboot/lyra-card.fragment" out="$PWD/uboot-out"
cross="arm-linux-gnueabihf-" jobs=$(nproc 2>/dev/null || echo 1) dry=0
usage() {
	echo "usage: $0 --repo URL --commit HASH --defconfig NAME [--pin FILE] [--dts PATH] [--fragment FILE] [--out DIR] [--cross PREFIX] [--jobs N] [--dry-run]"
	exit 2
}
while [ $# -gt 0 ]; do
	case "$1" in
	--repo) repo=${2:-}; shift 2 ;;
	--commit) commit=${2:-}; shift 2 ;;
	--defconfig) defconfig=${2:-}; shift 2 ;;
	--pin) pin=${2:-}; shift 2 ;;
	--dts) dts=${2:-}; shift 2 ;;
	--fragment) fragment=${2:-}; shift 2 ;;
	--out) out=${2:-}; shift 2 ;;
	--cross) cross=${2:-}; shift 2 ;;
	--jobs) jobs=${2:-}; shift 2 ;;
	--dry-run) dry=1; shift ;;
	*) usage ;;
	esac
done
if [ -r "$pin" ]; then
	while IFS='=' read -r key value; do
		case "$key" in
		repo) [ -n "$repo" ] || repo=$value ;;
		commit) [ -n "$commit" ] || commit=$value ;;
		defconfig) [ -n "$defconfig" ] || defconfig=$value ;;
		dts) [ -n "$dts" ] || dts=$value ;;
		esac
	done < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$pin")
fi
[ -n "$repo" ] && [ -n "$commit" ] && [ -n "$defconfig" ] || usage
[ -r "$fragment" ] || { echo "build-uboot: no fragment at $fragment" >&2; exit 1; }
case "$out" in /|"") echo "build-uboot: refusing --out $out" >&2; exit 1 ;; esac
if [ -d "$out" ] && [ -n "$(ls -A "$out" 2>/dev/null)" ] && [ ! -e "$out/BUILD.txt" ] && [ ! -d "$out/src" ]; then
	echo "build-uboot: refusing to clear a non-build folder: $out" >&2; exit 1
fi
src="$out/src" build="$out/build"
say() { echo "build-uboot: $*"; }
doit() {
	if [ "$dry" = 1 ]; then printf '  %s\n' "$*"; else "$@"; fi
}
if [ "$dry" = 0 ]; then
	command -v git >/dev/null || { echo "build-uboot: git is not installed" >&2; exit 1; }
	command -v make >/dev/null || { echo "build-uboot: make is not installed" >&2; exit 1; }
	command -v "${cross}gcc" >/dev/null || { echo "build-uboot: ${cross}gcc is not installed" >&2; exit 1; }
	command -v fdtget >/dev/null || { echo "build-uboot: fdtget is not installed (device-tree-compiler)" >&2; exit 1; }
fi
say "repository $repo at $commit"
say "defconfig $defconfig; fragment $fragment"
say "output $out; cross ${cross}gcc; jobs $jobs"
if [ "$dry" = 1 ]; then
	doit git clone "$repo" "$src"
	doit git -C "$src" checkout "$commit"
	doit git -C "$src" remote get-url origin
	doit cp "$here/uboot/irate-bootstate.dtsi" "$src/arch/arm/dts/irate-bootstate.dtsi"
	doit printf '#include "irate-bootstate.dtsi"\n' '>>' "$src/$dts"
	doit git -C "$src" rev-parse HEAD
	doit env SOURCE_DATE_EPOCH="<commit date>" make -C "$src" O="$build" "$defconfig"
	doit "$src/scripts/kconfig/merge_config.sh" -O "$build" "$build/.config" "$fragment"
	doit make -C "$src" O="$build" olddefconfig
	doit make -C "$src" O="$build" -j"$jobs" CROSS_COMPILE="$cross"
	doit cp "$build/u-boot-rockchip.bin" "$out/u-boot-rockchip.bin"
	doit fdtget "<built lyra dtb>" /config u-boot,mmc-env-partition
	doit sha256sum "$out/u-boot-rockchip.bin"
	exit 0
fi
mkdir -p "$out"
if [ ! -d "$src/.git" ]; then
	git clone "$repo" "$src"
fi
git -C "$src" fetch --tags origin
[ "$(git -C "$src" remote get-url origin)" = "$repo" ] || {
	echo "build-uboot: $src has another origin; use another --out" >&2
	exit 1
}
git -C "$src" checkout --detach "$commit"
[ -r "$src/$dts" ] || { echo "build-uboot: no device tree at $src/$dts" >&2; exit 1; }
cp "$here/uboot/irate-bootstate.dtsi" "$src/arch/arm/dts/irate-bootstate.dtsi"
grep -q 'irate-bootstate.dtsi' "$src/$dts" || printf '#include "irate-bootstate.dtsi"\n' >>"$src/$dts"
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
dtb=$(find "$build" -type f -name '*lyra*.dtb' | head -1)
[ -n "$dtb" ] || { echo "build-uboot: no Lyra DTB in the build" >&2; exit 1; }
[ "$(fdtget "$dtb" /config u-boot,mmc-env-partition)" = "irate-bootstate" ] || {
	echo "build-uboot: the DTB does not name the bootstate partition; refusing a build that would write LBA 0" >&2
	exit 1
}
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
