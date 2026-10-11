const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:1439'
const output = path.resolve(process.env.MIO_UI_EVIDENCE || 'ui-tool-picker')
const agentId = 'desktop_agent_fixture'
const workflows = [
  { id: 'anima-2.9b-image', label: 'Anima 2.9B 图片', media_type: 'image', available: true },
  { id: 'qwen-2.1-image', label: '千问 Qwen 2.1 图片', media_type: 'image', available: true },
  { id: 'minimax-h3-video', label: 'MINIMAX H3 双采样 · 8G 视频', media_type: 'video', available: true },
]
async function main() {
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const errors = [], captures = []
  try {
    for (const [theme, width] of [['xuan', 1100], ['ink', 760]]) {
      const context = await browser.newContext({ viewport: { width, height: 920 } })
      await context.addInitScript(theme => {
        localStorage.setItem('mio_app_preferences', JSON.stringify({ theme, font_size: 'large' }))
        localStorage.setItem('mio_agent_active_view', 'chat')
      }, theme)
      const page = await context.newPage()
      page.on('pageerror', e => errors.push(e.message))
      let failCatalog = false, disableVideo = false
      await context.route('**/*', async route => {
        const url = new URL(route.request().url()), p = url.pathname
        if (url.origin !== new URL(base).origin) return route.abort()
        if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
        let value = {}
        const conversations = [{ id: agentId, title: '工具切换验收', kind: 'agent' }]
        if (p === '/api/onboarding/status') value = { completed: true }
        else if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversation_id: agentId, conversations, messages: [], models: [], diaries: [], qq: {}, settings: {} }
        else if (p === '/api/agent/conversations') value = conversations
        else if (p === '/api/agent/messages') value = []
        else if (p === '/api/creation/workflows') {
          if (failCatalog) return route.fulfill({ status: 503, json: { detail: '暂时不可用' } })
          value = { workflows: workflows.map(w => ({ ...w, available: !(disableVideo && w.media_type === 'video'), missing_reason: '视频工作流缺失' })) }
        } else if (p === '/api/agent/chat') {
          const body = route.request().postDataJSON(); captures.push(body)
          value = { reply: '工具选择已收到', request_id: body.client_request_id }
        } else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
        else if (p.includes('startup-greeting')) value = { enabled: false }
        else if (p.includes('/profile')) value = { profile: {}, settings: {} }
        else if (/^\/(diaries|reviews|weekly)$/.test(p)) value = []
        else if (p.endsWith('/tasks')) value = { tasks: [] }
        await route.fulfill({ json: value })
      })
      await page.goto(`${base}/?workspace=agent&conversation_id=${agentId}`)
      const trigger = page.getByRole('button', { name: /^切换工具/ })
      await trigger.click()
      await page.getByRole('menuitemradio', { name: /千问生图/ }).waitFor()
      await page.getByRole('status').filter({ hasText: '正在更新工具' }).waitFor({ state: 'hidden' })
      assert.equal(await page.getByRole('menuitemradio').count(), 4)
      const menuBox = await page.getByRole('menu', { name: '选择任务工具' }).boundingBox()
      assert.ok(menuBox.x >= 0 && menuBox.x + menuBox.width <= width && menuBox.y >= 0)
      await page.screenshot({ path: path.join(output, `picker-${theme}.png`) })
      for (const [label, workflow] of [['千问生图', workflows[1].id], ['H3 生视频', workflows[2].id], ['Anima 生图', workflows[0].id], ['自动调用', '']]) {
        if (!await page.getByRole('menu').count()) await trigger.click()
        await page.getByRole('menuitemradio', { name: new RegExp(label) }).click()
        await page.locator('.composer-input').fill(`测试${label}`)
        const sent = page.waitForResponse(r => new URL(r.url()).pathname === '/api/agent/chat')
        await page.locator('.composer-input').press('Enter')
        await sent
        await page.waitForFunction(() => !document.querySelector('.agent-tool-mode').disabled)
        assert.equal(captures.at(-1).creation_workflow_id, workflow)
        assert.equal(captures.at(-1).agent_workspace, true)
      }
      await trigger.click()
      await page.getByRole('menuitemradio', { name: /千问生图/ }).click()
      await page.reload()
      await page.getByRole('button', { name: '切换工具，当前千问生图' }).waitFor()
      await trigger.click()
      await page.getByRole('menuitemradio', { name: /千问生图/ }).focus()
      await page.keyboard.press('ArrowUp')
      await page.keyboard.press('Enter')
      assert.equal(await trigger.textContent(), 'Anima 生图')
      await trigger.click(); await page.keyboard.press('Escape')
      assert.equal(await page.getByRole('menu').count(), 0)
      assert.equal(await trigger.evaluate(el => el === document.activeElement), true)
      await trigger.click(); await page.mouse.click(width - 170, 400)
      assert.equal(await page.getByRole('menu').count(), 0)
      disableVideo = true
      await trigger.click()
      await page.waitForFunction(() => [...document.querySelectorAll('[role="menuitemradio"]')].some(el => el.textContent.includes('H3') && el.disabled))
      assert.equal(await page.getByRole('menuitemradio', { name: /H3 生视频/ }).isDisabled(), true)
      await page.keyboard.press('Escape')
      failCatalog = true
      await trigger.click()
      await page.getByRole('alert').filter({ hasText: '工具列表加载失败' }).waitFor()
      failCatalog = false
      await page.getByRole('button', { name: '重试', exact: true }).click()
      await page.getByRole('alert').waitFor({ state: 'hidden' })
      await page.keyboard.press('Escape')
      await page.goto(`${base}/?view=chat&conversation_id=desktop_default`)
      assert.equal(await page.getByRole('button', { name: /^切换工具/ }).count(), 0)
      await context.close()
    }
    assert.deepEqual(errors, [])
    const report = { options: 4, payloads: captures.map(p => p.creation_workflow_id), persistence: true, keyboard: true, outsideClick: true, unavailable: true, retry: true, companionHidden: true, themes: 2, errors }
    await fs.writeFile(path.join(output, 'tool-picker-ui.json'), JSON.stringify(report, null, 2))
    console.log(JSON.stringify(report))
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
