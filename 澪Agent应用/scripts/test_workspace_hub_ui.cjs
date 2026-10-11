const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const output = process.env.MIO_UI_EVIDENCE || require('node:path').join(require('node:os').tmpdir(),'mio-workspace-hub-ui')
const desktop = process.env.MIO_UI_BASE || 'http://127.0.0.1:18431'
const mobile = process.env.MIO_MOBILE_UI_BASE || 'https://127.0.0.1:18443'
async function swipe(page, cdp, from, to) {
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [from] })
  for (let step = 1; step <= 20; step++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: from.x + (to.x - from.x) * step / 20, y: from.y + (to.y - from.y) * step / 20 }] })
    await page.waitForTimeout(20)
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] })
}
async function run() {
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const errors = [], results = []
  try {
    for (const theme of ['xuan', 'ink']) {
      const context = await browser.newContext({ viewport: { width: 1280, height: 620 } })
      await context.addInitScript(theme => { localStorage.setItem('mio_app_preferences', JSON.stringify({ theme, font_size: 'medium' })); localStorage.setItem('mio_agent_active_view', 'projects') }, theme)
      await context.route('**/*', async route => {
        const p = new URL(route.request().url()).pathname
        if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p) || p.startsWith('/api/workspace') || p.startsWith('/api/agent/work') || p.startsWith('/api/artifacts') || p.startsWith('/api/mobile-portal')) return route.continue()
        let value = {}
        if (p === '/api/onboarding/status') value = { completed: true }
        else if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversation_id: 'desktop_agent_ui', conversations: [], messages: [], models: [], diaries: [], qq: {}, settings: {} }
        else if (p === '/api/agent/conversations' || p === '/api/agent/messages' || /^\/(diaries|reviews|weekly)$/.test(p)) value = []
        else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
        else if (p.includes('startup-greeting')) value = { enabled: false }
        else if (p.includes('/profile')) value = { profile: {}, settings: {} }
        return route.fulfill({ json: value })
      })
      const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message))
      await page.goto(desktop + '/agent-app/?workspace=agent')
      await page.getByRole('heading', { name: '项目工作台' }).waitFor()
      await page.getByLabel('当前项目').selectOption({ label: '澪的创作计划' })
      await page.getByText('请选择一版封面，或告诉我需要调整的方向。').waitFor()
      const desktopScrollBefore = await page.locator('.workspace-hub').evaluate(el => ({
        clientHeight: el.clientHeight,
        scrollHeight: el.scrollHeight,
        scrollTop: el.scrollTop,
      }))
      assert.ok(desktopScrollBefore.scrollHeight > desktopScrollBefore.clientHeight + 1, `desktop workspace should scroll vertically: ${JSON.stringify(desktopScrollBefore)}`)
      const desktopBox = await page.locator('.workspace-hub').boundingBox()
      await page.mouse.move(desktopBox.x + desktopBox.width - 18, desktopBox.y + Math.min(160, desktopBox.height / 2))
      await page.mouse.wheel(0, 420)
      await page.waitForTimeout(80)
      const desktopWheelDebug = await page.locator('.workspace-hub').evaluate(el => {
        const r = el.getBoundingClientRect(); const point = document.elementFromPoint(r.right - 18, r.top + Math.min(160, r.height / 2))
        return { scrollTop: el.scrollTop, clientHeight: el.clientHeight, scrollHeight: el.scrollHeight, overflowY: getComputedStyle(el).overflowY, point: point?.className || point?.tagName }
      })
      assert.ok(desktopWheelDebug.scrollTop > 0, `desktop workspace wheel did not scroll: ${JSON.stringify(desktopWheelDebug)}`)
      await page.locator('.workspace-hub').evaluate(el => { el.scrollTop = el.scrollHeight })
      const desktopScrollAfter = await page.locator('.workspace-hub').evaluate(el => el.scrollTop)
      assert.ok(desktopScrollAfter > 0, `desktop workspace did not move vertically: ${desktopScrollAfter}`)
      await page.screenshot({ path: path.join(output, `desktop-tasks-${theme}.png`), fullPage: true })
      await page.getByRole('button', { name: '成果', exact: true }).click()
      await page.getByRole('button', { name: '选用', exact: true }).first().click()
      await page.getByText('已选定。继续给任务提要求时，澪会看到这项选择').waitFor()
      for (const box of await page.getByRole('checkbox', { name: '对比' }).all()) await box.check()
      await page.getByRole('heading', { name: '版本对比' }).waitFor()
      await page.screenshot({ path: path.join(output, `desktop-results-${theme}.png`), fullPage: true })
      await page.getByRole('button', { name: '资料', exact: true }).click()
      await page.getByLabel('上传资料').setInputFiles({ name: `ui-${theme}.md`, mimeType: 'text/markdown', buffer: Buffer.from('秋日封面\n海洋蓝色与暖色两套视觉方向。') })
      await page.getByText('资料已保存并索引').waitFor()
      await page.getByLabel('检索项目资料').fill('海洋蓝色')
      await page.getByRole('button', { name: '搜索原文' }).click()
      await page.locator('.hub-form article a').first().waitFor()
      await page.getByRole('button', { name: '流程', exact: true }).click()
      await page.getByLabel('名称', { exact: true }).fill(`封面到视频-${theme}`)
      await page.getByLabel('目标', { exact: true }).fill('先给我候选封面，选定后制作短视频')
      await page.getByLabel('参考指导').fill('保留原图；根据新结果调整步骤')
      await page.getByRole('button', { name: '保存流程', exact: true }).click()
      await page.getByText('流程已保存，澪可以根据实际结果调整步骤').waitFor()
      await page.getByRole('button', { name: '自动任务', exact: true }).click()
      await page.getByLabel('名称', { exact: true }).fill(`每日整理-${theme}`)
      await page.getByLabel('触发后要完成什么').fill('整理项目进展')
      await page.getByRole('button', { name: '保存为草稿' }).click()
      await page.getByText('已保存为停用状态，点击启用后开始运行').waitFor()
      await page.getByRole('button', { name: '手机连接', exact: true }).click()
      await page.getByRole('heading', { name: '把工作台带到手机' }).waitFor()
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
      assert.equal(overflow, false)
      results.push({ theme, project: true, selection: true, comparison: true, upload: true, retrieval: true, template: true, automationDraft: true, overflow, verticalScroll: true })
      await context.close()
    }
    const context = await browser.newContext({ viewport: { width: 390, height: 640 }, isMobile: true, hasTouch: true, ignoreHTTPSErrors: true })
    const pairingResponse = await context.request.post(desktop + '/api/mobile-portal/pairing', { data: {} })
    assert.ok(pairingResponse.ok())
    const pairingCode = (await pairingResponse.json()).code
    const page = await context.newPage(); page.on('pageerror', e => errors.push(e.message))
    await page.goto(mobile + '/mobile/')
    await page.getByLabel('八位配对码').fill(pairingCode)
    await page.getByRole('button', { name: '配对并进入工作台' }).click()
    await page.getByRole('heading', { name: '澪 · 随身工作台' }).waitFor()
    await page.getByLabel('当前项目').selectOption({ label: '澪的创作计划' })
    await page.getByText('请选择一版封面，或告诉我需要调整的方向。').waitFor()
    assert.equal(await page.locator('.workspace-hub').evaluate(el => getComputedStyle(el).touchAction), 'manipulation')
    const cdp = await context.newCDPSession(page)
    const mobileScrollBefore = await page.evaluate(() => ({ height: innerHeight, scrollHeight: document.scrollingElement.scrollHeight }))
    assert.ok(mobileScrollBefore.scrollHeight > mobileScrollBefore.height + 1)
    await swipe(page, cdp, { x: 370, y: 530 }, { x: 370, y: 130 })
    await page.waitForFunction(() => scrollY > 0)
    const mobileScrollAfter = await page.evaluate(() => scrollY)
    await page.evaluate(() => scrollTo(0, 0))
    await page.waitForTimeout(500)
    // On a narrow phone the last tab must be reachable with a horizontal swipe.
    await page.setViewportSize({ width: 280, height: 640 })
    await page.waitForTimeout(300)
    const tabs = page.getByLabel('项目功能'), tabsBox = await tabs.boundingBox()
    assert.ok(await tabs.evaluate(el => el.scrollWidth > el.clientWidth))
    await swipe(page, cdp, { x: tabsBox.x + tabsBox.width - 15, y: tabsBox.y + tabsBox.height / 2 }, { x: tabsBox.x + 30, y: tabsBox.y + tabsBox.height / 2 })
    await page.waitForFunction(() => document.querySelector('.hub-tabs').scrollLeft > 0)
    await page.setViewportSize({ width: 390, height: 640 })
    await page.getByRole('button').filter({ hasText: '做三版秋日封面' }).click()
    await page.getByLabel('继续提要求').fill('用第一版，保留封面留白')
    await page.getByRole('button', { name: '发送补充' }).click()
    await page.getByText('补充要求已加入原任务').waitFor()
    await page.getByRole('button', { name: '暂停', exact: true }).click()
    await page.getByText('已暂停', { exact: true }).waitFor()
    await page.screenshot({ path: path.join(output, 'mobile-tasks.png'), fullPage: true })
    await page.getByRole('button', { name: '成果', exact: true }).click()
    await page.getByRole('button', { name: '选用', exact: true }).first().waitFor()
    await page.screenshot({ path: path.join(output, 'mobile-results.png'), fullPage: true })
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
    await page.reload(); await page.getByRole('heading', { name: '澪 · 随身工作台' }).waitFor()
    await page.route('**/api/mobile/logout', route => route.fulfill({ status: 503, json: { detail: '测试断开失败，请重试' } }))
    await page.getByRole('button', { name: '断开此设备' }).click()
    await page.getByRole('alert').filter({ hasText: '测试断开失败' }).waitFor()
    await page.getByRole('heading', { name: '澪 · 随身工作台' }).waitFor()
    await page.unroute('**/api/mobile/logout')
    await page.getByRole('button', { name: '断开此设备' }).click()
    await page.getByRole('heading', { name: '连接你的澪' }).waitFor()
    await context.close()
    assert.deepEqual(errors, [])
    await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ desktop: results, mobile: { paired: true, sharedProject: true, followup: true, pause: true, preview: true, reloadSession: true, logout: true, logoutErrorVisible: true, verticalTouchScroll: true, horizontalTabSwipe: true, mobileScrollBefore, mobileScrollAfter }, errors }, null, 2))
    console.log('Workspace desktop and mobile UI checks passed')
  } finally { await browser.close() }
}
run().catch(error => { console.error(error); process.exitCode = 1 })
