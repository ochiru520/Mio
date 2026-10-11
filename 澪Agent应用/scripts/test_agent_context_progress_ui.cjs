/* Production bundle, synthetic API/SSE only. No model calls or personal data. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18461'
const output = process.env.MIO_UI_EVIDENCE
async function main() {
  if (!output) throw new Error('Set MIO_UI_EVIDENCE to a dedicated output directory')
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 850 } })
  ctx.setDefaultTimeout(12000)
  const errors = [], checks = []
  let live = false
  const conversation = { id: 'desktop_agent_context_ui', title: '上下文验收', kind: 'agent' }
  const models = [{ id: 'test-model', model: 'synthetic', display_name: '合成模型', provider: '合成', supports_reasoning: true }]
  const messages = [{ id: 1, role: 'user', content: '检查报告，保留原文件。' },
    { id: 2, role: 'assistant', content: '唯一的最终回复。', agent_task_id: 'ui-task', agent_run_id: 'ui-run', prompt_tokens: 170000, completion_tokens: 1768 }]
  await ctx.addInitScript(() => {
    localStorage.setItem('mio_agent_active_view', 'chat')
    localStorage.setItem('mio_agent_conversation_id', 'desktop_agent_context_ui')
    localStorage.setItem('mio_app_preferences', JSON.stringify({ theme: 'xuan' }))
    window.__streams = []
    window.EventSource = class {
      constructor(url) { this.url = url; this.handlers = {}; window.__streams.push(this); setTimeout(() => this.onopen?.(), 10) }
      addEventListener(name, cb) { this.handlers[name] = cb }
      close() { this.closed = true }
      emit(name, value) { if (!this.closed) this.handlers[name]?.({ data: JSON.stringify(value) }) }
    }
  })
  await ctx.route('**/*', async route => {
    const p = new URL(route.request().url()).pathname
    if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
    let value = {}
    if (p === '/api/onboarding/status') value = { completed: true }
    else if (p === '/api/agent/bootstrap') value = { onboarding: { completed: true }, conversation_id: conversation.id, conversations: [conversation], models, messages, settings: {}, diaries: [], qq: {}, model: models[0], provider_presets: [] }
    else if (p === '/api/agent/conversations') value = [conversation]
    else if (p === '/api/agent/messages') value = messages
    else if (p === '/api/agent/models/catalog') value = { models, provider_presets: [] }
    else if (p === '/api/agent/context-usage') value = { used_tokens: 4000, max_tokens: 20000, percent: 20, has_summary: true, compression_count: 2, context_kind: 'agent_planner', estimated: true }
    else if (p === '/api/workspace') value = { projects: [], tasks: [] }
    else if (p === '/api/workspace/conversations') value = { projects: {}, modes: { [conversation.id]: 'work' } }
    else if (p === '/api/workspace/pending') value = { items: [], total: 0 }
    else if (p === '/api/artifacts') value = { artifacts: [], has_more: false }
    else if (p === '/api/agent/work') value = { tasks: live ? [{ id: 'ui-task', conversation_id: conversation.id, status: 'running', snapshot: {} }] : [], limits: {} }
    else if (p.endsWith('/inputs')) value = { inputs: [], has_more: false, next_version: 0 }
    else if (p.endsWith('/history')) value = { changes: [], jobs: [], observations: [] }
    else if (p.includes('/profile')) value = { profile: {}, settings: {} }
    else if (p === '/api/companion/status') value = { pet: { settings: {} }, screen: {}, settings: {} }
    else if (/^\/(diaries|reviews|weekly)$/.test(p)) value = []
    await route.fulfill({ json: value })
  })
  const page = await ctx.newPage()
  page.on('pageerror', error => errors.push(error.message))
  try {
    await page.goto(base + '/?workspace=agent&conversation_id=' + conversation.id)
    await page.getByLabel('上下文验收的菜单').waitFor()
    const usage = page.locator('.context-usage-control')
    await usage.focus()
    await page.getByText('4,000 / 20,000 token（估算）', { exact: true }).waitFor()
    await page.getByText('已自动压缩 2 次，完整记录保留', { exact: true }).waitFor()
    assert.match(await usage.getAttribute('aria-label'), /20\.0%/)
    assert.match(await page.locator('.message-hover-meta').last().innerText(), /本轮累计 171768 token/)
    checks.push('Agent当前上下文与累计用量分开；焦点可查看压缩次数')
    await page.getByText('查看分析与执行过程', { exact: true }).click()
    await page.waitForFunction(() => window.__streams.length > 0)
    const emit = async events => page.evaluate(events => {
      for (const event of events) window.__streams.at(-1).emit('progress', { ...event, run_id: 'ui-run' })
    }, events)
    await emit([
      { id: 1, event_type: 'analysis_stream_started', payload: { stream_id: 's1' } },
      { id: 2, event_type: 'analysis_text_delta', payload: { stream_id: 's1', text: '**先核对原文件。**\n然后检查 `report.docx`。' } },
    ])
    await page.locator('.analysis-history summary').waitFor()
    assert.equal(await page.locator('.current-analysis').count(), 0)
    assert.equal(await page.locator('.analysis-history[open]').count(), 0)
    await emit([
      { id: 3, event_type: 'analysis_stream_finished', payload: { stream_id: 's1', status: 'generated' } },
      { id: 4, event_type: 'tool_batch_completed', payload: { tools: [{ step_id: 'a', tool_name: 'code_read_file', display_name: '读取报告', status: 'completed', result: { path: 'report.docx' } }, { step_id: 'update', tool_name: 'agent_task_update', status: 'completed' }] } },
      { id: 5, event_type: 'analysis_stream_started', payload: { stream_id: 's2' } },
      { id: 6, event_type: 'analysis_text_delta', payload: { stream_id: 's2', text: 'Let me find the source docx file.' } },
      { id: 7, event_type: 'analysis_language_notice', payload: { stream_id: 's2', text: '模型返回的分析不是中文' } },
      { id: 8, event_type: 'analysis_stream_finished', payload: { stream_id: 's2', status: 'generated' } },
      { id: 9, event_type: 'tool_batch_completed', payload: { tools: [{ step_id: 'a', tool_name: 'code_read_file', display_name: '读取报告', status: 'completed', result: { path: 'report.docx' } }] } },
      { id: 10, event_type: 'final_response_completed', payload: { reply: '唯一的最终回复。' } },
    ])
    await page.evaluate(() => window.__streams.at(-1).emit('task_state', { status: 'completed', snapshot: { summary: '这段总结不能重复显示。' } }))
    await page.getByText('模型返回的部分分析不是中文，以下显示中文执行进度。', { exact: true }).waitFor()
    assert.equal(await page.locator('.current-analysis').count(), 0)
    assert.equal(await page.locator('.analysis-history[open], .process-actions[open]').count(), 0)
    assert.equal(await page.locator('.task-progress .text, .task-progress .process-summary').count(), 0)
    assert.equal(await page.getByText('唯一的最终回复。', { exact: true }).count(), 1)
    assert.equal(await page.getByText('Let me find', { exact: false }).count(), 0)
    await page.locator('.analysis-history summary').click()
    await page.locator('.process-actions summary').click()
    assert.equal(await page.locator('.analysis-entry').count(), 1)
    assert.equal(await page.locator('.process-actions article').count(), 1)
    await page.screenshot({ path: output + '/analysis-records.png' })
    checks.push('中文Markdown和真实事件格式，旧英文提示，历史统一折叠，操作去重，最终正文不重复')
    for (const width of [600, 1280, 1920]) {
      await page.setViewportSize({ width, height: 850 })
      await page.locator('.task-progress').evaluate(el => { if (el.scrollWidth > el.clientWidth + 1) throw new Error('Process records overflow') })
    }
    live = true
    await page.goto(base + '/?workspace=agent&conversation_id=' + conversation.id)
    await page.waitForFunction(() => window.__streams.length > 0)
    await emit([
      { id: 1, event_type: 'analysis_stream_started', payload: { stream_id: 'live' } },
      { id: 2, event_type: 'analysis_text_delta', payload: { stream_id: 'live', text: '**先检查项目。**\n保留你的修改，再核对结果。' } },
    ])
    await page.locator('.current-analysis[open] strong').getByText('先检查项目。', { exact: true }).waitFor()
    assert.equal(await page.locator('.analysis-history').count(), 0)
    await emit([
      { id: 3, event_type: 'analysis_stream_finished', payload: { stream_id: 'live', status: 'generated' } },
      { id: 4, event_type: 'analysis_stream_started', payload: { stream_id: 'next' } },
      { id: 5, event_type: 'analysis_text_delta', payload: { stream_id: 'next', text: '现在核对测试结果。' } },
    ])
    await page.locator('.current-analysis[open]').getByText('现在核对测试结果。', { exact: true }).waitFor()
    assert.equal(await page.locator('.analysis-history[open]').count(), 0)
    checks.push('当前中文分析自动展开，上一段自动归入折叠记录')
    await page.screenshot({ path: output + '/current-analysis.png' })
    assert.deepEqual(errors, [])
    await fs.writeFile(output + '/report.json', JSON.stringify({ checks, pageErrors: errors, scope: 'production bundle; synthetic API/SSE; no real model calls' }, null, 2))
    console.log(JSON.stringify({ checks, pageErrors: errors }))
  } catch (error) {
    await page.screenshot({ path: output + '/failure.png' })
    await fs.writeFile(output + '/failure-dom.txt', await page.locator('body').innerText())
    throw error
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exit(1) })
