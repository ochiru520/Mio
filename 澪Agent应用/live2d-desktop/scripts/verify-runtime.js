const fs = require('fs')
const path = require('path')

const endpoint = process.argv[2] || 'http://127.0.0.1:9224'
const outputPath = path.resolve(process.argv[3] || 'runtime-screenshot.png')
const speechText = process.argv[4] || ''
const interruptText = process.argv[5] || ''
const requireStream = process.env.MIO_PET_REQUIRE_STREAM === '1'
const maxFirstAudioMs = Number(process.env.MIO_PET_MAX_FIRST_AUDIO_MS || 30000)
let activeSocket = null

async function main() {
  const targets = await fetch(`${endpoint}/json`).then((response) => response.json())
  const target = targets.find((item) => (
    item.type === 'page'
    && String(item.url || '').includes('/live2d-pet/index.html')
    && item.webSocketDebuggerUrl
  ))
  if (!target) throw new Error('没有找到 Electron 渲染页面')

  const socket = new WebSocket(target.webSocketDebuggerUrl)
  activeSocket = socket
  const pending = new Map()
  let nextId = 1
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data)
    if (!message.id || !pending.has(message.id)) return
    const { resolve, reject } = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) reject(new Error(message.error.message))
    else resolve(message.result)
  }
  await new Promise((resolve, reject) => {
    socket.onopen = resolve
    socket.onerror = () => reject(new Error('无法连接 Electron 调试端口'))
  })
  const command = (method, params = {}) => new Promise((resolve, reject) => {
    const id = nextId++
    pending.set(id, { resolve, reject })
    socket.send(JSON.stringify({ id, method, params }))
  })

  await command('Page.enable')
  await command('Runtime.enable')
  await command('Page.reload', { ignoreCache: true })
  await new Promise((resolve) => setTimeout(resolve, 5000))
  const evaluate = async (expression, awaitPromise = true) => {
    const evaluated = await command('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise,
    })
    if (evaluated.exceptionDetails) {
      throw new Error(evaluated.exceptionDetails.exception?.description || 'Electron 页面脚本执行失败')
    }
    return evaluated.result?.value
  }
  const readSnapshot = (includePixels = false) => evaluate(
    `window.__mioPetDebug?.snapshot?.({ includePixels: ${includePixels ? 'true' : 'false'} }) || null`,
  )
  const waitForSnapshot = async (predicate, timeoutMs, label) => {
    const deadline = Date.now() + timeoutMs
    let latest = null
    while (Date.now() < deadline) {
      latest = await readSnapshot()
      if (predicate(latest)) return latest
      await new Promise((resolve) => setTimeout(resolve, 100))
    }
    throw new Error(`${label}超时：${JSON.stringify(latest)}`)
  }

  const snapshot = await readSnapshot(true)
  let speechVerification = null
  if (speechText) {
    const escapedSpeech = JSON.stringify({ text: speechText, emotion: 'gentle' })
    const initialStarts = Number(snapshot?.speechStartCount || 0)
    const initialEnds = Number(snapshot?.speechEndCount || 0)
    const initialInterrupts = Number(snapshot?.speechInterruptCount || 0)
    await evaluate(`window.__mioPetDebug.speak(${escapedSpeech}); true`, false)
    const started = await waitForSnapshot(
      (value) => Number(value?.speechStartCount || 0) > initialStarts,
      120000,
      '等待流式语音开始',
    )

    let interrupted = null
    if (interruptText) {
      await new Promise((resolve) => setTimeout(resolve, 250))
      const escapedInterrupt = JSON.stringify({ text: interruptText, emotion: 'cheerful' })
      await evaluate(`window.__mioPetDebug.speak(${escapedInterrupt}); true`, false)
      interrupted = await waitForSnapshot(
        (value) => Number(value?.speechInterruptCount || 0) > initialInterrupts,
        10000,
        '等待新语音打断旧语音',
      )
    }

    const expectedEnds = initialEnds + (interruptText ? 2 : 1)
    const finished = await waitForSnapshot(
      (value) => Number(value?.speechEndCount || 0) >= expectedEnds,
      120000,
      '等待语音播放结束',
    )
    speechVerification = { started, interrupted, finished }
    if (requireStream && started.lastStreamMode !== 'stream') {
      throw new Error(`没有使用真实流式播放：${started.lastStreamMode || 'unknown'}`)
    }
    if (started.streamFirstAudioLatencyMs == null || started.streamFirstAudioLatencyMs > maxFirstAudioMs) {
      throw new Error(`首音延迟不合格：${started.streamFirstAudioLatencyMs}ms`)
    }
    if (Number(finished.maxMouthLevel || 0) <= 0.02) {
      throw new Error(`口型峰值无效：${finished.maxMouthLevel}`)
    }
    if (finished.lastSpeechError) throw new Error(`语音播放错误：${finished.lastSpeechError}`)
  }
  const screenshot = await command('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  })
  fs.mkdirSync(path.dirname(outputPath), { recursive: true })
  fs.writeFileSync(outputPath, Buffer.from(screenshot.data, 'base64'))
  socket.close()
  activeSocket = null

  console.log(JSON.stringify({ snapshot, speechVerification, screenshot: outputPath }, null, 2))
  if (!snapshot?.modelLoaded) throw new Error('Live2D 模型没有加载')
  if (snapshot.socketState !== 1) throw new Error('桌宠 WebSocket 没有连接')
  if (snapshot.errorVisible) throw new Error(`渲染错误：${snapshot.errorMessage}`)
  if (!snapshot.modelBounds || snapshot.modelBounds.width <= 0 || snapshot.modelBounds.height <= 0) {
    throw new Error('Live2D 模型边界无效')
  }
  if (snapshot.sampledPixels > 0 && snapshot.alphaPixels <= 0) {
    throw new Error('Live2D Canvas 没有非透明模型像素')
  }
}

main().catch((error) => {
  try { activeSocket?.close() } catch (_) {}
  console.error(error.stack || error.message || String(error))
  process.exitCode = 1
})
