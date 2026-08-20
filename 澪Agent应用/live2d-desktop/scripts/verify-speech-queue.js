const endpoint = process.argv[2] || 'http://127.0.0.1:9224'
const timeoutMs = Number(process.env.MIO_PET_QUEUE_TIMEOUT_MS || 180000)
const speechLanguage = String(process.env.MIO_PET_QUEUE_LANGUAGE || 'zh')
const utterances = [
  '落落、听得到吗？',
  '这是第二句连续播放测试。',
  '第三句不应该打断前一句。',
  '第四句也要完整说出来。',
  '最后一句，连续播放结束。',
]

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
  await command('Runtime.enable')
  const evaluate = async (expression, awaitPromise = true) => {
    const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise })
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || 'Electron 页面脚本执行失败')
    }
    return result.result?.value
  }
  const snapshot = () => evaluate('window.__mioPetDebug?.snapshot?.({ includePixels: false }) || null')
  const ready = await snapshot()
  if (!ready?.modelLoaded || ready.socketState !== 1) throw new Error('桌宠运行时尚未就绪')
  await new Promise((resolve) => setTimeout(resolve, 1200))
  const idleSnapshot = await snapshot()
  const idleRendererFps = Number(idleSnapshot?.rendererFps || 0)
  const reset = await evaluate('window.__mioPetDebug?.resetSpeechDiagnostics?.() || false')
  if (!reset) throw new Error('桌宠仍在播放，无法建立干净的语音诊断基线')
  const initial = await snapshot()
  const runId = `queue-verification-${Date.now()}`

  for (let index = 0; index < utterances.length; index += 1) {
    const payload = JSON.stringify({
      text: utterances[index],
      emotion: 'gentle',
      speech_language: speechLanguage,
      response_id: `${runId}-${index}`,
    })
    await evaluate(`window.__mioPetDebug.speak(${payload}); true`, false)
    await new Promise((resolve) => setTimeout(resolve, 80))
  }

  const deadline = Date.now() + timeoutMs
  let current = initial
  while (Date.now() < deadline) {
    current = await snapshot()
    const lifecycle = (current?.speechLifecycleHistory || []).filter((item) => (
      String(item.responseId || '').startsWith(`${runId}-`)
    ))
    const terminal = lifecycle.filter((item) => ['finished', 'failed', 'dropped'].includes(item.status))
    if (terminal.length >= utterances.length && Number(current.speechQueueLength || 0) === 0) break
    await new Promise((resolve) => setTimeout(resolve, 100))
  }

  const lifecycle = (current?.speechLifecycleHistory || []).filter((item) => (
    String(item.responseId || '').startsWith(`${runId}-`)
  )).sort((left, right) => String(left.responseId).localeCompare(String(right.responseId)))
  const frameTimes = lifecycle.flatMap((item) => item.playbackMetrics?.frameTimesMs || [])
    .map(Number)
    .filter((value) => Number.isFinite(value) && value >= 0)
    .sort((left, right) => left - right)
  const frameTimeP95Index = frameTimes.length
    ? Math.min(frameTimes.length - 1, Math.ceil(frameTimes.length * 0.95) - 1)
    : -1
  const playbackFps = lifecycle.map((item) => Number(item.playbackMetrics?.minimumFps))
    .filter((value) => Number.isFinite(value) && value > 0)

  const result = {
    requested: utterances.length,
    language: speechLanguage,
    tracked: lifecycle.length,
    started: lifecycle.filter((item) => item.startedAt).length,
    finished: lifecycle.filter((item) => item.status === 'finished').length,
    failed: lifecycle.filter((item) => item.status === 'failed').length,
    interrupted: lifecycle.filter((item) => item.status === 'interrupted').length,
    queued: Number(current?.speechQueuedCount || 0),
    dropped: lifecycle.filter((item) => item.status === 'dropped').length,
    remaining: Number(current?.speechQueueLength || 0),
    firstAudioMs: lifecycle.map((item) => item.firstAudioLatencyMs).filter((value) => value != null),
    idleRendererFps: Number(idleRendererFps.toFixed(1)),
    minimumPlaybackFps: playbackFps.length ? Number(Math.min(...playbackFps).toFixed(1)) : null,
    frameTimeP95Ms: frameTimeP95Index >= 0 ? Number(frameTimes[frameTimeP95Index].toFixed(2)) : null,
    framesOver33Ms: frameTimes.filter((value) => value > 33).length,
    framesOver50Ms: frameTimes.filter((value) => value > 50).length,
    mode: current?.lastStreamMode,
    error: lifecycle.find((item) => item.error)?.error || current?.lastSpeechError || '',
    lifecycle,
  }
  console.log(JSON.stringify(result, null, 2))
  if (result.tracked !== result.requested) throw new Error(`只追踪到 ${result.tracked}/${result.requested} 条`)
  if (result.started !== result.requested) throw new Error(`只开始播放 ${result.started}/${result.requested} 条`)
  if (result.finished !== result.requested) throw new Error(`只完整播放 ${result.finished}/${result.requested} 条`)
  if (result.failed !== 0) throw new Error(`连续回复有 ${result.failed} 条在播放前或播放中失败`)
  if (result.interrupted !== 0) throw new Error(`普通连续回复错误中断了 ${result.interrupted} 条`)
  if (result.dropped !== 0 || result.remaining !== 0) throw new Error('连续回复队列存在丢弃或残留')
  if (result.minimumPlaybackFps !== null && result.minimumPlaybackFps < 45) {
    throw new Error(`语音实际播放期间桌宠最低更新率过低：${result.minimumPlaybackFps} FPS`)
  }
  if (result.error) throw new Error(`最后一次播放错误：${result.error}`)
  socket.close()
  activeSocket = null
}

main().catch((error) => {
  try { activeSocket?.close() } catch (_) {}
  console.error(error.stack || error.message || String(error))
  process.exitCode = 1
})
