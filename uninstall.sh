#!/usr/bin/env bash
# Removes what install.sh set up, so the box is as it was before (or ready for a clean
# reinstall). Tailscale is not irate-box's: it is handed back as an ordinary boot service,
# in whatever state it is in now.
#
#   sudo ./uninstall.sh                     units, code, apps, config, state, the hub user
#   sudo ./uninstall.sh --keep-state        ... but leave /var/lib/hub (notes, saves, ZIMs)
#   sudo ./uninstall.sh --purge-packages    ... and the apt packages install.sh added:
#                                           caddy (and its apt repo), kiwix-tools, syncthing,
#                                           mosquitto, nodejs
#
# State goes too unless --keep-state: back it up first. -y skips the confirmation.
set -euo pipefail

KEEP_STATE=0 PURGE_PKGS=0 YES=0
while [ $# -gt 0 ]; do
	case "$1" in
	--keep-state) KEEP_STATE=1; shift ;;
	--purge-packages) PURGE_PKGS=1; shift ;;
	-y | --yes) YES=1; shift ;;
	-h | --help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
	*) echo "unknown option: $1" >&2; exit 2 ;;
	esac
done

HUB_USER=hub
CODE=/opt/irate-box
STATE=/var/lib/hub
SHARE=/usr/share/hub
ETC=/etc/hub
UNITDIR=/etc/systemd/system

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "run as root (sudo ./uninstall.sh …)" >&2; exit 1; }

if [ "$YES" != 1 ]; then
	echo "This removes irate-box from this machine$([ "$KEEP_STATE" = 1 ] || echo ", including $STATE (notes, saves, ZIMs)")$([ "$PURGE_PKGS" = 1 ] && echo ", and its apt packages")."
	read -r -p "Continue? [y/N] " answer
	[ "${answer,,}" = y ] || { echo "Aborted."; exit 0; }
fi

say "Stopping and disabling services"
for u in irate-box kiwix silverbullet "syncthing@$HUB_USER" ttyd excalidraw-room \
	irate-box-tailscale.path irate-box-tailscale.service irate-box-tailscale-boot.service \
	irate-box-tailscale-off.timer; do
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

say "Removing unit files and drop-ins"
rm -f "$UNITDIR"/{irate-box,kiwix,silverbullet,ttyd,excalidraw-room}.service \
	"$UNITDIR"/irate-box-tailscale.{path,service} "$UNITDIR"/irate-box-tailscale-boot.service \
	"$UNITDIR/caddy.service.d/irate-box.conf" \
	"$UNITDIR/syncthing@$HUB_USER.service.d/irate-box.conf"
rmdir "$UNITDIR/caddy.service.d" "$UNITDIR/syncthing@$HUB_USER.service.d" 2>/dev/null || true

say "Removing service config"
rm -f /etc/mosquitto/conf.d/irate-box.conf /etc/mosquitto/irate-box.acl
if [ -f /etc/caddy/Caddyfile.pre-irate-box ]; then
	mv /etc/caddy/Caddyfile.pre-irate-box /etc/caddy/Caddyfile
	systemctl try-restart caddy || true
fi
rm -f /etc/caddy/Caddyfile.new

say "Removing code, apps, config and binaries"
rm -rf "$CODE" "$SHARE" "$ETC"
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

if [ "$PURGE_PKGS" = 1 ]; then
	say "Purging packages"
	export DEBIAN_FRONTEND=noninteractive
	pkgs=()
	for p in caddy kiwix-tools syncthing mosquitto mosquitto-clients nodejs; do
		dpkg -s "$p" >/dev/null 2>&1 && pkgs+=("$p")
	done
	[ ${#pkgs[@]} -eq 0 ] || apt-get purge -y -q "${pkgs[@]}"
	apt-get autoremove --purge -y -q
	rm -f /etc/apt/sources.list.d/caddy-stable.list /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	# Their logs outlive the purge, owned by users that no longer exist. Armbian's log2ram
	# keeps an on-disk copy of /var/log in /var/log.hdd, so both places.
	for d in /var/log /var/log.hdd; do
		rm -rf "$d/caddy" "$d/mosquitto"
	done
	apt-get update -q
fi

systemctl daemon-reload
systemctl reset-failed >/dev/null 2>&1 || true
say "irate-box removed"
