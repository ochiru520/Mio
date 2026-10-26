
import * as capabilitiesApi from '../services/capabilitiesApi.js'
import { ref } from 'vue'
import { apiRequest } from '../services/api.js'

export function useCapabilityChecks({ request = apiRequest, schedule = setTimeout, unschedule = clearTimeout } = {}) {
  const capabilities = ref([])
  const busy = ref(false)
  const sending = ref(false)
  const error = ref('')
  const confirmation = ref(null)
  const unsupported = ref(false)
  let timer = null
  let generation = 0
  let disposed = false

  function apply(value) {
    capabilities.value = value.capabilities || []
    busy.value = Boolean(value.busy)
    if (timer !== null) unschedule(timer)
    timer = busy.value && !disposed ? schedule(refresh, 1200) : null
  }
  async function refresh() {
    const current = ++generation
    try {
      const result = await capabilitiesApi.loadCapabilities({}, request)
      if (!disposed && current === generation) { apply(result); unsupported.value = false; error.value = '' }
    } catch (cause) {
      if (!disposed && current === generation) {
        if (cause.status === 404) {
          unsupported.value = true
          confirmation.value = null
          apply({ capabilities: [], busy: false })
          error.value = ''
          return
        }
        error.value = cause.message || '无法读取能力状态'
        if (timer !== null) unschedule(timer)
        timer = busy.value ? schedule(refresh, 3000) : null
      }
    }
  }
  function prepare(item) {
    if (!unsupported.value && !busy.value && !sending.value) confirmation.value = item
  }
  async function perform(item, action) {
    if (unsupported.value || sending.value || disposed) return
    sending.value = true
    error.value = ''
    const current = ++generation
    if (timer !== null) unschedule(timer)
    timer = null
    try {
      const result = await capabilitiesApi.submitCapabilities(item.id, action, action === 'check' ? { consent: true } : { run_id: item.run_id }, {}, request)
      if (!disposed && current === generation) apply(result)
    } catch (cause) {
      if (!disposed && current === generation) {
        error.value = cause.message || '能力检查未完成'
        // A timed-out POST may have reached the server. Reconcile before retrying.
        timer = schedule(refresh, 1200)
      }
    } finally {
      sending.value = false
      confirmation.value = null
    }
  }
  async function confirm() {
    if (confirmation.value) await perform(confirmation.value, 'check')
  }
  function dispose() {
    disposed = true
    generation++
    if (timer !== null) unschedule(timer)
  }
  return { capabilities, busy, sending, error, confirmation, unsupported, refresh, prepare, confirm,
    cancel: item => perform(item, 'cancel'), dispose }
}
