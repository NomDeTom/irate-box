#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
#
# Logs onto a board's onboard raw NAND flash instead of its SD card. Raw NAND (SLC, with UBI's wear
# levelling under UBIFS) takes far more writes than an SD card, and an unused one is otherwise idle.
#
#   sudo tools/nand-logs.sh check            what flash there is, whether it is free, what would move
#   sudo tools/nand-logs.sh on [--mtd N]     format it (UBI, one UBIFS volume), mount it at /var/log.nand
#                                            at every boot, and move the logs there
#   sudo tools/nand-logs.sh off              the logs back to the card; the flash left formatted
#   sudo tools/nand-logs.sh reflush          on a box set up before it: the timer that keeps the journal on the flash
#   sudo tools/nand-logs.sh state plan|on|off  the box's often-written state onto the flash too (the logs on first):
#                                            the hub's own (all of /var/lib/hub but its large folders, bound back
#                                            from the card), and the system's (NetworkManager, systemd, chrony,
#                                            logrotate, /var/tmp); the card is then written when content is added
#                                            or the box updates. plan says what would move, and changes nothing
#
# What moves, each only if the flash has room for it (smaller chips take fewer):
#   armbian-ramlog        on Armbian, its copy of /var/log on disk (HDD_LOG, its own setting), synced daily and
#                         at shutdown, with the persistent journal it keeps there and links /var/log/journal to
#   the journal           elsewhere, or where the flash is too small for ramlog's copy: the journal's folder
#                         bind-mounted from the flash; journald's size capped to a share of the flash either way
#   crash watch           /var/lib/irate-box-root/crashwatch, bind-mounted (safeio refuses symlinks)
#
# A flash is used only when nothing else does: not attached to UBI, not mounted, not named on the kernel's
# command line, and its first two erase blocks blank. A board that boots from the flash has its loader
# there; --force overrides that last test, for a flash known to hold nothing the board needs.
set -euo pipefail

MNT=/var/log.nand
UBI_DEV=9 # a fixed number, so the mount can name ubi9:logs before anything else attaches
ATTACH_UNIT=/etc/systemd/system/nand-logs-attach.service
FSTAB_TAG="# nand-logs"
RAMLOG_DEFAULT=/etc/default/armbian-ramlog
JOURNALD_DROPIN=/etc/systemd/journald.conf.d/nand-logs.conf
FLUSH_DROPIN=/etc/systemd/system/systemd-journal-flush.service.d/nand-logs.conf
RAMLOG_DROPIN=/etc/systemd/system/armbian-ramlog.service.d/nand-logs.conf
# armbian-ramlog hands the journal back to RAM after each of its syncs (journalctl --relinquish-var, by design for
# a card): every 10 minutes the journal goes back to the flash, which is what it is there for.
REFLUSH_UNIT=/etc/systemd/system/nand-logs-flush.service
REFLUSH_TIMER=/etc/systemd/system/nand-logs-flush.timer
CRASHWATCH=/var/lib/irate-box-root/crashwatch
STATE=${NAND_STATE_FILE:-/etc/nand-logs.conf} # what "on" changed, for "off"
FSTAB=${NAND_FSTAB:-/etc/fstab}
# The box's often-written state ("state on"): the hub's folder, whose large folders stay on the card (content the
# owner adds: books, firmware, kits, git, the library, uploads, builds, and any other folder past BIG_KIB), bound
# back into it from the card's own copy, exposed at HUB_CARD before the flash's covers it; and the system's.
HUB=${NAND_HUB:-/var/lib/hub}
HUB_CARD=${NAND_HUB_CARD:-/var/lib/hub.card}
HUB_LARGE=(zim firmware kits git library source drop ci factory-targets)
BIG_KIB=8192
SYS_STATE=(/var/lib/NetworkManager /var/lib/systemd /var/lib/chrony /var/lib/logrotate /var/tmp)
STATE_TAG="# nand-state"
STATE_DROPIN=nand-state.conf

