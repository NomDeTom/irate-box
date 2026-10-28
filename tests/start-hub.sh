# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# shellcheck shell=bash
# Sourced by tests/run.sh and tests/jsdom/run.sh: start_hub STATE LOG [VAR=VALUE ...] starts the hub with throwaway
# state on a spare port and waits until it answers, setting $port and $pid. The port is below 32768, outside the
# range Linux hands out to outgoing connections, so a busy runner's own sockets do not take it. A hub that exits
# (its port taken after all) is started again on another port; a hub that never answers prints its log and returns 1,
# so the tests after it say why instead of "connection refused".
start_hub() {
	local state="$1" log="$2" attempt
	shift 2
	for attempt in 1 2 3; do
		port=$((20000 + RANDOM % 12000))
		env HUB_STATE_DIR="$state" PORT="$port" HUB_BIND=127.0.0.1 "$@" "${repo:?}/irate-box" server >"$log" 2>&1 &
		pid=$!
		for _ in $(seq 300); do
			curl -s -o /dev/null "http://127.0.0.1:$port/status" && return 0
			kill -0 "$pid" 2>/dev/null || break
			sleep 0.1
		done
		kill "$pid" 2>/dev/null
		wait "$pid" 2>/dev/null
		echo "the hub did not answer on port $port (attempt $attempt); its log ends:" >&2
		tail -15 "$log" >&2
	done
	return 1
}
