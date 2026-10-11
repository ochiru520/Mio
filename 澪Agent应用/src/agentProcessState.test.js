import test from 'node:test'
import assert from 'node:assert/strict'
import { applyAnalysisEvent, visibleAnalysis, processOperations } from './agentProcessState.js'
import { contextUsagePresentation } from './contextUsageState.js'

test('old English analysis is suppressed and Chinese instructions retain technical paths', () => {
  assert.deepEqual(visibleAnalysis('Let me find the source docx file.'), { text: '', suppressed: true })
  assert.deepEqual(visibleAnalysis('Let'), { text: '', suppressed: true })
  const text = '先读取 D:\\项目\\report.docx，然后使用 read_file 与 API 检查。\n准备修改 `index.ts`。'
  assert.deepEqual(visibleAnalysis(text), { text, suppressed: false })
  assert.deepEqual(visibleAnalysis('Let me find the file.\n先核对项目目录。'), { text: '先核对项目目录。', suppressed: true })
  for (const technical of ['检查 Python 脚本并运行 pytest。', '先运行测试。\n```python\nprint("hello world")\n```', '~~~js\nconsole.log("hello world")\n~~~', '```js\nconsole.log("unfinished")', '使用 pnpm 和 vitest 检查 Vue，再用 ruff 检查 Python。']) {
    assert.deepEqual(visibleAnalysis(technical), { text: technical, suppressed: false })
  }
})

test('a language notice is isolated to its stream without creating substitute reasoning', () => {
  let blocks = applyAnalysisEvent([], { event_type: 'analysis_language_notice', payload: { stream_id: 's1', text: '模型返回的分析不是中文' } })
  blocks = applyAnalysisEvent(blocks, { event_type: 'analysis_text_delta', payload: { stream_id: 's2', text: '核对结果。' } })
  blocks = applyAnalysisEvent(blocks, { event_type: 'analysis_stream_finished', payload: { stream_id: 's1', status: 'generated' } })
  assert.equal(blocks[0].content, '')
  assert.equal(blocks[0].languageNotice, '模型返回的分析不是中文')
  assert.equal(blocks[0].status, 'completed')
  assert.equal(blocks[1].content, '核对结果。')
})

test('operations merge repeated receipt ids and exclude task bookkeeping', () => {
  const events = [
    { id: 1, event_type: 'tool_batch_completed', payload: { tools: [{ step_id: 'a', tool_name: 'read_file', status: 'running' }, { step_id: 'b', tool_name: 'agent_task_update' }] } },
    { id: 2, event_type: 'planner_completed', payload: { tools: [{ step_id: 'unused' }] } },
    { id: 3, event_type: 'tool_batch_completed', payload: { tools: [{ step_id: 'a', tool_name: 'read_file', status: 'completed' }, { step_id: 'c', tool_name: 'write_file', status: 'failed' }] } },
  ]
  assert.deepEqual(processOperations(events).map(step => [step.step_id, step.status]), [['a', 'completed'], ['c', 'failed']])
})

test('context display separates current estimated window from cumulative billed usage', () => {
  const shown = contextUsagePresentation({ used_tokens: 4000, max_tokens: 20000, percent: 20, has_summary: true, compression_count: 2, total_tokens: 171768 })
  assert.equal(shown.amount, '4,000 / 20,000 token（估算）')
  assert.equal(shown.compression, '已自动压缩 2 次，完整记录保留')
  assert.equal(shown.label.includes('171,768'), false)
  assert.equal(contextUsagePresentation({ used_chars: 3000, max_chars: 18000, percent: 16.7 }).amount, '3,000 / 18,000 字')
  assert.equal(contextUsagePresentation({ auto_compression_enabled: false }).compression, '自动压缩未启用')
  assert.equal(contextUsagePresentation({ has_summary: true, compression_count: 2, compression_warning: '摘要失败，已缩短旧记录；完整记录可回读' }).compression, '摘要失败，已缩短旧记录；完整记录可回读')
})
