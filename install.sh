#!/usr/bin/env bash
# Irate-Box installer for Debian-family boards: Armbian, and mPWRD-OS (which is an
# Armbian build). Plain Debian and Raspberry Pi OS should work too; they are not tested.
#
# What it sets up:
#   /opt/irate-box            the hub (server.py, static/), copied from a checkout or cloned
#   /var/lib/hub              state: messages, board, store, clock; notes/ for the add-ons
#   /usr/share/hub/apps/      prebuilt static apps: mermaid/, draw/, tools/, serial/ (optional)
#   /var/lib/hub/zim/         --zim: ZIM files and the Kiwix library.xml
#   /etc/hub/                 hub.env, admin-password
#   /etc/nginx/conf.d/irate-box.conf   the front on :80, from the repo's irate-box.nginx
#                             (--web caddy: /etc/caddy/Caddyfile from the repo's Caddyfile)
#   irate-box.service         server.py on 127.0.0.1:8000 as the `hub` user
#   silverbullet.service      --with-notes: SilverBullet on 127.0.0.1:3000 under /notes/
#   syncthing@hub.service     --with-sync: Syncthing GUI on 127.0.0.1:8384 under /sync/
#   kiwix.service             --zim: kiwix-serve on 127.0.0.1:8081 under /wiki/
#   ttyd.service              ttyd on 127.0.0.1:7681 under /term/; enabled by --with-term only
#   mosquitto.service         --with-mqtt: MQTT on :1883, and WebSockets on 127.0.0.1:9001 at /mqtt
#   excalidraw-room.service   --with-collab: live Excalidraw sessions on 127.0.0.1:3002 (/socket.io/)
#   irate-box-tailscale.path  if Tailscale is installed: its on/off switch on /admin
#
# It does not set up the access point, dnsmasq or the captive portal. The box keeps
# whatever network it already has, and the hub is served at http://<its address>/.
# Running it again upgrades the hub in place and keeps state and the admin password.
set -euo pipefail

usage() {
	cat <<'EOF'
Usage: sudo ./install.sh [options]

  --src DIR             install the hub from this checkout (default: the checkout this
                        script sits in; if it is not in one, clone --repo instead)
  --repo URL            repository to clone (default: https://github.com/NomDeTom/irate-box)
  --branch NAME         branch to clone (default: main)
  --apps DIR            copy prebuilt static apps from DIR/mermaid, DIR/draw, DIR/tools,
                        DIR/serial
  --apps-from-actions   fetch the newest prebuilt draw, mermaid and serial (and room, with
                        --with-collab) from the forks' Actions builds via nightly.link: no
                        desktop build and no token. They are then kept current from /admin.
  --tools               the calculators (nomdetom.github.io), cloned and adapted for the hub,
                        then kept current from /admin like the other apps
  --with-notes          install SilverBullet at /notes/ (armv7, aarch64, x86_64 only)
  --with-sync           install Syncthing at /sync/ (behind the admin password)
  --zim FILE|URL        add a ZIM to the Kiwix library at /wiki/ (repeatable; a URL is
                        downloaded on the box). Installs kiwix-serve.
  --with-mqtt           install the mosquitto MQTT broker: :1883 for nodes and the phone
                        app, and WebSockets at /mqtt for pages. Anonymous, limited to msh/#.
  --with-collab         live collaboration in /draw/: Debian's nodejs plus the room relay
                        from --apps DIR/room, built on a desktop (ARMv7 and up)
  --with-term           turn on the ttyd terminal at /term/. It is always installed, but
                        stays off without this: it is a root login prompt on the network.
  --admin-password PW   password for /admin, /sync and /term, user "admin" (default: keep
                        the existing one; on a first install there is none, and the first
                        visit to /admin/ asks for it)
  --hub-url URL         where captive-portal probes are redirected (default: /)
  --web nginx|caddy     the web server in front of everything (default: nginx). Caddy is
                        the fallback: used when asked for, when the box already runs the
                        owner's Caddy and no nginx, when irate-box set this box up with
                        Caddy before nginx became the default, and when nginx cannot be
                        installed. Recorded, so updates keep it; naming the other one
                        switches over and turns irate-box's copy of the first one off.
  --port N              the port the hub is served on (default: 80). If something else
                        already serves :80, the hub is put on a free port (8080 first) and
                        says so; the other service is left as it is.
  --remove NAME         take an add-on off again: notes, sync, mqtt, term or collab
                        (repeatable). Its service stops and its unit and config go; its
                        data (the notes folder, Syncthing's state) and packages stay. This
                        is what /admin's Add-ons page runs.
  --take-port-80        when something else serves :80, stop and disable it so the hub can
                        have the port (the captive portal needs it). Recorded, so /admin's
                        Security page can undo it and uninstall.sh starts it again.
  --download-cache DIR  take release downloads (ttyd, SilverBullet, Caddy's .deb) from DIR
                        when they are there, still checked against their checksums. /admin's
                        "Check for updates" fills it, so "Install update" needs no network
                        for anything already installed.
  -h, --help            this text
EOF
}

SRC="" REPO="https://github.com/NomDeTom/irate-box" BRANCH="main" APPS_SRC="" DL_CACHE=""
APPS_FROM_ACTIONS=0 WITH_TOOLS=0 WITH_NOTES=0 WITH_SYNC=0 WITH_TERM=0 WITH_MQTT=0 WITH_COLLAB=0 ADMIN_PW="" HUB_URL="/" ZIMS=()
HUB_PORT="" TAKE_PORT_80=0 REMOVE=() WEB=""
while [ $# -gt 0 ]; do
	case "$1" in
	--src) SRC="$2"; shift 2 ;;
	--repo) REPO="$2"; shift 2 ;;
	--branch) BRANCH="$2"; shift 2 ;;
	--apps) APPS_SRC="$2"; shift 2 ;;
	--tools) WITH_TOOLS=1; shift ;;
	--apps-from-actions) APPS_FROM_ACTIONS=1; shift ;;
	--with-notes) WITH_NOTES=1; shift ;;
	--with-sync) WITH_SYNC=1; shift ;;
	--zim) ZIMS+=("$2"); shift 2 ;;
	--with-term) WITH_TERM=1; shift ;;
	--with-mqtt) WITH_MQTT=1; shift ;;
	--with-collab) WITH_COLLAB=1; shift ;;
	--admin-password) ADMIN_PW="$2"; shift 2 ;;
	--hub-url) HUB_URL="$2"; shift 2 ;;
	--port) HUB_PORT="$2"; shift 2 ;;
	--web)
		case "$2" in nginx | caddy) WEB="$2" ;; *) echo "--web takes nginx or caddy" >&2; exit 2 ;; esac
		shift 2 ;;
	--take-port-80) TAKE_PORT_80=1; shift ;;
	--remove)
		case "$2" in notes | sync | mqtt | term | collab) REMOVE+=("$2") ;; *) die "--remove takes notes, sync, mqtt, term or collab" ;; esac
		shift 2 ;;
	--download-cache) DL_CACHE="$2"; shift 2 ;;
	-h | --help) usage; exit 0 ;;
	*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
	esac
done

# An add-on being removed is not installed by this run, whatever else asks for it.
for r in "${REMOVE[@]}"; do
	case "$r" in
	notes) WITH_NOTES=0 ;; sync) WITH_SYNC=0 ;; mqtt) WITH_MQTT=0 ;; term) WITH_TERM=0 ;; collab) WITH_COLLAB=0 ;;
	esac
done

HUB_USER=hub
CODE=/opt/irate-box
STATE=/var/lib/hub
APPS=/usr/share/hub/apps
ROOM=/usr/share/hub/room
ETC=/etc/hub
SB_VERSION=2.11.1
TTYD_VERSION=1.7.7

# Bold on a terminal only: from /admin the output goes to a log file that a page shows.
say() { if [ -t 1 ]; then printf '\033[1m==> %s\033[0m\n' "$*"; else printf '==> %s\n' "$*"; fi; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }
# Things the owner should know, said where they happen and again at the end of the run.
NOTICES=()
notice() {
	NOTICES+=("$*")
	if [ -t 1 ]; then printf '\033[1;33m!!  %s\033[0m\n' "$*"; else printf '!!  %s\n' "$*"; fi
}
# fetch URL DEST [CACHED-NAME]: from the download cache if it holds CACHED-NAME (default:
# the URL's file name), else from the network. Callers check checksums either way.
fetch() {
	local name="${3:-$(basename "$1")}"
	if [ -n "$DL_CACHE" ] && [ -s "$DL_CACHE/$name" ]; then
		cp "$DL_CACHE/$name" "$2"
	else
		curl -fsSL -o "$2" "$1"
	fi
}

# --- preflight -------------------------------------------------------------------
[ "$(id -u)" = 0 ] || die "run as root (sudo ./install.sh …)"
command -v apt-get >/dev/null || die "needs a Debian-family system with apt"
command -v systemctl >/dev/null || die "needs systemd"
. /etc/os-release
DISTRO="${PRETTY_NAME:-$ID}"
[ -f /etc/armbian-release ] && DISTRO="$DISTRO (Armbian $(. /etc/armbian-release; echo "${BOARD_NAME:-$BOARD}"))"
ARCH="$(uname -m)"
say "Installing on $DISTRO, $ARCH"

