#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
# Removes what install.sh set up, so the box is as it was before (or ready for a clean
# reinstall). Tailscale is not irate-box's: it is handed back as an ordinary boot service,
# in whatever state it is in now.
#
#   sudo ./uninstall.sh                     units, code, apps, config, state, the hub user
#   sudo ./uninstall.sh --keep-state        ... but leave /var/lib/hub (notes, saves, ZIMs)
#   sudo ./uninstall.sh --purge-packages    ... and the apt packages install.sh added:
#                                           nginx (if irate-box installed it), caddy (and
#                                           its apt repo), kiwix-tools, syncthing,
#                                           mosquitto, nodejs, cgit, fcgiwrap
#
# State goes too unless --keep-state (the git repositories are state): back it up
# first. -y skips the confirmation.
set -euo pipefail

KEEP_STATE=0 PURGE_PKGS=0 YES=0
while [ $# -gt 0 ]; do
	case "$1" in
	--keep-state) KEEP_STATE=1; shift ;;
	--purge-packages) PURGE_PKGS=1; shift ;;
	-y | --yes) YES=1; shift ;;
	-h | --help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
	*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
done

HUB_USER=hub
CODE=/opt/irate-box
STATE=/var/lib/hub
SHARE=/usr/share/hub
ETC=/etc/hub
UNITDIR=/etc/systemd/system
# Read now: $ETC goes before the packages do.
NGINX_OURS=0
[ -f "$ETC/nginx-ours" ] && NGINX_OURS=1

say() { if [ -t 1 ]; then printf '\033[1m==> %s\033[0m\n' "$*"; else printf '==> %s\n' "$*"; fi; }
[ "$(id -u)" = 0 ] || { echo "run as root (sudo ./uninstall.sh …)" >&2; exit 1; }

if [ "$YES" != 1 ]; then
	echo "This removes irate-box from this machine$([ "$KEEP_STATE" = 1 ] || echo ", including $STATE (notes, saves, ZIMs)")$([ "$PURGE_PKGS" = 1 ] && echo ", and its apt packages")."
	read -r -p "Continue? [y/N] " answer
	[ "${answer,,}" = y ] || { echo "Aborted."; exit 0; }
fi

say "Stopping and disabling services"
for u in irate-box kiwix silverbullet "syncthing@$HUB_USER" ttyd excalidraw-room \
	irate-box-git.socket irate-box-git.service irate-box-ci.path irate-box-ci.service \
	irate-box-tailscale.path irate-box-tailscale.service irate-box-tailscale-boot.service \
	irate-box-tailscale-off.timer irate-box-librarian.timer irate-box-librarian.service \
	irate-box-control.path irate-box-control.service irate-box-uplink.service; do
	systemctl disable --now "$u" >/dev/null 2>&1 || true
done
# install.sh enables mosquitto only for --with-mqtt; with its config gone it would come
# back up as a bare broker on :1883, so it is stopped here and purged or left disabled.
systemctl disable --now mosquitto >/dev/null 2>&1 || true

if systemctl cat tailscaled.service >/dev/null 2>&1; then
	say "Handing Tailscale back as an ordinary boot service (left $(systemctl is-active tailscaled))"
	rm -f "$UNITDIR/tailscaled.service.d/irate-box.conf"
	rmdir "$UNITDIR/tailscaled.service.d" 2>/dev/null || true
	systemctl enable --quiet tailscaled
fi

# What the Security page changed on the owner's say-so (Cockpit, SSH, LLMNR, units switched
# off) goes back as it was found, before the code that knows how is removed.
if [ -f "$ETC/security-changes.json" ] && [ -f "$CODE/irate_box/root/security.py" ]; then
	# A service install.sh --take-port-80 switched off needs :80 back, so the hub's web
	# server lets go of it first.
	if grep -q 'take-port-80' "$ETC/security-changes.json"; then
		[ -f /etc/caddy/irate-box.caddy ] || systemctl disable --now caddy >/dev/null 2>&1 || true
		[ "$NGINX_OURS" = 0 ] || systemctl disable --now nginx >/dev/null 2>&1 || true
	fi
	say "Putting back what the Security page changed"
	HUB_ETC_DIR="$ETC" "$CODE/irate-box" security undo-all | sed 's/^/    /' || true
fi

# "Keep retrying" on /admin's Network page changed two settings of the owner's WiFi profile;
# put them back (uplink.py recorded the old values).
if [ -f "$ETC/uplink-changes.json" ] && [ -f "$CODE/irate_box/hub/uplink.py" ]; then
	say "Putting the WiFi profile back as it was"
	HUB_ETC_DIR="$ETC" "$CODE/irate-box" uplink undo-all | sed 's/^/    /' || true
fi

# A clock module set up by rtc.py: its units go, and a kernel-declared module is released.
if [ -f "$ETC/rtc.json" ] && [ -f "$CODE/irate_box/root/rtc.py" ]; then
	HUB_ETC_DIR="$ETC" HUB_STATE_DIR="$STATE" "$CODE/irate-box" rtc remove | sed 's/^/    /' || true
fi

