#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Builds irate-box_<version>_all.deb from this checkout's HEAD into ./dist (or $1).
#
# The package carries the code (/usr/share/irate-box/src) and irate-box-setup, which runs
# install.sh from it. It does not set the box up by itself: install.sh asks apt for packages,
# downloads release binaries and can take minutes, none of which a package's scripts may do,
# so the package depends on what install.sh needs and says what to run. On a box already set
# up, `sudo irate-box-setup` with no options applies the new version with the box's options.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(git -C "$here" rev-parse --show-toplevel)"
out="$(realpath -m "${1:-$repo/dist}")"
sha="$(git -C "$repo" rev-parse --short=7 HEAD)"
when="$(git -C "$repo" log -1 --format=%cd --date=format:%Y%m%d%H%M HEAD)"
tag="$(git -C "$repo" describe --tags --abbrev=0 2>/dev/null || true)"
# A tag v1.2.3 is 1.2.3; without one, 0~<commit time>.g<commit> sorts in time order.
if [ -n "$tag" ] && [ "$(git -C "$repo" rev-list -n1 "$tag")" = "$(git -C "$repo" rev-parse HEAD)" ]; then
	version="${tag#v}"
else
	version="0~${when}.g${sha}"
fi
root="$(mktemp -d)"; trap 'rm -rf "$root"' EXIT
src="$root/usr/share/irate-box/src"
mkdir -p "$src" "$root/usr/sbin" "$root/usr/share/doc/irate-box" "$root/DEBIAN"
git -C "$repo" archive --format=tar HEAD | tar -x -C "$src"
printf '%s (deb %s)\n' "$sha" "$version" >"$src/VERSION"
install -m 755 "$here/irate-box-setup" "$root/usr/sbin/irate-box-setup"
install -m 644 "$here/copyright" "$root/usr/share/doc/irate-box/copyright"
for s in postinst postrm; do install -m 755 "$here/$s" "$root/DEBIAN/$s"; done
size="$(du -sk "$root" | cut -f1)"
sed -e "s/@VERSION@/$version/" -e "s/@SIZE@/$size/" "$here/control" >"$root/DEBIAN/control"
mkdir -p "$out"
deb="$out/irate-box_${version}_all.deb"
fakeroot dpkg-deb --build -Zxz "$root" "$deb" >/dev/null
echo "$deb"
