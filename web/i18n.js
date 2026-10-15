// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The pages' own words in the reader's language. Each piece of text in a page carries its message ID,
// with the English as the page's own content:
//
//   <p data-i18n="admin.first-use.no-admin"><strong>This box has no administrator account.</strong> Anyone …
//      <a href="/admin/setup">Set up this box</a> over HTTPS …</p>
//   <input placeholder="name or type" data-i18n-placeholder="admin.app-inventory.search-placeholder">
//
// A message is the element's content, its child elements numbered in document order: <n>…</n> for one that
// is part of the sentence (a link, strong, em, a span with words: INLINE), its words translated and the
// element kept with its attributes; <n/> for any other (code, a form control, an emoji, an element with its
// own data-i18n), kept whole. So "<0>This box has no administrator account.</0> Anyone … <1>Set up this
// box</1> …". data-i18n-<name> is the ID of the attribute <name>, or of data-<name> (a choice's data-says).
// tests/static_msg_guard.py reads a page by the same rules and lists every ID with its English.
//
// A catalogue is {id: {text, en}}: the translation, and the English it was made from. A message whose
// catalogue entry is missing, made from other English (stale), or does not keep every child element
// stays in English. With no catalogue (today: English only) this file does nothing.
//
// Loaded first among a page's scripts at the end of <body>, so the page is localised before its own
// scripts read it (the choices' data-says, a label's text).
(function () {
  'use strict';
  // Keep these three in step with tests/static_msg_guard.py.
  var INLINE = ['a', 'abbr', 'b', 'cite', 'dfn', 'em', 'i', 'mark', 'q', 's', 'small', 'span', 'strong', 'sub', 'sup', 'time', 'u'];
  var SKIP = ['script', 'style', 'code', 'kbd', 'pre', 'samp', 'var', 'template'];
  var LETTER = /\p{L}/u;
  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'label', 'aria-description', 'aria-placeholder', 'content'];

  function tag(el) { return el.localName; }
  function noTranslate(el) { return el.getAttribute('translate') === 'no'; }
  function letters(s) { return LETTER.test(s); }

  function hasWords(el) {
    for (var c = el.firstChild; c; c = c.nextSibling) {
      if (c.nodeType === 3 && letters(c.data)) return true;
      if (c.nodeType === 1 && SKIP.indexOf(tag(c)) < 0 && !noTranslate(c) && !c.hasAttribute('data-i18n') && hasWords(c)) return true;
    }
    return false;
  }
  function inline(el) {
    return INLINE.indexOf(tag(el)) >= 0 && !el.hasAttribute('data-i18n') && !noTranslate(el) && hasWords(el);
  }

  // The English of a message element, as the guard writes it, and its child elements by number.
  function read(el) {
    var nodes = [];
    function walk(node) {
      var out = '';
      for (var c = node.firstChild; c; c = c.nextSibling) {
        if (c.nodeType === 3) { out += c.data; continue; }
        if (c.nodeType !== 1) continue;
        var n = nodes.length;
        nodes.push(c);
        if (inline(c)) out += '<' + n + '>' + walk(c) + '</' + n + '>';
        else {
          out += '<' + n + '/>';
          var all = c.getElementsByTagName('*');
          for (var i = 0; i < all.length; i++) nodes.push(null);   // numbered, not in the message
        }
      }
      return out;
    }
    var en = walk(el).replace(/\s+/g, ' ').trim();
    return { en: en, nodes: nodes };
  }

  // A message's text as a tree: strings, and {n, kids} (kids null for <n/>). null if the tags do not nest.
  function parse(text) {
    var root = { kids: [] }, stack = [root], re = /<(\d+)\/>|<(\d+)>|<\/(\d+)>/g, at = 0, m;
    while ((m = re.exec(text))) {
      var top = stack[stack.length - 1];
      if (m.index > at) top.kids.push(text.slice(at, m.index));
      at = re.lastIndex;
      if (m[1] !== undefined) top.kids.push({ n: +m[1], kids: null });
      else if (m[2] !== undefined) { var e = { n: +m[2], kids: [] }; top.kids.push(e); stack.push(e); }
      else if (stack.length > 1 && top.n === +m[3]) stack.pop();
      else return null;
    }
    if (stack.length !== 1) return null;
    if (at < text.length) root.kids.push(text.slice(at));
    return root.kids;
  }
  // The child elements a message refers to, as "n" or "n/", sorted: a translation must keep every one.
  function shape(kids, out) {
    out = out || [];
    kids.forEach(function (k) {
      if (typeof k === 'string') return;
      out.push(k.kids ? String(k.n) : k.n + '/');
      if (k.kids) shape(k.kids, out);
    });
    return out.sort();
  }

  function build(kids, nodes, doc) {
    return kids.map(function (k) {
      if (typeof k === 'string') return doc.createTextNode(k);
      var el = nodes[k.n];
      if (k.kids) {
        while (el.firstChild) el.removeChild(el.firstChild);
        build(k.kids, nodes, doc).forEach(function (c) { el.appendChild(c); });
      }
      return el;
    });
  }

  function entryFor(catalogue, id, en) {
    var e = catalogue[id];
    if (!e || typeof e.text !== 'string') return null;
    if (e.en !== undefined && e.en !== en) return null;   // stale: made from other English
    return e;
  }

  // Say every message under root in the catalogue's language. Returns how many it said.
  function apply(catalogue, root) {
    root = root || document;
    var doc = root.ownerDocument || root, said = 0;
    var els = root.querySelectorAll('[data-i18n]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i], msg = read(el), e = entryFor(catalogue, el.getAttribute('data-i18n'), msg.en);
      if (!e) continue;
      var want = parse(msg.en), got = parse(e.text);
      if (!want || !got || shape(want).join() !== shape(got).join()) continue;
      var built = build(got, msg.nodes, doc);
      while (el.firstChild) el.removeChild(el.firstChild);
      built.forEach(function (c) { el.appendChild(c); });
      said++;
    }
    var all = root.querySelectorAll('*');
    for (var j = 0; j < all.length; j++) {
      var a = all[j].attributes;
      for (var k = 0; k < a.length; k++) {
        if (a[k].name.indexOf('data-i18n-') !== 0) continue;
        var name = a[k].name.slice('data-i18n-'.length);
        var target = ATTRS.indexOf(name) >= 0 ? name : 'data-' + name;
        var cur = all[j].getAttribute(target);
        if (cur === null) continue;
        var ae = entryFor(catalogue, a[k].value, cur.replace(/\s+/g, ' ').trim());
        if (ae) { all[j].setAttribute(target, ae.text); said++; }
      }
    }
    if (catalogue['@lang']) doc.documentElement.setAttribute('lang', catalogue['@lang']);
    return said;
  }

  // The hook for the language choice (not made yet): the catalogue of the reader's language, or null for
  // English. When there is one, it is i18n/<lang>.json, read once; nothing reads or fetches one today.
  function catalogue() { return null; }

  var I18N = { apply: apply, read: read, catalogue: catalogue };
  if (typeof window !== 'undefined') {
    window.I18N = I18N;
    var cat = catalogue();
    if (cat && typeof document !== 'undefined') apply(cat, document);
  }
})();
