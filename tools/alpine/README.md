<!-- SPDX-License-Identifier: MIT -->
<!-- SPDX-FileCopyrightText: 2026 NomDeTom -->

# The Alpine rescue root

`tools/make-alpine-rescue.sh` makes an armv7 root with Alpine's `apk.static`, then measures it and makes a zstd
squashfs. Nothing here boots it.

**Sources and checks.** `apk.static` comes from Alpine's `apk-tools-static` package for the host architecture. Pass
its path with `--apk`, and its SHA-256 with `--apk-sha256`: the script refuses a mismatch. The signing keys in
`keys/` are from `alpine-keys` 2.5-r0 (the package's SHA-256 is in `keys/README.md`), and are passed with
`--keys-dir`; `--allow-untrusted` is never used. The individual key hashes are in `keys/SHA256SUMS`.

**Not configured.** `--no-scripts` leaves the packages' own setup for later, so the root has no BusyBox applet links
and the default output is named `rescue-unconfigured.squashfs`. Run the scripts on the Pi (armv7 natively) or under
qemu-user with binfmt before booting it.

**The kernel and the driver.** `--kernel-modules` copies the vendor's module tree; `--aic8800` with
`--kernel-version` puts the prebuilt driver under `lib/modules/<version>/extra/` and runs `depmod -b`.

**Pinned rebuilds.** `--versions` takes `name=version` lines. `VERSIONS.txt` is written in that same form, so the
resolved versions of one build can be fed to the next; a rebuild that resolves differently is refused.
