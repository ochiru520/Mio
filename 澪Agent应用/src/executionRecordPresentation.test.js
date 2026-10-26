import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createSSRApp, reactive } from 'vue'
import { renderToString } from '@vue/server-renderer'
import { executionReceiptStatusLabels, executionRecordPresentation, groupExecutionRecords, matchesExecutionReceiptFilter } from './executionRecordPresentation.js'

test('real timeout and approval receipt states have Chinese labels and stay reachable through filters', () => {
  assert.equal(executionReceiptStatusLabels.timed_out, '已超时')
  assert.equal(executionReceiptStatusLabels.approved, '已批准')
  assert(matchesExecutionReceiptFilter({status:'timed_out'}, 'failed'))
  assert(matchesExecutionReceiptFilter({status:'approved'}, 'approved'))
  assert.equal(matchesExecutionReceiptFilter({status:'approved'}, 'running'), false)
  assert.equal(matchesExecutionReceiptFilter({status:'approved'}, 'completed'), false)
  assert.equal(matchesExecutionReceiptFilter({status:'executed'}, 'failed'), false)
  assert.equal(groupExecutionRecords([{tool_name:'agent_task_update',status:'timed_out'}])[0].technical, false)
})

test('actual legacy result identifiers become short Chinese outcomes with original details preserved', () => {
  const samples = [
    ['needs_confirmation', 'task:123', /确认后执行/],
    ['executed', 'material:123', /添加日记素材/],
    ['executed', 'memory:123:candidate', /待确认的记忆/],
    ['executed', 'memory:123:created', /记忆记录已处理/],
    ['executed', 'daily_thirty:kept_done', /保留今日已完成/],
    ['executed', 'mood:updated:8', /更新今日情绪/],
    ['executed', 'today_state:unchanged', /没有变化/],
    ['executed', 'thread:123', /创建待跟进/],
    ['executed', 'thread_resolved:123', /事项已处理/],
    ['executed', 'follow_up_result:123:done', /跟进结果/],
    ['executed', 'diary_missing_material:123', /保存为日记素材/],
    ['executed', 'profile:updated', /角色属性/],
    ['executed', 'diary:already_confirmed', /保留现有内容/],
  ]
  for (const [status,result,pattern] of samples) {
    const shown=executionRecordPresentation({status,result})
    assert.match(shown.summary,pattern)
    assert.equal(shown.details.find(x=>x.label==='结果详情').value,result)
  }
  const failure=executionRecordPresentation({status:'failed',result:'memory:123:created'})
  assert.match(failure.summary,/执行失败/)
  assert.equal(executionRecordPresentation({status:'executed',result:'https://example.com/a:123'}).summary,'https://example.com/a:123')
})

test('records use Chinese actions, concise results and translated failure reasons', () => {
  const result = executionRecordPresentation({ action_type: 'remember_memory', title: '长请求'.repeat(100), status: 'executed', result: '已保存这条记忆。\n' + '补充细节'.repeat(100) })
  assert.equal(result.label, '保存记忆')
  assert.ok(result.summary.length <= 120)
  assert.equal(result.summary.includes('\n'), false)
  const failed = executionRecordPresentation({ tool_name: 'write_file', status: 'failed', error: 'Permission denied: private.txt' })
  assert.match(failed.summary, /没有所需权限/)
  assert.equal(failed.details.find(item => item.label === '错误详情').value, 'Permission denied: private.txt')
  assert.match(executionRecordPresentation({ status: 'failed' }).summary, /未提供失败原因/)
  assert.match(executionRecordPresentation({ status: 'needs_confirmation' }).summary, /确认后执行/)
  assert.match(executionRecordPresentation({ result: { exit_code: 3 }, status: 'executed' }).summary, /退出码 3/)
  assert.equal(executionRecordPresentation({ tool_name: 'agent_read_document' }).label, '读取文档')
  assert.equal(executionRecordPresentation({ tool_name: 'agent_write_document' }).label, '写入文档')
  assert.equal(executionRecordPresentation({ tool_name: 'agent_list_files' }).label, '查看项目文件')
  assert.equal(executionRecordPresentation({ tool_name: 'agent_search_files' }).label, '搜索项目文件')
})

test('request bodies and empty fields are omitted, technical identifiers remain in details', () => {
  const request = '请按照以下完整要求处理文件。'.repeat(100)
  const shown = executionRecordPresentation({ action_type: 'code_apply_patch', title: request, status: 'executed', conversation_id: 'internal-session', payload: { instruction: request, changes: [{ path: 'src/a.js' }] }, result: request, source_message_id: null })
  assert.equal(shown.label, '修改代码文件')
  assert.equal(JSON.stringify(shown).includes(request), false)
  assert.equal(shown.details.find(item => item.label === '会话编号').value, 'internal-session')
  assert.equal(shown.details.find(item => item.label === '修改文件').value, 'src/a.js')
  assert.equal(shown.details.some(item => item.label === '来源消息'), false)
  assert.equal(shown.details.some(item => !item.value.trim()), false)
  const nested = executionRecordPresentation({ payload: { instruction: request }, result: { summary: '已检查文件', original_request: request, entries: [request, '有效结果'] } })
  assert.equal(JSON.stringify(nested).includes(request), false)
  assert.equal(nested.summary, '已检查文件')
  assert.match(nested.details.find(item => item.label === '结果详情').value, /有效结果/)
})

