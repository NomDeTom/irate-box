// SPDX-License-Identifier: MIT
// SPDX-FileCopyrightText: 2026 NomDeTom
// The hub's gallery for SVG-Edit: Save to box, Save as, and Open from box. Additive to the
// device Open/Save in ext-opensave. A save is {kind: "svgedit", name, state, thumb}; the state
// is the SVG string, the thumbnail a PNG from the editor's own rasterExport. The gallery is
// scoped to this app by the web server, so the list never shows another app's work.

const KIND = 'svgedit'
const SAVES = '/api/saves'
const LOCKS = 'saves'
const LOCKED_ELSEWHERE =
  'Saved as a new copy. That drawing is locked to another browser.'

let editor = null
let savedId = null
let savedName = ''
let savedLockN = 0
let objectUrls = []

const say = (text) => {
  if (window.seAlert) window.seAlert(text)
  else console.warn(text)
}

const cleanName = (value) => String(value || '').trim().slice(0, 64)

const suggestedName = () => {
  if (savedName) return savedName
  const title = String(editor.title || '').replace(/\.svg$/i, '').trim()
  return title && title.toLowerCase() !== 'untitled' ? title : ''
}

const thumbnail = async (name) => {
  try {
    const result = await editor.svgCanvas.rasterExport('PNG', 1, name)
    return result && result.datauri ? result.datauri : undefined
  } catch (_error) {
    // A save without a preview is still a save.
    return undefined
  }
}

const saveDrawing = async (asNew) => {
  let name = savedName
  if (asNew || !name) {
    const suggestion = suggestedName() || 'drawing'
    const answer = window.prompt('Name this drawing:', suggestion)
    if (answer === null) return
    name = cleanName(answer) || suggestion
  }
  const saveOptions = editor.svgCanvas.mergeDeep(editor.svgCanvas.getSvgOption(), {
    images: editor.configObj.pref('img_save'),
    round_digits: 2,
  })
  for (const [key, value] of Object.entries(saveOptions)) {
    editor.svgCanvas.setSvgOption(key, value)
  }
  editor.svgCanvas.setSvgOption('apply', true)
  editor.svgCanvas.clearSelection()
  const state = editor.svgCanvas.svgCanvasToString()
  const thumb = await thumbnail(name)
  const headers = { 'Content-Type': 'application/json' }
  let id = asNew ? null : savedId
  let change = null
  let fresh = null
  let copied = false
  if (id && savedLockN > 0) {
    if (window.HubLock && window.HubLock.has(LOCKS, id)) {
      change = window.HubLock.change(LOCKS, id, savedLockN)
    }
    if (!change) {
      id = null
      copied = true
    }
  }
  if (!change && !id && window.HubLock) fresh = window.HubLock.create()
  if (change) Object.assign(headers, change.headers)
  if (fresh) headers['X-Lock-New'] = fresh.header
  let response
  try {
    response = await fetch(SAVES, {
      method: 'POST',
      headers,
      body: JSON.stringify({ kind: KIND, id: id || undefined, name, state, thumb }),
    })
  } catch (_error) {
    say("Not saved. The box's gallery did not answer.")
    return
  }
  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = body.message || body.error || ''
    } catch (_error) {
      // no JSON to read
    }
    say(`Not saved. The box's gallery answered ${response.status}${detail ? `: ${detail}` : '.'}`)
    return
  }
  const meta = await response.json()
  if (change) change.done()
  if (fresh) window.HubLock.keep(LOCKS, meta.id, fresh.seed)
  savedId = meta.id
  savedName = name
  savedLockN = meta.lock_n || 0
  editor.topPanel.updateTitle(`${name}.svg`)
  say(copied ? `${LOCKED_ELSEWHERE} It is named "${name}".` : `Saved "${name}" to the box's gallery.`)
}

const closeDialog = (overlay) => {
  for (const url of objectUrls) URL.revokeObjectURL(url)
  objectUrls = []
  overlay.remove()
}

const openSave = async (save, overlay) => {
  try {
    const response = await fetch(`${SAVES}/${encodeURIComponent(save.id)}`)
    if (!response.ok) throw new Error(String(response.status))
    const body = await response.json()
    if (typeof body.state !== 'string') throw new Error('not an SVG')
    await editor.loadSvgString(body.state)
    editor.updateCanvas()
  } catch (_error) {
    say("Not opened. The box's gallery did not answer with the drawing.")
    return
  }
  savedId = save.id
  savedName = save.name || 'drawing'
  savedLockN = save.lock_n || 0
  editor.topPanel.updateTitle(`${savedName}.svg`)
  closeDialog(overlay)
}

