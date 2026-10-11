// Positions remain in this window only; no chat content is persisted.
const positions = new Map()
export function captureReadingPosition(scroll) {
  if (!scroll) return null
  const top=scroll.getBoundingClientRect().top
  const anchor=[...scroll.querySelectorAll('[data-message-anchor]')].find(el=>el.getBoundingClientRect().bottom>top+4)
  return {top:scroll.scrollTop,atBottom:scroll.scrollHeight-scroll.clientHeight-scroll.scrollTop<48,
    anchor:anchor?.dataset.messageAnchor || '',offset:anchor ? anchor.getBoundingClientRect().top-top : 0}
}
export function restoreReadingPosition(scroll, position) {
  if (!scroll || !position) return
  if (position.atBottom) {scroll.scrollTop=scroll.scrollHeight;return}
  const anchor=[...scroll.querySelectorAll('[data-message-anchor]')].find(el=>el.dataset.messageAnchor===position.anchor)
  scroll.scrollTop=anchor ? scroll.scrollTop+anchor.getBoundingClientRect().top-scroll.getBoundingClientRect().top-position.offset : position.top
}
export function rememberReadingPosition(id, scroll) {if(id && scroll)positions.set(id,captureReadingPosition(scroll))}
export function savedReadingPosition(id) {return positions.get(id)}
