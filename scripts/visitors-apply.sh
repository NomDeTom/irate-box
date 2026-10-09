#!/bin/sh
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Applies the owner's choice to count unique visitors (the hub records it in
# $STATE/visitors.want, one word, on or off). On: the counting helper runs
# (irate-box-visitors.service, irate_box/root/visitors.py). Off: it is stopped and kept from
# starting at boot, and the counts it left go. Run as root by irate-box-visitors-switch.path on
# every change to the file, and once by install.sh.
set -eu

WANT=${HUB_STATE_DIR:-/var/lib/hub}/visitors.want
mode=on
# A plain file only: not a link, nor a FIFO that would hang this. No file: the default, on.
if [ -f "$WANT" ] && [ ! -L "$WANT" ]; then
	read -r mode <"$WANT" || true
fi
# Written by the hub, so parsed strictly: anything unexpected means off.
case "$mode" in on | off) ;; *) mode=off ;; esac

if [ "$mode" = on ]; then
	systemctl enable --quiet --now irate-box-visitors.service
else
	systemctl disable --quiet --now irate-box-visitors.service 2>/dev/null || true
	rm -f /run/irate-box-visitors/counts.json
fi
