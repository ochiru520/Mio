export const DEFAULT_CONTEXT_MAX_TOKENS = 272000

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
  return { used, limit, percent, amount, compression, label: `上下文预算：${amount}（${percent.toFixed(1)}%）；${compression}` }
}
