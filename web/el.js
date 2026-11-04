// SPDX-License-Identifier: AGPL-3.0-or-later
// SPDX-FileCopyrightText: 2026 NomDeTom
// The one element builder every page shares: el(tag, props, ...children). It is small enough for a guest page
// (the locks, the add-on bridge, the mesh page) to load without the admin's widgets.
(function () {
  'use strict';
  // Props that would run or parse text as markup: a handler only as a function, and never innerHTML and its kind,
  // so a spread of the hub's data into props can never become script.
  const MARKUP_PROP = /^(innerhtml|outerhtml|srcdoc)$/i;
  function el(tag, props = {}, ...kids) {
    // A button with no class of its own is an action button (the control vocabulary).
    if (tag === 'button' && !props.className) props = { ...props, className: 'action-btn' };
    const { dataset, ...rest } = props;
    for (const [k, v] of Object.entries(rest))
      if (MARKUP_PROP.test(k) || (/^on/i.test(k) && v != null && typeof v !== 'function'))
        throw new TypeError(`el(): ${k} is not set from props`);
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(rest)) {
      if (k === 'role' || k.startsWith('aria-')) node.setAttribute(k, String(v));
      else node[k] = v;
    }
    if (dataset) Object.assign(node.dataset, dataset);
    node.append(...kids.filter((k) => k !== null && k !== undefined));
    return node;
  }
  window.HubEl = { el };
})();
