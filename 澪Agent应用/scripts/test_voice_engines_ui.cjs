/* Isolated actual Vue rendering; synthetic state, no API calls or personal App startup. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const { createServer } = await import('vite')
  const server = await createServer({ server: { host: '127.0.0.1', port: 18475, strictPort: true } })
  await server.listen()
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || '../验收与归档/多语音引擎-20261031')
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1152, height: 900 } })
  page.setDefaultTimeout(10000)
  const errors = [], apiCalls = [], checks = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/**', route => { apiCalls.push(route.request().url()); return route.abort() })
  try {
    await page.goto('http://127.0.0.1:18475/scripts/voice-engines-preview.html')
    await page.getByRole('heading', { name: '角色语音', exact: true }).waitFor()
    assert.equal(await page.locator('.voice-engine-choice > button').count(), 5)
    assert.equal(await page.locator('.connection-label.online').innerText(), '服务在线')
    const originalProfiles = await page.evaluate(() => JSON.stringify(window.voiceEngineFixture.status.pet.settings.voice_profiles))
    const text = '你好，我是澪。我们用同一句话，比较开始说话的速度和声音。'
    await page.getByRole('textbox', { name: '对比试听文字', exact: true }).fill(text)
    const engines = [
      ['GPT-SoVITS v5', 'gpt_sovits_v5', 'v5 服务地址'],
      ['AnifLive-TTS', 'aniflive', '模型包 ID'],
      ['IndexTTS', 'indextts2', '适配服务地址'],
    ]
    for (const [label, engine, field] of engines) {
      await page.locator('.voice-engine-choice > button').filter({ hasText: label }).click()
      await page.getByText(engine === 'aniflive' ? '高级连接与音色设置' : '高级连接设置', { exact: true }).click()
      await page.getByRole('textbox', { name: new RegExp(`^${field}`) }).waitFor()
      assert.equal(await page.getByRole('textbox', { name: '对比试听文字', exact: true }).inputValue(), text)
      assert.equal(await page.locator('.connection-label').innerText(), '待保存并验证')
      assert.equal(await page.locator('.connection-label.online').count(), 0)
      await page.getByRole('button', { name: '保存并试听', exact: true }).click()
      assert.equal(await page.evaluate(() => window.voiceEngineFixture.tests.at(-1).text), text)
      assert.equal(await page.evaluate(() => window.voiceEngineFixture.tests.at(-1).engine), engine)
      await page.getByRole('button', { name: '保存并启动', exact: true }).click()
      assert.deepEqual(await page.evaluate(() => window.voiceEngineFixture.controls.at(-1)), { action: 'start', engine })
      await page.evaluate(engine => { window.voiceEngineFixture.status.voice_runtime = {
        engine, service_running: false, service_loading: true, managed_available: true, managed_running: true,
      } }, engine)
      assert.equal(await page.locator('.connection-label').innerText(), '正在加载与预热')
      assert.equal(await page.getByRole('button', { name: '保存并启动', exact: true }).isDisabled(), true)
      await page.getByRole('button', { name: '停止', exact: true }).click()
      assert.deepEqual(await page.evaluate(() => window.voiceEngineFixture.controls.at(-1)), { action: 'stop', engine })
      await page.evaluate(() => { window.voiceEngineFixture.status.voice_runtime = { engine: 'gpt_sovits', service_running: true } })
      assert.equal(await page.evaluate(() => JSON.stringify(window.voiceEngineFixture.status.pet.settings.voice_profiles)), originalProfiles)
      if (engine === 'gpt_sovits_v5') {
        assert.equal(await page.getByRole('textbox', { name: '参考音频准确原文', exact: true }).count(), 1)
        assert.equal(await page.getByText('GPT 权重', { exact: true }).count(), 0)
      }
      if (engine === 'aniflive') {
        assert.equal(await page.getByText('当前适配尚未通过正式精度检查；原始 V2 / Genie 音色包需要专门转换，不能直接混用。', { exact: true }).isVisible(), true)
        assert.equal(await page.getByRole('textbox', { name: '模型包 ID', exact: true }).getAttribute('maxlength'), '160')
        assert.equal(await page.locator('input[type=file]').count(), 0)
      }
      for (const size of [{ width: 1152, height: 900 }, { width: 800, height: 700 }, { width: 390, height: 844 }]) {
        await page.setViewportSize(size)
        const overflow = await page.evaluate(() => [...document.querySelectorAll('.voice-engine-choice, .voice-cloud-panel, .settings-form-grid')]
          .filter(el => el.scrollWidth > el.clientWidth + 2).map(el => el.className))
        assert.deepEqual(overflow, [], `${engine} overflow at ${size.width}`)
        await page.screenshot({ path: path.join(output, `${engine}-${size.width}.png`), fullPage: true })
      }
      checks.push(`${engine}: 同文试听事件、音色保留、状态不串用、1152/800/390 无横向溢出`)
    }
    await page.evaluate(() => { window.voiceEngineFixture.status.voice_runtime = { engine: 'indextts2', service_running: false, model_version: 'unknown' } })
    assert.equal(await page.locator('.connection-label').innerText(), '服务未就绪')
    assert.equal(await page.locator('.connection-label.online').count(), 0)
    assert.equal(await page.getByText(/正在使用：/).count(), 0)
    await page.screenshot({ path: path.join(output, 'indextts2-offline-390.png'), fullPage: true })
    checks.push('实际离线显示服务未就绪，unknown 版本不冒充已确认')
    assert.deepEqual(errors, [])
    assert.deepEqual(apiCalls, [])
    await fs.writeFile(path.join(output, '前端UI验收.json'), JSON.stringify({ checks, screenshots: 10, pageErrors: errors, apiCalls, synthetic: true }, null, 2))
    console.log(`Voice engines UI passed: ${checks.length} groups, 10 screenshots; no API or personal config access.`)
  } finally { await browser.close(); await server.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
