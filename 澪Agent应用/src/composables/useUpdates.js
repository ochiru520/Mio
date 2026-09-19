import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

/** Native updater client. No download URLs, keys or shell commands reach the UI. */
export function createUpdateClient(getBridge = () => globalThis.window?.pywebview?.api) {
  const state = ref({ state: 'idle', message: '正在读取更新状态', release: null })
  const error = ref('')
  const busy = ref(false)
  const available = ref(false)
  let revision = 0
  let refreshing = false
  async function refresh() {
    if (refreshing) return
    const bridge = getBridge()
    available.value = typeof bridge?.update_status === 'function'
    if (!available.value) return
    const current = revision
    refreshing = true
    try {
      const result = await bridge.update_status()
      if (current !== revision) return
      if (result.status) state.value = result.status
      if (!result.ok) error.value = result.error || '无法读取更新状态'
    } catch (reason) {
      error.value = reason.message || '无法连接桌面更新服务'
    } finally {
      refreshing = false
    }
  }
  async function invoke(command, ...args) {
    const allowed = new Set(['check_updates', 'download_update', 'cancel_update', 'configure_updates', 'install_update'])
    if (!allowed.has(command)) throw new Error('不支持的更新操作')
    const bridge = getBridge()
    if (typeof bridge?.[command] !== 'function') {
      error.value = '请在 Mio 桌面安装版中使用更新功能'
      return false
    }
    if (busy.value) return false
    busy.value = true
    error.value = ''
    revision++
    try {
      const result = await bridge[command](...args)
      if (result.status) state.value = result.status
      if (!result.ok) throw new Error(result.error || '更新操作失败')
      return true
    } catch (reason) {
      error.value = reason.message || '更新操作失败'
      return false
    } finally {
      busy.value = false
    }
  }
  const working = computed(() => busy.value || ['checking', 'downloading', 'preparing', 'installing'].includes(state.value.state))
  const progress = computed(() => Math.max(0, Math.min(100, state.value.total ? state.value.downloaded * 100 / state.value.total : 0)))
  return { state, error, busy, available, working, progress, refresh, invoke }
}

let shared
let subscribers = 0
let timer
export function useUpdates() {
  if (!shared) shared = createUpdateClient()
  onMounted(() => {
    subscribers++
    void shared.refresh()
    if (subscribers === 1) {
      window.addEventListener('pywebviewready', shared.refresh)
      timer = window.setInterval(shared.refresh, 3000)
    }
  })
  onBeforeUnmount(() => {
    subscribers--
    if (subscribers === 0) {
      window.removeEventListener('pywebviewready', shared.refresh)
      window.clearInterval(timer)
      timer = null
    }
  })
  return shared
}