say() { echo "nand-logs: $*"; }
die() {
	echo "nand-logs: $*" >&2
	exit 1
}
mib() { echo $(($1 / 1048576)); }

# Each raw NAND flash: "N size erasesize name". NAND_LOGS_TYPES widens it for a test (a block2mtd device is "ram").
flashes() {
	for d in /sys/class/mtd/mtd*; do
		[[ $d =~ /mtd[0-9]+$ ]] || continue
		[[ " ${NAND_LOGS_TYPES:-nand mlc-nand} " == *" $(cat "$d/type") "* ]] || continue
		echo "${d##*/mtd} $(cat "$d/size") $(cat "$d/erasesize") $(cat "$d/name")"
	done
}

# Why flash N can't be used, or nothing when it can.
in_use() {
	local n=$1 force=${2:-} erase
	for u in /sys/class/ubi/ubi*; do
		[[ -e $u/mtd_num ]] && [[ $(cat "$u/mtd_num") == "$n" ]] && [[ ${u##*/ubi} != "$UBI_DEV" ]] && {
			echo "attached to UBI as ${u##*/}"
			return
		}
	done
	grep -qE "^/dev/mtdblock$n " /proc/mounts && {
		echo "mounted as /dev/mtdblock$n"
		return
	}
	grep -qE "(ubi\.mtd=($n|$(cat /sys/class/mtd/mtd"$n"/name))\b|mtdblock$n\b)" /proc/cmdline && {
		echo "named on the kernel's command line"
		return
	}
	if [[ -z $force ]] && ! ours "$n"; then
		erase=$(cat /sys/class/mtd/mtd"$n"/erasesize)
		if (($(dd if=/dev/mtd"$n"ro bs="$erase" count=2 status=none | tr -d '\377' | wc -c) > 0)); then
			echo "its first two erase blocks hold data (a boot loader?)"
			return
		fi
	fi
}

ours() { [[ -e /sys/class/ubi/ubi$UBI_DEV/mtd_num ]] && [[ $(cat /sys/class/ubi/ubi$UBI_DEV/mtd_num) == "$1" ]]; }
ramlog() { [[ -f $RAMLOG_DEFAULT ]] && grep -q '^ENABLED=true' "$RAMLOG_DEFAULT"; }
journal_dir() { readlink -f /var/log/journal 2>/dev/null || echo /var/log/journal; }

# The parts that fit, from the volume's usable bytes, and the journal's cap last: "ramlog crashwatch 60M".
# ramlog's copy holds up to its RAM disk's size (50 MB by default) beside the journal.
plan() {
	local usable=$1 parts cap
	cap=$((usable * 30 / 100))
	((cap > 256 * 1048576)) && cap=$((256 * 1048576))
	if ramlog && ((usable >= 120 * 1048576)); then parts="ramlog"; else parts="journal"; fi
	((usable >= 48 * 1048576)) && parts="$parts crashwatch"
	echo "$parts $(mib "$cap")M"
}

check() {
	local found=0
	while read -r n size erase name; do
		found=1
		local why
		why=$(in_use "$n")
		echo "mtd$n \"$name\": $(mib "$size") MiB raw NAND, erase block $((erase / 1024)) KiB"
		if ours "$n"; then
			echo "  in use by nand-logs: ubi$UBI_DEV, mounted at $MNT: $(df -h --output=used,size "$MNT" 2>/dev/null | tail -1)"
		elif [[ -n $why ]]; then
			echo "  not free: $why"
		else
			read -r -a p <<<"$(plan $((size * 80 / 100)))"
			echo "  free. On it (about $(mib $((size * 80 / 100))) MiB usable): ${p[*]:0:${#p[@]}-1}; the journal capped at ${p[-1]}"
		fi
	done < <(flashes)
	((found)) || echo "no raw NAND flash on this board (/proc/mtd lists none)"
	echo "now: the journal in $(journal_dir) ($(du -sh "$(journal_dir)" 2>/dev/null | cut -f1)), crash watch $(du -sh "$CRASHWATCH" 2>/dev/null | cut -f1)$(ramlog && echo ", armbian-ramlog's copy in $(ramlog_hdd)")"
}

ramlog_hdd() {
	local h
	h=$(sed -n 's/^HDD_LOG=//p' "$RAMLOG_DEFAULT" 2>/dev/null | tail -1)
	echo "${h:-/var/log.hdd}"
}

need_tools() {
	command -v ubiformat >/dev/null && return
	say "installing mtd-utils"
	apt-get install -y mtd-utils >/dev/null || die "mtd-utils is needed (apt-get install mtd-utils)"
}

attach_and_mount() {
	local n=$1
	cat >"$ATTACH_UNIT" <<EOF
[Unit]
Description=Onboard NAND flash for logs: attach mtd$n to UBI as ubi$UBI_DEV (tools/nand-logs.sh)
DefaultDependencies=no
Before=local-fs-pre.target
Wants=local-fs-pre.target
ConditionPathExists=/dev/mtd$n

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=-/usr/sbin/modprobe -q ubi
ExecStartPre=-/usr/sbin/modprobe -q ubifs
ExecStart=/usr/sbin/ubiattach -m $n -d $UBI_DEV
ExecStop=-/usr/sbin/ubidetach -d $UBI_DEV

[Install]
WantedBy=local-fs-pre.target
EOF
	systemctl daemon-reload
	systemctl enable -q nand-logs-attach.service
	if ! systemctl start nand-logs-attach.service || [[ ! -e /sys/class/ubi/ubi$UBI_DEV/volumes_count ]]; then
		undo_attach
		die "ubiattach failed: journalctl -u nand-logs-attach"
	fi
	if ! ubinfo -d $UBI_DEV -N logs >/dev/null 2>&1; then
		ubimkvol /dev/ubi$UBI_DEV -N logs -m >/dev/null
	fi
	mkdir -p "$MNT"
	add_fstab "ubi$UBI_DEV:logs $MNT ubifs noatime,nofail,x-systemd.requires=nand-logs-attach.service 0 0"
	systemctl daemon-reload
	if ! mountpoint -q "$MNT" && ! mount "$MNT"; then
		undo_attach
		die "the UBIFS volume would not mount"
	fi
}

# Nothing moved yet: the mount, the unit and the state taken away again.
undo_attach() {
	sed -i "/$FSTAB_TAG\$/d" "$FSTAB"
	systemctl disable -q --now nand-logs-attach.service 2>/dev/null || true
	rm -f "$ATTACH_UNIT" "$STATE"
	systemctl daemon-reload
}

# A folder's contents into another: owners, modes and times kept, ACLs and other extended attributes not
# (UBIFS has none, and a journal folder often carries them).
copy_tree() {
	mkdir -p "$2"
	tar -C "$1" -cf - . | tar -C "$2" -xpf -
}

# Whatever happens, journald and crash watch running again at the end.
restart_on_exit() { trap 'journalctl --flush 2>/dev/null; systemctl start irate-box-crashwatch.service 2>/dev/null || true' EXIT; }

# journald off the disk while its files move: it keeps logging to /run, and --flush brings it back. (Stopping
# it doesn't hold: its sockets start it again.)
journal_away() { journalctl --relinquish-var; }

add_fstab() { grep -qxF "$1 $FSTAB_TAG" "$FSTAB" || echo "$1 $FSTAB_TAG" >>"$FSTAB"; }

# A folder's contents copied onto the flash, then the folder bind-mounted from there at every boot.
bind_over() {
	local from=$1 to=$2
	mkdir -p "$to"
	chmod --reference="$from" "$to"
	chown --reference="$from" "$to"
	copy_tree "$from" "$to"
	add_fstab "$to $from none bind,x-systemd.requires-mounts-for=$MNT 0 0"
	mountpoint -q "$from" || mount "$from"
}

# The journal's own folder (where /var/log/journal leads) bind-mounted from the flash.
move_journal() {
	local cap=$1 dir
	dir=$(journal_dir)
	mkdir -p "$dir"
	echo "journal_dir=$dir" >>"$STATE"
	journal_away
	bind_over "$dir" "$MNT/journal"
	journal_settings "$cap" "$dir"
}

journal_settings() {
	local cap=$1 dir=$2
	mkdir -p "${JOURNALD_DROPIN%/*}" "${FLUSH_DROPIN%/*}"
	printf '[Journal]\nStorage=persistent\nSystemMaxUse=%s\n' "$cap" >"$JOURNALD_DROPIN"
	printf '[Unit]\nRequiresMountsFor=%s %s\n' "$MNT" "$dir" >"$FLUSH_DROPIN"
	systemctl daemon-reload
	systemctl restart systemd-journald
	journalctl --flush
}

# armbian-ramlog's copy of /var/log, its journal with it, kept on the flash (HDD_LOG).
move_ramlog() {
	local cap=$1 old
	old=$(ramlog_hdd)
	echo "ramlog_hdd=$(sed -n 's/^HDD_LOG=//p' "$RAMLOG_DEFAULT" | tail -1)" >>"$STATE"
	echo "ramlog_old=$old" >>"$STATE"
	journal_away # the journal's files move
	mkdir -p "$MNT/ramlog"
	copy_tree "$old" "$MNT/ramlog"
	sed -i '/^HDD_LOG=/d' "$RAMLOG_DEFAULT"
	echo "HDD_LOG=$MNT/ramlog $FSTAB_TAG" >>"$RAMLOG_DEFAULT"
	mkdir -p "${RAMLOG_DROPIN%/*}" "$MNT/ramlog/journal"
	printf '[Unit]\nRequiresMountsFor=%s\n' "$MNT" >"$RAMLOG_DROPIN"
	# As armbian-ramlog links it at boot: /var/log/journal to the journal beside its copy.
	[[ -L /var/log/journal || ! -e /var/log/journal ]] && ln -sfn "$MNT/ramlog/journal" /var/log/journal
	journal_settings "$cap" "$MNT/ramlog/journal"
	reflush_on
}

reflush_on() {
	printf '[Unit]\nDescription=The journal back onto the onboard flash after armbian-ramlog relinquished it (tools/nand-logs.sh)\nRequiresMountsFor=%s\n\n[Service]\nType=oneshot\nExecStart=/usr/bin/journalctl --flush\n' "$MNT" >"$REFLUSH_UNIT"
	printf '[Unit]\nDescription=The journal back onto the onboard flash every 10 minutes (tools/nand-logs.sh)\n\n[Timer]\nOnBootSec=2min\nOnUnitActiveSec=10min\n\n[Install]\nWantedBy=timers.target\n' >"$REFLUSH_TIMER"
	systemctl daemon-reload
	systemctl enable -q --now nand-logs-flush.timer
}

on() {
	local n="" force=""
	while (($#)); do
		case "$1" in
		--mtd) n=$2 && shift ;;
		--force) force=1 ;;
		*) die "unknown option $1" ;;
		esac
		shift
	done
	[[ -e $STATE ]] && die "already on ($STATE); 'off' first to start again"
	restart_on_exit
	if [[ -z $n ]]; then
		n=$(flashes | while read -r m _; do [[ -z $(in_use "$m" "$force") ]] && echo "$m" && break; done)
		[[ -n $n ]] || die "no free raw NAND flash (see: $0 check)"
	fi
	local why
	why=$(in_use "$n" "$force")
	[[ -z $why ]] || die "mtd$n is not free: $why"
	need_tools
	say "mtd$n: formatting for UBI (wear counts kept where the flash has them)"
	ours "$n" || ubiformat /dev/mtd"$n" -y -q
	: >"$STATE"
	echo "mtd=$n" >>"$STATE"
	attach_and_mount "$n"
	local usable
	usable=$(($(df -B1 --output=avail "$MNT" | tail -1)))
	read -r -a p <<<"$(plan "$usable")"
	local cap=${p[-1]}
	for part in "${p[@]:0:${#p[@]}-1}"; do
		case $part in
		journal)
			say "the journal (capped at $cap)"
			move_journal "$cap"
			;;
		ramlog)
			say "armbian-ramlog's copy of /var/log, and the journal in it (capped at $cap)"
			move_ramlog "$cap"
			;;
		crashwatch)
			[[ -d $CRASHWATCH ]] || continue
			say "crash watch's records"
			echo "crashwatch=1" >>"$STATE"
			systemctl stop irate-box-crashwatch.service 2>/dev/null || true
			bind_over "$CRASHWATCH" "$MNT/crashwatch"
			systemctl start irate-box-crashwatch.service 2>/dev/null || true
			;;
		esac
	done
	say "done: $(df -h --output=used,size "$MNT" | tail -1 | xargs) on $MNT. Undo: $0 off"
}

