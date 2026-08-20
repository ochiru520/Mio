/*
 * Mio Live2D renderer. The Electron process owns desktop behavior while
 * FastAPI remains the single owner of persona, memory, speech and vision.
 */
(function () {
  'use strict'

  const builtInModels = {
    hiyori: {
      id: 'hiyori',
      name: 'Hiyori Momose',
      url: './models/hiyori/Hiyori.model3.json',
      idleGroup: 'Idle',
      tapGroup: 'TapBody',
      source: 'built_in',
      capabilities: {
        motions: [{ name: 'Idle', count: 9 }, { name: 'TapBody', count: 1 }],
        expressions: [], physics: true, pose: true,
        lipSyncParameters: ['ParamMouthOpenY'], eyeBlinkParameters: ['ParamEyeLOpen', 'ParamEyeROpen'],
        idleGroup: 'Idle', tapGroup: 'TapBody',
        motionSlots: {
          idle: 'Idle', touch: 'TapBody', think: 'Idle', speak: 'Idle', observe: 'Idle',
          cheerful: 'TapBody', concerned: 'Idle', alert: 'TapBody', attention: 'Idle', shy: 'Idle',
        },
        unassignedMotions: [],
      },
    },
  }

  const requestedModelId = new URLSearchParams(window.location.search).get('model')
  const runtimeMode = new URLSearchParams(window.location.search).get('runtime')
  const electronRuntime = Boolean(window.mioDesktop)
  const root = document.getElementById('pet-root')
  const canvas = document.getElementById('live2d-canvas')
  const loading = document.getElementById('loading')
  const errorPanel = document.getElementById('error')
  const errorMessage = document.getElementById('error-message')
  const contextMenu = document.getElementById('context-menu')
  const activityIndicator = document.getElementById('activity-indicator')
  const speechBubbleButton = contextMenu.querySelector('[data-action="speech-bubble"]')
  const speechLanguageButton = contextMenu.querySelector('[data-action="speech-language"]')
  const speechBubble = document.getElementById('speech-bubble')
  const speechBubbleText = document.getElementById('speech-bubble-text')
  const modelSelect = document.getElementById('model-select')
  const sizePanel = document.getElementById('size-panel')
  const sizeSlider = document.getElementById('size-slider')
  const sizeValue = document.getElementById('size-value')

  let app = null
  let model = null
  let currentModelId = ''
  let modelDefinitions = { ...builtInModels }
  let desktopState = { bounds: { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight } }
  let desktopSelectedModelId = ''
  let settings = {}
  let latestMessageId = null
  let lastStatusSuccess = Date.now()
  let activityRevision = -1
  let currentActivity = 'idle'
  let activityUntil = 0
  let currentEmotion = 'neutral'
  let emotionUntil = 0
  let speakingUntil = 0
  let nextIdleMotionAt = Date.now() + 9000
  let lastSizePercent = 0
  let pointerDown = null
  let dragged = false
  let modelLoadGeneration = 0
  let sizeCommitTimer = null
  let passThroughFrame = null
  let interactiveState = null
  let socket = null
  let socketRetry = 0
  let socketRetryTimer = null
  let socketPingTimer = null
  let backendOfflineSince = 0
  let backendOfflineTimer = null
  let audioContext = null
  let analyser = null
  let analyserData = null
  let currentAudio = null
  let currentAudioUrl = ''
  let currentSpeechAbort = null
  let streamGain = null
  let streamSources = new Set()
  let streamPlaybackActive = false
  let streamScheduledUntil = 0
  let streamPendingBuffers = []
  let streamPendingDuration = 0
  let streamHasStarted = false
  let streamSpeechToken = 0
  let voiceLifecycleActive = false
  let mouthLevel = 0
  let speechStartCount = 0
  let speechEndCount = 0
  let speechInterruptCount = 0
  let speechFinishedCount = 0
  let speechQueuedCount = 0
  let speechDroppedCount = 0
  let speechRequestCount = 0
  let speechFailureCount = 0
  let speechLifecycleHistory = []
  let lastSpeechError = ''
  let maxMouthLevel = 0
  let lastSpeechStartedAt = ''
  let lastSpeechEndedAt = ''
  let lastVoiceEventSent = false
  let lastStreamMode = ''
  let streamFirstAudioLatencyMs = null
  let lastVisualEvent = null
  let lastForegroundTitle = ''
  let activeResponseId = ''
  let activeSpeechPriority = -1
  let speechQueue = []
  let speechDrainTimer = null
  let activeSpeechText = ''
  let speechBubbleTimer = null
  let speechBubbleHideTimer = null
  let speechBubbleCharacterCount = 0
  let speechBubbleVisibleCount = 0
  let speechBubbleResponseId = ''
  let streamFirstAudioTimer = null
  let lastMotionGroup = ''
  let lastMotionPlayedAt = 0
  let cursorFocusFrame = null
  let lastForegroundHandledAt = 0
  let lastHandledForegroundTitle = ''
  let lastAppliedExpressionEmotion = ''
  let rendererUpdateCount = 0
  let rendererUpdateWindowStartedAt = performance.now()
  let rendererMeasuredFps = 0
  let rendererLastFrameAt = performance.now()
  let playbackFrameTimes = []
  let playbackFpsSamples = []
  let playbackFrameWindowStartedAt = 0
  let playbackFrameWindowCount = 0
  let proceduralMotionHint = ''
  let proceduralMotionStartedAt = 0
  let proceduralMotionUntil = 0
  let proceduralMotionIntensity = 0
  let proceduralMotionStartCount = 0
  let proceduralMotionPeak = 0
  let visualPerformanceUntil = 0
  let visualFrameTimes = []
  let visualFpsSamples = []
  let visualFrameWindowStartedAt = 0
  let visualFrameWindowCount = 0

  const browserBridge = {
    begin_drag: async () => ({ ok: true }),
    drag_to: async () => ({ ok: true }),
    end_drag: async () => ({ ok: true }),
    resize_window: async (percent) => ({ ok: true, percent }),
    set_size: async (percent) => ({ ok: true, percent }),
    cursor_state: async () => ({ ok: false, x: 0, y: 0 }),
    open_chat: async () => ({ ok: false }),
    open_agent: async () => ({ ok: false }),
    say_hello: async () => ({ ok: false }),
    toggle_observation: async () => ({ ok: false, running: false }),
    close_pet: async () => ({ ok: false }),
  }

  const electronBridge = {
    begin_drag: async () => ({ ok: true }),
    drag_to: async () => ({ ok: true }),
    end_drag: async () => ({ ok: true }),
    resize_window: async (percent) => ({ ok: true, percent }),
    set_size: async (percent) => {
      await request('/api/companion/size', {
        method: 'PATCH',
        body: JSON.stringify({ percent }),
      })
      return { ok: true, percent }
    },
    cursor_state: async () => ({ ok: false, x: 0, y: 0 }),
    open_chat: async () => {
      const bounds = model?.getBounds?.()
      const anchorX = bounds
        ? Number(window.screenX || 0) + Number(bounds.x || 0) + Number(bounds.width || 0) / 2
        : Number(window.screenX || 0) + window.innerWidth / 2
      const anchorY = bounds
        ? Number(window.screenY || 0) + Number(bounds.y || 0)
        : Number(window.screenY || 0) + window.innerHeight / 2
      return window.mioDesktop.toggleChatWindow({
        anchorX: Math.round(anchorX),
        anchorY: Math.round(anchorY),
      })
    },
    open_agent: async () => {
      return window.mioDesktop.openAgent()
    },
    say_hello: async () => {
      await dispatchSpeech({ text: '我在这里', emotion: 'gentle', source: 'desktop_pet', priority: 100 })
      return { ok: true }
    },
    toggle_observation: async () => toggleObservation(),
    close_pet: async () => window.mioDesktop.close(),
  }

  const waitForBridge = () => new Promise((resolve) => {
    if (electronRuntime) {
      resolve(electronBridge)
      return
    }
    if (window.pywebview?.api) {
      resolve(window.pywebview.api)
      return
    }
    window.addEventListener('pywebviewready', () => resolve(window.pywebview.api), { once: true })
    window.setTimeout(() => resolve(window.pywebview?.api || browserBridge), 350)
  })

  async function request(path, options = {}) {
    const response = await fetch(path, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    })
    if (!response.ok) {
      let message = `HTTP ${response.status}`
      try {
        const detail = await response.json()
        message = String(detail.detail || message)
      } catch (_) {}
      throw new Error(message)
    }
    const contentType = response.headers.get('content-type') || ''
    return contentType.includes('application/json') ? response.json() : response
  }

  function createApplication() {
    app = new PIXI.Application({
      view: canvas,
      autoStart: true,
      resizeTo: root,
      transparent: true,
      backgroundAlpha: 0,
      antialias: true,
      autoDensity: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
    })
    app.stage.sortableChildren = true
    app.ticker.add(updateModelPerformance, undefined, -50)
  }

  function modelDefinition(modelId) {
    return modelDefinitions[modelId] || modelDefinitions.hiyori
  }

  function currentCapabilityPayload() {
    const definition = modelDefinition(currentModelId)
    const capabilities = definition?.capabilities || {}
    return {
      motions: Array.isArray(capabilities.motions) ? capabilities.motions : [],
      expressions: Array.isArray(capabilities.expressions) ? capabilities.expressions : [],
      physics: Boolean(capabilities.physics),
      pose: Boolean(capabilities.pose),
      lipSyncParameters: Array.isArray(capabilities.lipSyncParameters) ? capabilities.lipSyncParameters : [],
      eyeBlinkParameters: Array.isArray(capabilities.eyeBlinkParameters) ? capabilities.eyeBlinkParameters : [],
      idleGroup: String(definition?.idleGroup || capabilities.idleGroup || ''),
      tapGroup: String(definition?.tapGroup || capabilities.tapGroup || ''),
      motionSlots: capabilities.motionSlots && typeof capabilities.motionSlots === 'object'
        ? { ...capabilities.motionSlots }
        : {},
      unassignedMotions: Array.isArray(capabilities.unassignedMotions)
        ? capabilities.unassignedMotions.map(String)
        : [],
    }
  }

  function sendRendererReady() {
    const definition = modelDefinition(currentModelId)
    sendSocket('renderer_ready', {
      runtime: runtimeMode || (electronRuntime ? 'electron' : (window.pywebview ? 'pywebview' : 'browser')),
      model_id: currentModelId,
      model_name: String(definition?.name || currentModelId),
      capabilities: currentCapabilityPayload(),
    })
  }

  function selectedModelId() {
    if (requestedModelId && modelDefinitions[requestedModelId]) return requestedModelId
    if (desktopSelectedModelId && modelDefinitions[desktopSelectedModelId]) return desktopSelectedModelId
    return modelDefinitions[String(settings.live2d_model_id || '')]
      ? String(settings.live2d_model_id)
      : 'hiyori'
  }

  function populateModelSelect() {
    if (!modelSelect) return
    const selected = selectedModelId()
    modelSelect.replaceChildren()
    Object.values(modelDefinitions).forEach((definition) => {
      const option = document.createElement('option')
      option.value = definition.id
      option.textContent = definition.name
      option.selected = definition.id === selected
      modelSelect.appendChild(option)
    })
  }

  function mergeImportedModels(payload) {
    const imported = Array.isArray(payload?.models) ? payload.models : []
    modelDefinitions = { ...builtInModels }
    imported.forEach((entry) => {
      if (!entry?.id || !entry?.modelUrl) return
      modelDefinitions[String(entry.id)] = {
        id: String(entry.id),
        name: String(entry.name || entry.id),
        url: String(entry.modelUrl),
        idleGroup: String(entry.capabilities?.idleGroup || ''),
        tapGroup: String(entry.capabilities?.tapGroup || ''),
        source: 'imported',
        capabilities: entry.capabilities && typeof entry.capabilities === 'object' ? entry.capabilities : {},
        authorization: entry.authorization && typeof entry.authorization === 'object' ? entry.authorization : {},
        renderOptimization: entry.renderOptimization && typeof entry.renderOptimization === 'object'
          ? { ...entry.renderOptimization }
          : {},
      }
    })
    desktopSelectedModelId = String(payload?.selectedModelId || desktopSelectedModelId || '')
    populateModelSelect()
  }

  async function loadModel(modelId) {
    const definition = modelDefinition(modelId)
    const generation = ++modelLoadGeneration
    loading.hidden = false
    errorPanel.hidden = true
    let nextModel = null
    try {
      nextModel = await PIXI.live2d.Live2DModel.from(definition.url, { autoInteract: false })
      if (generation !== modelLoadGeneration) {
        nextModel.destroy({ children: true, texture: false, baseTexture: false })
        return
      }
      if (model) {
        app.stage.removeChild(model)
        model.destroy({ children: true, texture: false, baseTexture: false })
      }
      app.stage.children.slice().forEach((child) => {
        if (child?.internalModel && child !== nextModel) {
          app.stage.removeChild(child)
          child.destroy({ children: true, texture: false, baseTexture: false })
        }
      })
      model = nextModel
      model.anchor.set(0.5, 0.5)
      model.eventMode = 'none'
      app.stage.addChild(model)
      currentModelId = definition.id
      lastAppliedExpressionEmotion = ''
      fitModel()
      nextIdleMotionAt = Date.now() + 3500
      loading.hidden = true
      populateModelSelect()
      updateMousePassThrough(true)
      sendRendererReady()
    } catch (error) {
      if (generation !== modelLoadGeneration) {
        if (nextModel) nextModel.destroy({ children: true, texture: false, baseTexture: false })
        return
      }
      console.error('[MioLive2D] model load failed', error)
      loading.hidden = true
      errorPanel.hidden = false
      errorMessage.textContent = String(error?.message || error || '未知错误')
      sendSocket('renderer_error', { message: errorMessage.textContent, model_id: definition.id })
      setInteractive(true)
    }
  }

  function targetSize() {
    const percent = Number(settings.pet_size_percent || 150) / 100
    return { width: 280 * percent, height: 400 * percent }
  }

  function fitModel() {
    if (!model) return
    model.scale.set(1)
    const bounds = model.getLocalBounds()
    const target = targetSize()
    const baseScale = Math.min(
      target.width / Math.max(1, bounds.width),
      target.height / Math.max(1, bounds.height),
    )
    model.scale.set(baseScale * Number(settings.live2d_scale || 1))
    if (electronRuntime) {
      const virtual = desktopState.bounds || { x: 0, y: 0 }
      // 换屏/改分辨率后，保存的位置可能超出当前虚拟屏幕（例如 2K 屏右下角
      // 换到 1K 屏）。把位置限制在当前虚拟屏幕内，保证桌宠主体始终可见。
      let posX = Number(settings.position_x ?? 80)
      let posY = Number(settings.position_y ?? 420)
      const vw = Number(virtual.width) || window.innerWidth
      const vh = Number(virtual.height) || window.innerHeight
      const marginX = Math.min(72, Math.max(32, vw * 0.08))
      const marginY = Math.min(96, Math.max(48, vh * 0.1))
      posX = Math.min(Math.max(posX, virtual.x + marginX), virtual.x + vw - marginX)
      posY = Math.min(Math.max(posY, virtual.y + marginY), virtual.y + vh - marginY)
      model.x = posX - Number(virtual.x || 0) + target.width / 2
      model.y = posY - Number(virtual.y || 0) + target.height / 2
        + target.height * Number(settings.live2d_vertical_offset || 0)
    } else {
      model.x = window.innerWidth / 2
      model.y = window.innerHeight * (0.5 + Number(settings.live2d_vertical_offset || 0))
    }
    if (settings.live2d_keep_visible) clampModelToViewport()
    updateSpeechBubblePosition()
    updateLockControlAnchor()
  }

  function clampModelToViewport() {
    if (!model) return
    const margin = 8
    const minimumVisibleWidth = Math.min(72, Math.max(32, window.innerWidth * 0.08))
    const minimumVisibleHeight = Math.min(96, Math.max(48, window.innerHeight * 0.1))
    const bounds = model.getBounds()
    let dx = 0
    let dy = 0
    if (bounds.x + bounds.width < margin + minimumVisibleWidth) {
      dx = margin + minimumVisibleWidth - bounds.x - bounds.width
    } else if (bounds.x > window.innerWidth - margin - minimumVisibleWidth) {
      dx = window.innerWidth - margin - minimumVisibleWidth - bounds.x
    }
    if (bounds.y + bounds.height < margin + minimumVisibleHeight) {
      dy = margin + minimumVisibleHeight - bounds.y - bounds.height
    } else if (bounds.y > window.innerHeight - margin - minimumVisibleHeight) {
      dy = window.innerHeight - margin - minimumVisibleHeight - bounds.y
    }
    model.x += dx
    model.y += dy
  }

  function setParameter(id, value, weight = 1) {
    try {
      model?.internalModel?.coreModel?.setParameterValueById(id, value, weight)
    } catch (_) {}
  }

  function addParameter(id, value, weight = 1) {
    try {
      model?.internalModel?.coreModel?.addParameterValueById(id, value, weight)
    } catch (_) {}
  }

  function percentile(values, ratio) {
    if (!values.length) return 0
    const sorted = [...values].sort((left, right) => left - right)
    return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * ratio) - 1))]
  }

  function visualPerformanceSnapshot() {
    return {
      sampleCount: visualFrameTimes.length,
      minFps: visualFpsSamples.length ? Number(Math.min(...visualFpsSamples).toFixed(1)) : 0,
      frameTimeP95Ms: Number(percentile(visualFrameTimes, 0.95).toFixed(2)),
      framesOver33Ms: visualFrameTimes.filter((value) => value > 33).length,
      framesOver50Ms: visualFrameTimes.filter((value) => value > 50).length,
    }
  }

  function rendererDiagnostics() {
    const renderer = app?.renderer
    const gl = renderer?.gl
    let graphicsRenderer = ''
    let graphicsVendor = ''
    try {
      const debugInfo = gl?.getExtension?.('WEBGL_debug_renderer_info')
      if (debugInfo) {
        graphicsRenderer = String(gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) || '')
        graphicsVendor = String(gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) || '')
      }
    } catch (_) {}
    return {
      type: Number(renderer?.type || 0),
      resolution: Number(renderer?.resolution || 0),
      width: Number(renderer?.width || 0),
      height: Number(renderer?.height || 0),
      graphicsRenderer,
      graphicsVendor,
    }
  }

  function startVisualPerformanceWindow(durationMs) {
    const now = performance.now()
    const wasActive = now < visualPerformanceUntil
    visualPerformanceUntil = Math.max(visualPerformanceUntil, now + Math.max(1000, Number(durationMs || 0)))
    if (!wasActive) {
      visualFrameWindowStartedAt = now
      visualFrameWindowCount = 0
    }
  }

  function startProceduralMotion(hint, importance = 0.5) {
    if (!model?.internalModel?.coreModel) return false
    const now = Date.now()
    const normalizedHint = String(hint || 'observe')
    if (
      now - lastMotionPlayedAt < 1200
      || (lastMotionGroup === `procedural:${normalizedHint}` && now - lastMotionPlayedAt < 3500)
    ) return false
    const duration = {
      celebrate: 2400,
      concern: 2100,
      alert: 1500,
      attention: 1700,
      observe: 2000,
      thinking: 2200,
      listening: 1800,
      shy: 2200,
    }[normalizedHint] || 1800
    proceduralMotionHint = normalizedHint
    proceduralMotionStartedAt = now
    proceduralMotionUntil = now + duration
    proceduralMotionIntensity = Math.max(0.35, Math.min(1, Number(importance || 0.5)))
    proceduralMotionStartCount += 1
    proceduralMotionPeak = 0
    lastMotionGroup = `procedural:${normalizedHint}`
    lastMotionPlayedAt = now
    startVisualPerformanceWindow(duration + 300)
    return true
  }

  function updateProceduralMotion() {
    const now = Date.now()
    if (!proceduralMotionHint || now >= proceduralMotionUntil) {
      proceduralMotionHint = ''
      proceduralMotionUntil = 0
      proceduralMotionIntensity = 0
      return
    }
    const duration = Math.max(1, proceduralMotionUntil - proceduralMotionStartedAt)
    const progress = Math.max(0, Math.min(1, (now - proceduralMotionStartedAt) / duration))
    const envelope = Math.sin(Math.PI * progress) * proceduralMotionIntensity
    const wave = Math.sin(progress * Math.PI * 2)
    const fastWave = Math.sin(progress * Math.PI * 4)
    let angleX = 0
    let angleY = 0
    let angleZ = 0
    let bodyX = 0
    let bodyY = 0
    let bodyZ = 0
    if (proceduralMotionHint === 'celebrate') {
      angleY = -7 * envelope
      angleZ = 5 * wave * envelope
      bodyY = 4 * Math.abs(fastWave) * envelope
    } else if (proceduralMotionHint === 'concern') {
      angleX = -4 * envelope
      angleY = 5 * envelope
      angleZ = -5 * envelope
      bodyX = -2.5 * envelope
    } else if (proceduralMotionHint === 'alert') {
      angleY = -8 * envelope
      bodyY = 4 * envelope
    } else if (proceduralMotionHint === 'attention' || proceduralMotionHint === 'listening') {
      angleX = 7 * wave * envelope
      angleY = -3 * envelope
      bodyX = 2 * wave * envelope
    } else if (proceduralMotionHint === 'thinking') {
      angleX = 5 * envelope
      angleZ = -6 * envelope
      bodyX = 2 * envelope
    } else if (proceduralMotionHint === 'shy') {
      angleX = -5 * envelope
      angleY = 4 * envelope
      angleZ = -6 * envelope
      bodyZ = -2 * envelope
    } else {
      angleX = 5 * wave * envelope
      angleY = -2 * envelope
      bodyX = 1.8 * wave * envelope
    }
    proceduralMotionPeak = Math.max(
      proceduralMotionPeak,
      Math.abs(angleX), Math.abs(angleY), Math.abs(angleZ),
      Math.abs(bodyX), Math.abs(bodyY), Math.abs(bodyZ),
    )
    addParameter('ParamAngleX', angleX, 0.85)
    addParameter('ParamAngleY', angleY, 0.85)
    addParameter('ParamAngleZ', angleZ, 0.7)
    addParameter('ParamBodyAngleX', bodyX, 0.75)
    addParameter('ParamBodyAngleY', bodyY, 0.75)
    addParameter('ParamBodyAngleZ', bodyZ, 0.75)
  }

  function updateLockControlAnchor() {
    if (!electronRuntime || !model || !model.visible) return
    const bounds = model.getBounds()
    window.mioDesktop.setLockControlAnchor({
      x: bounds.x,
      y: bounds.y,
      width: bounds.width,
      height: bounds.height,
    })
  }

  function emotionParameters(emotion) {
    return {
      neutral: { mouth: 0, smile: 0, brow: 0, cheek: 0, angle: 0 },
      gentle: { mouth: 0.35, smile: 0.45, brow: 0.18, cheek: 0.12, angle: -2 },
      cheerful: { mouth: 0.82, smile: 0.95, brow: 0.35, cheek: 0.45, angle: 4 },
      concerned: { mouth: -0.45, smile: 0, brow: -0.38, cheek: 0, angle: -5 },
      serious: { mouth: -0.25, smile: 0, brow: -0.2, cheek: 0, angle: 1 },
      shy: { mouth: 0.18, smile: 0.25, brow: -0.08, cheek: 0.85, angle: -7 },
    }[emotion] || { mouth: 0, smile: 0, brow: 0, cheek: 0, angle: 0 }
  }

  function updateModelPerformance() {
    if (!model?.internalModel?.coreModel) return
    const measuredAt = performance.now()
    const frameTime = Math.max(0, measuredAt - rendererLastFrameAt)
    rendererLastFrameAt = measuredAt
    rendererUpdateCount += 1
    if (voiceLifecycleActive && frameTime > 0) {
      playbackFrameTimes.push(frameTime)
      if (playbackFrameTimes.length > 2400) playbackFrameTimes.shift()
      playbackFrameWindowCount += 1
      const playbackWindowMs = measuredAt - playbackFrameWindowStartedAt
      if (playbackWindowMs >= 500) {
        playbackFpsSamples.push(playbackFrameWindowCount * 1000 / playbackWindowMs)
        playbackFrameWindowStartedAt = measuredAt
        playbackFrameWindowCount = 0
      }
    }
    if (measuredAt < visualPerformanceUntil && frameTime > 0) {
      visualFrameTimes.push(frameTime)
      if (visualFrameTimes.length > 4800) visualFrameTimes.shift()
      visualFrameWindowCount += 1
      const visualWindowMs = measuredAt - visualFrameWindowStartedAt
      if (visualWindowMs >= 500) {
        visualFpsSamples.push(visualFrameWindowCount * 1000 / visualWindowMs)
        if (visualFpsSamples.length > 1200) visualFpsSamples.shift()
        visualFrameWindowStartedAt = measuredAt
        visualFrameWindowCount = 0
      }
    }
    const measuredWindow = measuredAt - rendererUpdateWindowStartedAt
    if (measuredWindow >= 1000) {
      rendererMeasuredFps = rendererUpdateCount * 1000 / measuredWindow
      rendererUpdateCount = 0
      rendererUpdateWindowStartedAt = measuredAt
    }
    if (activityUntil && Date.now() >= activityUntil && currentActivity !== 'speaking') {
      currentActivity = 'idle'
      activityUntil = 0
      activityIndicator.className = 'activity-indicator idle'
      activityIndicator.classList.remove('active')
    }
    if (Date.now() > emotionUntil && currentActivity === 'idle') currentEmotion = 'neutral'
    const emotion = emotionParameters(currentEmotion)
    setParameter('ParamMouthForm', emotion.mouth, 0.38)
    setParameter('ParamEyeLSmile', emotion.smile, 0.45)
    setParameter('ParamEyeRSmile', emotion.smile, 0.45)
    setParameter('ParamBrowLY', emotion.brow, 0.35)
    setParameter('ParamBrowRY', emotion.brow, 0.35)
    setParameter('ParamCheek', emotion.cheek, 0.5)
    setParameter('ParamAngleZ', emotion.angle, 0.15)
    updateProceduralMotion()

    let nextMouth = 0
    if (analyser && analyserData && ((currentAudio && !currentAudio.paused) || streamPlaybackActive)) {
      analyser.getByteTimeDomainData(analyserData)
      let sum = 0
      for (const value of analyserData) {
        const normalized = (value - 128) / 128
        sum += normalized * normalized
      }
      nextMouth = Math.min(1, Math.sqrt(sum / analyserData.length) * 5.2)
    } else if (Date.now() < speakingUntil || currentActivity === 'speaking') {
      nextMouth = 0.12 + Math.abs(Math.sin(Date.now() / 95)) * 0.42
    }
    mouthLevel += (nextMouth - mouthLevel) * (nextMouth > mouthLevel ? 0.36 : 0.16)
    maxMouthLevel = Math.max(maxMouthLevel, mouthLevel)
    setParameter('ParamMouthOpenY', mouthLevel)
  }

  function playMotion(group, index, { force = false } = {}) {
    if (!model || !group) return false
    const now = Date.now()
    const normalizedGroup = String(group)
    if (!force && (now - lastMotionPlayedAt < 1200 || (normalizedGroup === lastMotionGroup && now - lastMotionPlayedAt < 3500))) return false
    try {
      model.motion(group, index, 2)
      lastMotionGroup = normalizedGroup
      lastMotionPlayedAt = now
      return true
    } catch (error) {
      console.debug('[MioLive2D] motion unavailable', group, error)
      return false
    }
  }

  function availableMotionNames() {
    const motions = currentCapabilityPayload().motions
    return motions.map((entry) => String(entry?.name || '')).filter(Boolean)
  }

  function semanticMotion(hint) {
    const names = availableMotionNames()
    const slots = currentCapabilityPayload().motionSlots || {}
    const slotName = {
      celebrate: 'cheerful',
      concern: 'concerned',
      alert: 'alert',
      attention: 'attention',
      observe: 'observe',
      thinking: 'think',
      speaking: 'speak',
      listening: 'attention',
      touch: 'touch',
      shy: 'shy',
      idle: 'idle',
    }[hint] || hint
    const configured = settings.live2d_motion_slots?.[currentModelId]?.[slotName]
    if (configured && names.includes(String(configured))) return String(configured)
    if (slots[slotName] && names.includes(String(slots[slotName]))) return String(slots[slotName])
    const patterns = {
      celebrate: [/happy|joy|cheer|win|victory|success/i, /tap|touch/i],
      concern: [/sad|worry|concern|down|lose|defeat/i, /idle|wait/i],
      alert: [/alert|angry|serious|surprise|shock/i, /tap|touch/i],
      attention: [/look|attention|curious|question/i, /idle|wait/i],
      observe: [/look|watch|observe|idle|wait/i],
      thinking: [/think|question|ponder/i, /idle|wait/i],
      speaking: [/speak|talk|voice|mouth/i, /idle|wait/i],
      listening: [/attention|listen|curious|look/i, /idle|wait/i],
      touch: [/tap|touch|click|body/i],
      shy: [/shy|blush|embarrass/i, /idle|wait/i],
      idle: [/idle|wait|stand/i],
    }[hint] || [/idle|wait/i]
    return names.find((name) => patterns.some((pattern) => pattern.test(name)))
      || modelDefinition(currentModelId)?.idleGroup
      || names[0]
      || ''
  }

  function expressionForEmotion(emotion) {
    const configured = settings.live2d_expression_slots?.[currentModelId]?.[emotion]
    return window.MioLive2DExpression.selectExpression(
      currentCapabilityPayload().expressions,
      emotion,
      configured,
    )
  }

  function applyModelExpression(emotion) {
    const expression = expressionForEmotion(emotion)
    if (!expression || !model?.expression) return false
    try {
      Promise.resolve(model.expression(expression)).catch(() => {})
      return true
    } catch (_) {
      return false
    }
  }

  function applyEmotion(emotion, durationMs = 10000) {
    const nextEmotion = String(emotion || 'neutral')
    const expressionChanged = nextEmotion !== lastAppliedExpressionEmotion
    currentEmotion = nextEmotion
    emotionUntil = Date.now() + Math.max(2000, Number(durationMs || 10000))
    if (expressionChanged && applyModelExpression(currentEmotion)) {
      lastAppliedExpressionEmotion = currentEmotion
    }
  }

  function handleVisualEvent(event) {
    if (!event) return
    lastVisualEvent = { ...event }
    const emotion = String(event.emotion || 'gentle')
    const importance = Math.max(0, Math.min(1, Number(event.importance || 0)))
    currentActivity = 'observing'
    const visualDuration = Math.max(2500, 2500 + importance * 4500)
    activityUntil = Date.now() + visualDuration
    startVisualPerformanceWindow(visualDuration)
    applyEmotion(emotion, Math.max(4000, 4500 + importance * 5500))
    const motionHint = String(event.motion_hint || 'observe')
    const motionGroup = semanticMotion(motionHint)
    if (motionGroup) playMotion(motionGroup)
    else startProceduralMotion(motionHint, importance)
  }

  function handleForegroundChanged(event) {
    const title = String(event?.title || '').trim()
    lastForegroundTitle = title
    if (!title || ['speaking', 'responding'].includes(currentActivity)) return
    const now = Date.now()
    if (title === lastHandledForegroundTitle && now - lastForegroundHandledAt < 5000) return
    lastHandledForegroundTitle = title
    lastForegroundHandledAt = now
    currentActivity = 'observing'
    activityUntil = Date.now() + 2200
    startVisualPerformanceWindow(2200)
    if (!availableMotionNames().length) startProceduralMotion('attention', 0.35)
  }

  function applyActivity(activity) {
    if (!activity) return
    const revision = Number(activity.revision || 0)
    if (revision && revision === activityRevision) return
    if (revision) activityRevision = revision
    currentActivity = String(activity.state || 'idle')
    activityUntil = currentActivity === 'idle'
      ? 0
      : Date.now() + Math.max(500, Number(activity.remaining_ms || 5000))
    activityIndicator.className = `activity-indicator ${currentActivity}`
    activityIndicator.classList.toggle('active', currentActivity !== 'idle')
    activityIndicator.title = String(activity.label || 'Mio 当前状态')
    applyEmotion(activity.emotion || 'neutral', Number(activity.remaining_ms || 6000))
  }

  async function applySettings(nextSettings) {
    const previousModel = selectedModelId()
    settings = { ...settings, ...(nextSettings || {}) }
    if (electronRuntime) {
      window.mioDesktop.setAlwaysOnTop(Boolean(settings.live2d_always_on_top ?? true))
      window.mioDesktop.setClickThroughLocked(Boolean(settings.live2d_click_through_locked))
    }
    root.classList.toggle('click-through-locked', Boolean(settings.live2d_click_through_locked))
    updateContextMenuLabels()
    if (!settings.live2d_speech_bubble_enabled) hideSpeechBubble()
    const nextModel = selectedModelId()
    if (!model || previousModel !== nextModel || currentModelId !== nextModel) {
      await loadModel(nextModel)
    } else {
      fitModel()
    }
    const percent = Number(settings.pet_size_percent || 150)
    sizeSlider.value = String(percent)
    sizeValue.value = `${percent}%`
    if (percent !== lastSizePercent && !electronRuntime && window.pywebview?.api) {
      lastSizePercent = percent
      try { await window.pywebview.api.resize_window(percent) } catch (_) {}
    }
    populateModelSelect()
  }

  async function pollStatus() {
    try {
      const status = await request('/api/companion/status')
      const pet = status.pet || {}
      await applySettings(pet.settings || {})
      applyActivity(pet.activity || {})
      lastStatusSuccess = Date.now()
    } catch (error) {
      if (Date.now() - lastStatusSuccess > 25000 && !electronRuntime && window.pywebview?.api) {
        try { await window.pywebview.api.close_pet() } catch (_) {}
      }
    }
  }

  function shouldSpeak(message) {
    if (typeof message.should_speak === 'boolean') return message.should_speak
    const source = String(message.source || '')
    if (!settings.voice_enabled) return false
    if (['proactive', 'desktop_proactive'].includes(source)) return Boolean(settings.speak_proactive)
    if (source === 'screen') return Boolean(settings.speak_screen_observations)
    if (['game', 'desktop_pet_wake'].includes(source)) return Boolean(settings.speak_game_observations)
    return true
  }

  function messagePriority(message) {
    const explicit = Number(message?.priority)
    if (Number.isFinite(explicit)) return Math.max(0, Math.min(100, explicit))
    const source = String(message?.source || '').toLowerCase()
    if (source === 'desktop_pet') return 100
    if (['desktop', 'web', 'qq'].includes(source)) return 95
    if (source.startsWith('qq_group')) return 90
    if (source === 'startup') return 70
    if (source === 'desktop_pet_wake') return 68
    if (source === 'game') return 62
    if (source === 'screen') return 56
    if (['proactive', 'desktop_proactive'].includes(source)) return 35
    return 75
  }

  function messageResponseId(message) {
    return String(
      message?.response_id
      || message?.request_id
      || (message?.message_id ? `message-${message.message_id}` : ''),
    )
  }

  function recordSpeechLifecycle(responseId, event, details = {}) {
    const id = String(responseId || '')
    if (!id) return
    let entry = speechLifecycleHistory.find((item) => item.responseId === id)
    if (!entry) {
      entry = { responseId: id, status: 'requested', events: [] }
      speechLifecycleHistory.push(entry)
      if (speechLifecycleHistory.length > 80) speechLifecycleHistory.shift()
    }
    const at = new Date().toISOString()
    entry.status = event
    entry.events.push({ event, at })
    if (entry.events.length > 12) entry.events.shift()
    if (event === 'requested') entry.requestedAt = at
    if (event === 'queued') entry.queuedAt = at
    if (event === 'preparing') entry.preparingAt = at
    if (event === 'started') entry.startedAt = at
    if (['finished', 'failed', 'interrupted', 'dropped'].includes(event)) entry.endedAt = at
    if (details.mode) entry.mode = details.mode
    if (details.priority != null) entry.priority = Number(details.priority)
    if (details.firstAudioLatencyMs != null) entry.firstAudioLatencyMs = Number(details.firstAudioLatencyMs)
    if (details.error) entry.error = String(details.error)
    if (details.playbackMetrics) entry.playbackMetrics = details.playbackMetrics
  }

  function trackSpeechRequest(message) {
    if (message?.__speechRequestTracked) return message
    const existingId = messageResponseId(message)
    const responseId = existingId || `speech-${Date.now()}-${speechRequestCount + 1}`
    if (!speechLifecycleHistory.some((item) => item.responseId === responseId)) {
      speechRequestCount += 1
      recordSpeechLifecycle(responseId, 'requested', { priority: messagePriority(message) })
    }
    return { ...message, response_id: responseId, __speechRequestTracked: true }
  }

  function resetPlaybackPerformance() {
    playbackFrameTimes = []
    playbackFpsSamples = []
    playbackFrameWindowStartedAt = performance.now()
    playbackFrameWindowCount = 0
    rendererLastFrameAt = playbackFrameWindowStartedAt
  }

  function finalizePlaybackPerformance() {
    const measuredAt = performance.now()
    const partialWindowMs = measuredAt - playbackFrameWindowStartedAt
    if (playbackFrameWindowCount > 0 && partialWindowMs >= 250) {
      playbackFpsSamples.push(playbackFrameWindowCount * 1000 / partialWindowMs)
    }
    const sorted = [...playbackFrameTimes].sort((left, right) => left - right)
    const p95Index = sorted.length ? Math.min(sorted.length - 1, Math.ceil(sorted.length * 0.95) - 1) : -1
    return {
      frameCount: playbackFrameTimes.length,
      minimumFps: playbackFpsSamples.length
        ? Number(Math.min(...playbackFpsSamples).toFixed(1))
        : null,
      frameTimeP95Ms: p95Index >= 0 ? Number(sorted[p95Index].toFixed(2)) : null,
      maximumFrameTimeMs: sorted.length ? Number(sorted[sorted.length - 1].toFixed(2)) : null,
      framesOver33Ms: playbackFrameTimes.filter((value) => value > 33).length,
      framesOver50Ms: playbackFrameTimes.filter((value) => value > 50).length,
      frameTimesMs: playbackFrameTimes.map((value) => Number(value.toFixed(3))),
    }
  }

  function speechInProgress() {
    return Boolean(currentSpeechAbort || voiceLifecycleActive || currentAudio || streamPlaybackActive)
  }

  function enqueueSpeech(message) {
    message = trackSpeechRequest(message)
    const responseId = messageResponseId(message)
    if (responseId && (responseId === activeResponseId || speechQueue.some((item) => messageResponseId(item) === responseId))) {
      return false
    }
    speechQueue.push({ ...message, priority: messagePriority(message), queued_at: Date.now() })
    speechQueuedCount += 1
    recordSpeechLifecycle(responseId, 'queued', { priority: messagePriority(message) })
    speechQueue.sort((left, right) => Number(right.priority) - Number(left.priority) || left.queued_at - right.queued_at)
    if (speechQueue.length > 20) {
      const dropped = speechQueue.slice(20)
      speechDroppedCount += dropped.length
      for (const item of dropped) recordSpeechLifecycle(messageResponseId(item), 'dropped', { error: 'queue-limit' })
      speechQueue = speechQueue.slice(0, 20)
    }
    return true
  }

  function scheduleSpeechDrain() {
    window.clearTimeout(speechDrainTimer)
    speechDrainTimer = window.setTimeout(() => {
      if (speechInProgress() || !speechQueue.length) return
      const next = speechQueue.shift()
      dispatchSpeech(next)
    }, 30)
  }

  async function dispatchSpeech(message) {
    const text = String(message?.text || message?.content || '').trim()
    if (!text || !shouldSpeak(message || {})) return false
    message = trackSpeechRequest(message)
    const priority = messagePriority(message)
    const responseId = messageResponseId(message)
    if (responseId && responseId === activeResponseId) return false
    if (speechInProgress()) {
      // A normal follow-up at the same priority belongs to the same flow and
      // waits its turn. Only a genuinely higher-priority event may preempt it.
      if (priority <= activeSpeechPriority) return enqueueSpeech({ ...message, text, priority, response_id: responseId })
      stopSpeech('interrupted')
    }
    showSpeechBubble(text, 0, responseId)
    activeResponseId = responseId
    activeSpeechPriority = priority
    recordSpeechLifecycle(responseId, 'preparing', { priority })
    await playSpeech({ ...message, text, priority, response_id: responseId })
    return true
  }

  function clearStreamingAudio() {
    for (const source of streamSources) {
      source.onended = null
      try { source.stop() } catch (_) {}
      try { source.disconnect() } catch (_) {}
    }
    streamSources.clear()
    streamPlaybackActive = false
    streamScheduledUntil = 0
    streamPendingBuffers = []
    streamPendingDuration = 0
    streamHasStarted = false
    if (streamGain) {
      try { streamGain.disconnect() } catch (_) {}
      streamGain = null
    }
  }

  function stopSpeech(reason = 'interrupted') {
    const hadSpeech = voiceLifecycleActive
    const endedResponseId = activeResponseId
    const endedPriority = activeSpeechPriority
    const playbackMetrics = hadSpeech ? finalizePlaybackPerformance() : null
    streamSpeechToken += 1
    window.clearTimeout(streamFirstAudioTimer)
    streamFirstAudioTimer = null
    if (currentSpeechAbort) currentSpeechAbort.abort()
    currentSpeechAbort = null
    if (currentAudio) {
      const audio = currentAudio
      currentAudio = null
      audio.onended = null
      audio.onerror = null
      audio.pause()
      audio.removeAttribute('src')
    }
    clearStreamingAudio()
    if (analyser) {
      try { analyser.disconnect() } catch (_) {}
    }
    analyser = null
    analyserData = null
    mouthLevel = 0
    if (currentAudioUrl) URL.revokeObjectURL(currentAudioUrl)
    currentAudioUrl = ''
    activeResponseId = ''
    activeSpeechPriority = -1
    activeSpeechText = ''
    if (['finished', 'error'].includes(reason) && !speechBubble.hidden) {
      window.clearTimeout(speechBubbleHideTimer)
      speechBubbleHideTimer = window.setTimeout(
        hideSpeechBubble,
        Math.max(1000, Math.min(30000, Number(settings.bubble_seconds || 9) * 1000)),
      )
    } else {
      hideSpeechBubble()
    }
    if (reason && hadSpeech) {
      voiceLifecycleActive = false
      speechEndCount += 1
      if (reason === 'interrupted') speechInterruptCount += 1
      if (reason === 'finished') speechFinishedCount += 1
      if (reason === 'error') speechFailureCount += 1
      lastSpeechEndedAt = new Date().toISOString()
      lastVoiceEventSent = sendSocket('voice_ended', {
        reason,
        request_id: endedResponseId,
        response_id: endedResponseId,
        priority: endedPriority,
      })
      recordSpeechLifecycle(
        endedResponseId,
        reason === 'error' ? 'failed' : reason,
        { error: reason === 'error' ? lastSpeechError : '', playbackMetrics },
      )
    } else if (reason === 'error' && endedResponseId) {
      speechFailureCount += 1
      lastSpeechEndedAt = new Date().toISOString()
      recordSpeechLifecycle(endedResponseId, 'failed', { error: lastSpeechError })
    }
    if (reason === 'manual') {
      const dropped = speechQueue
      speechDroppedCount += dropped.length
      for (const item of dropped) recordSpeechLifecycle(messageResponseId(item), 'dropped', { error: 'manual-stop' })
      speechQueue = []
    }
    // Interrupting the active item must not discard follow-up replies. Drain
    // the retained queue once the current audio resources are released.
    if (reason && reason !== 'manual') scheduleSpeechDrain()
  }

  function beginVoiceLifecycle(message, text, mode, durationMs = 0) {
    if (voiceLifecycleActive) return
    voiceLifecycleActive = true
    currentActivity = 'speaking'
    speakingUntil = Date.now() + Math.max(3000, text.length * 190)
    speechStartCount += 1
    lastSpeechStartedAt = new Date().toISOString()
    lastStreamMode = mode
    activeSpeechText = text
    resetPlaybackPerformance()
    recordSpeechLifecycle(messageResponseId(message), 'started', {
      mode,
      priority: messagePriority(message),
      firstAudioLatencyMs: streamFirstAudioLatencyMs,
    })
    showSpeechBubble(text, durationMs, messageResponseId(message))
    lastVoiceEventSent = sendSocket('voice_started', {
      message_id: message.message_id || null,
      request_id: message.request_id || '',
      response_id: messageResponseId(message),
      priority: messagePriority(message),
      emotion: currentEmotion,
      mode,
      first_audio_latency_ms: streamFirstAudioLatencyMs,
    })
  }

  function appendBytes(left, right) {
    if (!left?.length) return new Uint8Array(right)
    const combined = new Uint8Array(left.length + right.length)
    combined.set(left, 0)
    combined.set(right, left.length)
    return combined
  }

  function asciiAt(bytes, offset, length) {
    let value = ''
    for (let index = 0; index < length; index += 1) value += String.fromCharCode(bytes[offset + index])
    return value
  }

  function parseWavHeader(bytes) {
    if (bytes.length < 12) return null
    if (asciiAt(bytes, 0, 4) !== 'RIFF' || asciiAt(bytes, 8, 4) !== 'WAVE') {
      throw new Error('流式语音不是有效的 WAV 数据')
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
    let position = 12
    let format = null
    while (position + 8 <= bytes.length) {
      const chunkId = asciiAt(bytes, position, 4)
      const chunkSize = view.getUint32(position + 4, true)
      const chunkStart = position + 8
      if (chunkId === 'fmt ') {
        if (bytes.length < chunkStart + Math.min(chunkSize, 16)) return null
        format = {
          tag: view.getUint16(chunkStart, true),
          channels: view.getUint16(chunkStart + 2, true),
          sampleRate: view.getUint32(chunkStart + 4, true),
          blockAlign: view.getUint16(chunkStart + 12, true),
          bitsPerSample: view.getUint16(chunkStart + 14, true),
        }
      } else if (chunkId === 'data') {
        if (!format) throw new Error('流式 WAV 缺少音频格式')
        return { format, dataOffset: chunkStart }
      }
      const next = chunkStart + chunkSize + (chunkSize % 2)
      if (next > bytes.length) return null
      position = next
    }
    return null
  }

  function sampleFromPcm(view, offset, format) {
    if (format.tag === 3 && format.bitsPerSample === 32) return view.getFloat32(offset, true)
    if (format.tag !== 1) throw new Error(`不支持的 WAV 编码：${format.tag}`)
    if (format.bitsPerSample === 8) return (view.getUint8(offset) - 128) / 128
    if (format.bitsPerSample === 16) return view.getInt16(offset, true) / 32768
    if (format.bitsPerSample === 24) {
      let value = view.getUint8(offset) | (view.getUint8(offset + 1) << 8) | (view.getUint8(offset + 2) << 16)
      if (value & 0x800000) value |= 0xff000000
      return value / 8388608
    }
    if (format.bitsPerSample === 32) return view.getInt32(offset, true) / 2147483648
    throw new Error(`不支持的 WAV 位深：${format.bitsPerSample}`)
  }

  function audioBufferFromPcm(bytes, format) {
    const bytesPerSample = format.bitsPerSample / 8
    const frameCount = Math.floor(bytes.length / format.blockAlign)
    if (!frameCount || !Number.isFinite(bytesPerSample)) return null
    const output = audioContext.createBuffer(format.channels, frameCount, format.sampleRate)
    const view = new DataView(bytes.buffer, bytes.byteOffset, frameCount * format.blockAlign)
    for (let channel = 0; channel < format.channels; channel += 1) {
      const channelData = output.getChannelData(channel)
      for (let frame = 0; frame < frameCount; frame += 1) {
        const offset = frame * format.blockAlign + channel * bytesPerSample
        channelData[frame] = Math.max(-1, Math.min(1, sampleFromPcm(view, offset, format)))
      }
    }
    return output
  }

  function scheduleAudioBuffer(buffer, message, text, token, requestStartedAt) {
    if (!buffer || token !== streamSpeechToken) return false
    const source = audioContext.createBufferSource()
    source.buffer = buffer
    source.connect(streamGain)
    const startsAt = Math.max(audioContext.currentTime + 0.035, streamScheduledUntil)
    streamScheduledUntil = startsAt + buffer.duration
    streamSources.add(source)
    source.onended = () => streamSources.delete(source)
    source.start(startsAt)
    if (!streamPlaybackActive) {
      window.clearTimeout(streamFirstAudioTimer)
      streamFirstAudioTimer = null
      streamPlaybackActive = true
      streamFirstAudioLatencyMs = Math.round(performance.now() - requestStartedAt)
      beginVoiceLifecycle(message, text, 'stream')
    }
    return true
  }

  function startPendingPcm(message, text, token, requestStartedAt) {
    if (streamHasStarted || !streamPendingBuffers.length || token !== streamSpeechToken) return false
    streamHasStarted = true
    const pending = streamPendingBuffers
    streamPendingBuffers = []
    streamPendingDuration = 0
    for (const buffer of pending) scheduleAudioBuffer(buffer, message, text, token, requestStartedAt)
    return streamPlaybackActive
  }

  function schedulePcm(bytes, format, message, text, token, requestStartedAt, forceStart = false) {
    if (!bytes.length || token !== streamSpeechToken) return false
    const buffer = audioBufferFromPcm(bytes, format)
    if (!buffer) return false
    if (!streamHasStarted) {
      streamPendingBuffers.push(buffer)
      streamPendingDuration += buffer.duration
      if (forceStart || streamPendingDuration >= 0.28) startPendingPcm(message, text, token, requestStartedAt)
      return true
    }
    return scheduleAudioBuffer(buffer, message, text, token, requestStartedAt)
  }

  async function playStreamingResponse(response, controller, message, text, token, requestStartedAt) {
    if (!response.body) throw new Error('浏览器没有收到可读取的流式音频')
    audioContext = audioContext || new AudioContext()
    if (audioContext.state === 'suspended') await audioContext.resume()
    analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    analyser.smoothingTimeConstant = 0.42
    analyserData = new Uint8Array(analyser.fftSize)
    streamGain = audioContext.createGain()
    streamGain.gain.value = Math.max(0, Math.min(1, Number(settings.voice_volume || 85) / 100))
    streamGain.connect(analyser)
    analyser.connect(audioContext.destination)

    const reader = response.body.getReader()
    let pending = new Uint8Array(0)
    let format = null
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (controller.signal.aborted || token !== streamSpeechToken) return false
        if (value?.length) pending = appendBytes(pending, value)
        if (!format) {
          const parsed = parseWavHeader(pending)
          if (parsed) {
            format = parsed.format
            if (!format.channels || !format.sampleRate || !format.blockAlign) {
              throw new Error('流式 WAV 的格式参数无效')
            }
            pending = pending.slice(parsed.dataOffset)
          }
        }
        if (format) {
          const aligned = pending.length - (pending.length % format.blockAlign)
          const minimum = done ? format.blockAlign : Math.max(format.blockAlign, Math.floor(format.sampleRate * 0.16) * format.blockAlign)
          if (aligned >= minimum) {
            schedulePcm(pending.slice(0, aligned), format, message, text, token, requestStartedAt)
            pending = pending.slice(aligned)
          }
        }
        if (done) break
      }
      if (format && pending.length >= format.blockAlign) {
        const aligned = pending.length - (pending.length % format.blockAlign)
        schedulePcm(pending.slice(0, aligned), format, message, text, token, requestStartedAt, true)
      }
      startPendingPcm(message, text, token, requestStartedAt)
      if (!streamPlaybackActive) throw new Error('流式响应没有可播放的 PCM 音频')
      while (!controller.signal.aborted && token === streamSpeechToken && audioContext.currentTime < streamScheduledUntil - 0.015) {
        await new Promise((resolve) => window.setTimeout(resolve, 20))
      }
      return !controller.signal.aborted && token === streamSpeechToken
    } finally {
      try { reader.releaseLock() } catch (_) {}
    }
  }

  async function playCompleteResponse(response, controller, message, text, token, mode = 'complete', requestStartedAt = 0) {
    const blob = await response.blob()
    if (controller.signal.aborted || token !== streamSpeechToken) return
    currentAudioUrl = URL.createObjectURL(blob)
    currentAudio = new Audio(currentAudioUrl)
    currentAudio.volume = Math.max(0, Math.min(1, Number(settings.voice_volume || 85) / 100))
    audioContext = audioContext || new AudioContext()
    if (audioContext.state === 'suspended') await audioContext.resume()
    const source = audioContext.createMediaElementSource(currentAudio)
    analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    analyser.smoothingTimeConstant = 0.42
    analyserData = new Uint8Array(analyser.fftSize)
    source.connect(analyser)
    analyser.connect(audioContext.destination)
    currentAudio.onended = () => {
      if (token !== streamSpeechToken) return
      currentActivity = 'idle'
      stopSpeech('finished')
    }
    currentAudio.onerror = () => {
      if (token !== streamSpeechToken) return
      const mediaError = currentAudio?.error
      lastSpeechError = mediaError?.message || `音频错误代码 ${mediaError?.code || 0}`
      sendSocket('renderer_error', { message: `桌宠语音播放失败：${lastSpeechError}` })
      currentActivity = 'idle'
      stopSpeech('error')
    }
    await currentAudio.play()
    window.clearTimeout(streamFirstAudioTimer)
    streamFirstAudioTimer = null
    if (requestStartedAt) streamFirstAudioLatencyMs = Math.round(performance.now() - requestStartedAt)
    const durationMs = Number.isFinite(currentAudio.duration) ? currentAudio.duration * 1000 : 0
    beginVoiceLifecycle(message, text, mode, durationMs)
  }

  async function requestSpeechAudio(endpoint, text, message, controller, speechLanguage = '') {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        context: text,
        emotion: message.emotion || '',
        model_id: message.model_id || '',
        language: speechLanguage || message.speech_language || (settings.pet_speech_language === 'ja' ? 'ja' : 'zh'),
      }),
      signal: controller.signal,
    })
    if (!response.ok) {
      let detail = `HTTP ${response.status}`
      try {
        const payload = await response.json()
        detail = payload?.detail || detail
      } catch (_) {}
      throw new Error(`语音生成失败：${detail}`)
    }
    return response
  }

  async function playSpeech(message) {
    const text = String(message.text || message.content || '').trim()
    if (!text || !shouldSpeak(message)) return
    const controller = new AbortController()
    const token = ++streamSpeechToken
    currentSpeechAbort = controller
    lastSpeechError = ''
    lastStreamMode = ''
    streamFirstAudioLatencyMs = null
    maxMouthLevel = 0
    currentActivity = 'responding'
    applyEmotion(message.emotion || 'neutral', Math.max(6000, text.length * 180))
    const requestStartedAt = performance.now()
    streamFirstAudioTimer = window.setTimeout(() => {
      if (token === streamSpeechToken && !voiceLifecycleActive) controller.abort('first-audio-timeout')
    }, 12000)
    try {
      if (settings.voice_streaming_enabled !== false) {
        try {
          const streamResponse = await requestSpeechAudio('/api/companion/voice/stream', text, message, controller)
          const completed = await playStreamingResponse(streamResponse, controller, message, text, token, requestStartedAt)
          if (completed && token === streamSpeechToken) {
            currentActivity = 'idle'
            stopSpeech('finished')
          }
          return
        } catch (streamError) {
          if (token !== streamSpeechToken) return
          if (controller.signal.aborted) {
            if (controller.signal.reason === 'first-audio-timeout') throw streamError
            return
          }
          if (voiceLifecycleActive) throw streamError
          clearStreamingAudio()
          if (analyser) {
            try { analyser.disconnect() } catch (_) {}
          }
          analyser = null
          analyserData = null
          lastStreamMode = 'complete-fallback'
        }
      }
      const response = await requestSpeechAudio('/api/companion/voice/audio', text, message, controller)
      await playCompleteResponse(
        response,
        controller,
        message,
        text,
        token,
        lastStreamMode || 'complete',
        requestStartedAt,
      )
    } catch (error) {
      if (token !== streamSpeechToken) return
      const requestedLanguage = message.speech_language || settings.pet_speech_language
      currentActivity = 'idle'
      lastSpeechError = controller.signal.aborted
        ? (requestedLanguage === 'ja'
            ? '日语语音未能在 12 秒内开始，已停止；不会改说中文'
            : '语音未能在 12 秒内开始播放')
        : String(error?.message || error)
      showSpeechBubble(lastSpeechError, 0, messageResponseId(message))
      sendSocket('renderer_error', { message: lastSpeechError })
      stopSpeech('error')
    } finally {
      window.clearTimeout(streamFirstAudioTimer)
      streamFirstAudioTimer = null
      if (currentSpeechAbort === controller) currentSpeechAbort = null
    }
  }

  async function handleMessages(messages) {
    for (let index = 0; index < messages.length; index += 1) {
      const message = messages[index]
      const requestId = String(message.request_id || '')
      const chunks = [String(message.content || '').trim()].filter(Boolean)
      while (requestId && messages[index + 1] && String(messages[index + 1].request_id || '') === requestId) {
        index += 1
        const chunk = String(messages[index].content || '').trim()
        if (chunk) chunks.push(chunk)
      }
      const text = chunks.join('\n')
      if (!text) continue
      const payload = { ...message, text }
      applyEmotion(payload.emotion || 'neutral', Math.max(5000, text.length * 160))
      showSpeechBubble(text, 0, messageResponseId(payload))
      if (shouldSpeak(payload)) await dispatchSpeech(payload)
    }
  }

  async function pollFeed() {
    if (electronRuntime) return
    try {
      const query = latestMessageId === null ? '' : `?after_id=${latestMessageId}`
      const feed = await request(`/api/companion/feed${query}`)
      latestMessageId = Number(feed.latest_id || latestMessageId || 0)
      await handleMessages(Array.isArray(feed.messages) ? feed.messages : [])
    } catch (_) {}
  }

  function sendSocket(type, payload) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false
    socket.send(JSON.stringify({ type, payload: payload || {} }))
    return true
  }

  function connectSocket() {
    if (!electronRuntime) return
    window.clearTimeout(socketRetryTimer)
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    socket = new WebSocket(`${scheme}//${window.location.host}/api/companion/ws`)
    socket.onopen = () => {
      socketRetry = 0
      backendOfflineSince = 0
      window.clearTimeout(backendOfflineTimer)
      backendOfflineTimer = null
      activityIndicator.classList.remove('offline')
      activityIndicator.title = 'Mio 当前状态'
      if (speechBubbleResponseId === 'agent-offline') hideSpeechBubble()
      sendRendererReady()
      window.clearInterval(socketPingTimer)
      socketPingTimer = window.setInterval(() => sendSocket('ping', {}), 15000)
    }
    socket.onmessage = async (event) => {
      let message
      try { message = JSON.parse(event.data) } catch (_) { return }
      const payload = message?.payload || {}
      if (message.type === 'ready') {
        await applySettings(payload.settings || {})
        applyActivity(payload.activity || {})
      } else if (message.type === 'settings_changed') {
        await applySettings(payload)
      } else if (message.type === 'activity') {
        applyActivity(payload)
      } else if (message.type === 'speak') {
        const text = String(payload.text || payload.content || '').trim()
        applyEmotion(payload.emotion || 'neutral', Math.max(6000, text.length * 180))
        showSpeechBubble(text, 0, messageResponseId(payload))
        if (payload.should_speak) {
          const speakingMotion = semanticMotion('speaking')
          const idleMotion = semanticMotion('idle')
          if (speakingMotion && speakingMotion !== idleMotion) playMotion(speakingMotion)
          await dispatchSpeech(payload)
        }
      } else if (message.type === 'speech_interrupt') {
        const reason = String(payload.reason || 'interrupted')
        const targetResponseId = String(payload.response_id || '')
        if (!targetResponseId || !activeResponseId || targetResponseId === activeResponseId) {
          stopSpeech(['user_started_speaking', 'call_ended'].includes(reason) ? 'interrupted' : reason)
        }
      } else if (message.type === 'visual_event') {
        handleVisualEvent(payload)
      } else if (message.type === 'foreground_changed') {
        handleForegroundChanged(payload)
      } else if (message.type === 'chat_window_state') {
        if (!electronRuntime) window.mioDesktop?.setChatWindowOpen?.(Boolean(payload.open))
      } else if (message.type === 'motion_preview') {
        playMotion(String(payload.group || ''), Number.isInteger(payload.index) ? payload.index : undefined)
      } else if (message.type === 'expression_preview') {
        if (payload.expression && model?.expression) {
          Promise.resolve(model.expression(String(payload.expression))).catch(() => {})
        }
      } else if (message.type === 'shutdown') {
        const bridge = await waitForBridge()
        await bridge.close_pet()
      }
    }
    socket.onclose = () => {
      window.clearInterval(socketPingTimer)
      if (!backendOfflineSince) backendOfflineSince = Date.now()
      activityIndicator.classList.add('offline')
      activityIndicator.title = 'Mio 已离线，正在重连'
      if (!backendOfflineTimer) {
        backendOfflineTimer = window.setTimeout(() => {
          backendOfflineTimer = null
          if (backendOfflineSince) showSpeechBubble('Agent 已离线，正在重连', 0, 'agent-offline')
        }, 1200)
      }
      const delay = Math.min(15000, 500 * (2 ** Math.min(socketRetry, 5))) + Math.random() * 300
      socketRetry += 1
      socketRetryTimer = window.setTimeout(connectSocket, delay)
    }
    socket.onerror = () => socket.close()
  }

  async function trackCursor() {
    if (!model || !settings.live2d_follow_cursor || electronRuntime || !window.pywebview?.api) return
    try {
      const cursor = await window.pywebview.api.cursor_state()
      if (cursor?.ok) model.focus(Number(cursor.x || 0), Number(cursor.y || 0))
    } catch (_) {}
  }

  function idleTick() {
    if (!model || !settings.live2d_idle_motion || Date.now() < nextIdleMotionAt) return
    playMotion(semanticMotion('idle'))
    nextIdleMotionAt = Date.now() + 45000 + Math.random() * 45000
  }

  function pointHitsModel(clientX, clientY) {
    if (!model || !model.visible) return false
    const bounds = model.getBounds()
    return clientX >= bounds.x && clientX <= bounds.x + bounds.width
      && clientY >= bounds.y && clientY <= bounds.y + bounds.height
  }

  function clickThroughLocked() {
    return electronRuntime && Boolean(settings.live2d_click_through_locked)
  }

  function updateContextMenuLabels() {
    if (speechBubbleButton) {
      speechBubbleButton.textContent = settings.live2d_speech_bubble_enabled === false
        ? '显示说话气泡'
        : '隐藏说话气泡'
    }
    if (speechLanguageButton) {
      speechLanguageButton.textContent = settings.pet_speech_language === 'ja'
        ? '语音语言：日语'
        : '语音语言：中文'
    }
  }

  function updateSpeechBubblePosition() {
    if (!model || speechBubble.hidden) return
    const bounds = model.getBounds()
    const bubbleBounds = speechBubble.getBoundingClientRect()
    const margin = 8
    const preferredLeft = bounds.x + bounds.width * 0.62
    const fallbackLeft = bounds.x - bubbleBounds.width + bounds.width * 0.38
    const useLeftSide = preferredLeft + bubbleBounds.width > window.innerWidth - margin
    const left = Math.max(
      margin,
      Math.min(window.innerWidth - bubbleBounds.width - margin, useLeftSide ? fallbackLeft : preferredLeft),
    )
    const top = Math.max(
      margin,
      Math.min(window.innerHeight - bubbleBounds.height - margin, bounds.y + bounds.height * 0.1 - bubbleBounds.height),
    )
    speechBubble.style.left = `${Math.round(left)}px`
    speechBubble.style.top = `${Math.round(top)}px`
    speechBubble.classList.toggle('align-left', !useLeftSide)
  }

  function showSpeechBubble(text, durationMs = 0, responseId = '') {
    if (settings.live2d_speech_bubble_enabled === false || !String(text || '').trim()) {
      hideSpeechBubble()
      return
    }
    const normalizedResponseId = String(responseId || '')
    const normalizedText = String(text).trim()
    if (!speechBubble.hidden && normalizedResponseId && normalizedResponseId === speechBubbleResponseId) {
      return
    }
    speechBubbleResponseId = normalizedResponseId
    window.clearTimeout(speechBubbleTimer)
    window.clearTimeout(speechBubbleHideTimer)
    speechBubbleHideTimer = null
    const characters = Array.from(normalizedText)
    speechBubbleCharacterCount = characters.length
    speechBubbleVisibleCount = 0
    speechBubble.style.width = ''
    speechBubble.style.visibility = 'hidden'
    speechBubbleText.textContent = characters.join('')
    speechBubble.hidden = false
    const measuredWidth = Math.ceil(speechBubble.getBoundingClientRect().width)
    speechBubble.style.width = `${Math.max(72, Math.min(260, measuredWidth))}px`
    speechBubbleText.textContent = ''
    speechBubble.style.visibility = ''
    const intervalMs = Number(durationMs) > 0
      ? Math.max(55, Math.min(240, Number(durationMs) * 0.92 / Math.max(1, characters.length)))
      : 180
    const revealNext = () => {
      if (speechBubble.hidden || settings.live2d_speech_bubble_enabled === false) return
      speechBubbleVisibleCount += 1
      speechBubbleText.textContent = characters.slice(0, speechBubbleVisibleCount).join('')
      speechBubbleText.scrollTop = speechBubbleText.scrollHeight
      updateSpeechBubblePosition()
      if (speechBubbleVisibleCount < characters.length) {
        speechBubbleTimer = window.setTimeout(revealNext, intervalMs)
      }
    }
    revealNext()
  }

  function hideSpeechBubble() {
    window.clearTimeout(speechBubbleTimer)
    window.clearTimeout(speechBubbleHideTimer)
    speechBubbleTimer = null
    speechBubbleHideTimer = null
    speechBubbleCharacterCount = 0
    speechBubbleVisibleCount = 0
    speechBubbleResponseId = ''
    speechBubble.hidden = true
    speechBubble.style.visibility = ''
    speechBubble.style.width = ''
    speechBubbleText.textContent = ''
  }

  async function persistCompanionSetting(key, value) {
    settings[key] = value
    await request('/api/companion/settings', {
      method: 'PATCH',
      body: JSON.stringify({ [key]: value }),
    })
  }

  async function setClickThroughLocked(enabled, persist = true) {
    const locked = Boolean(enabled)
    settings.live2d_click_through_locked = locked
    root.classList.toggle('click-through-locked', locked)
    contextMenu.hidden = true
    sizePanel.hidden = true
    pointerDown = null
    dragged = false
    root.classList.remove('dragging')
    updateContextMenuLabels()
    if (electronRuntime) window.mioDesktop.setClickThroughLocked(locked)
    if (persist) {
      try { await persistCompanionSetting('live2d_click_through_locked', locked) } catch (_) {}
    }
    if (locked) setInteractive(false)
    else updateMousePassThrough(true)
  }

  function setInteractive(interactive) {
    if (!electronRuntime || interactiveState === interactive) return
    interactiveState = interactive
    window.mioDesktop.setInteractive(interactive)
  }

  function updateMousePassThrough(force = false, event = null) {
    if (clickThroughLocked()) {
      setInteractive(false)
      return
    }
    if (!electronRuntime || !settings.live2d_smart_passthrough) {
      if (electronRuntime) setInteractive(true)
      return
    }
    if (passThroughFrame && !force) return
    passThroughFrame = window.requestAnimationFrame(() => {
      passThroughFrame = null
      const menuOpen = !contextMenu.hidden || !sizePanel.hidden
      const x = Number(event?.clientX ?? -10000)
      const y = Number(event?.clientY ?? -10000)
      setInteractive(menuOpen || Boolean(pointerDown) || pointHitsModel(x, y))
    })
  }

  window.addEventListener('mousemove', (event) => {
    if (model && settings.live2d_follow_cursor && !cursorFocusFrame) {
      const { clientX, clientY } = event
      cursorFocusFrame = window.requestAnimationFrame(() => {
        cursorFocusFrame = null
        if (model && settings.live2d_follow_cursor) model.focus(clientX, clientY)
      })
    }
    updateMousePassThrough(false, event)
  })

  canvas.addEventListener('pointerdown', async (event) => {
    if (clickThroughLocked() || event.button !== 0 || !pointHitsModel(event.clientX, event.clientY)) return
    pointerDown = {
      x: event.clientX,
      y: event.clientY,
      screenX: event.screenX,
      screenY: event.screenY,
      modelX: model?.x || 0,
      modelY: model?.y || 0,
      positionX: Number(settings.position_x || 0),
      positionY: Number(settings.position_y || 0),
      time: Date.now(),
      pointerId: event.pointerId,
    }
    dragged = false
    root.classList.add('dragging')
    canvas.setPointerCapture(event.pointerId)
    setInteractive(true)
    const bridge = await waitForBridge()
    await bridge.begin_drag(event.screenX, event.screenY)
  })

  canvas.addEventListener('pointermove', async (event) => {
    if (!pointerDown) return
    const dx = event.clientX - pointerDown.x
    const dy = event.clientY - pointerDown.y
    const distance = Math.hypot(dx, dy)
    if (distance > 6) dragged = true
    if (electronRuntime && model) {
      model.x = pointerDown.modelX + dx
      model.y = pointerDown.modelY + dy
      updateSpeechBubblePosition()
      updateLockControlAnchor()
    } else if (dragged) {
      const bridge = await waitForBridge()
      await bridge.drag_to(event.screenX, event.screenY)
    }
  })

  async function finishPointerInteraction(event) {
    if (!pointerDown) return
    const interaction = pointerDown
    const wasDragged = dragged
    const dx = Number(event?.clientX ?? interaction.x) - interaction.x
    const dy = Number(event?.clientY ?? interaction.y) - interaction.y
    pointerDown = null
    dragged = false
    root.classList.remove('dragging')
    const bridge = await waitForBridge()
    await bridge.end_drag()
    if (electronRuntime && wasDragged) {
      const virtual = desktopState.bounds || { x: 0, y: 0 }
      const vw = Number(virtual.width) || window.innerWidth
      const vh = Number(virtual.height) || window.innerHeight
      const marginX = Math.min(72, Math.max(32, vw * 0.08))
      const marginY = Math.min(96, Math.max(48, vh * 0.1))
      const nextX = Math.round(Math.min(Math.max(interaction.positionX + dx, virtual.x + marginX), virtual.x + vw - marginX))
      const nextY = Math.round(Math.min(Math.max(interaction.positionY + dy, virtual.y + marginY), virtual.y + vh - marginY))
      settings.position_x = nextX
      settings.position_y = nextY
      request('/api/companion/position', {
        method: 'PATCH',
        body: JSON.stringify({ x: nextX, y: nextY }),
      }).catch(() => {})
      sendSocket('dragged', { distance: Math.round(Math.hypot(dx, dy)), x: nextX, y: nextY })
    } else if (!wasDragged) {
      if (settings.live2d_click_motion) playMotion(semanticMotion('touch'), undefined, { force: true })
      sendSocket('clicked', { x: event?.screenX || 0, y: event?.screenY || 0, emotion: 'gentle' })
    }
    updateMousePassThrough(true, event)
  }

  canvas.addEventListener('pointerup', finishPointerInteraction)
  canvas.addEventListener('pointercancel', finishPointerInteraction)

  canvas.addEventListener('dblclick', async (event) => {
    if (clickThroughLocked() || !pointHitsModel(event.clientX, event.clientY)) return
    event.preventDefault()
    sendSocket('double_clicked', { x: event.screenX, y: event.screenY })
    const bridge = await waitForBridge()
    await bridge.open_chat()
  })

  document.addEventListener('contextmenu', (event) => {
    event.preventDefault()
    if (clickThroughLocked()) return
    if (electronRuntime && !pointHitsModel(event.clientX, event.clientY)) return
    updateContextMenuLabels()
    contextMenu.hidden = false
    contextMenu.style.visibility = 'hidden'
    contextMenu.style.left = '4px'
    contextMenu.style.top = '4px'
    const menuBounds = contextMenu.getBoundingClientRect()
    contextMenu.style.left = `${Math.max(4, Math.min(window.innerWidth - menuBounds.width - 4, event.clientX))}px`
    contextMenu.style.top = `${Math.max(4, Math.min(window.innerHeight - menuBounds.height - 4, event.clientY))}px`
    contextMenu.style.visibility = ''
    setInteractive(true)
  })

  document.addEventListener('pointerdown', (event) => {
    if (!contextMenu.contains(event.target)) contextMenu.hidden = true
    if (!sizePanel.contains(event.target) && event.target?.dataset?.action !== 'size') sizePanel.hidden = true
  })

  contextMenu.addEventListener('click', async (event) => {
    const action = event.target.closest('button')?.dataset.action
    if (!action) return
    contextMenu.hidden = true
    const bridge = await waitForBridge()
    if (action === 'chat') await bridge.open_chat()
    if (action === 'size') sizePanel.hidden = false
    if (action === 'speech-language') {
      const language = settings.pet_speech_language === 'ja' ? 'zh' : 'ja'
      try { await persistCompanionSetting('pet_speech_language', language) } catch (_) {}
      updateContextMenuLabels()
    }
    if (action === 'speech-bubble') {
      const enabled = settings.live2d_speech_bubble_enabled === false
      try { await persistCompanionSetting('live2d_speech_bubble_enabled', enabled) } catch (_) {}
      updateContextMenuLabels()
      if (enabled && voiceLifecycleActive) showSpeechBubble(activeSpeechText)
      else if (!enabled) hideSpeechBubble()
    }
    if (action === 'agent') await bridge.open_agent()
    if (action === 'close') await bridge.close_pet()
    updateMousePassThrough(true, event)
  })

  modelSelect?.addEventListener('change', async () => {
    const modelId = String(modelSelect.value || '')
    if (!modelDefinitions[modelId]) return
    if (electronRuntime && modelDefinitions[modelId].source === 'imported') {
      await window.mioDesktop.selectModel(modelId)
      desktopSelectedModelId = modelId
    } else if (electronRuntime) {
      await window.mioDesktop.selectModel('')
      desktopSelectedModelId = ''
      settings.live2d_model_id = modelId
      await request('/api/companion/settings', {
        method: 'PATCH',
        body: JSON.stringify({ ...settings, live2d_model_id: modelId }),
      })
    }
    await loadModel(modelId)
  })

  sizeSlider.addEventListener('input', async () => {
    const percent = Number(sizeSlider.value)
    sizeValue.value = `${percent}%`
    settings.pet_size_percent = percent
    if (electronRuntime) fitModel()
    else {
      const bridge = await waitForBridge()
      await bridge.resize_window(percent)
    }
    window.clearTimeout(sizeCommitTimer)
    sizeCommitTimer = window.setTimeout(async () => {
      const bridge = await waitForBridge()
      await bridge.set_size(percent)
    }, 140)
  })

  sizeSlider.addEventListener('change', async () => {
    window.clearTimeout(sizeCommitTimer)
    const bridge = await waitForBridge()
    await bridge.set_size(Number(sizeSlider.value))
  })

  window.addEventListener('resize', fitModel)
  window.addEventListener('beforeunload', () => {
    stopSpeech('')
    if (socket) socket.close()
  })

  async function start() {
    createApplication()
    await waitForBridge()
    if (electronRuntime) {
      desktopState = await window.mioDesktop.getState()
      mergeImportedModels(desktopState)
      window.mioDesktop.onModelsChanged(async (payload) => {
        mergeImportedModels(payload)
        await loadModel(selectedModelId())
      })
      window.mioDesktop.onClickThroughChanged(async (enabled) => {
        await setClickThroughLocked(enabled, true)
      })
      connectSocket()
    }
    await pollStatus()
    await pollFeed()
    setInterval(pollStatus, electronRuntime ? 5000 : 1800)
    if (!electronRuntime) setInterval(pollFeed, 900)
    setInterval(trackCursor, 80)
    setInterval(idleTick, 1000)
  }

  window.__mioPetDebug = {
    speak: (payload) => dispatchSpeech({
      source: 'runtime_verification',
      should_speak: true,
      priority: 100,
      ...(payload || {}),
    }),
    stopSpeech: (reason = 'interrupted') => stopSpeech(reason),
    resetSpeechDiagnostics: () => {
      if (speechInProgress()) return false
      speechStartCount = 0
      speechEndCount = 0
      speechInterruptCount = 0
      speechFinishedCount = 0
      speechQueuedCount = 0
      speechDroppedCount = 0
      speechRequestCount = 0
      speechFailureCount = 0
      speechLifecycleHistory = []
      lastSpeechError = ''
      return true
    },
    visualEvent: (payload = {}) => handleVisualEvent({
      emotion: 'gentle',
      importance: 0.7,
      motion_hint: 'observe',
      ...(payload || {}),
    }),
    proceduralMotion: (hint = 'observe', importance = 0.7) => (
      startProceduralMotion(hint, importance)
    ),
    resetVisualDiagnostics: () => {
      visualFrameTimes = []
      visualFpsSamples = []
      visualFrameWindowStartedAt = performance.now()
      visualFrameWindowCount = 0
      visualPerformanceUntil = 0
      rendererLastFrameAt = performance.now()
      proceduralMotionHint = ''
      proceduralMotionStartedAt = 0
      proceduralMotionUntil = 0
      proceduralMotionIntensity = 0
      if (String(lastMotionGroup).startsWith('procedural:')) {
        lastMotionGroup = ''
        lastMotionPlayedAt = 0
      }
      proceduralMotionStartCount = 0
      proceduralMotionPeak = 0
      return true
    },
    snapshot: (options = {}) => {
      const bounds = model?.getBounds?.()
      let alphaPixels = 0
      let sampledPixels = 0
      if (options?.includePixels) {
        try {
          const pixels = model && app?.renderer?.extract?.pixels(model)
          if (pixels) {
            const stride = Math.max(4, Math.floor(pixels.length / 40000 / 4) * 4)
            for (let index = 3; index < pixels.length; index += stride) {
              sampledPixels += 1
              if (pixels[index] > 8) alphaPixels += 1
            }
          }
        } catch (_) {}
      }
      return {
        electronRuntime,
        modelLoaded: Boolean(model),
        currentModelId,
        socketState: socket?.readyState ?? -1,
        activity: currentActivity,
        emotion: currentEmotion,
        audioPlaying: Boolean((currentAudio && !currentAudio.paused) || streamPlaybackActive),
        speechInProgress: speechInProgress(),
        mouthLevel: Number(mouthLevel.toFixed(4)),
        speechStartCount,
        speechEndCount,
        speechInterruptCount,
        speechFinishedCount,
        speechQueuedCount,
        speechDroppedCount,
        speechRequestCount,
        speechFailureCount,
        speechLifecycleHistory: speechLifecycleHistory.map((item) => ({
          ...item,
          events: item.events.map((event) => ({ ...event })),
          playbackMetrics: item.playbackMetrics ? { ...item.playbackMetrics } : undefined,
        })),
        lastSpeechError,
        maxMouthLevel: Number(maxMouthLevel.toFixed(4)),
        lastSpeechStartedAt,
        lastSpeechEndedAt,
        lastVoiceEventSent,
        lastStreamMode,
        streamFirstAudioLatencyMs,
        streamQueuedSources: streamSources.size,
        streamBufferSeconds: Number(streamPendingDuration.toFixed(3)),
        streamPlaybackUntil: streamScheduledUntil,
        rendererFps: Number(rendererMeasuredFps.toFixed(1)),
        activeResponseId,
        activeSpeechPriority,
        speechQueueLength: speechQueue.length,
        clickThroughLocked: clickThroughLocked(),
        speechBubbleEnabled: settings.live2d_speech_bubble_enabled !== false,
        speechBubbleVisible: !speechBubble.hidden,
        speechBubbleProgress: `${speechBubbleVisibleCount}/${speechBubbleCharacterCount}`,
        capabilities: currentCapabilityPayload(),
        renderOptimization: modelDefinition(currentModelId)?.renderOptimization || {},
        lastVisualEvent,
        lastForegroundTitle,
        proceduralMotion: {
          active: Boolean(proceduralMotionHint && Date.now() < proceduralMotionUntil),
          hint: proceduralMotionHint,
          startCount: proceduralMotionStartCount,
          peak: Number(proceduralMotionPeak.toFixed(2)),
        },
        visualPerformance: visualPerformanceSnapshot(),
        renderer: rendererDiagnostics(),
        modelBounds: bounds ? { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height } : null,
        alphaPixels,
        sampledPixels,
        errorVisible: !errorPanel.hidden,
        errorMessage: errorMessage.textContent,
      }
    },
  }

  start().catch((error) => {
    loading.hidden = true
    errorPanel.hidden = false
    errorMessage.textContent = String(error?.message || error || '初始化失败')
    sendSocket('renderer_error', { message: errorMessage.textContent })
    setInteractive(true)
  })
})()
