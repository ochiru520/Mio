import { ref } from 'vue'
import { loadRuntimeSettings } from './services/settingsApi.js'
import { saveRuntimeSettingsWithRecovery } from './services/runtimeSettingsSave.js'

const keys = ['chat_summary_strategy', 'agent_summary_strategy']
const defaults = () => Object.fromEntries(keys.map(key => [key, 'independent']))

export function useCacheSummarySettings() {
  const draft = ref(defaults()), ready = ref(false), busy = ref(false), error = ref(''), feedback = ref('')
  let baseline = defaults(), revision = '', disposed = false
  function apply(result) {
    baseline = Object.fromEntries(keys.map(key => [key, result.settings?.[key] || 'independent']))
    draft.value = { ...baseline }; revision = result.revision || ''; ready.value = true
  }
  async function load() {
    if (busy.value) return false
    busy.value = true; error.value = ''; feedback.value = ''
    try {
      const result = await loadRuntimeSettings()
      if (disposed) return false
      apply(result); return true
    } catch (cause) { if (!disposed) error.value = cause.message; return false }
    finally { if (!disposed) busy.value = false }
  }
  async function save() {
    if (busy.value || !ready.value) return false
    const changes = Object.fromEntries(keys.filter(key => draft.value[key] !== baseline[key]).map(key => [key, draft.value[key]]))
    if (!Object.keys(changes).length) { feedback.value = '设置未变化'; return true }
    busy.value = true; error.value = ''; feedback.value = ''
    try {
      const result = await saveRuntimeSettingsWithRecovery(changes, { baseline, revision })
      if (disposed) return false
      apply(result); feedback.value = '摘要策略已保存，后续压缩时生效'; return true
    } catch (cause) { if (!disposed) error.value = cause.message; return false }
    finally { if (!disposed) busy.value = false }
  }
  return { draft, ready, busy, error, feedback, load, save, dispose: () => { disposed = true } }
}
