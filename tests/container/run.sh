#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Build the systemd test box (tests/container/Dockerfile) and run the ct-*.sh checks in it,
# the same way CI would if CI had a systemd container. Each check runs inside a throwaway
# container with the hub freshly installed by install.sh; see each ct-*.sh for what it asserts.
#
#   tests/container/run.sh                 # the default set (nginx + caddy fronts)
#   tests/container/run.sh check-nginx     # one scenario by name
#   tests/container/run.sh --list          # the scenario names
#   tests/container/run.sh --rebuild all   # rebuild the image first, then every scenario
#   tests/container/run.sh --keep net      # leave the container up afterwards to poke at
#
# Needs Docker with systemd-in-a-container (cgroup v2): Docker Desktop on WSL, or a Linux host.
# The image bakes in the apt packages install.sh needs, so a run needs the network only the
# first time (to build the image). The hub's own release downloads (ttyd, SilverBullet) are
# add-on-only; scenarios that need them say so and take a --download-cache.
set -u
cd "$(dirname "$0")/../.." || exit 2            # the checkout root
HERE=tests/container
IMAGE=irate-box-ct:trixie
DOCKER=${DOCKER:-docker}
KEEP=0 REBUILD=0

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }

# --- the image ---------------------------------------------------------------------------
build_image() {
	if [ "$REBUILD" = 1 ] || ! $DOCKER image inspect "$IMAGE" >/dev/null 2>&1; then
		say "Building $IMAGE"
		$DOCKER build -t "$IMAGE" -f "$HERE/Dockerfile" "$HERE" || exit 2
	fi
}

# --- a container's life ------------------------------------------------------------------
# boot NAME -> prints the container id; waits until systemd is up. The checkout is mounted
# read-only at /src; install.sh is run from there with --src /src.
CID=""
boot() {
	local name=$1
	$DOCKER rm -f "$name" >/dev/null 2>&1 || true
	CID=$($DOCKER run -d --name "$name" --privileged --cgroupns=host \
		--tmpfs /run --tmpfs /run/lock -v /sys/fs/cgroup:/sys/fs/cgroup \
		-v "$PWD":/src:ro "$IMAGE") || return 1
	local i
	for i in $(seq 60); do
		case "$($DOCKER exec "$CID" systemctl is-system-running 2>/dev/null)" in
			running | degraded) return 0 ;;
		esac
		sleep 0.5
	done
	echo "   systemd did not come up in the container" >&2
	$DOCKER exec "$CID" systemctl is-system-running 2>&1 | sed 's/^/   /' >&2
	return 1
}
cexec() { $DOCKER exec "$CID" "$@"; }
# install OPTS... : run install.sh inside the booted container.
install_hub() {
	note "install.sh $*"
	cexec bash /src/install.sh --src /src "$@"
}
# claim PW : do the first-use claim so /etc/hub/admin-password holds PW (the ct-*.sh read it,
# or hardcode correct-horse-1). Returns non-zero and prints why if the password never took.
claim() {
	local pw=$1 i got
	cexec bash -c "curl -s -X POST -H 'Content-Type: application/json' -H 'X-Irate-Admin: 1' \
		-d '{\"password\":\"$pw\"}' http://127.0.0.1:80/admin/setup >/dev/null" || return 1
	for i in $(seq 60); do
		got=$(cexec bash -c "curl -s -o /dev/null -w '%{http_code}' -u 'admin:$pw' http://127.0.0.1:80/admin/" 2>/dev/null)
		[ "$got" = 200 ] && return 0
		sleep 0.5
	done
	echo "   the claimed password never started working (last code: ${got:-none})" >&2
	return 1
}
destroy() {
	[ "$KEEP" = 1 ] && { note "kept: $DOCKER exec -it ${CID:0:12} bash"; return; }
	[ -n "$CID" ] && $DOCKER rm -f "$CID" >/dev/null 2>&1 || true
}

# --- scenarios ---------------------------------------------------------------------------
# Each boots a container, installs, and runs one or more ct-*.sh. It must leave $rc at the
# number of failed checks (0 = pass). CT=/src/tests/container inside the container.
CT=/src/tests/container
rc=0
# run_ct SCRIPT ARGS... : run a ct-*.sh in the container, echo its output, add its exit to $rc.
run_ct() {
	local s=$1; shift
	say "ct-$s.sh $*"
	cexec bash "$CT/ct-$s.sh" "$@"
	rc=$((rc + $?))
}

# One nginx box, installed once with the uplink ct-net wants, claimed, then the ct-*.sh that a
# plain install can satisfy (the suite the Lyra runs behind nginx). ct-access and ct-flasher are
# not here: they need a web-flasher bundle present (see scenario_flasher).
scenario_nginx() {
	boot irate-box-ct-nginx || return 1
	install_hub --web nginx --uplink standard,strict || { rc=$((rc + 1)); return; }
	run_ct check nginx 80 claim      # claims the admin password (correct-horse-1)
	run_ct layout 80
	run_ct git nginx
	run_ct net 80
	run_ct floor
	run_ct health 80
	run_ct ci
}

