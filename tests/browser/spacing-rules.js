// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The vertical-spacing rules shared by the browser style audit (style-audit.html, and so style-audit.sh and
// card-audit.sh) and the local Playwright runner (spacing-audit.cjs). A card or a button must not sit on the text
// above or below it, and a gap must stay in its band. The measurement is of sibling boxes in the DOM: adjacent
// visible children of a flow container, wherever they sit (a pane, a card, a wrapper the page fills).
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.SpacingAudit = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  // A card is a box with a frame of its own. A group of cards (the tiles, a list of cards) is a card for spacing:
  // the gap to the words above it is the same, and its own children are measured separately.
  const CARD = [
    '.net-card', '.net-device', '.setting', '.finding', '.aw-card', '.git-card', '.lib-card', '.aw-settings',
    '.admin-tile', '.update-card', '.service-card', '.factory-card', '.qr-card', '.people-card', '.system-card',
    '.attention', '.aw-block', '.sec-choice', '.access-card', '.doc-card', '.item-list', '.net-cards', '.admin-tiles',
    '.settings',
    '.admin-checks', '.admin-progress', '.help-lead', '.danger-box', '.settings-note',
  ].join(', ');
  // A row of buttons, or the line that says what its action answered.
  const BUTTON = [
    '.library-buttons', '.library-actions', '.action-note', '.admin-save', '.aw-foot', '.factions', '.view-switch',
    '.select-chips', '.access-toggle', '.appstore-actions', '.acct-form > .action-btn', '.inline-form',
    'button.action-btn', 'a.action-btn', 'button.link-button', 'button', '.action-btn',
  ].join(', ');
  const HEADING = 'h1, h2, h3, h4, h5, h6, summary, .ftitle, .admin-page-title, .net-head';
  const TEXT = [
    'p', '.setting-desc', '.setting-name', '.fmeta', '.kind-tag', 'label', '.admin-note', '.help-lead', 'dl', 'ul', 'ol',
    'table', 'pre', 'blockquote', '.admin-list', '.fresh-strip', '.aw-note', '.aw-warn', '.aw-reason', '.chip-says',
    '.step-text', '.mock-row', '.mock-group-title',
  ].join(', ');
  // The page's own data-art names its background art; only the art elements themselves are skipped.
  const SKIP = 'script, style, template, .admin-art, .krab';

  const visible = (el, win) => {
    if (!el || !el.getClientRects || !el.getClientRects().length) return false;
    const st = win.getComputedStyle(el);
    return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
  };
  const shown = (el, win) => el && visible(el, win) && !el.closest(SKIP);
  const ownText = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
  const said = (el) => {
    const id = el.id ? `#${el.id}` : '';
    const cls = el.className && typeof el.className === 'string'
      ? `.${el.className.trim().split(/\s+/).slice(0, 2).join('.')}` : '';
    return `${el.tagName.toLowerCase()}${id}${cls}: "${(el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 55)}"`;
  };
  const round = (n) => Math.round(n * 10) / 10;

  // What a block is, for the gap it keeps: a card, a button row, a heading, text, or something else (a form, a
  // table's wrapper, a fold). A wrapper with one kind of content inside takes that kind: the page's own div around
  // a card is the card for the words above it.
  function role(el, win) {
    if (el.matches(CARD)) return 'card';
    if (el.matches(BUTTON)) return 'button';
    if (el.matches(HEADING)) return 'heading';
    if (el.matches(TEXT) || ownText(el)) return 'text';
    const kids = [...el.children].filter((kid) => shown(kid, win));
    const roles = new Set(kids.map((kid) => role(kid, win)));
    if (roles.size === 1) return [...roles][0];
    if (roles.has('card')) return 'card';
    if (roles.has('button')) return 'button';
    if (roles.has('heading')) return 'heading';
    if (roles.has('text')) return 'text';
    return 'other';
  }

  // The gap a pair may keep, in rem. Text-to-text pairs are not policed here: they are often one thought's lines,
  // and the card's first/last line check already covers the card's own padding. `card` is whether the pair sits
  // inside a card, where the space is tighter.
  function band(left, right, card) {
    if (left === right && left === 'other') return null;
    if (left === 'text' && right === 'text') return card ? { min: 0.1, max: 0.8 } : null;
    if (left === 'heading') return card ? { min: 0.15, max: 1.2 } : { min: 0.2, max: 1.2 };
    if (right === 'heading') return card ? { min: 0.4, max: 1.2 } : { min: 1.2, max: 2.5 };
    if (left === 'button' || right === 'button') {
      if (left === 'button' && right === 'button') return card ? { min: 0.2, max: 0.8 } : { min: 0.1, max: 0.8 };
      return card ? { min: 0.2, max: 0.9 } : { min: 0.3, max: 1.2 };
    }
    return card ? { min: 0.1, max: 1.1 } : { min: 0.7, max: 1.4 };
  }

  // A parent whose children are inline (a paragraph's strong and links) has no block gaps to measure.
  function flowKids(parent, win) {
    return [...parent.children].filter((kid) => {
      if (!shown(kid, win)) return false;
      if (kid.matches('br')) return false;
      // A textarea is inline-block by the browser's default, but it fills a block of the page like any field.
      if (kid.matches('textarea')) return true;
      const st = win.getComputedStyle(kid);
      return st.display !== 'inline' && st.display !== 'inline-block' && st.display !== 'contents';
    });
  }

  function measureSiblings(parent, win, out, page, pane) {
    const rem = parseFloat(win.getComputedStyle(win.document.documentElement).fontSize) || 16;
    const kids = flowKids(parent, win);
    // A row (grid or flex) of cards side by side: measure the group as a block, not the boxes inside it. The pane's
    // own sibling check sees the group; the group's rows are not vertical gaps between words and cards.
    const inCard = !!parent.closest(CARD);
    for (let i = 0; i + 1 < kids.length; i += 1) {
      const left = kids[i];
      const right = kids[i + 1];
      const a = left.getBoundingClientRect();
      const b = right.getBoundingClientRect();
      if (a.height < 1 || b.height < 1) continue;
      // Side by side (their vertical spans overlap) or reversed: not a vertical gap.
      if (b.top < a.bottom - 1) continue;
      const gap = round((b.top - a.bottom) / rem);
      const leftRole = role(left, win);
      const rightRole = role(right, win);
      const expected = band(leftRole, rightRole, inCard);
      if (!expected) continue;
      if (gap < expected.min - 0.06 || gap > expected.max + 0.06) {
        out.push({
          pane,
          page,
          rule: `a ${leftRole} to ${rightRole} gap of ${gap}rem (expected ${expected.min}–${expected.max}rem)`,
          what: `${said(left)} ↑ ${said(right)}`,
          gap,
          expected,
          pair: `${leftRole}→${rightRole}`,
        });
      }
    }
    for (const kid of kids) {
      if (kid.children.length && !kid.matches(TEXT)) measureSiblings(kid, win, out, page, pane);
    }
  }

  function measurePane(win, doc, pane, page) {
    const out = [];
    if (!shown(pane, win)) return out;
    measureSiblings(pane, win, out, page, pane.id || '');
    return out;
  }

  // The sidebar is a flow of groups, links and the foot: a group head must stand off its links, the links keep the
  // menu's tight rhythm, and the foot is apart from the list.
  function measureSidebar(win, doc, page) {
    const out = [];
    const rem = parseFloat(win.getComputedStyle(doc.documentElement).fontSize) || 16;
    const list = doc.querySelector('.admin-side-list');
    const side = doc.querySelector('.admin-side');
    if (list && shown(list, win)) {
      const kids = flowKids(list, win);
      for (let i = 0; i + 1 < kids.length; i += 1) {
        const left = kids[i];
        const right = kids[i + 1];
        const a = left.getBoundingClientRect();
        const b = right.getBoundingClientRect();
        if (a.height < 1 || b.height < 1) continue;
        if (b.top < a.bottom - 1) continue;
        const gap = round((b.top - a.bottom) / rem);
        // A group head keeps 1rem above it; its links follow it closely (the menu's tight 0.1rem).
        const headToLinks = left.classList.contains('admin-side-group') && right.classList.contains('admin-side-links');
        const linksToHead = left.classList.contains('admin-side-links') && right.classList.contains('admin-side-group');
        const expected = headToLinks ? { min: 0, max: 0.6 } : linksToHead ? { min: 0.7, max: 1.5 } : { min: 0, max: 0.25 };
        if (gap < expected.min - 0.06 || gap > expected.max + 0.06) {
          out.push({
            pane: 'sidebar', page, rule: `a sidebar gap of ${gap}rem (expected ${expected.min}–${expected.max}rem)`,
            what: `${said(left)} ↑ ${said(right)}`, gap, expected, pair: 'sidebar',
          });
        }
      }
    }
    const foot = side && side.querySelector('.side-foot');
    if (foot && shown(foot, win) && list && shown(list, win)) {
      const gap = round((foot.getBoundingClientRect().top - list.getBoundingClientRect().bottom) / rem);
      const expected = { min: 0.4, max: 1.6 };
      if (gap < expected.min - 0.06 || gap > expected.max + 0.06) {
        out.push({
          pane: 'sidebar', page, rule: `the sidebar's foot gap of ${gap}rem (expected ${expected.min}–${expected.max}rem)`,
          what: `${said(list)} ↑ ${said(foot)}`, gap, expected, pair: 'list→foot',
        });
      }
    }
    return out;
  }

  // A page that scrolls sideways is broken on a phone whatever its vertical gaps: the document must fit the window.
  function measureOverflow(win, doc, page) {
    const over = doc.documentElement.scrollWidth - win.innerWidth;
    if (over <= 1) return [];
    return [{
      pane: 'page', page,
      rule: `a page ${over}px wider than the window`,
      what: `${doc.documentElement.scrollWidth}px of content in ${win.innerWidth}px`,
      gap: over, expected: { min: 0, max: 1 }, pair: 'overflow',
    }];
  }

  return { measurePane, measureSidebar, measureOverflow, role, band };
});
