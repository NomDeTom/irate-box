#!/bin/bash -p
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
# Uninstall Irate-Box: remove the services, code, apps, configuration, user data and the hub user
# that install.sh set up. Use it before a clean reinstall. The box may not return to its exact
# state before the install: packages, joined networks and netplan ownership can differ.
# Tailscale is not part of Irate-Box. The uninstaller sets it to start at boot as a normal service.
# The uninstaller restores the service settings that Irate-Box recorded. It ignores a failed restore.
#
#   sudo ./uninstall.sh                     delete units, code, apps, configuration, user data and the hub user
#   sudo ./uninstall.sh --keep-state        keep user data in /var/lib/hub (notes, saves, ZIM books, Git repositories)
#   sudo ./uninstall.sh --purge-packages    also purge the apt packages that install.sh added:
#                                           nginx (if irate-box installed it), caddy (and
#                                           its apt repository), kiwix-tools, syncthing,
#                                           mosquitto, ngircd, nodejs, cgit, fcgiwrap.
#                                           A package that was on the box before the install is also purged.
#                                           apt also removes dependencies that nothing else needs.
#   sudo ./uninstall.sh -y                  skip the confirmation question
#
# Without --keep-state, the uninstaller deletes the user data: shared and private notebooks,
# accounts, files, posts, books, and Git and build data. Make a backup first.
set -euo pipefail

KEEP_STATE=0 PURGE_PKGS=0 YES=0
while [ $# -gt 0 ]; do
	case "$1" in
	--keep-state) KEEP_STATE=1; shift ;;
	--purge-packages) PURGE_PKGS=1; shift ;;
	-y | --yes) YES=1; shift ;;
	-h | --help) sed -n '4,21p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
	*) echo "uninstall.sh: unknown option: $1. Run ./uninstall.sh --help" >&2; exit 2 ;;
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
[ "$(id -u)" = 0 ] || { echo "Run the uninstaller as root: sudo ./uninstall.sh" >&2; exit 1; }

if [ "$YES" != 1 ]; then
	echo "This uninstalls Irate-Box from this machine."
	if [ "$KEEP_STATE" = 1 ]; then
		echo "It keeps the user data in $STATE."
	else
		echo "It deletes the user data in $STATE: notes, private notebooks, accounts, files, posts, books, and Git and build data."
		echo "It cannot undo this. Make a backup first."
	fi
	echo "It turns off the box's hotspot first. A device connected through the hotspot, this one perhaps, loses its connection."
	[ "$PURGE_PKGS" != 1 ] || echo "It also purges the listed apt packages and the dependencies that nothing else needs. This includes packages that were on the box before the install."
	read -r -p "Uninstall Irate-Box? Type y to continue, or anything else to stop. [y/N] " answer
	[ "${answer,,}" = y ] || { echo "Stopped. Nothing was changed."; exit 0; }
fi

# The hotspot first, while its code is still here: off, and its units, connection and files gone. Its
# firewall (irate-box-firewall.service, below) goes only after that: a hotspot left up without it would hand guests
# an unfiltered way onto the box. When the hotspot cannot be confirmed off, stop here with the firewall in place.
say "Turning off the hotspot"
if [ -x "$CODE/irate-box" ]; then
	if ! "$CODE/irate-box" hub_control ap-remove; then
		echo "Stopped: the hotspot is still up, so its firewall stays. Turn the hotspot off on /admin (Network → Hotspot), then run the uninstaller again." >&2
		exit 1
	fi
else
	# A partial install, its code gone: the same names, by hand.
	for u in irate-box-ap-deadman.timer irate-box-ap-deadman.service irate-box-ap.service irate-box-ap-dns.service irate-box-ap-hostapd.service; do
		systemctl disable --now "$u" >/dev/null 2>&1 || true
	done
	nmcli connection down irate-box-ap >/dev/null 2>&1 || true
	nmcli connection delete irate-box-ap >/dev/null 2>&1 || true
	ip link show ap0 >/dev/null 2>&1 && iw dev ap0 del 2>/dev/null
	rm -f /etc/NetworkManager/system-connections/irate-box-ap.nmconnection /etc/NetworkManager/dispatcher.d/90-irate-box-ap \
		/etc/systemd/network/10-irate-box-ap.link "$UNITDIR"/irate-box-ap.service "$UNITDIR"/irate-box-ap-dns.service \
		"$UNITDIR"/irate-box-ap-hostapd.service "$ETC"/ap-dnsmasq.conf "$ETC"/ap-hostapd.conf "$ETC"/ap.json "$ETC"/ap-tried.json
	systemctl daemon-reload
	if systemctl is-active --quiet irate-box-ap-dns.service || systemctl is-active --quiet irate-box-ap-hostapd.service ||
		nmcli -t -f NAME connection show --active 2>/dev/null | grep -qx irate-box-ap || ip link show ap0 >/dev/null 2>&1; then
		echo "Stopped: the hotspot is still up, so its firewall stays. Stop it by hand (nmcli connection down irate-box-ap), then run the uninstaller again." >&2
		exit 1
	fi
