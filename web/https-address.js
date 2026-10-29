// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The box's HTTPS address, for the pages that link to it. The port is the box's own HTTPS port, as
// /certificate.json tells it (HUB_TLS_PORT), never this page's: over plain HTTP, location.host carries
// the HTTP port, and the two can differ from 80 and 443.

// The host as a URL writes it: an IPv6 address in brackets.
const urlHost = (host) => (host.includes(':') && !host.startsWith('[') ? `[${host}]` : host);

// The box's HTTPS root at host (this page's by default), with the port when it is not 443.
function httpsUrl(port, host = location.hostname) {
  return `https://${urlHost(host)}${port && port !== 443 ? `:${port}` : ''}/`;
}

// This page, its query and fragment kept, over HTTPS.
function httpsHere(port) {
  return httpsUrl(port) + location.pathname.slice(1) + location.search + location.hash;
}

// What the box says of its HTTPS ({set_up, on, port, ...}), or null when it does not answer.
async function httpsInfo() {
  try {
    const response = await fetch('/certificate.json', { cache: 'no-store' });
    return response.ok ? await response.json() : null;
  } catch (_) {
    return null;
  }
}
