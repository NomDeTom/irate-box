// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// A question drawn on the page, for the pages that do not load the admin widgets (drop, locks): the same
// .aw-ask as AW.confirmHere, the words, then the action and Cancel, after what was pressed. Resolves true or
// false. One at a time: a new one answers the last one No. Cancel has the focus; Escape cancels.
window.askHere = (() => {
  let asked = null;
  const button = (text, cls, onclick) => Object.assign(document.createElement('button'), { type: 'button', className: cls, textContent: text, onclick });
  return (text, opts = {}) => {
    if (asked) asked(false);
    const at = opts.anchor || null;
    return new Promise((resolve) => {
      const box = Object.assign(document.createElement('div'), { className: 'aw-ask' });
      box.setAttribute('role', 'alertdialog');
      box.setAttribute('aria-label', opts.label || 'Confirm');
      const answer = (yes) => {
        if (asked !== answer) return;
        asked = null;
        box.remove();
        resolve(yes);
      };
      asked = answer;
      const words = Object.assign(document.createElement('p'), { className: 'setting-desc', textContent: text });
      const no = button('Cancel', 'action-btn small', () => answer(false));
      box.append(words, button(opts.label || 'Confirm', 'action-btn small primary', () => answer(true)), no);
      box.addEventListener('keydown', (event) => { if (event.key === 'Escape') answer(false); });
      if (at && at.isConnected) at.after(box);
      else document.querySelector('main, section, body').prepend(box);
      no.focus();
    });
  };
})();
