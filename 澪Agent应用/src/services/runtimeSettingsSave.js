import { loadRuntimeSettings, saveRuntimeSettings } from './settingsApi.js'

const sameValue = (left, right) => JSON.stringify(left) === JSON.stringify(right)

// A global revision may change because a different category or window was saved.
// Rebase only untouched fields, and retry once under the new revision.
export async function saveRuntimeSettingsWithRecovery(changes, { baseline, revision }) {
  try {
    return await saveRuntimeSettings(changes, { revision })
  } catch (error) {
    if (error.status !== 409) throw error
    const latest = await loadRuntimeSettings()
    if (!latest.settings || !latest.revision) throw error
    const conflicts = Object.keys(changes).filter(key =>
      !sameValue(latest.settings[key], baseline[key]) && !sameValue(latest.settings[key], changes[key]))
    if (conflicts.length) {
      error.message = '同一项设置已在其他窗口修改。你的修改已保留；可重新读取最新设置后调整并保存。'
      throw error
    }
    try {
      return await saveRuntimeSettings(changes, { revision: latest.revision })
    } catch (retryError) {
      if (retryError.status === 409) retryError.message = '设置仍在被其他窗口更新。你的修改已保留，请稍后再保存或重新读取设置。'
      throw retryError
    }
  }
}