test('bookkeeping is grouped separately without deleting or mutating source records', () => {
  const records = [{ id: 1, tool_name: 'agent_task_update', status: 'completed' }, { id: 2, tool_name: 'read_file', status: 'failed' }]
  const before = JSON.stringify(records)
  const groups = groupExecutionRecords(records)
  assert.deepEqual(groups.map(group => [group.technical, group.items.map(item => item.record.id)]), [[false, [2]], [true, [1]]])
  assert.equal(groups[1].items[0].record, records[0])
  assert.equal(JSON.stringify(records), before)
  for (const status of ['failed', 'cancelled', 'needs_confirmation', 'running']) {
    const urgent = groupExecutionRecords([{ tool_name: 'agent_task_update', status }])
    assert.equal(urgent[0].technical, false)
  }
})

test('actual API serialized receipts show useful metadata without JSON or document bodies', () => {
  const request = '私有文档正文'.repeat(300)
  const shown = executionRecordPresentation({tool_name:'agent_write_document',status:'executed',request:{content:request},result:JSON.stringify({path:'report.docx',content:request})})
  assert.equal(shown.summary,'文件：report.docx')
  assert.equal(JSON.stringify(shown).includes(request),false)
  const truncated = executionRecordPresentation({tool_name:'agent_read_document',status:'executed',result:'{"content":"'+request})
  assert.equal(truncated.summary.includes('{'),false)
  assert.equal(JSON.stringify(truncated).includes(request),false)
  assert.match(executionRecordPresentation({status:'timed_out',result:'工具执行超过 30 秒。'}).summary,/30/)
})

async function compileComponent(file) {
  const { descriptor } = parse(await readFile(new URL(file, import.meta.url), 'utf8'))
  const compiled = compileScript(descriptor, { id: 'execution-record-test', inlineTemplate: true })
  // Resolve imports for a data-URL module; no generated files or running API needed.
  const withStubIcons = compiled.content.replace(/import SelectField from ['"][^'"]+['"]/g, `const SelectField = 'select'`).replace(/import\s*\{([^}]+)\}\s*from\s*['"]@lucide\/vue['"]/g, (match, names) => {
    const icons = names.split(',').map(name => name.trim()).filter(Boolean)
    return icons.map(name => `const ${name} = () => null`).join('\n')
  })
  const source = withStubIcons.replace(/from\s+(['"])([^'"]+)\1/g, (match, quote, specifier) => {
    const target = specifier.startsWith('.') ? new URL(specifier, new URL(file, import.meta.url)).href : import.meta.resolve(specifier)
    return `from ${JSON.stringify(target)}`
  })
  // Both groups resolve only native HTML tags. Avoid duplicate pnpm Vue runtime
  // instances invoking the native-tag resolver through a separate import path.
  const nativeTags = source.replace(/_resolveDynamicComponent\(group.technical \? 'details' : 'div'\)/g, "(group.technical ? 'details' : 'div')")
  return (await import(`data:text/javascript;base64,${Buffer.from(nativeTags).toString('base64')}`)).default
}

test('execution center renders short summaries with collapsed technical IDs and unchanged approval controls', async () => {
  const component = await compileComponent('./components/TasksPage.vue')
  const request = '完整用户请求'.repeat(100)
  const context = reactive({
    taskCenterTab: 'actions', tasksLoading: false, tasksLoaded: true, tasksError: '', autonomyLoading: false,
    taskStatusFilter: 'needs_confirmation', taskStatusFilters: [{ id: 'all', label: '全部' }, { id: 'needs_confirmation', label: '待确认' }],
    taskSummary: { total: 2, pending: 1 }, taskBusy: '',
    filteredAgentTasks: [{ id: 12, action_type: 'code_apply_patch', title: request, payload: { instruction: request }, conversation_id: 'private-session-id', status: 'needs_confirmation' }, { id: 13, action_type: 'agent_task_update', status: 'executed' }],
    taskStatusLabel: status => status === 'needs_confirmation' ? '等待确认' : '执行完成', formatRealTime: value => value,
  })
  const app = createSSRApp(component)
  app.provide('mio-records-page', context)
  const html = await renderToString(app)
  assert.match(html, /修改代码文件/)
  assert.match(html, /等待你的确认后执行/)
  assert.match(html, /确认执行/)
  assert.match(html, /状态筛选/)
  assert.equal(html.includes(request), false)
  assert.match(html, /<details class="task-record-details"[^>]*><summary>技术详情<\/summary>/)
  assert.match(html, /<dt>会话编号<\/dt><dd>private-session-id<\/dd>/)
  assert.match(html, /<details class="task-technical-records"[^>]*><summary>技术记录 · 1 次任务进度更新<\/summary>/)
  assert.equal(/<details[^>]*\bopen(?:=|\s|>)/.test(html), false)
})

test('settings receipts component compiles with the shared presentation helper and filter', async () => {
  const component = await compileComponent('./components/AgentToolSettings.vue')
  assert.equal(typeof component.setup, 'function')
})
