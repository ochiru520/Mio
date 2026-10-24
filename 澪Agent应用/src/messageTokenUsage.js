function tokenCount(value) {
  const count = Number(value)
  return Number.isFinite(count) && count > 0 ? count : 0
}

function reportedCacheRatio(usage) {
  const input = tokenCount(usage?.prompt_tokens)
  const cached = tokenCount(usage?.cached_prompt_tokens)
  // Existing records store both unreported usage and a reported zero as 0.
  // Avoid presenting either case as a confirmed cache miss.
  return input > 0 && cached > 0 && cached <= input ? cached / input : null
}

export function messageCacheRateLabel(usage) {
  const ratio = reportedCacheRatio(usage)
  return ratio === null ? '缓存 —' : `缓存 ${(ratio * 100).toFixed(1)}%`
}

export function messageTokenUsageTitle(turn, cumulative = false) {
  const input = tokenCount(turn?.prompt_tokens)
  const cached = tokenCount(turn?.cached_prompt_tokens)
  const output = tokenCount(turn?.completion_tokens)
  const reasoning = tokenCount(turn?.reasoning_tokens)
  const format = (count) => count.toLocaleString('zh-CN')
  const reportedRatio = reportedCacheRatio(turn)
  const ratio = reportedRatio !== null ? `占输入 ${(reportedRatio * 100).toFixed(1)}%`
    : input > 0 && cached === 0 ? '占输入 0.0%，实际命中率待确认' : '占输入比例未知'
  return [
    cumulative ? '本轮多次模型调用的累计用量，不等于当前上下文占用。' : '本轮模型用量',
    `输入：${format(input)} token（已包含缓存输入）`,
    `上游报告缓存：${format(cached)} token（${ratio}）`,
    `输出：${format(output)} token（其中推理 ${format(reasoning)} token）`,
    '缓存命中率按上游报告的缓存输入 ÷ 总输入计算；不含输出，不等于节省费用比例。',
    ...(cached === 0 ? ['缓存为 0 也可能是上游未报告，不能据此确认实际未命中。'] : []),
  ].join('\n')
}