# ---- The box's often-written state on the flash ("state") ----

# The hub's entries that stay on the card: the named large ones, and any other folder past BIG_KIB.
hub_large() {
	local e kib
	for e in "$HUB"/*; do
		[[ -e $e ]] || continue
		e=${e##*/}
		if [[ " ${HUB_LARGE[*]} " == *" $e "* ]]; then
			echo "$e"
		elif [[ -d $HUB/$e ]]; then
			kib=$(du -sxk "$HUB/$e" 2>/dev/null | cut -f1)
			((${kib:-0} > BIG_KIB)) && echo "$e"
		fi
	done
}

# The fstab lines "state on" adds, in the order systemd must mount them: the card's hub exposed, the flash's over
# it, the large folders bound back from the card's; then the system's folders.
state_lines() {
	local d
	echo "$HUB $HUB_CARD none bind 0 0"
	echo "$MNT/hub $HUB none bind,nofail,x-systemd.requires-mounts-for=$MNT,x-systemd.after=$(systemd-escape -p --suffix=mount "$HUB_CARD") 0 0"
	for d in "$@"; do
		echo "$HUB_CARD/$d $HUB/$d none bind,nofail,x-systemd.requires-mounts-for=$HUB,x-systemd.requires-mounts-for=$HUB_CARD 0 0"
	done
	for d in "${SYS_STATE[@]}"; do
		[[ -d $d ]] && echo "$MNT/sys${d//\//_} $d none bind,nofail,x-systemd.requires-mounts-for=$MNT 0 0"
	done
}

