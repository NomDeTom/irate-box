// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The IRC page: the address this page was reached by is the chat server's address too, so the
// connection details and the irc:// link are filled in from it.
(() => {
  const PORT = 6667;
  const host = location.hostname || 'this box';
  const set = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  set('irc-host', host);
  set('irc-port', String(PORT));
  set('irc-link-host', host);
  const link = document.getElementById('irc-link');
  // An IPv6 literal needs its brackets in a URL; location.hostname already has them.
  if (link && location.hostname) link.href = `irc://${host}:${PORT}/lobby`;
})();
