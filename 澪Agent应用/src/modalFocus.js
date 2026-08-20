const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusableElements(container) {
  if (!container?.querySelectorAll) return []
  return [...container.querySelectorAll(FOCUSABLE_SELECTOR)].filter((element) => (
    !element.hidden && element.getAttribute?.('aria-hidden') !== 'true'
  ))
}

export function focusModal(container) {
  if (!container) return null
  const target = container.querySelector?.('[autofocus]')
    || container.querySelector?.('[data-modal-initial-focus]')
    || focusableElements(container)[0]
    || container
  target.focus?.({ preventScroll: true })
  return target
}

export function trapModalFocus(event, container) {
  if (event?.key !== 'Tab' || !container) return false
  const elements = focusableElements(container)
  if (!elements.length) {
    event.preventDefault?.()
    container.focus?.({ preventScroll: true })
    return true
  }

  const first = elements[0]
  const last = elements[elements.length - 1]
  const active = container.ownerDocument?.activeElement
  if (event.shiftKey && (active === first || !container.contains?.(active))) {
    event.preventDefault?.()
    last.focus?.({ preventScroll: true })
  } else if (!event.shiftKey && (active === last || !container.contains?.(active))) {
    event.preventDefault?.()
    first.focus?.({ preventScroll: true })
  }
  return true
}

export function restoreModalFocus(element) {
  if (!element?.isConnected) return false
  element.focus?.({ preventScroll: true })
  return true
}
