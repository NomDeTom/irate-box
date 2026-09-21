// "Back to the hub" pill for apps served under the hub's origin. Each app's hub build
// includes <script src="/hub-return.js"> and nothing else; the pill is a plain link.
(function () {
  if (document.getElementById('hub-return')) return;
  var a = document.createElement('a');
  a.id = 'hub-return';
  a.href = '/';
  a.title = 'Back to the hub';
  a.textContent = '⌂ Hub';
  var css = document.createElement('style');
  css.textContent =
    '#hub-return{position:fixed;left:50%;bottom:10px;transform:translateX(-50%);z-index:2147483000;' +
    'padding:6px 14px;border-radius:999px;font:600 13px system-ui,sans-serif;text-decoration:none;' +
    'color:#e2e4ef;background:rgba(26,29,39,.85);border:1px solid rgba(255,255,255,.15);' +
    'backdrop-filter:blur(6px);box-shadow:0 4px 14px rgba(0,0,0,.35);opacity:.8;transition:opacity .15s}' +
    '#hub-return:hover{opacity:1}' +
    '@media (prefers-color-scheme:light){#hub-return{color:#1b1d27;background:rgba(255,255,255,.88);border-color:rgba(0,0,0,.12)}}';
  document.head.appendChild(css);
  document.body.appendChild(a);
})();
