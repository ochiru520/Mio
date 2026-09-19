/** DataPrivacy operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import { cleanupAutomaticBackups } from '../services/dataPrivacyApi.js'
import { completeOnboarding as completeOnboardingRequest } from '../services/onboardingApi.js'
import { createCompleteBackup } from '../services/dataPrivacyApi.js'
import { deleteCompleteBackup } from '../services/dataPrivacyApi.js'
import { importCompleteBackup } from '../services/dataPrivacyApi.js'
import { loadDataPrivacy } from '../services/dataPrivacyApi.js'
import { loadOnboardingEnvironment } from '../services/onboardingApi.js'
import { restoreCompleteBackup } from '../services/dataPrivacyApi.js'
import { setPrivacyPaused } from '../services/dataPrivacyApi.js'

export function useDataPrivacy(deps) {
  async function completeOnboarding(payload) {
    deps.onboardingBusy.value = true
    deps.onboardingError.value = ''
    try {
      await completeOnboardingRequest(payload)
      window.location.reload()
    } catch (error) {
      deps.onboardingError.value = error.message
    } finally {
      deps.onboardingBusy.value = false
    }
  }
  async function refreshOnboardingEnvironment() {
    deps.onboardingEnvironmentBusy.value = true
    deps.onboardingError.value = ''
    try {
      deps.onboardingEnvironment.value = await loadOnboardingEnvironment()
    } catch (error) {
      deps.onboardingEnvironment.value = null
      deps.onboardingError.value = error.message
    } finally {
      deps.onboardingEnvironmentBusy.value = false
    }
  }
  async function refreshDataPrivacy({ quiet = false } = {}) {
    if (!quiet) deps.dataPrivacyLoading.value = true
    try {
      deps.dataPrivacyState.value = await loadDataPrivacy()
    } catch (error) {
      deps.settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
    } finally {
      deps.dataPrivacyLoading.value = false
    }
  }
  async function createDataBackup() {
    deps.dataPrivacyBusy.value = 'create'
    try {
      await createCompleteBackup()
      await refreshDataPrivacy({ quiet: true })
      deps.settingsFeedback.value = { section: 'data', type: 'success', message: '完整备份已经创建并校验通过' }
    } catch (error) {
      deps.settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
    } finally {
      deps.dataPrivacyBusy.value = ''
    }
  }
  async function importDataBackup(event) {
    const file = event?.target?.files?.[0]
    if (!file) return
    deps.dataPrivacyBusy.value = 'import'
    try {
      await importCompleteBackup(file)
      await refreshDataPrivacy({ quiet: true })
      deps.settingsFeedback.value = { section: 'data', type: 'success', message: '完整备份已导入并校验通过，可以选择恢复' }
    } catch (error) {
      deps.settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
    } finally {
      event.target.value = ''
      deps.dataPrivacyBusy.value = ''
    }
  }
  async function restoreDataBackup(name) {
    const confirmed = await deps.showAppConfirm({
      title: '恢复本地数据',
      message: `恢复“${name}”会覆盖当前本地数据。恢复前会自动创建回退备份。`,
      confirmText: '继续恢复',
      danger: true,
    })
    if (!confirmed) return
    deps.dataPrivacyBusy.value = name
    try {
      const result = await restoreCompleteBackup(name)
      deps.settingsFeedback.value = { section: 'data', type: 'success', message: `恢复完成。已保留回退备份 ${result.rollback_backup}，请重启应用后继续使用` }
      await refreshDataPrivacy({ quiet: true })
    } catch (error) {
      deps.settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
    } finally {
      deps.dataPrivacyBusy.value = ''
    }
  }
  async function deleteDataBackup(name) {
    const confirmed = await deps.showAppConfirm({
      title: '删除本地备份',
      message: `确定删除“${name}”吗？删除后只能依靠其他备份恢复。`,
      confirmText: '删除备份',
      danger: true,
    })
    if (!confirmed) return
    deps.dataPrivacyBusy.value = `delete:${name}`
    try {
      const result = await deleteCompleteBackup(name)
      await refreshDataPrivacy({ quiet: true })
      const reclaimedMb = (Number(result.reclaimed_bytes || 0) / 1048576).toFixed(2)
      deps.settingsFeedback.value = { section: 'data', type: 'success', message: `已删除备份，释放 ${reclaimedMb} MB` }
    } catch (error) {
      deps.settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
    } finally {
      deps.dataPrivacyBusy.value = ''
    }
  }
  async function cleanupDataBackups() {
    const storage = deps.dataPrivacyState.value.backupStorage || {}
    const confirmed = await deps.showAppConfirm({
      title: '清理旧自动备份',
      message: `将按照“最多 ${storage.keep_count || 14} 份、总量不超过 ${Math.round(Number(storage.max_total_bytes || 0) / 1048576) || 1024} MB”清理旧自动备份。手动和导入备份不会删除。`,
      confirmText: '开始清理',
    })
    if (!confirmed) return
    deps.dataPrivacyBusy.value = 'cleanup'
    try {
      const result = await cleanupAutomaticBackups()
      await refreshDataPrivacy({ quiet: true })
      const reclaimedMb = (Number(result.reclaimed_bytes || 0) / 1048576).toFixed(2)
      deps.settingsFeedback.value = { section: 'data', type: 'success', message: `清理完成：删除 ${result.deleted_count || 0} 份，释放 ${reclaimedMb} MB` }
    } catch (error) {
      deps.settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
    } finally {
      deps.dataPrivacyBusy.value = ''
    }
  }
  async function togglePrivacyPause() {
    deps.dataPrivacyBusy.value = 'privacy'
    try {
      const privacy = deps.dataPrivacyState.value.privacy || {}
      const shouldPause = !privacy.paused || ['pause_incomplete', 'state_uncertain'].includes(privacy.transition)
      deps.dataPrivacyState.value = {
        ...deps.dataPrivacyState.value,
        privacy: await setPrivacyPaused(shouldPause),
      }
      deps.settingsFeedback.value = {
        section: 'data',
        type: 'success',
        message: deps.dataPrivacyState.value.privacy.paused ? '敏感能力已全部暂停' : '已恢复暂停前的能力设置',
      }
    } catch (error) {
      deps.settingsFeedback.value = { section: 'data', type: 'error', message: error.message }
      await refreshDataPrivacy({ quiet: true }).catch(() => {})
    } finally {
      deps.dataPrivacyBusy.value = ''
    }
  }
  return { completeOnboarding, refreshOnboardingEnvironment, refreshDataPrivacy, createDataBackup, importDataBackup, restoreDataBackup, deleteDataBackup, cleanupDataBackups, togglePrivacyPause }
}
