/* Built app regression: a physical main window can display Agent after fallback. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises'), http = require('node:http'), path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const dist = path.resolve(process.env.MIO_UI_DIST || path.join(__dirname, '../dist'))
const output = process.env.MIO_UI_EVIDENCE || path.join(require('node:os').tmpdir(), 'mio-workspace-return')
async function main() {
  await fs.mkdir(output, { recursive: true })
  const server = http.createServer(async (req, res) => {
    try {
      const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '')
      let file = path.resolve(dist, relative || 'index.html')
      if (!file.startsWith(dist + path.sep)) throw Error('Invalid asset path')
      if (!(await fs.stat(file).catch(() => null))?.isFile()) file = path.join(dist, 'index.html')
      res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' })[path.extname(file)] || 'application/octet-stream')
      res.end(await fs.readFile(file))
    } catch { res.writeHead(404).end() }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const base = `http://127.0.0.1:${server.address().port}`
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const ctx = await browser.newContext({ viewport: { width: 1252, height: 780 } })
  const errors = [], checks = []
  await ctx.addInitScript(() => {
    // Native bridge identity stays main even when its URL becomes Agent.
    window.pywebview = { api: { switch_workspace(target) {
      const calls = JSON.parse(sessionStorage.getItem('switch_calls') || '[]')
      sessionStorage.setItem('switch_calls', JSON.stringify([...calls, target]))
      if (target === 'main') return Promise.resolve({ ok: true, workspace: target, navigation_required: true })
      return new Promise(() => {})
    } } }
  })
  await ctx.route('**/api/**', async route => {
    const p = new URL(route.request().url()).pathname
    const conversations = [{ id: 'desktop_agent_fixture', kind: 'agent', title: 'Agent验证' }, { id: 'desktop_pet', kind: 'companion', title: 'Mio聊天验证' }]
    let value = {}
    if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversation_id: 'desktop_pet', conversations, messages: [], models: [], diaries: [], settings: {}, qq: {}, model: {}, provider_presets: [] }
    else if (p === '/api/onboarding/status') value = { completed: true }
    else if (p === '/api/agent/conversations') value = conversations
    else if (p === '/api/agent/messages' || /^\/(diaries|reviews|weekly)$/.test(p)) value = []
    else if (p === '/api/agent/models/catalog') value = { models: [], provider_presets: [], hidden_model_providers: [] }
    else if (p === '/api/workspace') value = { projects: [], tasks: [] }
    else if (p === '/api/workspace/conversations') value = { projects: {}, modes: {} }
    else if (p === '/api/agent/work') value = { tasks: [], limits: {} }
    else if (p.endsWith('/notifications')) value = { notifications: [] }
    else if (p.includes('/profile')) value = { profile: {}, settings: {} }
    else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
    else if (p.includes('startup-greeting')) value = { enabled: false }
    await route.fulfill({ json: value })
  })
  const page = await ctx.newPage()
  page.on('pageerror', error => errors.push(error.message))
  async function returnToChat() {
    await page.getByRole('button', { name: '我的空间', exact: true }).click()
    await page.getByRole('button', { name: '返回 Mio 聊天页面', exact: true }).click()
    if (process.env.MIO_EXPECT_BROKEN_RETURN === '1') {
      await page.waitForTimeout(2500)
      assert.equal(new URL(page.url()).searchParams.get('workspace'), 'agent')
      assert.equal(await page.locator('.agent-workspace-shell').count(), 1)
      return false
    }
    await page.waitForURL(url => !url.searchParams.has('workspace') && url.searchParams.get('mio_return') === 'chat')
    await page.locator('.app-shell.active-view-chat:not(.agent-workspace-shell)').waitFor()
    assert.notEqual(new URL(page.url()).searchParams.get('conversation_id'), 'desktop_agent_fixture')
    return true
  }
  try {
    await page.goto(base + '/?workspace=agent&conversation_id=desktop_agent_fixture&view=projects')
    await page.locator('.agent-workspace-shell').waitFor()
    const fixed = await returnToChat()
    if (!fixed) checks.push('旧包复现：桥接返回成功且要求同窗导航，点击后仍滞留Agent')
    else {
      checks.push('物理Mio窗显示Agent后，菜单返回真正进入Mio聊天且移除Agent会话ID')
      await page.locator('.workspace-title-switch').click()
      await page.waitForURL(url => url.searchParams.get('workspace') === 'agent')
      await page.locator('.agent-workspace-shell').waitFor()
      assert.notEqual(new URL(page.url()).searchParams.get('conversation_id'), 'desktop_pet')
      checks.push('桌面切换不响应时，超时后仍能进入Agent且不携带聊天会话ID')
      await returnToChat()
      checks.push('经历主窗→Agent超时兜底后，第二次返回聊天仍正常')
    }
    await page.screenshot({ path: path.join(output, fixed ? 'returned-chat.png' : 'stuck-agent.png') })
    assert.deepEqual(errors, [])
    const calls = await page.evaluate(() => JSON.parse(sessionStorage.getItem('switch_calls') || '[]'))
    await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ checks, calls, errors, dist }, null, 2))
    console.log(JSON.stringify({ checks, calls, errors, output }))
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)) }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
