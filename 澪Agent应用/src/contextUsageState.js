export const DEFAULT_CONTEXT_MAX_TOKENS = 272000
const categoryNames = {
  fixed_instructions: '人格与固定规则', tool_schema: '工具说明', runtime_tool_catalog: '工具目录',
  runtime_compressed_memory: '历史摘要', runtime_project: '项目资料', history: '历史消息',
  current_request: '当前问题', runtime_agent_state: '任务状态', runtime_current_state: '当前状态',
  runtime_structured_memory: '私人记忆', runtime_diary: '日记摘录', runtime_other: '运行资料',
  runtime_framing: '资料封装', runtime_current_time: '当前时间', runtime_review: '回顾',
}

function positiveTokenCount(value, fallback = 0) {
  const count = Number(value)
  return Number.isFinite(count) && count > 0 ? count : fallback
}

export function contextUsagePresentation(usage = {}) {
  const used = positiveTokenCount(usage?.used_tokens)
  const limit = positiveTokenCount(usage?.max_tokens, DEFAULT_CONTEXT_MAX_TOKENS)
  // Derive the ratio from the same token values we display. Legacy character
  // fields and cumulative billed usage describe different quantities.
  const percent = used / limit * 100
  const unit = usage?.estimated === false ? 'token' : 'token（估算）'
  const count = Math.max(0, Number(usage?.compression_count) || 0)
  const compression = usage?.compression_warning
    ? String(usage.compression_warning)
    : usage?.has_summary
    ? `已自动压缩${count ? ` ${count} 次` : ''}，完整记录保留`
    : usage?.auto_compression_enabled === false ? '自动压缩未启用' : '接近预算时自动压缩'
  const amount = `${used.toLocaleString('zh-CN')} / ${limit.toLocaleString('zh-CN')} ${unit}`
  const grouped = new Map()
  const measured = usage?.last_request_breakdown || usage?.breakdown
  for (const block of measured?.blocks || []) {
    const category = String(block.category || 'runtime_other')
    grouped.set(category, (grouped.get(category) || 0) + positiveTokenCount(block.estimated_tokens))
  }
  const breakdown = [...grouped].map(([category, tokens]) => ({ category, label: categoryNames[category] || '其他资料', tokens }))
  const details = breakdown.map(item => `${item.label}：${item.tokens.toLocaleString('zh-CN')} token（估算）`)
  if (usage?.last_request_breakdown) details.unshift('上次实际请求的分项（本地估算，和本次预览占用可能不同）：')
  if (measured?.scope === 'chat_memory_and_history_only' && !usage?.last_request_breakdown) details.unshift('当前仅估算聊天资料与历史；人格、工具和图片未包含在这些分项。')
  if (measured?.unestimated_image_count > 0) details.push(`另有 ${measured.unestimated_image_count} 张图片，用量未知，未伪估入文本分项`)
  if (usage?.before_compression_tokens != null) details.push(`压缩前后：${Number(usage.before_compression_tokens).toLocaleString('zh-CN')} → ${used.toLocaleString('zh-CN')} token（估算）`)
  const route = usage?.model_window_tokens ? usage : measured
  if (route?.model_window_tokens) details.push(`模型窗口 ${Number(route.model_window_tokens).toLocaleString('zh-CN')} token，预留输出 ${Number(route.output_reserved_tokens || 0).toLocaleString('zh-CN')}；用户期望预算 ${Number(route.configured_max_tokens || limit).toLocaleString('zh-CN')}`)
  else if (route?.model_window_source === 'unverified_default') details.push('模型真实窗口未知；保留用户期望预算，供应商超限时只在持久减负有进展后有限重试。')
  if (details.length) details.push('分项为本地文本估算，与供应商输入计费和累计用量不同。')
  const inputReferences = usage?.input_references || measured?.input_references || []
  for (const item of inputReferences) details.push(`${item.kind === 'image' ? '较早图片已省略' : '较早工具结果已裁剪'}，本地原件引用：${item.id}；需要细节时回读，不能猜测或重放动作。`)
  const label = `上下文预算：${amount}（${percent.toFixed(1)}%）；${compression}`
  return { used, limit, percent, amount, compression, breakdown, inputReferences, title: [label, ...details].join('\n'), label: [label, ...details].join('\n') }
}