say "Removing unit files and drop-ins"
rm -f "$UNITDIR"/{irate-box,kiwix,silverbullet,ttyd,excalidraw-room,irate-box-git,irate-box-uplink}.service \
	"$UNITDIR"/irate-box-git.socket "$UNITDIR"/irate-box-ci.{path,service} \
	"$UNITDIR"/irate-box-tailscale.{path,service} "$UNITDIR"/irate-box-tailscale-boot.service \
	"$UNITDIR"/irate-box-librarian.{service,timer} "$UNITDIR"/irate-box-control.{path,service} \
	"$UNITDIR/caddy.service.d/irate-box.conf" \
	"$UNITDIR/syncthing@$HUB_USER.service.d/irate-box.conf"
rmdir "$UNITDIR/caddy.service.d" "$UNITDIR/syncthing@$HUB_USER.service.d" 2>/dev/null || true

say "Removing service config"
rm -f /etc/mosquitto/conf.d/irate-box.conf /etc/mosquitto/irate-box.acl
# An owner's own Caddy: the hub's site file and the import line install.sh added go; the
# rest of their Caddyfile is theirs and stays as it is.
if [ -f /etc/caddy/irate-box.caddy ]; then
	sed -i '/^# irate-box: the hub, on its own port/d; \|^import /etc/caddy/irate-box.caddy$|d' /etc/caddy/Caddyfile
	rm -f /etc/caddy/irate-box.caddy /etc/caddy/irate-box.caddy.prev
	systemctl try-restart caddy || true
elif [ -f /etc/caddy/Caddyfile.pre-irate-box ]; then
	mv /etc/caddy/Caddyfile.pre-irate-box /etc/caddy/Caddyfile
	systemctl try-restart caddy || true
fi
rm -f /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile.irate-box-off /etc/caddy/irate-box-unclaimed
# nginx: the hub's site, its login file and the first-use mark go; the package's default site
# comes back if install.sh switched it off. The rest of the config is the owner's, or the
# package's, and stays.
if [ -f /etc/nginx/conf.d/irate-box.conf ] || [ -f "$ETC/nginx-default-site-off" ]; then
	rm -f /etc/nginx/conf.d/irate-box.conf /etc/nginx/conf.d/irate-box.conf.prev \
		/etc/nginx/irate-box.htpasswd /etc/nginx/irate-box.htpasswd.new /etc/nginx/irate-box-unclaimed
	if [ -f "$ETC/nginx-default-site-off" ] && [ -f /etc/nginx/sites-available/default ]; then
		ln -sf ../sites-available/default /etc/nginx/sites-enabled/default
	fi
	systemctl try-restart nginx || true
fi

say "Removing code, apps, config and binaries"
rm -rf "$CODE" "$SHARE" "$ETC" /var/cache/irate-box /var/log/irate-box
rm -f /usr/local/bin/silverbullet /usr/local/bin/ttyd

if [ "$KEEP_STATE" = 1 ]; then
	say "Keeping $STATE"
else
	say "Removing $STATE"
	rm -rf "$STATE"
fi
if id -u "$HUB_USER" >/dev/null 2>&1; then
	say "Removing the $HUB_USER user"
	pkill -u "$HUB_USER" 2>/dev/null || true
	sleep 1
	userdel "$HUB_USER" 2>/dev/null || true
	getent group "$HUB_USER" >/dev/null && groupdel "$HUB_USER" 2>/dev/null || true
fi
# The builds' user (ci.py): it owns nothing outside $STATE/ci.
if id -u hubci >/dev/null 2>&1; then
	say "Removing the hubci user"
	pkill -u hubci 2>/dev/null || true
	sleep 1
	userdel hubci 2>/dev/null || true
	getent group hubci >/dev/null && groupdel hubci 2>/dev/null || true
fi

if [ "$PURGE_PKGS" = 1 ]; then
	say "Purging packages"
	export DEBIAN_FRONTEND=noninteractive
	pkgs=()
	for p in caddy kiwix-tools syncthing mosquitto mosquitto-clients nodejs cgit fcgiwrap; do
		dpkg -s "$p" >/dev/null 2>&1 && pkgs+=("$p")
	done
	# nginx only if irate-box brought it: an owner's nginx serves their own sites.
	[ "$NGINX_OURS" = 1 ] && for p in nginx nginx-common; do
		dpkg -s "$p" >/dev/null 2>&1 && pkgs+=("$p")
	done
	# Stopped first, not left to the packages' own scripts: a policy-rc.d (containers, some
	# images) stops those from stopping anything, and nginx outlived its purge in a test.
	for p in "${pkgs[@]}"; do
		case "$p" in nginx | caddy | mosquitto) systemctl disable --now "$p" >/dev/null 2>&1 || true ;; esac
	done
	[ ${#pkgs[@]} -eq 0 ] || apt-get purge -y -q "${pkgs[@]}"
	apt-get autoremove --purge -y -q
	rm -f /etc/apt/sources.list.d/caddy-stable.list /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	# Their logs outlive the purge, owned by users that no longer exist. Armbian's log2ram
	# keeps an on-disk copy of /var/log in /var/log.hdd, so both places.
	for d in /var/log /var/log.hdd; do
		rm -rf "$d/caddy" "$d/mosquitto"
		[ "$NGINX_OURS" = 0 ] || rm -rf "$d/nginx"
	done
	apt-get update -q
fi

systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true
say "irate-box removed"
