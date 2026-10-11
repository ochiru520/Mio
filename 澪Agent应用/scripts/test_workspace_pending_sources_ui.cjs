const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18436'
const output = process.env.MIO_UI_EVIDENCE || path.resolve(__dirname, '../../临时验收/pending-sources-20261004')

async function run() {
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const results = []
  try {
    for (const mobile of [false, true]) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 720 } })
      await context.addInitScript(() => localStorage.setItem('mio_agent_active_view', 'projects'))
      let resolved = false, indexed = false, reindexes = 0, confirmations = 0
      const errors = [], task = { id: 'task_ui_pending', conversation_id: 'desktop_agent_ui', original_goal: '整理星空项目资料', status: 'waiting_user', snapshot: { blocker: '请补充范围' } }
      await context.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url()), p = url.pathname
        if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
        let value = {}
        if (p === '/api/onboarding/status') value = { completed: true }
        else if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversations: [], messages: [], models: [], diaries: [], qq: {}, settings: {} }
        else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
        else if (p === '/api/mobile/session') value = { paired: true, name: '测试手机' }
        else if (p === '/api/workspace') value = { projects: [{ id: 'project_ui', name: '星空项目' }], tasks: [], templates: [], routines: [], browser_pending: [] }
        else if (p === '/api/workspace/tasks') value = { tasks: url.searchParams.get('project_id') ? [task] : [], has_more: false }
        else if (p === '/api/workspace/pending') {
          const offset = Number(url.searchParams.get('offset') || 0)
          value = resolved ? { items: [], total: 0, offset: 0, has_more: false } : offset ? {
            items: [{ id: 'task:background', kind: 'task', task_id: task.id, title: '后台等待', status: 'waiting_jobs', requires_action: false }], total: 51, offset, has_more: false,
          } : {
            items: [{ id: 'browser:ui', kind: 'browser', task_id: task.id, title: task.original_goal, status: 'waiting_confirmation', project_name: '星空项目', reason: '提交摘要', requires_action: true, confirmation_id: 'ui', action: { operation: 'click', purpose: '提交摘要' }, url: 'https://example.com' }], total: 51, offset: 0, has_more: true,
          }
        } else if (p === `/api/agent/work/${task.id}`) value = { ...task, observations: [], last_response: '正在核对资料。' }
        else if (p.endsWith('/events')) value = { events: [], next_id: 0, has_more: false }
        else if (p.endsWith('/citations')) value = { sources: [
          { key: 'current', document_id: 'doc_current', name: '有效资料', sha256: 'a'.repeat(64), page: 3, status: 'current', url: '/api/workspace/knowledge/doc_current/source#page=3' },
          { key: 'stale', document_id: 'doc_stale', name: '过期资料', sha256: 'b'.repeat(64), line: 8, status: 'stale', reason: '检索后原文件发生变化', can_reindex: true, url: '/old-source' },
        ], reply_citations: [{ message_id: 'reply_ui', created_at: '2026-10-05T00:00:00Z', sentences: [{ index: 0, text: '有效资料支持这项结论。' }], source_snapshot: [{ key: 'current', document_id: 'doc_current', name: '有效资料', sha256: 'a'.repeat(64), page: 3, status: 'current', url: '/api/workspace/knowledge/doc_current/source#page=3', excerpt: '这是回复生成时保存的原文摘录。' }] }], next_id: 1, has_more: false }
        else if (p === '/api/workspace/knowledge/doc_stale/reindex') { assert.equal(request.method(), 'POST'); indexed = true; reindexes++; value = { id: 'doc_stale' } }
        else if (p === '/api/workspace/browser/ui/confirm') { assert.deepEqual(request.postDataJSON(), { approve: false }); confirmations++; resolved = true }
        else if (p === '/api/workspace/projects/project_ui') value = { project: { id: 'project_ui', name: '星空项目' }, tasks: [task], artifacts: [], jobs: [], batches: [], assemblies: [], documents: [{ id: 'doc_ocr', name: '扫描资料.pdf', path: 'D:/扫描资料.pdf', indexed_at: '2026-10-05T00:00:00Z', ocr_status: 'used', ocr_used: true, ocr_error: '' }], notes: [], browser: { hosts: [], allow_public: false } }
        else if (p === '/api/workspace/projects/project_ui/search') value = { results: [{ id: 1, document_id: 'doc_stale', citation: '项目资料 · 第 8 行', content: indexed ? '当前资料' : '旧资料', url: '/api/workspace/knowledge/doc_stale/source', stale: !indexed }] }
        else if (p.includes('startup-greeting')) value = { enabled: false }
        else if (p.includes('/profile')) value = { profile: {}, settings: {} }
        else if (p === '/api/agent/conversations' || p === '/api/agent/messages' || /^\/(diaries|reviews|weekly)$/.test(p)) value = []
        await route.fulfill({ json: value })
      })
      const page = await context.newPage()
      page.on('pageerror', error => errors.push(error.message))
      await page.goto(base + (mobile ? '/mobile.html' : '/?workspace=agent'))
      const hub = page.locator('.workspace-hub')
      await hub.waitFor()
      const idle = () => page.waitForFunction(() => document.querySelector('.workspace-hub')?.getAttribute('aria-busy') === 'false')
      await idle()
      await hub.getByRole('button', { name: '待处理（51）', exact: true }).click()
      await hub.getByRole('button', { name: '下一页待处理' }).click(); await idle()
      await hub.getByText('正在等待后台', { exact: true }).waitFor()
      assert.ok(await hub.getByRole('button', { name: '下一页待处理' }).isDisabled())
      await hub.getByRole('button', { name: '上一页待处理' }).click(); await idle()
      await hub.getByRole('button', { name: '查看任务 / 补充要求' }).click(); await idle()
      await hub.getByLabel('任务资料来源').waitFor()
      await hub.getByText('查看这次回复使用的来源', { exact: true }).click()
      await hub.getByText('这是回复生成时保存的原文摘录。', { exact: true }).waitFor()
      assert.equal(await hub.getByRole('link', { name: '查看原文', exact: true }).count(), 1, 'stale source must not open as current evidence')
      assert.equal(await hub.getByRole('link', { name: '查看原文', exact: true }).getAttribute('href'), '/api/workspace/knowledge/doc_current/source#page=3')
      await hub.getByRole('button', { name: '更新索引', exact: true }).click(); await idle()
      assert.equal(reindexes, 1)
      await page.screenshot({ path: path.join(output, mobile ? 'mobile-sources.png' : 'desktop-sources.png'), fullPage: true })
      await hub.getByRole('button', { name: '待处理（51）', exact: true }).click()
      await hub.getByRole('button', { name: '拒绝', exact: true }).click(); await idle()
      await hub.getByText('当前没有待处理事项。', { exact: true }).waitFor()
      assert.equal(confirmations, 1)
      assert.equal(await hub.getByRole('button', { name: '待处理（51）', exact: true }).count(), 0)
      await hub.getByLabel('当前项目').selectOption('project_ui'); await idle()
      await hub.getByRole('button', { name: '资料', exact: true }).click()
      await hub.getByLabel('资料文字识别状态').waitFor()
      await hub.getByText('已使用 OCR', { exact: true }).waitFor()
      indexed = false
      await hub.getByLabel('检索项目资料').fill('星空')
      await hub.getByRole('button', { name: '搜索原文', exact: true }).click(); await idle()
      await hub.getByRole('button', { name: '更新这份资料索引', exact: true }).click(); await idle()
      assert.equal(reindexes, 2)
      await hub.getByText('当前资料', { exact: true }).waitFor()
      assert.equal(await hub.getByRole('button', { name: '更新这份资料索引', exact: true }).count(), 0)
      assert.deepEqual(errors, [])
      results.push({ surface: mobile ? 'mobile-viewport' : 'desktop', pagination: true, taskJump: true, sourceVersion: true, reindex: true, pendingResolution: true, errors })
      await context.close()
    }
    console.log(JSON.stringify({ results, output }))
  } finally { await browser.close() }
}
run().catch(error => { console.error(error); process.exitCode = 1 })
