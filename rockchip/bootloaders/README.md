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
/var/lib/hub/rk-bootloaders/nand-uboot/rescue.itb
/var/lib/hub/rk-bootloaders/nand-uboot/irate-nand.ubi
/var/lib/hub/rk-bootloaders/nand-uboot/SHA256SUMS
/var/lib/hub/rk-bootloaders/stock/idbloader.img
/var/lib/hub/rk-bootloaders/stock/u-boot.itb
/var/lib/hub/rk-bootloaders/stock/SHA256SUMS
```

`SHA256SUMS` is the usual two-column form (`sha256sum file …`). The modified bundle's hashes are in
the metadata: `idbloader.img` `0cee35dc…`, `u-boot.itb` `0b53b23e…`, `rescue.itb` `099d2b17…` and
`irate-nand.ubi` `01f0dca3…`. The original's must come from wherever its files came from. A bundle is
offered only when every file is there and matches its hash.

From the Pi's build folders:

```sh
sudo install -d -m 755 /var/lib/hub/rk-bootloaders/nand-uboot
sudo install -m 644 ~/nand-alpine/out/idbloader.img ~/nand-alpine/out/u-boot.itb \
  ~/nand-alpine/out/rescue.itb ~/nand-alpine/out/irate-nand.ubi \
  /var/lib/hub/rk-bootloaders/nand-uboot/
( cd /var/lib/hub/rk-bootloaders/nand-uboot && sudo sha256sum idbloader.img u-boot.itb rescue.itb irate-nand.ubi | sudo tee SHA256SUMS >/dev/null )
```

The original bootloader is the one Luckfox ships: take `idbloader.img` and `u-boot.itb` from the
vendor image or package, put them in `rk-bootloaders/stock/`, and write their hashes in `SHA256SUMS`.

## What installing it does

The page reads the onboard flash first, asks for a confirmation when it is not blank, optionally saves
a full copy (the **Save a full copy of the onboard flash first** box, on by default), erases the whole
flash, writes `idbloader.img` at 0 and 4 MiB, `u-boot.itb` at 8 MiB, `rescue.itb` at 12 MiB and
`irate-nand.ubi` at 32 MiB, reads each piece back, and resets the board. The board then boots its card
first, USB second, and the Alpine rescue (C) from the NAND third. **Any logs kept on the NAND are
erased.** There is no migration from an old whole-device UBI layout; the NAND is laid out again from
the boot area.
