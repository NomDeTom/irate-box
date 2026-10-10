#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 NomDeTom
# Irate-Box installer for Debian-family boards: Armbian, and mPWRD-OS (which is an
# Armbian build). Plain Debian and Raspberry Pi OS should work too; they are not tested.
#
# What it sets up:
#   /opt/irate-box            the hub (irate_box/, web/, config/), copied from a checkout or cloned
#   /var/lib/hub              state: messages, board, store, clock; notes/ for the add-ons
#   /usr/share/hub/apps/      prebuilt static apps: mermaid/, draw/, tools/, serial/
#   /var/lib/hub/addons/      the local add-ons (from /admin, Add-ons), on their own port: 8090
#   /var/lib/hub/zim/         --zim: ZIM files and the Kiwix library.xml
#   /etc/hub/                 hub.env, admin-password
#   /etc/nginx/conf.d/irate-box.conf   the front on :80, from the repo's irate-box.nginx
#                             (--web caddy: /etc/caddy/Caddyfile from the repo's Caddyfile)
#   irate-box.service         server.py on 127.0.0.1:8000 as the `hub` user
#   silverbullet.service      --with-notes: SilverBullet on a socket in /run/silverbullet, under /notes/
#   syncthing@hub.service     --with-sync: Syncthing GUI on 127.0.0.1:8384 under /sync/
#   kiwix.service             --zim: kiwix-serve on 127.0.0.1:8081 under /wiki/
#   ttyd.service              ttyd on /run/ttyd/ttyd.sock under /term/; enabled by --with-term only
#   mosquitto.service         --with-mqtt: MQTT on :1883, and WebSockets on 127.0.0.1:9001 at /mqtt
#   ngircd.service            --with-irc: an IRC server on :6667 (ngIRCd), for chat in any IRC app
#   excalidraw-room.service   --with-collab: live Excalidraw sessions on 127.0.0.1:3002 (/socket.io/)
#   irate-box-tailscale.path  if Tailscale is installed: its on/off switch on /admin
#   irate-box-git.socket      git http-backend and cgit as the hub user, for /git/ (public:
#                             browse and clone for all, push with the admin login) and
#                             /git-private/ (all behind the login); repos in /var/lib/hub/git
#   irate-box-ci.path         builds on push to a private repo with a .irate-ci.sh (ci.py), run
#                             by irate-box-ci.service as the unprivileged hubci user
#   irate-box-uplink.service  the uplink watchdog (uplink.py, root): keeps the box on its network
#                             as eagerly as --uplink or /admin's Network page says
#   irate-box-crashwatch.service  crash watch (crashwatch.py, root): snapshots to the box's storage,
#                             a crash filed at the next boot, a failing radio pre-empted as far as
#                             the box doctor's setting says
#   irate-box-visitors.service  unique visitors (visitors.py, unprivileged): devices' addresses
#                             hashed with daily and weekly salts held in memory, only the counts
#                             written; runs only while counting is on (irate-box-visitors-switch.path)
#   /var/lib/hub/control/netinv.json   what the box has for networking (netinv.py), looked
#                             at once here and again from /admin
#
# It does not start the hotspot: the box keeps whatever network it already has, and the hub
# is served at http://<its address>/. The hotspot is switched on from /admin → Network
# (root/ap.py), which fits it to the radios it finds; dnsmasq-base is installed for it.
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
                        DIR/serial, DIR/flasher (the web flasher, from the fork's
                        .github/irate-box/package.sh)
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
  --with-kiwix          install Kiwix, the offline library at /wiki/, even before any book (a box with
                        books gets it anyway, unless it was taken out with --remove kiwix)
  --with-irc            install ngIRCd, a small IRC server: :6667 for any IRC app, with a
                        #lobby channel ready. Open to everyone on the box's network, with no
                        accounts and no encryption; hosts are hidden from other users.
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
  --remove NAME         take an add-on off again: notes, sync, mqtt, irc, term, collab or kiwix
                        (repeatable). Its service stops and its unit and config go; its
                        data (the notes folder, Syncthing's state) and packages stay. This
                        is what /admin's Add-ons page runs.
  --take-port-80        when something else serves :80, stop and disable it so the hub can
                        have the port (the captive portal needs it). Recorded, so /admin's
                        Security page can undo it and uninstall.sh starts it again.
  --uplink K=V[,K=V]    how the box works to stay on its network (uplink.py): pace gentle
                        (default), steady, prompt or urgent; reach watch, reconnect, restart,
                        radio or reboot (default); sensitivity 1-20 missed checks within the
                        pace's window (default 3); guests protect (default) or ignore; on_wedge ladder (default) or
                        radio. E.g. --uplink pace=steady,reach=radio. (The old single level, e.g.
                        standard,strict, is still read.) Kept in /etc/hub/uplink.json, not in the
                        install options, so an update never undoes a choice made on /admin's
                        Network page, which also has the custom values. The owner's own WiFi profile is never
                        changed here: "Keep retrying" on that page does it, by consent.
  --rtc auto|off        a battery-backed clock module on I2C (DS3231, RV-8803, RX8130, …;
                        rtc.py). auto (default): look for one, and if exactly one is found set
                        it up, so the box keeps its time while off (with the kernel's driver
                        where it has one, otherwise irate-box's own); a module set up earlier
                        is kept. No device-tree or /boot changes. off: do not look. Health on
                        /admin looks again, sets one up, or stops using it.
  --download-cache DIR  take release downloads (ttyd, SilverBullet, Caddy's .deb) from DIR
                        when they are there, still checked against their checksums. /admin's
                        "Check for updates" fills it, so "Install update" needs no network
                        for anything already installed.
  --make-offline-bundle DIR
                        do not install: make DIR a kit that sets up a box with no internet.
                        It holds this code, the apps (the forks' builds and the calculators,
                        fetched and checked as the librarian does; with --apps DIR, those too,
                        e.g. a flasher you built), ttyd and SilverBullet for each --arch,
                        Caddy's .deb with --web caddy, any --zim, checksums, and setup.sh, which
                        runs this installer from the kit (sudo DIR/setup.sh [options]). No root
                        needed, nothing on this machine changes. Debian's own packages (nginx,
                        cgit, ...) still come from the box's apt.
  --arch LIST           with --make-offline-bundle: the boxes' architectures, comma-separated
                        (aarch64, armv7l, armv6l, x86_64; default aarch64,armv7l)
  -h, --help            this text
EOF
}

SRC="" REPO="https://github.com/NomDeTom/irate-box" BRANCH="main" APPS_SRC="" DL_CACHE=""
MAKE_BUNDLE="" BUNDLE_ARCHS="aarch64,armv7l"
APPS_FROM_ACTIONS=0 WITH_TOOLS=0 WITH_NOTES=0 WITH_SYNC=0 WITH_TERM=0 WITH_MQTT=0 WITH_IRC=0 WITH_COLLAB=0 WITH_KIWIX=0 ADMIN_PW="" HUB_URL="/" ZIMS=()
HUB_PORT="" TAKE_PORT_80=0 REMOVE=() WEB="" UPLINK="" RTC=auto
# The arguments as given, for the install record, with the password masked.
ARGS_SHOWN="" _mask=0
for _a in "$@"; do
	if [ "$_mask" = 1 ]; then ARGS_SHOWN+=" ***"; _mask=0; continue; fi
	ARGS_SHOWN+=" $_a"
	[ "$_a" = --admin-password ] && _mask=1
done
ARGS_SHOWN="${ARGS_SHOWN# }"
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
	# ELIZA was an option here; it is an add-on from /admin's catalogue now. Still
	# accepted, so an older install record replays.
	--with-eliza) shift ;;
	--with-mqtt) WITH_MQTT=1; shift ;;
	--with-irc) WITH_IRC=1; shift ;;
	--with-kiwix) WITH_KIWIX=1; shift ;;
	--with-collab) WITH_COLLAB=1; shift ;;
	--admin-password) ADMIN_PW="$2"; shift 2 ;;
	--hub-url) HUB_URL="$2"; shift 2 ;;
	--port) HUB_PORT="$2"; shift 2 ;;
	--web)
		case "$2" in nginx | caddy) WEB="$2" ;; *) echo "--web takes nginx or caddy" >&2; exit 2 ;; esac
		shift 2 ;;
	--take-port-80) TAKE_PORT_80=1; shift ;;
	--rtc)
		case "$2" in auto | off) RTC="$2" ;; *) echo "--rtc takes auto or off" >&2; exit 2 ;; esac
		shift 2 ;;
	--uplink)
		# KEY=VALUE pairs (uplink.py checks each again), or the old single level E[,F].
		if [[ "$2" =~ ^(pace|reach|sensitivity|forgiveness|guests|on_wedge|ignore_roams)=[a-z0-9]+(,(pace|reach|sensitivity|forgiveness|guests|on_wedge|ignore_roams)=[a-z0-9]+)*$ ]] ||
			[[ "$2" =~ ^(off|patient|standard|persistent|stubborn)(,(tolerant|normal|strict))?$ ]]; then
			UPLINK="$2"
		else
			echo "--uplink takes KEY=VALUE pairs (pace, reach, sensitivity, guests, on_wedge, ignore_roams=yes|no), e.g. pace=steady,reach=radio,sensitivity=4" >&2; exit 2
		fi
		shift 2 ;;
	--remove)
		case "$2" in notes | sync | mqtt | irc | term | collab | kiwix) REMOVE+=("$2") ;; eliza) ;; *) die "--remove takes notes, sync, mqtt, irc, term, collab or kiwix" ;; esac
		shift 2 ;;
	--download-cache) DL_CACHE="$2"; shift 2 ;;
	--make-offline-bundle) MAKE_BUNDLE="$2"; shift 2 ;;
	--arch) BUNDLE_ARCHS="$2"; shift 2 ;;
	-h | --help) usage; exit 0 ;;
	*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
	esac
done
# Books from the network over HTTPS only: a book swapped on the way has its pages served
# on the hub's own origin.
for z in ${ZIMS[@]+"${ZIMS[@]}"}; do
	case "$z" in http://*) echo "install.sh: --zim $z: use https:// (a book fetched over plain HTTP can be swapped on the way)" >&2; exit 2 ;; esac
done

# An add-on being removed is not installed by this run, whatever else asks for it.
for r in "${REMOVE[@]}"; do
	case "$r" in
	notes) WITH_NOTES=0 ;; sync) WITH_SYNC=0 ;; mqtt) WITH_MQTT=0 ;; irc) WITH_IRC=0 ;; term) WITH_TERM=0 ;; collab) WITH_COLLAB=0 ;; kiwix) WITH_KIWIX=0 ;;
	esac
done

HUB_USER=hub
CODE=/opt/irate-box
STATE=/var/lib/hub
APPS=/usr/share/hub/apps
SB_SOCKET=/run/silverbullet/silverbullet.sock  # --with-notes: SilverBullet listens here
TTYD_SOCKET=/run/ttyd/ttyd.sock  # --with-term: ttyd listens here
# The local add-ons: their own origin, this port of the web server,
# serving $STATE/addons/<id>/; their manifests in $STATE/apps.d/. Both the hub's, so adding one
# from /admin needs no root.
ADDON_PORT=8090
GIT_PORT=8093  # cgit's web view, its own origin; clones and pushes stay on the hub's port
WIKI_PORT=8092  # Kiwix's own origin; /wiki/ on the hub's port redirects there
NOTES_PORT=8091  # SilverBullet's own origin; /notes/ on the hub's port redirects there
# HTTPS: each origin's TLS twin, listening only once the box has a
# certificate (/admin → Security, HTTPS; root/tls.py writes the includes in $ETC/tls/front).
TLS_PORT=443
ADDON_TLS_PORT=8490
NOTES_TLS_PORT=8491
WIKI_TLS_PORT=8492
GIT_TLS_PORT=8493
ROOM=/usr/share/hub/room
ETC=/etc/hub
SB_VERSION=2.11.1
# SilverBullet's release zips, as GitHub records them (the release's asset digests).
# A new SB_VERSION needs these from: gh api repos/silverbulletmd/silverbullet/releases/tags/VERSION
SB_SHA256_x86_64=82af0d5d008c377cdb4b49dbd21db0d21f8d14619efa40b0fdeae1dce778d018
SB_SHA256_aarch64=4ca826b88431fcbe8d4c8f4d7d3f8cc3b076d42f724a40b6dc4d45a676d9dd87
SB_SHA256_armv7=fb27b0cf7af3d95d03ccfbeb25c4260ce5228d6b8402d531f84535bafbe25855
# Its manual (the repository's docs/ at the release's commit), packed reproducibly by library/sbdocs.py:
# the commit, and the sha256 of the tar the pack holds. A new SB_VERSION needs both:
#   gh api repos/silverbulletmd/silverbullet/git/ref/tags/VERSION --jq .object.sha
#   ./irate-box sbdocs fetch COMMIT /tmp/d.tar.gz && ./irate-box sbdocs sum /tmp/d.tar.gz
SB_DOCS_COMMIT=1340f7369e782a9c99e898de75cd588051fdb7cd
SB_DOCS_SHA256=ebc4f8134b98d8a21596f874b580c71f75ed6e3801d69534ea68bff3f493f280
SB_DOCS_NAME="silverbullet-docs-$SB_VERSION.tar.gz"
TTYD_VERSION=1.7.7
# ESP Web Tools (ESPHome's install button, Apache-2.0), the light web flasher's engine: npm's tarball,
# whose dist/web/ is the whole thing bundled, served at /flasher/esp-web-tools/. A new EWT_VERSION
# needs its digest: curl -sL https://registry.npmjs.org/esp-web-tools/-/esp-web-tools-VERSION.tgz | sha256sum
EWT_VERSION=10.4.0
EWT_SHA256=f18da75335d2f0dca044c4bb052848c0696e7b03cc23d61d8530dd6eba0a9008
EWT_NAME="esp-web-tools-$EWT_VERSION.tgz"
EWT_URL="https://registry.npmjs.org/esp-web-tools/-/$EWT_NAME"

