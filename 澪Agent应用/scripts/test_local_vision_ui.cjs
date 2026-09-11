const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs/promises')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const browser = await chromium.launch({ headless: true, channel: 'msedge' })
  const page = await browser.newPage({ viewport: { width: 1152, height: 900 } })
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || 'ui-evidence')
  await fs.mkdir(output, { recursive: true })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  let state = 'installed'
  let failure = true
  let installing = false
  let alreadyInstalled = false
  let activations = 0
  let installs = 0
  const details = {
    installed: '模型文件已完整安装，Mio 本地视觉服务尚未启动。',
    degraded: 'CUDA out of memory: 模型加载失败，请释放显存后重试。',
    ready: 'Mio 独立本地视觉服务已通过推理验证。',
    missing: '运行器已安装，但 qwen2.5vl:3b 模型文件缺失或不完整。',
  }
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    let data = {}
    let status = 200
    if (url.pathname === '/api/dependencies') {
      data = { dependencies: [{ id: 'ollama_vision', label: '本地视觉', kind: 'script', status: state, installing,
        what: '本地屏幕观察模型', detail: details[state], how: '下载安装运行器和模型',
        install_path: 'D:\\Mio\\Data\\本地视觉\\models\\manifests\\registry.ollama.ai\\library\\qwen2.5vl\\3b' }] }
    } else if (url.pathname.endsWith('/activate')) {
      activations++
      await new Promise(resolve => setTimeout(resolve, 200))
      state = failure ? 'degraded' : 'ready'
      status = failure ? 400 : 200
      data = { status: failure ? 'degraded' : 'available', detail: details[state] }
    } else if (url.pathname.endsWith('/install')) {
      installs++
      installing = !alreadyInstalled
      if (alreadyInstalled) state = 'installed'
      data = { installing, message: alreadyInstalled ? '文件已经安装，请启动并验证。' : '正在安装' }
    } else if (url.pathname.endsWith('/status')) {
      installing = false
      state = 'installed'
      data = { installing: false, stage: 'done', percent: 100, message: '文件已安装' }
    }
    await route.fulfill({ status, json: data })
  })
  const open = () => page.goto(`${process.env.MIO_UI_BASE || 'http://127.0.0.1:1421'}/scripts/agent-settings-preview.html?view=dependencies`)
  try {
    await open()
    await page.getByRole('button', { name: '启动并验证', exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: '一键安装' }).count(), 0)
    await page.getByRole('button', { name: '启动并验证', exact: true }).click()
    await page.getByRole('button', { name: '正在启动并验证' }).waitFor()
    assert.equal(await page.getByRole('button', { name: '正在启动并验证' }).isDisabled(), true)
    await page.getByRole('button', { name: '重新验证', exact: true }).waitFor()
    await page.getByRole('status').filter({ hasText: 'CUDA out of memory' }).waitFor()
    assert.equal(await page.getByRole('button', { name: '一键安装' }).count(), 0)
    for (const size of [{ width: 1152, height: 900 }, { width: 800, height: 600 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(size)
      const overflow = await page.evaluate(() => [...document.querySelectorAll('.dependency-center, .dependency-item-main, .dependency-item-text, .dependency-item-actions, .integrated-workspace')].filter(el => el.scrollWidth > el.clientWidth + 2).map(el => el.className))
      assert.deepEqual(overflow, [], `overflow at ${size.width}`)
      await page.screenshot({ path: path.join(output, `local-vision-error-${size.width}.png`) })
    }
    failure = false
    await page.getByRole('button', { name: '重新验证', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '已通过推理验证' }).waitFor()
    await page.getByText('已就绪', { exact: true }).first().waitFor()
    assert.equal(activations, 2)
    assert.equal(installs, 0)
    state = 'missing'
    await open()
    await page.getByRole('button', { name: '一键安装' }).click()
    await page.getByRole('button', { name: '启动并验证', exact: true }).waitFor()
    assert.equal(installs, 1)
    state = 'missing'
    alreadyInstalled = true
    await open()
    await page.getByRole('button', { name: '一键安装' }).click()
    await page.getByRole('button', { name: '启动并验证', exact: true }).waitFor()
    await page.getByRole('status').filter({ hasText: '文件已经安装' }).waitFor()
    assert.equal(installs, 2)
    assert.deepEqual(errors, [])
    console.log('Local vision UI passed: installed/activate, failure/retry, completion polling, existing files, responsive layout.')
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
