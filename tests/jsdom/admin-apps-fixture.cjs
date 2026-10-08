// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// /admin/apps as the hub answers it (irate_box/hub/manifests.py admin_apps), from apps.d/, for
// the jsdom tests of the admin page (menu overhaul M4).
const fs = require('fs'), path = require('path');
const APPS = path.resolve(__dirname, '../../apps.d');
module.exports = function adminApps() {
  return fs.readdirSync(APPS).filter((f) => f.endsWith('.json'))
    .map((f) => JSON.parse(fs.readFileSync(path.join(APPS, f), 'utf8'))).sort((a, b) => a.order - b.order)
    .flatMap((m) => {
      const tile = m.tile || {}, adm = m.admin || {}, menu = m.menu;
      const name = adm.title || tile.name || (menu || {}).title;
      if (!name || (tile.widget && !m.admin)) return [];
      return [{ id: m.id, name, icon: adm.icon || tile.icon || '', sections: adm.sections || [], folder: !!menu, art: (menu || {}).art || null, local: false }];
    });
};
