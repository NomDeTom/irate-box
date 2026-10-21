// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The About page: this box's version and the clone address on whatever network it is reached by.
(() => {
  const clone = document.getElementById('about-clone');
  if (clone) clone.textContent = `git clone ${location.origin}/git/irate-box-source.git`;
  fetch('/status').then((response) => response.json()).then((status) => {
    const v = document.getElementById('about-version');
    if (v && status.version) v.textContent = status.version;
  }).catch(() => {});
})();
