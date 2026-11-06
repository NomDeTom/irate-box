// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The flashing page is open to guests; the board tools on it are for a signed-in administrator. Ask the
// hub's session check and show them only for an admin account. The pages themselves keep their own /admin
// sign-in, so a direct link still asks there.
(async () => {
  'use strict';
  const section = document.getElementById('flasher-board-tools');
  if (!section) return;
  try {
    const response = await fetch('/_irate/admin', { credentials: 'same-origin', headers: { 'X-Irate-Admin': '1' } });
    if (response.status === 204) section.hidden = false;
  } catch (_) {
    // Not signed in, or no hub: the section stays hidden.
  }
})();
