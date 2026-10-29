# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom

# Alpine signing keys

These are the public keys from Alpine's `alpine-keys` package, version `2.5-r0`, taken from
`https://dl-cdn.alpinelinux.org/alpine/v3.22/main/x86_64/alpine-keys-2.5-r0.apk` (SHA-256
`1069fa68769607690e46b0d689f1ad9b5e346be2752ece313685b4f29ec70e25` — replace with the checked value in
`SHA256SUMS` beside the keys). `make-alpine-rescue.sh` passes this directory as `--keys-dir`; it never uses
`--allow-untrusted`.
