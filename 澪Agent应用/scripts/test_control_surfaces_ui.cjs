const assert = require('node:assert/strict')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1100, height: 1000 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  try {
    await page.goto('http://127.0.0.1:1422/scripts/control-surfaces-preview.html', { waitUntil: 'domcontentloaded', timeout: 60000 })
    await page.getByText('模型策略').waitFor()
    assert.match(await page.locator('.runtime-timeline').innerText(), /推进任务/)
    assert.match(await page.locator('.artifact-library').innerText(), /result.png/)
    assert.match(await page.locator('.privacy-operations-panel').innerText(), /推进任务/)
    await page.getByLabel('Agent 任务选择策略').selectOption('fixed')
    await page.getByLabel('Agent 任务固定模型').selectOption('model-b')
    await page.getByRole('button', { name: '保存Agent 任务策略' }).click()
    await page.getByText('Agent 任务策略已保存').waitFor()
    assert.deepEqual(errors, [])
    await page.screenshot({ path: path.join(process.env.MIO_UI_EVIDENCE, 'control-surfaces.png') })
    console.log('Control surfaces UI: strategy, timeline, artifacts and privacy passed')
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exit(1) })
