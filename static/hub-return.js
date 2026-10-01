// "Back to the hub" pill, and a 🛟 beside it, for apps served under the hub's origin.
// Each app's hub build includes <script src="/hub-return.js"> and nothing else, so this
// carries its own styles. Inside app.html the hub bar already does both jobs, so the pill
// only appears when an app is opened on its own.
(function () {
  if (document.getElementById('hub-return')) return;
  try { if (window.parent !== window && window.parent.document.body.classList.contains('app-frame')) return; } catch (_) {}

  var wrap = document.createElement('div');
  wrap.id = 'hub-return';
  var home = document.createElement('a');
  home.href = '/';
  home.title = 'Back to the hub';
  home.textContent = '⌂ Hub';
  var help = document.createElement('button');
  help.type = 'button';
  help.title = 'What is this?';
  help.setAttribute('aria-expanded', 'false');
  help.textContent = '🛟';
  var pop = document.createElement('div');
  pop.id = 'hub-return-help';
  pop.hidden = true;
  pop.innerHTML =
    '<p><strong>Don\'t panic.</strong> This is hosted on an Irate-Box: a small computer nearby, ' +
    'serving it over its own network. Nothing here is on the internet, and nothing you do ' +
    'leaves the box.</p>' +
    '<p>⌂ Hub takes you back to the start. If something here asks for the internet, that part ' +
    'is switched off on purpose.</p><a href="/help.html">Quick help →</a>';
  wrap.appendChild(home);
  wrap.appendChild(help);

  var css = document.createElement('style');
  css.textContent =
    '#hub-return{position:fixed;left:50%;bottom:10px;transform:translateX(-50%);z-index:2147483000;' +
    'display:flex;align-items:center;gap:2px;padding:2px;border-radius:999px;' +
    'font:600 13px system-ui,sans-serif;color:#e2e4ef;background:rgba(26,29,39,.85);' +
    'border:1px solid rgba(255,255,255,.15);backdrop-filter:blur(6px);' +
    'box-shadow:0 4px 14px rgba(0,0,0,.35);opacity:.8;transition:opacity .15s}' +
    '#hub-return:hover,#hub-return:focus-within{opacity:1}' +
    '#hub-return a{padding:4px 12px;color:inherit;text-decoration:none}' +
    '#hub-return button{padding:2px 8px;border:0;background:none;font-size:14px;cursor:pointer}' +
    '#hub-return-help{position:fixed;left:50%;bottom:48px;transform:translateX(-50%);' +
    'z-index:2147483000;width:min(22rem,calc(100vw - 2rem));padding:10px 14px;border-radius:8px;' +
    'font:13px/1.5 system-ui,sans-serif;color:#e2e4ef;background:rgba(26,29,39,.96);' +
    'border:1px solid rgba(255,255,255,.15);border-left:4px solid #7c6af7;' +
    'box-shadow:0 6px 20px rgba(0,0,0,.4)}' +
    '#hub-return-help[hidden]{display:none}' +
    '#hub-return-help p{margin:0 0 8px}#hub-return-help a{color:#a99bff}' +
    '@media (prefers-color-scheme:light){#hub-return,#hub-return-help{color:#1b1d27;' +
    'background:rgba(255,255,255,.95);border-color:rgba(0,0,0,.12)}#hub-return-help a{color:#5b4bd6}}';

  function setOpen(open) {
    pop.hidden = !open;
    help.setAttribute('aria-expanded', String(open));
  }
  help.addEventListener('click', function (e) {
    e.stopPropagation();
    setOpen(pop.hidden);
  });
  document.addEventListener('click', function (e) { if (!pop.contains(e.target)) setOpen(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') setOpen(false); });

  document.head.appendChild(css);
  document.body.appendChild(pop);
  document.body.appendChild(wrap);
})();
