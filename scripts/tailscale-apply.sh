#!/bin/sh
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Applies the operator's remote-access choice. The hub runs unprivileged and can only
# record that choice in $STATE/tailscale.want; this runs as root to act on it.
#   off           tailscaled stopped
#   on            tailscaled running, and started again at every boot
#   on SECONDS    running, then back to off after SECONDS of uptime. The timer is
#                 monotonic, so it counts correctly on a board with no RTC. A reboot
#                 ends the session.
# Run by irate-box-tailscale.path on every change to the file, and by
# irate-box-tailscale-boot.service (with --boot) once at startup.
set -eu

WANT=${HUB_STATE_DIR:-/var/lib/hub}/tailscale.want
HUB_USER=${HUB_USER:-hub}
# The file is the hub's, in the hub's folder: root writes it only as the hub, so a link the hub
# put there leads nowhere the hub could not write already.
want_off="runuser -u $HUB_USER -- sh -c 'printf \"off\\n\" >\"\$1\"' sh $WANT"
OFF_TIMER=irate-box-tailscale-off

mode=off secs=
# A plain file only: not a link, nor a FIFO that would hang this.
if [ -f "$WANT" ] && [ ! -L "$WANT" ]; then
	read -r mode secs <"$WANT" || true
fi
# Written by the hub, so parsed strictly: anything unexpected means off.
case "$mode" in on | off) ;; *) mode=off ;; esac
case "$secs" in *[!0-9]*) mode=off secs= ;; esac

if [ "${1:-}" = --boot ] && [ -n "$secs" ]; then
	eval "$want_off"
	mode=off secs=
fi

# Whatever happens next, an earlier countdown no longer applies.
systemctl stop "$OFF_TIMER.timer" 2>/dev/null || true
systemctl reset-failed "$OFF_TIMER.timer" "$OFF_TIMER.service" 2>/dev/null || true

if [ "$mode" = on ]; then
	systemctl start tailscaled.service
	if [ -n "$secs" ]; then
		# Writing "off" re-triggers the path unit, which stops tailscaled.
		systemd-run --quiet --unit="$OFF_TIMER" --on-active="${secs}s" \
			/bin/sh -c "$want_off"
	fi
else
	systemctl stop tailscaled.service
fi
