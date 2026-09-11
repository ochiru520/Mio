export const taskStatusLabels = {
  running: '执行中', responding: '正在整理回复', ready: '等待继续', waiting_jobs: '等待后台结果',
  waiting_confirmation: '等待确认', waiting_user: '等待补充', budget_exhausted: '本轮预算已用完',
  paused: '已暂停', completed: '已完成', failed: '失败', cancelled: '已取消',
}

export function taskStatusLabel(status) {
  return taskStatusLabels[status] || '状态待确认'
}

export function executionStatus(receipts = [], creationJob = null) {
  if (creationJob) return null
  if (receipts.some((r) => r.status === 'needs_confirmation')) return '等待确认'
  if (receipts.some((r) => ['running', 'queued'].includes(r.status))) return '执行中'
  if (receipts.some((r) => ['failed', 'timed_out'].includes(r.status))) return '执行遇到问题'
  if (receipts.some((r) => r.status === 'cancelled')) return '已停止'
  return receipts.length ? '工具执行完成' : ''
}

export function canResumeTask(task) {
  return ['paused', 'failed', 'budget_exhausted', 'waiting_user'].includes(task.status)
}
