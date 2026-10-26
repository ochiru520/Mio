import { computed, ref } from 'vue'

const keyOf = item => `${item.task_id}:${item.id}`
export function notificationTarget(item) {
  const source = item?.task_id || item?.conversation_id ? item : item?.target
  const task = source?.task_id, conversation = source?.conversation_id
  return typeof task === 'string' && task.trim() && typeof conversation === 'string' && conversation.trim()
    ? { task_id: task, conversation_id: conversation } : null
}

export function createNotificationState(api, isVisible = () => !globalThis.document?.hidden) {
  const items = ref([]), error = ref(''), open = ref(false)
  const hidden = ref(new Set()), presented = new Set(), presenting = new Set(), dismissing = new Set()
  const visible = computed(() => items.value.filter(item => !item.dismissed && !item.resolved && !hidden.value.has(keyOf(item))))
  const pending = computed(() => visible.value.filter(item => item.actionable))
  const completed = computed(() => visible.value.filter(item => !item.actionable))
  let disposed = false, inflight = null
  async function present() {
    if (disposed || !open.value || !isVisible()) return
    for (const item of visible.value) {
      const key = keyOf(item)
      if (disposed || !open.value || !isVisible()) return
      if (item.presented_at || presented.has(key) || presenting.has(key)) continue
      presenting.add(key)
      try { await api.presentNotification(item.task_id, item.id); presented.add(key) }
      catch (cause) { if (!disposed) error.value = cause.message }
      finally { presenting.delete(key) }
    }
  }
  function load() {
    if (inflight) return inflight
    if (disposed || !isVisible()) return Promise.resolve(false)
    inflight = Promise.resolve().then(async () => {
      try {
        const data = await api.allNotifications()
        if (disposed) return false
        const unique = new Map()
        for (const item of data.notifications || []) if (item?.task_id && item.id != null) {
          const key = keyOf(item), previous = unique.get(key)
          unique.set(key, previous ? { ...previous, ...item, dismissed: previous.dismissed || item.dismissed, resolved: previous.resolved || item.resolved } : item)
        }
        items.value = [...unique.values()]; error.value = ''
        await present(); return true
      } catch (cause) { if (!disposed) error.value = cause.message; return false }
      finally { inflight = null }
    })
    return inflight
  }
  async function dismiss(item) {
    const key = keyOf(item)
    if (disposed || dismissing.has(key)) return
    dismissing.add(key)
    try {
      await api.dismissNotification(item.task_id, item.id)
      if (!disposed) hidden.value = new Set([...hidden.value, key])
    } catch (cause) { if (!disposed) error.value = cause.message }
    finally { dismissing.delete(key) }
  }
  function target(item) {
    const value = notificationTarget(item)
    if (!value) error.value = '此提醒缺少任务或会话信息，无法打开。'
    return value
  }
  return { items, error, open, visible, pending, completed, load, present, dismiss, target, dispose: () => { disposed = true } }
}
