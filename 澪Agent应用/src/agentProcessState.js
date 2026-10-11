export function applyAnalysisEvent(blocks, event) {
  const p=event.payload || {}, key=p.stream_id || `${event.run_id}:${p.round ?? 0}`
  if (!event.event_type.startsWith('analysis_')) return blocks
  const next=blocks.map(b => ({...b})), index=next.findIndex(b => b.id===key)
  const block=index<0 ? {id:key,content:'',status:'running',round:p.round ?? 0,sequence:event.id} : next[index]
  if (event.event_type==='analysis_text_delta') block.content += p.text || ''
  if (event.event_type==='analysis_language_notice') block.languageNotice = p.text || '模型返回的分析不是中文，以下显示中文执行进度。'
  if (event.event_type==='analysis_stream_finished') block.status=p.status==='generated' ? 'completed' : 'interrupted'
  if (index<0) next.push(block); else next[index]=block
  return next
}

// Old provider streams may predate server-side language checks. Keep identifiers
// and paths intact, but never use English narrative as a user-facing preview.
export function visibleAnalysis(text = '') {
  let suppressed = false
  const parts = String(text).split(/(^[ \t]{0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]{0,3}\2[ \t]*(?:\n|$)|(?![\s\S])))/gm)
  const paragraphs = parts.filter(part => part && !/^(`{3,}|~{3,})$/.test(part)).flatMap(part => /^[ \t]{0,3}(`{3,}|~{3,})/.test(part) ? [part] : part.split(/(?<=[。！？\n])/u)).filter(paragraph => {
    if (/^[ \t]{0,3}(`{3,}|~{3,})/.test(paragraph)) return true
    const narrative = paragraph.replace(/`[^`]*`|https?:\/\/\S+|[A-Za-z]:[\\/][^\s，。；！？]*|\b[A-Za-z][\w-]*[._/][\w./-]+\b|\b[A-Z][A-Z0-9_-]+\b/g, '')
      .replace(/\b(?:Python|JavaScript|TypeScript|Node\.js|Vue|React|pytest|npm|pnpm|node|pip|ruff|mypy|vitest|eslint|Docker|bash|uvicorn|FastAPI|PowerShell|Rust|Cargo|Git|SQLite|Markdown|Playwright)\b/gi, '')
    const latin = (narrative.match(/[A-Za-z]/g) || []).length
    const chinese = (narrative.match(/[\u3400-\u9fff]/g) || []).length
    const hidden = latin > 0 && chinese < latin * .6
    if (hidden) suppressed = true
    return !hidden
  })
  return { text: paragraphs.join('').trim(), suppressed }
}

export function processOperations(events = []) {
  const steps = new Map()
  for (const event of events) {
    if (event.event_type !== 'tool_batch_completed') continue
    for (const [index, step] of (event.payload?.tools || []).entries()) {
      if (step.tool_name === 'agent_task_update') continue
      steps.set(step.step_id || `${event.id}:${index}`, step)
    }
  }
  return [...steps.values()]
}

export function receiptSummary(receipt) {
  if (receipt?.error) return receipt.error
  const r=receipt?.result || {}
  if (receipt.tool_name==='agent_task_update') return r.snapshot?.summary || r.snapshot?.next_step || ''
  if (receipt.tool_name==='execution_capabilities') {
    const code=r.code || r.code_workspace || {}, desktop=r.desktop || {}
    return [code.root, code.write_enabled ? '可提出代码修改' : '', code.execute_enabled ? '可提出终端命令' : '', desktop.enabled ? '电脑控制已启用' : ''].filter(Boolean).join(' · ')
  }
  return [r.summary,r.path,r.command,typeof r.exit_code==='number' ? `退出码 ${r.exit_code}` : '',r.status].filter(Boolean).join(' · ').slice(0,450)
}