if [ "$WITH_NOTES" = 1 ]; then
	case "$ARCH" in
	x86_64) SB_ARCH=x86_64 ;;
	aarch64) SB_ARCH=aarch64 ;;
	armv7l | armv8l) SB_ARCH=armv7 ;;
	*) die "--with-notes: SilverBullet has no build for $ARCH (ARMv6 boards such as the Pi Zero W cannot run it)" ;;
	esac
fi

if [ "$WITH_COLLAB" = 1 ]; then
	[ "$ARCH" != armv6l ] || die "--with-collab: Node.js has no ARMv6 build (the Pi Zero W cannot run it)"
	[ -f "${APPS_SRC:-/nonexistent}/room/dist/index.js" ] || [ -f "$ROOM/dist/index.js" ] || [ "$APPS_FROM_ACTIONS" = 1 ] ||
		die "--with-collab needs the relay: --apps-from-actions, or --apps DIR with a prebuilt DIR/room (see BUILDING.md)"
fi

case "$ARCH" in
x86_64 | aarch64) TTYD_ARCH=$ARCH ;;
armv7l | armv8l) TTYD_ARCH=armhf ;;
armv6l) TTYD_ARCH=arm ;;
*) die "no ttyd build for $ARCH" ;;
esac

all_installed() {
	local p
	for p in "$@"; do dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'ok installed' || return 1; done
}

# --- the web server --------------------------------------------------------------
# nginx is the front: measured on the Lyra (2026-10-02) at ~40 MB less RAM than Caddy and a
# fraction of the CPU per request. Caddy is the fallback, with the same routes. The choice is
# recorded in install-options, so an update keeps it; otherwise, in order: a box irate-box
# already serves through Caddy stays on it (switching is offered, not imposed), an owner's
# own web server is the one the hub fits into, and a bare box gets nginx.
NGINX_SITE=/etc/nginx/conf.d/irate-box.conf
NGINX_LOGINS=/etc/nginx/irate-box.htpasswd
# irate-box installed the package (so uninstall.sh --purge-packages may remove it), and it
# turned off the package's default site (so uninstall.sh turns it back on).
NGINX_OURS_MARK=/etc/hub/nginx-ours
NGINX_DEFAULT_MARK=/etc/hub/nginx-default-site-off
CADDY_OURS_MARK=/etc/hub/caddy-ours
# Is irate-box's site set up in this server now?
hub_site_in() {
	case "$1" in
	nginx) [ -f "$NGINX_SITE" ] ;;
	caddy) [ -f /etc/caddy/irate-box.caddy ] || grep -qs '^# Generated by irate-box' /etc/caddy/Caddyfile ;;
	esac
}
[ -z "$WEB" ] && [ -f "$ETC/install-options" ] &&
	WEB="$(grep -A1 -x -- --web "$ETC/install-options" | tail -1 || true)"
case "$WEB" in nginx | caddy) ;; *) WEB="" ;; esac
if [ -z "$WEB" ]; then
	holder80="$(ss -Hltnp 'sport = :80' 2>/dev/null | grep -o 'users:(("[^"]*' | cut -d'"' -f2 | head -1 || true)"
	if hub_site_in caddy; then
		WEB=caddy
		notice "This hub is served by Caddy, as irate-box set boxes up before nginx became the default. It stays on Caddy; rerun with --web nginx to switch, for about 40 MB less RAM."
	elif [ "$holder80" = caddy ] || { all_installed caddy && ! all_installed nginx; }; then
		WEB=caddy
	else
		WEB=nginx
	fi
fi

# --- the port --------------------------------------------------------------------
# The hub wants :80: captive-portal probes are plain HTTP on port 80. Something else already
# serving it is flagged and worked around, never overridden silently: the hub takes a free
# port instead, or, with --take-port-80, the other service is switched off (recorded, so it
# can be undone). A port chosen this way is recorded, so updates keep it.
port_holder() { ss -Hltnp "sport = :$1" 2>/dev/null | grep -o 'users:(("[^"]*' | cut -d'"' -f2 | head -1 || true; }
port_unit() {
	local pid
	pid="$(ss -Hltnp "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2 || true)"
	[ -n "$pid" ] && sed -n 's|.*/\([^/]*\.service\)$|\1|p' "/proc/$pid/cgroup" 2>/dev/null | head -1 || true
}
port_free() { [ -z "$(ss -Hltn "sport = :$1" 2>/dev/null)" ]; }
# The port a previous run chose, unless this run asks for :80 back.
[ -z "$HUB_PORT" ] && [ "$TAKE_PORT_80" = 0 ] && [ -f "$ETC/install-options" ] &&
	HUB_PORT="$(grep -A1 -x -- --port "$ETC/install-options" | tail -1 || true)"
[ "${HUB_PORT:-80}" = --port ] && HUB_PORT=""
HUB_PORT="${HUB_PORT:-80}"
case "$HUB_PORT" in *[!0-9]* | "") die "--port takes a number" ;; esac
TAKE_UNIT=""
if command -v ss >/dev/null; then
	holder="$(port_holder "$HUB_PORT")"
	# The chosen server on the port is the one the hub goes into (its own config is checked
	# below). The other one serving the hub now is switched off below; if it is the owner's and
	# keeps the port, the hub moves then.
	case "$holder" in nginx | caddy) { [ "$holder" = "$WEB" ] || hub_site_in "$holder"; } && holder="" ;; esac
	if [ -n "$holder" ]; then
		unit="$(port_unit "$HUB_PORT")"
		if [ "$HUB_PORT" = 80 ] && [ "$TAKE_PORT_80" = 1 ]; then
			[ -n "$unit" ] || die "port 80 is held by $holder, which is not a systemd service: stop it yourself, or install without --take-port-80"
			TAKE_UNIT="$unit"
			notice "Port 80 is served by $holder ($unit). --take-port-80: it will be stopped and disabled; /admin's Security page or uninstall.sh puts it back."
		elif [ "$HUB_PORT" = 80 ]; then
			for p in 8080 8088 8888; do port_free "$p" && { HUB_PORT=$p; break; }; done
			[ "$HUB_PORT" != 80 ] || die "port 80 is held by $holder, and 8080, 8088 and 8888 are taken too: pass --port N"
			notice "Port 80 is already served by $holder${unit:+ ($unit)}, so the hub goes on :$HUB_PORT and $holder is left as it is. The hotspot's sign-in sheet needs port 80, so it will not pop up; rerun with --take-port-80 to give the hub port 80 (that stops $holder)."
		else
			die "port $HUB_PORT is held by $holder: pass a free one with --port"
		fi
	fi
fi

if [ -z "$SRC" ] && [ -f "$(dirname "$0")/server.py" ]; then
	SRC="$(cd "$(dirname "$0")" && pwd)"
fi

