const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs/promises')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const browser = await chromium.launch({ headless: true, channel: process.env.MIO_UI_BROWSER || 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1152, height: 1080 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || 'ui-evidence')
  await fs.mkdir(output, { recursive: true })
  let limits = { model_calls: 8, tool_calls: 20, seconds: 180, cost_yuan: 2 }
  let roots = ['D:\\Agent-output']
  let failBudgetLoad = false
  let failBudgetSave = false
  let budgetWrites = 0
  let rootsWrites = 0
  const task = { id: 'ui-test', original_goal: '整理项目文档', snapshot: { goal: '整理项目文档', plan: ['读取资料', '保存结果'] }, observations: [], status: 'paused', budget_yuan: 2, spent_yuan: 0 }
  const workflows = [{ id: 'image', label: '图片工作流', filename: '图片.json', media_type: 'image', available: true }, { id: 'video', label: '视频工作流', filename: '视频.json', media_type: 'video', available: true }]
  await page.route('**/api/**', async route => {
    const req = route.request()
    const url = new URL(req.url())
    let data = {}
    let status = 200
    if (url.pathname === '/api/agent/work') {
      if (failBudgetLoad && url.searchParams.get('limit') === '1') { status = 503; data = { detail: '预算读取暂不可用' } }
      else data = { tasks: [task], limits }
    } else if (url.pathname === '/api/agent/work/limits') {
      budgetWrites++
      if (failBudgetSave) { status = 500; data = { detail: '保存失败' } }
      else { limits = req.postDataJSON(); data = limits }
    } else if (url.pathname === '/api/agent/work/file-roots') {
      if (req.method() === 'PUT') { rootsWrites++; roots = ['D:\\Agent-output', ...req.postDataJSON().paths] }
      data = { roots, output_root: roots[0] }
    } else if (url.pathname === '/api/creation/configuration') {
      data = { environment: { comfyui_root: 'D:\\AI\\ComfyUI', comfyui_base_url: 'http://127.0.0.1:8188' }, defaults: { image_workflow_id: 'image', video_workflow_id: 'video' } }
    } else if (url.pathname.endsWith('/preflight')) {
      data = { ok: false, workflows: workflows.map(item => ({ ...item, status: 'needs_setup', errors: ['缺少模型，需处理'] })) }
    } else if (url.pathname.endsWith('/bootstrap')) {
      data = { comfyui: { reachable: false, root: 'D:\\AI\\ComfyUI', base_url: 'http://127.0.0.1:8188' }, workflows, presets: [], assets: [], remote_providers: [], jobs: [] }
    }
    await route.fulfill({ status, json: data })
  })
  const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:1421'
  const open = view => page.goto(`${base}/scripts/agent-settings-preview.html?view=${view}`)
  const category = name => page.getByRole('navigation', { name: 'Agent 设置分类' }).getByRole('button', { name, exact: true }).click()
  try {
    await open('settings')
    await page.getByLabel('Agent 模型').waitFor()
    await category('任务执行')
    await page.getByRole('button', { name: '保存预算', exact: true }).waitFor()
    assert.equal(await page.getByLabel('启用每轮执行限制').isChecked(), false)
    assert.equal(await page.getByLabel('执行时限（秒）').count(), 0)
    await page.getByLabel('启用每轮执行限制').check()
    await page.getByLabel('执行时限（秒）').fill('240')
    await category('文件权限')
    await category('任务执行')
    assert.equal(await page.getByLabel('执行时限（秒）').inputValue(), '240')
    await page.getByRole('button', { name: '保存预算', exact: true }).click()
    await page.getByText('预算已保存。', { exact: true }).waitFor()
    assert.equal(limits.seconds, 240)
    assert.equal(budgetWrites, 1)
    await page.screenshot({ path: path.join(output, 'settings-budget-desktop.png') })
    await page.getByLabel('模型调用次数').fill('25')
    await page.getByRole('button', { name: '保存预算', exact: true }).click()
    assert.equal(budgetWrites, 1, 'invalid budgets must not be submitted')
    await page.getByLabel('模型调用次数').fill('8')
    failBudgetSave = true
    await page.getByRole('button', { name: '保存预算', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: '保存失败' }).waitFor()
    assert.equal(await page.getByText('预算已保存。', { exact: true }).count(), 0)
    failBudgetSave = false
    await category('文件权限')
    await page.getByText('手动填写路径', { exact: true }).click()
    await page.getByLabel('资料目录', { exact: true }).fill('D:\\Documents')
    await page.getByRole('button', { name: '授权读取', exact: true }).click()
    await page.getByText('D:\\Documents', { exact: true }).waitFor()
    assert.equal(rootsWrites, 1)
    await page.getByRole('button', { name: '撤销目录授权' }).click()
    await page.waitForFunction(() => !document.querySelector('.agent-root-list').textContent.includes('Documents'))
    assert.deepEqual(roots, ['D:\\Agent-output'])
    await category('工作流')
    await page.getByRole('button', { name: '检查依赖' }).click()
    await page.getByText('缺少模型，需处理').first().waitFor()
    assert.equal(await page.getByText('缺少模型，需处理', { exact: true }).count(), 2)
    await category('界面显示')
    await page.getByLabel('显示右侧状态栏', { exact: true }).uncheck()
    assert.equal(await page.getByLabel('悬停展开右侧状态栏').isDisabled(), true)
    for (const size of [{ width: 1152, height: 1080 }, { width: 800, height: 600 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(size)
      for (const name of ['模型与推理', '任务执行', '文件权限', '创作环境', '工作流', '界面显示']) {
        await category(name)
        const overflowing = await page.evaluate(() => [...document.querySelectorAll('.agent-settings-body, .settings-window-navigation, .integrated-workspace')].some(el => el.scrollWidth > el.clientWidth + 2))
        assert.equal(overflowing, false, `${name} overflow at ${size.width}`)
      }
      await category('文件权限')
      await page.screenshot({ path: path.join(output, `settings-files-${size.width}.png`) })
    }
    await page.setViewportSize({ width: 1152, height: 1080 })
    await open('settings')
    await category('任务执行')
    assert.equal(await page.getByLabel('执行时限（秒）').inputValue(), '240')
    failBudgetLoad = true
    await open('settings')
    await category('任务执行')
    await page.getByText('预算读取暂不可用', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: '保存预算', exact: true }).isDisabled(), true)
    failBudgetLoad = false
    await page.getByRole('button', { name: '重新读取预算' }).click()
    await page.waitForFunction(() => !document.querySelector('.agent-task-settings fieldset').disabled)
    await open('tasks')
    await page.getByText('整理项目文档', { exact: true }).waitFor()
    assert.equal(await page.locator('input[type=number]').count(), 0)
    assert.equal(await page.getByText('文件访问目录', { exact: true }).count(), 0)
    await page.getByRole('button', { name: '任务设置', exact: true }).click()
    assert.equal(await page.evaluate(() => window.lastNavigation), 'settings')
    await page.screenshot({ path: path.join(output, 'tasks-desktop.png') })
    await open('home')
    await page.getByText('整理项目文档', { exact: true }).waitFor()
    await page.getByRole('button', { name: '全部任务' }).click()
    assert.equal(await page.evaluate(() => window.lastNavigation), 'tasks')
    await page.screenshot({ path: path.join(output, 'home-desktop.png') })
    await open('creation')
    await page.getByRole('heading', { name: '图片创作' }).waitFor()
    await page.getByText('创作预设', { exact: true }).click()
    await page.getByPlaceholder('预设名称').waitFor()
    await page.screenshot({ path: path.join(output, 'creation-desktop.png') })
    assert.deepEqual(errors, [])
    console.log('Agent UI checks passed: categories, draft preservation, save/readback, validation, failure/retry, directory grant/revoke, navigation, desktop/mobile overflow.')
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