fi

say "Stopping and disabling services"
for u in irate-box kiwix silverbullet "syncthing@$HUB_USER" ttyd excalidraw-room \
	irate-box-git.socket irate-box-git.service irate-box-ci.path irate-box-ci.service \
	irate-box-tailscale.path irate-box-tailscale.service irate-box-tailscale-boot.service \
	irate-box-tailscale-off.timer irate-box-librarian.timer irate-box-librarian.service irate-box-secdoctor.timer irate-box-secdoctor.service irate-box-firewall.service \
	irate-box-control.path irate-box-control.service irate-box-uplink.service irate-box-crashwatch.service irate-box-led.service \
	irate-box-visitors-switch.path irate-box-visitors-switch.service irate-box-visitors.service \
	irate-box-notebooks.path irate-box-notebooks.service; do
	systemctl disable --now "$u" >/dev/null 2>&1 || true
done
# The notebooks per login that are running (a template, started by irate-box-notebooks.service): stopped.
systemctl stop 'irate-box-notebook@*.service' >/dev/null 2>&1 || true
# Guests' onward internet (root/share.py): its rules went with irate-box-firewall.service above (one
# table with the hotspot firewall); the guests' resolver goes; forwarding goes back to what the box had before
# sharing, and its sysctl file goes.
systemctl disable --now irate-box-guest-dns.service 2>/dev/null || true
rm -f /etc/systemd/system/irate-box-guest-dns.service
rm -f /etc/logrotate.d/irate-box-pages /etc/tmpfiles.d/irate-box-pagelog.conf
rm -rf /run/irate-box/pagelog
if [ -f /etc/sysctl.d/90-irate-box-share.conf ]; then
	rm -f /etc/sysctl.d/90-irate-box-share.conf
	was="$(python3 -c 'import json; print(json.load(open("/etc/hub/share.json")).get("forward_was") or "")' 2>/dev/null || true)"
	if [ "$was" = 0 ]; then sysctl -q -w net.ipv4.ip_forward=0 || true; fi
fi
# install.sh enables mosquitto only for --with-mqtt; with its config gone it would come
# back up as a bare broker on :1883, so it is stopped here and purged or left disabled.
systemctl disable --now mosquitto >/dev/null 2>&1 || true
# The same for ngircd (--with-irc): Debian starts it on install, and without irate-box's
# config it would come back as Debian's example server on :6667.
systemctl disable --now ngircd >/dev/null 2>&1 || true

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
	# Only the server the hub was in, read from what was recorded, never a guess: Caddy when the hub's site is
	# its own Caddyfile (an owner's Caddy that imports the hub keeps running), nginx when irate-box installed it;
	# and never the service that gave up the port, which is the one being given it back.
	taken="$(python3 -c 'import json, sys
units = json.load(open(sys.argv[1])).get("units", {})
print(" ".join(u for u, c in units.items() if "take-port-80" in str(c.get("reason", ""))))' "$ETC/security-changes.json" 2>/dev/null || true)"
	if [ -n "$taken" ]; then
		case " $taken " in *" caddy.service "* | *" caddy "*) ;; *)
			[ ! -f /etc/caddy/irate-box.caddy ] && grep -qs '^# Generated by irate-box' /etc/caddy/Caddyfile &&
				{ systemctl disable --now caddy >/dev/null 2>&1 || true; }
			;;
		esac
		case " $taken " in *" nginx.service "* | *" nginx "*) ;; *)
			[ "$NGINX_OURS" = 0 ] || systemctl disable --now nginx >/dev/null 2>&1 || true
			;;
		esac
	fi
	say "Restoring the settings that the Security page changed"
	HUB_ETC_DIR="$ETC" "$CODE/irate-box" security undo-all | sed 's/^/    /' || true
fi

# "Keep retrying" on /admin's Network page changed two settings of the owner's WiFi profile;
# put them back (uplink.py recorded the old values).
if { [ -f "$ETC/uplink-changes.json" ] || [ -f "$ETC/uplink-roaming.json" ]; } && [ -f "$CODE/irate_box/hub/uplink.py" ]; then
	say "Restoring the recorded WiFi profile settings"
	HUB_ETC_DIR="$ETC" "$CODE/irate-box" uplink undo-all | sed 's/^/    /' || true
fi
# The box defaults chosen on the Network page: irate-box's own NetworkManager drop-in, removed; whatever
# it shadowed (the image's drop-ins, NetworkManager's defaults) applies again.
if [ -f /etc/NetworkManager/conf.d/zz-90-irate-box-network.conf ]; then
	say "Removing the box defaults from the Network page"
	rm -f /etc/NetworkManager/conf.d/zz-90-irate-box-network.conf
	systemctl reload NetworkManager 2>/dev/null || true
