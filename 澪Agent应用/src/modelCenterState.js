export function formatModelBytes(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '尚未提供大小'
  const bytes = Math.max(0, Number(value))
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(1)} GB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

export function modelStatus(item, dependency) {
  const progress = dependency?.progress
  if (progress?.stage === 'paused') return '下载已暂停'
  if (dependency?.installing) return '正在安装'
  if (progress?.stage === 'cancelled') return '下载已取消'
  if (['error', 'failed'].includes(progress?.stage) || dependency?.last_error) return '安装未完成，可重试'
  if (item.status === 'checking') return '安装与运行状态待检查'
  if (item.status === 'unknown') return '当前状态待确认'
  if (item.status === 'loading') return '模型加载中，尚未就绪'
  if (item.status === 'degraded') return '已安装，当前不可用'
  if (item.running) return '服务运行中，可实测'
  if (item.status === 'unavailable') return '当前版本不可用'
  if (item.status === 'ready') return '环境已就绪，可实测'
  if (item.installed) return '文件已安装，待启动验证'
  return '尚未安装'
}

export function downloadActions(progress = {}) {
  return ['pause', 'resume', 'cancel'].filter(action => progress[`can_${action}`] === true)
}
