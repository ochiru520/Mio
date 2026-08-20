const {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  Menu,
  nativeImage,
  screen,
  session,
  Tray,
} = require('electron')
const fs = require('fs')
const http = require('http')
const path = require('path')
const crypto = require('crypto')
const {
  modelCapabilities,
  previewCandidate,
  registerUnlistedExpressions,
  renderOptimizedModel,
} = require('./model-discovery')

const API_BASE = String(process.env.MIO_PET_API_BASE || 'http://127.0.0.1:8000').replace(/\/$/, '')
const defaultStateRoot = fs.existsSync('D:/')
  ? 'D:/Mio数据'
  : path.join(process.env.LOCALAPPDATA || process.env.USERPROFILE || '.', 'MioAgent')
const STATE_DIR = path.resolve(process.env.MIO_PET_STATE_DIR || path.join(defaultStateRoot, 'Live2D桌宠'))
const MODELS_DIR = path.join(STATE_DIR, 'models')
const STATE_PATH = path.join(STATE_DIR, 'runtime.json')
const LOG_PATH = path.join(STATE_DIR, 'desktop-pet.log')
const INSTANCE_DIR = process.env.MIO_PET_ALLOW_PARALLEL === '1'
  ? path.join(STATE_DIR, 'instance')
  : path.resolve(process.env.MIO_PET_INSTANCE_DIR || path.join(path.dirname(STATE_DIR), 'Live2D桌宠进程'))

app.setName('MioLive2D桌宠')
app.setAppUserModelId('local.mio.live2d.desktop')
app.setPath('userData', INSTANCE_DIR)
app.setPath('sessionData', path.join(STATE_DIR, 'Chromium'))
fs.mkdirSync(MODELS_DIR, { recursive: true })
app.commandLine.appendSwitch('disable-background-timer-throttling')
app.commandLine.appendSwitch('disable-renderer-backgrounding')
app.commandLine.appendSwitch('disable-backgrounding-occluded-windows')
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required')
if (process.env.MIO_PET_DEBUG_PORT) {
  app.commandLine.appendSwitch('remote-debugging-port', String(process.env.MIO_PET_DEBUG_PORT))
}
if (process.env.MIO_PET_DISABLE_GPU === '1') app.disableHardwareAcceleration()

let mainWindow = null
let lockControlWindow = null
let chatWindow = null
let tray = null
let modelServer = null
let modelServerOrigin = ''
let quitting = false
let rendererRestarts = []
let desiredAlwaysOnTop = true
let clickThroughLocked = false
let rendererInteractive = false
let chatWindowOpen = false
let topmostWatchdog = null
let lockControlAnchor = null
let parentWatchdog = null
const lastWindowReportAt = new Map()

function log(message, details = '') {
  try {
    if (fs.existsSync(LOG_PATH) && fs.statSync(LOG_PATH).size > 1024 * 1024) {
      fs.renameSync(LOG_PATH, `${LOG_PATH}.1`)
    }
    fs.appendFileSync(
      LOG_PATH,
      `${new Date().toISOString()} ${message}${details ? ` ${details}` : ''}\n`,
      'utf8',
    )
  } catch (_) {}
}

function startParentWatchdog() {
  const parentPid = Number(process.env.MIO_AGENT_PARENT_PID || 0)
  if (!Number.isInteger(parentPid) || parentPid <= 0) return
  clearInterval(parentWatchdog)
  parentWatchdog = setInterval(() => {
    try {
      process.kill(parentPid, 0)
    } catch (_) {
      log('agent.parent_gone', `pid=${parentPid}`)
      quitting = true
      app.quit()
    }
  }, 1500)
}

function postBackend(pathname) {
  return new Promise((resolve, reject) => {
    const request = http.request(`${API_BASE}${pathname}`, {
      method: 'POST',
      headers: { 'Content-Length': '0' },
      timeout: 5000,
    }, (response) => {
      const chunks = []
      response.on('data', (chunk) => chunks.push(chunk))
      response.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8')
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`HTTP ${response.statusCode}: ${body}`))
          return
        }
        try { resolve(body ? JSON.parse(body) : { ok: true }) }
        catch (_) { resolve({ ok: true }) }
      })
    })
    request.on('timeout', () => request.destroy(new Error('请求 Mio 超时')))
    request.on('error', reject)
    request.end()
  })
}

