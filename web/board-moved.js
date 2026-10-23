// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The board moved to the landing page (board.html): an old link lands there, a thread's link on its thread.
location.replace('/' + (/^#t\d+$/.test(location.hash) ? location.hash : '#board'));
