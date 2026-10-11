// Presentation only: keep original records intact and never surface request bodies.
export const executionActionLabels = {
  get_self_state: '读取自我状态', list_capabilities: '读取能力清单', get_active_view: '读取当前页面',
  get_service_health: '检查服务状态', explain_last_route: '读取最近路由', get_today_state: '读取今日状态',
  search_web: '联网查证', search_memory: '检索记忆', get_diary: '读取日记',
  creation_list_presets: '读取创作预设', creation_list_loras: '读取 LoRA 用途', creation_list_assets: '读取参考素材',
  creation_use_reference: '选用聊天图片或生成结果', creation_check_workflow: '检查创作工作流',
  creation_inspect_workflow: '研究工作流与节点', creation_read_workflow_source: '读取节点说明与源码',
  creation_read_workflow_reference: '查阅工作流参考文档', creation_remember_workflow: '保存工作流理解',
  comfyui_generate_image: '创建图片任务', comfyui_generate_video: '创建视频任务', remote_generate_image: '创建远程图片任务',
  creation_get_job: '读取创作任务', creation_get_output: '读取创作结果', creation_cancel_job: '取消创作任务',
  add_diary_material: '添加日记素材', set_daily_thirty: '更新每日三十', set_daily_mood: '更新今日情绪',
  update_today_state: '更新今日状态', remember_thread: '创建待跟进事项', resolve_thread: '完成待跟进事项',
  remember_memory: '保存记忆', edit_today_diary: '修改今日日记', generate_today_diary: '生成今日日记', update_profile: '更新 Mio 属性',
  agent_task_update: '更新任务进度', execution_capabilities: '检查执行权限',
  agent_discover_tools: '查找可用工具', agent_read_task_inputs: '读取任务资料',
  agent_read_tool_result: '读取工具结果', record_follow_up_result: '记录跟进结果',
  read_file: '读取文件', write_file: '写入文件', list_directory: '查看文件目录', search_files: '搜索文件',
  agent_read_document: '读取文档', agent_write_document: '写入文档', agent_list_files: '查看项目文件', agent_search_files: '搜索项目文件',
  code_read_file: '读取代码文件', code_search: '搜索代码', code_list_files: '查看项目文件',
  code_apply_patch: '修改代码文件', code_run_command: '运行终端命令', run_command: '运行命令',
  desktop_screenshot: '查看电脑画面', desktop_click: '点击电脑界面', desktop_type: '输入文字', desktop_press_key: '操作键盘',
}

export const executionReceiptStatusLabels = {
  queued: '等待执行', needs_confirmation: '待确认', approved: '已批准', running: '执行中',
  completed: '已完成', executed: '已完成', failed: '失败', timed_out: '已超时', skipped: '已跳过', cancelled: '已取消',
}

export function matchesExecutionReceiptFilter(record, filter = 'all') {
  if (filter === 'all') return true
  if (filter === 'completed') return ['executed', 'completed'].includes(record.status)
  if (filter === 'failed') return ['failed', 'timed_out', 'skipped', 'cancelled'].includes(record.status)
  if (filter === 'running') return ['queued', 'running'].includes(record.status)
  return record.status === filter
}

function legacyResultSummary(value, status) {
  if (typeof value !== 'string') return value
  if (/^task:\d+$/.test(value)) return status === 'needs_confirmation' ? '等待你的确认后执行。' : ''
  if (!['executed', 'completed'].includes(status)) return value
  if (/^material:\d+$/.test(value)) return '已添加日记素材。'
  if (/^memory:\d+:[a-z_]+$/.test(value)) return value.endsWith(':candidate') ? '已记录待确认的记忆。' : '记忆记录已处理。'
  if (/^daily_thirty:(done|missed|unknown|kept_done)$/.test(value)) return ({done:'已记录今日完成。',missed:'已记录今日未完成。',unknown:'今日状态尚未确认。',kept_done:'保留今日已完成状态。'})[value.split(':')[1]]
  if (/^mood:updated:-?\d+(\.\d+)?$/.test(value)) return '已更新今日情绪。'
  if (/^today_state:(updated|unchanged)$/.test(value)) return value.endsWith(':updated') ? '已更新今日状态。' : '今日状态没有变化。'
  if (/^thread:\d+$/.test(value)) return '已创建待跟进事项。'
  if (/^thread_resolved:\d+$/.test(value)) return '待跟进事项已处理。'
  if (/^follow_up_result:\d+:[a-z_]+$/.test(value)) return '已记录跟进结果。'
  if (/^diary_missing_material:\d+$/.test(value)) return '尚无当日日记，内容已保存为日记素材。'
  return ({'profile:updated':'已更新角色属性。','diary:edited':'已修改当日日记。','diary:already_confirmed':'日记已经确认，保留现有内容。','diary:generated':'已生成当日日记。'})[value] || value
}

export function compactExecutionText(value, limit = 120) {
  if (typeof value !== 'string' && typeof value !== 'number') return ''
  const text = String(value).replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text
}

function nonempty(value) {
  if (value === null || value === undefined || value === '') return false
  if (typeof value === 'string') return Boolean(value.trim())
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'object') return Object.keys(value).length > 0
  return true
}

function detailValue(value) {
  return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)
}

