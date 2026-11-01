<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->

# Bootloaders for a Lyra's onboard flash

The Rockchip boards page (`/admin/rockchip.html`) writes one of these to a board's SPI NAND. The
metadata (`bootloaders.json`) is shipped; the binaries are not, because they are Luckfox's or a local
build's.

## Where the files go

On the box, under the state folder (usually `/var/lib/hub`):

```text
/var/lib/hub/rk-bootloaders/nand-uboot/idbloader.img
/var/lib/hub/rk-bootloaders/nand-uboot/u-boot.itb
/var/lib/hub/rk-bootloaders/nand-uboot/SHA256SUMS
/var/lib/hub/rk-bootloaders/stock/idbloader.img
/var/lib/hub/rk-bootloaders/stock/u-boot.itb
/var/lib/hub/rk-bootloaders/stock/SHA256SUMS
```

`SHA256SUMS` is the usual two-column form (`sha256sum file …`). The modified U-Boot's two hashes are
in the metadata; the original's must come from wherever its files came from. A bundle is offered only
when every file is there and matches its hash.

From the Pi's build folders:

```sh
sudo install -d -m 755 /var/lib/hub/rk-bootloaders/nand-uboot
sudo install -m 644 ~/uboot-nand/build/idbloader.img ~/uboot-nand/build/u-boot.itb \
  /var/lib/hub/rk-bootloaders/nand-uboot/
( cd /var/lib/hub/rk-bootloaders/nand-uboot && sudo sha256sum idbloader.img u-boot.itb | sudo tee SHA256SUMS >/dev/null )
```

The original bootloader is the one Luckfox ships: take `idbloader.img` and `u-boot.itb` from the
vendor image or package, put them in `rk-bootloaders/stock/`, and write their hashes in `SHA256SUMS`.

## What installing it does

The page reads the onboard flash first, asks for a confirmation when it is not blank, erases the whole
flash, writes `idbloader.img` at 0 and 4 MiB and `u-boot.itb` at 8 MiB, reads each piece back, and
resets the board. **Any logs kept on the NAND are erased.** There is no migration from the old
whole-device UBI layout; the NAND is laid out again from the boot area.
