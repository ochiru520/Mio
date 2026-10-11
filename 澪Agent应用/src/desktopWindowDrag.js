export function blockStandardWindowDrag(event, reportError = () => {}) {
  if (event.target.closest('button, input, select, textarea, a, nav, [role=button]')) return
  if (window.__mioWindowMoveActive) {
    event.stopPropagation()
    return
  }
  const drag = window.pywebview?.api?.window_drag
  if (!drag) return
  const host = window, gesture = {}
  // A few WebView2 builds can emit mousedown without exposing pointerdown to
  // the page. Keep the same titlebar usable by falling back to the native
  // caption drag on that event.
  event.preventDefault()
  event.stopPropagation()
  host.__mioWindowMoveActive = gesture
  void Promise.resolve().then(() => drag()).then(result => {
    if (result?.ok === false) reportError(result.error || '窗口拖动失败')
  }).catch(cause => reportError(cause.message)).finally(() => {
    host.setTimeout(() => { if (host.__mioWindowMoveActive === gesture) host.__mioWindowMoveActive = false }, 0)
  })
}

export function beginWindowMove(event, reportError = () => {}) {
  if ((event.button !== undefined && event.button !== 0) || event.target.closest('button, input, select, textarea, a, nav, [role=button]')) return
  const move = window.pywebview?.api?.window_move_by
  const fallback = window.pywebview?.api?.window_drag
  if (!move && !fallback) return
  const host = window, gesture = {}
  event.stopPropagation()
  event.preventDefault()
  if (!move) {
    host.__mioWindowMoveActive = gesture
    void Promise.resolve().then(() => fallback()).then(result => {
      if (result?.ok === false) reportError(result.error || '窗口拖动失败')
    }).catch(cause => reportError(cause.message)).finally(() => {
      host.setTimeout(() => { if (host.__mioWindowMoveActive === gesture) host.__mioWindowMoveActive = false }, 0)
    })
    return
  }
  host.__mioWindowMoveActive = gesture
  const target = event.currentTarget, pointer = event.pointerId
  const screenPoint = next => ({
    x: Number.isFinite(next?.screenX) ? next.screenX : ((window.screenX || 0) + (Number(next?.clientX) || 0)),
    y: Number.isFinite(next?.screenY) ? next.screenY : ((window.screenY || 0) + (Number(next?.clientY) || 0)),
  })
  const start = screenPoint(event), id = crypto.randomUUID()
  let x = 0, y = 0, finished = false, sending = false, dirty = false, fallbackStarted = false
  let captured = false
  try {
    if (Number.isFinite(pointer) && target.setPointerCapture) {
      target.setPointerCapture(pointer)
      captured = true
    }
  } catch {
    // Some WebView2 versions reject capture on a frameless window. Document
    // listeners below keep the gesture alive in that case.
  }
  async function send() {
    dirty = true
    if (sending) return
    sending = true
    try {
      while (dirty) {
        dirty = false
        const result = await move(x, y, id, finished)
        if (result?.ok === false) throw new Error(result.error || '窗口拖动失败')
      }
    } catch (cause) {
      if (!finished && !fallbackStarted && fallback) {
        fallbackStarted = true
        try {
          const result = await fallback()
          if (result?.ok !== false) return
          reportError(result.error || cause.message)
        } catch (fallbackCause) { reportError(fallbackCause.message || cause.message) }
      } else reportError(cause.message)
    }
    finally { sending = false }
  }
  function update(next) {
    if (Number.isFinite(pointer) && next.pointerId !== pointer) return
    const point = screenPoint(next)
    x = point.x - start.x; y = point.y - start.y
    void send()
  }
  let eventTarget = captured ? target : document
  let options = captured ? undefined : true
  function switchToDocumentCapture() {
    if (finished || eventTarget === document) return
    eventTarget.removeEventListener('pointermove', update, options)
    eventTarget.removeEventListener('pointerup', end, options)
    eventTarget.removeEventListener('pointercancel', end, options)
    captured = false
    eventTarget = document
    options = true
    eventTarget.addEventListener('pointermove', update, options)
    eventTarget.addEventListener('pointerup', end, options)
    eventTarget.addEventListener('pointercancel', end, options)
  }
  function handleLostPointerCapture() {
    switchToDocumentCapture()
  }
  function cleanup() {
    eventTarget.removeEventListener('pointermove', update, options)
    eventTarget.removeEventListener('pointerup', end, options)
    eventTarget.removeEventListener('pointercancel', end, options)
    target.removeEventListener('lostpointercapture', handleLostPointerCapture)
    host.removeEventListener('blur', cancel)
    if (captured && target.hasPointerCapture?.(pointer)) target.releasePointerCapture(pointer)
    if (host.__mioWindowMoveActive === gesture) host.__mioWindowMoveActive = false
  }
  function end(next) {
    if (Number.isFinite(pointer) && next.pointerId !== pointer || finished) return
    if (next.type === 'pointerup') {
      const point = screenPoint(next)
      x = point.x - start.x; y = point.y - start.y
    }
    finished = true
    cleanup()
    void send()
  }
  function cancel() { end({ type: 'pointercancel', pointerId: pointer }) }
  eventTarget.addEventListener('pointermove', update, options)
  eventTarget.addEventListener('pointerup', end, options)
  eventTarget.addEventListener('pointercancel', end, options)
  if (captured) target.addEventListener('lostpointercapture', handleLostPointerCapture)
  host.addEventListener('blur', cancel)
  void send()
}