# --- packages --------------------------------------------------------------------
say "Installing packages"
export DEBIAN_FRONTEND=noninteractive
CADDY_LIST=/etc/apt/sources.list.d/caddy-stable.list
CADDY_KEY=/usr/share/keyrings/caddy-stable-archive-keyring.gpg
# Caddy's apt repository is the preferred source. But on 2026-10-01 its index was signed
# with a subkey that expired in 2024: GnuPG warns and accepts, while Debian trixie's sqv
# rejects it and fails the whole `apt-get update`. If that happens, the repository is
# dropped and Caddy comes from its GitHub release instead (install_caddy_release), and
# $ETC/caddy-from-release records it so later runs do not add the repository back to fail
# again. Delete that file to try the repository once more.
CADDY_MARK=/etc/hub/caddy-from-release
CADDY_FROM_RELEASE=0
[ -f "$CADDY_MARK" ] && CADDY_FROM_RELEASE=1
pkgs=(python3 curl ca-certificates git unzip)
[ "$WITH_SYNC" = 1 ] && pkgs+=(syncthing)
# mosquitto-clients: mosquitto_sub/_pub, for watching the broker from the terminal.
[ "$WITH_MQTT" = 1 ] && pkgs+=(mosquitto mosquitto-clients)
[ ${#ZIMS[@]} -gt 0 ] && pkgs+=(kiwix-tools)
[ "$WITH_COLLAB" = 1 ] && pkgs+=(nodejs)
apt_update() {
	local out
	out="$(apt-get update -q 2>&1)" && { printf '%s\n' "$out"; return 0; }
	printf '%s\n' "$out"
	# Whether Caddy's repository is the cause is tested, not guessed from the messages:
	# set it aside and try again. If that works, it was; if not (offline), it goes back.
	if [ -f "$CADDY_LIST" ]; then
		mv "$CADDY_LIST" "$CADDY_LIST.off"
		if out="$(apt-get update -q 2>&1)"; then
			printf '%s\n' "$out"
			echo "    Caddy's apt repository did not verify: dropping it, using Caddy's GitHub release"
			rm -f "$CADDY_LIST.off" "$CADDY_KEY"
			CADDY_FROM_RELEASE=1
			install -d -m 750 "$(dirname "$CADDY_MARK")"
			echo "apt repository failed verification $(date -u +%Y-%m-%d)" >"$CADDY_MARK"
			return 0
		fi
		mv "$CADDY_LIST.off" "$CADDY_LIST"
	fi
	# Offline, or a mirror is down: fine if there is nothing new to install.
	if all_installed "${pkgs[@]}"; then
		echo "    apt-get update failed; every package needed is already installed, so carrying on"
		return 0
	fi
	return 1
}
install_caddy_release() {
	local a tag ver tmp deb base
	case "$ARCH" in
	armv7l | armv8l) a=armv7 ;;
	aarch64) a=arm64 ;;
	x86_64) a=amd64 ;;
	*) die "no Caddy release build for $ARCH" ;;
	esac
	# sed, not grep -m1: an early exit closes the pipe on curl (error 23), and with pipefail
	# that kills the script inside the substitution, before any message.
	tag="$(curl -fsSL https://api.github.com/repos/caddyserver/caddy/releases/latest |
		sed -n 's/^ *"tag_name": *"\([^"]*\)".*/\1/p')" || tag=""
	# Offline: the tag /admin's update check looked up, else keep what is installed.
	[ -n "$tag" ] || [ -z "$DL_CACHE" ] || tag="$(cat "$DL_CACHE/caddy-release-tag" 2>/dev/null || true)"
	if [ -z "$tag" ]; then
		command -v caddy >/dev/null || die "could not reach GitHub for Caddy's latest release"
		echo "    could not reach GitHub; keeping the installed $(caddy version | cut -d' ' -f1)"
		return
	fi
	ver="${tag#v}"
	if [ "$(dpkg-query -W -f='${Version}' caddy 2>/dev/null)" = "$ver" ]; then
		echo "    Caddy $ver is already installed"
		return
	fi
	say "Installing Caddy $ver from its GitHub release ($a)"
	tmp="$(mktemp -d)"
	deb="caddy_${ver}_linux_${a}.deb"
	base="https://github.com/caddyserver/caddy/releases/download/$tag"
	fetch "$base/$deb" "$tmp/$deb"
	fetch "$base/caddy_${ver}_checksums.txt" "$tmp/checksums.txt"
	(cd "$tmp" && grep " $deb\$" checksums.txt | sha512sum -c --quiet) ||
		die "$deb does not match its published checksum"
	apt-get install -y -q --no-install-recommends "$tmp/$deb"
	rm -rf "$tmp"
}
apt_update || die "apt-get update failed, and some packages still need installing"
all_installed "${pkgs[@]}" || apt-get install -y -q --no-install-recommends "${pkgs[@]}"

# nginx: Debian's own package, ARMv6 included. An nginx the owner installed is used as it is.
# Openssl hashes the admin login for it (SHA-512-crypt; see "the web server" below).
# If nginx cannot be installed (no candidate, offline), the hub falls back to Caddy.
if [ "$WEB" = nginx ]; then
	if all_installed nginx; then
		[ -f "$NGINX_OURS_MARK" ] ||
			notice "nginx was already installed, so irate-box uses it as it is: the hub's site goes in $NGINX_SITE, and the rest of its config stays the owner's."
	elif apt-get install -y -q --no-install-recommends nginx openssl; then
		install -d -m 750 /etc/hub
		echo "nginx installed by irate-box $(date -u +%Y-%m-%d); uninstall.sh --purge-packages may remove it" >"$NGINX_OURS_MARK"
	else
		notice "nginx could not be installed, so the hub is served by Caddy, the fallback. Rerun with --web nginx once the package is reachable."
		WEB=caddy
	fi
	all_installed openssl || apt-get install -y -q --no-install-recommends openssl
fi

if [ "$WEB" = caddy ]; then
	# Caddy: from Caddy's own apt repo, which is current (distros lag: Debian trixie ships
	# 2.6 from 2022). Except on ARMv6: that repo's armhf build is GOARM=7 and dies with
	# SIGILL on a Pi Zero W, while Debian/Raspbian's armhf caddy is a real ARMv6 build.
	# A Caddy the owner installed themselves is used as it is: no repository added, no upgrade.
	# ($ETC/caddy-ours marks one irate-box installed; a box set up before the marker existed has
	# Caddy's repository list or the release mark instead.)
	OWNER_CADDY=0
	all_installed caddy && [ ! -f "$CADDY_OURS_MARK" ] && [ ! -f "$CADDY_LIST" ] && [ "$CADDY_FROM_RELEASE" = 0 ] &&
		OWNER_CADDY=1
	if [ "$OWNER_CADDY" = 1 ]; then
		notice "Caddy $(caddy version 2>/dev/null | cut -d' ' -f1) was already installed, so irate-box uses it as it is: no Caddy repository added, no upgrade."
	elif [ "$ARCH" = armv6l ]; then
		caddy_cand="$(apt-cache policy caddy 2>/dev/null | awk '/Candidate:/ {print $2}')"
		[ -n "$caddy_cand" ] && [ "$caddy_cand" != "(none)" ] ||
			die "ARMv6 needs the distro's caddy package, and this distro has none"
	elif [ ! -f "$CADDY_LIST" ] && [ "$CADDY_FROM_RELEASE" = 0 ]; then
		say "Adding Caddy's apt repository"
		all_installed gpg || apt-get install -y -q --no-install-recommends gpg
		if curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o "$CADDY_KEY.new" &&
			curl -fsSL -o "$CADDY_LIST.new" https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt; then
			mv "$CADDY_KEY.new" "$CADDY_KEY"
			mv "$CADDY_LIST.new" "$CADDY_LIST"
			apt_update || die "apt-get update failed after adding Caddy's repository"
		else
			rm -f "$CADDY_KEY.new" "$CADDY_LIST.new"
			command -v caddy >/dev/null || die "could not reach Caddy's apt repository"
			echo "    could not reach Caddy's apt repository; keeping the installed Caddy"
		fi
	fi
	if [ "$OWNER_CADDY" = 1 ]; then
		:
	elif [ "$CADDY_FROM_RELEASE" = 1 ]; then
		install_caddy_release
	elif ! all_installed caddy || [ -f "$CADDY_LIST" ]; then
		apt-get install -y -q --no-install-recommends caddy ||
			{ all_installed caddy && echo "    could not upgrade Caddy; keeping the installed one"; } ||
			die "could not install Caddy"
	fi
	if [ "$OWNER_CADDY" = 0 ]; then
		install -d -m 750 /etc/hub
		echo "Caddy installed by irate-box $(date -u +%Y-%m-%d); uninstall.sh --purge-packages may remove it" >"$CADDY_OURS_MARK"
	fi
fi

# --- user and directories --------------------------------------------------------
id -u "$HUB_USER" >/dev/null 2>&1 ||
	useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin "$HUB_USER"
install -d -o "$HUB_USER" -g "$HUB_USER" -m 755 "$STATE" "$STATE/notes"
install -d -m 755 "$CODE" "$APPS"
install -d -m 750 "$ETC"

# --- the hub ---------------------------------------------------------------------
if [ -n "$SRC" ]; then
	say "Copying the hub from $SRC"
	[ -f "$SRC/server.py" ] || die "$SRC does not look like an irate-box checkout"
	# State files a dev checkout may have lying around stay behind.
	find "$CODE" -mindepth 1 -delete
	tar -C "$SRC" -cf - \
		--exclude=./.git --exclude=./.notes --exclude=__pycache__ --exclude=./store-state \
		--exclude=./store --exclude=./messages.json --exclude=./board.json \
		--exclude=./clock.json --exclude=./settings.json --exclude='./tailscale.want*' . |
		tar -C "$CODE" -xf -
	chown -R root:root "$CODE"
elif [ -d "$CODE/.git" ]; then
	say "Updating $CODE"
	git -C "$CODE" pull --ff-only
else
	say "Cloning $REPO ($BRANCH)"
	rm -rf "$CODE"
	git clone --depth 1 -b "$BRANCH" "$REPO" "$CODE"
fi

# What /admin reports as the installed version: the commit, and when it was installed. A
# source with no git history but a VERSION (a copy of the installed code, as the Add-ons page
# uses) keeps the version it already had.
if ver="$(git -C "${SRC:-$CODE}" describe --always --dirty --tags 2>/dev/null)"; then
	printf '%s (installed %s)\n' "$ver" "$(date -u +%Y-%m-%d)" >"$CODE/VERSION"
elif [ -n "$SRC" ] && [ -f "$SRC/VERSION" ]; then
	cp "$SRC/VERSION" "$CODE/VERSION"
else
	printf 'unknown (installed %s)\n' "$(date -u +%Y-%m-%d)" >"$CODE/VERSION"
fi

