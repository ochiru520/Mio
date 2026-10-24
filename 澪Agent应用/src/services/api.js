export const API_DEADLINES_MS = Object.freeze({
  read: 15_000,
  mutation: 30_000,
  model: 240_000,
  media: 180_000,
  backup: 600_000,
})

export function classifyApiRequest(url, method = 'GET') {
  const path = String(url || '').toLowerCase()
  const normalizedMethod = String(method || 'GET').toUpperCase()
  if (path.includes('/backups') || path.includes('/migrations')) return 'backup'
  if (path.includes('/voice/audio') || path.includes('/voice/test') || path.includes('/call/turn')) return 'media'
  if (
    path.endsWith('/chat')
    || path.includes('/generate')
    || path.includes('/analyze')
    || path.includes('/web-search/test')
    || path.includes('/providers/discover')
    || (path.includes('/models/') && path.endsWith('/test'))
  ) return 'model'
  return normalizedMethod === 'GET' || normalizedMethod === 'HEAD' ? 'read' : 'mutation'
}

function requestError(message, code, extra = {}) {
  const error = new Error(message)
  error.code = code
  Object.assign(error, extra)
  return error
}

function errorDetailMessage(value, depth = 0) {
  if (depth > 4 || value == null) return ''
  if (typeof value === 'string') return value.trim()
  if (Array.isArray(value)) {
    return value.slice(0, 5).map(item => errorDetailMessage(item, depth + 1)).filter(Boolean).join('；')
  }
  if (typeof value !== 'object') return ''
  const message = errorDetailMessage(value.message || value.msg || value.detail || value.error, depth + 1)
  if (!message) return ''
  const fields = {
    backend_root: '后端源码目录', desktop_root: '桌面应用源码目录',
    expected_revision: '设置版本', goal: '改造目标', acceptance: '验收要求',
    base_url: '服务地址', api_key: 'API Key', model: '模型名称',
  }
  const location = Array.isArray(value.loc) ? value.loc.filter(part => !['body', 'query', 'path'].includes(part)) : []
  const field = location.map(part => fields[part] || String(part)).join('.')
  const validationMessages = {
    missing: '请填写此项', string_too_short: '填写的内容太短',
    greater_than_equal: '数值低于允许范围', list_too_short: '至少需要一项内容',
  }
  return field ? `${field}：${validationMessages[value.type] || message}` : message
}

function responseBody(response, responseType) {
  if (responseType === 'blob') return response.blob()
  if (responseType === 'text') return response.text()
  if (responseType === 'response') return response
  return response.json()
}

export async function apiRequest(url, options = {}) {
  const {
    deadlineClass = classifyApiRequest(url, options.method),
    timeoutMs = API_DEADLINES_MS[deadlineClass] || API_DEADLINES_MS.mutation,
    responseType = 'json',
    signal: callerSignal,
    headers,
    ...fetchOptions
  } = options
  const controller = new AbortController()
  let abortKind = ''
  let timeoutId = null
  const onCallerAbort = () => {
    if (controller.signal.aborted) return
    abortKind = 'caller'
    controller.abort(callerSignal?.reason)
  }
  if (callerSignal?.aborted) onCallerAbort()
  else callerSignal?.addEventListener('abort', onCallerAbort, { once: true })
  if (Number(timeoutMs) > 0) {
    timeoutId = setTimeout(() => {
      if (controller.signal.aborted) return
      abortKind = 'deadline'
      controller.abort('deadline_exceeded')
    }, Number(timeoutMs))
  }

  try {
    const response = await fetch(url, {
      headers: { 'Content-Type': 'application/json', ...(headers || {}) },
      ...fetchOptions,
      signal: controller.signal,
    })
    if (!response.ok) {
      let detail = `请求失败：HTTP ${response.status}`
      let structuredDetail = null
      try {
        const data = await response.json()
        detail = errorDetailMessage(data.detail || data.message || data.error) || detail
        structuredDetail = typeof data.detail === 'object' && data.detail ? data.detail : null
      } catch {
        // Keep the HTTP fallback when the response is not JSON.
      }
      throw requestError(detail, 'http_error', {
        status: response.status,
        detail: structuredDetail,
      })
    }
    if (response.status === 204) return null
    return await responseBody(response, responseType)
  } catch (error) {
    if (abortKind === 'deadline') {
      throw requestError(
        `请求超过 ${Math.ceil(Number(timeoutMs) / 1000)} 秒仍未完成，已停止等待`,
        'request_timeout',
        { timeoutMs: Number(timeoutMs), deadlineClass },
      )
    }
    if (abortKind === 'caller' || callerSignal?.aborted) {
      throw requestError('请求已取消', 'request_cancelled')
    }
    if (error?.code === 'http_error') throw error
    throw requestError('Mio 的后台暂时没有响应，桌面应用正在尝试恢复，请稍后重试', 'network_error', {
      cause: error,
    })
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId)
    callerSignal?.removeEventListener('abort', onCallerAbort)
  }
}