# Add-ons already on this box stay added on a rerun that does not name them (an update, the
# doctor's "run the installer again", or a run by hand): from the last record, or, for a box
# set up before records kept them, from irate-box's own service for it being enabled (or
# stopped by /admin's "off" switch). Only --remove takes one away. Said, not silent.
KEPT=()
addon_present() { # $1 add-on, $2 irate-box's marker file, $3 its unit
	grep -qx -- "--with-$1" "$ETC/install-options" 2>/dev/null && return 0
	[ -e "$2" ] || return 1
	systemctl is-enabled --quiet "$3" 2>/dev/null && return 0
	grep -qs "\"$3.service\"" "$ETC/access-stopped.json"
}
keep_addon() { # $1 add-on, $2 its WITH_ variable, $3 marker, $4 unit
	[ "${!2}" = 1 ] && return 0
	printf '%s\n' "${REMOVE[@]}" | grep -qx "$1" && return 0
	addon_present "$1" "$3" "$4" || return 0
	printf -v "$2" 1
	KEPT+=("$1")
}
keep_addon notes WITH_NOTES /etc/systemd/system/silverbullet.service silverbullet
keep_addon sync WITH_SYNC "/etc/systemd/system/syncthing@$HUB_USER.service.d/irate-box.conf" "syncthing@$HUB_USER"
keep_addon mqtt WITH_MQTT /etc/mosquitto/conf.d/irate-box.conf mosquitto
keep_addon irc WITH_IRC /etc/ngircd/irate-box.conf ngircd
keep_addon term WITH_TERM /etc/systemd/system/ttyd.service ttyd
keep_addon collab WITH_COLLAB /etc/systemd/system/excalidraw-room.service excalidraw-room
keep_addon kiwix WITH_KIWIX /etc/systemd/system/kiwix.service kiwix

# Bold on a terminal only: from /admin the output goes to a log file that a page shows.
# Decided now, before the output is also copied to the install log (below).
BOLD=0
[ -t 1 ] && BOLD=1
STEP=""
say() {
	STEP="$*"
	if [ "$BOLD" = 1 ]; then printf '\033[1m==> %s\033[0m\n' "$*"; else printf '==> %s\n' "$*"; fi
	state_write
}
die() {
	printf 'install.sh: %s\n' "$*" >&2
	ABORT_CMD="${ABORT_CMD:-install.sh refused: $*}"
	exit 1
}
# Things the owner should know, said where they happen and again at the end of the run.
NOTICES=()
notice() {
	NOTICES+=("$*")
	if [ "$BOLD" = 1 ]; then printf '\033[1;33m!!  %s\033[0m\n' "$*"; else printf '!!  %s\n' "$*"; fi
}
# Something did not work, but the install carries on: said now, counted in the closing line,
# kept in the install record for the doctor (health.py, /admin → Health → Services doctor).
PROBLEMS=()
problem() {
	PROBLEMS+=("$*")
	fail=1
	if [ "$BOLD" = 1 ]; then printf '\033[1;31m    problem: %s\033[0m\n' "$*"; else printf '    problem: %s\n' "$*"; fi
	state_write
}
fail=0

# Folders under $STATE: the hub owns $STATE, so any folder in it may have been swapped for a
# link, and `install -d -o/-m` (chown, chmod) would change wherever the link leads. Each path is
# walked from $STATE one folder at a time with O_NOFOLLOW, made where missing, and owned and
# moded through its own fd: a link anywhere below $STATE stops the install instead.
#   state_dir OWNER GROUP MODE PATH...     (each PATH under $STATE)
# control/ and control/results/ are root's, readable by the hub's group; only requests/ is the
# hub's. A box from before kept them the hub's, and may hold links the hub left there: they
# go (root's writers never follow one, but nothing of root's should sit in a folder with them).
control_dirs() {
	state_dir root "$HUB_USER" 750 "$STATE/control" "$STATE/control/results"
	state_dir "$HUB_USER" "$HUB_USER" 700 "$STATE/control/requests"
	find "$STATE/control" "$STATE/control/results" -mindepth 1 -maxdepth 1 \( -type l -o \( -type f -links +1 \) \) -delete
	# Files the hub was given before are root's again: root reads some back (update.json says
	# which commit was verified), and the hub must not be able to edit them in place.
	find "$STATE/control" "$STATE/control/results" -mindepth 1 -maxdepth 1 -type f -exec chown -h root:"$HUB_USER" {} + -exec chmod 644 {} +
}
state_dir() {
	python3 - "$STATE" "$@" <<'PY' || die "a folder under $STATE is a link or cannot be made (see above): look at what made it, remove it, and run again"
import grp, os, pwd, sys
base, owner, group, mode, *paths = sys.argv[1:]
uid, gid, mode = pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid, int(mode, 8)
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
for path in paths:
    rel = os.path.relpath(path, base)
    if rel == "." or rel.startswith(".."):
        sys.exit(f"state_dir: {path} is not under {base}")
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in rel.split("/"):
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            try:
                nfd = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                sys.exit(f"state_dir: {path}: {part} is a link or not a folder ({exc.strerror})")
            os.close(fd)
            fd = nfd
        os.fchown(fd, uid, gid)
        os.fchmod(fd, mode)
    finally:
        os.close(fd)
PY
}

# --- the install record and log ------------------------------------------------------------
# /var/log/irate-box/install.log keeps this run's output (the previous run's in .1), and
# install-state.json how far it got: the step, the problems, and if it stopped early, where.
# The doctor reads both, so "what happened?" has an answer after the terminal is gone.
LOGDIR=/var/log/irate-box
STATE_FILE="$LOGDIR/install-state.json"
ABORT_LINE="" ABORT_CMD="" RUNNING=false STARTED="$(date +%s)"
json_str() {
	local s="$1"
	s="${s//\\/\\\\}"
	s="${s//\"/\\\"}"
	s="${s//$'\t'/ }"
	s="${s//$'\n'/ }"
	printf '"%s"' "$s"
}
state_write() {
	[ -d "$LOGDIR" ] || return 0
	local probs="" p
	for p in "${PROBLEMS[@]}"; do probs+="${probs:+, }$(json_str "$p")"; done
	printf '{"started": %s, "pid": %s, "running": %s, "step": %s, "args": %s, "problems": [%s], "exit": %s, "aborted": %s, "failed_line": %s, "failed_command": %s, "at": %s}\n' \
		"$STARTED" "$$" "$RUNNING" "$(json_str "$STEP")" "$(json_str "${ARGS_SHOWN:-}")" "$probs" "${EXIT_CODE:-null}" \
		"$([ -n "$ABORT_CMD" ] && echo true || echo false)" "${ABORT_LINE:-null}" "$(json_str "$ABORT_CMD")" "$(date +%s)" \
		>"$STATE_FILE.tmp" 2>/dev/null && mv -f "$STATE_FILE.tmp" "$STATE_FILE"
}
on_err() {
	# Only the first: the trap runs again in every function the failure passes through.
	[ -n "$ABORT_CMD" ] || { ABORT_LINE="$1"; ABORT_CMD="$2"; }
}
on_exit() {
	EXIT_CODE=$?
	RUNNING=false
	state_write
	[ "$EXIT_CODE" = 0 ] || [ -z "$ABORT_CMD" ] && return
	local doctor="$CODE/irate-box"
	[ -f "$CODE/irate_box/root/health.py" ] || doctor="${SRC:-.}/irate-box"
	printf '\ninstall.sh stopped during "%s"' "${STEP:-the start}"
	[ -n "$ABORT_LINE" ] && printf ': line %s, `%s` failed (exit %s)' "$ABORT_LINE" "$ABORT_CMD" "$EXIT_CODE"
	printf '.\n'
	echo "  Nothing after that step was done; what was set up before it stays as it is."
	echo "  The full output is in $LOGDIR/install.log."
	echo "  See what is wrong:  sudo $doctor health"
	echo "  Then fix it and run the installer again: it carries on from what is already there."
}
# fetch URL DEST [CACHED-NAME]: from the download cache if it holds CACHED-NAME (default:
# the URL's file name), else from the network. Callers check checksums either way.
# Whatever it gets is kept in the box's own cache too, so an offline kit made from this box later
# (/admin, Backup) can hand it on.
OWN_CACHE=/var/cache/irate-box/downloads
# SilverBullet's zip ($1) for $2 (its arch) is the release's, by the pinned digest; or, from an
# offline kit, the box-made copy the kit lists in its own SHA256SUMS (as ttyd's).
sb_sum_ok() {
	local got want
	got="$(sha256sum <"$1" | cut -d' ' -f1)"
	want="$(eval "echo \${SB_SHA256_$2:-}")"
	[ -n "$want" ] && [ "$got" = "$want" ] && return 0
	[ -n "$DL_CACHE" ] && grep -qx "$got  silverbullet-server-linux-$2.zip" "$DL_CACHE/silverbullet-$SB_VERSION-SHA256SUMS" 2>/dev/null
}
fetch() {
	local name="${3:-$(basename "$1")}"
	if [ -n "$DL_CACHE" ] && [ -s "$DL_CACHE/$name" ]; then
		cp "$DL_CACHE/$name" "$2"
	else
		curl -fsSL -o "$2" "$1"
	fi
	if [ ! -s "$OWN_CACHE/$name" ] && [ "$(id -u)" = 0 ]; then
		install -d -m 755 "$OWN_CACHE" && install -m 644 "$2" "$OWN_CACHE/$name" || true
	fi
}