# What would move, its size against the flash's room, what stays on the card, and the lines: nothing changed.
state_plan() {
	local large small=0 kib e
	read -r -a large <<<"$(hub_large | xargs)"
	for e in "$HUB"/* "$HUB"/.[!.]*; do
		[[ -e $e ]] || continue
		[[ " ${large[*]} " == *" ${e##*/} "* ]] && continue
		kib=$(du -sxk "$e" 2>/dev/null | cut -f1)
		small=$((small + ${kib:-0}))
	done
	for e in "${SYS_STATE[@]}"; do
		[[ -d $e ]] || continue
		kib=$(du -sxk "$e" 2>/dev/null | cut -f1)
		small=$((small + ${kib:-0}))
	done
	echo "to the flash: $((small / 1024)) MiB (the hub's state, and ${SYS_STATE[*]})"
	echo "stays on the card: ${large[*]:-nothing}"
	if mountpoint -q "$MNT" 2>/dev/null; then
		echo "the flash has $(($(df -k --output=avail "$MNT" | tail -1) / 1024)) MiB free"
	fi
	echo "fstab:"
	state_lines "${large[@]}" | sed 's/^/  /'
}

# The hub's units, which must wait for its folder (the flash's mount is nofail: a fault there leaves the box
# booting, the hub not starting on an empty folder).
hub_units() { systemctl list-unit-files --no-legend 'irate-box*.service' 2>/dev/null | awk '{print $1}' | grep -v '@\.service$'; }