# How this box was installed, for /admin's "Install update", which reruns this script the
# same way (hub_control.py). One argument per line. Root-owned, so the unprivileged hub
# can neither choose the repository an update comes from nor add options to it. Paths
# (--src, --apps, --zim) and the password are not recorded: apps and books stay as they
# are, and the password file is kept.
rec_repo="$REPO" rec_branch="$BRANCH"
if [ -n "$SRC" ] && git -C "$SRC" rev-parse >/dev/null 2>&1; then
	rec_repo="$(git -C "$SRC" remote get-url origin 2>/dev/null || echo "$REPO")"
	rec_branch="$(git -C "$SRC" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "$BRANCH")"
	[ "$rec_branch" = HEAD ] && rec_branch="$BRANCH"
fi
{
	printf '%s\n' --repo "$rec_repo" --branch "$rec_branch" --web "$WEB"
	[ "$WITH_NOTES" = 1 ] && echo --with-notes
	[ "$WITH_SYNC" = 1 ] && echo --with-sync
	[ "$WITH_MQTT" = 1 ] && echo --with-mqtt
	[ "$WITH_TERM" = 1 ] && echo --with-term
	[ "$WITH_COLLAB" = 1 ] && echo --with-collab
	[ "$HUB_URL" != / ] && printf '%s\n' --hub-url "$HUB_URL"
	[ "$HUB_PORT" != 80 ] && printf '%s\n' --port "$HUB_PORT"
	true
} >"$ETC/install-options"
chmod 644 "$ETC/install-options"
# A copy the hub can read ($ETC is 750: it holds the admin password), for /admin's Add-ons
# page to show what is added. Display only: updates and add-ons run from the root-owned one.
install -m 644 "$ETC/install-options" "$STATE/install-options"

# --- static apps -----------------------------------------------------------------
if [ -n "$APPS_SRC" ]; then
	for app in mermaid draw tools serial; do
		[ -d "$APPS_SRC/$app" ] || continue
		say "Installing $app from $APPS_SRC/$app"
		rm -rf "${APPS:?}/$app"
		cp -a "$APPS_SRC/$app" "$APPS/$app"
		rm -rf "$APPS/$app/.git"
		chown -R root:root "$APPS/$app"
	done
fi
# Prebuilt apps from the forks' irate-box-bundle.yml artifacts. The librarian downloads and
# checks each as the hub user; hub_control.py checks it again and unpacks it as root -- the
# same path /admin's Apps section uses later, which also keeps them current.
# Where each app comes from is in its manifest (apps.d/): a fork's Actions build, or for the
# calculators a git repository, cloned and adapted.
fetch_apps=()
if [ "$APPS_FROM_ACTIONS" = 1 ]; then
	fetch_apps+=(draw mermaid serial)
	[ "$WITH_COLLAB" = 1 ] && fetch_apps+=(room)