# The same suite behind Caddy (Debian's own caddy, --web caddy).
scenario_caddy() {
	boot irate-box-ct-caddy || return 1
	install_hub --web caddy || { rc=$((rc + 1)); return; }
	run_ct check caddy 80 claim
	run_ct git caddy
}

# The web flasher and the access switch: both need a flasher bundle installed (the fork's
# one-file build). Set FLASHER_BUNDLE to a directory holding it (what install.sh --apps expects
# under apps/flasher), or FLASHER_APPS to an --apps dir. Without it, there is no /flasher/ to
# test, so these are skipped. NOTE: ct-access also still asserts /git/ and /wiki/ on the main
# origin (401/200), from before those moved to their own origin; update it before relying on it.
scenario_flasher() {
	local apps=${FLASHER_APPS:-}
	[ -n "$apps" ] && [ -d "$apps/flasher" ] || { note "skipped: set FLASHER_APPS to an --apps dir holding flasher/ (a built web-flasher bundle)"; return 0; }
	boot irate-box-ct-flasher || return 1
	$DOCKER cp "$apps" "$CID":/apps >/dev/null || { rc=$((rc + 1)); return; }
	install_hub --web nginx --apps /apps || { rc=$((rc + 1)); return; }
	claim correct-horse-1 || { rc=$((rc + 1)); return; }
	run_ct flasher 127.0.0.1
	run_ct access 80
}

# A rerun keeps the add-ons already on the box. Needs ttyd, so a --download-cache with it.
scenario_addons() {
	local dc=${DOWNLOAD_CACHE:-}
	[ -n "$dc" ] || { note "skipped: set DOWNLOAD_CACHE to a dir holding ttyd (install.sh --download-cache)"; return 0; }
	boot irate-box-ct-addons || return 1
	$DOCKER cp "$dc" "$CID":/dl >/dev/null || { rc=$((rc + 1)); return; }
	install_hub --web nginx --with-term --download-cache /dl || { rc=$((rc + 1)); return; }
	run_ct addons-kept
	run_ct kiwix-addon
}

# Books: needs a real mermaid-docs.zim. Set ZIM to its path.
scenario_zim() {
	local zim=${ZIM:-}
	[ -n "$zim" ] && [ -f "$zim" ] || { note "skipped: set ZIM to a mermaid-docs.zim file"; return 0; }
	boot irate-box-ct-zim || return 1
	install_hub --web nginx || { rc=$((rc + 1)); return; }
	$DOCKER cp "$zim" "$CID":/var/lib/hub/zim/mermaid-docs.zim >/dev/null
	cexec chown hub:hub /var/lib/hub/zim/mermaid-docs.zim
	run_ct zim 80
}

# Accounts under Caddy: self-contained (its own non-systemd container; see its header).
scenario_caddy_accounts() {
	say "ct-caddy-accounts.sh (standalone)"
	$DOCKER run --rm -v "$PWD":/src:ro debian:trixie bash /src/tests/container/ct-caddy-accounts.sh
	rc=$((rc + $?))
}

declare -A SCENARIOS=(
	[nginx]=scenario_nginx
	[caddy]=scenario_caddy
	[flasher]=scenario_flasher
	[addons]=scenario_addons
	[zim]=scenario_zim
	[caddy-accounts]=scenario_caddy_accounts
)
DEFAULT="nginx caddy"
# Aliases so a single ct name maps to the scenario that runs it.
alias_of() { case "$1" in
	check-nginx | git-nginx | health | net | layout | ci) echo nginx ;;
	check-caddy | git-caddy) echo caddy ;;
	access | flasher-page) echo flasher ;;
	addons-kept) echo addons ;;
	*) echo "$1" ;;
esac; }

# --- arguments ---------------------------------------------------------------------------
want=()
for a in "$@"; do case "$a" in
	--keep) KEEP=1 ;;
	--rebuild) REBUILD=1 ;;
	--list) printf '%s\n' "${!SCENARIOS[@]}" | sort; exit 0 ;;
	-h | --help) sed -n '2,20p' "$0"; exit 0 ;;
	all) want=(nginx caddy flasher addons zim); ;;
	-*) echo "unknown option: $a" >&2; exit 2 ;;
	*) want+=("$(alias_of "$a")") ;;
esac; done
[ ${#want[@]} -gt 0 ] || want=($DEFAULT)

build_image
# De-duplicate (an alias and its scenario can both be asked for).
run_one() { local fn=${SCENARIOS[$1]:-}; [ -n "$fn" ] || { echo "no scenario: $1" >&2; rc=$((rc + 1)); return; }
	CID=""; "$fn"; destroy; }
seen=" "
for s in "${want[@]}"; do case "$seen" in *" $s "*) continue ;; esac; seen="$seen$s "; run_one "$s"; done

say "container tests: $rc failed"
exit $rc