state_on() {
	[[ -e $STATE ]] || die "the logs are not on the flash: '$0 on' first"
	grep -q '^state=1' "$STATE" && die "the state is on the flash already"
	mountpoint -q "$MNT" || die "$MNT is not mounted"
	local large u d avail need
	read -r -a large <<<"$(hub_large | xargs)"
	need=$(state_plan | sed -n 's/^to the flash: \([0-9]*\) MiB.*/\1/p')
	avail=$(($(df -k --output=avail "$MNT" | tail -1) / 1024))
	((need + 32 < avail)) || die "the state ($need MiB) does not fit with room to spare ($avail MiB free)"
	mapfile -t units < <(hub_units)
	say "stopping the hub (${#units[@]} units) while its state moves"
	for u in "${units[@]}"; do [[ $u == irate-box-control.service ]] || systemctl stop "$u" 2>/dev/null || true; done
	trap 'for u in "${units[@]}"; do systemctl start "$u" 2>/dev/null || true; done' EXIT
	mkdir -p "$MNT/hub" "$HUB_CARD"
	chmod --reference="$HUB" "$MNT/hub"
	chown --reference="$HUB" "$MNT/hub"
	local skip=()
	for d in "${large[@]}"; do skip+=("--exclude=./$d"); done
	tar -C "$HUB" "${skip[@]}" -cf - . | tar -C "$MNT/hub" -xpf -
	for d in "${large[@]}"; do
		mkdir -p "$MNT/hub/$d"
		chmod --reference="$HUB/$d" "$MNT/hub/$d"
		chown --reference="$HUB/$d" "$MNT/hub/$d"
	done
	for d in "${SYS_STATE[@]}"; do
		[[ -d $d ]] || continue
		mkdir -p "$MNT/sys${d//\//_}"
		chmod --reference="$d" "$MNT/sys${d//\//_}"
		chown --reference="$d" "$MNT/sys${d//\//_}"
		copy_tree "$d" "$MNT/sys${d//\//_}"
	done
	while read -r line; do
		grep -qxF "$line $STATE_TAG" "$FSTAB" || echo "$line $STATE_TAG" >>"$FSTAB"
	done < <(state_lines "${large[@]}")
	for u in "${units[@]}"; do
		mkdir -p "/etc/systemd/system/$u.d"
		printf '[Unit]\nRequiresMountsFor=%s\n' "$HUB" >"/etc/systemd/system/$u.d/$STATE_DROPIN"
	done
	systemctl daemon-reload
	# Mounted now in the order of the lines (systemd mounts them so at the next boot).
	mount --bind "$HUB" "$HUB_CARD"
	mount --bind "$MNT/hub" "$HUB"
	for d in "${large[@]}"; do mount --bind "$HUB_CARD/$d" "$HUB/$d"; done
	for d in "${SYS_STATE[@]}"; do [[ -d $d ]] && mount --bind "$MNT/sys${d//\//_}" "$d"; done
	echo "state=1" >>"$STATE"
	echo "state_large=${large[*]}" >>"$STATE"
	say "done: the hub's state and the system's on the flash; on the card: ${large[*]}. Undo: $0 state off"
}

