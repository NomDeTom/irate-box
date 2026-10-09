#!/bin/bash
# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# A look at a box's pages in headless Chromium (look.mjs), from a dev machine with Docker:
#
#   tests/browser/run.sh lyra@192.168.1.181 [OUT_DIR]
#
# Chromium and the script run in one container (the image needs only Chromium: this machine's
# Node is mounted in, NODE_DIR, Node 22 or newer for its WebSocket). The box's script login
# (/etc/hub/admin-password, root's) is read over SSH with sudo -n and passed in the
# environment, never printed. LOOK_PAGES (space-separated paths) narrows what is looked at.
set -u
cd "$(dirname "$0")" || exit 2
box=${1:?usage: run.sh USER@HOST [OUT_DIR]}
out=$(realpath -m "${2:-out}")
image=${CHROME_IMAGE:-irate-chrome}
node_dir=${NODE_DIR:-$(dirname "$(dirname "$(readlink -f "$(command -v node)")")")}
host=${box#*@}
pw=$(ssh "$box" 'sudo -n cat /etc/hub/admin-password') || { echo "could not read the box's script login" >&2; exit 2; }
mkdir -p "$out"
docker run --rm -v "$node_dir":/opt/node:ro -v "$PWD":/look:ro -v "$out":/out \
	-e HUB_LOGIN="admin:$pw" -e LOOK_PAGES="${LOOK_PAGES:-}" -e LOOK_SETTLE_MS="${LOOK_SETTLE_MS:-3000}" \
	"$image" /opt/node/bin/node /look/look.mjs "http://$host" /out