fi
# One connection's own settings chosen on the Network page: put back as they were.
if [ -f "$ETC/nm-connection-changes.json" ] && [ -f "$CODE/irate_box/root/nmconnection.py" ]; then
	say "Restoring the recorded WiFi connection settings"
	HUB_ETC_DIR="$ETC" "$CODE/irate-box" nmconnection undo-all | sed 's/^/    /' || true
fi
# mPWRD-OS's wifisync, if switched here: back as the image had it.
if [ -f "$STATE/control/wifisync.json" ]; then
	was=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("was", ""))' "$STATE/control/wifisync.json" 2>/dev/null || true)
	# Masked here when disable couldn't take (a link it doesn't own): unmasked first, or enable fails.
	if [ -n "$was" ] && [ "$was" != masked ] && [ "$(systemctl is-enabled wifisync.service 2>/dev/null)" = masked ]; then
		systemctl unmask wifisync.service >/dev/null 2>&1 || true
	fi
	case "$was" in
	enabled) systemctl enable --now wifisync.service >/dev/null 2>&1 || true ;;
	disabled) systemctl disable --now wifisync.service >/dev/null 2>&1 || true ;;
	masked) systemctl mask --now wifisync.service >/dev/null 2>&1 || true ;;
	esac
fi
# Connections handed over from netplan to NetworkManager stay NetworkManager's (the box stays on its
# network); said how to put the netplan files back.
if [ -f "$ETC/netplan-handover.json" ] && [ -f "$CODE/irate_box/root/nmhandover.py" ]; then
	HUB_ETC_DIR="$ETC" HUB_STATE_DIR="$STATE" "$CODE/irate-box" nmhandover kept | sed 's/^/    /' || true
fi
# Networks added on the Network page's access tab are the owner's: kept, and said how to remove them.
if [ -f "$ETC/wifi-joined.json" ] && [ -f "$CODE/irate_box/root/wifijoin.py" ]; then
	HUB_ETC_DIR="$ETC" "$CODE/irate-box" wifijoin kept | sed 's/^/    /' || true
fi

# A clock module set up by rtc.py: its units go, and a kernel-declared module is released.
# Crash watch's hang settings: the kernel's panic restart and the watchdog, taken away.
if [ -f "$CODE/irate_box/root/crashwatch.py" ]; then
	HUB_ETC_DIR="$ETC" HUB_STATE_DIR="$STATE" "$CODE/irate-box" crashwatch undo-all | sed 's/^/    /' || true
fi
# The status light: each LED it took back to its trigger and brightness from before.
if [ -f "$CODE/irate_box/root/led.py" ]; then
	HUB_ETC_DIR="$ETC" HUB_STATE_DIR="$STATE" "$CODE/irate-box" led undo-all | sed 's/^/    /' || true
fi
# The clock's sources chosen on the Clock page: the image's time service as it was (chrony removed and
# systemd-timesyncd installed again if the page swapped them, chrony.conf as it came, our drop-ins gone).
if [ -f "$ETC/clock.json" ] && [ -f "$CODE/irate_box/root/clock.py" ]; then
	HUB_ETC_DIR="$ETC" HUB_STATE_DIR="$STATE" "$CODE/irate-box" clock undo-all | sed 's/^/    /' || true
fi
if [ -f "$ETC/rtc.json" ] && [ -f "$CODE/irate_box/root/rtc.py" ]; then
	HUB_ETC_DIR="$ETC" HUB_STATE_DIR="$STATE" "$CODE/irate-box" rtc remove | sed 's/^/    /' || true
fi
# The search for a clock module now and then (irate-box-rtc-look.timer).
if [ -f "$CODE/irate_box/root/rtc.py" ]; then
	HUB_ETC_DIR="$ETC" HUB_STATE_DIR="$STATE" "$CODE/irate-box" rtc look-off || true
fi

say "Removing unit files and drop-ins"
rm -f "$UNITDIR/irate-box-nginx-recover.service" "$UNITDIR/nginx.service.d/irate-box-recovery.conf"
rmdir "$UNITDIR/nginx.service.d" 2>/dev/null || true
rm -f "$UNITDIR"/{irate-box,kiwix,silverbullet,ttyd,excalidraw-room,irate-box-git,irate-box-uplink,irate-box-crashwatch,irate-box-led}.service \
	"$UNITDIR"/irate-box-git.socket "$UNITDIR"/irate-box-ci.{path,service} \
	"$UNITDIR"/irate-box-tailscale.{path,service} "$UNITDIR"/irate-box-tailscale-boot.service \
	"$UNITDIR"/irate-box-librarian.{service,timer} "$UNITDIR"/irate-box-secdoctor.{service,timer} "$UNITDIR"/irate-box-firewall.service "$UNITDIR"/irate-box-control.{path,service} \
	"$UNITDIR"/irate-box-visitors.service "$UNITDIR"/irate-box-visitors-switch.{path,service} \
	"$UNITDIR"/irate-box-notebooks.{path,service} "$UNITDIR"/irate-box-notebook@.service \
	"$UNITDIR/caddy.service.d/irate-box.conf" \
	"$UNITDIR/syncthing@$HUB_USER.service.d/irate-box.conf" "$UNITDIR/ngircd.service.d/irate-box.conf"
