#!/bin/bash -p
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
#
# Make the minimal Alpine rescue root for armv7, measure it, and make its zstd squashfs. The Alpine release and the
# package versions are recorded; a rebuild is the same only when those versions are pinned (--versions) or the index
# is a snapshot. The packages' scripts run on the Pi or under qemu, not here.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
alpine="3.22" out="$PWD/alpine-rescue" root="" squash="" apk="apk.static"
packages="$here/alpine/packages.txt" versions="" repos="" kernel_modules="" aic8800="" dry=0
usage() {
	echo "usage: $0 [--alpine VERSION] [--out DIR] [--root DIR] [--squashfs FILE] [--apk PATH] [--packages FILE] [--versions FILE] [--repositories FILE] [--kernel-modules DIR] [--aic8800 KO] [--dry-run]"
	exit 2
}
while [ $# -gt 0 ]; do
	case "$1" in
	--alpine) alpine=${2:-}; shift 2 ;;
	--out) out=${2:-}; shift 2 ;;
	--root) root=${2:-}; shift 2 ;;
	--squashfs) squash=${2:-}; shift 2 ;;
	--apk) apk=${2:-}; shift 2 ;;
	--packages) packages=${2:-}; shift 2 ;;
	--versions) versions=${2:-}; shift 2 ;;
	--repositories) repos=${2:-}; shift 2 ;;
	--kernel-modules) kernel_modules=${2:-}; shift 2 ;;
	--aic8800) aic8800=${2:-}; shift 2 ;;
	--dry-run) dry=1; shift ;;
	*) usage ;;
	esac
done
[ -r "$packages" ] || { echo "make-alpine-rescue: no package list at $packages" >&2; exit 1; }
root=${root:-"$out/root"}
squash=${squash:-"$out/rescue.squashfs"}
doit() {
	if [ "$dry" = 1 ]; then printf '  %s\n' "$*"; else "$@"; fi
}
say() { echo "make-alpine-rescue: $*"; }
mapfile -t names < <(sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$packages")
((${#names[@]})) || { echo "make-alpine-rescue: the package list is empty" >&2; exit 1; }
if [ -n "$versions" ]; then
	[ -r "$versions" ] || { echo "make-alpine-rescue: no versions at $versions" >&2; exit 1; }
	while IFS= read -r line; do
		case "$line" in ""|\#*) continue ;; esac
		name=${line%%=*}
		for i in "${!names[@]}"; do
			[ "${names[i]}" = "$name" ] && names[i]=$line
		done
	done <"$versions"
fi
if [ "$dry" = 0 ]; then
	[ "$(id -u)" = 0 ] || { echo "make-alpine-rescue: run as root" >&2; exit 1; }
	command -v "$apk" >/dev/null || { echo "make-alpine-rescue: $apk is not installed" >&2; exit 1; }
	command -v mksquashfs >/dev/null || { echo "make-alpine-rescue: mksquashfs is not installed" >&2; exit 1; }
fi
if [ -n "$repos" ]; then
	[ -r "$repos" ] || { echo "make-alpine-rescue: no repositories file at $repos" >&2; exit 1; }
	repo_file=$repos
else
	repo_file="$out/repositories"
fi
say "Alpine $alpine, armv7, root $root, squashfs $squash"
say "packages: ${names[*]}"
if [ "$dry" = 1 ]; then
	[ -n "$repos" ] || printf '  write %s: https://dl-cdn.alpinelinux.org/alpine/v%s/{main,community}\n' "$repo_file" "$alpine"
	doit "$apk" --arch armv7 --root "$root" --initdb --no-scripts --repositories-file "$repo_file" add "${names[@]}"
	[ -z "$kernel_modules" ] || doit cp -a "$kernel_modules" "$root/lib/modules/"
	[ -z "$aic8800" ] || doit cp "$aic8800" "$root/lib/modules/"
	doit "$apk" --root "$root" --arch armv7 info -v
	doit du -sk "$root"
	doit mksquashfs "$root" "$squash" -comp zstd -noappend
	doit du -sk "$squash"
	exit 0
fi
mkdir -p "$out"
if [ -z "$repos" ]; then
	printf 'https://dl-cdn.alpinelinux.org/alpine/v%s/main\nhttps://dl-cdn.alpinelinux.org/alpine/v%s/community\n' "$alpine" "$alpine" >"$repo_file"
fi
rm -rf "$root"
mkdir -p "$root"
"$apk" --arch armv7 --root "$root" --initdb --no-scripts --repositories-file "$repo_file" add "${names[@]}"
if [ -n "$kernel_modules" ]; then
	mkdir -p "$root/lib/modules"
	cp -a "$kernel_modules" "$root/lib/modules/"
fi
if [ -n "$aic8800" ]; then
	mkdir -p "$root/lib/modules"
	cp "$aic8800" "$root/lib/modules/"
fi
"$apk" --root "$root" --arch armv7 info -v | sort >"$out/VERSIONS.new"
if [ -f "$out/VERSIONS.txt" ] && ! cmp -s "$out/VERSIONS.txt" "$out/VERSIONS.new"; then
	echo "make-alpine-rescue: the resolved versions differ from the last build:" >&2
	diff -u "$out/VERSIONS.txt" "$out/VERSIONS.new" >&2 || true
	echo "make-alpine-rescue: pin the versions (--versions) or use the same index snapshot, then build again." >&2
	exit 1
fi
mv "$out/VERSIONS.new" "$out/VERSIONS.txt"
rm -f "$squash"
mksquashfs "$root" "$squash" -comp zstd -noappend >"$out/mksquashfs.log"
{
	echo "alpine $alpine"
	echo "arch armv7"
	echo "root_kib $(du -sk "$root" | cut -f1)"
	echo "squashfs_kib $(du -sk "$squash" | cut -f1)"
	echo "packages ${names[*]}"
	echo "versions $out/VERSIONS.txt"
	echo "scripts: not run here (run them on the Pi or under qemu-user with binfmt)"
} | tee "$out/BUILD.txt"
say "done; the budget is in $out/BUILD.txt"
