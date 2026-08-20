const endpoint = process.argv[2] || 'http://127.0.0.1:9224'
const timeoutMs = Number(process.env.MIO_PET_INTERRUPT_TIMEOUT_MS || 120000)

let activeSocket = null

async function waitFor(snapshot, predicate, label) {
  const deadline = Date.now() + timeoutMs
  let latest = null
  while (Date.now() < deadline) {
    latest = await snapshot()
    if (predicate(latest)) return latest
    await new Promise((resolve) => setTimeout(resolve, 50))
  }
  throw new Error(`${label}: ${JSON.stringify(latest)}`)
}

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
  await waitFor(
    snapshot,
    (value) => !value?.speechInProgress && Number(value?.speechQueueLength || 0) === 0,
    '等待空闲状态超时',
  )
  const reset = await evaluate('window.__mioPetDebug?.resetSpeechDiagnostics?.() || false')
  if (!reset) throw new Error('无法建立干净的语音诊断基线')

  const runId = `interrupt-verification-${Date.now()}`
  const utterances = [
    {
      text: '这是主动打断验收的当前长语音。我会持续说一段足够长的话，等声音真正开始以后再打断。当前这一条应该停止，排在后面的回复必须完整保留。',
      response_id: `${runId}-current`,
    },
    { text: '主动打断之后的第一条回复。', response_id: `${runId}-follow-1` },
    { text: '主动打断之后的第二条回复。', response_id: `${runId}-follow-2` },
  ]
  for (const utterance of utterances) {
    const payload = JSON.stringify({
      ...utterance,
      emotion: 'gentle',
      speech_language: 'zh',
      source: 'runtime_verification',
      should_speak: true,
      priority: 100,
    })
    await evaluate(`window.__mioPetDebug.speak(${payload}); true`, false)
    await new Promise((resolve) => setTimeout(resolve, 80))
  }

  const speaking = await waitFor(
    snapshot,
    (value) => (
      value?.activeResponseId === `${runId}-current`
      && Number(value?.speechStartCount || 0) === 1
      && Number(value?.speechQueueLength || 0) === 2
      && Number(value?.mouthLevel || 0) > 0.02
    ),
    '首条语音未真正开始播放',
  )
  const interruptedResponseId = speaking.activeResponseId
  await evaluate("window.__mioPetDebug.stopSpeech('interrupted'); true")

  const settled = await waitFor(
    snapshot,
    (value) => (
      !value?.speechInProgress
      && Number(value?.speechQueueLength || 0) === 0
      && Number(value?.speechStartCount || 0) === 3
      && Number(value?.speechInterruptCount || 0) === 1
      && Number(value?.speechFinishedCount || 0) === 2
    ),
    '中断后队列未正确完成',
  )
  const lifecycle = (settled.speechLifecycleHistory || [])
    .filter((item) => String(item.responseId || '').startsWith(runId))
  const statusById = Object.fromEntries(lifecycle.map((item) => [item.responseId, item.status]))
  const result = {
    requested: utterances.length,
    tracked: lifecycle.length,
    interruptedResponseId,
    interrupted: Number(settled.speechInterruptCount || 0),
    finished: Number(settled.speechFinishedCount || 0),
    dropped: Number(settled.speechDroppedCount || 0),
    remaining: Number(settled.speechQueueLength || 0),
    statusById,
    error: settled.lastSpeechError || '',
  }
  console.log(JSON.stringify(result, null, 2))

  if (result.tracked !== 3) throw new Error(`只追踪到 ${result.tracked}/3 条语音`)
  if (result.interruptedResponseId !== `${runId}-current`) throw new Error('中断目标不是当前语音')
  if (statusById[`${runId}-current`] !== 'interrupted') throw new Error('当前语音没有被标记为中断')
  if (statusById[`${runId}-follow-1`] !== 'finished') throw new Error('第一条后续回复未完成')
  if (statusById[`${runId}-follow-2`] !== 'finished') throw new Error('第二条后续回复未完成')
  if (result.interrupted !== 1 || result.finished !== 2) throw new Error('语音生命周期计数不正确')
  if (result.dropped !== 0 || result.remaining !== 0) throw new Error('后续回复被丢弃或队列有残留')
  if (result.error) throw new Error(`播放错误：${result.error}`)
  socket.close()
  activeSocket = null
}

main().catch((error) => {
  try { activeSocket?.close() } catch (_) {}
  console.error(error.stack || error.message || String(error))
  process.exitCode = 1
})
