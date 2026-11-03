/* Actual Vue rendering with synthetic identity metadata; no model or private API access. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const { createServer } = await import('vite')
  const server = await createServer({ server: { host: '127.0.0.1', port: 18478, strictPort: true } })
  await server.listen()
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || '../验收与归档/Anif原MioV2适配-20261101/ui')
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1152, height: 900 } })
  const pageErrors = [], apiCalls = [], checks = [], screenshots = []
  page.on('pageerror', error => pageErrors.push(error.message))
  await page.route('**/api/**', route => { apiCalls.push(route.request().url()); return route.abort() })
  const identity = page.getByRole('region', { name: '当前语音运行状态', exact: true })
  try {
    await page.goto('http://127.0.0.1:18478/scripts/voice-engines-preview.html?settings-shell=1')
    await page.getByRole('heading', { name: '角色语音', exact: true }).waitFor()
    await page.evaluate(() => {
      const { status } = window.voiceEngineFixture
      status.pet.settings.voice_engine = 'aniflive'
      status.voice_runtime = {
        engine: 'aniflive', local_voice_runtime: 'aniflive', service_running: true,
        model_version: 'GPT-SoVITS V2', reference_name: 'mio-original-v2',
        original_mio_weights_loaded: true, uses_original_finetuned_weights: true,
        active_weights: { gpt: 'D:/synthetic/mio.ckpt', sovits: 'D:/synthetic/mio.pth' },
        weight_sha256: { gpt: 'a'.repeat(64), sovits: 'b'.repeat(64) },
      }
    })
    await identity.getByText('原 Mio V2 微调权重，由 AnifLive 加速。', { exact: true }).waitFor()
    assert.equal(await identity.locator('dd').nth(0).innerText(), 'AnifLive 加速引擎')
    assert.equal(await identity.locator('dd').nth(1).innerText(), 'GPT-SoVITS V2')
    assert.equal(await identity.locator('dd').nth(2).innerText(), '原 Mio 音色')
    assert.equal(await identity.getByText(/V2ProPlus/).count(), 0)
    checks.push('实际引擎 Anif、底模 V2、双 SHA256 和原权重来源标志显示原 Mio 音色')
    for (const width of [1152, 800, 390]) {
      await page.setViewportSize({ width, height: 900 })
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 2), false)
      const overflow = await identity.locator('dl > div').evaluateAll(elements => elements
        .filter(element => element.scrollWidth > element.clientWidth + 2).map(element => element.textContent))
      assert.deepEqual(overflow, [])
      const name = `anif-original-v2-${width}.png`
      await page.screenshot({ path: path.join(output, name), fullPage: true }); screenshots.push(name)
    }
    checks.push('1152、800、390 宽度无横向溢出，身份说明可完整阅读')
    await page.getByRole('button', { name: 'GPT-SoVITS', exact: true }).click()
    assert.equal(await page.locator('.connection-label').innerText(), '待保存并验证')
    assert.equal(await identity.locator('dd').nth(0).innerText(), 'AnifLive 加速引擎')
    assert.equal(await identity.locator('dd').nth(2).innerText(), '原 Mio 音色')
    checks.push('切换未保存标签时，当前真实发声身份仍为 Anif 原 Mio V2')
    await page.getByRole('button', { name: 'AnifLive-TTS（实验）', exact: true }).click()
    await page.evaluate(() => { delete window.voiceEngineFixture.status.voice_runtime.weight_sha256.sovits })
    await identity.getByText('使用 V2 模型包；服务尚未确认是否为原 Mio 权重。', { exact: true }).waitFor()
    assert.equal(await identity.getByText('原 Mio V2 微调权重，由 AnifLive 加速。', { exact: true }).count(), 0)
    checks.push('缺任一权重哈希，拒绝显示原 Mio 权重已确认')
    await page.evaluate(() => {
      const runtime = window.voiceEngineFixture.status.voice_runtime
      runtime.model_version = 'GPT-SoVITS V2ProPlus'
      runtime.original_mio_weights_loaded = false
      runtime.reference_name = 'default'
    })
    await identity.getByText('使用 V2ProPlus 音色包，与原 Mio V2 是不同的模型包。', { exact: true }).waitFor()
    assert.equal(await identity.locator('dd').nth(1).innerText(), 'GPT-SoVITS V2ProPlus')
    assert.equal(await identity.locator('dd').nth(2).innerText(), 'default')
    checks.push('V2ProPlus 服务显示独立音色包，不混称原 Mio V2')
    await page.evaluate(() => { window.voiceEngineFixture.status.voice_runtime.service_running = false })
    await identity.getByText('未运行', { exact: true }).waitFor()
    assert.equal(await identity.locator('dd').nth(1).innerText(), '未加载')
    assert.equal(await identity.locator('dd').nth(2).innerText(), '未加载')
    checks.push('服务停止后不保留已加载模型或音色的假在线状态')
    assert.deepEqual(pageErrors, []); assert.deepEqual(apiCalls, [])
    await fs.writeFile(path.join(output, 'identity-result.json'), JSON.stringify({
      passed: true, checks, screenshots, pageErrors, apiCalls, syntheticMetadata: true,
    }, null, 2))
    console.log(`Anif V2 identity UI passed: ${checks.length} groups, ${screenshots.length} screenshots`)
  } finally { await browser.close(); await server.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
