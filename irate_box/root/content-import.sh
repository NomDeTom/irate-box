#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
# Brings a content export into a box that has irate-box already: run it as root from the export's
# irate-box/ folder (a stick, or the download unpacked):  sudo sh import.sh [--dry-run] [--yes]
# Git repositories are added (one already on the box is left as it is); this box's settings, or settings
# and data, are replaced by the export's after a question (--yes answers it); toolkits are imported,
# each package checked against Debian's signatures. Books: /admin → Library → Books → USB stick.
here="$(cd "$(dirname "$0")" && pwd)"
dry=0 yes=0
for a in "$@"; do
	case "$a" in
	--dry-run) dry=1 ;;
	--yes) yes=1 ;;
	*) echo "usage: sudo sh import.sh [--dry-run] [--yes]" >&2; exit 2 ;;
	esac
done
hub=${IRATE_BOX_CLI:-/opt/irate-box/irate-box} state=${HUB_STATE_DIR:-/var/lib/hub}
[ -x "$hub" ] && [ -d "$state" ] || { echo "irate-box is not installed here: install it first (or use an export that carries the hub program)" >&2; exit 1; }
[ "$dry" = 1 ] || [ "$(id -u)" = 0 ] || { echo "run it as root: sudo sh $0" >&2; exit 1; }
did=0
for repo in "$here"/git/public/*.git "$here"/git/private/*.git; do
	[ -d "$repo" ] || continue
	dest="$state/git/$(basename "$(dirname "$repo")")/$(basename "$repo")"
	if [ -e "$dest" ]; then echo "git: $dest is there already: left as it is"; continue; fi
	if [ "$dry" = 1 ]; then echo "git: would add $dest"; continue; fi
	cp -a "$repo" "$dest" && chown -R hub:hub "$dest" && echo "git: added $dest" && did=1
done
if [ -f "$here/state-backup.tar.gz" ]; then
	echo "state: the export holds:"
	tar -tzf "$here/state-backup.tar.gz" | grep -v '/$' | cut -d/ -f2 | grep -v -e '^$' -e '^BACKUP-CONTENTS.txt$' | sort -u | sed 's/^/    /'
	if [ "$dry" = 1 ]; then
		echo "state: would replace those in $state"
	else
		ok=$yes
		if [ "$ok" != 1 ]; then
			printf 'Replace them in %s with the export'"'"'s? [y/N] ' "$state"
			read -r answer
			case "$answer" in y | Y | yes) ok=1 ;; esac
		fi
		if [ "$ok" = 1 ]; then
			runuser -u hub -- tar -xzf "$here/state-backup.tar.gz" -C "$state" --strip-components=1 \
				--exclude=irate-box-state/BACKUP-CONTENTS.txt && echo "state: restored into $state" && did=1
		else
			echo "state: left as it is"
		fi
	fi
fi
if [ -d "$here/kits" ]; then
	if [ "$dry" = 1 ]; then
		echo "toolkits: would import $(cd "$here/kits" && ls -d */*/ 2>/dev/null | cut -d/ -f2 | sort -u | tr '\n' ' ')"
	else
		echo "toolkits (each package checked against Debian's signatures):"
		"$hub" kits import-dir "$here/kits" | sed 's/^/    /'
	fi
fi
ls "$here"/*.zim >/dev/null 2>&1 && echo "books: import them on /admin → Library → Books → USB stick (each is checked there)"
[ "$did" = 1 ] && systemctl restart irate-box 2>/dev/null
exit 0
