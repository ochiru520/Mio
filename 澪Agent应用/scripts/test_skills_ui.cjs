const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18436'
const output = path.resolve(__dirname, '../../临时验收/skills-20261004')

async function run() {
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const results = []
  try {
    for (const mobile of [false, true]) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 800 } })
      await context.addInitScript(() => localStorage.setItem('mio_agent_active_view', 'projects'))
      const errors = [], writes = []
      let item = { id: 'skill_ui', name: '合成技能', description: '可调整指导', enabled: true, revision: 1, source_status: 'local', content: '合成正文'.repeat(6000) + '完整末尾', content_hash: 'a' }
      let conflict = false
      await context.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url()), p = url.pathname
        if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
        let value = {}
        if (p === '/api/onboarding/status') value = { completed: true }
        else if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversations: [], messages: [], models: [], diaries: [], qq: {}, settings: {} }
        else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
        else if (p === '/api/mobile/session') value = { paired: true, name: '合成手机' }
        else if (p === '/api/workspace') value = { projects: [], templates: [], routines: [], browser_pending: [] }
        else if (p === '/api/workspace/pending') value = { items: [], total: 0, has_more: false }
        else if (p === '/api/workspace/tasks') value = { tasks: [], has_more: false }
        else if (p.startsWith('/api/workspace/skills')) {
          if (request.method() !== 'GET') {
            if (mobile) assert.equal(request.headers()['x-mio-mobile'], '1')
            writes.push({ p, body: request.postDataJSON() })
            if (request.method() === 'PUT' && conflict) { conflict = false; return route.fulfill({ status: 400, json: { detail: '技能已被其他编辑更新，草稿未覆盖' } }) }
            if (p.endsWith('/enabled')) item = { ...item, enabled: !item.enabled, revision: item.revision + 1 }
            else item = { ...item, ...request.postDataJSON(), revision: item.revision + 1 }
            value = item
          } else if (p === '/api/workspace/skills') value = { skills: [item] }
          else {
            const offset = Number(url.searchParams.get('offset') || 0), revision = Number(url.searchParams.get('revision') || item.revision)
            const content = revision === 1 && item.revision > 1 ? '只读旧版本' : item.content
            value = { ...item, revision, current_revision: item.revision, content: content.slice(offset, offset + 20000), next_offset: Math.min(content.length, offset + 20000), has_more: offset + 20000 < content.length }
          }
        }
        else if (p.includes('startup-greeting')) value = { enabled: false }
        else if (p.includes('/profile')) value = { profile: {}, settings: {} }
        else if (p === '/api/agent/conversations' || p === '/api/agent/messages' || /^\/(diaries|reviews|weekly)$/.test(p)) value = []
        await route.fulfill({ json: value })
      })
      const page = await context.newPage(); page.on('pageerror', error => errors.push(error.message))
      await page.goto(base + (mobile ? '/mobile.html' : '/?workspace=agent'))
      const hub = page.locator('.workspace-hub'); await hub.waitFor()
      await hub.getByRole('button', { name: '技能', exact: true }).click()
      const panel = page.locator('.agent-skills')
      const idle = () => page.waitForFunction(() => document.querySelector('.agent-skills')?.getAttribute('aria-busy') === 'false')
      await panel.getByRole('button', { name: '编辑 合成技能', exact: true }).waitFor(); await idle()
      assert.equal(await panel.getByRole('button', { name: '导入技能', exact: true }).count(), mobile ? 0 : 1)
      await panel.getByRole('button', { name: '编辑 合成技能', exact: true }).click(); await idle()
      assert.ok((await panel.getByLabel('技能正文', { exact: true }).inputValue()).endsWith('完整末尾'))
      await panel.getByLabel('技能正文', { exact: true }).fill('用户新草稿')
      conflict = true
      await panel.getByRole('button', { name: '保存技能', exact: true }).click(); await idle()
      await panel.getByRole('alert').getByText(/草稿未覆盖/).waitFor()
      assert.equal(await panel.getByLabel('技能正文', { exact: true }).inputValue(), '用户新草稿')
      await panel.getByRole('button', { name: '保存技能', exact: true }).click(); await idle()
      assert.equal(item.content, '用户新草稿')
      await panel.getByRole('button', { name: '停用 合成技能', exact: true }).click(); await idle()
      await panel.getByRole('button', { name: '启用 合成技能', exact: true }).waitFor()
      await panel.getByRole('button', { name: '编辑 合成技能', exact: true }).click(); await idle()
      await panel.getByLabel('历史版本', { exact: true }).fill('1')
      await panel.getByRole('button', { name: '查看版本', exact: true }).click(); await idle()
      await panel.getByText(/只读旧版本/).waitFor()
      assert.equal(await panel.getByLabel('技能正文', { exact: true }).inputValue(), '用户新草稿')
      await page.screenshot({ path: path.join(output, mobile ? 'mobile.png' : 'desktop.png'), fullPage: true })
      assert.deepEqual(errors, []); assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth))
      assert.equal(writes.length, 3)
      results.push({ mobile, fullRead: true, conflictDraft: true, toggle: true, history: true })
      await context.close()
    }
  } finally { await browser.close() }
  console.log(JSON.stringify({ results, output }))
}
run().catch(error => { console.error(error); process.exitCode = 1 })