function failureSummary(error) {
  const text = compactExecutionText(error)
  if (/权限|拒绝|未授权|permission denied|access denied|not authorized|forbidden|EACCES|EPERM/i.test(text)) return '执行失败：没有所需权限，请检查授权。'
  if (/超时|timeout|timed out/i.test(text)) return '执行失败：操作超时。'
  if (/不存在|未找到|not found|ENOENT/i.test(text)) return '执行失败：未找到所需文件或资源。'
  if (/连接|网络|connection|network|ECONNREFUSED|fetch failed/i.test(text)) return '执行失败：服务或网络连接异常。'
  return /[\u3400-\u9fff]/u.test(text) ? text : '执行失败，展开详情查看错误信息。'
}

export function executionRecordPresentation(record = {}) {
  const name = record.tool_name || record.action_type || ''
  const request = record.payload || record.request
  const payload = request && typeof request === 'object' ? request : {}
  let result = record.result
  if (typeof result === 'string' && /^[\s]*[\[{]/.test(result)) {
    try { result = JSON.parse(result) } catch { result = undefined }
  }
  const resultData = result && typeof result === 'object' ? result : {}
  const requestBodies = [payload.content, payload.instruction, payload.text, payload.user_message].filter(value => typeof value === 'string' && value.trim())
  const isRequestEcho = value => typeof value === 'string' && requestBodies.some(request => value.includes(request))
  function withoutRequestEcho(value) {
    if (isRequestEcho(value)) return undefined
    if (Array.isArray(value)) return value.map(withoutRequestEcho).filter(nonempty)
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, withoutRequestEcho(entry)]).filter(([, entry]) => nonempty(entry)))
    return value
  }
  // Long legacy task titles can be the entire user request. Use the action name instead.
  const customLabel = [record.display_name, record.title].find(value => typeof value === 'string' && value.length <= 48 && /[\u3400-\u9fff]/u.test(value) && !isRequestEcho(value))
  const label = executionActionLabels[name] || customLabel || '执行操作'
  const error = record.error || record.error_message || resultData.error || resultData.error_message
  let summary = ''
  if (nonempty(error)) summary = failureSummary(typeof error === 'object' ? error.message || detailValue(error) : error)
  else {
    const candidates = [record.result_summary, resultData.summary, resultData.message, typeof result === 'string' ? result : ''].map(value => legacyResultSummary(value, record.status))
    // Some legacy results echo the request; do not reintroduce it as a result preview.
    const chosen = candidates.find(value => typeof value === 'string' && value.trim() && !isRequestEcho(value))
    if (chosen) summary = ['failed', 'skipped', 'timed_out'].includes(record.status) ? failureSummary(chosen) : compactExecutionText(chosen)
    else if (typeof resultData.exit_code === 'number') summary = resultData.exit_code === 0 ? '命令已完成，退出码 0。' : `命令返回退出码 ${resultData.exit_code}，请查看详情。`
    else if (resultData.path || resultData.file_path) summary = `文件：${compactExecutionText(resultData.path || resultData.file_path, 100)}`
    else if (Array.isArray(resultData.results)) summary = `返回 ${resultData.results.length} 项结果。`
    else if (Array.isArray(resultData.files)) summary = `找到 ${resultData.files.length} 个文件。`
  }
  const paths = Array.isArray(payload.changes) ? payload.changes.map(change => change.path).filter(Boolean) : []
  const target = paths.length ? `修改 ${paths.length} 个文件：${compactExecutionText(paths.join('、'), 85)}`
    : payload.command ? `运行命令：${compactExecutionText(payload.command, 95)}`
      : payload.path || payload.file_path ? `文件：${compactExecutionText(payload.path || payload.file_path, 100)}` : ''
  if (!summary && target && ['needs_confirmation', 'queued', 'running', 'executed', 'completed'].includes(record.status)) {
    summary = `${({ needs_confirmation: '待确认', queued: '待执行', running: '执行中', executed: '已完成', completed: '已完成' })[record.status]} · ${target}`
  }
  if (!summary) summary = ({
    queued: '等待执行。', approved: '已经批准，后续执行结果见正式执行记录。', running: '正在执行，结果尚未返回。', needs_confirmation: '等待你的确认后执行。',
    executed: '执行已完成，未提供结果摘要。', completed: '执行已完成，未提供结果摘要。',
    failed: '执行失败，未提供失败原因。', timed_out: '执行超时，请查看任务或服务状态。', skipped: '已跳过，未提供原因。', cancelled: '已取消。',
  })[record.status] || '暂未提供执行结果。'

  const details = [
    ['工具标识', name], ['记录编号', record.id || record.receipt_id], ['会话编号', record.conversation_id],
    ['来源消息', record.source_message_id], ['原始状态', record.status],
    ['命令', payload.command], ['文件', payload.path || payload.file_path],
    ['修改文件', Array.isArray(payload.changes) ? payload.changes.map(change => change.path).filter(Boolean).join('\n') : ''],
    ['错误详情', withoutRequestEcho(error)], ['结果详情', withoutRequestEcho(result)],
  ].filter(([, value]) => nonempty(value)).map(([label, value]) => ({ label, value: detailValue(value) }))
  // Failures and confirmation requests must remain visible and actionable.
  return { label, summary, details, technical: name === 'agent_task_update' && ['executed', 'completed'].includes(record.status) }
}

export function groupExecutionRecords(records = []) {
  const items = records.map(record => ({ record, presentation: executionRecordPresentation(record) }))
  return [
    { technical: false, items: items.filter(item => !item.presentation.technical) },
    { technical: true, items: items.filter(item => item.presentation.technical) },
  ].filter(group => group.items.length)
}