state_off() {
	grep -q '^state=1' "$STATE" 2>/dev/null || die "the state is not on the flash"
	local large u d
	read -r -a large <<<"$(sed -n 's/^state_large=//p' "$STATE")"
	mapfile -t units < <(hub_units)
	say "stopping the hub while its state moves back"
	for u in "${units[@]}"; do [[ $u == irate-box-control.service ]] || systemctl stop "$u" 2>/dev/null || true; done
	trap 'for u in "${units[@]}"; do systemctl start "$u" 2>/dev/null || true; done' EXIT
	for d in "${SYS_STATE[@]}"; do [[ -d $d ]] && unbind "$d"; done
	for d in "${large[@]}"; do umount "$HUB/$d" 2>/dev/null || true; done
	# The flash's state onto the card's own folder (seen at HUB_CARD), its large folders left as they are.
	local skip=()
	for d in "${large[@]}"; do skip+=("--exclude=./$d"); done
	tar -C "$MNT/hub" "${skip[@]}" -cf - . | tar -C "$HUB_CARD" -xpf -
	umount "$HUB"
	umount "$HUB_CARD"
	rmdir "$HUB_CARD" 2>/dev/null || true
	sed -i "/$STATE_TAG\$/d" "$FSTAB"
	for u in "${units[@]}"; do rm -f "/etc/systemd/system/$u.d/$STATE_DROPIN"; done
	systemctl daemon-reload
	sed -i '/^state=1$/d; /^state_large=/d' "$STATE"
	say "done: the state is on the card again; the flash keeps its copy"
}

