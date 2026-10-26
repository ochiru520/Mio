/* Real production UI with synthetic conversations; no backend or model calls. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const http = require('node:http')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const dist = path.resolve(process.env.MIO_UI_DIST || path.join(__dirname, '../dist'))
const output = process.env.MIO_UI_EVIDENCE || path.join(require('node:os').tmpdir(), 'mio-chat-handoff-layout')
const expectBroken = process.env.MIO_EXPECT_BROKEN_LAYOUT === '1'

async function main() {
  await fs.mkdir(output, { recursive: true })
  const server = http.createServer(async (req, res) => {
    try {
      const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '')
      let file = path.resolve(dist, relative || 'index.html')
      if (!file.startsWith(dist + path.sep)) throw new Error('Invalid asset path')
      if (!(await fs.stat(file).catch(() => null))?.isFile()) file = path.join(dist, 'index.html')
      const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' }
      res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream')
      const content = await fs.readFile(file)
      if (path.extname(file) === '.css' && process.env.MIO_UI_CSS_PATCH) {
        res.end(Buffer.concat([content, await fs.readFile(process.env.MIO_UI_CSS_PATCH)]))
      } else res.end(content)
    } catch { res.writeHead(404).end() }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const base = `http://127.0.0.1:${server.address().port}`
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const ctx = await browser.newContext({ viewport: { width: 1128, height: 720 } })
  const page = await ctx.newPage()
  const errors = [], checks = []
  let handoff = true, handoffStatus = process.env.MIO_EXPECT_STALE_HANDOFF === '1' ? 'completed' : 'running', handoffTaskId = 'synthetic-task'
  const conversations = [{ id: 'qq_layout_fixture', title: 'QQ共享对话', kind: 'qq' }]
  const messages = Array.from({ length: 45 }, (_, i) => ({ id: i + 1, role: i % 2 ? 'assistant' : 'user',
    content: `合成对话 ${i + 1}。` + '这是用于检查聊天输入框位置的测试内容。'.repeat(8), created_at: '2026-10-24T12:00:00', request_id: `layout-${i}` }))
  page.on('pageerror', error => errors.push(error.message))
  await ctx.addInitScript(() => {
    localStorage.setItem('mio_agent_active_view', 'chat')
    localStorage.setItem('mio_conversation_id', 'qq_layout_fixture')
    localStorage.setItem('mio_app_preferences', JSON.stringify({ theme: 'xuan', default_open_page: 'chat' }))
    window.EventSource = class { addEventListener() {} close() {} }
  })
  await ctx.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname
    if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
    let value = {}
    if (p === '/api/onboarding/status') value = { completed: true }
    else if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversation_id: 'qq_layout_fixture', conversations, messages,
      models: [], diaries: [], settings: {}, qq: {}, model: {}, provider_presets: [] }
    else if (p.includes('/handoff/source/')) value = { handoff: handoff ? { task_id: handoffTaskId, conversation_id: 'desktop_agent_fixture', status: handoffStatus } : null }
    else if (p === '/api/agent/conversations') value = conversations
    else if (p === '/api/agent/messages') value = messages
    else if (p === '/api/agent/models/catalog') value = { models: [], provider_presets: [], hidden_model_providers: [] }
    else if (p === '/api/agent/context-usage') value = { used_tokens: 1200, max_tokens: 16000, token_percent: 7.5 }
    else if (p === '/api/workspace') value = { projects: [], tasks: [] }
    else if (p === '/api/workspace/conversations') value = { projects: {}, modes: {} }
    else if (p === '/api/agent/work') value = { tasks: [], limits: {} }
    else if (p.includes('/profile')) value = { profile: {}, settings: {} }
    else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
    else if (/^\/(diaries|reviews|weekly)$/.test(p)) value = []
    await route.fulfill({ json: value })
  })
  const url = base + '/?workspace=companion&conversation_id=qq_layout_fixture'
  async function geometry(footer = '.composer-wrap') {
    return page.evaluate(selector => {
      const rect = s => { const r = document.querySelector(s).getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height } }
      const scroll = document.querySelector('.message-scroll')
      return { column: rect('.chat-column'), reading: rect('.chat-reading-pane'), footer: rect(selector),
        scrollHeight: scroll.scrollHeight, clientHeight: scroll.clientHeight }
    }, footer)
  }
  async function assertBottom(name, footer) {
    const size = await geometry(footer)
    assert.ok(size.reading.height > 80, `${name}: message pane collapsed: ${JSON.stringify(size)}`)
    assert.ok(size.footer.top >= size.reading.bottom - 2, `${name}: input appeared above messages: ${JSON.stringify(size)}`)
    assert.ok(Math.abs(size.footer.bottom - size.column.bottom) <= 2, `${name}: footer not at bottom: ${JSON.stringify(size)}`)
    assert.ok(size.scrollHeight > size.clientHeight, `${name}: history not scrollable`)
    checks.push({ name, geometry: size })
  }
  try {
    await page.goto(url)
    await page.locator('.chat-handoff-notice').waitFor()
    await page.locator('[data-message-anchor]').first().waitFor()
    if (process.env.MIO_EXPECT_STALE_HANDOFF === '1') {
      assert.match(await page.locator('.chat-handoff-notice').innerText(), /继续处理/)
      await page.screenshot({ path: path.join(output, 'stale-completed-task.png') })
      await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ reproduced: true, taskStatus: handoffStatus, staleNoticeVisible: true, pageErrors: errors }, null, 2))
      console.log('Reproduced: completed historical task still claims continued execution')
      return
    }
    if (expectBroken) {
      const size = await geometry()
      assert.ok(size.footer.top < size.reading.top, 'Original defect must reproduce before repair')
      await page.screenshot({ path: path.join(output, 'before.png') })
      console.log(JSON.stringify({ reproduced: true, geometry: size }))
      return
    }
    await assertBottom('handoff notice visible')
    await page.reload()
    await page.locator('.chat-handoff-notice').waitFor()
    await assertBottom('reload preserves bottom composer')
    for (const viewport of [{ width: 1280, height: 850 }, { width: 980, height: 620 }, { width: 640, height: 500 }]) {
      await page.setViewportSize(viewport)
      await assertBottom(`resize ${viewport.width}x${viewport.height}`)
    }
    await page.setViewportSize({ width: 1128, height: 720 })
    await page.locator('.composer-input').fill(Array(12).fill('输入长文本时也要保持在底部').join('\n'))
    await assertBottom('long draft')
    await page.locator('.composer-input').fill('')
    await page.screenshot({ path: path.join(output, 'after.png') })
    await page.getByTitle('对话窗口', { exact: true }).click()
    await page.getByTitle('选择要清理的 QQ 消息', { exact: true }).click()
    await page.locator('.qq-message-cleanup-bar').waitFor()
    await assertBottom('QQ cleanup footer', '.qq-message-cleanup-bar')
    await page.locator('.qq-message-cleanup-bar').getByRole('button', { name: '取消', exact: true }).click()
    await assertBottom('cancel QQ cleanup')
    handoff = false
    await page.reload()
    await page.locator('.composer-input').waitFor()
    await page.locator('[data-message-anchor]').first().waitFor()
    assert.equal(await page.locator('.chat-handoff-notice').count(), 0)
    await assertBottom('without handoff notice')
    handoff = true; handoffStatus = 'completed'
    await page.reload(); await page.locator('[data-message-anchor]').first().waitFor()
    assert.equal(await page.locator('.chat-handoff-notice').count(), 0)
    checks.push({ name: 'completed historical task stays hidden after reload' })
    handoffStatus = 'running'
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await page.locator('.chat-handoff-notice').filter({ hasText: 'Agent 正在处理' }).waitFor()
    handoffStatus = 'completed'
    await page.locator('.chat-handoff-notice').waitFor({ state: 'detached', timeout: 10000 })
    checks.push({ name: 'running task finishes and disappears through periodic status refresh' })
    handoffStatus = 'paused'
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await page.locator('.chat-handoff-notice').filter({ hasText: '任务已暂停' }).waitFor()
    await page.getByRole('button', { name: '关闭任务提示', exact: true }).click()
    await page.locator('.chat-handoff-notice').waitFor({ state: 'detached' })
    await page.reload(); await page.locator('[data-message-anchor]').first().waitFor()
    assert.equal(await page.locator('.chat-handoff-notice').count(), 0)
    assert.equal(handoffStatus, 'paused')
    checks.push({ name: 'paused label is accurate; dismiss survives reload without changing task' })
    handoffTaskId = 'new-task'; handoffStatus = 'waiting_confirmation'
    await page.evaluate(() => window.dispatchEvent(new Event('focus')))
    await page.locator('.chat-handoff-notice').filter({ hasText: '等待确认' }).waitFor()
    checks.push({ name: 'dismissing an old task does not suppress a new task' })
    for (const status of ['cancelled', 'failed']) {
      handoffStatus = status
      await page.evaluate(() => window.dispatchEvent(new Event('focus')))
      await page.locator('.chat-handoff-notice').waitFor({ state: 'detached' })
      checks.push({ name: `${status} task does not claim continued execution` })
    }
    await page.screenshot({ path: path.join(output, 'completed-notice-cleared.png') })
    assert.deepEqual(errors, [])
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ checks, pageErrors: errors, scope: 'Production frontend, synthetic API; no real conversations or model calls.' }, null, 2))
    console.log(JSON.stringify({ passed: checks.length, pageErrors: errors }))
  } catch (error) {
    await page.screenshot({ path: path.join(output, 'failure.png') })
    await fs.writeFile(path.join(output, 'failure-dom.txt'), await page.locator('body').innerText())
    throw error
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
