export const requestCategoryLabels = { chat: '聊天', agent_planner: 'Agent 规划', agent_reply: '最终回复',
  context_compression: '上下文压缩', memory: '后台记忆', background: '其他后台', legacy: '旧记录' }

export function coverageLabel(value) {
  return value == null || !Number.isFinite(Number(value)) ? '未知' : `${(Math.max(0, Math.min(1, Number(value))) * 100).toFixed(1)}%`
}

export function cacheUsageLabel(item = {}) {
  return item.cache_hit_ratio == null ? '未知' : coverageLabel(item.cache_hit_ratio)
}
