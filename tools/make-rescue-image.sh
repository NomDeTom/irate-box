#!/bin/bash -p
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
#
# Make the card/USB image the NAND U-Boot boots into the Alpine rescue: an MBR, a FAT partition with the kernel, DTB,
# initramfs and extlinux.conf, a raw squashfs partition, and an empty ext4 data partition. The same layout the
# tested USB stick used. It needs root (losetup, mount) and refuses to overwrite an image that is not its own.
set -euo pipefail
kernel="" dtb="" initramfs="" squashfs="" out="$PWD/alpine-rescue.img"
size_mib=512 rootdev="/dev/sda2" dry=0
console="console=ttyS2,115200n8 console=tty1 console=ttyFIQ0,1500000 earlyprintk rootwait"
usage() {
	echo "usage: $0 --kernel FILE --dtb FILE --initramfs FILE --squashfs FILE [--out FILE] [--size-mib N] [--root DEV] [--console STRING] [--dry-run]"
	exit 2
}
while [ $# -gt 0 ]; do
	case "$1" in
	--kernel) kernel=${2:-}; shift 2 ;;
	--dtb) dtb=${2:-}; shift 2 ;;
	--initramfs) initramfs=${2:-}; shift 2 ;;
	--squashfs) squashfs=${2:-}; shift 2 ;;
	--out) out=${2:-}; shift 2 ;;
	--size-mib) size_mib=${2:-}; shift 2 ;;
	--root) rootdev=${2:-}; shift 2 ;;
	--console) console=${2:-}; shift 2 ;;
	--dry-run) dry=1; shift ;;
	*) usage ;;
	esac
done
[ -n "$kernel" ] && [ -n "$dtb" ] && [ -n "$initramfs" ] && [ -n "$squashfs" ] || usage
case "$out" in /|"") echo "make-rescue-image: refusing --out $out" >&2; exit 1 ;; esac
case "$size_mib" in ''|*[!0-9]*) echo "make-rescue-image: --size-mib must be a number" >&2; exit 1 ;; esac
[ "$size_mib" -ge 400 ] || { echo "make-rescue-image: --size-mib must be at least 400" >&2; exit 1; }
if [ "$dry" = 0 ]; then
	[ "$(id -u)" = 0 ] || { echo "make-rescue-image: run as root" >&2; exit 1; }
	for f in "$kernel" "$dtb" "$initramfs" "$squashfs"; do
		[ -r "$f" ] || { echo "make-rescue-image: no $f" >&2; exit 1; }
	done
	[ ! -e "$out" ] || { echo "make-rescue-image: $out exists; move it first" >&2; exit 1; }
	command -v sfdisk >/dev/null || { echo "make-rescue-image: sfdisk is not installed (util-linux)" >&2; exit 1; }
	command -v mkfs.vfat >/dev/null || { echo "make-rescue-image: mkfs.vfat is not installed (dosfstools)" >&2; exit 1; }
	command -v mkfs.ext4 >/dev/null || { echo "make-rescue-image: mkfs.ext4 is not installed (e2fsprogs)" >&2; exit 1; }
fi
dtb_name=$(basename "$dtb")
initramfs_name=$(basename "$initramfs")
# p1 at 1 MiB, 128 MiB; p2 256 MiB; p3 the rest (the image must be at least 400 MiB for it to exist).
first=2048
p1=$((128 * 1024 * 1024 / 512))
p2=$((256 * 1024 * 1024 / 512))
p2_start=$((first + p1))
p3_start=$((p2_start + p2))
last=$((size_mib * 1024 * 1024 / 512 - 1))
say() { echo "make-rescue-image: $*"; }
say "kernel $kernel; dtb $dtb_name; initramfs $initramfs_name; squashfs $squashfs"
say "image $out (${size_mib} MiB); rescue.root $rootdev"
if [ "$dry" = 1 ]; then
	printf '  truncate -s %sM %q\n' "$size_mib" "$out"
	printf '  sfdisk %q (p1 %s-%s FAT, p2 %s-%s squashfs, p3 %s-%s ext4)\n' "$out" "$first" "$((p2_start - 1))" "$p2_start" "$((p3_start - 1))" "$p3_start" "$last"
	printf '  losetup --find --show --partscan %q\n' "$out"
	echo "  mkfs.vfat -F 32 -n RESCUEBOOT <loop>p1"
	echo "  mount <loop>p1 <dir>; copy kernel as zImage, $dtb_name, $initramfs_name, extlinux.conf and extlinux/extlinux.conf"
	echo "  dd if=$squashfs of=<loop>p2 bs=4M conv=fsync"
	echo "  mkfs.ext4 -L rescue-data <loop>p3"
	echo "  umount <dir>; losetup -d <loop>"
	exit 0
fi
truncate -s "${size_mib}M" "$out"
sfdisk "$out" >/dev/null <<EOF
label: dos
unit: sectors
start=$first, size=$p1, type=c, bootable
start=$p2_start, size=$p2, type=83
start=$p3_start, size=$((last - p3_start + 1)), type=83
EOF
loop=$(losetup --find --show --partscan "$out")
mnt=$(mktemp -d)
cleanup() { umount "$mnt" 2>/dev/null || true; losetup -d "$loop" 2>/dev/null || true; rmdir "$mnt" 2>/dev/null || true; }
trap cleanup EXIT
mkfs.vfat -F 32 -n RESCUEBOOT "${loop}p1" >/dev/null
mount "${loop}p1" "$mnt"
install -m 644 "$kernel" "$mnt/zImage"
install -m 644 "$dtb" "$mnt/$dtb_name"
install -m 644 "$initramfs" "$mnt/$initramfs_name"
install -d "$mnt/extlinux"
conf="default rescue
label rescue
  kernel /zImage
  fdt /$dtb_name
  initrd /$initramfs_name
  append $console rescue.root=$rootdev
"
printf '%s' "$conf" >"$mnt/extlinux.conf"
printf '%s' "$conf" >"$mnt/extlinux/extlinux.conf"
sync
umount "$mnt"
if [ "$((p3_start))" -le "$last" ]; then mkfs.ext4 -q -L rescue-data "${loop}p3"; fi
sync
losetup -d "$loop"
trap - EXIT
rmdir "$mnt"
size=$(stat -c %s "$out")
{
	echo "image $out"
	echo "size $size"
	echo "kernel $kernel"
	echo "dtb $dtb"
	echo "initramfs $initramfs"
	echo "squashfs $squashfs"
	echo "rescue.root $rootdev"
	echo "console $console"
	echo "sha256 $(sha256sum "$out" | cut -d' ' -f1)"
} >"$out.BUILD.txt"
say "done; the build record is $out.BUILD.txt"
