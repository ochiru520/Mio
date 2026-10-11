const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18466'
const output = process.env.MIO_UI_EVIDENCE || require('node:path').join(require('node:os').tmpdir(),'mio-open-settings-projects-ui')
;(async () => {
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1252, height: 900 } })
  const errors = [], checks = []
  page.on('pageerror', error => errors.push(error.message))
  try {
    for (const theme of ['rose', 'xuan', 'ink']) {
      await page.goto(base + '/scripts/character-presets-preview.html?theme=' + theme)
      for (const width of [1252, 800, 390]) {
        await page.setViewportSize({ width, height: 900 })
        const geometry = await page.evaluate(() => {
          const panel = document.querySelector('.character-card-panel')
          const row = panel.querySelector('.character-card-grid > label')
          return { border: getComputedStyle(panel).borderTopWidth, background: getComputedStyle(row).backgroundColor,
            overflow: [...document.querySelectorAll('main,.character-card-panel,.character-card-grid,label,input:not([type=file]),textarea,select')].some(el => el.scrollWidth > el.clientWidth + 2) }
        })
        assert.equal(geometry.border, '0px'); assert.equal(geometry.background, 'rgba(0, 0, 0, 0)'); assert.equal(geometry.overflow, false)
        await page.screenshot({ path: `${output}/settings-${theme}-${width}.png`, fullPage: true })
      }
    }
    checks.push('角色卡开放分区，三主题三宽度无横向溢出')
    await page.goto(base + '/scripts/character-presets-preview.html')
    const core = page.getByRole('textbox', { name: '一句话人设', exact: true })
    await page.getByLabel('选择角色预设').selectOption('cheerful-friend')
    page.once('dialog', dialog => dialog.dismiss()); await page.getByRole('button', { name: '套用预设', exact: true }).click()
    assert.equal(await core.inputValue(), '自然、友好的伙伴。')
    page.once('dialog', dialog => dialog.accept()); await page.getByRole('button', { name: '套用预设', exact: true }).click()
    assert((await core.inputValue()).includes('开朗的元气朋友'))
    checks.push('角色预设取消与套用仍有效')
    const projects = [{ id: 'a', name: '首个项目', directory: 'D:/test/a' }, { id: 'b', name: '当前项目', directory: 'D:/test/b' }]
    const conversations = [{ id: 'desktop_agent_ui', title: '当前对话', kind: 'agent' }]
    const links = { projects: { desktop_agent_ui: 'b' }, modes: { desktop_agent_ui: 'work' } }
    const messages = []
    let deletes = 0, creates = 0
    await page.addInitScript(() => { localStorage.setItem('mio_agent_active_view', 'chat'); localStorage.setItem('mio_agent_conversation_id', 'desktop_agent_ui'); localStorage.setItem('mio_app_preferences', JSON.stringify({ theme: 'rose' })) })
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), p = url.pathname
      if (p === '/api/agent/avatar' || p === '/api/settings/avatar') return route.fulfill({ path: require('node:path').resolve('public/mio-avatar.png'), contentType: 'image/png' })
      if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
      let data = {}
      if (p === '/api/onboarding/status') data = { completed: true }
      else if (p === '/api/agent/bootstrap') data = { onboarding: { completed: true }, conversation_id: 'desktop_agent_ui', conversations, models: [], messages, diaries: [], settings: {}, qq: {}, model: {}, provider_presets: [] }
      else if (p === '/api/agent/conversations') data = conversations
      else if (p === '/api/agent/messages') data = messages
      else if (p === '/api/agent/models/catalog') data = { models: [], provider_presets: [], hidden_model_providers: [] }
      else if (p === '/api/workspace') data = { projects, tasks: [] }
      else if (p === '/api/workspace/conversations') data = links
      else if (p === '/api/workspace/projects' && req.method() === 'POST') { creates++; data = { ...req.postDataJSON(), id: 'new' }; projects.push(data) }
      else if (p === '/api/workspace/projects/b' && req.method() === 'DELETE') { deletes++; projects.splice(projects.findIndex(item => item.id === 'b'), 1); delete links.projects.desktop_agent_ui; data = { removed: true } }
      else if (/\/projects\/[^/]+\/conversations\//.test(p)) { links.projects.desktop_agent_ui = p.split('/')[4]; data = { ok: true } }
      else if (p === '/api/workspace/pending') data = { items: [], total: 0 }
      else if (p === '/api/artifacts') data = { artifacts: [] }
      else if (p === '/api/agent/work') data = { tasks: [], limits: {} }
      else if (p === '/api/settings/profile') data = { profile: { identity: { name: 'Mio', core: '自然、友好的伙伴。', age_feel: '' }, speaking_style: { tone: '温和自然', bubble_style: '简洁完整', avoid: [] }, preferences: { user_address: '你', relationship_distance: '友好的伙伴', custom_notes: [] }, behavior: { initiative: '按你的节奏相处' } } }
      else if (p === '/api/settings/runtime') data = { settings: { chat_context_max_chars: 18000, chat_history_limit: 100, chat_recent_keep_messages: 12, chat_raw_history_limit: 200, chat_follow_up_capture_seconds: 3, chat_follow_up_max_capture_count: 3 } }
      else if (p === '/api/companion/status') data = { pet: { settings: {} }, screen: {}, settings: {} }
      else if (p.includes('startup-greeting')) data = { enabled: false }
      else if (/^\/(diaries|reviews|weekly)$/.test(p)) data = []
      await route.fulfill({ json: data })
    })
    await page.setViewportSize({ width: 1252, height: 900 })
    await page.goto(base + '/?workspace=agent&conversation_id=desktop_agent_ui')
    await page.getByLabel('当前对话的菜单').waitFor()
    await page.getByRole('button', { name: '选择项目', exact: true }).click()
    assert.equal(await page.locator('.agent-project-popover input').count(), 1)
    assert.equal(await page.locator('.agent-project-popover form').count(), 0)
    await page.getByRole('searchbox', { name: '搜索项目' }).fill('当前')
    assert.equal(await page.locator('.agent-project-options button').count(), 1)
    await page.screenshot({ path: output + '/project-picker.png' })
    await page.locator('.agent-project-popover').getByRole('button', { name: '新建项目', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '新建项目', exact: true })
    await dialog.waitFor(); assert.equal(await page.locator('.agent-project-popover').count(), 0)
    await dialog.getByRole('button', { name: '新建文件夹', exact: true }).click()
    await dialog.getByRole('textbox', { name: '项目名称', exact: true }).fill('新增项目')
    await dialog.getByRole('textbox', { name: '新文件夹的父目录', exact: true }).fill('D:/test')
    await page.screenshot({ path: output + '/project-create.png' })
    await dialog.getByRole('button', { name: '创建项目', exact: true }).click()
    await dialog.waitFor({ state: 'hidden' }); assert.equal(creates, 1)
    checks.push('搜索项目为纯列表，创建独立弹窗且真实POST连接')
    await page.getByLabel('当前对话的菜单').click()
    const menu = page.getByRole('menu', { name: '对话菜单', exact: true })
    assert.equal(await menu.getByRole('menuitem').count(), 5)
    assert.equal(await menu.getByText('从当前对话创建分支', { exact: true }).count(), 0)
    await page.screenshot({ path: output + '/conversation-menu.png' }); await page.keyboard.press('Escape')
    await page.locator('.agent-project-folder-heading').filter({ hasText: '当前项目' }).click({ button: 'right' })
    page.once('dialog', dialog => dialog.dismiss()); await page.getByRole('menuitem', { name: '删除项目', exact: true }).click(); assert.equal(deletes, 0)
    await page.getByLabel('当前项目的项目菜单', { exact: true }).click()
    page.once('dialog', dialog => dialog.accept()); await page.getByRole('menuitem', { name: '删除项目', exact: true }).click()
    await page.getByText('项目已删除，对话和本机文件已保留。', { exact: true }).waitFor()
    assert.equal(deletes, 1); assert.equal(await page.getByLabel('当前项目的项目菜单', { exact: true }).count(), 0)
    await page.reload(); await page.getByLabel('当前对话的菜单').waitFor()
    assert.equal(await page.getByLabel('当前项目的项目菜单', { exact: true }).count(), 0)
    checks.push('精简菜单、项目右键/三点删除、取消不删除、删除后刷新与重载')
    for (const width of [800, 390]) {
      await page.setViewportSize({ width, height: 900 })
      await page.getByRole('button', { name: '选择项目', exact: true }).click()
      assert.equal(await page.locator('.agent-project-popover').evaluate(el => el.scrollWidth > el.clientWidth + 2), false)
      await page.screenshot({ path: `${output}/project-picker-${width}.png` }); await page.keyboard.press('Escape')
    }
    await page.setViewportSize({ width: 1252, height: 900 })
    await page.getByRole('button', { name: '我的空间', exact: true }).click()
    await page.locator('.agent-personal-menu').getByRole('button', { name: '人设与表达', exact: true }).click()
    await page.getByRole('textbox', { name: '一句话人设', exact: true }).waitFor()
    await page.getByText('高级编辑', { exact: true }).click()
    for (const width of [1252, 800, 390]) {
      await page.setViewportSize({ width, height: 900 })
      assert.equal(await page.locator('.mio-settings-content').evaluate(el => el.scrollWidth > el.clientWidth + 2), false)
      await page.locator('.character-card-panel').evaluate(el => el.scrollIntoView({ block: 'start' }))
      await page.screenshot({ path: `${output}/profile-full-${width}.png` })
      assert.equal(await page.locator('.settings-form-grid > label').first().evaluate(el => getComputedStyle(el).backgroundColor), 'rgba(0, 0, 0, 0)')
    }
    checks.push('真实设置外壳与高级表单去套框，三种宽度滚动正常')
    assert.deepEqual(errors, [])
    await fs.writeFile(output + '/ui-report.json', JSON.stringify({ passed: true, checks, pageErrors: errors }, null, 2))
    console.log('Open settings and project UI checks passed')
  } finally { await browser.close() }
})().catch(error => { console.error(error); process.exitCode = 1 })