function postBackendJson(pathname, payload) {
  return new Promise((resolve, reject) => {
    const body = Buffer.from(JSON.stringify(payload), 'utf8')
    const request = http.request(`${API_BASE}${pathname}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': String(body.length),
      },
      timeout: 3000,
    }, (response) => {
      const chunks = []
      response.on('data', (chunk) => chunks.push(chunk))
      response.on('end', () => {
        const text = Buffer.concat(chunks).toString('utf8')
        if (response.statusCode < 200 || response.statusCode >= 300) {
          reject(new Error(`HTTP ${response.statusCode}: ${text}`))
          return
        }
        try { resolve(text ? JSON.parse(text) : { ok: true }) }
        catch (_) { resolve({ ok: true }) }
      })
    })
    request.on('timeout', () => request.destroy(new Error('窗口拓扑上报超时')))
    request.on('error', reject)
    request.end(body)
  })
}

function windowState(windowId, window, action) {
  let bounds = { x: 0, y: 0, width: 0, height: 0 }
  if (window && !window.isDestroyed()) bounds = window.getBounds()
  return {
    source: 'electron-main',
    runtime: 'electron',
    window_id: windowId,
    pid: process.pid,
    action,
    correlation_id: `${Date.now().toString(36)}-${crypto.randomBytes(5).toString('hex')}`,
    visible: Boolean(window && !window.isDestroyed() && window.isVisible()),
    focused: Boolean(window && !window.isDestroyed() && window.isFocused()),
    bounds,
  }
}

function reportWindowAction(windowId, window, action) {
  const reportKey = `${windowId}:${action}`
  const now = Date.now()
  if (action === 'positioned' && now - Number(lastWindowReportAt.get(reportKey) || 0) < 250) return null
  lastWindowReportAt.set(reportKey, now)
  const payload = windowState(windowId, window, action)
  log(
    'window.action',
    `source=${payload.source} runtime=${payload.runtime} window_id=${windowId} pid=${payload.pid} action=${action} correlation_id=${payload.correlation_id}`,
  )
  postBackendJson('/api/companion/window-topology/events', payload).catch((error) => {
    log('window.action_report_failed', `${windowId} ${action} ${error.message || String(error)}`)
  })
  return payload
}

async function openAgentWindow() {
  try {
    const result = await postBackend('/api/companion/agent/show')
    log('agent.show', String(result.method || 'unknown'))
    return result
  } catch (error) {
    log('agent.show_failed', error.message || String(error))
    return { ok: false, error: error.message || String(error) }
  }
}

function loadRuntimeState() {
  try {
    const value = JSON.parse(fs.readFileSync(STATE_PATH, 'utf8'))
    return value && typeof value === 'object' ? value : {}
  } catch (_) {
    return {}
  }
}

function saveRuntimeState(changes) {
  const state = { ...loadRuntimeState(), ...changes }
  const temporary = `${STATE_PATH}.tmp`
  fs.writeFileSync(temporary, JSON.stringify(state, null, 2), 'utf8')
  fs.renameSync(temporary, STATE_PATH)
  return state
}

function sanitizeModelId(name) {
  const normalized = String(name || 'model')
    .normalize('NFKC')
    .replace(/[^a-zA-Z0-9\u4e00-\u9fff_-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 48) || 'model'
  return `${normalized}-${Date.now().toString(36)}`
}

function walkFiles(root) {
  const found = []
  const pending = [root]
  while (pending.length) {
    const current = pending.pop()
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const absolute = path.join(current, entry.name)
      if (entry.isDirectory()) pending.push(absolute)
      else found.push(absolute)
    }
  }
  return found
}

function modelCatalog() {
  const catalog = []
  if (!fs.existsSync(MODELS_DIR)) return catalog
  for (const entry of fs.readdirSync(MODELS_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const modelRoot = path.join(MODELS_DIR, entry.name)
    const metadataPath = path.join(modelRoot, 'mio-model.json')
    try {
      const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'))
      const sourceModelPath = path.resolve(modelRoot, metadata.modelPath)
      if (!sourceModelPath.startsWith(`${modelRoot}${path.sep}`) || !fs.existsSync(sourceModelPath)) continue
      let renderAsset = {
        modelPath: sourceModelPath,
        optimized: false,
        optimizedTextureCount: 0,
        maxTextureSize: 2048,
        sourceTextureMaxSize: 0,
      }
      try {
        renderAsset = renderOptimizedModel(modelRoot, sourceModelPath, nativeImage, 2048)
      } catch (error) {
        log('model.optimize_failed', `${entry.name}: ${error.message || String(error)}`)
      }
      const modelPath = renderAsset.modelPath
      const relative = path.relative(modelRoot, modelPath).split(path.sep).map(encodeURIComponent).join('/')
      catalog.push({
        id: entry.name,
        name: String(metadata.name || entry.name),
        source: 'imported',
        modelUrl: `${modelServerOrigin}/models/${encodeURIComponent(entry.name)}/${relative}`,
        importedAt: String(metadata.importedAt || ''),
        renderOptimization: {
          optimized: Boolean(renderAsset.optimized),
          optimizedTextureCount: Number(renderAsset.optimizedTextureCount || 0),
          maxTextureSize: Number(renderAsset.maxTextureSize || 2048),
          sourceTextureMaxSize: Number(renderAsset.sourceTextureMaxSize || 0),
        },
        sourceLabel: String(metadata.sourceLabel || '本地目录导入'),
        capabilities: metadata.capabilities && typeof metadata.capabilities === 'object'
          ? metadata.capabilities
          : {},
        authorization: metadata.authorization && typeof metadata.authorization === 'object'
          ? metadata.authorization
          : { status: 'unknown', notice: '未记录模型授权，禁止随安装包分发。' },
      })
    } catch (_) {}
  }
  return catalog
}

function contentType(filePath) {
  const extension = path.extname(filePath).toLowerCase()
  return {
    '.json': 'application/json; charset=utf-8',
    '.moc3': 'application/octet-stream',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.wav': 'audio/wav',
  }[extension] || 'application/octet-stream'
}

function startModelServer() {
  return new Promise((resolve, reject) => {
    modelServer = http.createServer((request, response) => {
      response.setHeader('Access-Control-Allow-Origin', '*')
      response.setHeader('Cache-Control', 'no-cache')
      try {
        const requestUrl = new URL(request.url, 'http://127.0.0.1')
        const parts = requestUrl.pathname.split('/').filter(Boolean).map(decodeURIComponent)
        if (parts.length < 3 || parts[0] !== 'models') {
          response.writeHead(404).end()
          return
        }
        const modelRoot = path.resolve(MODELS_DIR, parts[1])
        const filePath = path.resolve(modelRoot, ...parts.slice(2))
        if (!filePath.startsWith(`${modelRoot}${path.sep}`) || !fs.statSync(filePath).isFile()) {
          response.writeHead(403).end()
          return
        }
        response.writeHead(200, { 'Content-Type': contentType(filePath) })
        fs.createReadStream(filePath).pipe(response)
      } catch (_) {
        response.writeHead(404).end()
      }
    })
    modelServer.once('error', reject)
    modelServer.listen(0, '127.0.0.1', () => {
      const address = modelServer.address()
      modelServerOrigin = `http://127.0.0.1:${address.port}`
      resolve()
    })
  })
}

