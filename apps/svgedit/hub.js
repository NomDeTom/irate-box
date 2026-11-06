// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hub's SVG-Edit wrapper. Upstream's index.html leaves the default extension list on, which
// includes the local-storage extension; the hub's trimmed build does not ship it, so the list is
// fixed here. No URL settings: an SVG opened from the gallery must not reconfigure the editor.
import Editor from './Editor.js'

const svgEditor = new Editor(document.getElementById('container'))
svgEditor.setConfig({
  allowInitialUserOverride: false,
  preventAllURLConfig: true,
  lockExtensions: true,
  noDefaultExtensions: true,
  extensions: [
    'ext-connector',
    'ext-eyedropper',
    'ext-grid',
    'ext-markers',
    'ext-panning',
    'ext-shapes',
    'ext-polystar',
    'ext-opensave',
    'ext-layer_view',
  ],
})
svgEditor.init()
