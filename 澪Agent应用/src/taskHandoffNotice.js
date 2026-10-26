const titles = {
  ready: 'Agent 等待继续', running: 'Agent 正在处理', responding: 'Agent 正在整理回复',
  waiting_jobs: 'Agent 等待后台结果', waiting_terminal: 'Agent 等待终端结果',
  waiting_confirmation: 'Agent 等待确认', waiting_user: 'Agent 等待补充',
  paused: 'Agent 任务已暂停', budget_exhausted: 'Agent 本轮预算已用完',
}
export function handoffNoticeTitle(handoff) {
  return handoff?.task_id ? titles[handoff.status] || '' : ''
}
export function handoffNoticeKey(conversationId, handoff) {
  return handoff?.task_id ? JSON.stringify([conversationId, handoff.task_id]) : ''
}