function virtualBounds() {
  const displays = screen.getAllDisplays()
  const left = Math.min(...displays.map((display) => display.bounds.x))
  const top = Math.min(...displays.map((display) => display.bounds.y))
  const right = Math.max(...displays.map((display) => display.bounds.x + display.bounds.width))
  const bottom = Math.max(...displays.map((display) => display.bounds.y + display.bounds.height))
  return { x: left, y: top, width: right - left, height: bottom - top }
}

function iconPath() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'desktop', 'mio.ico')
    : path.resolve(__dirname, '..', 'desktop', 'mio.ico')
}

function sendModelsChanged() {
  if (!mainWindow || mainWindow.isDestroyed()) return
  mainWindow.webContents.send('mio-desktop:models-changed', {
    models: modelCatalog(),
    selectedModelId: String(loadRuntimeState().selectedModelId || ''),
  })
}

async function importModel() {
  const result = await dialog.showOpenDialog({
    title: '导入 Live2D 模型目录',
    properties: ['openDirectory'],
  })
  if (result.canceled || !result.filePaths[0]) return { ok: false, canceled: true }
  const sourceRoot = result.filePaths[0]
  const allFiles = walkFiles(sourceRoot)
  const modelFiles = allFiles.filter((file) => file.toLowerCase().endsWith('.model3.json'))
  if (!modelFiles.length) {
    return { ok: false, error: '所选目录中没有 .model3.json 文件' }
  }
  const sourceModel = modelFiles[0]
  let capabilities
  try {
    capabilities = modelCapabilities(sourceRoot, sourceModel, allFiles)
  } catch (error) {
    return { ok: false, error: `模型配置无法解析：${error.message || error}` }
  }
  const id = sanitizeModelId(path.basename(sourceRoot))
  const targetRoot = path.join(MODELS_DIR, id)
  fs.cpSync(sourceRoot, targetRoot, { recursive: true, errorOnExist: true })
  const modelPath = path.relative(sourceRoot, sourceModel)
  const importedModelPath = path.join(targetRoot, modelPath)
  const importedFiles = walkFiles(targetRoot)
  registerUnlistedExpressions(targetRoot, importedModelPath, importedFiles)
  capabilities = modelCapabilities(targetRoot, importedModelPath, importedFiles)
  const preview = previewCandidate(sourceRoot, allFiles)
  fs.writeFileSync(
    path.join(targetRoot, 'mio-model.json'),
    JSON.stringify({
      name: path.basename(sourceRoot),
      modelPath,
      previewPath: preview ? path.relative(sourceRoot, preview) : '',
      sourceLabel: sourceRoot,
      importedAt: new Date().toISOString(),
      capabilities,
      authorization: {
        status: capabilities.licenseFiles.length ? 'files_found' : 'unverified',
        licenseFiles: capabilities.licenseFiles,
        notice: capabilities.licenseFiles.length
          ? '已发现随模型提供的授权文件；使用和分发前仍需按文件内容核对。'
          : '未发现授权文件；该模型只能本机测试，禁止随安装包分发。',
        distributionAllowed: false,
      },
    }, null, 2),
    'utf8',
  )
  saveRuntimeState({ selectedModelId: id })
  sendModelsChanged()
  return { ok: true, modelId: id, models: modelCatalog() }
}

