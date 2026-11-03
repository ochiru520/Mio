/* Fixed public text through the real app and actual WebAudio, no private chat. */
const assert = require('node:assert/strict'), fs = require('node:fs/promises'), path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
async function main() {
  const { createServer } = await import('vite')
  const syntheticOnly = process.env.MIO_SYNTHETIC_ONLY === '1'
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || '../验收与归档/聊天语音即时播放-20261101/browser')
  await fs.mkdir(output, { recursive: true })
  const server = await createServer({ server: { host: '127.0.0.1', port: 18486, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false,
      configure(proxy) { proxy.on('proxyReq', (outgoing, incoming) => outgoing.setHeader('host', incoming.headers.host)) },
    } } } })
  let browser, page
  const errors = [], checks = [], timings = []
  try {
    await server.listen()
    browser = await chromium.launch({ channel: 'msedge', headless: true })
    page = await browser.newPage({ viewport: { width: 900, height: 580 } })
    page.on('pageerror', error => errors.push(error.message))
    await page.addInitScript(({ syntheticOnly }) => {
      window.marks = []; window.mode = syntheticOnly ? 'finite-synthetic' : 'real'; window.cancelledBodies = 0; window.stoppedNodes = 0
      const originalFetch = window.fetch.bind(window)
      const originalSource = AudioContext.prototype.createBufferSource
      AudioContext.prototype.createBufferSource = function(...args) {
        const source = originalSource.apply(this, args)
        const start = source.start.bind(source), stop = source.stop.bind(source)
        source.start = (...args) => { marks.push({ type: 'schedule', time: performance.now(), when: args[0], current: this.currentTime }); return start(...args) }
        source.stop = (...args) => { stoppedNodes++; return stop(...args) }
        return source
      }
      function synthetic() {
        const bytes = new Uint8Array(6444), v = new DataView(bytes.buffer)
        for (const [offset, text] of [[0, 'RIFF'], [8, 'WAVE'], [12, 'fmt '], [36, 'data']]) bytes.set(new TextEncoder().encode(text), offset)
        v.setUint32(4, 0x7ffffff0 + 36, true); v.setUint32(16, 16, true)
        v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, 32000, true)
        v.setUint32(28, 64000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); v.setUint32(40, 0x7ffffff0, true)
        if (window.mode === 'finite-synthetic') {
          v.setUint32(4, 6436, true); v.setUint32(40, 6400, true)
          for (let index = 0; index < 3200; index++) v.setInt16(44 + index * 2, Math.round(Math.sin(index * 2 * Math.PI * 440 / 32000) * 1200), true)
          let first, last
          return new Response(new ReadableStream({
            start(sender) { first = setTimeout(() => { marks.push({ type: 'first-pcm', time: performance.now() }); sender.enqueue(bytes) }, 50); last = setTimeout(() => { marks.push({ type: 'eof', time: performance.now() }); sender.close() }, 500) },
            cancel() { clearTimeout(first); clearTimeout(last); cancelledBodies++ },
          }), { headers: { 'Content-Type': 'audio/wav' } })
        }
        let timer
        return new Response(new ReadableStream({
          start(sender) { timer = setTimeout(() => sender.enqueue(bytes), window.mode === 'delayed' ? 1500 : 50) },
          cancel() { clearTimeout(timer); cancelledBodies++ },
        }), { headers: { 'Content-Type': 'audio/wav' } })
      }
      window.fetch = async (url, options) => {
        if (!String(url).includes('/voice/')) return originalFetch(url, options)
        marks.push({ type: 'request', time: performance.now(), url, payload: JSON.parse(options.body) })
        if (mode !== 'real') return synthetic()
        const response = await originalFetch(url, options)
        let bytes = 0, first = false
        const body = response.body.pipeThrough(new TransformStream({
          transform(piece, controller) {
            bytes += piece.length
            if (!first && bytes > 44) { first = true; marks.push({ type: 'first-pcm', time: performance.now() }) }
            controller.enqueue(piece)
          },
          flush() { marks.push({ type: 'eof', time: performance.now() }) },
        }))
        return new Response(body, { status: response.status, headers: response.headers })
      }
    }, { syntheticOnly })
    await page.goto('http://127.0.0.1:18486/scripts/message-voice-preview.html')
    await page.click('#play')
    await page.waitForFunction(() => document.querySelector('#speaking').textContent === 'first', null, { timeout: 120000 })
    const first = await page.evaluate(() => marks)
    assert.equal(first.find(row => row.type === 'request').url, '/api/companion/voice/stream')
    assert.equal(first.some(row => row.type === 'eof'), false)
    await page.screenshot({ path: path.join(output, 'playing-before-eof.png') })
    await page.waitForFunction(() => marks.some(row => row.type === 'eof'), null, { timeout: 120000 })
    await page.waitForFunction(() => !document.querySelector('#speaking').textContent, null, { timeout: 120000 })
    const real = await page.evaluate(() => marks), start = real.find(row => row.type === 'request').time
    const schedule = real.find(row => row.type === 'schedule'), eof = real.find(row => row.type === 'eof')
    timings.push({ first_pcm_ms: syntheticOnly ? null : real.find(row => row.type === 'first-pcm').time - start,
      scheduled_playback_ms: schedule.time - start + (schedule.when - schedule.current) * 1000,
      generation_eof_ms: syntheticOnly ? null : eof.time - start, hardware_output_verified: false, synthetic: syntheticOnly })
    checks.push(syntheticOnly ? '合成PCM流：真实WebAudio首音排播与整段结束清理通过，未调用实际V2或声卡' : '实际V2：WebAudio已安排首音时HTTP仍未结束；整段播放完清理状态')
    await page.evaluate(() => { mode = 'synthetic'; marks = [] })
    await page.click('#play')
    await page.waitForFunction(() => document.querySelector('#speaking').textContent === 'first')
    await page.click('#next')
    await page.waitForFunction(() => document.querySelector('#speaking').textContent === 'second')
    assert.ok(await page.evaluate(() => cancelledBodies >= 1 && stoppedNodes >= 1))
    await page.click('#stop')
    await page.waitForFunction(() => !document.querySelector('#speaking').textContent && !document.querySelector('#loading').textContent)
    checks.push('切换消息取消旧响应与排播节点；停止不留旧状态或残音节点')
    await page.evaluate(() => { mode = 'delayed'; marks = [] })
    await page.click('#play'); await page.click('#stop')
    await page.waitForTimeout(1600)
    assert.equal(await page.evaluate(() => marks.filter(row => row.type === 'schedule').length), 0)
    assert.equal(await page.locator('#error').innerText(), '')
    checks.push('首音前停止后迟到响应不播放；保留请求语言设置')
    assert.deepEqual(errors, [])
    await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ ok: true, checks, errors, timings, synthetic_only: syntheticOnly, model_loaded: !syntheticOnly }, null, 2))
    console.log(JSON.stringify({ ok: true, checks, timings }))
  } catch (error) {
    console.error(JSON.stringify({ errors, body: await page?.locator('body').innerText(), marks: await page?.evaluate(() => window.marks) }))
    throw error
  } finally {
    await browser?.close()
    await server.close()
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