fi
[ "$WITH_TOOLS" = 1 ] && fetch_apps+=(tools)
if [ ${#fetch_apps[@]} -gt 0 ]; then
	install -d -o "$HUB_USER" -g "$HUB_USER" -m 755 "$STATE/library"
	for app in "${fetch_apps[@]}"; do
		say "Fetching the $app app"
		if zip="$(runuser -u "$HUB_USER" -- env HUB_STATE_DIR="$STATE" python3 "$CODE/librarian.py" app-fetch "$app")"; then
			HUB_STATE_DIR="$STATE" python3 "$CODE/hub_control.py" install-app "$app" "$zip"
		else
			echo "    could not fetch $app; its tile stays empty until it is updated from /admin"
		fi
	done
	runuser -u "$HUB_USER" -- env HUB_STATE_DIR="$STATE" python3 "$CODE/librarian.py" add-apps "${fetch_apps[@]}" >/dev/null
fi
# Calculators copied in by --apps are adapted here (fetched ones already were). Idempotent:
# a page already adapted is skipped.
if [ -d "$APPS/tools" ] && [ ! -f "$APPS/tools/irate-box-bundle.json" ]; then
	python3 "$CODE/adapt_tools.py" "$APPS/tools" "$CODE/static"
fi
for app in mermaid draw tools serial; do
	[ -d "$APPS/$app" ] || echo "    (no $app build installed: its card will lead to an empty page)"
done

# --- config ----------------------------------------------------------------------
# The admin password is chosen by the owner, in the browser, on first use: until then the
# logins get a random placeholder nobody knows (so /sync and /term stay shut), and
# $UNCLAIMED_MARK tells the web server and the hub to offer /admin/ as the set-the-password
# page. It sits beside the web server's config, where its workers can see it (/etc/hub cannot
# be read by them). --admin-password sets the password here instead, for scripted installs.
UNCLAIMED_MARK=/etc/$WEB/irate-box-unclaimed
UNCLAIMED=0
if [ -n "$ADMIN_PW" ]; then
	printf '%s\n' "$ADMIN_PW" >"$ETC/admin-password"
	chmod 600 "$ETC/admin-password"
elif [ -s "$ETC/admin-password" ]; then
	chmod 600 "$ETC/admin-password"
	ADMIN_PW="$(head -1 "$ETC/admin-password")"
else
	UNCLAIMED=1
	ADMIN_PW="$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
fi

cat >"$ETC/hub.env" <<EOF
# Read by irate-box.service. Changes take effect on: systemctl restart irate-box
PORT=8000
HUB_BIND=127.0.0.1
HUB_STATE_DIR=$STATE
HUB_URL=$HUB_URL
# The web server in front (install.sh --web) and its first-use mark, for /status and /admin.
HUB_WEB_SERVER=$WEB
HUB_UNCLAIMED_FILE=$UNCLAIMED_MARK
# The same roots the web server serves, so /status can tell a static app that is not
# installed from one that is.
HUB_DRAW_ROOT=$APPS/draw
HUB_MERMAID_ROOT=$APPS/mermaid
HUB_TOOLS_ROOT=$APPS/tools
HUB_SERIAL_ROOT=$APPS/serial
EOF

# When :80 is the owner's, the hub takes a port of its own (8080 first); recorded, so
# updates keep it.
pick_own_port() {
	local p
	for p in 8080 8088 8888; do port_free "$p" && { HUB_PORT=$p; return 0; }; done
	return 1
}
record_port() {
	sed -i '/^--port$/,+1d' "$ETC/install-options"
	printf '%s\n' --port "$HUB_PORT" >>"$ETC/install-options"
	install -m 644 "$ETC/install-options" "$STATE/install-options"
}

# --- moving between web servers ----------------------------------------------------
# The hub set up in the other server (--web named the other one, or this box predates nginx
# as the default) comes out of it first, so the two never both claim the port. Irate-box's
# own copy is stopped and disabled but stays installed, as the fallback: --web with its name
# brings it back. An owner's own server only loses the hub's file and carries on.
OTHER=caddy
[ "$WEB" = caddy ] && OTHER=nginx
if hub_site_in "$OTHER"; then
	say "Moving the hub from $OTHER to $WEB"
	if [ "$OTHER" = caddy ]; then
		if [ -f /etc/caddy/irate-box.caddy ]; then
			sed -i '/^# irate-box: the hub, on its own port/d; \|^import /etc/caddy/irate-box.caddy$|d' /etc/caddy/Caddyfile
			rm -f /etc/caddy/irate-box.caddy /etc/caddy/irate-box.caddy.prev
			systemctl try-restart caddy || true
			notice "The hub's site is out of the owner's Caddy (/etc/caddy/irate-box.caddy and its import line); Caddy carries on with the rest of its config."
		else
			systemctl disable --now caddy >/dev/null 2>&1 || true
			# The owner's Caddyfile back; failing that ours is set aside, so this box no longer
			# counts as served by Caddy (--web caddy writes a fresh one).
			if [ -f /etc/caddy/Caddyfile.pre-irate-box ]; then
				mv /etc/caddy/Caddyfile.pre-irate-box /etc/caddy/Caddyfile
			elif [ -f /etc/caddy/Caddyfile ]; then
				mv /etc/caddy/Caddyfile /etc/caddy/Caddyfile.irate-box-off
			fi
			notice "Caddy, which served the hub until now, is stopped and disabled. It stays installed as the fallback: rerun with --web caddy to go back to it."
		fi
		rm -f /etc/caddy/irate-box-unclaimed /etc/systemd/system/caddy.service.d/irate-box.conf
		rmdir /etc/systemd/system/caddy.service.d 2>/dev/null || true
	else
		rm -f "$NGINX_SITE" "$NGINX_SITE.prev" "$NGINX_LOGINS" /etc/nginx/irate-box-unclaimed
		if [ -f "$NGINX_DEFAULT_MARK" ]; then
			ln -sf ../sites-available/default /etc/nginx/sites-enabled/default
			rm -f "$NGINX_DEFAULT_MARK"
		fi
		if [ -f "$NGINX_OURS_MARK" ]; then
			systemctl disable --now nginx >/dev/null 2>&1 || true
			notice "nginx, which served the hub until now, is stopped and disabled. It stays installed: rerun with --web nginx to go back to it."
		else
			systemctl try-reload-or-restart nginx || true
			notice "The hub's site is out of the owner's nginx ($NGINX_SITE); nginx carries on with the rest of its config."
		fi
	fi
	# An owner's server may keep :80 for its own sites; then the hub moves.
	h="$(port_holder "$HUB_PORT")"
	if [ "$HUB_PORT" = 80 ] && [ -n "$h" ] && [ "$h" != "$WEB" ]; then
		pick_own_port || die "port 80 stays with $h, and 8080, 8088 and 8888 are taken too: pass --port N"
		record_port
		notice "Port 80 stays with $h, so the hub is on :$HUB_PORT. The hotspot's sign-in sheet needs :80, so it will not pop up."
	fi
fi

# --- the web server: nginx ---------------------------------------------------------
# The site goes in conf.d/, which every nginx layout (Debian's, nginx.org's) includes inside
# http {}; the rest of the config is left as it is.
#
# Logins: SHA-512-crypt, checked by the system's crypt(3). Not the bcrypt Caddy gets: nginx
# checks the hash on every request and keeps no cache, and bcrypt at cost 14 takes ~5.8 s of
# CPU per check on the Lyra, against 38 ms for this. The file is read on every request too,
# so a new password needs no reload.
NGINX_DEFAULT=/etc/nginx/sites-enabled/default
# The package's default site, enabled and untouched: it holds :80, so it is switched off
# (recorded, and put back by uninstall.sh). Edited, it is the owner's and stays.
nginx_default_untouched() {
	local f=/etc/nginx/sites-available/default shipped
	[ -L "$NGINX_DEFAULT" ] && [ "$(readlink -f "$NGINX_DEFAULT")" = "$f" ] || return 1
	shipped="$(dpkg-query -W -f='${Conffiles}\n' nginx-common nginx 2>/dev/null | awk -v f="$f" '$1 == f {print $2}' | head -1)"
	[ -n "$shipped" ] && [ "$(md5sum <"$f" | cut -d' ' -f1)" = "$shipped" ]
}
# Does any nginx config but the hub's listen on port $1? (A second default_server on a port
# fails nginx -t, but a plain listen would quietly hand the hub the owner's unmatched hosts.)
nginx_serves_port() {
	nginx -T 2>/dev/null | awk -v ours="$NGINX_SITE" -v p="$1" '
		/^# configuration file / { f = $4; sub(/:$/, "", f); next }
		f != ours && $1 == "listen" { a = $2; sub(/;$/, "", a); sub(/.*:/, "", a); if (a == p) found = 1 }
		END { exit !found }'
}
nginx_config() {
	echo "# Generated by irate-box install.sh from $CODE/irate-box.nginx. Edits are overwritten on reinstall."
	sed -e "s|@PORT@|$1|g" -e "s|@STATIC@|$CODE/static|g" -e "s|@APPS@|$APPS|g" \
		-e "s|@HTPASSWD@|$NGINX_LOGINS|g" -e "s|@UNCLAIMED@|$UNCLAIMED_MARK|g" "$CODE/irate-box.nginx" |
		# A kernel without IPv6: the [::] listener would stop nginx from starting at all.
		if [ -f /proc/net/if_inet6 ]; then cat; else sed '/listen \[::\]:/d'; fi
}
if [ "$WEB" = nginx ]; then
	say "Configuring nginx"
	ngx_user="$(sed -n 's/^[[:space:]]*user[[:space:]]\{1,\}\([^[:space:];]\{1,\}\).*/\1/p' /etc/nginx/nginx.conf 2>/dev/null | head -1)"
	ngx_group="$(id -gn "${ngx_user:-www-data}" 2>/dev/null || echo root)"
	hash="$(printf '%s\n' "$ADMIN_PW" | openssl passwd -6 -stdin)"
	( umask 077; printf 'admin:%s\n' "$hash" >"$NGINX_LOGINS.new" )
	chown "root:$ngx_group" "$NGINX_LOGINS.new"
	chmod 640 "$NGINX_LOGINS.new"
	mv "$NGINX_LOGINS.new" "$NGINX_LOGINS"
	removed_default=0
	if [ -e "$NGINX_DEFAULT" ] && nginx_default_untouched; then
		rm -f "$NGINX_DEFAULT"
		echo "the nginx package's default site, switched off by irate-box $(date -u +%Y-%m-%d); uninstall.sh turns it back on" >"$NGINX_DEFAULT_MARK"
		removed_default=1
	fi
	if [ "$HUB_PORT" = 80 ] && nginx_serves_port 80; then
		pick_own_port || die "the existing nginx config serves :80, and 8080, 8088 and 8888 are taken too: pass --port N"
		record_port
		notice "The existing nginx config already serves :80, so the hub is on :$HUB_PORT instead. The hotspot's sign-in sheet needs :80, so it will not pop up."
	fi
	[ -f "$NGINX_SITE" ] && cp "$NGINX_SITE" "$NGINX_SITE.prev"
	nginx_config "$HUB_PORT" >"$NGINX_SITE"
	if ! out="$(nginx -t 2>&1)"; then
		printf '%s\n' "$out" | tail -3 >&2
		if [ -f "$NGINX_SITE.prev" ]; then mv "$NGINX_SITE.prev" "$NGINX_SITE"; else rm -f "$NGINX_SITE"; fi
		if [ "$removed_default" = 1 ]; then
			ln -sf ../sites-available/default "$NGINX_DEFAULT"
			rm -f "$NGINX_DEFAULT_MARK"
		fi
		die "the hub's nginx site does not pass nginx -t with the rest of the config, which is left as it was"
	fi
	rm -f "$NGINX_SITE.prev"
fi

# --- the web server: Caddy (the fallback) ----------------------------------------------
if [ "$WEB" = caddy ]; then
	say "Configuring Caddy"
	HASH="$(caddy hash-password --plaintext "$ADMIN_PW")"
	CADDY_VER="$(caddy version | grep -oE '[0-9]+\.[0-9]+' | head -1)"
	CADDY_SITE=/etc/caddy/irate-box.caddy
	IMPORT_LINE="import $CADDY_SITE"
	# Whose Caddyfile is it? Ours (a previous run wrote it) or the package's untouched default:
	# ours to write. Anything else is the owner's, and stays theirs: the hub's site goes in its
	# own file, pulled in by one import line that uninstall.sh takes out again.
	caddyfile_owner() {
		local f=/etc/caddy/Caddyfile shipped
		[ -f "$f" ] || { echo default; return; }
		grep -q '^# Generated by irate-box' "$f" && { echo ours; return; }
		grep -qxF "$IMPORT_LINE" "$f" && { echo owner; return; }
		shipped="$(dpkg-query -W -f='${Conffiles}\n' caddy 2>/dev/null | awk '$1 == "/etc/caddy/Caddyfile" {print $2}')"
		[ -n "$shipped" ] && [ "$(md5sum <"$f" | cut -d' ' -f1)" = "$shipped" ] && { echo default; return; }
		echo owner
	}
	# The repo's Caddyfile with this box's login hash and port; with "site", the site block alone
	# (global options are the owner's business in their own Caddyfile).
	caddy_config() {
		local part="$1" port="$2"
		if [ "$part" = site ]; then
			echo "# Generated by irate-box install.sh from $CODE/Caddyfile and imported by /etc/caddy/Caddyfile. Edits are overwritten on reinstall."
			sed -n '/^:80 {/,$p' "$CODE/Caddyfile"
		else
			echo "# Generated by irate-box install.sh from $CODE/Caddyfile. Edits are overwritten on reinstall."
			cat "$CODE/Caddyfile"
		fi | sed -e "s|\$2a\$14\$REPLACE_ME_WITH_CADDY_HASH_PASSWORD_OUTPUT|$HASH|" -e "s|^:80 {|:$port {|" |
			# basic_auth is the 2.8+ spelling; older Caddy (Debian trixie ships 2.6) only knows basicauth.
			if [ "$(printf '%s\n' "$CADDY_VER" 2.8 | sort -V | head -1)" != 2.8 ]; then sed 's/\bbasic_auth\b/basicauth/'; else cat; fi
	}
	caddy_check() { caddy validate --adapter caddyfile --config "$1" 2>&1; }
	CADDY_MODE="$(caddyfile_owner)"
	if [ "$CADDY_MODE" = owner ]; then
		notice "Caddy is already set up here (/etc/caddy/Caddyfile is not irate-box's). It stays as it is: the hub's site goes in $CADDY_SITE, added with one line ($IMPORT_LINE), and uninstall.sh takes both out. Caddy restarts once, so its sites blink."
		[ -f "$CADDY_SITE" ] && cp "$CADDY_SITE" "$CADDY_SITE.prev"
		added_import=0
		if ! grep -qxF "$IMPORT_LINE" /etc/caddy/Caddyfile; then
			[ -z "$(tail -c1 /etc/caddy/Caddyfile)" ] || echo >>/etc/caddy/Caddyfile
			printf '# irate-box: the hub, on its own port or :80 (uninstall.sh removes these two lines)\n%s\n' "$IMPORT_LINE" >>/etc/caddy/Caddyfile
			added_import=1
		fi
		caddy_config site "$HUB_PORT" >"$CADDY_SITE"
		ok=0
		out="$(caddy_check /etc/caddy/Caddyfile)" && ok=1
		if [ "$ok" = 0 ] && [ "$HUB_PORT" = 80 ]; then
			# Most often the owner's config serves :80 itself; the hub then takes a port of its own.
			if pick_own_port; then
				caddy_config site "$HUB_PORT" >"$CADDY_SITE"
				out="$(caddy_check /etc/caddy/Caddyfile)" && ok=1
			fi
			if [ "$ok" = 1 ]; then
				notice "The existing Caddy config already serves :80, so the hub is on :$HUB_PORT instead. The hotspot's sign-in sheet needs :80, so it will not pop up."
				record_port
			fi
		fi
		if [ "$ok" = 0 ]; then
			printf '%s\n' "$out" | tail -3 >&2
			if [ -f "$CADDY_SITE.prev" ]; then mv "$CADDY_SITE.prev" "$CADDY_SITE"; else rm -f "$CADDY_SITE"; fi
			[ "$added_import" = 1 ] && sed -i '/^# irate-box: the hub, on its own port/d; \|^import /etc/caddy/irate-box.caddy$|d' /etc/caddy/Caddyfile
			die "the hub's Caddy site does not fit the existing Caddy config, which is left as it was"
		fi
		rm -f "$CADDY_SITE.prev"
	else
		[ -f /etc/caddy/Caddyfile ] && [ ! -f /etc/caddy/Caddyfile.pre-irate-box ] && [ "$CADDY_MODE" = default ] &&
			cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-irate-box
		caddy_config all "$HUB_PORT" >/etc/caddy/Caddyfile.new
		if ! out="$(caddy_check /etc/caddy/Caddyfile.new)"; then
			printf '%s\n' "$out" | tail -3 >&2
			die "generated Caddyfile does not validate (left at /etc/caddy/Caddyfile.new)"
		fi
		mv /etc/caddy/Caddyfile.new /etc/caddy/Caddyfile
	fi

	# The Caddyfile's roots are {$ENV:default} placeholders; point them at this layout.
	install -d /etc/systemd/system/caddy.service.d
	cat >/etc/systemd/system/caddy.service.d/irate-box.conf <<-EOF
	[Service]
	Environment=HUB_STATIC=$CODE/static
	Environment=HUB_MERMAID_ROOT=$APPS/mermaid
	Environment=HUB_DRAW_ROOT=$APPS/draw
	Environment=HUB_TOOLS_ROOT=$APPS/tools
	Environment=HUB_SERIAL_ROOT=$APPS/serial
	EOF
	# The Caddyfile turns the admin API off, so "systemctl reload caddy" fails; restart instead.
fi
if [ -n "$TAKE_UNIT" ]; then
	say "Taking port 80 from $TAKE_UNIT"
	python3 "$CODE/security.py" unit-off "$TAKE_UNIT" "install.sh --take-port-80" | sed 's/^/    /'
fi
if [ "$UNCLAIMED" = 1 ]; then
	echo "no admin password chosen yet (install.sh)" >"$UNCLAIMED_MARK"
	chmod 644 "$UNCLAIMED_MARK"
else
	rm -f "$UNCLAIMED_MARK"
fi


# --- hub service -----------------------------------------------------------------
cat >/etc/systemd/system/irate-box.service <<EOF
[Unit]
Description=Irate-Box hub
After=network.target

[Service]
User=$HUB_USER
Group=$HUB_USER
EnvironmentFile=$ETC/hub.env
ExecStart=/usr/bin/python3 $CODE/server.py
WorkingDirectory=$STATE
Restart=on-failure
ProtectSystem=full
ReadWritePaths=$STATE

[Install]
WantedBy=multi-user.target
EOF

# --- SilverBullet ----------------------------------------------------------------
if [ "$WITH_NOTES" = 1 ]; then
	if ! /usr/local/bin/silverbullet --version 2>/dev/null | grep -q "$SB_VERSION"; then
		say "Downloading SilverBullet $SB_VERSION ($SB_ARCH)"
		tmp="$(mktemp -d)"
		fetch "https://github.com/silverbulletmd/silverbullet/releases/download/$SB_VERSION/silverbullet-server-linux-$SB_ARCH.zip" \
			"$tmp/sb.zip" "silverbullet-$SB_VERSION-silverbullet-server-linux-$SB_ARCH.zip"
		unzip -q -o "$tmp/sb.zip" -d "$tmp"
		bin="$(find "$tmp" -type f -name silverbullet | head -1)"
		[ -n "$bin" ] || die "no silverbullet binary in the release zip"
		install -m 755 "$bin" /usr/local/bin/silverbullet
		rm -rf "$tmp"
	fi
	[ -f "$ETC/silverbullet.env" ] || cat >"$ETC/silverbullet.env" <<'EOF'
# Read by silverbullet.service. Guests can edit by default, like the board.
# To lock it down, uncomment one:
#SB_READ_ONLY=true
#SB_USER=user:password
EOF
	cat >/etc/systemd/system/silverbullet.service <<EOF
[Unit]
Description=SilverBullet notes for Irate-Box (/notes/)
After=network.target

[Service]
User=$HUB_USER
Group=$HUB_USER
# Must match the web server's route, which passes /notes through unstripped.
Environment=SB_URL_PREFIX=/notes
Environment=SB_HOSTNAME=127.0.0.1
Environment=SB_PORT=3000
Environment=SB_FOLDER=$STATE/notes
EnvironmentFile=-$ETC/silverbullet.env
ExecStart=/usr/local/bin/silverbullet
WorkingDirectory=$STATE
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
fi

# --- Syncthing -------------------------------------------------------------------
if [ "$WITH_SYNC" = 1 ]; then
	install -d /etc/systemd/system/syncthing@$HUB_USER.service.d
	cat >/etc/systemd/system/syncthing@$HUB_USER.service.d/irate-box.conf <<EOF
[Service]
# No ~/Sync "Default Folder"; the installer shares the notes folder instead.
Environment=STNODEFAULTFOLDER=1
Environment=HOME=$STATE
ExecStart=
ExecStart=/usr/bin/syncthing serve --no-browser --no-restart --gui-address=127.0.0.1:8384 --logflags=0
EOF
fi

# --- Kiwix -----------------------------------------------------------------------
if [ ${#ZIMS[@]} -gt 0 ]; then
	say "Adding ZIMs to the Kiwix library"
	install -d -o "$HUB_USER" -g "$HUB_USER" "$STATE/zim"
	for z in "${ZIMS[@]}"; do
		case "$z" in
		http://* | https://*)
			f="$STATE/zim/$(basename "${z%%\?*}")"
			if [ ! -s "$f" ]; then
				# -C - resumes a .part left by an earlier, interrupted run.
				curl -fL --retry 5 -C - -o "$f.part" "$z"
				mv "$f.part" "$f"
			fi
			;;
		*)
			[ -f "$z" ] || die "--zim: no such file: $z"
			f="$STATE/zim/$(basename "$z")"
			[ "$(realpath "$z")" = "$f" ] || cp "$z" "$f"
			;;
		esac
		chown "$HUB_USER:$HUB_USER" "$f"
	done
fi

# Written whenever Kiwix is in use, not only when --zim adds a book, so a rerun brings an
# existing box's unit up to date (the memory settings below arrived after first installs).
if [ ${#ZIMS[@]} -gt 0 ] || [ -f /etc/systemd/system/kiwix.service ]; then
	cat >/etc/systemd/system/kiwix.service <<EOF
[Unit]
Description=Kiwix offline library for Irate-Box (/wiki/)
After=network.target

[Service]
User=$HUB_USER
Group=$HUB_USER
# --urlRootLocation must match the web server's route, which passes /wiki through unstripped.
# --monitorLibrary picks up books added later with kiwix-manage, without a restart.
# Memory: libzim keeps decompressed clusters per worker thread. Measured on the Lyra
# (2026-09-30, top-mini Wikipedia): defaults grow to ~104 MB anon after searching;
# 2 threads + 4 cached clusters hold ~22 MB with no loss in search latency.
# ZIM_CLUSTERCACHE is a cluster *count*, not bytes - a large value OOMs the box.
Environment=ZIM_CLUSTERCACHE=4
ExecStart=/usr/bin/kiwix-serve --threads 2 --library --monitorLibrary --blockexternal --nodatealiases --address 127.0.0.1 --port 8081 --urlRootLocation /wiki $STATE/zim/library.xml
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
fi

# The sweep: library.xml is rebuilt from the .zim files actually in $STATE/zim, every
# run. kiwix-manage keys books by the ZIM's UUID, so editing the old library in place
# leaves a stale entry beside every rebuilt file, and keeps entries for deleted ones.
# Adding or removing a book is therefore: change the files, rerun the installer.
SWEPT=0
if compgen -G "$STATE/zim/*.zim" >/dev/null; then
	say "Rebuilding the Kiwix library from $STATE/zim"
	lib="$STATE/zim/library.xml"
	rm -f "$lib.new"
	for f in "$STATE"/zim/*.zim; do
		runuser -u "$HUB_USER" -- kiwix-manage "$lib.new" add "$f" ||
			echo "    skipped $(basename "$f"): kiwix-manage could not read it"
	done
	[ -f "$lib.new" ] && mv "$lib.new" "$lib"
	SWEPT=1
fi

# --- MQTT broker -------------------------------------------------------------------
# Meshtastic nodes speak raw MQTT over TCP, which the web server does not proxy, so :1883
# is a second listener on the network: with Syncthing's ports, the exception to the web
# server being the only one. Browsers never see it; they use WebSockets on loopback,
# which the web server serves at /mqtt on the one origin. There is nobody to authenticate on an
# open network, so the limits below are the abuse control (plan, mqtt-and-node-red):
# anonymous clients reach msh/# and nothing else, messages are small, and nothing is
# persisted to the SD card. Debian's mosquitto.conf reads conf.d/ after its own
# "persistence true", so the "false" here is the one that counts.
if [ "$WITH_MQTT" = 1 ]; then
	cat >/etc/mosquitto/conf.d/irate-box.conf <<EOF
# Generated by irate-box install.sh. Edits are overwritten on reinstall.
per_listener_settings false
persistence false
allow_anonymous true
acl_file /etc/mosquitto/irate-box.acl
# A Meshtastic ServiceEnvelope is a few hundred bytes; the default is unlimited.
message_size_limit 4096
max_queued_messages 100
max_inflight_messages 20

# Nodes and the phone app (MQTT Client Proxy). Every interface until the installer
# sets up the access point; then this should become the AP address only.
listener 1883
max_connections 64

# Pages, through the web server at /mqtt.
listener 9001 127.0.0.1
protocol websockets
max_connections 64
EOF
	cat >/etc/mosquitto/irate-box.acl <<'EOF'
# Anonymous clients (every client: there are no users) may use the Meshtastic root
# topic and nothing else, so the broker is not a free message bus for anything else.
topic readwrite msh/#
EOF
	# Owned by mosquitto and not world-readable, or later versions refuse to load it.
	chown mosquitto:mosquitto /etc/mosquitto/irate-box.acl
	chmod 640 /etc/mosquitto/irate-box.acl
fi

# --- Excalidraw live collaboration ---------------------------------------------------
# excalidraw-room is a socket.io relay: no database, rooms in memory, and scenes and
# pasted files persisted through store.py like everything else. Built on a desktop
# (dist/ plus production node_modules/, all plain JS) and copied in; nothing compiles
# here. Measured on the Lyra: ~8 MB anon idle, ~19 MB with twelve busy clients.
if [ "$WITH_COLLAB" = 1 ]; then
	if [ -f "${APPS_SRC:-/nonexistent}/room/dist/index.js" ]; then
		say "Installing the collaboration relay from $APPS_SRC/room"
		rm -rf "${ROOM:?}"
		install -d -m 755 "$(dirname "$ROOM")"
		cp -a "$APPS_SRC/room" "$ROOM"
		rm -rf "$ROOM/.git"
		chown -R root:root "$ROOM"
	fi
	cat >/etc/systemd/system/excalidraw-room.service <<EOF
[Unit]
Description=Excalidraw collaboration relay for Irate-Box (/socket.io/ for /draw/)
After=network.target

[Service]
DynamicUser=yes
Environment=NODE_ENV=production PORT=3002 HOST=127.0.0.1
WorkingDirectory=$ROOM
# Heap capped well below the cgroup limit, so V8 collects before systemd has to act.
ExecStart=/usr/bin/node --max-old-space-size=48 $ROOM/dist/index.js
Restart=on-failure
MemoryHigh=80M
MemoryMax=128M
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
fi

# --- ttyd --------------------------------------------------------------------------
if ! /usr/local/bin/ttyd --version 2>/dev/null | grep -q "$TTYD_VERSION"; then
	say "Downloading ttyd $TTYD_VERSION ($TTYD_ARCH)"
	tmp="$(mktemp -d)"
	base="https://github.com/tsl0922/ttyd/releases/download/$TTYD_VERSION"
	fetch "$base/ttyd.$TTYD_ARCH" "$tmp/ttyd.$TTYD_ARCH" "ttyd-$TTYD_VERSION-ttyd.$TTYD_ARCH"
	fetch "$base/SHA256SUMS" "$tmp/SHA256SUMS" "ttyd-$TTYD_VERSION-SHA256SUMS"
	(cd "$tmp" && grep " ttyd.$TTYD_ARCH\$" SHA256SUMS | sha256sum -c --quiet) ||
		die "ttyd.$TTYD_ARCH does not match its published checksum"
	install -m 755 "$tmp/ttyd.$TTYD_ARCH" /usr/local/bin/ttyd
	rm -rf "$tmp"
fi
# Same credential as the web server's gate, so the Basic-auth header it passes on
# satisfies ttyd too; the terminal itself is /bin/login, so a real account is still needed.
( umask 077; printf 'TTYD_CREDENTIAL=admin:%s\n' "$ADMIN_PW" >"$ETC/ttyd.env" )
cat >/etc/systemd/system/ttyd.service <<EOF
[Unit]
Description=ttyd terminal for Irate-Box (/term/), off unless install.sh --with-term
After=network.target

[Service]
EnvironmentFile=$ETC/ttyd.env
ExecStart=/usr/local/bin/ttyd --interface lo --port 7681 --base-path /term --credential \${TTYD_CREDENTIAL} --writable /bin/login
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

# --- admin control (root helper) ---------------------------------------------------
# /admin starts and stops services and changes the admin password, which need root. The
# hub only queues a request in $STATE/control/requests/; this path unit runs hub_control.py
# as root, which acts on its own allow-list only and answers in $STATE/control/results/.
install -d -o "$HUB_USER" -g "$HUB_USER" -m 700 "$STATE/control" "$STATE/control/requests"
install -d -o "$HUB_USER" -g "$HUB_USER" -m 755 "$STATE/control/results"
cat >/etc/systemd/system/irate-box-control.service <<EOF
[Unit]
Description=Irate-Box: carry out /admin requests that need root (services, admin password)

[Service]
Type=oneshot
Environment=HUB_STATE_DIR=$STATE HUB_ETC_DIR=$ETC HUB_USER=$HUB_USER HUB_CODE_DIR=$CODE HUB_WEB_SERVER=$WEB HUB_UNCLAIMED_FILE=$UNCLAIMED_MARK
ExecStart=/usr/bin/python3 $CODE/hub_control.py
# "Install update" reruns install.sh from here, which can take a while on a small board.
TimeoutStartSec=50min
EOF
cat >/etc/systemd/system/irate-box-control.path <<EOF
[Unit]
Description=Irate-Box: watch for /admin requests that need root

[Path]
DirectoryNotEmpty=$STATE/control/requests
Unit=irate-box-control.service

[Install]
WantedBy=multi-user.target
EOF

# --- the librarian -----------------------------------------------------------------
# librarian.py keeps ZIM books current from the sources set on /admin (Library). It runs
# as the hub user: a new version is swapped in under the same file name and library.xml is
# rebuilt, which kiwix-serve's --monitorLibrary picks up. No restart, so no root needed.
install -d -o "$HUB_USER" -g "$HUB_USER" -m 755 "$STATE/zim" "$STATE/library"
cat >/etc/systemd/system/irate-box-librarian.service <<EOF
[Unit]
Description=Irate-Box librarian: update the ZIM books whose check is due (/admin, Library)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$HUB_USER
Group=$HUB_USER
Environment=HUB_STATE_DIR=$STATE
ExecStart=/usr/bin/python3 $CODE/librarian.py update --scheduled
# A download competes with guests for the card and the CPU; let it lose.
Nice=10
IOSchedulingClass=idle
ProtectSystem=full
ReadWritePaths=$STATE
EOF
cat >/etc/systemd/system/irate-box-librarian.timer <<EOF
[Unit]
Description=Irate-Box librarian, hourly (each source is checked only when its policy says so)

[Timer]
OnBootSec=15min
OnUnitActiveSec=1h
RandomizedDelaySec=5min

[Install]
WantedBy=timers.target
EOF

# --- Tailscale remote access -------------------------------------------------------
# Not installed by this script. Where it is already present, it stops being a boot
# service and comes under the switch on /admin: the hub records the choice in
# $STATE/tailscale.want, and the path unit applies it as root (tailscale-apply.sh).
# Its current state is never changed here -- this may be running over Tailscale.
WITH_TAILSCALE=0
if systemctl cat tailscaled.service >/dev/null 2>&1; then
	WITH_TAILSCALE=1
	say "Putting Tailscale under the remote-access switch on /admin"
	if [ ! -f "$STATE/tailscale.want" ]; then
		if systemctl is-active --quiet tailscaled; then echo on; else echo off; fi \
			>"$STATE/tailscale.want"
		chown "$HUB_USER:$HUB_USER" "$STATE/tailscale.want"
	fi
	install -d /etc/systemd/system/tailscaled.service.d
	cat >/etc/systemd/system/tailscaled.service.d/irate-box.conf <<'EOF'
[Service]
# Same as tailscaled --no-logs-no-support: an offline box should not keep trying to
# upload logs. Takes effect the next time tailscaled starts.
Environment=TS_NO_LOGS_NO_SUPPORT=true
EOF
	cat >/etc/systemd/system/irate-box-tailscale.service <<EOF
[Unit]
Description=Irate-Box: apply the remote-access switch ($STATE/tailscale.want)

[Service]
Type=oneshot
Environment=HUB_STATE_DIR=$STATE
ExecStart=$CODE/tailscale-apply.sh
EOF
	cat >/etc/systemd/system/irate-box-tailscale.path <<EOF
[Unit]
Description=Irate-Box: watch the remote-access switch

[Path]
PathChanged=$STATE/tailscale.want
Unit=irate-box-tailscale.service

[Install]
WantedBy=multi-user.target
EOF
	cat >/etc/systemd/system/irate-box-tailscale-boot.service <<EOF
[Unit]
Description=Irate-Box: remote access at boot (on stays on; a timed session ends)
After=network.target

[Service]
Type=oneshot
Environment=HUB_STATE_DIR=$STATE
ExecStart=$CODE/tailscale-apply.sh --boot

[Install]
WantedBy=multi-user.target
EOF
fi

# --- start -----------------------------------------------------------------------
say "Starting services"
systemctl daemon-reload
units=(irate-box "$WEB")
[ "$WITH_NOTES" = 1 ] && units+=(silverbullet)
[ "$WITH_SYNC" = 1 ] && units+=("syncthing@$HUB_USER")
[ ${#ZIMS[@]} -gt 0 ] && units+=(kiwix)
[ "$WITH_TERM" = 1 ] && units+=(ttyd)
[ "$WITH_MQTT" = 1 ] && units+=(mosquitto)
[ "$WITH_COLLAB" = 1 ] && units+=(excalidraw-room)
systemctl enable --quiet "${units[@]}"
systemctl restart "${units[@]}"
# Turned on by an earlier run: keep it on, with the current credential.
[ "$WITH_TERM" = 1 ] || systemctl try-restart ttyd
# A ZIM replaced under the same name stays open in kiwix-serve until it restarts;
# --monitorLibrary only notices library.xml changing, not the files it points at.
[ ${#ZIMS[@]} -gt 0 ] || [ "$SWEPT" = 0 ] || systemctl try-restart kiwix
systemctl enable --quiet --now irate-box-librarian.timer irate-box-control.path
if [ "$WITH_TAILSCALE" = 1 ]; then
	# The boot unit decides from now on. Not started here: its state is left as found.
	systemctl disable --quiet tailscaled
	systemctl enable --quiet irate-box-tailscale-boot.service
	systemctl enable --quiet --now irate-box-tailscale.path
fi

if [ "$WITH_SYNC" = 1 ]; then
	say "Configuring Syncthing for an offline box"
	st() { runuser -u "$HUB_USER" -- env HOME="$STATE" syncthing cli "$@"; }
	for _ in $(seq 60); do st show system >/dev/null 2>&1 && break; sleep 1; done
	st show system >/dev/null 2>&1 || die "Syncthing did not come up (journalctl -u syncthing@$HUB_USER)"
	# None of these can reach anything without an uplink; local discovery stays on.
	st config options global-ann-enabled set false
	st config options relays-enabled set false
	st config options natenabled set false
	st config options uraccepted set -- -1
	st config options set-low-priority set true
	st config options raw-max-folder-concurrency set 1
	# Same credentials as the web server's gate, so the one Basic-auth prompt satisfies both.
	st config gui user set admin
	st config gui password set "$ADMIN_PW"
	if [ "$WITH_NOTES" = 1 ] && ! st config folders list | grep -qx hub-notes; then
		st config folders add --id hub-notes --label "Hub notes" --path "$STATE/notes"
	fi
fi

# --- add-ons taken off ------------------------------------------------------------
# --remove: the service stops and its unit and config go. Data and packages stay, so adding
# it back finds the notes, Syncthing's identity and so on where they were.
for r in "${REMOVE[@]}"; do
	case "$r" in
	notes)
		say "Removing the notes add-on (SilverBullet); $STATE/notes stays"
		systemctl disable --now silverbullet >/dev/null 2>&1 || true
		rm -f /etc/systemd/system/silverbullet.service /usr/local/bin/silverbullet ;;
	sync)
		say "Removing the sync add-on (Syncthing); its state in $STATE stays"
		systemctl disable --now "syncthing@$HUB_USER" >/dev/null 2>&1 || true
		rm -f "/etc/systemd/system/syncthing@$HUB_USER.service.d/irate-box.conf"
		rmdir "/etc/systemd/system/syncthing@$HUB_USER.service.d" 2>/dev/null || true ;;
	mqtt)
		say "Removing the MQTT add-on (mosquitto's irate-box config)"
		systemctl disable --now mosquitto >/dev/null 2>&1 || true
		rm -f /etc/mosquitto/conf.d/irate-box.conf /etc/mosquitto/irate-box.acl ;;
	term)
		say "Turning the terminal off (ttyd stays installed, off)"
		systemctl disable --now ttyd >/dev/null 2>&1 || true ;;
	collab)
		say "Removing the collaboration add-on (the relay's service; Node.js stays)"
		systemctl disable --now excalidraw-room >/dev/null 2>&1 || true
		rm -f /etc/systemd/system/excalidraw-room.service ;;
	esac
done
[ ${#REMOVE[@]} -eq 0 ] || systemctl daemon-reload

# --- check -----------------------------------------------------------------------
sleep 2
fail=0
for u in "${units[@]}"; do
	systemctl is-active --quiet "$u" || { echo "    $u is not running: journalctl -u $u"; fail=1; }
done
hostport="127.0.0.1$([ "$HUB_PORT" = 80 ] || echo ":$HUB_PORT")"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://$hostport/" || true)"
[ "$code" = 200 ] || { echo "    http://$hostport/ answered $code"; fail=1; }

addr="$(ip -4 -o route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' || hostname -I | cut -d' ' -f1)"
where="http://${addr:-<this box>}$([ "$HUB_PORT" = 80 ] || echo ":$HUB_PORT")"
echo
say "Irate-Box is $([ $fail = 0 ] && echo up || echo 'installed, with problems above') at $where/"
echo "    web server: $WEB$([ "$WEB" = caddy ] && echo ' (the fallback; --web nginx switches to the default)')"
if [ "$UNCLAIMED" = 1 ]; then
	echo "    admin login: not chosen yet. Open $where/admin/ and set it now:"
	echo "                 until then, the first person on this network to open that page can."
else
	echo "    admin login: admin / (in $ETC/admin-password)"
fi
[ "$WITH_NOTES" = 1 ] && echo "    notes: /notes/  (folder $STATE/notes, lock it down in $ETC/silverbullet.env)"
[ -f "$STATE/zim/library.xml" ] && echo "    wiki:  /wiki/   ($(grep -c '<book ' "$STATE/zim/library.xml") books in $STATE/zim/library.xml)"
[ -f /etc/mosquitto/conf.d/irate-box.conf ] && echo "    mqtt:  :1883 for nodes, /mqtt for pages (anonymous, msh/# only)"
term_state="$(systemctl is-enabled ttyd 2>/dev/null)" || true
echo "    term:  /term/   (${term_state:-disabled}; admin login, then an account on the box)"
[ "$WITH_SYNC" = 1 ] && echo "    sync:  /sync/   (behind the admin login; folder \"hub-notes\" shared if notes are installed)"
[ "$WITH_COLLAB" = 1 ] && echo "    collab: live sessions in /draw/ (relay on 127.0.0.1:3002, via /socket.io/)"
[ -f "$STATE/library/sources.json" ] && echo "    library: $(grep -c "\"name\":" "$STATE/library/sources.json") sources kept current; settings on /admin"
[ "$WITH_TAILSCALE" = 1 ] && echo "    remote: Tailscale is $(systemctl is-active tailscaled); switch it on /admin"

# What install.sh found and did not change: said once more here, then the Security page's
# own report of the box. Nothing below is changed without the owner's say-so (/admin).
if [ "${#NOTICES[@]}" -gt 0 ]; then
	echo
	say "Worth knowing"
	for n in "${NOTICES[@]}"; do echo "    $n"; done
fi
if report="$(timeout 120 python3 "$CODE/security.py" summary 2>/dev/null)" && [ -n "$report" ]; then
	echo
	say "Found on this box (fix or leave each on $where/admin/#security)"
	printf '%s\n' "$report" | sed 's/^/    /'
fi
exit $fail