function applyMainWindowMousePolicy() {
  if (!mainWindow || mainWindow.isDestroyed()) return
  if (chatWindowOpen) {
    mainWindow.setIgnoreMouseEvents(true)
    return
  }
  mainWindow.setIgnoreMouseEvents(clickThroughLocked || !rendererInteractive, { forward: true })
}

function setMouseInteractive(interactive) {
  if (!mainWindow || mainWindow.isDestroyed()) return
  rendererInteractive = Boolean(interactive)
  applyMainWindowMousePolicy()
}

function applyWindowLayering() {
  if (!mainWindow || mainWindow.isDestroyed()) return
  mainWindow.setAlwaysOnTop(desiredAlwaysOnTop, desiredAlwaysOnTop ? 'screen-saver' : 'normal')
  applyMainWindowMousePolicy()
  if (desiredAlwaysOnTop && !chatWindowOpen) mainWindow.moveTop()
  if (chatWindow && !chatWindow.isDestroyed() && chatWindow.isVisible()) {
    chatWindow.setAlwaysOnTop(true, 'screen-saver')
    chatWindow.moveTop()
  }
  if (lockControlWindow && !lockControlWindow.isDestroyed()) {
    lockControlWindow.setAlwaysOnTop(
      desiredAlwaysOnTop,
      desiredAlwaysOnTop ? 'screen-saver' : 'normal',
    )
    if (desiredAlwaysOnTop) lockControlWindow.moveTop()
  }
}

