export function petStartupPresentation(pet = {}, action = '') {
  const state = ['start', 'restart'].includes(action) ? 'starting' : pet.startup?.state || (pet.running ? 'unverified' : 'stopped')
  const labels = { ready: '运行中', starting: '启动中', failed: '启动异常', unverified: '待确认显示', stopped: '未启动' }
  return {
    state, label: labels[state] || '待确认显示', ready: state === 'ready',
    message: ['start', 'restart'].includes(action) ? '正在加载桌宠，请稍候…' : pet.startup?.message || '',
  }
}
