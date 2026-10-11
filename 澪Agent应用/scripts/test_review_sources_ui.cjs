const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const output = path.resolve(__dirname, '../../临时验收/review-sources-20261004')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18436'
async function run() {
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const results = []
  try {
    for (const narrow of [false, true]) {
      const context = await browser.newContext({ viewport: narrow ? { width: 390, height: 844 } : { width: 1280, height: 720 } })
      const date = '2026-10-04', diary = { date, title: '合成日记', markdown_content: '原始合成记录', daily_thirty_status: 'unknown' }
      const versions = ['stale', 'unknown', 'current'], errors = []
      let refreshes = 0, generated = 0
      const metadata = () => ({ source_status: versions[Math.min(refreshes, 2)], stale_reason: '来源日记已更正', source_dates: [date] })
      await context.route('**/*', async route => {
        const request = route.request(), p = new URL(request.url()).pathname
        if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
        let value = {}
        if (p === '/api/onboarding/status') value = { completed: true }
        else if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversation_id: 'desktop_ui', conversations: [], messages: [], models: [], diaries: [diary], qq: {}, settings: {}, logical_date: date }
        else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
        else if (p === '/api/diaries') value = [diary]
        else if (p === '/api/diaries/' + date && request.method() === 'PUT') { diary.markdown_content = request.postDataJSON().markdown_content; refreshes++ }
        else if (p === '/api/diaries/' + date) value = diary
        else if (p === '/api/reviews') value = [{ date, markdown_content: '已有回顾正文', ...metadata() }]
        else if (p === '/api/weekly') value = [{ week_start: '2026-09-28', week_end: date, markdown_content: '已有周记正文', ...metadata() }]
        else if (p === '/api/monthly') value = [{ month: '2026-10', month_start: '2026-10-01', month_end: '2026-10-31', markdown_content: '已有月记正文', source_diaries: [diary], ...metadata() }]
        else if (/\/generate$/.test(p)) { generated++; refreshes = 2 }
        else if (p === '/api/memory') value = { structured: [], structured_candidates: [], threads: [], profile: {}, runtime_summary: {} }
        else if (p.includes('startup-greeting')) value = { enabled: false }
        else if (p.includes('/profile')) value = { profile: {}, settings: {} }
        else if (p === '/api/agent/conversations' || p === '/api/agent/messages' || /^\/(diaries|reviews|weekly)$/.test(p)) value = []
        await route.fulfill({ json: value })
      })
      const page = await context.newPage()
      page.on('pageerror', error => errors.push(error.message))
      await page.goto(base + '/?view=diaries')
      await page.getByText('原始合成记录', { exact: true }).waitFor()
      await page.getByText('来源已变化，回顾需要更新', { exact: true }).waitFor()
      await page.getByTitle('编辑日记', { exact: true }).click()
      await page.getByLabel('日记 Markdown 正文').fill('更正后的合成记录')
      await page.locator('.diary-reader').getByRole('button', { name: '保存', exact: true }).click()
      await page.getByText('旧回顾未记录来源版本', { exact: true }).waitFor()
      await page.getByText('已有回顾正文', { exact: true }).waitFor()
      await page.getByRole('button', { name: '重新生成回顾', exact: true }).click()
      await page.getByText('生成时的来源仍有效', { exact: true }).waitFor()
      for (const kind of ['周记', '月记']) {
        await page.getByRole('navigation', { name: '日记类型' }).getByRole('button', { name: kind, exact: true }).click()
        await page.locator('.period-reader .markdown-body').getByText(kind === '周记' ? '已有周记正文' : '已有月记正文', { exact: true }).waitFor()
        await page.getByText('生成时的来源仍有效', { exact: true }).waitFor()
      }
      await page.screenshot({ path: path.join(output, narrow ? 'narrow.png' : 'desktop.png'), fullPage: true })
      await page.locator('.review-sources').getByText('查看来源日期（1）', { exact: true }).click()
      await page.locator('.review-sources').getByRole('button', { name: date, exact: true }).click()
      await page.getByText('更正后的合成记录', { exact: true }).waitFor()
      assert.equal(generated, 1); assert.deepEqual(errors, [])
      results.push({ surface: narrow ? 'narrow-desktop' : 'desktop', sourceVersion: true, editRefresh: true, bodyPreserved: true, sourceJump: true, errors })
      await context.close()
    }
  } finally { await browser.close() }
  console.log(JSON.stringify({ results, output }))
}
run().catch(error => { console.error(error); process.exitCode = 1 })
