// Service dashboard: every service /status knows about, as running / not running /
// not installed, refreshed on the same 15 s beat as the home page.
const list = document.getElementById('service-list');
const note = document.getElementById('services-note');
const systemSection = document.getElementById('system-section');
const systemList = document.getElementById('system-list');

const LABEL = { running: 'Running', stopped: 'Not running', missing: 'Not installed' };
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
    const r = await fetch('/status');
    if (!r.ok) throw new Error(r.status);
    data = await r.json();
  } catch (_) {
    note.textContent = 'The hub server did not answer.';
    note.hidden = false;
    return;
  }
  const services = [...data.services].sort(
    (a, b) => ORDER[a.state] - ORDER[b.state] || a.name.localeCompare(b.name));
  list.replaceChildren(...services.map(
    (s) => row(s.name, s.path || s.note || 'part of the box', s.state)));
  note.textContent = data.proxied ? ''
    : 'Served without the web server in front: the apps are unreachable whatever their state.';
  note.hidden = data.proxied;

  const sys = data.system || {};
  const rows = [];
  if (sys.mem_total) {
    rows.push(row('Memory', `${fmt(sys.mem_available)} free of ${fmt(sys.mem_total)}`));
  }
  if (sys.disk_total) {
    rows.push(row('Disk', `${fmt(sys.disk_free)} free of ${fmt(sys.disk_total)}`));
  }
  if (typeof data.uptime === 'number') {
    const h = Math.floor(data.uptime / 3600);
    rows.push(row('Powered on', `${Math.floor(h / 24)} d ${h % 24} h in total`));
  }
  systemList.replaceChildren(...rows);
  systemSection.hidden = rows.length === 0;
}

refresh();
setInterval(refresh, 15000);
