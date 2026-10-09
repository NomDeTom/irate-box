#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# Run what .github/workflows/ci.yml runs, here, so a branch is green before it is pushed:
# the licences (REUSE), tests/run.sh, the shell scripts' parse, and the .deb build. This is
# the "checks" and "deb" jobs of CI, in order; CI then only confirms.
#
#   tests/local-ci.sh            # everything
#   tests/local-ci.sh --quick    # skip the .deb build (the slow part)
#
# python3: taken from PATH, or the uv-managed one this dev box keeps if PATH has none.
# reuse: run in a throwaway python container (the host need not have it, as CI uses pipx);
# set REUSE="reuse" to use one already on PATH, or NO_DOCKER=1 to skip the licence check.
set -u
cd "$(dirname "$0")/.." || exit 2
# CI runners build and test with a 022 umask; this dev box's login shell is 0002, which makes
# the kits-cache "root's alone" checks fail on files the tests create. Match the runner.
umask 022
QUICK=0
for a in "$@"; do case "$a" in
	--quick) QUICK=1 ;;
	-h | --help) sed -n '2,13p' "$0"; exit 0 ;;
	*) echo "unknown option: $a" >&2; exit 2 ;;
esac; done

# python3 for tests/run.sh (this box has no system python3; fall back to the uv one).
if ! command -v python3 >/dev/null 2>&1; then
	uvpy=$(ls /home/*/.local/share/uv/python/*/bin/python3 2>/dev/null | head -1)
	[ -n "$uvpy" ] && export PATH="$(dirname "$uvpy"):$PATH"
fi
command -v python3 >/dev/null 2>&1 || { echo "no python3 on PATH and none under uv" >&2; exit 2; }

rc=0
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "Licences (REUSE)"
REUSE=${REUSE:-}
if [ -n "$REUSE" ]; then
	$REUSE lint || rc=$((rc + 1))
elif [ "${NO_DOCKER:-0}" != 1 ] && command -v docker >/dev/null 2>&1; then
	# As CI's `pipx run reuse lint`, in a container so the host needs nothing.
	docker run --rm -v "$PWD":/d:ro -w /d python:3.12-slim \
		sh -c 'pip install -q reuse >/dev/null 2>&1 && reuse lint' || rc=$((rc + 1))
else
	echo "   skipped (no reuse on PATH, no Docker); set REUSE= or install reuse" >&2
fi

step "Tests (tests/run.sh)"
tests/run.sh || rc=$((rc + 1))

step "Shell scripts parse"
for f in install.sh uninstall.sh scripts/*.sh packaging/deb/build.sh tests/run.sh tests/*.sh tests/container/*.sh; do
	[ -f "$f" ] || continue
	bash -n "$f" || { echo "   parse FAILED: $f"; rc=$((rc + 1)); }
done
echo "   ok"

if [ "$QUICK" = 1 ]; then
	echo; echo "local-ci (--quick, no .deb): $rc failed"; exit $rc
fi

step "Build the .deb"
if deb=$(packaging/deb/build.sh dist); then
	echo "   $deb"
	dpkg-deb --info "$deb" >/dev/null && echo "   dpkg-deb --info: ok"
	echo "   contents: $(dpkg-deb --contents "$deb" | wc -l) entries"
else
	echo "   .deb build FAILED" >&2; rc=$((rc + 1))
fi

echo; echo "local-ci: $rc failed"
exit $rc