function setChatWindowOpen(open) {
  chatWindowOpen = Boolean(open)
  applyWindowLayering()
}

function normalizedChatAnchor(anchor) {
  const anchorX = Number(anchor?.anchorX ?? anchor?.anchor_x)
  const anchorY = Number(anchor?.anchorY ?? anchor?.anchor_y)
  if (Number.isFinite(anchorX) && Number.isFinite(anchorY)) return { x: anchorX, y: anchorY }
  if (lockControlAnchor) {
    const desktop = virtualBounds()
    return {
      x: desktop.x + lockControlAnchor.x + lockControlAnchor.width / 2,
      y: desktop.y + lockControlAnchor.y,
    }
  }
  const cursor = screen.getCursorScreenPoint()
  return { x: cursor.x, y: cursor.y }
}

function positionChatWindow(anchor) {
  if (!chatWindow || chatWindow.isDestroyed()) return
  const point = normalizedChatAnchor(anchor)
  const display = screen.getDisplayNearestPoint({ x: Math.round(point.x), y: Math.round(point.y) })
  const area = display.workArea
  const [width, height] = chatWindow.getSize()
  const preferredAbove = Math.round(point.y - height - 18)
  const fallbackBelow = Math.round(point.y + 24)
  const x = Math.max(area.x + 8, Math.min(area.x + area.width - width - 8, Math.round(point.x - width / 2)))
  const y = preferredAbove >= area.y + 8
    ? preferredAbove
    : Math.max(area.y + 8, Math.min(area.y + area.height - height - 8, fallbackBelow))
  chatWindow.setPosition(x, y, false)
  reportWindowAction('pet-chat-input', chatWindow, 'positioned')
}

function hideChatWindow() {
  if (chatWindow && !chatWindow.isDestroyed()) {
    chatWindow.webContents.executeJavaScript(
      "window.dispatchEvent(new CustomEvent('mio:pet-chat-hidden'))",
      true,
    ).catch(() => {})
    chatWindow.hide()
    reportWindowAction('pet-chat-input', chatWindow, 'hidden')
  }
  setChatWindowOpen(false)
  return { ok: true, visible: false }
}