const showList = (saves) => {
  const old = document.getElementById('svghub-open')
  if (old) old.remove()
  const overlay = document.createElement('div')
  overlay.id = 'svghub-open'
  overlay.style.cssText =
    'position:fixed;inset:0;z-index:2147483001;display:flex;align-items:center;justify-content:center;' +
    'background:rgba(0,0,0,.55);font:14px/1.4 system-ui,sans-serif'
  const panel = document.createElement('div')
  panel.style.cssText =
    'width:min(34rem,calc(100vw - 2rem));max-height:calc(100vh - 4rem);overflow:auto;padding:14px 16px;' +
    'border-radius:10px;background:#1f2230;color:#e6e8f2;box-shadow:0 10px 40px rgba(0,0,0,.5)'
  const head = document.createElement('div')
  head.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px'
  const title = document.createElement('strong')
  title.textContent = "Open from the box's gallery"
  const close = document.createElement('button')
  close.type = 'button'
  close.textContent = 'Close'
  close.style.cssText = 'padding:4px 10px;border:0;border-radius:6px;cursor:pointer'
  close.addEventListener('click', () => closeDialog(overlay))
  head.append(title, close)
  panel.append(head)
  if (!saves.length) {
    const none = document.createElement('p')
    none.textContent = "No drawings in the box's gallery yet."
    panel.append(none)
  }
  for (const save of saves) {
    const row = document.createElement('button')
    row.type = 'button'
    row.style.cssText =
      'display:flex;align-items:center;gap:10px;width:100%;margin:6px 0;padding:8px;border:0;' +
      'border-radius:8px;background:#2a2e3f;color:inherit;cursor:pointer;text-align:left'
    const picture = document.createElement('img')
    picture.alt = ''
    picture.width = 72
    picture.height = 54
    picture.style.cssText = 'object-fit:contain;background:#151722;border-radius:4px;flex:none'
    picture.hidden = !save.thumb
    if (save.thumb) {
      fetch(`${SAVES}/${encodeURIComponent(save.id)}/thumb`)
        .then((response) => (response.ok ? response.blob() : null))
        .then((blob) => {
          if (!blob) return
          const url = URL.createObjectURL(new Blob([blob], { type: 'image/png' }))
          objectUrls.push(url)
          picture.src = url
        })
        .catch(() => {})
    }
    const label = document.createElement('span')
    label.textContent = save.name || 'drawing'
    row.append(picture, label)
    row.addEventListener('click', () => openSave(save, overlay))
    panel.append(row)
  }
  overlay.append(panel)
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) closeDialog(overlay)
  })
  const onKey = (event) => {
    if (event.key === 'Escape') {
      document.removeEventListener('keydown', onKey)
      closeDialog(overlay)
    }
  }
  document.addEventListener('keydown', onKey)
  document.body.append(overlay)
}

const openFromBox = async () => {
  let body
  try {
    const response = await fetch(SAVES)
    if (!response.ok) throw new Error(String(response.status))
    body = await response.json()
  } catch (_error) {
    say("Could not read the box's gallery. The device Open and Save still work.")
    return
  }
  showList((body.saves || []).filter((save) => save.kind === KIND))
}

const addMenuItems = () => {
  const { $id, $click } = editor.svgCanvas
  const menu = $id('main_button')
  const items = [
    ['tool_hub_open', 'Open from box', 'shift+O', 'open.svg', openFromBox],
    ['tool_hub_save', 'Save to box', 'shift+S', 'saveImg.svg', () => saveDrawing(false)],
    ['tool_hub_save_as', 'Save as', 'shift+A', 'saveImg.svg', () => saveDrawing(true)],
  ]
  items.forEach(([id, label, shortcut, icon, action], offset) => {
    editor.svgCanvas.insertChildAtIndex(
      menu,
      `<se-menu-item id="${id}" label="${label}" shortcut="${shortcut}" src="${icon}"></se-menu-item>`,
      5 + offset,
    )
    $click($id(id), action)
  })
}

export default {
  name: 'hub-store',
  init() {
    editor = this
    addMenuItems()
  },
  // A device file is not the drawing in the gallery: the next save makes a new row.
  onOpenedDocument() {
    savedId = null
    savedName = ''
    savedLockN = 0
  },
  afterClear() {
    savedId = null
    savedName = ''
    savedLockN = 0
  },
}
