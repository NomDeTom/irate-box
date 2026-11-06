#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The hostile SVG in headless Chromium, behind the real /svg/ policy: an inline script, an on*=
# handler, a foreignObject and a javascript: link are loaded through SVG-Edit's own
# loadSvgString and as a gallery save. Nothing runs; the policy reports the inline script it
# refuses. Not CI (no browser there): skips without Playwright or Chromium.
#
#   SVGEDIT_BUNDLE=/path/to/dist/editor PLAYWRIGHT=/path/to/node_modules/playwright \
#     CHROME=~/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell \
#     CHROMELIBS=/path/to/extracted/libs tests/browser/hostile-svg.sh
#
# CHROMELIBS is a directory of extra shared libraries (set LD_LIBRARY_PATH to it), for a machine
# whose system Chromium libraries are not installed. Without any of these it says so and stops.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
node=${NODE:-node}
command -v "$node" >/dev/null 2>&1 || { echo "skipped: no node"; exit 0; }

pw="${PLAYWRIGHT:-}"
if [ -z "$pw" ]; then
	pw=$("$node" -e 'console.log(require.resolve("playwright").replace(/\/index\.js$/, ""))' 2>/dev/null) || pw=""
fi
[ -n "$pw" ] && [ -d "$pw" ] || { echo "skipped: no Playwright (set PLAYWRIGHT=…/node_modules/playwright)"; exit 0; }

chrome="${CHROME:-}"
if [ -z "$chrome" ]; then
	chrome=$(NODE_PATH="$(dirname "$pw")" "$node" -e 'console.log(require("playwright").chromium.executablePath())' 2>/dev/null) || chrome=""
fi
[ -n "$chrome" ] && [ -x "$chrome" ] || { echo "skipped: no Chromium (set CHROME=…)"; exit 0; }

[ -n "${SVGEDIT_BUNDLE:-}" ] && [ -f "$SVGEDIT_BUNDLE/index.html" ] || {
	echo "skipped: set SVGEDIT_BUNDLE to a built SVG-Edit tree with index.html"
	exit 0
}

if [ -n "${CHROMELIBS:-}" ]; then
	export LD_LIBRARY_PATH="$CHROMELIBS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

SVGEDIT_BUNDLE="$SVGEDIT_BUNDLE" CHROME="$chrome" PLAYWRIGHT="$pw" node "$here/hostile-svg.cjs"