# A bind mount from the flash undone: its contents copied back into the folder beneath.
unbind() {
	local dir=$1 tmp
	mountpoint -q "$dir" || return 0
	tmp=$(mktemp -d)
	copy_tree "$dir" "$tmp"
	umount "$dir"
	copy_tree "$tmp" "$dir"
	rm -r -- "$tmp"
}

off() {
	[[ -e $STATE ]] || die "not on (no $STATE)"
	grep -q '^state=1' "$STATE" && state_off
	restart_on_exit
	local journal_dir="" crashwatch="" ramlog_hdd="" ramlog_old=""
	while IFS='=' read -r k v; do
		case $k in
		journal_dir) journal_dir=$v ;;
		crashwatch) crashwatch=1 ;;
		ramlog_hdd) ramlog_hdd=$v ;;
		ramlog_old) ramlog_old=$v ;;
		esac
	done <"$STATE"
	if [[ -n $crashwatch ]]; then
		say "crash watch's records back"
		systemctl stop irate-box-crashwatch.service 2>/dev/null || true
		unbind "$CRASHWATCH"
		systemctl start irate-box-crashwatch.service 2>/dev/null || true
	fi
	journal_away
	if [[ -n $ramlog_old ]]; then
		say "armbian-ramlog's copy, and its journal, back to $ramlog_old"
		copy_tree "$MNT/ramlog" "$ramlog_old"
		sed -i "/$FSTAB_TAG\$/d" "$RAMLOG_DEFAULT"
		[[ -n $ramlog_hdd ]] && echo "HDD_LOG=$ramlog_hdd" >>"$RAMLOG_DEFAULT"
		rm -f "$RAMLOG_DROPIN"
		systemctl disable -q --now nand-logs-flush.timer 2>/dev/null || true
		rm -f "$REFLUSH_UNIT" "$REFLUSH_TIMER"
		ln -sfn "$ramlog_old/journal" /var/log/journal
	fi
	if [[ -n $journal_dir ]]; then
		say "the journal back to $journal_dir"
		unbind "$journal_dir"
	fi
	rm -f "$JOURNALD_DROPIN" "$FLUSH_DROPIN"
	sed -i "/$FSTAB_TAG\$/d" "$FSTAB"
	systemctl daemon-reload
	systemctl restart systemd-journald
	journalctl --flush
	umount "$MNT" 2>/dev/null || true
	systemctl disable -q --now nand-logs-attach.service 2>/dev/null || true
	rm -f "$ATTACH_UNIT" "$STATE"
	systemctl daemon-reload
	say "done: the logs are on the card again; the flash is still formatted, with its copy"
}

[[ ${1:-} == state && ${2:-} == plan ]] && state_plan && exit 0
[[ $(id -u) == 0 ]] || die "run as root"
case "${1:-}" in
state)
	case "${2:-}" in
	on) state_on ;;
	off) state_off ;;
	*) die "usage: $0 state plan | on | off" ;;
	esac
	;;
check) check ;;
reflush) [[ -e $STATE ]] && grep -q '^ramlog_old=' "$STATE" && reflush_on && say "the journal goes back to the flash every 10 minutes" ;;
on) shift && on "$@" ;;
off) off ;;
*) die "usage: $0 check | on [--mtd N] [--force] | off | reflush | state plan|on|off" ;;
esac
