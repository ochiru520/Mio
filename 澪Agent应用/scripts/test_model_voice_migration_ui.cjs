/* Only synthetic APIs: no model downloads, microphone or private user data. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const { createServer } = await import('vite')
  const server = await createServer({ server: { host: '127.0.0.1', port: 18489, strictPort: true } })
  await server.listen()
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || '../验收与归档/模型音色升级管理-20261103/ui')
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1152, height: 900 } })
  const errors = [], mutations = [], checks = []
  page.on('pageerror', error => errors.push(error.message))
  let chosen = 'qwen2.5vl:3b', installed = false, removed = false, failSwitch = true, voiceSelected = false
  const vision = () => ({ selected_model_id: chosen, recommended_model_id: 'qwen3.5:2b', migration_message: '已保留旧版模型，升级不会强制切换。', download: {}, models: [
    { id: 'qwen2.5vl:3b', label: 'Qwen2.5-VL 3B', selected: chosen === 'qwen2.5vl:3b', installed: !removed, legacy: true, verified: true, can_remove: true, size_bytes: 3 * 1024 ** 3, sources: [] },
    { id: 'qwen3.5:2b', label: 'Qwen3.5 2B', selected: chosen === 'qwen3.5:2b', installed, recommended: true, verified: false, can_remove: true, size_bytes: 2 * 1024 ** 3, sources: [] },
  ] })
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url()), body = req.postDataJSON()
    if (req.method() !== 'GET') mutations.push({ path: url.pathname, body })
    let data
    if (url.pathname === '/api/model-management') data = { items: [{ id: 'ollama_vision', label: '本地视觉运行环境', category: 'vision', description: '运行器与模型分别管理', installed: true, can_uninstall: true, dependency_id: 'ollama_vision', dependency: {}, sources: [], limitations: [] }, { id: 'aniflive', label: 'AnifLive 快速声音（实验）', category: 'speech', description: '音色加速', dependency_id: null, sources: [], limitations: [] }], storage: [], hardware: {} }
    else if (url.pathname === '/api/model-management/vision/models') data = vision()
    else if (url.pathname.endsWith('/vision/models/download')) { assert.equal(body.model_id, 'qwen3.5:2b'); installed = true; data = vision() }
    else if (url.pathname.endsWith('/vision/models/verify')) data = { ok: true, verification: { ready: true }, catalog: vision() }
    else if (url.pathname.endsWith('/vision/models/select')) {
      if (failSwitch) { failSwitch = false; await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: '加载失败，原来的视觉模型选择保留。' }) }); return }
      chosen = body.model_id; data = { ok: true, catalog: vision() }
    }
    else if (url.pathname.endsWith('/vision/models/remove-preview')) data = { token: 'one-model', model_id: body.model_id, paths: ['D:/synthetic/old-model-manifest'], shared_files: ['D:/synthetic/shared-blob'], size_bytes: 1024 ** 3, impact: '仅删除旧模型，视觉运行器和新模型保留。', preserved: '其他模型使用的共用文件保留。' }
    else if (url.pathname.endsWith('/vision/models/remove')) { assert.equal(body.token, 'one-model'); assert.equal(body.model_id, 'qwen2.5vl:3b'); removed = true; data = { ok: true, removed_bytes: 1024 ** 3, catalog: vision() } }
    else if (url.pathname === '/api/model-management/voices') data = { voices: [{ id: 'own', name: '自己的音色', selected: voiceSelected, engine_label: 'GPT-SoVITS V2', model_version: 'V2', languages: ['中文', '日语'], missing_files: [], owned_bytes: 1024 ** 3, shared_files: [], external_files: ['D:/external/original.pth'], can_delete_files: true, variants: [ { engine: 'mio_v2', label: '原生 V2', state: 'available', can_select: true, reason: '原件齐全，切换时加载验证。' }, { engine: 'aniflive', label: 'Anif 加速', state: 'needs_conversion', can_select: false, reason: '需要在本机转换原始音色。' }, { engine: 'genie', label: 'Genie', state: 'incompatible', can_select: false, reason: '没有与原件匹配的 ONNX 副本。' } ] }] }
    else if (url.pathname === '/api/model-management/voices/select') { assert.deepEqual(body, { profile_id: 'own', engine: 'mio_v2' }); voiceSelected = true; data = { ok: true, verified: true, message: '原音色加载验证通过，已选择原生 V2。' } }
    else if (url.pathname.endsWith('/removal-preview')) { await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: '原始权重被 Anif 加速副本引用，请先卸载加速副本。' }) }); return }
    else if (url.pathname.includes('/capabilities')) data = { capabilities: [], busy: false }
    else if (url.pathname.includes('/presets')) data = { presets: [] }
    else throw new Error('Unexpected API ' + url.pathname)
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(data) })
  })
  try {
    await page.goto('http://127.0.0.1:18489/scripts/model-center-preview.html')
    await page.getByRole('button', { name: '看懂屏幕和图片', exact: true }).click()
    const area = page.getByRole('region', { name: '本地视觉模型选择与保留' })
    await area.getByText('已保留旧版模型，升级不会强制切换。', { exact: true }).waitFor()
    assert.equal(mutations.length, 0)
    assert.equal(await area.getByRole('article', { name: 'Qwen2.5-VL 3B' }).getByRole('button', { name: '仅删除这个模型' }).count(), 0)
    await page.getByRole('button', { name: '卸载整个视觉功能', exact: true }).waitFor()
    await area.getByRole('button', { name: '下载 Qwen3.5 2B', exact: true }).click()
    assert.equal(mutations.length, 0)
    await area.getByRole('button', { name: '确认下载', exact: true }).click()
    await area.getByRole('button', { name: '验证并使用这个模型', exact: true }).click()
    await area.getByRole('alert').filter({ hasText: '原来的视觉模型选择保留' }).waitFor()
    assert.equal(chosen, 'qwen2.5vl:3b')
    await area.getByRole('button', { name: '验证并使用这个模型', exact: true }).click()
    await area.getByText('已切换视觉模型；其他已安装模型仍保留。', { exact: true }).waitFor()
    assert.equal(chosen, 'qwen3.5:2b')
    const old = area.getByRole('article', { name: 'Qwen2.5-VL 3B' })
    await old.getByRole('button', { name: '仅删除这个模型', exact: true }).click()
    await area.getByText('保留共用文件：D:/synthetic/shared-blob').waitFor()
    assert.equal(removed, false)
    await area.getByRole('button', { name: '确认删除此模型', exact: true }).click()
    await area.getByRole('button', { name: '下载 Qwen2.5-VL 3B', exact: true }).waitFor()
    checks.push('旧模型保持选中；新模型下载需确认且不自动使用；加载失败保留旧选择；单模型删除预览保护共享文件')
    await page.getByText('个人音色文件管理', { exact: true }).click()
    const voices = page.getByRole('region', { name: '个人音色文件管理' })
    await voices.getByText('支持语言：中文、日语', { exact: true }).waitFor()
    assert.equal(await voices.getByRole('button', { name: '使用此音色 · Genie', exact: true }).count(), 0)
    await voices.getByRole('button', { name: '管理这个音色', exact: true }).click()
    assert.equal(await voices.getByRole('checkbox', { name: '删除此音色独占的 Mio 管理文件', exact: true }).isChecked(), false)
    await voices.getByRole('checkbox', { name: '删除此音色独占的 Mio 管理文件', exact: true }).check()
    await voices.getByRole('button', { name: '预览影响与文件', exact: true }).click()
    await voices.getByRole('alert').filter({ hasText: '原始权重被 Anif 加速副本引用' }).waitFor()
    assert.equal(mutations.some(row => row.path.endsWith('/voices/own/remove')), false)
    await voices.getByRole('button', { name: '使用此音色 · 原生 V2', exact: true }).click()
    await voices.getByText('原音色加载验证通过，已选择原生 V2。', { exact: true }).waitFor()
    await voices.getByRole('button', { name: '管理这个音色', exact: true }).click()
    const before = mutations.length
    await voices.getByRole('button', { name: '预览影响与文件', exact: true }).click()
    await voices.getByRole('alert').filter({ hasText: '请先切换到其他音色' }).waitFor()
    assert.equal(mutations.length, before)
    checks.push('音色显示版本/语言与兼容方式；不兼容不能切换；原件默认保留且转换引用阻止删除')
    for (const width of [1152, 800, 390]) {
      await page.setViewportSize({ width, height: 900 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
      assert.equal(await page.locator('.vision-models button, .voice-storage button').evaluateAll(nodes => nodes.every(n => n.scrollWidth <= n.clientWidth + 1)), true)
      await page.screenshot({ path: path.join(output, `model-voices-${width}.png`), fullPage: true })
    }
    await voices.getByRole('button', { name: '去安装 Anif / 转换音色', exact: true }).click()
    await page.getByText('AnifLive 快速声音（实验）', { exact: true }).waitFor()
    checks.push('转换入口返回 Anif 卡片；1152/800/390宽无水平溢出和按钮裁字')
    assert.deepEqual(errors, [])
    await fs.writeFile(path.join(output, 'verification.json'), JSON.stringify({ ok: true, checks, mutations, page_errors: errors, private_files_read: false, model_downloads: false }, null, 2))
    console.log(JSON.stringify({ ok: true, checks, output }, null, 2))
  } finally { await browser.close(); await server.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
