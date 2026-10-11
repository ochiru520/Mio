const assert = require('node:assert/strict')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18435'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

async function run() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 620 } })
    await context.addInitScript(() => localStorage.setItem('mio_agent_active_view', 'projects'))
    let projectReads = 0, slowReadStarted = false, slowReadFinished = false, slowProject = false
    let goalStarted = false, submittedGoal
    const cancelledRequests = []
    const projects = [{ id: 'a', name: '项目甲' }, { id: 'b', name: '项目乙' }]
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url()), p = url.pathname
      if (/\/api\/(mobile|workspace\/music|workspace\/stickers|workspace\/moments)/.test(p) || /\/api\/workspace\/projects\/[^/]+\/moments/.test(p)) cancelledRequests.push(p)
      if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
      let value = {}
      if (p === '/api/onboarding/status') value = { completed: true }
      else if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversations: [], messages: [], models: [], diaries: [], qq: {}, settings: {} }
      else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
      else if (p === '/api/workspace') value = { projects, tasks: [], templates: [{ id: 'tpl', project_id: 'a', name: '甲的流程', goal: '甲的目标', instructions: '' }], routines: [], browser_pending: [] }
      else if (p === '/api/workspace/tasks') { const id = url.searchParams.get('project_id'); value = { tasks: id ? [{ id: `task-${id}`, status: 'paused', original_goal: `${id === 'a' ? '甲' : '乙'}的任务`, snapshot: {} }] : [], has_more: false } }
      else if (/^\/api\/workspace\/projects\/[ab]$/.test(p)) {
        const id = p.at(-1)
        projectReads++
        if (id === 'a' && slowProject) {
          slowProject = false; slowReadStarted = true
          await delay(1300); slowReadFinished = true
        }
        value = { project: projects.find(item => item.id === id), tasks: [{ id: `task-${id}`, status: 'paused', original_goal: `${id === 'a' ? '甲' : '乙'}的任务`, snapshot: {} }], browser: { hosts: [`${id}.example.com`], allow_public: false }, artifacts: [], jobs: [], batches: [], assemblies: [], documents: [], notes: [] }
      } else if (p === '/api/workspace/goals') {
        submittedGoal = request.postDataJSON(); goalStarted = true
        await delay(400); value = { id: 'new-task' }
      } else if (p.includes('startup-greeting')) value = { enabled: false }
      else if (p.includes('/profile')) value = { profile: {}, settings: {} }
      else if (p === '/api/agent/conversations' || p === '/api/agent/messages' || /^\/(diaries|reviews|weekly)$/.test(p)) value = []
      await route.fulfill({ json: value })
    })
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.goto(base + '/agent-app/?workspace=agent')
    const selector = page.getByLabel('当前项目')
    await selector.selectOption('a')
    await page.getByText('甲的任务', { exact: true }).waitFor()
    await selector.selectOption('')
    assert.equal(await page.getByLabel('选择可复用流程').locator('option[value="tpl"]').count(), 0, 'project template offered without a project')
    await page.getByRole('button', { name: '流程', exact: true }).click()
    await page.getByRole('button', { name: '用于新任务', exact: true }).click()
    assert.equal(await selector.inputValue(), 'a', 'using project template should select its owning project')
    assert.equal(await page.getByLabel('选择可复用流程').inputValue(), 'tpl')
    await page.getByRole('button', { name: '流程', exact: true }).click()
    await page.getByRole('button', { name: '编辑', exact: true }).click()
    assert.equal(await page.getByLabel('名称', { exact: true }).inputValue(), '甲的流程')
    await selector.selectOption('b')
    await page.waitForFunction(() => document.querySelector('.workspace-hub').getAttribute('aria-busy') === 'false')
    assert.equal(await page.getByLabel('名称', { exact: true }).inputValue(), '', 'project switch retained another project template draft')
    await page.getByLabel('项目功能').getByRole('button', { name: '任务', exact: true }).click()
    await selector.selectOption('a')
    await page.getByText('甲的任务', { exact: true }).waitFor()
    // Hold a background poll while a newer selection finishes.
    slowProject = true
    await page.waitForFunction(() => document.querySelector('.workspace-hub').getAttribute('aria-busy') === 'false')
    for (let i = 0; i < 70 && !slowReadStarted; i++) await delay(100)
    assert.ok(slowReadStarted, 'background project poll did not start')
    await selector.selectOption('b')
    await page.getByText('乙的任务', { exact: true }).waitFor()
    for (let i = 0; i < 30 && !slowReadFinished; i++) await delay(100)
    await page.waitForTimeout(150)
    assert.equal(await selector.inputValue(), 'b')
    assert.equal(await page.getByText('甲的任务', { exact: true }).count(), 0, 'late project response replaced selected project')
    await page.getByLabel('任务目标').fill('第一次要求')
    await page.getByRole('button', { name: '开始任务', exact: true }).click()
    for (let i = 0; i < 20 && !goalStarted; i++) await delay(20)
    assert.ok(goalStarted)
    assert.ok(await selector.isDisabled(), 'project selection should be locked while submitting')
    await page.getByLabel('任务目标').fill('下一次要求')
    await page.getByText('任务已交给澪，完成后会保留在这里').waitFor()
    assert.equal(submittedGoal.project_id, 'b')
    assert.equal(submittedGoal.goal, '第一次要求')
    assert.equal(await page.getByLabel('任务目标').inputValue(), '下一次要求', 'successful submission discarded a newer draft')
    await page.getByRole('button', { name: '刷新', exact: true }).click()
    for (const name of ['音乐', '贴纸', '动态', '手机连接']) {
      assert.equal(await page.getByRole('button', { name, exact: true }).count(), 0)
    }
    await page.getByRole('button', { name: '浏览器', exact: true }).click()
    await page.getByRole('heading', { name: '项目专用浏览器' }).waitFor()
    await page.waitForTimeout(5200)
    assert.deepEqual(cancelledRequests, [], 'workspace still requests cancelled feature APIs')
    assert.deepEqual(errors, [])
    console.log(JSON.stringify({ projectDraftIsolation: true, stalePollIgnored: true, submissionContextStable: true, newerDraftPreserved: true, cancelledTabsAbsent: true, cancelledRequests, browserTabAvailable: true, projectReads, errors }))
    await context.close()
  } finally { await browser.close() }
}
run().catch(error => { console.error(error); process.exitCode = 1 })
