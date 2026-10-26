function tokenCount(value) {
  const count = Number(value)
  return Number.isFinite(count) && count > 0 ? count : 0
}

function reportedCacheRatio(usage) {
  if(usage?.cached_tokens_reporting==='partial')return null
  if(usage?.cache_hit_rate != null && Number.isFinite(Number(usage.cache_hit_rate)))return Math.max(0,Math.min(1,Number(usage.cache_hit_rate)))
  if (usage?.cache_hit_ratio != null && Number.isFinite(Number(usage.cache_hit_ratio))) {
    const ratio = Number(usage.cache_hit_ratio)
    return ratio >= 0 && ratio <= 1 ? ratio : null
  }
  const input = tokenCount(usage?.prompt_tokens)
  const cached = tokenCount(usage?.cached_prompt_tokens)
  if (usage?.cached_prompt_tokens == null) return null
  const reported = ['reported', 'reported_positive', 'reported_zero'].includes(usage?.cached_tokens_reporting)
  return input > 0 && (reported || cached > 0) && cached <= input ? cached / input : null
}

export function messageCacheRateLabel(usage) {
  const ratio = reportedCacheRatio(usage)
  return ratio === null ? usage?.cached_tokens_reporting==='partial' ? '缓存统计不完整' : '缓存 未知' : `缓存 ${(ratio * 100).toFixed(1)}%`
}

export function messageTokenUsageTitle(turn, cumulative = false) {
  const input = tokenCount(turn?.prompt_tokens)
  const cached = tokenCount(turn?.cached_prompt_tokens)
  const output = tokenCount(turn?.completion_tokens)
  const reasoning = tokenCount(turn?.reasoning_tokens)
  const format = (count) => count.toLocaleString('zh-CN')
  const reportedRatio = reportedCacheRatio(turn)
  const ratio = reportedRatio !== null ? `占${turn?.cache_reporting_coverage != null && turn.cache_reporting_coverage < 1 ? '已报告样本' : ''}输入 ${(reportedRatio * 100).toFixed(1)}%` : '占输入比例未知'
  const coverage = turn?.cache_reporting_coverage
  return [
    cumulative ? '本轮多次模型调用的累计用量，不等于当前上下文占用。' : '本轮模型用量',
    `输入：${format(input)} token（已包含缓存输入）`,
    reportedRatio === null ? '上游缓存：未知（占输入比例未知；供应商未报告，旧记录也无法补推）'
      : `上游报告缓存：${format(cached)} token（${ratio}）`,
    `输出：${format(output)} token（其中推理 ${format(reasoning)} token）`,
    '缓存命中率按上游报告的缓存输入 ÷ 总输入计算；不含输出，不等于节省费用比例。',
    ...(coverage != null ? [`缓存报告覆盖率：${(Math.max(0, Math.min(1, Number(coverage))) * 100).toFixed(1)}%；命中率仅覆盖已报告样本。`] : []),
    ...(Number(turn?.unknown_cost_calls) > 0 ? [`${Number(turn.unknown_cost_calls)} 次费用未知，不能视为免费。`] : []),
  ].join('\n')
}
