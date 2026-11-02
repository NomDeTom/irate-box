<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->

# The Alpine rescue card image

The Rockchip boards page offers the images named `alpine-rescue*.img` (or `.img.xz`) in
`/var/lib/hub/rk-images`, with their `.sha256` beside them. The same image can be written to a card in
a reader or to the board's SD card through its loader.

## Making one from the built parts

`tools/make-rescue-image.sh` builds the layout the tested USB stick used: an MBR, a 128 MiB FAT
partition with the kernel, DTB, initramfs and `extlinux.conf`, a 256 MiB raw squashfs partition, and
an ext4 data partition. It needs root, and the four parts from the Pi's `~/alpine-rescue/` and the
vendor kernel:

```sh
sudo tools/make-rescue-image.sh \
  --kernel     ~/alpine-rescue/kernel/boot/vmlinuz-6.1.115-vendor-rockchip \
  --dtb        ~/alpine-rescue/kernel/boot/rk3506b-luckfox-lyra-zero-w-sd.dtb \
  --initramfs  ~/alpine-rescue/initramfs-rescue7.gz \
  --squashfs   ~/alpine-rescue/alpine-rescue-final15.squashfs \
  --out        /var/lib/hub/rk-images/alpine-rescue.img \
  --root       /dev/mmcblk0p2
```

`--root` is the partition the rescue mounts (`mmcblk0p2` for a card in the board, `sda2` for a USB
stick). Make the `.sha256` beside the image from its `.BUILD.txt`:

```sh
sudo awk '/^sha256 / {print $2 "  alpine-rescue.img"}' \
  /var/lib/hub/rk-images/alpine-rescue.img.BUILD.txt \
  | sudo tee /var/lib/hub/rk-images/alpine-rescue.img.sha256 >/dev/null
```

## What the page does with it

The bootloader is written to the board's NAND first; then the image is written to the card in the
reader, checked against its sha256 and read back. A failure on the card leaves the bootloader
installed and says the rescue was not written.
