/* Actual Vue/Edge with synthetic installation metadata; no downloads or GPU jobs. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const { createServer } = await import('vite')
  const server = await createServer({ server: { host: '127.0.0.1', port: 18486, strictPort: true } })
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || '../验收与归档/Anif普通用户安装-20261101/ui')
  await fs.mkdir(output, { recursive: true })
  let browser
  const calls = [], errors = [], checks = [], layouts = []
  let state = {
    voices: [{ id: 'own-v2', name: '自己的日语音色', compatible: true }],
    checks: [{ id: 'gpu', label: '显卡', ok: true, message: 'NVIDIA 显卡可用于本机编译' }, { id: 'disk', label: '可用空间', ok: true, message: '空间充足' }],
    can_start: true, can_activate: false, can_uninstall: false, blockers: [], installed: null, job: {},
  }
  const catalog = { items: [{ id: 'aniflive', category: 'speech', label: 'AnifLive 快速声音（实验）', description: '使用自己的 V2 音色进行流式加速。', sources: [], limitations: [], installed: false, verification: 'experimental' }], hardware: {}, storage: [] }
  try {
    await server.listen()
    browser = await chromium.launch({ channel: 'msedge', headless: true })
    const page = await browser.newPage({ viewport: { width: 1152, height: 1000 } })
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url()), method = request.method()
      calls.push({ path: url.pathname, method, body: request.postDataJSON() })
      let response
      if (url.pathname === '/api/model-management') response = catalog
      else if (url.pathname === '/api/model-management/voices') response = { voices: [] }
      else if (url.pathname === '/api/capabilities') response = { capabilities: [], busy: false }
      else if (url.pathname === '/api/model-management/anif/setup' && method === 'GET') response = state
      else if (url.pathname === '/api/model-management/anif/setup' && method === 'POST') {
        assert.equal(request.postDataJSON().voice_id, 'own-v2')
        state = { ...state, job: { id: 'job-ui', voice_id: 'own-v2', stage: 'build', running: true, can_cancel: true, percent: 50, message: '正在为这台电脑编译音色' }, can_start: false }
        response = state
      } else if (url.pathname.endsWith('/setup/cancel')) {
        state = { ...state, can_start: true, job: { ...state.job, stage: 'cancelled', running: false, can_cancel: false, message: '已取消' } }
        response = state
      } else if (url.pathname.endsWith('/setup/activate')) {
        state = { ...state, selected: true, installed: { active: true, voice_id: 'own-v2', job_id: 'job-ui' } }
        response = { message: '已选择 Anif 和这个音色' }
      } else if (url.pathname.endsWith('/setup/uninstall-preview')) response = { token: 'owned-preview', size_bytes: 1024 ** 3, impact: '移除受管的 Anif 环境与编译文件。', preserved: '原始音色和其他引擎保留。' }
      else if (url.pathname.endsWith('/setup/uninstall')) {
        assert.equal(request.postDataJSON().token, 'owned-preview')
        state = { ...state, installed: null, can_uninstall: false, can_activate: false, selected: false, job: {} }
        response = { message: '已卸载受管 Anif' }
      } else if (url.pathname.includes('/setup/audio/')) {
        assert.equal(url.searchParams.get('job_id'), 'job-ui')
        const wav = Buffer.alloc(684)
        wav.write('RIFF', 0); wav.writeUInt32LE(676, 4); wav.write('WAVEfmt ', 8); wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22); wav.writeUInt32LE(32000, 24); wav.writeUInt32LE(64000, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34); wav.write('data', 36); wav.writeUInt32LE(640, 40)
        return route.fulfill({ status: 200, contentType: 'audio/wav', body: wav })
      } else throw new Error(`Unexpected API ${method} ${url.pathname}`)
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(response) })
    })
    await page.goto('http://127.0.0.1:18486/scripts/model-center-preview.html')
    const panel = page.getByRole('region', { name: 'Anif 安装与音色转换' })
    await page.getByRole('button', { name: '安装 Anif / 转换自己的音色', exact: true }).click()
    await panel.getByRole('button', { name: '开始安装并编译', exact: true }).waitFor()
    assert.equal(calls.some(row => row.method === 'POST'), false)
    checks.push('展开只检查，安装为用户显式动作')
    await panel.getByRole('button', { name: '开始安装并编译', exact: true }).click()
    await panel.getByText('正在为这台电脑编译音色', { exact: true }).last().waitFor()
    assert.equal(await panel.getByRole('button', { name: /暂停/ }).count(), 0)
    await panel.getByRole('button', { name: '暂时跳过，返回模型列表', exact: true }).click()
    assert.equal(calls.some(row => row.path.endsWith('/cancel')), false)
    await panel.getByRole('button', { name: '安装 Anif / 转换自己的音色', exact: true }).click()
    await panel.getByRole('button', { name: '取消准备', exact: true }).click()
    await panel.getByText('已取消', { exact: true }).waitFor()
    checks.push('编译不显示暂停，收起继续后台运行，取消等待真实状态')
    await panel.getByRole('button', { name: '重试准备此音色', exact: true }).click()
    state = { ...state, can_start: true, can_activate: true, can_uninstall: true, installed: { active: false, voice_id: 'own-v2', job_id: 'job-ui' }, job: { ...state.job, stage: 'ready', ready: true, running: false, can_cancel: false, percent: 100, message: '中文和日语发声检查已完成' } }
    await panel.getByRole('button', { name: '重新检查', exact: true }).click()
    await panel.getByRole('button', { name: '读取中文试听', exact: true }).click()
    await panel.getByRole('button', { name: '读取日语试听', exact: true }).click()
    assert.equal(await panel.locator('audio').count(), 2)
    assert.equal(calls.some(row => row.path.endsWith('/activate')), false)
    checks.push('ready后读取两种试听，检查完成不自动切换引擎')
    for (const width of [1152, 800, 390]) {
      await page.setViewportSize({ width, height: 1200 })
      const metrics = await panel.evaluate(element => ({ width: innerWidth, doc: document.documentElement.scrollWidth, clipped: [...element.querySelectorAll('button,select,p,audio')].filter(node => { const box = node.getBoundingClientRect(); return box.width && (box.left < -1 || box.right > innerWidth + 1 || node.scrollHeight > node.clientHeight + 2) }).map(node => ({ tag: node.tagName, text: node.textContent.slice(0, 90), scroll: node.scrollHeight, height: node.clientHeight })) }))
      assert.ok(metrics.doc <= width, JSON.stringify(metrics))
      assert.deepEqual(metrics.clipped, [], JSON.stringify(metrics))
      layouts.push(metrics)
      await page.screenshot({ path: path.join(output, `anif-setup-${width}.png`), fullPage: true })
    }
    await panel.getByRole('button', { name: '使用这个音色', exact: true }).click()
    await panel.getByRole('button', { name: '正在使用这个音色', exact: true }).waitFor()
    await panel.getByRole('button', { name: '卸载 Anif，释放磁盘', exact: true }).click()
    assert.equal(calls.some(row => row.path.endsWith('/setup/uninstall')), false)
    await panel.getByRole('button', { name: '确认卸载', exact: true }).click()
    await panel.getByText('已卸载受管 Anif', { exact: true }).waitFor()
    checks.push('使用需显式选择；卸载需预览与令牌确认')
    state = { ...state, voices: [], can_start: false, checks: [{ id: 'gpu', label: '显卡', ok: false, message: '没有可用的兼容显卡，仍可使用其他语音引擎' }], blockers: ['可先跳过此功能。'] }
    await panel.getByRole('button', { name: '重新检查', exact: true }).click()
    await panel.getByText('可先跳过此功能。', { exact: true }).waitFor()
    assert.equal(await panel.getByRole('button', { name: '开始安装并编译', exact: true }).isDisabled(), true)
    await panel.getByRole('button', { name: '暂时跳过，返回模型列表', exact: true }).click()
    checks.push('不兼容/无音色时提供指引和跳过，不阻塞模型列表')
    assert.deepEqual(errors, [])
    await fs.writeFile(path.join(output, 'ui-result.json'), JSON.stringify({ ok: true, checks, layouts, errors, calls }, null, 2))
    console.log(JSON.stringify({ ok: true, checks, layouts, errors }))
  } finally { await browser?.close(); await server.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