function createChatWindow(anchor) {
  chatWindow = new BrowserWindow({
    width: 520,
    height: 84,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    movable: true,
    focusable: true,
    hasShadow: false,
    fullscreenable: false,
    webPreferences: {
      preload: path.join(__dirname, 'pet-chat-preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      backgroundThrottling: false,
    },
  })
  reportWindowAction('pet-chat-input', chatWindow, 'created')
  chatWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  chatWindow.loadURL(`${API_BASE}/agent-app/?v=electron-pet-chat#pet-chat-window`)
  chatWindow.once('ready-to-show', () => {
    positionChatWindow(anchor)
    chatWindow.show()
    chatWindow.focus()
    setChatWindowOpen(true)
    reportWindowAction('pet-chat-input', chatWindow, 'shown')
  })
  chatWindow.on('hide', () => setChatWindowOpen(false))
  chatWindow.on('focus', () => reportWindowAction('pet-chat-input', chatWindow, 'focused'))
  chatWindow.on('closed', () => {
    reportWindowAction('pet-chat-input', chatWindow, 'closed')
    chatWindow = null
    setChatWindowOpen(false)
  })
  chatWindow.webContents.on('did-fail-load', (_event, code, description) => {
    log('chat_window.load_failed', `${code} ${description}`)
  })
}

function showChatWindow(anchor) {
  if (!chatWindow || chatWindow.isDestroyed()) {
    createChatWindow(anchor)
    return { ok: true, visible: true, created: true }
  }
  positionChatWindow(anchor)
  chatWindow.show()
  chatWindow.focus()
  setChatWindowOpen(true)
  reportWindowAction('pet-chat-input', chatWindow, 'shown')
  return { ok: true, visible: true, created: false }
}

function toggleChatWindow(anchor) {
  if (chatWindow && !chatWindow.isDestroyed() && chatWindow.isVisible()) return hideChatWindow()
  return showChatWindow(anchor)
}

function restoreWindowVisibility() {
  if (!mainWindow || mainWindow.isDestroyed()) return
  if (!mainWindow.isVisible()) mainWindow.showInactive()
  applyWindowLayering()
  if (lockControlWindow && !lockControlWindow.isDestroyed()) {
    if (!lockControlAnchor) positionLockControlAtDefault()
    if (!lockControlWindow.isVisible()) lockControlWindow.showInactive()
  }
}

function sendLockControlState() {
  if (!lockControlWindow || lockControlWindow.isDestroyed()) return
  lockControlWindow.webContents.send('mio-lock-control:state', { locked: clickThroughLocked })
}

function positionLockControlAtDefault() {
  if (!lockControlWindow || lockControlWindow.isDestroyed()) return
  const display = screen.getDisplayNearestPoint(screen.getCursorScreenPoint())
  const area = display.workArea
  const size = 34
  lockControlWindow.setBounds({
    x: Math.round(area.x + area.width - size - 14),
    y: Math.round(area.y + area.height * 0.55 - size / 2),
    width: size,
    height: size,
  }, false)
  reportWindowAction('pet-lock-control', lockControlWindow, 'positioned')
}

function positionLockControl(anchor) {
  if (!anchor || typeof anchor !== 'object') return
  const values = ['x', 'y', 'width', 'height'].map((key) => Number(anchor[key]))
  if (!values.every(Number.isFinite) || values[2] <= 0 || values[3] <= 0) return
  lockControlAnchor = { x: values[0], y: values[1], width: values[2], height: values[3] }
  if (!lockControlWindow || lockControlWindow.isDestroyed()) return
  const desktop = virtualBounds()
  const anchorCenter = {
    x: Math.round(desktop.x + lockControlAnchor.x + lockControlAnchor.width / 2),
    y: Math.round(desktop.y + lockControlAnchor.y + lockControlAnchor.height / 2),
  }
  const workArea = screen.getDisplayNearestPoint(anchorCenter).workArea
  const size = 34
  const preferredX = desktop.x + lockControlAnchor.x + lockControlAnchor.width - size - 10
  // 放在角色右下方，避免挡住头顶的输入框和说话气泡。
  const preferredY = desktop.y + lockControlAnchor.y + lockControlAnchor.height + 10
  const x = Math.round(Math.max(workArea.x + 6, Math.min(workArea.x + workArea.width - size - 6, preferredX)))
  const y = Math.round(Math.max(workArea.y + 6, Math.min(workArea.y + workArea.height - size - 6, preferredY)))
  lockControlWindow.setBounds({ x, y, width: size, height: size }, false)
  reportWindowAction('pet-lock-control', lockControlWindow, 'positioned')
  if (!lockControlWindow.isVisible()) lockControlWindow.showInactive()
}

function createLockControlWindow() {
  lockControlWindow = new BrowserWindow({
    width: 34,
    height: 34,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    movable: false,
    focusable: false,
    hasShadow: false,
    fullscreenable: false,
    webPreferences: {
      preload: path.join(__dirname, 'lock-control-preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      backgroundThrottling: false,
    },
  })
  reportWindowAction('pet-lock-control', lockControlWindow, 'created')
  lockControlWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  lockControlWindow.loadFile(path.join(__dirname, 'lock-control.html'))
  lockControlWindow.webContents.on('did-finish-load', () => {
    sendLockControlState()
    if (lockControlAnchor) positionLockControl(lockControlAnchor)
    else positionLockControlAtDefault()
    lockControlWindow.showInactive()
    reportWindowAction('pet-lock-control', lockControlWindow, 'shown')
  })
  lockControlWindow.on('closed', () => {
    reportWindowAction('pet-lock-control', lockControlWindow, 'closed')
    lockControlWindow = null
  })
}

function updateTrayMenu() {
  if (!tray || tray.isDestroyed()) return
  tray.setContextMenu(Menu.buildFromTemplate([
    {
      label: '固定并穿透',
      type: 'checkbox',
      checked: clickThroughLocked,
      click: (item) => setClickThroughLocked(Boolean(item.checked), true),
    },
    { label: '显示桌宠', click: () => restoreWindowVisibility() },
    { label: '导入 Live2D 模型', click: () => importModel() },
    { label: '打开 Mio', click: () => openAgentWindow() },
    { type: 'separator' },
    { label: '退出桌宠', click: () => { quitting = true; app.quit() } },
  ]))
}

function setClickThroughLocked(enabled, notifyRenderer = false) {
  const nextValue = Boolean(enabled)
  const changed = clickThroughLocked !== nextValue
  clickThroughLocked = nextValue
  if (changed) saveRuntimeState({ clickThroughLocked })
  if (mainWindow && !mainWindow.isDestroyed()) {
    applyMainWindowMousePolicy()
    if (notifyRenderer) {
      mainWindow.webContents.send('mio-desktop:click-through-changed', clickThroughLocked)
    }
  }
  sendLockControlState()
  if (changed) updateTrayMenu()
}

function createWindow() {
  const bounds = virtualBounds()
  mainWindow = new BrowserWindow({
    ...bounds,
    show: false,
    frame: false,
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    movable: false,
    hasShadow: false,
    fullscreenable: false,
    thickFrame: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      backgroundThrottling: false,
    },
  })
  reportWindowAction('pet-root', mainWindow, 'created')
  desiredAlwaysOnTop = true
  clickThroughLocked = Boolean(loadRuntimeState().clickThroughLocked)
  restoreWindowVisibility()
  mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true })
  setMouseInteractive(false)
  mainWindow.loadURL(`${API_BASE}/agent-app/live2d-pet/index.html?runtime=electron`)
  log('window.load', `${API_BASE}/agent-app/live2d-pet/index.html`)
  mainWindow.once('ready-to-show', () => restoreWindowVisibility())
  mainWindow.on('show', () => reportWindowAction('pet-root', mainWindow, 'shown'))
  mainWindow.on('focus', () => reportWindowAction('pet-root', mainWindow, 'focused'))
  mainWindow.webContents.on('did-finish-load', () => restoreWindowVisibility())
  mainWindow.on('blur', () => {
    if (desiredAlwaysOnTop) setTimeout(restoreWindowVisibility, 40)
  })
  mainWindow.on('minimize', () => {
    if (desiredAlwaysOnTop) setTimeout(restoreWindowVisibility, 40)
  })
  mainWindow.on('hide', () => {
    reportWindowAction('pet-root', mainWindow, 'hidden')
    if (desiredAlwaysOnTop && !quitting) setTimeout(restoreWindowVisibility, 40)
  })
  mainWindow.webContents.on('did-fail-load', (_event, code, description) => {
    log('window.load_failed', `${code} ${description}`)
  })
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    log('renderer.gone', `${details.reason} ${details.exitCode}`)
    if (quitting) return
    const now = Date.now()
    rendererRestarts = rendererRestarts.filter((value) => now - value < 60000)
    if (rendererRestarts.length >= 3) return
    rendererRestarts.push(now)
    setTimeout(() => {
      if (mainWindow && !mainWindow.isDestroyed()) mainWindow.reload()
    }, details.reason === 'oom' ? 2500 : 800)
  })
  mainWindow.on('closed', () => {
    reportWindowAction('pet-root', mainWindow, 'closed')
    mainWindow = null
  })
  if (!lockControlWindow || lockControlWindow.isDestroyed()) createLockControlWindow()
  clearInterval(topmostWatchdog)
  topmostWatchdog = setInterval(() => {
    if (!quitting && desiredAlwaysOnTop) restoreWindowVisibility()
  }, 1500)
}

