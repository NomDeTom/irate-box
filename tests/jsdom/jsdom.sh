# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The jsdom the page tests run in, sourced by tests/jsdom/run.sh and tests/browser/card-audit.sh. jsdom is not the
# repository's. With JSDOM=/path/to/node_modules/jsdom, that one is used. Otherwise the version below is installed
# once with npm, outside the repository, in JSDOM_CACHE (by default ~/.cache/irate-box/jsdom-VERSION), and JSDOM is
# set to it: a few seconds, no Docker, no root. CI runs the same, so a page that passes here passes there.
JSDOM_VERSION=22.1.0
if [ -z "${JSDOM:-}" ]; then
	jsdom_cache="${JSDOM_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/irate-box/jsdom-$JSDOM_VERSION}"
	if [ -f "$jsdom_cache/node_modules/jsdom/package.json" ]; then
		:
	elif ! command -v npm >/dev/null 2>&1; then
		echo "jsdom $JSDOM_VERSION: no npm to install it with (it comes with Node.js)" >&2
	else
		echo "jsdom $JSDOM_VERSION: installing it in $jsdom_cache (once)" >&2
		# Into a folder of its own, then moved: an install cut short leaves no half a jsdom to be used next time.
		jsdom_part="$jsdom_cache.part.$$"
		rm -rf "$jsdom_part"
		if npm install --no-save --no-audit --no-fund --silent --prefix "$jsdom_part" "jsdom@$JSDOM_VERSION" >&2; then
			mkdir -p "$(dirname "$jsdom_cache")"
			rm -rf "$jsdom_cache"
			mv "$jsdom_part" "$jsdom_cache"
		else
			echo "jsdom $JSDOM_VERSION: npm could not install it" >&2
			rm -rf "$jsdom_part"
		fi
	fi
	[ -f "$jsdom_cache/node_modules/jsdom/package.json" ] && export JSDOM="$jsdom_cache/node_modules/jsdom"
fi
