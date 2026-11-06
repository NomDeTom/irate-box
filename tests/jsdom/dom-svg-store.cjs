// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// SVG-Edit's hub gallery (apps/svgedit/ext-hub-store.js) in jsdom: Save to box sends the SVG
// string and a PNG preview as kind svgedit, a second save changes the same row with its lock,
// Open from box lists and loads a real save, Save as makes a new copy, and a gallery that does
// not answer says so without touching the device Open/Save. Usage: node dom-svg-store.cjs
const fs = require('fs');
const path = require('path');
const { JSDOM } = require(process.env.JSDOM || 'jsdom');

const repo = path.join(__dirname, '..', '..');
const extensionPath = path.join(repo, 'apps', 'svgedit', 'ext-hub-store.js');
const lockPath = path.join(repo, 'apps', 'svgedit', 'lock.js');

let fails = 0;
const check = (name, ok, info = '') => {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${ok ? '' : `  ${info}`}`);
  fails += !ok;
};

(async () => {
  const dom = new JSDOM('<!doctype html><html><body></body></html>', {
    url: 'http://box:8099/svg/',
    runScripts: 'outside-only',
    pretendToBeVisual: true,
  });
  const { window } = dom;
  global.window = window;
  global.document = window.document;
  global.Blob = window.Blob || Blob;
  global.URL = window.URL;
  global.URL.createObjectURL = () => 'blob:preview';
  global.URL.revokeObjectURL = () => {};
  global.HTMLElement = window.HTMLElement;

  const alerts = [];
  window.seAlert = (text) => alerts.push(text);
  window.prompt = () => 'A drawing';
  window.eval(fs.readFileSync(lockPath, 'utf8'));

  const state = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>';
  const saves = [{ id: 'abc', kind: 'svgedit', name: 'A drawing', created: 2, thumb: true, lock_n: 4096 }];
  const calls = [];
  let failGallery = false;
  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    if (failGallery) return { ok: false, status: 503 };
    if (url === '/api/saves' && options.method === 'POST') {
      const body = JSON.parse(options.body);
      return {
        ok: true,
        status: 201,
        json: async () => ({
          id: body.id || 'new',
          kind: body.kind,
          name: body.name,
          lock_n: options.headers['X-Lock-New'] ? 4096 : 4095,
        }),
      };
    }
    if (url === '/api/saves') return { ok: true, json: async () => ({ now: 3, saves }) };
    if (url === '/api/saves/abc') {
      return { ok: true, json: async () => ({ id: 'abc', kind: 'svgedit', name: 'A drawing', state }) };
    }
    if (url === '/api/saves/abc/thumb') {
      return { ok: true, blob: async () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])]) };
    }
    return { ok: false, status: 404, json: async () => ({ message: 'Not found.' }) };
  };

  const items = {};
  const clicks = {};
  const menu = window.document.createElement('div');
  const svgCanvas = {
    $id: (id) => items[id],
    insertChildAtIndex: (_menu, html, index) => {
      const holder = window.document.createElement('div');
      holder.innerHTML = html;
      const node = holder.firstElementChild;
      items[node.id] = node;
      menu.insertBefore(node, menu.children[index] || null);
    },
    $click: (node, action) => {
      clicks[node.id] = action;
    },
    svgCanvasToString: () => state,
    rasterExport: async () => ({ datauri: 'data:image/png;base64,iVBORw0KGgo=' }),
    mergeDeep: (first, second) => ({ ...first, ...second }),
    getSvgOption: () => ({}),
    setSvgOption: () => {},
    clearSelection: () => {},
  };
  let title = 'untitled.svg';
  let loaded = null;
  const editor = {
    title: 'untitled.svg',
    configObj: { pref: () => 'embed' },
    svgCanvas,
    topPanel: { updateTitle: (next) => (title = next) },
    loadSvgString: async (svg) => (loaded = svg),
    updateCanvas: () => {},
  };

  const source = fs.readFileSync(extensionPath, 'utf8');
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;
  const { default: extension } = await import(moduleUrl);
  extension.init.call(editor);
  check(
    'init adds Open from box, Save to box and Save as to the main menu',
    ['tool_hub_open', 'tool_hub_save', 'tool_hub_save_as'].every((id) => items[id]),
    Object.keys(items),
  );

  await clicks.tool_hub_save();
  let posted = calls.find((call) => call.url === '/api/saves' && call.options.method === 'POST');
  let body = JSON.parse(posted.options.body);
  check(
    'Save to box posts kind svgedit, the SVG string and a PNG preview',
    body.kind === 'svgedit' && body.state === state && body.thumb.startsWith('data:image/png;base64,'),
    body,
  );
  check(
    '  a new save is locked to this browser',
    !!posted.options.headers['X-Lock-New'] && !!window.HubLock.seedOf('saves', 'new'),
  );
  check('  and the title and a plain message say it was saved', title === 'A drawing.svg' && /Saved "A drawing"/.test(alerts.at(-1)), title);

  await clicks.tool_hub_save();
  posted = calls.filter((call) => call.url === '/api/saves' && call.options.method === 'POST').at(-1);
  body = JSON.parse(posted.options.body);
  check(
    'a second save changes that row with its lock, not a new one',
    body.id === 'new' && !!posted.options.headers['X-Lock'] && body.name === 'A drawing',
    { id: body.id, headers: posted.options.headers },
  );

  await clicks.tool_hub_open();
  await new Promise((resolve) => setTimeout(resolve, 30));
  const dialog = window.document.getElementById('svghub-open');
  check('Open from box lists the gallery', !!dialog && dialog.textContent.includes('A drawing'));
  const row = [...dialog.querySelectorAll('button')].find((button) => button.textContent.includes('A drawing'));
  row.click();
  await new Promise((resolve) => setTimeout(resolve, 30));
  check(
    '  choosing a save loads its SVG string',
    loaded === state && title === 'A drawing.svg' && !window.document.getElementById('svghub-open'),
    { loaded, title },
  );

  await clicks.tool_hub_save_as();
  posted = calls.filter((call) => call.url === '/api/saves' && call.options.method === 'POST').at(-1);
  body = JSON.parse(posted.options.body);
  check('Save as makes a new copy with no id', body.id === undefined && !!posted.options.headers['X-Lock-New'], body);

  failGallery = true;
  await clicks.tool_hub_open();
  check(
    'a gallery that does not answer says so, and the editor is still usable',
    /Could not read the box's gallery/.test(alerts.at(-1)),
    alerts.at(-1),
  );

  console.log(fails ? `${fails} failure(s)` : 'ok');
  process.exit(fails ? 1 : 0);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