rmdir "$UNITDIR/caddy.service.d" "$UNITDIR/syncthing@$HUB_USER.service.d" "$UNITDIR/ngircd.service.d" 2>/dev/null || true
# The builds' loopback rule (install.sh ci-loopback.nft): its table out of the kernel; the file goes with /etc/hub.
nft delete table inet irate_box_ci 2>/dev/null || true

say "Removing service config"
rm -f /etc/mosquitto/conf.d/irate-box.conf /etc/mosquitto/irate-box.acl
rm -rf /etc/systemd/system/mosquitto.service.d/irate-box-sandbox.conf
rm -f /etc/ngircd/irate-box.conf /etc/ngircd/irate-box.motd
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
rm -rf /etc/caddy/irate-box-access
# nginx: the hub's site, its login file and the first-use mark go; the package's default site
# comes back if install.sh switched it off. The rest of the config is the owner's, or the
# package's, and stays.
if [ -f /etc/nginx/conf.d/irate-box.conf ] || [ -f "$ETC/nginx-default-site-off" ]; then
	rm -f /etc/nginx/conf.d/irate-box.conf /etc/nginx/conf.d/irate-box.conf.prev \
		/etc/nginx/irate-box.htpasswd /etc/nginx/irate-box.htpasswd.new /etc/nginx/irate-box-unclaimed \
		/etc/nginx/irate-box-front.conf /etc/nginx/irate-box-via.conf
	if [ -f "$ETC/nginx-default-site-off" ] && [ -f /etc/nginx/sites-available/default ]; then
		ln -sf ../sites-available/default /etc/nginx/sites-enabled/default
	fi
	systemctl try-restart nginx || true
fi

say "Removing code, apps, config and binaries"
rm -rf "$CODE" "$SHARE" "$ETC" /var/cache/irate-box /var/log/irate-box /var/lib/irate-box-kit-work
rm -f /usr/local/bin/silverbullet /usr/local/bin/ttyd

if [ "$KEEP_STATE" = 1 ]; then
	say "Keeping the user data in $STATE"
else
	say "Deleting the user data in $STATE"
	rm -rf "$STATE"
	# Root's own beside it: crash history and removed hotspot profiles. The netplan files handed
	# over stay, as the lines above say how to put them back from there.
	rm -rf /var/lib/irate-box-root/crashwatch /var/lib/irate-box-root/other-ap
	rmdir /var/lib/irate-box-root 2>/dev/null || true
fi
# With --keep-state the users stay: their files stay, and a user made later with the same uid
# would own them.
if [ "$KEEP_STATE" = 1 ]; then
	say "Keeping the $HUB_USER and hubci users, who own $STATE"
elif id -u "$HUB_USER" >/dev/null 2>&1; then
	say "Removing the $HUB_USER user"
	pkill -u "$HUB_USER" 2>/dev/null || true
	sleep 1
	userdel "$HUB_USER" 2>/dev/null || true
	getent group "$HUB_USER" >/dev/null && groupdel "$HUB_USER" 2>/dev/null || true
fi
# The builds' user (ci.py): it owns nothing outside $STATE/ci.
if [ "$KEEP_STATE" != 1 ] && id -u hubci >/dev/null 2>&1; then
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
	for p in caddy kiwix-tools syncthing mosquitto mosquitto-clients ngircd nodejs cgit fcgiwrap; do
		dpkg -s "$p" >/dev/null 2>&1 && pkgs+=("$p")
	done
	# nginx only if irate-box brought it: an owner's nginx serves their own sites.
	[ "$NGINX_OURS" = 1 ] && for p in nginx nginx-common; do
		dpkg -s "$p" >/dev/null 2>&1 && pkgs+=("$p")
	done
	# Stopped first, not left to the packages' own scripts: a policy-rc.d (containers, some
	# images) stops those from stopping anything, and nginx outlived its purge in a test.
	for p in "${pkgs[@]}"; do
		case "$p" in nginx | caddy | mosquitto | ngircd) systemctl disable --now "$p" >/dev/null 2>&1 || true ;; esac
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
say "Uninstall finished. The uninstaller ignores a failed restore, so check the network connection and services"
