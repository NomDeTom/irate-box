# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom

# Alpine signing keys

These are the **armv7** public keys from Alpine's `alpine-keys` package, version `2.5-r0`, taken from
`https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/alpine-keys-2.5-r0.apk` (the package's SHA-256 is
`1069fa68769607690e46b0d689f1ad9b5e346be2752ece313685b4f29ec70e25`); the package maps armv7 to
`524d27bb` and `616adfeb`, and both are here. `make-alpine-rescue.sh` passes this directory as `--keys-dir`; it never
uses `--allow-untrusted`. The individual key hashes are in `SHA256SUMS`.