# --- an offline kit (--make-offline-bundle), instead of installing -----------------------
# Everything this script would download, laid out as its own options read it: --src kit/irate-box,
# --apps kit/apps, --download-cache kit/downloads (the same file names as fetch() caches), and
# kit/zim for --zim. Each release download is checked against its published checksum here too.
# get NAME URL: into the kit's downloads, from --download-cache if it holds NAME (a box making a
# kit of itself has its own), else the network. Returns 1 when neither has it.
kit_get() {
	local dest="$KIT_DL/$1"
	[ -s "$dest" ] && return 0
	if [ -n "$DL_CACHE" ] && [ -s "$DL_CACHE/$1" ]; then
		cp "$DL_CACHE/$1" "$dest"
	else
		curl -fsSL -o "$dest" "$2" 2>/dev/null || { rm -f "$dest"; return 1; }
	fi
}
# SilverBullet's manual ($1, the pack's path) from the download cache, the box's own, or git; checked
# against SB_DOCS_SHA256 either way. Returns 1 when none has it (no internet and no copy), 2 when what it
# got does not match (and removes it).
sb_docs_get() {
	local src="" d
	for d in "$DL_CACHE" "$OWN_CACHE"; do
		[ -n "$d" ] && [ -s "$d/$SB_DOCS_NAME" ] && { src="$d/$SB_DOCS_NAME"; break; }
	done
	if [ -n "$src" ]; then
		cp "$src" "$1"
	else
		"$(dirname "$(readlink -f "$0")")/irate-box" sbdocs fetch "$SB_DOCS_COMMIT" "$1" 2>/dev/null || { rm -f "$1"; return 1; }
	fi
	[ "$("$(dirname "$(readlink -f "$0")")/irate-box" sbdocs sum "$1" 2>/dev/null)" = "$SB_DOCS_SHA256" ] || { rm -f "$1"; return 2; }
}
# ESP Web Tools' tarball ($1) from the download cache, the box's own, or npm; checked against
# EWT_SHA256 either way. Returns 1 when none has it, 2 when what it got does not match (and removes it).
ewt_get() {
	local src="" d
	for d in "$DL_CACHE" "$OWN_CACHE"; do
		[ -n "$d" ] && [ -s "$d/$EWT_NAME" ] && { src="$d/$EWT_NAME"; break; }
	done
	if [ -n "$src" ]; then
		cp "$src" "$1"
	else
		curl -fsSL -o "$1" "$EWT_URL" 2>/dev/null || { rm -f "$1"; return 1; }
	fi
	[ "$(sha256sum <"$1" | cut -d' ' -f1)" = "$EWT_SHA256" ] || { rm -f "$1"; return 2; }
}
own_ttyd_arch() { case "$(uname -m)" in x86_64 | aarch64) uname -m ;; armv7l | armv8l) echo armhf ;; armv6l) echo arm ;; esac; }
own_sb_arch() { case "$(uname -m)" in x86_64) echo x86_64 ;; aarch64) echo aarch64 ;; armv7l | armv8l) echo armv7 ;; esac; }
make_bundle() {
	local kit here a tmp z ver tag sb_arch ttyd_arch caddy_arch app zip t sum
	kit="$(realpath -m "$1")"
	here="$(cd "$(dirname "$0")" && pwd)"
	[ -f "$here/irate_box/hub/server.py" ] || die "--make-offline-bundle runs from an irate-box checkout"
	for t in curl git python3 unzip sha256sum; do command -v "$t" >/dev/null || die "--make-offline-bundle needs $t"; done
	[ ! -e "$kit" ] || [ -z "$(ls -A "$kit" 2>/dev/null)" ] || die "$kit is not empty"
	install -d "$kit/irate-box" "$kit/apps" "$kit/downloads" "$kit/zim"
	echo "==> The code"
	if git -C "$here" rev-parse >/dev/null 2>&1; then
		git -C "$here" archive --format=tar HEAD | tar -x -C "$kit/irate-box"
		ver="$(git -C "$here" describe --always --tags HEAD 2>/dev/null || echo unknown)"
	else
		tar -C "$here" -cf - --exclude=./.git --exclude=__pycache__ --exclude=./store-state --exclude='./*.code-workspace' . |
			tar -C "$kit/irate-box" -xf -
		ver="$(cut -d' ' -f1 "$here/VERSION" 2>/dev/null || echo unknown)"
	fi
	printf '%s (offline kit, made %s)\n' "$ver" "$(date -u +%Y-%m-%d)" >"$kit/irate-box/VERSION"
	echo "    irate-box $ver"
	echo "==> The apps (fetched and checked as the librarian does)"
	tmp="$(mktemp -d)"; install -d "$tmp/library"
	for app in draw mermaid serial tools room flasher; do
		if zip="$(HUB_STATE_DIR="$tmp" "$here/irate-box" librarian app-fetch "$app" 2>"$tmp/err")"; then
			install -d "$kit/apps/$app"
			unzip -q -o "$zip" -d "$kit/apps/$app"
			echo "    $(tail -1 "$tmp/err")"
			rm -f "$zip"
		else
			echo "    $app: not fetched ($(tail -1 "$tmp/err" | sed 's/^librarian: //'))"
		fi
	done
	rm -rf "$tmp"
	if [ -n "$APPS_SRC" ]; then
		for app in mermaid draw tools serial flasher room; do
			[ -d "$APPS_SRC/$app" ] || continue
			rm -rf "${kit:?}/apps/$app"
			cp -a "$APPS_SRC/$app" "$kit/apps/$app"
			rm -rf "$kit/apps/$app/.git"
			echo "    $app: from $APPS_SRC/$app"
		done
	fi
	# A box making a kit of itself: its installed collaboration relay too (it lives apart).
	if [ ! -f "$kit/apps/room/dist/index.js" ] && [ -f "$ROOM/dist/index.js" ]; then
		rm -rf "${kit:?}/apps/room"; cp -a "$ROOM" "$kit/apps/room"
		echo "    room: from $ROOM"
	fi
	echo "==> Release downloads for: $BUNDLE_ARCHS"
	KIT_DL="$kit/downloads"
	for a in ${BUNDLE_ARCHS//,/ }; do
		case "$a" in
		x86_64 | amd64) ttyd_arch=x86_64 sb_arch=x86_64 caddy_arch=amd64 ;;
		aarch64 | arm64) ttyd_arch=aarch64 sb_arch=aarch64 caddy_arch=arm64 ;;
		armv7l | armv8l | armhf) ttyd_arch=armhf sb_arch=armv7 caddy_arch=armv7 ;;
		armv6l) ttyd_arch=arm sb_arch="" caddy_arch="" ;;
		*) die "--arch: $a is not one of aarch64, armv7l, armv6l, x86_64" ;;
		esac
		if kit_get "ttyd-$TTYD_VERSION-ttyd.$ttyd_arch" "https://github.com/tsl0922/ttyd/releases/download/$TTYD_VERSION/ttyd.$ttyd_arch" &&
			kit_get "ttyd-$TTYD_VERSION-SHA256SUMS" "https://github.com/tsl0922/ttyd/releases/download/$TTYD_VERSION/SHA256SUMS"; then
			sum="$(awk -v f="ttyd.$ttyd_arch" '$2 == f || $2 == "*" f {print $1}' "$KIT_DL/ttyd-$TTYD_VERSION-SHA256SUMS")"
			[ "$(sha256sum <"$KIT_DL/ttyd-$TTYD_VERSION-ttyd.$ttyd_arch" | cut -d' ' -f1)" = "$sum" ] ||
				die "ttyd.$ttyd_arch does not match its published checksum"
			echo "    $a: ttyd $TTYD_VERSION"
		elif [ "$ttyd_arch" = "$(own_ttyd_arch)" ] && /usr/local/bin/ttyd --version 2>/dev/null | grep -q "$TTYD_VERSION"; then
			# This box's installed ttyd, the same version: its sum written here, since the
			# published SHA256SUMS could not be had.
			cp /usr/local/bin/ttyd "$KIT_DL/ttyd-$TTYD_VERSION-ttyd.$ttyd_arch"
			printf '%s  ttyd.%s\n' "$(sha256sum </usr/local/bin/ttyd | cut -d' ' -f1)" "$ttyd_arch" >>"$KIT_DL/ttyd-$TTYD_VERSION-SHA256SUMS"
			echo "    $a: ttyd $TTYD_VERSION (this box's installed copy; no internet for the published one)"
		else
			rm -f "$KIT_DL/ttyd-$TTYD_VERSION-ttyd.$ttyd_arch"
			echo "    $a: no ttyd (not in the download cache, and no internet): the box fetches it when set up"
		fi
		if [ -n "$sb_arch" ]; then
			z="silverbullet-$SB_VERSION-silverbullet-server-linux-$sb_arch.zip"
			if kit_get "$z" "https://github.com/silverbulletmd/silverbullet/releases/download/$SB_VERSION/silverbullet-server-linux-$sb_arch.zip"; then
				unzip -tq "$KIT_DL/$z" >/dev/null || die "$z is not a whole zip"
				sb_sum_ok "$KIT_DL/$z" "$sb_arch" || die "$z does not match SilverBullet's published digest"
				echo "    $a: SilverBullet $SB_VERSION"
			elif [ "$sb_arch" = "$(own_sb_arch)" ] && /usr/local/bin/silverbullet --version 2>/dev/null | grep -q "$SB_VERSION"; then
				# This box's installed SilverBullet, the same version, zipped as its release ships it.
				python3 -c 'import sys, zipfile; z = zipfile.ZipFile(sys.argv[1], "w", zipfile.ZIP_DEFLATED); z.write("/usr/local/bin/silverbullet", "silverbullet"); z.close()' "$KIT_DL/$z"
				# Not the release's zip, so not its digest: the kit lists this one's sum itself.
				printf '%s  silverbullet-server-linux-%s.zip\n' "$(sha256sum <"$KIT_DL/$z" | cut -d' ' -f1)" "$sb_arch" >>"$KIT_DL/silverbullet-$SB_VERSION-SHA256SUMS"
				echo "    $a: SilverBullet $SB_VERSION (this box's installed copy; no internet for the release)"
			else
				echo "    $a: no SilverBullet (not in the download cache, and no internet): only needed for notes"
			fi
		else
			echo "    $a: no SilverBullet build (notes need armv7, aarch64 or x86_64)"
		fi
		if [ "$WEB" = caddy ] && [ -n "$caddy_arch" ]; then
			[ -s "$KIT_DL/caddy-release-tag" ] || kit_get caddy-release-tag /nonexistent ||
				curl -fsSL https://api.github.com/repos/caddyserver/caddy/releases/latest 2>/dev/null |
				sed -n 's/^ *"tag_name": *"\([^"]*\)".*/\1/p' >"$KIT_DL/caddy-release-tag" || true
			tag="$(cat "$KIT_DL/caddy-release-tag" 2>/dev/null)"
			z="caddy_${tag#v}_linux_${caddy_arch}.deb"
			if [ -n "$tag" ] && kit_get "$z" "https://github.com/caddyserver/caddy/releases/download/$tag/$z" &&
				kit_get "caddy_${tag#v}_checksums.txt" "https://github.com/caddyserver/caddy/releases/download/$tag/caddy_${tag#v}_checksums.txt"; then
				(cd "$KIT_DL" && grep " $z\$" "caddy_${tag#v}_checksums.txt" | sha512sum -c --quiet) || die "$z does not match its published checksum"
				echo "    $a: Caddy ${tag#v}"
			else
				rm -f "$KIT_DL/caddy-release-tag"
				echo "    $a: no Caddy .deb (not in the download cache, and no internet)"
			fi
		fi
	done
	# SilverBullet's manual, the same for every architecture.
	if [ -s "$KIT_DL/$SB_DOCS_NAME" ] || sb_docs_get "$KIT_DL/$SB_DOCS_NAME"; then
		echo "    SilverBullet's manual ($SB_VERSION)"
	elif [ $? = 2 ]; then
		die "$SB_DOCS_NAME does not match its pinned digest (SB_DOCS_SHA256)"
	else
		echo "    no SilverBullet manual (not in the download cache, and no internet): notes work without it"
	fi
	# ESP Web Tools, for the light web flasher, the same for every architecture.
	if [ -s "$KIT_DL/$EWT_NAME" ] || ewt_get "$KIT_DL/$EWT_NAME"; then
		echo "    ESP Web Tools $EWT_VERSION"
	elif [ $? = 2 ]; then
		die "$EWT_NAME does not match its pinned digest (EWT_SHA256)"
	else
		echo "    no ESP Web Tools (not in the download cache, and no internet): the flasher page offers downloads only"
	fi
	if [ ${#ZIMS[@]} -gt 0 ]; then
		echo "==> Books"
		for z in "${ZIMS[@]}"; do
			case "$z" in
			http://* | https://*) curl -fL --progress-bar -o "$kit/zim/$(basename "${z%%\?*}")" "$z" ;;
			*) [ -f "$z" ] || die "--zim $z: no such file"; cp "$z" "$kit/zim/" ;;
			esac
			echo "    $(basename "${z%%\?*}")"
		done
	fi
	cat >"$kit/setup.sh" <<-'SETUP'
	#!/bin/sh
	# Sets up a box from this offline kit: the installer from the kit, with the kit's apps,
	# release downloads and books. Any installer option may follow (--with-notes, --web caddy, ...);
	# see irate-box/install.sh --help. Debian's own packages still come from the box's apt.
	here="$(cd "$(dirname "$0")" && pwd)"
	set -- --src "$here/irate-box" --apps "$here/apps" --download-cache "$here/downloads" "$@"
	for z in "$here"/zim/*.zim; do [ -f "$z" ] && set -- "$@" --zim "$z"; done
	bash "$here/irate-box/install.sh" "$@" || exit $?
	# What the kit's maker chose to bring: the old box's state, git repositories, toolkits.
	if [ -f "$here/state-backup.tar.gz" ]; then
		echo "==> The state of the box this kit was made on"
		runuser -u hub -- tar -xzf "$here/state-backup.tar.gz" -C /var/lib/hub --strip-components=1 \
			--exclude=irate-box-state/BACKUP-CONTENTS.txt && systemctl restart irate-box 2>/dev/null
		echo "    restored into /var/lib/hub"
	fi
	for repo in "$here"/git/public/*.git "$here"/git/private/*.git; do
		[ -d "$repo" ] || continue
		dest="/var/lib/hub/git/$(basename "$(dirname "$repo")")/$(basename "$repo")"
		if [ -e "$dest" ]; then echo "    $dest is there already: left as it is"; continue; fi
		cp -a "$repo" "$dest" && chown -R hub:hub "$dest" && echo "    git: $dest"
	done
	if [ -d "$here/kits" ]; then
		echo "==> Toolkits (each package checked against Debian's signatures)"
		/opt/irate-box/irate-box kits import-dir "$here/kits" | sed 's/^/    /'
	fi
	SETUP
	chmod 755 "$kit/setup.sh"
	(cd "$kit" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS)
	echo
	echo "==> Offline kit in $kit ($(du -sh "$kit" | cut -f1)). Copy it to the box (a USB stick will do), then:"
	echo "    cd <the kit> && sha256sum -c --quiet SHA256SUMS && sudo ./setup.sh [options]"
}
if [ -n "$MAKE_BUNDLE" ]; then
	make_bundle "$MAKE_BUNDLE"
	exit 0
fi

# --- preflight -------------------------------------------------------------------
[ "$(id -u)" = 0 ] || die "run as root (sudo ./install.sh …)"
install -d -m 755 "$LOGDIR"
[ -f "$LOGDIR/install.log" ] && mv -f "$LOGDIR/install.log" "$LOGDIR/install.log.1"
: >"$LOGDIR/install.log"
chmod 644 "$LOGDIR/install.log"
exec > >(tee -a "$LOGDIR/install.log") 2>&1
RUNNING=true
set -E
trap 'on_err "$LINENO" "$BASH_COMMAND"' ERR
trap on_exit EXIT
state_write
command -v apt-get >/dev/null || die "needs a Debian-family system with apt"
command -v systemctl >/dev/null || die "needs systemd"
. /etc/os-release
DISTRO="${PRETTY_NAME:-$ID}"
[ -f /etc/armbian-release ] && DISTRO="$DISTRO (Armbian $(. /etc/armbian-release; echo "${BOARD_NAME:-$BOARD}"))"
ARCH="$(uname -m)"
say "Installing on $DISTRO, $ARCH"
[ "${#KEPT[@]}" -eq 0 ] || notice "Keeping the add-ons already on this box: ${KEPT[*]} (they were not named this time; --remove NAME takes one away)."

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
# nginx is the front: measured on a Luckfox Lyra at ~40 MB less RAM than Caddy and a
# fraction of the CPU per request. Caddy is the fallback, with the same routes. The choice is
# recorded in install-options, so an update keeps it; otherwise, in order: a box irate-box
# already serves through Caddy stays on it (switching is offered, not imposed), an owner's
# own web server is the one the hub fits into, and a bare box gets nginx.
NGINX_SITE=/etc/nginx/conf.d/irate-box.conf
NGINX_LOGINS=/etc/nginx/irate-box.htpasswd
# The front's secret header for /admin (server.py FRONT_SECRET), as an nginx include: root's.
NGINX_FRONT=/etc/nginx/irate-box-front.conf
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

if [ -z "$SRC" ] && [ -f "$(dirname "$0")/irate_box/hub/server.py" ]; then
	SRC="$(cd "$(dirname "$0")" && pwd)"
fi

# --- packages --------------------------------------------------------------------
say "Installing packages"
export DEBIAN_FRONTEND=noninteractive
CADDY_LIST=/etc/apt/sources.list.d/caddy-stable.list
CADDY_KEY=/usr/share/keyrings/caddy-stable-archive-keyring.gpg
# Caddy's apt repository is the preferred source. But its index has been signed
# with a subkey that expired in 2024: GnuPG warns and accepts, while Debian trixie's sqv
# rejects it and fails the whole `apt-get update`. If that happens, the repository is
# dropped and Caddy comes from its GitHub release instead (install_caddy_release), and
# $ETC/caddy-from-release records it so later runs do not add the repository back to fail
# again. Delete that file to try the repository once more.
CADDY_MARK=/etc/hub/caddy-from-release
CADDY_FROM_RELEASE=0
[ -f "$CADDY_MARK" ] && CADDY_FROM_RELEASE=1
# fcgiwrap and cgit: the git servers (/git/, /git-private/), part of every install: ~2 MB,
# and nothing runs until someone browses, clones or pushes. python3-markdown: READMEs rendered
# on cgit's about pages (scripts/cgit-about.py), ~1 MB. libjs-highlight.js: code highlighted
# in the visitor's browser (web/cgit-hub.js), ~2 MB; Pygments on the box took 2-5 s a page.
# iw: the network inventory (netinv.py) reads the radios with it; ~0.3 MB.
pkgs=(python3 curl ca-certificates git unzip fcgiwrap cgit python3-markdown libjs-highlight.js iw dnsmasq-base nftables procps gpgv)
[ "$WITH_SYNC" = 1 ] && pkgs+=(syncthing)
# mosquitto-clients: mosquitto_sub/_pub, for watching the broker from the terminal.
[ "$WITH_MQTT" = 1 ] && pkgs+=(mosquitto mosquitto-clients)
# ngircd: ~0.2 MB package; Debian starts it on install with its own example config, which the
# IRC section below replaces and restarts into.
[ "$WITH_IRC" = 1 ] && pkgs+=(ngircd)
# Kiwix is in use when --zim adds a book, and also when books are already on disk: a reinstall
# over kept state (uninstall.sh --keep-state, then install.sh) brings /wiki/ back with them.
# Kiwix: asked for (--with-kiwix, an add-on), or brought by the books, unless it was taken out (--remove kiwix
# leaves a mark, so the books already there do not bring it back at the next update).
KIWIX=0
[ "$WITH_KIWIX" = 1 ] && { KIWIX=1; rm -f "$ETC/kiwix-removed"; }
[ -e "$ETC/kiwix-removed" ] || { { [ ${#ZIMS[@]} -gt 0 ] || compgen -G "$STATE/zim/*.zim" >/dev/null; } && KIWIX=1; }
printf '%s\n' "${REMOVE[@]}" | grep -qx kiwix && KIWIX=0
[ "$KIWIX" = 1 ] && WITH_KIWIX=1
[ "$KIWIX" = 1 ] && pkgs+=(kiwix-tools)
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
		# Pulled in by the irate-box package (apt marks it automatic): irate-box's, as if
		# installed here. Otherwise the owner's, used as it is.
		if [ ! -f "$NGINX_OURS_MARK" ] && apt-mark showauto 2>/dev/null | grep -qx nginx &&
			dpkg-query -W -f='${Status}' irate-box 2>/dev/null | grep -q 'ok installed'; then
			install -d -m 750 /etc/hub
			echo "nginx installed with the irate-box package $(date -u +%Y-%m-%d); uninstall.sh --purge-packages may remove it" >"$NGINX_OURS_MARK"
		fi
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
install -d -o "$HUB_USER" -g "$HUB_USER" -m 755 "$STATE"  # /var/lib is root's: no link to fear here
state_dir "$HUB_USER" "$HUB_USER" 755 "$STATE/notes" "$STATE/addons" "$STATE/apps.d"
install -d -m 755 "$CODE" "$APPS"
install -d -m 750 "$ETC"

# --- the hub ---------------------------------------------------------------------
if [ -n "$SRC" ]; then
	say "Copying the hub from $SRC"
	[ -f "$SRC/irate_box/hub/server.py" ] || die "$SRC does not look like an irate-box checkout"
	# State files a dev checkout may have lying around stay behind.
	find "$CODE" -mindepth 1 -delete
	tar -C "$SRC" -cf - \
		--exclude=./.git --exclude=./.notes --exclude=__pycache__ --exclude=./store-state \
		--exclude=./store --exclude=./messages.json --exclude=./board.json \
		--exclude=./clock.json --exclude=./settings.json --exclude='./tailscale.want*' --exclude='./*.code-workspace' . |
		tar -C "$CODE" -xf -
	# Root's alone, whatever modes the source carried (a checkout made with umask 002 would leave
	# files group-writable): the root helper, the doctors and this script run from here.
	chown -R root:root "$CODE"
	chmod -R go-w "$CODE"
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
	install -m 644 -o root -g root "$SRC/VERSION" "$CODE/VERSION"
else
	printf 'unknown (installed %s)\n' "$(date -u +%Y-%m-%d)" >"$CODE/VERSION"
fi
# irate_box/root/ (the root helper, the security switches, the doctors) is for root alone:
# nothing the hub's users run imports from it, so they have no reason to read it, and a hub
# process that goes wrong cannot run them. Its source is still public (/source, below).
[ -d "$CODE/irate_box/root" ] && chmod 700 "$CODE/irate_box/root"

# How this box was installed, for /admin's "Install update", which reruns this script the
# same way (hub_control.py). One argument per line. Root-owned, so the unprivileged hub
# can neither choose the repository an update comes from nor add options to it. Paths
# (--src, --apps, --zim) and the password are not recorded: apps and books stay as they
# are, and the password file is kept.
rec_repo="$REPO" rec_branch="$BRANCH"
if [ -n "$SRC" ] && git -C "$SRC" rev-parse >/dev/null 2>&1; then
	rec_repo="$(git -C "$SRC" remote get-url origin 2>/dev/null || echo "$REPO")"
	# A token in the remote's URL is not recorded (install-options is world-readable).
	rec_repo="$(printf '%s' "$rec_repo" | sed -E 's#^([a-z+]+://)[^/@]*@#\1#')"
	rec_branch="$(git -C "$SRC" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "$BRANCH")"
	[ "$rec_branch" = HEAD ] && rec_branch="$BRANCH"
fi
case "$rec_repo" in
http://* | git://*)
	notice "updates come from $rec_repo, a plain-text transport: the box's updater refuses it (anyone on the way could hand it code that root runs). Rerun with --repo https://… to have updates." ;;
esac
{
	printf '%s\n' --repo "$rec_repo" --branch "$rec_branch" --web "$WEB"
	[ "$WITH_NOTES" = 1 ] && echo --with-notes
	[ "$WITH_SYNC" = 1 ] && echo --with-sync
	[ "$WITH_MQTT" = 1 ] && echo --with-mqtt
	[ "$WITH_IRC" = 1 ] && echo --with-irc
	[ "$WITH_TERM" = 1 ] && echo --with-term
	[ "$WITH_COLLAB" = 1 ] && echo --with-collab
	[ "$WITH_KIWIX" = 1 ] && echo --with-kiwix
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
	for app in mermaid draw tools serial flasher; do
		[ -d "$APPS_SRC/$app" ] || continue
		say "Installing $app from $APPS_SRC/$app"
		rm -rf "${APPS:?}/$app"
		cp -a "$APPS_SRC/$app" "$APPS/$app"
		rm -rf "$APPS/$app/.git"
		chown -R root:root "$APPS/$app"
	done
fi
# The light web flasher's engine (ESP Web Tools): its bundle under $APPS/esp-web-tools, kept while
# it is the pinned version, so an update with no internet keeps the one it has.
if [ "$(cat "$APPS/esp-web-tools/VERSION" 2>/dev/null)" != "$EWT_VERSION" ]; then
	tmp="$(mktemp -d)"
	got=0; ewt_get "$tmp/$EWT_NAME" || got=$?
	if [ "$got" = 0 ]; then
		[ -s "$OWN_CACHE/$EWT_NAME" ] || install -D -m 644 "$tmp/$EWT_NAME" "$OWN_CACHE/$EWT_NAME" || true
		tar -xzf "$tmp/$EWT_NAME" -C "$tmp" package/dist/web package/LICENSE
		rm -rf "${APPS:?}/.esp-web-tools.new"
		install -d -m 755 "$APPS/.esp-web-tools.new"
		cp -r "$tmp/package/dist/web/." "$APPS/.esp-web-tools.new/"
		install -m 644 "$tmp/package/LICENSE" "$APPS/.esp-web-tools.new/LICENSE"
		echo "$EWT_VERSION" >"$APPS/.esp-web-tools.new/VERSION"
		chown -R root:root "$APPS/.esp-web-tools.new"
		chmod -R u=rwX,go=rX "$APPS/.esp-web-tools.new"
		rm -rf "${APPS:?}/esp-web-tools"
		mv "$APPS/.esp-web-tools.new" "$APPS/esp-web-tools"
		say "ESP Web Tools $EWT_VERSION, for the web flasher"
	elif [ "$got" = 2 ]; then
		problem "ESP Web Tools was not installed: $EWT_NAME does not match its pinned digest (EWT_SHA256)"
	else
		echo "    ESP Web Tools: not in the download cache, and no internet; the flasher page offers downloads only"
	fi
	rm -rf "$tmp"
fi
# Its notices: what the bundle holds and their licences (config/esp-web-tools-NOTICES.txt), current with the code.
[ ! -d "$APPS/esp-web-tools" ] || install -m 644 "$CODE/config/esp-web-tools-NOTICES.txt" "$APPS/esp-web-tools/THIRD-PARTY-NOTICES.txt"
# ELIZA was a built-in add-on here (--with-eliza). It is a local add-on now, from
# /admin's catalogue (addons/eliza.json), installed in $STATE/addons/ with no root. The old copy
# goes, and so does its librarian source unless the local add-on has taken the name.
if [ -d "$APPS/eliza" ] || [ -d "$APPS/.eliza.prev" ]; then
	rm -rf "${APPS:?}/eliza" "$APPS/.eliza.prev"
	notice "ELIZA has moved: it is an add-on in /admin's catalogue now (Add-ons). The old copy is removed; add it again there."
	if [ ! -f "$STATE/apps.d/eliza.json" ] && grep -qs '"name": "eliza"' "$STATE/library/sources.json"; then
		runuser -u "$HUB_USER" -- env HUB_STATE_DIR="$STATE" "$CODE/irate-box" librarian remove eliza || true
	fi
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
	state_dir "$HUB_USER" "$HUB_USER" 755 "$STATE/library"
	for app in "${fetch_apps[@]}"; do
		say "Fetching the $app app"
		if zip="$(runuser -u "$HUB_USER" -- env HUB_STATE_DIR="$STATE" "$CODE/irate-box" librarian app-fetch "$app")"; then
			HUB_STATE_DIR="$STATE" "$CODE/irate-box" hub_control install-app "$app" "$zip"
		else
			echo "    could not fetch $app; its tile stays empty until it is updated from /admin"
		fi
	done
	runuser -u "$HUB_USER" -- env HUB_STATE_DIR="$STATE" "$CODE/irate-box" librarian add-apps "${fetch_apps[@]}" >/dev/null
fi
# Calculators copied in by --apps are adapted here (fetched ones already were). Idempotent:
# a page already adapted is skipped.
if [ -d "$APPS/tools" ] && [ ! -f "$APPS/tools/irate-box-bundle.json" ]; then
	"$CODE/irate-box" adapt_tools "$APPS/tools" "$CODE/web"
fi
for app in mermaid draw tools serial; do
	[ -d "$APPS/$app" ] || echo "    (no $app build installed: its card will lead to an empty page)"
done

# --- config ----------------------------------------------------------------------
# No password is made here. The box starts in its first use: its admin pages free to anyone on the
# network ($UNCLAIMED_MARK tells the web server and the hub) until the owner makes the admin account
# (/admin/setup, or hub_control set-admin at the console). The web server's logins get a placeholder
# nobody knows, so /sync and /term stay shut; it is never saved or shown. The mark sits beside the web
# server's config, where its workers can see it (/etc/hub cannot be read by them). --admin-password
# sets the box's own login for scripts instead, for scripted installs.
UNCLAIMED_MARK=/etc/$WEB/irate-box-unclaimed
UNCLAIMED=0
if [ -n "$ADMIN_PW" ]; then
	# Root's alone from the moment it exists: a new file under umask 077, renamed over the old.
	(umask 077 && printf '%s\n' "$ADMIN_PW" >"$ETC/.admin-password.new") && mv -f "$ETC/.admin-password.new" "$ETC/admin-password"
elif [ -s "$ETC/admin-password" ]; then
	chmod 600 "$ETC/admin-password"
	ADMIN_PW="$(head -1 "$ETC/admin-password")"
else
	UNCLAIMED=1
	ADMIN_PW="$(head -c 24 /dev/urandom | base64 | tr -d '/+=')"
fi

# The front's secret for /admin (server.py FRONT_SECRET): the web server adds it to what comes
# through its /admin route, where it asked for the login, and the hub refuses /admin without
# it, so nothing else on the box that can reach the hub's loopback port is the admin. Made
# once, kept across reinstalls; root's alone (systemd reads it for the hub and Caddy).
if ! grep -qs '^HUB_FRONT_SECRET=[0-9a-f]\{64\}$' "$ETC/front-secret.env"; then
	( umask 077; printf 'HUB_FRONT_SECRET=%s\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >"$ETC/front-secret.env" )
fi
chown root:root "$ETC/front-secret.env"
chmod 600 "$ETC/front-secret.env"
FRONT_SECRET="$(sed -n 's/^HUB_FRONT_SECRET=//p' "$ETC/front-secret.env")"

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
# The git repositories (/admin's Git page): public/ and private/, bare, owned by the hub.
HUB_GIT_ROOT=$STATE/git
HUB_CI_ROOT=$STATE/ci
# The web flasher's bundle, and the firmware the librarian keeps for it (/flasher/).
HUB_FLASHER_ROOT=$APPS/flasher
HUB_FIRMWARE_ROOT=$STATE/firmware
# The light web flasher's engine (ESP Web Tools), served at /flasher/esp-web-tools/.
HUB_EWT_ROOT=$APPS/esp-web-tools
# The local add-ons' own origin: the web server's second port (/addons/<id>/ redirects there).
HUB_ADDON_PORT=$ADDON_PORT
# The notes' own origin (/notes/ redirects there).
HUB_NOTES_PORT=$NOTES_PORT
HUB_WIKI_PORT=$WIKI_PORT
HUB_GIT_PORT=$GIT_PORT
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
		rm -f "$NGINX_SITE" "$NGINX_SITE.prev" "$NGINX_LOGINS" "$NGINX_FRONT" /etc/nginx/irate-box-unclaimed
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
# CPU per check on a Luckfox Lyra, against 38 ms for this. The file is read on every request too,
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
	echo "# Generated by irate-box install.sh from $CODE/config/irate-box.nginx. Edits are overwritten on reinstall."
	sed -e "s|@PORT@|$1|g" -e "s|@STATIC@|$CODE/web|g" -e "s|@APPS@|$APPS|g" -e "s|@GIT_ROOT@|$STATE/git|g" -e "s|@FIRMWARE@|$STATE/firmware|g" \
		-e "s|@HTPASSWD@|$NGINX_LOGINS|g" -e "s|@UNCLAIMED@|$UNCLAIMED_MARK|g" -e "s|@ACCESS@|$ETC/nginx-access.conf|g" \
		-e "s|@FRONT@|$NGINX_FRONT|g" -e "s|@ADDON_PORT@|$ADDON_PORT|g" -e "s|@NOTES_PORT@|$NOTES_PORT|g" -e "s|@WIKI_PORT@|$WIKI_PORT|g" -e "s|@GIT_PORT@|$GIT_PORT|g" -e "s|@ADDONS@|$STATE/addons|g" \
		-e "s|@ADDON_ACCESS@|$ETC/nginx-addons.conf|g" -e "s|@ADDON_GATES@|$ETC/nginx-addon-gates.conf.d|g" -e "s|@TLS@|$ETC/tls/front|g" -e "s|@TLS_PORT@|$TLS_PORT|g" \
		-e "s|@ADDON_TLS_PORT@|$ADDON_TLS_PORT|g" -e "s|@NOTES_TLS_PORT@|$NOTES_TLS_PORT|g" -e "s|@WIKI_TLS_PORT@|$WIKI_TLS_PORT|g" \
		-e "s|@GIT_TLS_PORT@|$GIT_TLS_PORT|g" "$CODE/config/irate-box.nginx" |
		# A kernel without IPv6: the [::] listener would stop nginx from starting at all.
		if [ -f /proc/net/if_inet6 ]; then cat; else sed '/listen \[::\]:/d'; fi
}
# Who may open each app (/admin, Apps and Add-ons: public, private or off): the choices so far,
# or the defaults, as the web server's part, before the web server's config is checked.
control_dirs
access_install() {
	HUB_WEB_SERVER="$1" HUB_ETC_DIR="$ETC" HUB_STATE_DIR="$STATE" HUB_USER="$HUB_USER" HUB_CADDY_HASH="${HASH:-}" \
		"$CODE/irate-box" hub_control access-install
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
	access_install nginx >/dev/null
	( umask 077; printf '# Generated by irate-box install.sh from %s. Root only.\nproxy_set_header X-Irate-Front "%s";\n' \
		"$ETC/front-secret.env" "$FRONT_SECRET" >"$NGINX_FRONT" )
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
	HASH="$(printf '%s\n' "$ADMIN_PW" | caddy hash-password)"  # on stdin, not in /proc
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
			echo "# Generated by irate-box install.sh from $CODE/config/Caddyfile and imported by /etc/caddy/Caddyfile. Edits are overwritten on reinstall."
			sed -n '/^(irate_box_hub) {/,$p' "$CODE/config/Caddyfile"
		else
			echo "# Generated by irate-box install.sh from $CODE/config/Caddyfile. Edits are overwritten on reinstall."
			cat "$CODE/config/Caddyfile"
		fi | sed -e "s|\$2a\$14\$REPLACE_ME_WITH_CADDY_HASH_PASSWORD_OUTPUT|$HASH|" -e "s|^:80 {|:$port {|" |
			# basic_auth is the 2.8+ spelling; older Caddy (Debian trixie ships 2.6) only knows basicauth.
			if [ "$(printf '%s\n' "$CADDY_VER" 2.8 | sort -V | head -1)" != 2.8 ]; then sed 's/\bbasic_auth\b/basicauth/'; else cat; fi
	}
	caddy_check() { caddy validate --adapter caddyfile --config "$1" 2>&1; }
	access_install caddy >/dev/null
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
	Environment=HUB_STATIC=$CODE/web
	Environment=HUB_MERMAID_ROOT=$APPS/mermaid
	Environment=HUB_DRAW_ROOT=$APPS/draw
	Environment=HUB_TOOLS_ROOT=$APPS/tools
	Environment=HUB_SERIAL_ROOT=$APPS/serial
	Environment=HUB_GIT_ROOT=$STATE/git
	Environment=HUB_CODE_DIR=$CODE
	Environment=HUB_FLASHER_ROOT=$APPS/flasher
	Environment=HUB_EWT_ROOT=$APPS/esp-web-tools
	Environment=HUB_FIRMWARE_ROOT=$STATE/firmware
	Environment=HUB_ADDON_PORT=$ADDON_PORT
	Environment=HUB_ADDONS_ROOT=$STATE/addons
	# HUB_FRONT_SECRET, for /admin (header_up {env.HUB_FRONT_SECRET}): root's file, read by systemd.
	EnvironmentFile=$ETC/front-secret.env
	EOF
	# The Caddyfile turns the admin API off, so "systemctl reload caddy" fails; restart instead.
fi
if [ -n "$TAKE_UNIT" ]; then
	say "Taking port 80 from $TAKE_UNIT"
	"$CODE/irate-box" security unit-off "$TAKE_UNIT" "install.sh --take-port-80" | sed 's/^/    /'
fi
if [ "$UNCLAIMED" = 1 ]; then
	echo "no admin password chosen yet (install.sh)" >"$UNCLAIMED_MARK"
	chmod 644 "$UNCLAIMED_MARK"
else
	rm -f "$UNCLAIMED_MARK"
fi


# --- hub service -----------------------------------------------------------------
# The sandbox for every unit that runs as the hub user, guest-facing as they are: the
# whole system read-only but what each names in ReadWritePaths, no privilege gained through a
# setuid program, a /tmp of its own, no home folders, other users' processes out of sight, no
# devices, kernel settings or modules, no namespaces.
HUB_SANDBOX="ProtectSystem=strict
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectProc=invisible
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
SystemCallArchitectures=native
CapabilityBoundingSet=
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK"
# The baseline every unprivileged unit carries (tests/unit_guard.py fails on
# a missing line). AF_NETLINK: glibc's name lookup asks the kernel's addresses through it.
cat >/etc/systemd/system/irate-box.service <<EOF
[Unit]
Description=Irate-Box hub
After=network.target

[Service]
User=$HUB_USER
Group=$HUB_USER
EnvironmentFile=$ETC/hub.env
EnvironmentFile=$ETC/front-secret.env
ExecStart=$CODE/irate-box server
WorkingDirectory=$STATE
Restart=on-failure
$HUB_SANDBOX
ReadWritePaths=$STATE

[Install]
WantedBy=multi-user.target
EOF

# --- git servers -------------------------------------------------------------------
# git http-backend (clone, fetch, push) and cgit (the web view) are CGIs, run by an fcgiwrap
# of the hub's own, as the hub user, who owns the repositories; the hub creates and deletes
# them from /admin with no root. The socket is the web server's group only. Who may push is
# the web server's call (irate-box.nginx, the Caddyfile), so repos take every push it passes.
say "Setting up the git servers"
state_dir "$HUB_USER" "$HUB_USER" 755 "$STATE/git" "$STATE/git/public" "$STATE/git/private"
web_group=caddy
[ "$WEB" = nginx ] && web_group="$ngx_group"
for area in public private; do
	if [ "$area" = public ]; then
		desc="Public repositories: browse here and clone over HTTP. Pushing needs the admin login unless guests may push (/admin, Git)."
		url=/git/
	else
		desc="Private repositories, behind the admin login."
		url=/git-private/
	fi
	# Written as the hub: the folder is the hub's, which can change the file anyway; root
	# writing there by name would follow a link put in its place.
	runuser -u "$HUB_USER" -- sh -c 'umask 022 && cat >"$1.new" && mv -T "$1.new" "$1"' sh "$STATE/git/cgitrc-$area" <<EOF
# Generated by irate-box install.sh. Edits are overwritten on reinstall.
css=/git-static/cgit.css
logo=/git-static/cgit.png
js=/git-static/cgit.js
favicon=/git-static/favicon.ico
# The hub's colours and theme, and a phone layout (config/cgit-head.html, web/cgit-hub.css).
head-include=$CODE/config/cgit-head.html
# READMEs shown on a repository's about page, Markdown rendered with any HTML in it shown as
# text (the web server also forbids scripts here but the box's own). Code is highlighted in
# the browser (cgit-head.html).
about-filter=$CODE/scripts/cgit-about.py
readme=:README.md
readme=:readme.md
readme=:README
readme=:README.txt
root-title=Irate-Box git ($area)
root-desc=$desc
virtual-root=$url
clone-url=http://\$HTTP_HOST$url\$CGIT_REPO_URL
enable-http-clone=0
enable-index-owner=0
enable-commit-graph=1
enable-log-filecount=1
snapshots=tar.gz zip
cache-size=0
robots=noindex, nofollow
remove-suffix=0
# Last: the settings above apply to every repository it finds.
scan-path=$STATE/git/$area
EOF
done
cat >/etc/systemd/system/irate-box-git.socket <<EOF
[Unit]
Description=Irate-Box git servers: the socket the web server passes /git/ and /git-private/ to

[Socket]
ListenStream=/run/irate-box-git.sock
SocketUser=root
SocketGroup=$web_group
SocketMode=0660
RemoveOnStop=yes

[Install]
WantedBy=sockets.target
EOF
cat >/etc/systemd/system/irate-box-git.service <<EOF
[Unit]
Description=Irate-Box git servers: git http-backend and cgit (fcgiwrap, as $HUB_USER)
Requires=irate-box-git.socket

[Service]
User=$HUB_USER
Group=$HUB_USER
Environment=HOME=$STATE
ExecStart=/usr/sbin/fcgiwrap -c 2
$HUB_SANDBOX
# The post-receive hook of a private repository queues builds there (ci.py).
ReadWritePaths=$STATE/git $STATE/ci/queue
EOF

# --- builds on push ------------------------------------------------------------------
# A push to a private repository whose commit has a .irate-ci.sh queues a build (ci.py; the
# hook is git-hooks/post-receive, set on private repositories as core.hooksPath). Builds run
# as hubci, a user of their own that owns nothing else here and can only write under ci/, at
# idle priority, capped below the box's memory so a runaway build is stopped, not the box.
# Public repositories never build: guests may be allowed to push there.
say "Setting up builds on push"
id -u hubci >/dev/null 2>&1 ||
	useradd --system --home-dir "$STATE/ci/home" --shell /usr/sbin/nologin hubci
# ci/ itself is root's: hubci owns only what is inside, so it cannot swap those for links.
state_dir root root 755 "$STATE/ci"
state_dir hubci hubci 755 "$STATE/ci/runs" "$STATE/ci/work" "$STATE/ci/home"
# Written by the hub (the hook), read and emptied by hubci: shared through the group.
state_dir "$HUB_USER" hubci 2770 "$STATE/ci/queue"
for repo in "$STATE"/git/private/*.git; do
	[ -d "$repo" ] && runuser -u "$HUB_USER" -- git -C "$repo" config core.hooksPath "$CODE/scripts/git-hooks"
done
# Public repositories: guests may not rewrite or delete branches, nobody may push past the
# size cap, and every object is checked as it arrives (git-hooks-public/pre-receive).
# Each repository's push level (gitrepos.py): a repository from before
# gets the one that keeps its behaviour, public-everything where guest push was on, the
# area's default otherwise. Then the old one-switch flag file goes. http.receivepack follows.
git_level() {  # REPO DEFAULT
	local lv
	lv="$(runuser -u "$HUB_USER" -- git -C "$1" config --get irate-box.write 2>/dev/null)" || lv=""
	[ -n "$lv" ] || { lv="$2"; runuser -u "$HUB_USER" -- git -C "$1" config irate-box.write "$lv"; }
	runuser -u "$HUB_USER" -- git -C "$1" config http.receivepack "$([ "$lv" = nobody ] && echo false || echo true)"
}
pub_default=admin
[ -f "$STATE/git/guest-push" ] && pub_default=everyone
for repo in "$STATE"/git/public/*.git; do
	[ -d "$repo" ] || continue
	runuser -u "$HUB_USER" -- git -C "$repo" config core.hooksPath "$CODE/scripts/git-hooks-public"
	runuser -u "$HUB_USER" -- git -C "$repo" config receive.fsckObjects true
	git_level "$repo" "$pub_default"
done
for repo in "$STATE"/git/private/*.git; do
	[ -d "$repo" ] && git_level "$repo" admin
done
if [ -f "$STATE/git/guest-push" ]; then
	rm -f "$STATE/git/guest-push"
	notice "guest push was on, so each public repository is now public-everything (anyone may push); change them one by one on /admin's Git page"
fi
cat >/etc/systemd/system/irate-box-ci.path <<EOF
[Unit]
Description=Irate-Box builds on push: watch the queue ($STATE/ci/queue)

[Path]
DirectoryNotEmpty=$STATE/ci/queue
Unit=irate-box-ci.service

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/irate-box-ci.service <<EOF
[Unit]
Description=Irate-Box builds on push: run what is queued (ci.py), as hubci

[Service]
Type=oneshot
User=hubci
Group=hubci
Environment=HOME=$STATE/ci/home HUB_CI_ROOT=$STATE/ci HUB_FIRMWARE_ROOT=$STATE/firmware
ExecStart=$CODE/irate-box ci run
# Each build has its own limit (ci.py, HUB_CI_TIME_LIMIT: 12 h); this unit runs every queued
# build in turn, so it gets none of its own.
TimeoutStartSec=infinity
Nice=19
CPUWeight=10
IOSchedulingClass=idle
MemoryHigh=50%
MemoryMax=65%
# Only the namespaces an offline build needs (ci.py OFFLINE: a user namespace, and a network one
# with only loopback). Not IPAccounting nor IPAddressDeny: vendor kernels such as the Luckfox
# Lyra's can't attach systemd's cgroup programs (bpf-firewall, error 524), so they count nothing
# and block nothing.
RestrictNamespaces=user net
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
NoNewPrivileges=yes
ReadWritePaths=$STATE/ci
EOF

# --- SilverBullet ----------------------------------------------------------------
if [ "$WITH_NOTES" = 1 ]; then
	if ! /usr/local/bin/silverbullet --version 2>/dev/null | grep -q "$SB_VERSION"; then
		say "Downloading SilverBullet $SB_VERSION ($SB_ARCH)"
		tmp="$(mktemp -d)"
		fetch "https://github.com/silverbulletmd/silverbullet/releases/download/$SB_VERSION/silverbullet-server-linux-$SB_ARCH.zip" \
			"$tmp/sb.zip" "silverbullet-$SB_VERSION-silverbullet-server-linux-$SB_ARCH.zip"
		sb_sum_ok "$tmp/sb.zip" "$SB_ARCH" || die "the SilverBullet zip does not match its published digest (SB_SHA256_$SB_ARCH)"
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
	# The socket's group: the web server's, so it (and the hub, its owner) can connect, and
	# nothing else can.
	if [ "$WEB" = nginx ]; then sb_group="$ngx_group"; else sb_group="$(id -gn caddy 2>/dev/null || echo root)"; fi
	cat >/etc/systemd/system/silverbullet.service <<EOF
[Unit]
Description=SilverBullet notes for Irate-Box (/notes/)
After=network.target

[Service]
User=$HUB_USER
Group=$HUB_USER
# silverbullet.env (the owner's: SB_USER, SB_READ_ONLY) cannot undo what is set on the command
# line below, as it could an Environment= line here (an EnvironmentFile wins over those):
#   - SB_SHELL_BACKEND=off: no shell commands at /notes/.shell;
#   - a socket, not a port: with no IP networking at all (RestrictAddressFamilies), its HTTP
#     proxy (/notes/.proxy) cannot reach the hub's loopback API, which trusts the web server
#     in front to have asked for the login. The web server also refuses /notes/.shell,
#     .proxy and .runtime outright.
#   - the prefix must match the web server's route, which passes /notes through unstripped.
EnvironmentFile=-$ETC/silverbullet.env
ExecStart=/usr/bin/env SB_SHELL_BACKEND=off SB_UNIX_SOCKET=$SB_SOCKET SB_URL_PREFIX=/notes SB_FOLDER=$STATE/notes /usr/local/bin/silverbullet
# As root (+): the socket to the web server's group, once SilverBullet has made it (the start
# timeout bounds the wait).
ExecStartPost=+/bin/sh -c 'until [ -S $SB_SOCKET ]; do sleep 0.1; done; chgrp $sb_group $SB_SOCKET && chmod 660 $SB_SOCKET'
RuntimeDirectory=silverbullet
RuntimeDirectoryMode=0755
WorkingDirectory=$STATE/notes
Restart=on-failure
# Writes only its notes folder: never the root helper's queue or anything else the hub owns.
ProtectSystem=strict
ReadWritePaths=$STATE/notes
PrivateTmp=yes
PrivateDevices=yes
ProtectHome=yes
NoNewPrivileges=yes
RestrictAddressFamilies=AF_UNIX
IPAddressDeny=any
CapabilityBoundingSet=
RestrictNamespaces=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
SystemCallArchitectures=native
SystemCallFilter=@system-service

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
	state_dir "$HUB_USER" "$HUB_USER" 755 "$STATE/zim"
	for z in "${ZIMS[@]}"; do
		case "$z" in
		http://* | https://*)
			f="$STATE/zim/$(basename "${z%%\?*}")"
			if [ ! -s "$f" ]; then
				# -C - resumes a .part left by an earlier, interrupted run. As the hub, in the
				# hub's folder (as root, a link at $f.part would be written through).
				runuser -u "$HUB_USER" -- curl -fL --retry 5 -C - -o "$f.part" "$z"
				runuser -u "$HUB_USER" -- mv -T "$f.part" "$f"
			fi
			;;
		*)
			[ -f "$z" ] || die "--zim: no such file: $z"
			f="$STATE/zim/$(basename "$z")"
			# Root reads the owner's file; the hub writes the copy in its own folder.
			if [ "$(realpath "$z")" != "$f" ]; then
				runuser -u "$HUB_USER" -- sh -c 'cat >"$1.part" && mv -T "$1.part" "$1"' sh "$f" <"$z" ||
					die "--zim: could not copy $z to $f"
			fi
			;;
		esac
		chown -h "$HUB_USER:$HUB_USER" "$f"
	done
fi

# Written whenever Kiwix is in use, not only when --zim adds a book, so a rerun brings an
# existing box's unit up to date (the memory settings below arrived after first installs).
if [ "$KIWIX" = 1 ] || [ -f /etc/systemd/system/kiwix.service ]; then
	cat >/etc/systemd/system/kiwix.service <<EOF
[Unit]
Description=Kiwix offline library for Irate-Box (/wiki/)
After=network.target

[Service]
User=$HUB_USER
Group=$HUB_USER
# --urlRootLocation must match the web server's route, which passes /wiki through unstripped.
# --monitorLibrary picks up books added later with kiwix-manage, without a restart.
# Memory: libzim keeps decompressed clusters per worker thread. Measured on a Luckfox Lyra
# (top-mini Wikipedia): defaults grow to ~104 MB anon after searching;
# 2 threads + 4 cached clusters hold ~22 MB with no loss in search latency.
# ZIM_CLUSTERCACHE is a cluster *count*, not bytes - a large value OOMs the box.
Environment=ZIM_CLUSTERCACHE=4
ExecStart=/usr/bin/kiwix-serve --threads 2 --library --monitorLibrary --blockexternal --nodatealiases --address 127.0.0.1 --port 8081 --urlRootLocation /wiki $STATE/zim/library.xml
Restart=on-failure
# It reads the books and writes nothing.
$HUB_SANDBOX

[Install]
WantedBy=multi-user.target
EOF
fi

# This box's guide as a book, where Kiwix is chosen (irate_box/library/guide.py, from docs/guide): made again
# each run, so it describes the code installed; taken out with Kiwix.
GUIDE_ZIM="$STATE/zim/irate-box-guide.zim"
if [ "$KIWIX" = 1 ] && [[ " ${REMOVE[*]} " != *" kiwix "* ]]; then
	state_dir "$HUB_USER" "$HUB_USER" 755 "$STATE/zim"
	runuser -u "$HUB_USER" -- "$CODE/irate-box" guide zim "$GUIDE_ZIM" "$(cut -d' ' -f1 "$CODE/VERSION" 2>/dev/null)" >/dev/null ||
		problem "the guide was not made as a book: $CODE/irate-box guide zim says why"
else
	rm -f "$GUIDE_ZIM"
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
			problem "skipped $(basename "$f"): kiwix-manage could not read it (health.py says why; it can set it aside)"
	done
	if [ -f "$lib.new" ]; then
		mv "$lib.new" "$lib"
	elif [ -f "$lib" ]; then
		# Not one book could be read: a library from an earlier run would point at them anyway.
		mv -f "$lib" "$lib.old"
		echo "    no readable book: the old library is set aside as library.xml.old"
	fi
	SWEPT=1
fi
# Kiwix starts only with a library that has a book in it: kiwix-serve exits at once without
# one, and systemd would retry it into its start limit.
KIWIX_READY=0
[ -f "$STATE/zim/library.xml" ] && grep -q '<book ' "$STATE/zim/library.xml" && KIWIX_READY=1

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

# --- IRC server ----------------------------------------------------------------------
# ngIRCd, for chat in any IRC app (the pages cannot speak raw IRC, so, like MQTT, :6667 is a
# second listener on the network). Debian's unit already runs it as the unprivileged `irc`
# user with a hardened sandbox; what is added here is irate-box's own config, which the unit
# is pointed at by a drop-in, so Debian's /etc/ngircd/ngircd.conf (a package conffile) is
# never touched. There is nobody to authenticate on an open network, so as with MQTT the
# limits are the abuse control: 64 connections, 5 per address, 10 channels each.
#  - DNS and Ident off: a box with no internet would otherwise wait out a timeout for every
#    client that connects (or never finish registering it).
#  - Hosts are cloaked and the user name is the nickname: guests on one network can see each
#    other's addresses otherwise. MorePrivacy hides idle and sign-on times and quit messages.
#  - No [Operator] block: nobody can oper up, so nobody is above the channel rules. #lobby
#    is made at start-up, stays when empty, and nobody holds ops there, which locks its topic.
if [ "$WITH_IRC" = 1 ]; then
	install -d -m 755 /etc/ngircd
	cat >/etc/ngircd/irate-box.conf <<'EOF'
# Generated by irate-box install.sh. Edits are overwritten on reinstall.
[Global]
	Name = irc.irate-box
	Info = Irate-Box chat
	Network = Irate-Box
	AdminInfo1 = An Irate-Box
	AdminInfo2 = Chat for whoever is on this network
	MotdFile = /etc/ngircd/irate-box.motd
	PidFile = /run/ngircd/ngircd.pid
	# Every interface: on the hotspot, the floor (Security) decides whether guests reach it.
	Ports = 6667
	ServerUID = irc
	ServerGID = irc

[Limits]
	MaxConnections = 64
	MaxConnectionsIP = 5
	MaxJoins = 10
	MaxNickLength = 20
	PingTimeout = 120
	PongTimeout = 20

[Options]
	DNS = no
	Ident = no
	PAM = no
	MorePrivacy = yes
	CloakUserToNick = yes
	# %x is a hash of the real host: one device keeps one name, which a channel op can ban.
	CloakHost = %x.irate-box

[Channel]
	Name = #lobby
	Topic = Welcome to the Irate-Box lobby
	Modes = tn
EOF
	cat >/etc/ngircd/irate-box.motd <<'EOF'
Welcome to the Irate-Box chat.
Everyone here is on this box's own network; nothing leaves it.
There are no accounts, and nothing is encrypted: say what you would say aloud.
Join #lobby to say hello.
EOF
	chmod 644 /etc/ngircd/irate-box.conf /etc/ngircd/irate-box.motd
	install -d -m 755 /etc/systemd/system/ngircd.service.d
	cat >/etc/systemd/system/ngircd.service.d/irate-box.conf <<'EOF'
# Generated by irate-box install.sh: run ngIRCd with irate-box's config, not Debian's example.
[Service]
ExecStart=
ExecStart=/usr/sbin/ngircd -f /etc/ngircd/irate-box.conf
EOF
	# A config it cannot read would leave the unit failing; say so now, in words.
	ngircd --configtest -f /etc/ngircd/irate-box.conf >/dev/null 2>&1 ||
		problem "ngircd does not accept /etc/ngircd/irate-box.conf: ngircd --configtest -f /etc/ngircd/irate-box.conf"
fi

# --- Excalidraw live collaboration ---------------------------------------------------
# excalidraw-room is a socket.io relay: no database, rooms in memory, and scenes and
# pasted files persisted through store.py like everything else. Built on a desktop
# (dist/ plus production node_modules/, all plain JS) and copied in; nothing compiles
# here. Measured on a Luckfox Lyra: ~8 MB anon idle, ~19 MB with twelve busy clients.
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
$HUB_SANDBOX

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
# No credential of its own (ttyd's was the admin password, on its command line, readable
# in /proc by every process for as long as it ran). It listens on a socket only the web
# server's group can open, so the web server's admin login is the gate, and a stray start is
# not a root shell on loopback. The terminal itself is /bin/login: a real account is needed.
rm -f "$ETC/ttyd.env"
if [ "$WEB" = nginx ]; then term_group="$ngx_group"; else term_group="$(id -gn caddy 2>/dev/null || echo root)"; fi
cat >/etc/systemd/system/ttyd.service <<EOF
[Unit]
Description=ttyd terminal for Irate-Box (/term/), off unless install.sh --with-term
After=network.target

[Service]
ExecStart=/usr/local/bin/ttyd --interface $TTYD_SOCKET --base-path /term --check-origin --writable /bin/login
# As root (+): the socket to the web server's group, once ttyd has made it.
ExecStartPost=+/bin/sh -c 'until [ -S $TTYD_SOCKET ]; do sleep 0.1; done; chgrp $term_group $TTYD_SOCKET && chmod 660 $TTYD_SOCKET'
RuntimeDirectory=ttyd
RuntimeDirectoryMode=0755
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

# --- admin control (root helper) ---------------------------------------------------
# /admin starts and stops services and changes the admin password, which need root. The
# hub only queues a request in $STATE/control/requests/; this path unit runs hub_control.py
# as root, which acts on its own allow-list only and answers in $STATE/control/results/.
control_dirs
cat >/etc/systemd/system/irate-box-control.service <<EOF
[Unit]
Description=Irate-Box: carry out /admin requests that need root (services, admin password)
# Started by the path unit for every request; quick clicks on /admin must not trip systemd's
# start limit (5 in 10 s), which would leave every later request unanswered until a reboot.
StartLimitIntervalSec=0

[Service]
Type=oneshot
Environment=HUB_STATE_DIR=$STATE HUB_ETC_DIR=$ETC HUB_USER=$HUB_USER HUB_CODE_DIR=$CODE HUB_WEB_SERVER=$WEB HUB_UNCLAIMED_FILE=$UNCLAIMED_MARK
ExecStart=$CODE/irate-box hub_control
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

# --- this box's own source (the AGPL's offer) -----------------------------------------------
# The hub is AGPL-3.0-or-later: everyone who uses it over the network may have its source. An
# offline box has to offer that itself, so the installed code goes out three ways: a tarball at
# /source (in root-owned $STATE/source: root never writes into a hub-owned folder), a public
# repository on the box's git server (irate-box-source.git, browse or clone; nobody pushes to
# it), and a pinned file in the file drop (never expires; guests cannot remove it). The git and
# drop copies are written as the hub user.
say "Publishing this box's own source"
src_ver="$(cut -d' ' -f1 "$CODE/VERSION" 2>/dev/null | tr -cd 'A-Za-z0-9._-')"
# No git version (installed from a copy without its history): name it after the install date.
case "$src_ver" in "" | unknown) src_ver="local-$(date -u +%Y%m%d)" ;; esac
state_dir root root 755 "$STATE/source" "$STATE/kits"
src_tar="$STATE/source/irate-box-source.tar.gz"
src_tmp="$(mktemp "$STATE/source/.irate-box-source.XXXXXX")"
if tar -C "$(dirname "$CODE")" --exclude=__pycache__ --exclude='*.pyc' \
	--transform "s,^$(basename "$CODE"),irate-box-$src_ver," -czf "$src_tmp" "$(basename "$CODE")"; then
	chmod 644 "$src_tmp"
	mv -f "$src_tmp" "$src_tar"
	# The name the download is offered under, the same in all three places.
	printf 'irate-box-source-%s.tar.gz\n' "$src_ver" >"$STATE/source/name"
	chmod 644 "$STATE/source/name"
	echo "    /source: irate-box-$src_ver ($(du -h "$src_tar" | cut -f1))"
else
	rm -f "$src_tmp"
	problem "could not make the source tarball for /source"
fi
src_repo="$STATE/git/public/irate-box-source.git"
if [ -f "$src_tar" ] && runuser -u "$HUB_USER" -- sh -c '
	set -e
	repo="$1" tar="$2" ver="$3"
	[ -d "$repo" ] || git init -q --bare -b main "$repo"
	git -C "$repo" config http.receivepack false
	git -C "$repo" config irate-box.write nobody  # public-read-only on the Git page: the box writes it, nobody pushes
	printf "This box'"'"'s own source: irate-box %s, as installed (AGPL-3.0-or-later; see LICENSES/)\n" "$ver" >"$repo/description"
	scratch="$(mktemp -d)"; trap "rm -rf \"$scratch\"" EXIT
	# The tarball, unpacked: the same files as /source, root/ included (unreadable in $CODE here).
	mkdir "$scratch/tree"
	tar -xzf "$tar" -C "$scratch/tree" --strip-components=1
	export GIT_DIR="$repo" GIT_WORK_TREE="$scratch/tree" GIT_INDEX_FILE="$scratch/index"
	export GIT_AUTHOR_NAME="irate-box" GIT_AUTHOR_EMAIL="hub@irate-box.local" GIT_COMMITTER_NAME="irate-box" GIT_COMMITTER_EMAIL="hub@irate-box.local"
	git add -A -f -- . ":(exclude)*__pycache__*" ":(exclude)*.pyc"
	tree="$(git write-tree)"
	parent="$(git rev-parse -q --verify refs/heads/main || true)"
	if [ "$tree" != "$(git rev-parse -q --verify "refs/heads/main^{tree}" || true)" ]; then
		commit="$(git commit-tree "$tree" ${parent:+-p "$parent"} -m "irate-box $ver, as installed on this box")"
		git update-ref refs/heads/main "$commit"
	fi
' sh "$src_repo" "$src_tar" "$src_ver"; then
	echo "    /git/irate-box-source.git: browse or clone it"
else
	problem "could not put the source on the git server (/git/irate-box-source.git)"
fi
if [ -f "$src_tar" ]; then
	runuser -u "$HUB_USER" -- env HUB_STATE_DIR="$STATE" "$CODE/irate-box" store pin-drop "$STATE" "$src_tar" \
		"irate-box-source-$src_ver.tar.gz" | sed 's/^/    /' || problem "could not pin the source in the file drop"
fi

# --- the network: inventory and the uplink watchdog ---------------------------------------
# netinv.py looks at the radios and the stack that runs them (read-only); uplink.py watches
# the link and repairs it as eagerly as chosen. Settings in $ETC/uplink.json: written here
# only by --uplink (or the first time, as the defaults), otherwise by /admin's Network page
# through the root helper, so an update keeps what the owner chose there.
say "Looking at the network"
"$CODE/irate-box" netinv --write "$STATE/control/netinv.json" >/dev/null 2>&1 || notice "netinv.py could not look at the network; /admin's Network page can try again."
if [ -n "$UPLINK" ] || [ ! -f "$ETC/uplink.json" ]; then
	IFS=, read -r -a up <<<"${UPLINK:-pace=gentle,reach=reboot,sensitivity=3}"
	HUB_ETC_DIR="$ETC" "$CODE/irate-box" uplink set "${up[@]}" >/dev/null || problem "--uplink $UPLINK: not taken (uplink.py set)"
fi
# A clock module on I2C: found and set up if there is exactly one (rtc.py auto), kept if set up
# before. Nothing in /boot or the device tree changes.
if [ "$RTC" = auto ] || [ -f "$ETC/rtc.json" ]; then
	if out="$(HUB_STATE_DIR="$STATE" HUB_ETC_DIR="$ETC" timeout 120 "$CODE/irate-box" rtc auto 2>&1)"; then
		echo "    $out"
		case "$out" in *through* | *choose*) notice "$out" ;; esac
	else
		problem "looking for a clock module failed: $out"
	fi
fi
# Crash watch (crashwatch.py): always running, small. Its start notes the boot (and files the last one
# as a crash if it never shut down); its stop marks a clean shutdown.
cat >/etc/systemd/system/irate-box-crashwatch.service <<EOF
[Unit]
Description=Irate-Box crash watch: what the box was doing when it stopped (crashwatch.py; /admin, Box doctor)
After=local-fs.target systemd-journald.service

[Service]
Type=simple
Environment=HUB_STATE_DIR=$STATE HUB_ETC_DIR=$ETC PYTHONUNBUFFERED=1
ExecStartPre=$CODE/irate-box crashwatch boot
ExecStart=$CODE/irate-box crashwatch run
ExecStopPost=$CODE/irate-box crashwatch shutdown
Restart=always
RestartSec=10
Nice=-5

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/irate-box-uplink.service <<EOF
[Unit]
Description=Irate-Box uplink watchdog: keep the box on its network (uplink.py; /admin, Network)
After=network.target NetworkManager.service

[Service]
Type=simple
Environment=HUB_STATE_DIR=$STATE HUB_ETC_DIR=$ETC PYTHONUNBUFFERED=1
ExecStart=$CODE/irate-box uplink run
Restart=always
RestartSec=10
Nice=5

[Install]
WantedBy=multi-user.target
EOF

# --- the librarian -----------------------------------------------------------------
# librarian.py keeps ZIM books current from the sources set on /admin (Library). It runs
# as the hub user: a new version is swapped in under the same file name and library.xml is
# rebuilt, which kiwix-serve's --monitorLibrary picks up. No restart, so no root needed.
state_dir "$HUB_USER" "$HUB_USER" 755 "$STATE/zim" "$STATE/library" "$STATE/firmware"
cat >/etc/systemd/system/irate-box-librarian.service <<EOF
[Unit]
Description=Irate-Box librarian: update the ZIM books whose check is due (/admin, Library)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$HUB_USER
Group=$HUB_USER
# The hub's own settings (hub.env): the git, firmware and other roots it keeps. Without them the
# mirrors' repositories resolved under $CODE (read-only), and every scheduled run stopped there,
# before the books' and the hub's own updates.
EnvironmentFile=$ETC/hub.env
Environment=HUB_STATE_DIR=$STATE
ExecStart=$CODE/irate-box librarian update --scheduled
# A download competes with guests for the card and the CPU; let it lose.
Nice=10
IOSchedulingClass=idle
$HUB_SANDBOX
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

# The security doctor, daily: a read-only audit as root, its report
# where /admin reads it, so a new listener, sudo rule or port forward is seen without anyone
# pressing the button; the page says what is new since the last run.
cat >/etc/systemd/system/irate-box-secdoctor.service <<EOF
[Unit]
Description=Irate-Box security doctor: a read-only audit, its report for /admin

[Service]
Type=oneshot
ExecStart=$CODE/irate-box secdoctor run
Nice=19
IOSchedulingClass=idle
ProtectSystem=strict
ReadWritePaths=$STATE/control
PrivateTmp=yes
NoNewPrivileges=yes
ProtectHome=read-only
EOF
# The network floor (root/firewall.py): loads /etc/hub/firewall.nft when the Security page has
# written one, nothing otherwise; the page enables it, uninstall.sh takes it away.
PYTHONPATH="$CODE" python3 -c "from irate_box.root import firewall; print(firewall.unit_text(\"$CODE\"), end=\"\")" >/etc/systemd/system/irate-box-firewall.service

cat >/etc/systemd/system/irate-box-secdoctor.timer <<EOF
[Unit]
Description=Irate-Box security doctor, daily

[Timer]
OnBootSec=30min
OnUnitActiveSec=1d
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

# --- Unique visitors ---------------------------------------------------------------
# A helper of its own reads the leases and the neighbour table every minute, hashes each address
# with a salt made for the day (and one for the week) that it holds only in memory, and writes two
# numbers for the hub. It runs only while counting is on: the hub records the owner's choice in
# $STATE/visitors.want (on by default, one of the setup's decisions) and the path unit applies it.
cat >/etc/systemd/system/irate-box-visitors.service <<EOF
[Unit]
Description=Irate-Box: unique visitors, counted from salted hashes kept in memory only

[Service]
ExecStart=$CODE/irate-box visitors
# Not root: the leases and /proc/net/arp are readable by anyone; it writes one file in its runtime folder.
DynamicUser=yes
RuntimeDirectory=irate-box-visitors
RuntimeDirectoryMode=0755
Restart=on-failure
MemoryDenyWriteExecute=yes
$HUB_SANDBOX

[Install]
WantedBy=multi-user.target
EOF
cat >/etc/systemd/system/irate-box-visitors-switch.service <<EOF
[Unit]
Description=Irate-Box: apply the visitor-counting switch ($STATE/visitors.want)

[Service]
Type=oneshot
Environment=HUB_STATE_DIR=$STATE
ExecStart=$CODE/scripts/visitors-apply.sh
EOF
cat >/etc/systemd/system/irate-box-visitors-switch.path <<EOF
[Unit]
Description=Irate-Box: watch the visitor-counting switch

[Path]
PathChanged=$STATE/visitors.want
Unit=irate-box-visitors-switch.service

[Install]
WantedBy=multi-user.target
EOF
if [ ! -f "$STATE/visitors.want" ]; then
	# Written as the hub, in its folder: on, as the setting's default.
	echo on | runuser -u "$HUB_USER" -- sh -c 'cat >"$1"' sh "$STATE/visitors.want"
fi
systemctl daemon-reload
systemctl enable --quiet --now irate-box-visitors-switch.path || problem "irate-box-visitors-switch.path did not start"
HUB_STATE_DIR="$STATE" "$CODE/scripts/visitors-apply.sh" || problem "visitor counting: journalctl -u irate-box-visitors -n 30"

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
		# Written as the hub, in its folder.
		if systemctl is-active --quiet tailscaled; then echo on; else echo off; fi |
			runuser -u "$HUB_USER" -- sh -c 'cat >"$1"' sh "$STATE/tailscale.want"
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
Environment=HUB_STATE_DIR=$STATE HUB_USER=$HUB_USER
ExecStart=$CODE/scripts/tailscale-apply.sh
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
Environment=HUB_STATE_DIR=$STATE HUB_USER=$HUB_USER
ExecStart=$CODE/scripts/tailscale-apply.sh --boot

[Install]
WantedBy=multi-user.target
EOF
fi

# --- start -----------------------------------------------------------------------
say "Starting services"
systemctl daemon-reload
units=(irate-box "$WEB" irate-box-git.socket)
[ "$WITH_NOTES" = 1 ] && units+=(silverbullet)
[ "$WITH_SYNC" = 1 ] && units+=("syncthing@$HUB_USER")
if [ "$KIWIX" = 1 ] && [ "$KIWIX_READY" = 1 ]; then
	units+=(kiwix)
elif [ "$KIWIX" = 1 ]; then
	systemctl disable --quiet --now kiwix 2>/dev/null || true
	systemctl reset-failed kiwix 2>/dev/null || true
	if compgen -G "$STATE/zim/*.zim" >/dev/null; then
		problem "Kiwix not started: none of the books in $STATE/zim can be read. Add one (Library → Books, a USB stick, --zim); health.py says what is wrong with these"
	else
		echo "    kiwix: installed, waiting for a first book (Library → Books, a USB stick); the box doctor starts it then"
	fi
fi
[ "$WITH_TERM" = 1 ] && units+=(ttyd)
[ "$WITH_MQTT" = 1 ] && units+=(mosquitto)
[ "$WITH_IRC" = 1 ] && units+=(ngircd)
[ "$WITH_COLLAB" = 1 ] && units+=(excalidraw-room)
# Apps switched off on /admin (Apps, Add-ons) keep their services stopped.
mapfile -t off_units < <(HUB_ETC_DIR="$ETC" HUB_STATE_DIR="$STATE" HUB_USER="$HUB_USER" HUB_WEB_SERVER="$WEB" \
	"$CODE/irate-box" hub_control access-off-units 2>/dev/null)
# One at a time: a unit that will not start is a problem to report, not a reason to stop.
for u in "${units[@]}"; do
	if printf '%s\n' "${off_units[@]}" | grep -qxF "$u.service"; then
		echo "    $u: left stopped (switched off on /admin)"
		continue
	fi
	systemctl enable --quiet "$u" || problem "$u could not be enabled: systemctl status $u"
	systemctl restart "$u" || problem "$u did not start: journalctl -u $u -n 30"
done
# Turned on by an earlier run: keep it on, with the current credential.
[ "$WITH_TERM" = 1 ] || systemctl try-restart ttyd
# A ZIM replaced under the same name stays open in kiwix-serve until it restarts;
# --monitorLibrary only notices library.xml changing, not the files it points at.
[ "$KIWIX" = 1 ] || [ "$SWEPT" = 0 ] || systemctl try-restart kiwix
# SilverBullet's manual in the notes, under SilverBullet/, once SilverBullet has written its starting page
# (on its first start), so that page's links to the manual can point at the copy.
if [ "$WITH_NOTES" = 1 ] && systemctl is-active --quiet silverbullet; then
	for _ in $(seq 1 30); do [ -f "$STATE/notes/index.md" ] && break; sleep 1; done
	tmp="$(mktemp -d)"
	got=0; sb_docs_get "$tmp/$SB_DOCS_NAME" || got=$?
	if [ "$got" = 0 ]; then
		[ -s "$OWN_CACHE/$SB_DOCS_NAME" ] || install -D -m 644 "$tmp/$SB_DOCS_NAME" "$OWN_CACHE/$SB_DOCS_NAME" || true
		chmod 755 "$tmp" && chmod 644 "$tmp/$SB_DOCS_NAME"
		if said="$(runuser -u "$HUB_USER" -- "$CODE/irate-box" sbdocs install "$tmp/$SB_DOCS_NAME" "$STATE/notes" "$SB_VERSION" 2>&1)"; then
			echo "    $said"
		else
			problem "SilverBullet's manual was not put in the notes: ${said##*$'\n'}"
		fi
	elif [ "$got" = 2 ]; then
		problem "SilverBullet's manual was not put in the notes: $SB_DOCS_NAME does not match its pinned digest (SB_DOCS_SHA256)"
	else
		echo "    SilverBullet's manual: not in the download cache, and no internet; the notes work without it"
	fi
	rm -rf "$tmp"
fi
for u in irate-box-librarian.timer irate-box-secdoctor.timer irate-box-control.path irate-box-ci.path irate-box-uplink.service irate-box-crashwatch.service; do
	systemctl enable --quiet --now "$u" || problem "$u did not start: journalctl -u $u -n 30"
done
# A running watchdog keeps the old code until restarted.
systemctl try-restart irate-box-uplink.service || problem "irate-box-uplink did not restart: journalctl -u irate-box-uplink -n 30"
systemctl try-restart irate-box-crashwatch.service || problem "irate-box-crashwatch did not restart: journalctl -u irate-box-crashwatch -n 30"
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
	# Through Syncthing's REST API, the password on stdin: never on a command line.
	printf '%s\n' "$ADMIN_PW" | HUB_STATE_DIR="$STATE" HUB_ETC_DIR="$ETC" "$CODE/irate-box" hub_control syncthing-gui-password ||
		problem "could not set Syncthing's GUI password (/sync/ keeps its old one)"
	if [ "$WITH_NOTES" = 1 ] && ! st config folders list | grep -qx hub-notes; then
		st config folders add --id hub-notes --label "Hub notes" --path "$STATE/notes"
	fi
fi

# --- add-ons taken off ------------------------------------------------------------
# --remove: the service stops and its unit and config go. Data and packages stay, so adding
# it back finds the notes, Syncthing's identity and so on where they were.
for r in "${REMOVE[@]}"; do
	case "$r" in
	kiwix)
		say "Removing Kiwix; the books in $STATE/zim stay (but its own guide, made for Kiwix)"
		rm -f "$STATE/zim/irate-box-guide.zim"
		systemctl disable --now kiwix >/dev/null 2>&1 || true
		rm -f /etc/systemd/system/kiwix.service
		install -d -m 755 "$ETC" && touch "$ETC/kiwix-removed" ;;
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
	irc)
		say "Removing the IRC add-on (ngircd's irate-box config; the package stays, stopped)"
		systemctl disable --now ngircd >/dev/null 2>&1 || true
		rm -f /etc/ngircd/irate-box.conf /etc/ngircd/irate-box.motd /etc/systemd/system/ngircd.service.d/irate-box.conf
		rmdir /etc/systemd/system/ngircd.service.d 2>/dev/null || true ;;
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
for u in "${units[@]}"; do
	systemctl is-active --quiet "$u" || problem "$u is not running: journalctl -u $u -n 30"
done
hostport="127.0.0.1$([ "$HUB_PORT" = 80 ] || echo ":$HUB_PORT")"
code="$(curl -s -o /dev/null -w '%{http_code}' "http://$hostport/" || true)"
[ "$code" = 200 ] || problem "http://$hostport/ answered $code: journalctl -u irate-box -u $WEB -n 30"

addr="$(ip -4 -o route get 1.1.1.1 2>/dev/null | grep -oP 'src \K[0-9.]+' || hostname -I | cut -d' ' -f1)"
where="http://${addr:-<this box>}$([ "$HUB_PORT" = 80 ] || echo ":$HUB_PORT")"
echo
say "Irate-Box is $([ $fail = 0 ] && echo up || echo "installed, with ${#PROBLEMS[@]} problem$([ ${#PROBLEMS[@]} = 1 ] || echo s) (below)") at $where/"
echo "    web server: $WEB$([ "$WEB" = caddy ] && echo ' (the fallback; --web nginx switches to the default)')"
if [ "$UNCLAIMED" = 1 ]; then
	echo "    admin: not set yet. Until it is, anyone on this network can use $where/admin/. Make the admin account"
	echo "           now: over HTTPS ($where/admin/setup with https://), or here over SSH:"
	echo "           sudo $CODE/irate-box hub_control set-admin NAME"
else
	echo "    admin login: admin / (in $ETC/admin-password)"
fi
[ "$WITH_NOTES" = 1 ] && echo "    notes: /notes/  (folder $STATE/notes, lock it down in $ETC/silverbullet.env)"
[ -f "$STATE/zim/library.xml" ] && echo "    wiki:  /wiki/   ($(grep -c '<book ' "$STATE/zim/library.xml") books in $STATE/zim/library.xml)"
[ -f /etc/mosquitto/conf.d/irate-box.conf ] && echo "    mqtt:  :1883 for nodes, /mqtt for pages (anonymous, msh/# only)"
[ -f /etc/ngircd/irate-box.conf ] && echo "    irc:   :6667 for any IRC app, channel #lobby (open to the network, unencrypted; how to join: /irc.html)"
term_state="$(systemctl is-enabled ttyd 2>/dev/null)" || true
echo "    term:  /term/   (${term_state:-disabled}; admin login, then an account on the box)"
[ "$WITH_SYNC" = 1 ] && echo "    sync:  /sync/   (behind the admin login; folder \"hub-notes\" shared if notes are installed)"
echo "    git:   /git/ (public: clone for all, push with the admin login) and /git-private/ (admin login); repositories made on /admin"
[ "$WITH_COLLAB" = 1 ] && echo "    collab: live sessions in /draw/ (relay on 127.0.0.1:3002, via /socket.io/)"
[ -f "$STATE/library/sources.json" ] && echo "    library: $(grep -c "\"name\":" "$STATE/library/sources.json") sources kept current; settings on /admin"
[ "$WITH_TAILSCALE" = 1 ] && echo "    remote: Tailscale is $(systemctl is-active tailscaled); switch it on /admin"

up_now="$(HUB_ETC_DIR="$ETC" PYTHONPATH="$CODE" python3 -c 'from irate_box.hub import uplink as u; s=u.load_settings(); print(u.words(s)+(" (custom values)" if s["overrides"] else ""))' 2>/dev/null || echo unknown)"
echo "    uplink: $up_now — how hard it works to stay on the network; change on $where/admin/#network"

# What install.sh found and did not change: said once more here, then the Security page's
# own report of the box. Nothing below is changed without the owner's say-so (/admin).
if [ "${#NOTICES[@]}" -gt 0 ]; then
	echo
	say "Worth knowing"
	for n in "${NOTICES[@]}"; do echo "    $n"; done
fi
if net="$(timeout 60 "$CODE/irate-box" netinv summary 2>/dev/null)" && [ -n "$net" ]; then
	echo
	say "The network (more, and \"look again\" for another device, on $where/admin/#network)"
	printf '%s\n' "$net" | sed 's/^/    /'
fi
if report="$(timeout 120 "$CODE/irate-box" security summary 2>/dev/null)" && [ -n "$report" ]; then
	echo
	say "Found on this box (fix or leave each on $where/admin/#security)"
	printf '%s\n' "$report" | sed 's/^/    /'
fi
# Problems: listed once more, then what the doctor makes of the box now, with what to do.
if [ "${#PROBLEMS[@]}" -gt 0 ]; then
	echo
	say "Problems during this install"
	for p in "${PROBLEMS[@]}"; do echo "    $p"; done
	if doc="$(timeout 300 "$CODE/irate-box" health summary 2>/dev/null)" && [ -n "$doc" ]; then
		echo
		say "What the doctor finds (again any time: sudo $CODE/irate-box health, or $where/admin/#health)"
		printf '%s\n' "$doc" | sed 's/^/    /'
	fi
fi
exit $fail
