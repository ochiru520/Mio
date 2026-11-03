/* Synthetic fixtures only: never accesses a user's model files, records a microphone or downloads models. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const { createServer } = await import('vite')
  const server = await createServer({ server: { host: '127.0.0.1', port: 18479, strictPort: true } })
  await server.listen()
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || '../验收与归档/普通用户完整方案-20261101/model-ui')
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1152, height: 900 } })
  const errors = [], mutations = [], checks = []
  page.on('pageerror', error => errors.push(error.message))
  let deleted = false, installed = false, failDelete = true, paused = false
  const speech = { id: 'mio_v2', label: '原 Mio V2', category: 'speech', description: '原始权重流式语音', compatibility: '支持兼容的 V2 训练权重', dependency_id: null, install_mode: 'external', status: 'installed', installed: true, selected: true, can_release: true, can_uninstall: false, sources: [{ label: '项目来源', url: 'https://github.com/RVC-Boss/GPT-SoVITS' }], limitations: [], requirements: '需要兼容的本地模型', size_bytes: null }
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method(), body = request.postDataJSON()
    if (method !== 'GET') mutations.push({ path: url.pathname, body })
    let result
    if (url.pathname === '/api/model-management') result = { items: [speech, { id: 'whisper', label: '本地语音识别', category: 'recognition', description: '把语音变成文字', dependency_id: 'whisper', installed: !deleted, status: deleted ? 'missing' : 'ready', can_uninstall: !deleted, sources: [], limitations: [], requirements: '独立安装', size_bytes: 1024 ** 3 }], storage: [{ id: 'voice', label: '声音模型', path: 'D:/synthetic/voice', size_bytes: 1024 ** 3, free_bytes: 80 * 1024 ** 3 }], hardware: { ram_total_bytes: 32 * 1024 ** 3, gpus: [{ name: 'Synthetic GPU' }], recommendation: '按需安装，先测试再使用。', limitations: [] } }
    else if (url.pathname === '/api/model-management/voices') result = { voices: [{ id: 'custom', name: '自训测试音色', selected: false, owned_bytes: 1024 ** 3, external_files: ['D:/external/kept.pth'], shared_files: ['D:/shared/kept.ckpt'], can_delete_files: true }] }
    else if (url.pathname === '/api/model-management/cache/preview') result = { token: 'cache-preview', paths: ['D:/synthetic/downloads/owned.zip.part'], size_bytes: 1024 ** 2, impact: '将移除下载缓存。', preserved: '已安装模型与个人权重保留。' }
    else if (url.pathname === '/api/model-management/cache/clear') { assert.equal(body.token, 'cache-preview'); result = { ok: true, removed_bytes: 1024 ** 2, remaining_files: [], message: '下载缓存已清理。' } }
    else if (url.pathname.endsWith('/removal-preview')) result = { id: 'custom', name: '自训测试音色', ...body, token: 'voice-preview', paths: body.delete_files ? ['D:/owned/custom.pth'] : [], size_bytes: body.delete_files ? 1024 ** 3 : 0, external_files: ['D:/external/kept.pth'], shared_files: ['D:/shared/kept.ckpt'], impact: '将移除所选音色配置。', preserved: '外部和共用文件保留。' }
    else if (url.pathname.endsWith('/remove')) { assert.equal(body.token, 'voice-preview'); result = { ok: false, removed_bytes: 0, remaining_files: ['D:/owned/custom.pth'], message: '部分文件被占用。' } }
    else if (url.pathname === '/api/dependencies') result = { dependencies: [{ id: 'whisper', status: deleted ? 'missing' : 'ready', install_path: 'D:/synthetic/asr', installing: installed, progress: installed ? { percent: 30, stage: paused ? 'paused' : 'downloading', message: paused ? '已暂停' : '下载文件', can_pause: !paused, can_resume: paused, can_cancel: true } : null }] }
    else if (url.pathname === '/api/dependencies/diagnostics') result = { checked_at: '2026-11-01', summary: 'Synthetic diagnostics only', paths: ['<APP>/managed'], secrets: '<REDACTED>' }
    else if (url.pathname.endsWith('/release')) result = { released: true, message: '语音服务已停止，文件仍保留。' }
    else if (url.pathname.endsWith('/uninstall-preview')) result = { id: 'whisper', token: 'safe-preview', paths: ['D:/synthetic/asr'], size_bytes: 1024 ** 3, impact: '语音识别将停用', preserved: '聊天、个人音色和共享资源保留。' }
    else if (url.pathname.endsWith('/uninstall')) {
      assert.equal(body.token, 'safe-preview')
      if (failDelete) { failDelete = false; await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: '模型文件正被占用，请停止服务后重试。' }) }); return }
      deleted = true; result = { message: '已卸载所选模型。' }
    }
    else if (url.pathname.endsWith('/install')) { installed = true; result = { installing: true, message: '已启动下载' } }
    else if (url.pathname.endsWith('/download-control')) { paused = body.action === 'pause'; if (body.action === 'cancel') installed = false; result = { message: body.action === 'cancel' ? '已取消，保留已下载部分。' : '已更新' } }
    else if (url.pathname.includes('/storage/') && url.pathname.endsWith('/preview')) result = { token: 'storage-preview', destination: body.path, mode: 'copy_and_switch', size_bytes: 1024 ** 3, impact: '复制并切换模型位置', preserved: '原位置保留，个人外部引用不变。' }
    else if (url.pathname.includes('/storage/') && url.pathname.endsWith('/commit')) { assert.equal(body.token, 'storage-preview'); result = { message: '已复制并切换，原文件保留。' } }
    else if (url.pathname === '/api/audio-devices') result = { available: true, output_device_id: body?.output_device_id || 'default', outputs: [{ id: 'default', label: '系统默认' }, { id: 'speaker-fixture', label: '测试扬声器' }] }
    else if (url.pathname.includes('/capabilities')) result = { capabilities: [], busy: false }
    else if (url.pathname.includes('presets')) result = { presets: [] }
    else throw new Error('Unexpected mocked API: ' + url.pathname)
    if (url.pathname === '/api/model-management') {
      const row = result.items.find(item => item.id === 'whisper')
      row.dependency = { id: 'whisper', installing: installed, progress: installed ? { percent:30, stage:paused ? 'paused' : 'downloading', message:paused ? '已暂停' : '下载文件', can_pause:!paused, can_resume:paused, can_cancel:true } : null }
    }
    if (new URL(page.url()).searchParams.get('empty') === '1') {
      if (url.pathname === '/api/model-management') result.items = []
      if (url.pathname === '/api/model-management/voices') result.voices = []
      if (url.pathname === '/api/dependencies') result.dependencies = []
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(result) })
  })
  try {
    await page.goto('http://127.0.0.1:18479/scripts/model-center-preview.html')
    await page.getByText('原 Mio V2', { exact: true }).waitFor()
    assert.equal(mutations.length, 0)
    await page.getByRole('button', { name: '停止服务，释放内存' }).click()
    await page.getByText('语音服务已停止，文件仍保留。').waitFor()
    await page.getByRole('button', { name: '听懂我说话', exact: true }).click()
    await page.getByRole('button', { name: '卸载，释放磁盘' }).click()
    assert.equal(mutations.filter(item => item.path.endsWith('/uninstall')).length, 0)
    await page.getByText('聊天、个人音色和共享资源保留。').waitFor()
    await page.getByRole('button', { name: '确认执行', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: '模型文件正被占用' }).waitFor()
    assert.equal(await page.getByRole('region', { name: '确认模型操作' }).count(), 1)
    await page.getByRole('button', { name: '确认执行', exact: true }).click()
    await page.getByText('已卸载所选模型。').waitFor()
    checks.push('释放内存不调用删除；卸载预览后确认、令牌校验、失败留可重试计划')
    await page.getByRole('button', { name: '获取与安装', exact: true }).click()
    assert.equal(mutations.filter(item => item.path.endsWith('/install')).length, 0)
    await page.getByRole('button', { name: '确认安装', exact: true }).click()
    await page.getByRole('button', { name: '暂停下载', exact: true }).click()
    await page.getByRole('button', { name: '继续下载', exact: true }).click()
    await page.getByRole('button', { name: '取消下载', exact: true }).click()
    await page.getByText('已取消，保留已下载部分。').waitFor()
    checks.push('安装前确认；暂停继续取消仅随真实capability flags显示')
    await page.getByText('安装位置与磁盘空间', { exact: true }).click()
    await page.getByRole('textbox', { name: '新安装文件夹' }).fill('E:/synthetic/new-models')
    await page.getByRole('checkbox', { name: /复制已有受管模型并切换/ }).check()
    await page.getByRole('button', { name: '检查空间和影响' }).click()
    await page.getByText('原位置保留，个人外部引用不变。').waitFor()
    assert.equal(mutations.filter(item => item.path.endsWith('/commit')).length, 0)
    await page.getByRole('button', { name: '确认执行', exact: true }).click()
    await page.getByText('已复制并切换，原文件保留。').waitFor()
    checks.push('迁移展示原文件保留，必须确认才提交')
    await page.getByText('下载缓存管理', { exact: true }).click()
    await page.getByRole('button', { name: '检查可清理的下载缓存' }).click()
    await page.getByText('已安装模型与个人权重保留。').waitFor()
    assert.equal(mutations.filter(item => item.path.endsWith('/cache/clear')).length, 0)
    await page.getByRole('button', { name: '确认执行', exact: true }).click()
    await page.getByText('下载缓存已清理。 实际释放 1.0 MB。').waitFor()
    await page.getByText('个人音色文件管理', { exact: true }).click()
    await page.getByRole('button', { name: '管理这个音色' }).click()
    assert.equal(await page.getByRole('checkbox', { name: '删除此音色独占的 Mio 管理文件' }).isChecked(), false)
    await page.getByRole('checkbox', { name: '删除此音色独占的 Mio 管理文件' }).check()
    await page.getByRole('button', { name: '预览影响与文件' }).click()
    await page.getByText('保留外部文件：D:/external/kept.pth').waitFor()
    await page.getByText('保留共用文件：D:/shared/kept.ckpt').waitFor()
    assert.equal(mutations.filter(item => item.path.endsWith('/remove')).length, 0)
    await page.getByRole('button', { name: '确认音色清理' }).click()
    await page.getByRole('alert').filter({ hasText: '部分文件被占用。 实际释放 0.0 MB。' }).waitFor()
    await page.getByText('尚未删除：D:/owned/custom.pth').waitFor()
    checks.push('缓存必须预览确认；音色文件默认不删除，共享外部文件受保护，部分清理失败准确显示实际释放值')
    for (const size of [{ width: 1152, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(size)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
      assert.equal(await page.locator('.storage select').evaluate(element => getComputedStyle(element).appearance), 'none')
      assert.deepEqual(await page.locator('.model-center').evaluate(element => ({ body:getComputedStyle(element).fontSize, title:getComputedStyle(element.querySelector('h3')).fontSize })), { body:'14px', title:'16px' })
      await page.screenshot({ path: path.join(output, `models-${size.width}.png`), fullPage: true })
    }
    await page.addInitScript(() => {
      window.deviceFixture = { requests: 0, stopped: 0 }
      Object.defineProperty(navigator, 'mediaDevices', { value: {
        addEventListener() {}, removeEventListener() {},
        getUserMedia: async () => { window.deviceFixture.requests++; throw Object.assign(new Error('denied'), { name: 'NotAllowedError' }) },
        enumerateDevices: async () => [],
      }, configurable: true })
    })
    await page.goto('http://127.0.0.1:18479/scripts/model-center-preview.html?view=devices')
    await page.getByRole('button', { name: '检查设备', exact: true }).waitFor()
    assert.equal(await page.evaluate(() => window.deviceFixture.requests), 0)
    await page.getByRole('combobox', { name: 'Mio 语音输出（正式说话）' }).selectOption('speaker-fixture')
    await page.getByRole('button', { name: '保存 Mio 输出设备' }).click()
    assert.equal(mutations.findLast(item => item.path === '/api/audio-devices').body.output_device_id, 'speaker-fixture')
    await page.getByRole('button', { name: '检查设备', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: '麦克风权限被拒绝' }).waitFor()
    assert.equal(await page.getByRole('button', { name: '检查设备', exact: true }).isEnabled(), true)
    checks.push('设备页打开不申请麦克风；权限拒绝可重试；正式输出与回放设置分开')
    await page.screenshot({ path: path.join(output, 'device-permission-390.png'), fullPage: true })
    for (const size of [{ width: 1152, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(size)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
      assert.equal(await page.locator('.device-check select').evaluateAll(elements => elements.every(element => getComputedStyle(element).appearance === 'none')), true)
      assert.deepEqual(await page.locator('.device-check').evaluate(element => ({ body:getComputedStyle(element).fontSize, title:getComputedStyle(element.querySelector('h3')).fontSize })), { body:'14px', title:'16px' })
      await page.screenshot({ path: path.join(output, `devices-clean-${size.width}.png`), fullPage: true })
    }
    await page.goto('http://127.0.0.1:18479/scripts/model-center-preview.html?view=onboarding')
    const titles = ['检查这台电脑', '欢迎使用 Mio', '彼此怎么称呼', '模型服务', '选择声音与功能', '敏感能力', '试试声音设备', '准备完成']
    for (const [index, title] of titles.entries()) {
      await page.getByRole('heading', { name: title, exact: true }).waitFor()
      if ([4, 6, 7].includes(index)) {
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
        assert.equal(await page.evaluate(() => document.querySelector('.onboarding-content').getBoundingClientRect().bottom <= document.querySelector('.onboarding-card > footer').getBoundingClientRect().top + 1), true)
        await page.screenshot({ path: path.join(output, `onboarding-${index}-390.png`), fullPage: true })
      }
      if (index < titles.length - 1) await page.getByRole('button', { name: '继续', exact: true }).click()
    }
    await page.getByText(/你跳过了聊天模型测试/).waitFor()
    assert.equal(await page.evaluate(() => window.deviceFixture.requests), 0)
    checks.push('8步向导可跳过可选模型和设备测试；完成页准确指出聊天模型尚未测试；向导全程不自动请求录音')
    const mutationCountBeforeSkip = mutations.length
    await page.goto('http://127.0.0.1:18479/scripts/model-center-preview.html?view=onboarding&blocked=1&empty=1')
    await page.getByText('核心检查未通过，隔离验收无任何模型', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: '继续', exact: true }).isEnabled(), false)
    assert.equal(await page.getByRole('button', { name: '跳过这步', exact: true }).isEnabled(), true)
    await page.screenshot({ path: path.join(output, 'onboarding-blocked-skip-390.png'), fullPage: true })
    await page.getByRole('button', { name: '跳过教程，先进入', exact: true }).click()
    assert.deepEqual(await page.evaluate(() => window.onboardingEvents), [{ event: 'complete', payload: { assistant_name:'Mio', user_address:'你', web_search_enabled:false, proactive_enabled:false, daily_diary_auto_enabled:false, qq_enabled:false, tutorial_skipped:true } }])
    await page.goto('http://127.0.0.1:18479/scripts/model-center-preview.html?view=onboarding&blocked=1&checking=1&noenvironment=1&empty=1')
    await page.getByText('正在检查本机环境', { exact: true }).waitFor()
    await page.getByRole('button', { name: '跳过教程，先进入', exact: true }).click()
    assert.equal(await page.evaluate(() => window.onboardingEvents[0].payload.tutorial_skipped), true)
    await page.goto('http://127.0.0.1:18479/scripts/model-center-preview.html?view=onboarding&blocked=1&empty=1')
    for (let index = 0; index < 7; index++) {
      await page.getByRole('heading', { name: titles[index], exact: true }).waitFor()
      if (index === 2) {
        await page.getByRole('textbox', { name: '角色名字', exact: true }).fill(' ')
        await page.getByRole('textbox', { name: '她怎么称呼你', exact: true }).fill('')
      }
      await page.getByRole('button', { name: '跳过这步', exact: true }).click()
    }
    await page.getByRole('heading', { name: '准备完成', exact: true }).waitFor()
    await page.getByRole('button', { name: '进入应用', exact: true }).click()
    assert.equal(await page.evaluate(() => window.onboardingEvents[0].payload.assistant_name), 'Mio')
    assert.equal(await page.evaluate(() => window.onboardingEvents[0].payload.user_address), '你')
    assert.equal(await page.evaluate(() => window.onboardingEvents[0].payload.tutorial_skipped), true)
    await page.goto('http://127.0.0.1:18479/scripts/model-center-preview.html?view=onboarding&review=1&blocked=1&empty=1')
    await page.getByText(/正在回看教程：返回应用不会应用这里的称呼/).waitFor()
    assert.deepEqual(await page.locator('.onboarding-card').evaluate(element => ({ shadow:getComputedStyle(element).boxShadow, heading:getComputedStyle(element.querySelector('h1')).fontSize })), { shadow:'none', heading:'16px' })
    assert.equal(await page.locator('.onboarding-shell').evaluate(element => getComputedStyle(element).backgroundImage), 'none')
    await page.screenshot({ path: path.join(output, 'onboarding-review-return-390.png'), fullPage: true })
    await page.getByRole('button', { name: '返回应用', exact: true }).click()
    assert.deepEqual(await page.evaluate(() => window.onboardingEvents), [{ event:'close' }])
    await page.goto('http://127.0.0.1:18479/scripts/model-center-preview.html?view=onboarding&review=1&blocked=1&empty=1')
    for (let index = 0; index < 7; index++) await page.getByRole('button', { name: '跳过这步', exact: true }).click()
    await page.getByRole('heading', { name: '教程回看完成', exact: true }).waitFor()
    await page.getByRole('button', { name: '返回应用', exact: true }).first().click()
    assert.deepEqual(await page.evaluate(() => window.onboardingEvents), [{ event:'close' }])
    assert.equal(await page.evaluate(() => window.deviceFixture.requests), 0)
    assert.equal(mutations.length, mutationCountBeforeSkip)
    checks.push('核心检查失败/检查中均可全局跳过；7步可逐项跳过，空称呼自动使用默认；回看全局/最终仅close，不发complete；跳过全程无下载/录音/写配置')
    for (const size of [{ width: 1152, height: 900 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(size)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
      await page.screenshot({ path: path.join(output, `onboarding-clean-${size.width}.png`), fullPage: true })
    }
    checks.push('本轮新增选择框无下拉箭头；向导无阴影或渐变、标题16px，1152/390无横向溢出且跳过/返回按钮可用')
    await page.goto('http://127.0.0.1:18479/scripts/model-center-preview.html?view=dependencies')
    await page.getByText('高级：组件、下载源与诊断', { exact: true }).click()
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', { value: { writeText: async () => { throw new Error('Clipboard unavailable') } }, configurable: true }))
    await page.getByRole('button', { name: '复制脱敏诊断', exact: true }).click()
    await page.getByText('脱敏诊断预览', { exact: true }).waitFor()
    await page.getByText('Clipboard unavailable', { exact: true }).waitFor()
    const downloadPromise = page.waitForEvent('download')
    await page.getByRole('button', { name: '保存诊断 JSON', exact: true }).click()
    const download = await downloadPromise
    await download.saveAs(path.join(output, 'synthetic-diagnostics.json'))
    assert.equal(JSON.parse(await fs.readFile(path.join(output, 'synthetic-diagnostics.json'), 'utf8')).secrets, '<REDACTED>')
    checks.push('诊断复制失败仍可预览并保存已脱敏JSON，无自动上传')
    assert.deepEqual(errors, [])
    await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ checks, errors, mutations }, null, 2))
    console.log(JSON.stringify({ checks, errors, output }, null, 2))
  } finally {
    await browser.close(); await server.close()
    await fs.writeFile(path.join(output, 'owned-preview-cleanup.json'), JSON.stringify({
      task:'novice_model_ui', script:'scripts/test_model_center_ui.cjs', process_pid:process.pid,
      port:18479, browser_connected_after_close:browser.isConnected(), http_listening_after_close:server.httpServer?.listening === true,
      closed_at:new Date().toISOString(), method:'Awaited browser.close() and Vite server.close() in finally; no other process terminated.',
    }, null, 2))
  }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
