import { loadSourceHandoff } from '../services/appRuntimeApi.js'

// Poll the source task without allowing a late read to overwrite a new send,
// another conversation, or a disposed window. Same-generation reads coalesce.
export function useTaskHandoff(deps, load = loadSourceHandoff) {
  let pending = null, disposed = false
  function refreshSourceHandoff() {
    const conversationId = deps.selectedConversationId.value
    if (disposed || deps.isAgentWorkspace || !conversationId) return Promise.resolve()
    const version = deps.handoffLoadVersion, previous = deps.latestTaskHandoff.value
    if (pending?.conversationId === conversationId && pending.version === version && pending.previous === previous) return pending.promise
    const request = { conversationId, version, previous }
    request.promise = Promise.resolve().then(() => load(conversationId)).then(result => {
      if (!disposed && deps.selectedConversationId.value === conversationId && deps.handoffLoadVersion === version && deps.latestTaskHandoff.value === previous) {
        deps.latestTaskHandoff.value = result.handoff || null
      }
    }).catch(() => {
      // Transient status failures must not affect chat or discard a live task.
    }).finally(() => { if (pending === request) pending = null })
    pending = request
    return request.promise
  }
  return { refreshSourceHandoff, disposeHandoff: () => { disposed = true } }
}
