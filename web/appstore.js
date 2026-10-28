// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The App store page (appstore.py writes it): the filter chips over the files and a QR of the page's own
// address. Everything reads without this script; it only adds. The chips' words are the file names' known
// OS, architecture and flavour; a chip hides no file the visitor can still reach by scrolling.
(function () {
  'use strict';
  var src = document.getElementById('appstore-chips');
  var chips = {};
  try {
    chips = JSON.parse((src && src.textContent) || '{}');
  } catch (e) {
    chips = {};
  }
  var labels = { os: 'OS', architecture: 'Arch', flavour: 'Build' };
  var remembered = {};
  try {
    remembered = JSON.parse(localStorage.getItem('appstore-chips') || '{}');
  } catch (e) {
    remembered = {};
  }
  var on = {};
  Object.keys(remembered).forEach(function (k) {
    if (chips[k] && chips[k].indexOf(remembered[k]) >= 0) on[k] = remembered[k];
  });
  var lists = [].slice.call(document.querySelectorAll('.appstore-files'));
  function apply() {
    lists.forEach(function (ul) {
      [].slice.call(ul.querySelectorAll('li')).forEach(function (li) {
        var tags = (li.getAttribute('data-tags') || '').split(/\s+/);
        li.hidden = Object.keys(on).some(function (k) {
          return tags.indexOf(on[k]) < 0;
        });
      });
      var held = ul.querySelectorAll('li:not([hidden])').length;
      if (ul.closest('section')) {
        var lead = ul.closest('section').querySelector('.appstore-quiet');
        if (!lead && !held) {
          lead = document.createElement('p');
          lead.className = 'appstore-quiet setting-desc';
          lead.textContent = 'Nothing here matches the filter.';
          ul.closest('section').appendChild(lead);
        } else if (lead && held) lead.remove();
      }
    });
  }
  function make(kind, words) {
    if (!words.length) return;
    var row = document.createElement('p');
    row.className = 'appstore-chips';
    var name = document.createElement('span');
    name.textContent = labels[kind] || kind;
    row.appendChild(name);
    words.forEach(function (word) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = word;
      if (on[kind] === word) b.setAttribute('aria-pressed', 'true');
      b.addEventListener('click', function () {
        if (on[kind] === word) delete on[kind];
        else on[kind] = word;
        row.querySelectorAll('button').forEach(function (other) {
          if (on[kind] === other.textContent) other.setAttribute('aria-pressed', 'true');
          else other.removeAttribute('aria-pressed');
        });
        try {
          localStorage.setItem('appstore-chips', JSON.stringify(on));
        } catch (e) {}
        apply();
      });
      row.appendChild(b);
    });
    var anchor = document.querySelector('section');
    var box = document.getElementById('appstore-filters');
    if (!box) {
      box = document.createElement('div');
      box.id = 'appstore-filters';
      if (anchor) document.body.insertBefore(box, anchor);
      else document.body.appendChild(box);
    }
    box.appendChild(row);
  }
  Object.keys(chips).forEach(function (kind) {
    make(kind, (chips[kind] || []).slice(0, 24));
  });
  apply();
  var qr = document.getElementById('appstore-qr');
  if (qr && typeof qrcode === 'function' && qr.getAttribute('data-url')) {
    try {
      var q = qrcode(0, 'M');
      q.addData(qr.getAttribute('data-url'));
      q.make();
      qr.innerHTML = q.createSvgTag({ cellSize: 4, margin: 1, scalable: true });
    } catch (e) {}
  }
})();
