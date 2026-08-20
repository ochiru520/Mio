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
        detail = data.detail || detail
        structuredDetail = typeof data.detail === 'object' && data.detail ? data.detail : null
      } catch {
        // Keep the HTTP fallback when the response is not JSON.
      }
      throw requestError(structuredDetail?.message || String(detail), 'http_error', {
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
