export function contextUsagePresentation(usage = {}) {
  const tokenBased = usage.used_tokens != null && usage.max_tokens != null
  const used = Math.max(0, Number(tokenBased ? usage.used_tokens : usage.used_chars) || 0)
  const limit = Math.max(1, Number(tokenBased ? usage.max_tokens : usage.max_chars) || 18000)
  const percent = Math.max(0, Number(usage.percent ?? (used / limit * 100)) || 0)
  const unit = tokenBased ? 'token（估算）' : '字'
  const count = Math.max(0, Number(usage.compression_count) || 0)
  const compression = usage.compression_warning
    ? String(usage.compression_warning)
    : usage.has_summary
    ? `已自动压缩${count ? ` ${count} 次` : ''}，完整记录保留`
    : usage.auto_compression_enabled === false ? '自动压缩未启用' : '接近预算时自动压缩'
  const amount = `${used.toLocaleString('zh-CN')} / ${limit.toLocaleString('zh-CN')} ${unit}`
  return { used, limit, percent, amount, compression, label: `当前上下文：${amount}（${percent.toFixed(1)}%）；${compression}` }
}