function createTray() {
  const image = nativeImage.createFromPath(iconPath())
  tray = new Tray(image)
  tray.setToolTip('Mio Live2D 桌宠')
  const showPet = () => {
    if (!mainWindow || mainWindow.isDestroyed()) createWindow()
    else restoreWindowVisibility()
  }
  updateTrayMenu()
  tray.on('double-click', showPet)
}

function registerIpc() {
  ipcMain.handle('mio-desktop:get-state', () => ({
    apiBase: API_BASE,
    bounds: virtualBounds(),
    models: modelCatalog(),
    selectedModelId: String(loadRuntimeState().selectedModelId || ''),
    clickThroughLocked,
  }))
  ipcMain.on('mio-desktop:set-interactive', (_event, interactive) => setMouseInteractive(Boolean(interactive)))
  ipcMain.on('mio-desktop:set-click-through-locked', (_event, enabled) => {
    setClickThroughLocked(Boolean(enabled), false)
  })
  ipcMain.on('mio-desktop:set-lock-control-anchor', (_event, bounds) => {
    positionLockControl(bounds)
  })
  ipcMain.on('mio-lock-control:toggle', () => {
    setClickThroughLocked(!clickThroughLocked, true)
  })
  ipcMain.on('mio-desktop:set-always-on-top', (_event, enabled) => {
    desiredAlwaysOnTop = Boolean(enabled)
    restoreWindowVisibility()
  })
  ipcMain.on('mio-desktop:set-chat-window-open', (_event, open) => setChatWindowOpen(open))
  ipcMain.handle('mio-desktop:toggle-chat-window', (_event, anchor) => toggleChatWindow(anchor))
  ipcMain.handle('mio-desktop:show-chat-window', (_event, anchor) => showChatWindow(anchor))
  ipcMain.on('mio-pet-chat:hide', () => hideChatWindow())
  ipcMain.on('mio-desktop:restore-visibility', () => restoreWindowVisibility())
  ipcMain.handle('mio-desktop:import-model', importModel)
  ipcMain.handle('mio-desktop:select-model', (_event, modelId) => {
    const id = String(modelId || '')
    const available = modelCatalog().some((model) => model.id === id)
    saveRuntimeState({ selectedModelId: available ? id : '' })
    sendModelsChanged()
    return { ok: true, selectedModelId: available ? id : '' }
  })
  ipcMain.handle('mio-desktop:open-agent', () => openAgentWindow())
  ipcMain.on('mio-desktop:close', () => { quitting = true; app.quit() })
}

const hasLock = app.requestSingleInstanceLock()
if (!hasLock) app.quit()
else {
  app.on('second-instance', () => {
    restoreWindowVisibility()
  })
  app.whenReady().then(async () => {
    session.defaultSession.setPermissionCheckHandler((_webContents, permission, requestingOrigin) => (
      permission === 'media' && requestingOrigin.startsWith(`${API_BASE}/`)
    ))
    session.defaultSession.setPermissionRequestHandler((webContents, permission, callback) => {
      const originAllowed = webContents.getURL().startsWith(`${API_BASE}/`)
      callback(permission === 'media' && originAllowed)
    })
    await startModelServer()
    log('runtime.ready', `modelServer=${modelServerOrigin}`)
    registerIpc()
    createWindow()
    createTray()
    startParentWatchdog()
  })
}

app.on('window-all-closed', (event) => {
  if (!quitting) event.preventDefault()
})

app.on('before-quit', () => {
  quitting = true
  clearInterval(topmostWatchdog)
  clearInterval(parentWatchdog)
  if (chatWindow && !chatWindow.isDestroyed()) chatWindow.destroy()
  if (modelServer) modelServer.close()
})
