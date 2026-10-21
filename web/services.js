// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// Service dashboard: every service /status knows about, as running / stopped /
// not installed, refreshed on the same 15 s beat as the home page.
const list = document.getElementById('service-list');
const note = document.getElementById('services-note');
const systemSection = document.getElementById('system-section');
const systemList = document.getElementById('system-list');

const LABEL = { running: 'Running', stopped: 'Stopped', missing: 'Not installed' };
// Running first, then what could be started, then what is not there at all.
const ORDER = { running: 0, stopped: 1, missing: 2 };

const fmt = (bytes) => {
  const gb = bytes / 2 ** 30;
  return gb >= 1 ? `${gb.toFixed(1)} GB` : `${Math.round(bytes / 2 ** 20)} MB`;
};

function row(name, detail, state) {
  const li = document.createElement('li');
  li.className = 'service-row';
  const n = document.createElement('span');
  n.className = 'name';
  n.textContent = name;
  const d = document.createElement('span');
  d.className = 'desc';
  d.textContent = detail;
  li.append(n, d);
  if (state) {
    const b = document.createElement('span');
    b.className = `state state-${state}`;
    b.textContent = LABEL[state] || state;
    li.appendChild(b);
  }
  return li;
}

async function refresh() {
  let data;
  try {
    const response = await fetch('/status');
    if (!response.ok) throw new Error(response.status);
    data = await response.json();
  } catch (_) {
    note.textContent = 'The hub server did not answer.';
    note.hidden = false;
    return;
  }
  const services = [...data.services].sort(
    (left, right) => ORDER[left.state] - ORDER[right.state] || left.name.localeCompare(right.name));
  list.replaceChildren(...services.map(
    (s) => row(s.name, s.path || s.note || 'Built-in service', s.state)));
  note.textContent = data.proxied ? ''
    : 'No web server is in front of the hub server. Apps that need the web server cannot be reached.';
  note.hidden = data.proxied;

  const sys = data.system || {};
  const rows = [];
  if (sys.mem_total) {
    rows.push(row('Available memory', `${fmt(sys.mem_available)} of ${fmt(sys.mem_total)}`));
  }
  if (sys.disk_total) {
    rows.push(row('Free storage', `${fmt(sys.disk_free)} of ${fmt(sys.disk_total)}`));
  }
  if (typeof data.uptime === 'number') {
    const h = Math.floor(data.uptime / 3600);
    rows.push(row('Total powered-on time', `${Math.floor(h / 24)} d ${h % 24} h`));
  }
  systemList.replaceChildren(...rows);
  systemSection.hidden = rows.length === 0;
}

refresh();
setInterval(refresh, 15000);
