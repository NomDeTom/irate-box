// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin/apps as the hub answers it (irate_box/hub/manifests.py admin_apps), from apps.d/, for
// the jsdom tests of the admin page (menu overhaul M4).
const fs = require('fs'), path = require('path');
const APPS = path.resolve(__dirname, '../../apps.d');
// The apps whose access can be set: access.py's ROUTED, read from the source so they never drift.
const ROUTED = [...fs.readFileSync(path.resolve(__dirname, '../../irate_box/hub/access.py'), 'utf8')
  .match(/ROUTED = \{([\s\S]*?)\n\}/)[1].matchAll(/"([a-z0-9-]+)": \(/g)].map((m) => m[1]);
module.exports = function adminApps() {
  if (!ROUTED.length) throw new Error('access.ROUTED not found');
  return fs.readdirSync(APPS).filter((f) => f.endsWith('.json'))
    .map((f) => JSON.parse(fs.readFileSync(path.join(APPS, f), 'utf8'))).sort((a, b) => a.order - b.order)
    .flatMap((m) => {
      const tile = m.tile || {}, adm = m.admin || {}, menu = m.menu;
      const name = adm.title || tile.name || (menu || {}).title;
      if (!name || (tile.widget && !m.admin)) return [];
      return [{ id: m.id, name, icon: adm.icon || tile.icon || '', sections: adm.sections || [], folder: !!menu, art: (menu || {}).art || null, local: false,
        page: !!adm.page, width: adm.width || null, switch: ROUTED.includes(m.id),
        // server.seen_only: a tile or folder with no switch, not drawn on the hub page (F3)
        seen: !ROUTED.includes(m.id) && !adm.page && (tile.row || 'apps') === 'apps' && !tile.widget && !!(m.tile || menu) }];
    });
};
