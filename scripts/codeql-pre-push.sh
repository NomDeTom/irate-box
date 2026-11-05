#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The local CodeQL proof before a push, but only when the push changes something it proves: the model
# pack, validate.py, the fixture, or the proof script itself. Git gives the refs on stdin; this reads
# them, works out the files each ref changes, and runs tests/codeql_model_check.py when one of those
# paths is among them. It skips with one line when the CodeQL CLI is not installed.
set -u
repo=$(cd "$(dirname "$0")/.." && pwd)
cd "$repo" || exit 2

need='^(\.github/codeql/|irate_box/validate\.py$|tests/codeql_fixture/|tests/codeql_model_check\.py$)'
changed=""
while read -r _local_ref local_oid _remote_ref remote_oid; do
	[ -n "${local_oid:-}" ] || continue
	case "$local_oid" in
	*[!0]*) ;;
	*) continue ;; # the ref is being deleted
	esac
	if [ -z "${remote_oid:-}" ] || [[ "$remote_oid" =~ ^0+$ ]]; then
		# A new branch: compare with the main branch this checkout fetches.
		base=$(git merge-base "$local_oid" "${CODEQL_BASE_REF:-origin/main}" 2>/dev/null) || base=""
		if [ -z "$base" ]; then
			changed+=$(git log --name-only --pretty=format: "$local_oid" --not --remotes)$'\n'
			continue
		fi
		range="$base..$local_oid"
	else
		range="$remote_oid..$local_oid"
	fi
	changed+=$(git diff --name-only "$range")$'\n'
done

if ! printf '%s' "$changed" | grep -Eq "$need"; then
	exit 0
fi

codeql=${CODEQL:-}
if [ -z "$codeql" ]; then
	codeql=$(command -v codeql 2>/dev/null || true)
fi
if [ -z "$codeql" ]; then
	codeql="$HOME/.local/opt/codeql/codeql"
fi
if [ ! -x "$codeql" ]; then
	echo "CodeQL model check skipped: the CodeQL CLI is not installed."
	exit 0
fi

lock=${TESTS_LOCK:-/home/user/irate-box-wt/scratch/tests.lock}
mkdir -p "$(dirname "$lock")" 2>/dev/null || true
exec flock "$lock" env CODEQL="$codeql" python3 tests/codeql_model_check.py
