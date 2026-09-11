const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const evidence = process.env.MIO_UI_EVIDENCE
  await fs.mkdir(evidence, { recursive: true })
  let environment = { comfyui_root: 'D:\\AI\\ComfyUI', comfyui_base_url: 'http://127.0.0.1:8188' }
  let defaults = { image_workflow_id: 'image', video_workflow_id: 'video' }
  let workflows = [{ id: 'image', label: '内置图片', media_type: 'image', available: true }, { id: 'video', label: '内置视频', media_type: 'video', available: true }]
  let scans = 0, imports = 0, writes = 0, failSave = false, submitted
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url())
    let data = {}, status = 200
    if (url.pathname.endsWith('/configuration')) data = { environment, defaults }
    else if (url.pathname.endsWith('/configuration/environment')) {
      writes++
      if (failSave) { status = 400; data = { detail: '目录验证失败' } }
      else { environment = req.postDataJSON(); data = { environment } }
    } else if (url.pathname.endsWith('/configuration/workflows')) { defaults = req.postDataJSON(); data = { defaults } }
    else if (url.pathname.endsWith('/discover')) { scans++; data = { candidates: [{ root: 'E:\\ComfyUI', installed: true, launchable: true }] } }
    else if (url.pathname.endsWith('/bootstrap')) data = { comfyui: { reachable: false, root: environment.comfyui_root, base_url: environment.comfyui_base_url }, workflows, defaults, assets: [], jobs: [], presets: [], remote_providers: [] }
    else if (url.pathname.endsWith('/workflows/preview')) {
      const graph = req.postDataJSON().prompt
      if (graph.nodes?.length === 0) { status = 400; data = { detail: '工作流没有可执行节点。' } }
      else data = { prompt: graph.nodes ? { '1': { class_type: 'TextImage', inputs: { text: 'test', width: 512 } }, '2': { class_type: 'SaveImage', inputs: { images: ['1', 0], filename_prefix: 'old' } } } : graph, media_type: 'image', bindings: { prompt: [{ node_id: '1', input: 'text' }], width: [{ node_id: '1', input: 'width' }] }, notes: [] }
    }
    else if (url.pathname.endsWith('/workflows') && req.method() === 'POST') {
      imports++
      const payload = req.postDataJSON()
      assert.deepEqual(payload.bindings.prompt, [{ node_id: '1', input: 'text' }])
      const workflow = { id: 'custom-' + '1'.repeat(32), label: payload.label, media_type: payload.media_type, custom: true, available: true, bindings: payload.bindings, defaults: { width: 512, height: 512 } }
      workflows.push(workflow); data = { workflow }
    } else if (url.pathname.includes('/workflows/') && req.method() === 'DELETE') { workflows = workflows.filter(item => item.id !== url.pathname.split('/').pop()); data = { ok: true } }
    else if (url.pathname.endsWith('/preflight')) data = { ok: true, workflows: workflows.map(item => ({ ...item, status: 'ready', errors: [] })) }
    else if (url.pathname.endsWith('/jobs') && req.method() === 'POST') { submitted = req.postDataJSON(); data = { job: { id: 'test-job', status: 'completed', outputs: [] } } }
    else if (url.pathname.endsWith('/file-roots')) data = { roots: ['D:\\Output'], output_root: 'D:\\Output' }
    else if (url.pathname.endsWith('/work')) data = { limits: { model_calls: 8, tool_calls: 20, seconds: 180, cost_yuan: 2 } }
    await route.fulfill({ status, json: data })
  })
  const base = process.env.MIO_UI_BASE
  const open = view => page.goto(`${base}/scripts/agent-settings-preview.html?view=${view}`)
  const category = name => page.getByRole('navigation', { name: 'Agent 设置分类' }).getByRole('button', { name, exact: true }).click()
  const save = () => page.getByRole('button', { name: '保存设置', exact: true }).click()
  try {
    await open('settings')
    await page.getByLabel('搜索 Agent 设置').fill('ComfyUI')
    assert.equal(await page.getByRole('navigation').getByRole('button').count(), 1)
    await category('创作环境')
    await page.getByText('已安装 ComfyUI', { exact: true }).waitFor()
    assert.equal(scans, 1)
    assert.equal(await page.locator('.integrated-sidebar').isVisible(), false)
    assert.equal(await page.locator('.integrated-right-rail').isVisible(), false)
    await page.getByRole('button', { name: '使用此目录' }).click()
    assert.equal(await page.getByLabel('本机目录', { exact: true }).inputValue(), 'E:\\ComfyUI')
    await page.getByRole('button', { name: '取消', exact: true }).click()
    assert.equal(await page.getByLabel('本机目录', { exact: true }).inputValue(), environment.comfyui_root)
    await page.getByRole('button', { name: '使用此目录' }).click()
    failSave = true; await save()
    await page.getByRole('alert').filter({ hasText: '目录验证失败' }).waitFor()
    assert.equal(environment.comfyui_root, 'D:\\AI\\ComfyUI')
    failSave = false; await save()
    await page.getByText('连接配置已保存。', { exact: true }).waitFor()
    assert.equal(writes, 2)
    assert.equal(environment.comfyui_root, 'E:\\ComfyUI')
    await page.screenshot({ path: path.join(evidence, 'custom-environment.png') })
    await page.getByLabel('搜索 Agent 设置').fill('')
    await category('工作流')
    await page.locator('input[type=file]').setInputFiles({ name: '错误画布.json', mimeType: 'application/json', buffer: Buffer.from('{"nodes":[]}') })
    await page.getByRole('alert').filter({ hasText: '没有可执行节点' }).waitFor()
    assert.equal(imports, 0)
    const graph = { nodes: [{ id: 1, type: 'TextImage' }, { id: 2, type: 'SaveImage' }], links: [] }
    await page.locator('input[type=file]').setInputFiles({ name: '自定义图片.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(graph)) })
    await page.getByText('高级参数绑定', { exact: true }).click()
    await page.getByLabel('正向提示词节点绑定', { exact: true }).selectOption(JSON.stringify(['1', 'text']))
    await page.getByLabel('宽度节点绑定', { exact: true }).selectOption(JSON.stringify(['1', 'width']))
    await page.screenshot({ path: path.join(evidence, 'custom-workflow-import.png') })
    await page.getByRole('button', { name: '保存工作流', exact: true }).click()
    await page.getByText('工作流已导入。', { exact: true }).waitFor()
    assert.equal(imports, 1)
    const customId = workflows.at(-1).id
    await page.getByLabel('图片生成', { exact: true }).selectOption(customId)
    await save()
    await page.getByText('默认工作流已保存。', { exact: true }).waitFor()
    assert.equal(defaults.image_workflow_id, customId)
    assert.equal(await page.getByRole('button', { name: '请先切换默认工作流' }).isDisabled(), true)
    await page.screenshot({ path: path.join(evidence, 'custom-workflows.png') })
    await category('模型与推理')
    await page.getByLabel('Agent 模型', { exact: true }).selectOption('auto')
    assert.equal(await page.getByLabel('推理强度', { exact: true }).inputValue(), 'auto')
    await save()
    await category('界面显示')
    await page.getByLabel('显示右侧状态栏', { exact: true }).uncheck()
    assert.equal(await page.evaluate(() => localStorage.getItem('mio_agent_right_sidebar_visible')), null)
    await save()
    assert.equal(await page.evaluate(() => localStorage.getItem('mio_agent_right_sidebar_visible')), 'false')
    await open('creation')
    await page.getByRole('heading', { name: '图片创作' }).waitFor()
    assert.equal(await page.getByLabel('工作流', { exact: true }).inputValue(), customId)
    await page.locator('.creation-form textarea').first().fill('生成测试图片')
    await page.getByRole('button', { name: '生成一份图片' }).click()
    await page.waitForFunction(() => document.querySelector('.creation-status-card').textContent.includes('已完成'))
    assert.equal(submitted.workflow_id, customId)
    await open('settings'); await category('工作流')
    await page.getByLabel('图片生成', { exact: true }).selectOption('image'); await save()
    await page.getByText('默认工作流已保存。', { exact: true }).waitFor()
    await page.getByRole('button', { name: '移除 自定义图片', exact: true }).click()
    await page.getByText('工作流已移除，历史任务保留。', { exact: true }).waitFor()
    assert.equal(workflows.length, 2)
    for (const width of [800, 390]) {
      await page.setViewportSize({ width, height: 844 })
      for (const name of ['创作环境', '工作流']) {
        await category(name)
        assert.equal(await page.evaluate(() => [...document.querySelectorAll('.agent-settings-body, .settings-window-navigation')].some(el => el.scrollWidth > el.clientWidth + 2)), false)
      }
      await page.screenshot({ path: path.join(evidence, `custom-settings-${width}.png`) })
    }
    assert.deepEqual(errors, [])
    console.log('Custom settings UI passed: search, full shell, automatic discovery, config failure/retry/cancel, import validation/mappings, defaults persistence, creation submission, remove, model/preferences, responsive layout.')
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
