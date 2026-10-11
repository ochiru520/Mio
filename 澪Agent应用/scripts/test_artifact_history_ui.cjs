const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18436'
const output = path.resolve(__dirname, '../../临时验收/artifact-history-20261004')

async function run() {
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const results = []
  try {
    for (const mobile of [false, true]) {
      const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1280, height: 720 } })
      await context.addInitScript(() => localStorage.setItem('mio_agent_active_view', 'projects'))
      const errors = [], writes = []
      let artifact = { id: 'artifact_ui', name: '历史文稿.txt', task_id: 'task_ui', version: 2, size: 128, integrity_status: 'unknown', quality_status: 'not_reviewed', sha256: 'a'.repeat(64), url: '/api/artifacts/artifact_ui/download' }
      const task = { id: 'task_ui', conversation_id: 'desktop_agent_ui', original_goal: '历史任务', status: 'completed', snapshot: {} }
      await context.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url()), p = url.pathname
        if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
        let value = {}
        if (p === '/api/onboarding/status') value = { completed: true }
        else if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversations: [], messages: [], models: [], diaries: [], qq: {}, settings: {} }
        else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
        else if (p === '/api/mobile/session') value = { paired: true, name: '合成手机' }
        else if (p === '/api/workspace') value = { projects: [{ id: 'project_ui', name: '合成项目' }], tasks: [], templates: [], routines: [], browser_pending: [] }
        else if (p === '/api/workspace/pending') value = { items: [], total: 0, offset: 0, has_more: false }
        else if (p === '/api/workspace/tasks') value = { tasks: Number(url.searchParams.get('offset')) ? [task] : [{ ...task, id: 'task_recent', original_goal: '最近任务' }], has_more: !Number(url.searchParams.get('offset')) }
        else if (p === '/api/agent/work/task_ui') value = { ...task, observations: [], last_response: '历史任务完成。' }
        else if (p.endsWith('/events')) value = { events: [], next_id: 0 }
        else if (p.endsWith('/citations')) value = { sources: [], has_more: false }
        else if (p === '/api/artifacts') value = { artifacts: url.searchParams.get('project_id') ? [] : Number(url.searchParams.get('offset')) ? [artifact] : [{ ...artifact, id: 'artifact_recent', name: '最近文稿.txt', task_id: '' }], has_more: !url.searchParams.get('project_id') && !Number(url.searchParams.get('offset')) }
        else if (p === '/api/artifacts/artifact_ui/review' || p === '/api/artifacts/artifact_ui/verify') {
          assert.equal(request.method(), 'POST')
          if (mobile) assert.equal(request.headers()['x-mio-mobile'], '1')
          writes.push(p)
          if (p.endsWith('/review')) { const payload = request.postDataJSON(); artifact = { ...artifact, quality_status: payload.status, quality_note: payload.note, reviewed_at: '2026-10-04' } }
          else artifact = { ...artifact, integrity_status: 'failed', verified_at: '2026-10-04' }
          value = artifact
        } else if (p === '/api/workspace/projects/project_ui') value = { project: { id: 'project_ui', name: '合成项目' }, artifacts: [], jobs: [],
          batches: [{ id: 'batch_first', name: '最新队列', status: 'paused', items: [] }], batches_has_more: true,
          assemblies: [{ id: 'assembly_first', spec: { name: '最新成片' }, status: 'completed' }], assemblies_has_more: true,
          documents: [{ id: 'doc_first', name: '最新资料' }], documents_has_more: true,
          notes: [{ id: 'note_first', title: '最新记录', content: '合成记录', source: 'user' }], notes_has_more: true,
          selections: [{ id: 'selection_first', note: '最新选用' }], selections_has_more: true,
          browser: { hosts: [], allow_public: false } }
        else if (p.includes('/history/')) {
          const kind = p.split('/').at(-1)
          value = { [kind]: { batches: [{ id: 'batch_old', name: '历史队列', status: 'paused', items: [] }], assemblies: [{ id: 'assembly_old', spec: { name: '历史成片' }, status: 'completed' }], documents: [{ id: 'doc_old', name: '历史资料' }], notes: [{ id: 'note_old', title: '历史记录', content: '合成记录', source: 'user' }], selections: [{ id: 'selection_old', note: '历史选用' }] }[kind], has_more: false }
        }
        else if (p === '/api/workspace/projects/project_ui/jobs') value = { jobs: [{ id: 'job_ui', outputs: [{ name: Number(url.searchParams.get('offset')) ? '历史生成结果' : '最新生成结果', mime_type: 'text/plain', url: '/fixture' }] }], has_more: !Number(url.searchParams.get('offset')) }
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
      await hub.getByRole('button', { name: '下一页任务', exact: true }).click(); await idle()
      await hub.getByRole('button', { name: '历史任务', exact: false }).waitFor()
      assert.ok(await hub.getByRole('button', { name: '下一页任务', exact: true }).isDisabled())
      await hub.getByRole('button', { name: '成果', exact: true }).click()
      const library = hub.locator('.artifact-library')
      await library.getByText('最近文稿.txt', { exact: true }).waitFor()
      await library.getByRole('button', { name: '下一页成果', exact: true }).click()
      await library.getByText('历史文稿.txt', { exact: true }).waitFor()
      assert.ok(await library.getByRole('button', { name: '下一页成果', exact: true }).isDisabled())
      await library.getByText('评价与来源', { exact: true }).click()
      await library.getByLabel('历史文稿.txt质量说明').fill('结构需改进')
      await library.getByRole('button', { name: '需改进', exact: true }).click()
      await library.getByText(/需改进 · 文件待核验/).waitFor(); await idle()
      await library.getByRole('button', { name: '重新校验文件', exact: true }).click()
      await library.getByText(/需改进 · 文件校验失败/).waitFor(); await idle()
      assert.equal(await library.getByRole('link', { name: '下载', exact: true }).count(), 0)
      assert.equal(await library.getByLabel('历史文稿.txt质量说明').inputValue(), '结构需改进')
      await page.screenshot({ path: path.join(output, mobile ? 'mobile.png' : 'desktop.png'), fullPage: true })
      await library.getByRole('button', { name: '查看来源任务', exact: true }).click(); await idle()
      await hub.getByText('历史任务完成。', { exact: true }).waitFor()
      await hub.getByLabel('当前项目').selectOption('project_ui'); await idle()
      await hub.getByRole('button', { name: '成果', exact: true }).click()
      await library.getByText('还没有归档成果', { exact: true }).waitFor()
      assert.equal(await library.getByText('历史文稿.txt', { exact: true }).count(), 0)
      await hub.getByText('最新生成结果', { exact: true }).waitFor()
      await hub.getByRole('button', { name: '下一页生成', exact: true }).click(); await idle()
      await hub.getByText('历史生成结果', { exact: true }).waitFor()
      assert.ok(await hub.getByRole('button', { name: '下一页生成', exact: true }).isDisabled())
      for (const [label, text] of [['队列','历史队列'], ['成片','历史成片'], ['选用','历史选用']]) {
        await hub.getByRole('button', { name: `下一页${label}`, exact: true }).click(); await idle()
        await hub.getByText(new RegExp(text)).waitFor()
        assert.ok(await hub.getByRole('button', { name: `下一页${label}`, exact: true }).isDisabled())
      }
      await hub.getByRole('button', { name: '资料', exact: true }).click()
      for (const [label, text] of [['资料','历史资料'], ['记录','历史记录']]) {
        await hub.getByRole('button', { name: `下一页${label}`, exact: true }).click(); await idle()
        await (label === '资料' ? hub.getByRole('link', { name: text, exact: true }) : hub.getByText(text, { exact: true })).waitFor()
        assert.ok(await hub.getByRole('button', { name: `下一页${label}`, exact: true }).isDisabled())
      }
      assert.equal(writes.length, 2); assert.deepEqual(errors, [])
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'horizontal overflow')
      results.push({ surface: mobile ? 'mobile-viewport' : 'desktop', history: true, quality: true, integrity: true, taskJump: true, projectIsolation: true, errors })
      await context.close()
    }
  } finally { await browser.close() }
  console.log(JSON.stringify({ results, output }))
}
run().catch(error => { console.error(error); process.exitCode = 1 })
