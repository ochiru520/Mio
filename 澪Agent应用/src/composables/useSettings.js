/** Settings operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import { nextTick } from 'vue'
import * as settingsApi from '../services/settingsApi.js'
import { saveRuntimeSettingsWithRecovery } from '../services/runtimeSettingsSave.js'

export function useSettings(deps) {
  function focusComposer() {
    deps.activeView.value = 'chat'
    nextTick(() => document.querySelector('.composer-input')?.focus())
  }
  function openMemoryTab(tab) {
    deps.activeView.value = 'memory'
    deps.memoryTab.value = tab
  }
  function handleSettingsModelChange() {
    const options = deps.settingsReasoningOptions.value
    const optionIds = new Set(options.map((item) => item.id))
    if (!optionIds.has(deps.chatSettingsDraft.value.reasoning_level)) {
      const profile = deps.modelOptions.value.find((item) => item.id === deps.chatSettingsDraft.value.model_id)
      deps.chatSettingsDraft.value.reasoning_level = deps.chatSettingsDraft.value.model_id === 'auto'
        ? 'auto'
        : (profile?.default_reasoning_level || options[0]?.id || 'default')
    }
  }
  function handlePetModelChange() {
    const settings = deps.companionStatus.value.pet?.settings
    if (!settings) return
    const options = deps.petReasoningOptions.value
    const optionIds = new Set(options.map((item) => item.id))
    if (!optionIds.has(settings.pet_chat_reasoning_level)) {
      const profile = deps.modelOptions.value.find((item) => item.id === settings.pet_chat_model_id)
      settings.pet_chat_reasoning_level = settings.pet_chat_model_id === 'auto'
        ? 'auto'
        : (profile?.default_reasoning_level || options[0]?.id || 'default')
    }
  }
  function applyDisplayMode(mode) {
    const preset = deps.DISPLAY_MODE_PRESETS[mode]
    if (!preset) return
    deps.appPreferencesDraft.value = deps.cloneAppPreferences({
      ...deps.appPreferencesDraft.value,
      display_mode: mode,
      visibility: { ...deps.appPreferencesDraft.value.visibility, ...preset },
    })
  }
  function saveAppearanceSettings(section = 'appearance') {
    const current = deps.cloneAppPreferences(deps.savedAppPreferences.value)
    const draft = deps.cloneAppPreferences(deps.appPreferencesDraft.value)
    const saved = section === 'general'
      ? deps.cloneAppPreferences({ ...current, default_open_page: draft.default_open_page })
      : deps.cloneAppPreferences({
          ...current,
          theme: draft.theme,
          font_size: draft.font_size,
          left_sidebar_visible: draft.left_sidebar_visible,
          right_sidebar_visible: draft.right_sidebar_visible,
          left_sidebar_hover_expand: draft.left_sidebar_hover_expand,
          right_sidebar_hover_expand: draft.right_sidebar_hover_expand,
          remember_sidebar_state: draft.remember_sidebar_state,
          light_animations: draft.light_animations,
          focus_mode: draft.focus_mode,
          visibility: draft.visibility,
        })
    deps.savedAppPreferences.value = saved
    deps.appPreferencesDraft.value = deps.cloneAppPreferences(saved)
    localStorage.setItem('mio_app_preferences', JSON.stringify(saved))
    deps.leftSidebarVisible.value = saved.focus_mode ? false : saved.left_sidebar_visible !== false
    deps.rightSidebarVisible.value = saved.focus_mode ? false : saved.right_sidebar_visible !== false
    localStorage.setItem('mio_left_sidebar_visible', String(deps.leftSidebarVisible.value))
    localStorage.setItem('mio_right_sidebar_visible', String(deps.rightSidebarVisible.value))
    deps.showSettingsFeedback(section, 'success', section === 'general' ? '基础设置已保存' : '外观与界面设置已保存并生效')
  }
  function runtimeDraftIsDirty() {
    const baseline = deps.savedRuntimeSettingsSource()
    return Object.keys(deps.runtimeSettingsDraft.value).some(key =>
      JSON.stringify(deps.runtimeSettingsDraft.value[key]) !== JSON.stringify(baseline[key]))
  }
  async function loadRuntimeSettings({ quiet = false, discardDraft = false } = {}) {
    if (deps.runtimeSettingsBusy.value) return false
    if (!discardDraft && deps.runtimeSettingsReady.value && runtimeDraftIsDirty()) return false
    deps.runtimeSettingsBusy.value = true
    try {
      const result = await settingsApi.loadRuntimeSettings()
      if (!discardDraft && deps.runtimeSettingsReady.value && runtimeDraftIsDirty()) return false
      deps.runtimeSettingsDraft.value = { ...(result.settings || {}) }
      deps.runtimeSettingsRevision.value = result.revision || ''
      deps.savedRuntimeSettings.value = deps.serializeSettings(deps.runtimeSettingsDraft.value)
      deps.runtimeSettingsReady.value = true
      return true
    } catch (error) {
      if (!quiet) deps.errorMessage.value = error.message
      return false
    } finally {
      deps.runtimeSettingsBusy.value = false
    }
  }
  async function reloadRuntimeSettings() {
    if (deps.runtimeSettingsBusy.value) return false
    if (runtimeDraftIsDirty() && !await deps.showAppConfirm({
      title: '重新读取运行设置？', message: '运行设置中尚未保存的修改将被最新设置替换，其他设置草稿会保留。',
      confirmText: '重新读取', danger: true,
    })) return false
    const section = deps.activeSettingsSection.value
    const loaded = await loadRuntimeSettings({ discardDraft: true })
    if (loaded) deps.errorMessage.value = ''
    deps.showSettingsFeedback(section, loaded ? 'success' : 'error', loaded ? '已读取最新运行设置' : '重新读取失败，请稍后重试')
    return loaded
  }
  async function saveRuntimeSettings() {
    if (deps.runtimeSettingsBusy.value || !deps.runtimeSettingsReady.value) return false
    deps.runtimeSettingsBusy.value = true
    deps.errorMessage.value = ''
    const section = deps.activeSettingsSection.value
    const label = deps.activeSettingsItem.value.label
    try {
      const baseline = deps.savedRuntimeSettingsSource()
      const payload = Object.fromEntries((deps.runtimeSettingKeysBySection[section] || [])
        .filter(key => JSON.stringify(deps.runtimeSettingsDraft.value[key]) !== JSON.stringify(baseline[key]))
        .map(key => [key, deps.runtimeSettingsDraft.value[key]]))
      for (const key of deps.privateRuntimePathKeys) delete payload[key]
      if (!Object.keys(payload).length) return true
      const changes = JSON.parse(JSON.stringify(payload))
      const result = await saveRuntimeSettingsWithRecovery(changes, {
        baseline, revision: deps.runtimeSettingsRevision.value,
      })
      const remainingDraft = Object.fromEntries(Object.entries(deps.runtimeSettingsDraft.value)
        .filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(
          Object.hasOwn(changes, key) ? changes[key] : baseline[key])))
      deps.runtimeSettingsDraft.value = { ...(result.settings || {}), ...remainingDraft }
      deps.runtimeSettingsRevision.value = result.revision || ''
      deps.savedRuntimeSettings.value = deps.serializeSettings(result.settings || {})
      await deps.loadBootstrap({ quiet: true })
      const pendingRestart = result.application?.restart_required?.length
      deps.showSettingsFeedback(section, 'success', pendingRestart
        ? `${label}设置已保存，组件路径需重启应用后生效`
        : `${label}设置已保存并生效`)
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      deps.showSettingsFeedback(section, 'error', `${label}设置保存失败：${error.message}`)
      return false
    } finally {
      deps.runtimeSettingsBusy.value = false
    }
  }
  async function loadMioProfileSettings({ quiet = false, discardDraft = false } = {}) {
    if (deps.mioProfileBusy.value) return
    if (!discardDraft && deps.isSettingsSectionDirty('profile')) return
    deps.mioProfileBusy.value = true
    try {
      const result = await settingsApi.loadProfileSettings()
      if (!result.profile) throw new Error('人格设定为空，请重试')
      if (!discardDraft && deps.isSettingsSectionDirty('profile')) return
      deps.mioProfileDraft.value = result.profile
      deps.mioProfileNotesDraft.value = (result.profile?.preferences?.custom_notes || []).join('\n')
      deps.mioProfileAvoidDraft.value = (result.profile?.speaking_style?.avoid || []).join('\n')
      deps.savedMioProfile.value = deps.serializeSettings({ profile: deps.mioProfileDraft.value, notes: deps.mioProfileNotesDraft.value, avoid: deps.mioProfileAvoidDraft.value })
      deps.mioProfileReady.value = Boolean(deps.mioProfileDraft.value)
      deps.profileAvatarCustom.value = Boolean(result.avatar?.custom)
      deps.userAvatarCustom.value = Boolean(result.user_avatar?.custom)
      deps.chatBackgroundCustom.value = Boolean(result.chat_background?.custom)
      deps.profileAvatarNonce.value = Date.now()
      deps.userAvatarNonce.value = Date.now()
      deps.chatBackgroundNonce.value = Date.now()
      if (deps.settingsFeedback.value.section === 'profile' && deps.settingsFeedback.value.message.startsWith('人格设定读取失败')) {
        deps.settingsFeedback.value = { section: '', type: '', message: '' }
      }
    } catch (error) {
      if (!quiet) deps.errorMessage.value = error.message
      if (deps.activeView.value === 'settings' && deps.activeSettingsSection.value === 'profile') {
        deps.showSettingsFeedback('profile', 'error', `人格设定读取失败：${error.message}`)
      }
    } finally {
      deps.mioProfileBusy.value = false
    }
  }
  async function saveMioProfileSettings() {
    if (deps.mioProfileBusy.value || !deps.mioProfileReady.value || !deps.mioProfileDraft.value) return false
    deps.mioProfileBusy.value = true
    deps.errorMessage.value = ''
    try {
      const profile = JSON.parse(JSON.stringify(deps.mioProfileDraft.value))
      profile.identity ||= {}
      profile.identity.name = String(profile.identity.name || '').trim()
      if (!profile.identity.name) throw new Error('显示名字不能为空')
      profile.preferences ||= {}
      profile.preferences.custom_notes = deps.mioProfileNotesDraft.value
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean)
      profile.speaking_style ||= {}
      profile.speaking_style.avoid = deps.mioProfileAvoidDraft.value
        .split(/\r?\n/)
        .map((item) => item.trim())
        .filter(Boolean)
      const result = await deps.request('/api/settings/profile', {
        method: 'PATCH',
        body: JSON.stringify({ profile }),
      })
      deps.mioProfileDraft.value = result.profile
      deps.mioProfileNotesDraft.value = (result.profile?.preferences?.custom_notes || []).join('\n')
      deps.mioProfileAvoidDraft.value = (result.profile?.speaking_style?.avoid || []).join('\n')
      deps.savedMioProfile.value = deps.serializeSettings({ profile: deps.mioProfileDraft.value, notes: deps.mioProfileNotesDraft.value, avoid: deps.mioProfileAvoidDraft.value })
      deps.memoryData.value.profile = result.profile
      localStorage.setItem('mio_profile_updated', String(Date.now()))
      deps.showSettingsFeedback('profile', 'success', '人格与属性已保存，下一轮对话开始生效')
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      deps.showSettingsFeedback('profile', 'error', '人格与属性保存失败')
      return false
    } finally {
      deps.mioProfileBusy.value = false
    }
  }
  async function uploadProfileAvatar(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!deps.isImageAttachment(file)) {
      deps.showSettingsFeedback('profile', 'error', '请选择图片文件')
      return
    }
    if (file.size > 12 * 1024 * 1024) {
      deps.showSettingsFeedback('profile', 'error', '头像图片不能超过 12MB')
      return
    }
    deps.mioProfileBusy.value = true
    deps.errorMessage.value = ''
    try {
      const dataUrl = await deps.readFileAsDataUrl(file)
      await deps.request('/api/settings/avatar', {
        method: 'POST',
        body: JSON.stringify({ data_url: dataUrl }),
      })
      deps.profileAvatarCustom.value = true
      deps.profileAvatarNonce.value = Date.now()
      deps.showSettingsFeedback('profile', 'success', '头像已更新')
    } catch (error) {
      deps.errorMessage.value = error.message
      deps.showSettingsFeedback('profile', 'error', `头像更新失败：${error.message}`)
    } finally {
      deps.mioProfileBusy.value = false
    }
  }
  async function resetProfileAvatar() {
    if (deps.mioProfileBusy.value || !deps.profileAvatarCustom.value) return
    deps.mioProfileBusy.value = true
    deps.errorMessage.value = ''
    try {
      await deps.request('/api/settings/avatar', { method: 'DELETE' })
      deps.profileAvatarCustom.value = false
      deps.profileAvatarNonce.value = Date.now()
      deps.showSettingsFeedback('profile', 'success', '已恢复默认头像')
    } catch (error) {
      deps.errorMessage.value = error.message
      deps.showSettingsFeedback('profile', 'error', `恢复默认头像失败：${error.message}`)
    } finally {
      deps.mioProfileBusy.value = false
    }
  }
  async function uploadAppearanceImage(event, kind) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    if (!deps.isImageAttachment(file)) {
      deps.showSettingsFeedback('appearance', 'error', '请选择图片文件')
      return
    }
    if (file.size > 12 * 1024 * 1024) {
      deps.showSettingsFeedback('appearance', 'error', '图片不能超过 12MB')
      return
    }
    const isBackground = kind === 'background'
    const endpoint = isBackground ? '/api/settings/chat-background' : '/api/settings/user-avatar'
    deps.mioProfileBusy.value = true
    deps.errorMessage.value = ''
    try {
      const dataUrl = await deps.readFileAsDataUrl(file)
      await deps.request(endpoint, { method: 'POST', body: JSON.stringify({ data_url: dataUrl }) })
      if (isBackground) {
        deps.chatBackgroundCustom.value = true
        deps.chatBackgroundNonce.value = Date.now()
      } else {
        deps.userAvatarCustom.value = true
        deps.userAvatarNonce.value = Date.now()
      }
      deps.showSettingsFeedback('appearance', 'success', isBackground ? '对话背景已更新' : '用户头像已更新')
    } catch (error) {
      deps.errorMessage.value = error.message
      deps.showSettingsFeedback('appearance', 'error', `${isBackground ? '对话背景' : '用户头像'}更新失败：${error.message}`)
    } finally {
      deps.mioProfileBusy.value = false
    }
  }
  async function resetAppearanceImage(kind) {
    const isBackground = kind === 'background'
    const isCustom = isBackground ? deps.chatBackgroundCustom.value : deps.userAvatarCustom.value
    if (deps.mioProfileBusy.value || !isCustom) return
    const endpoint = isBackground ? '/api/settings/chat-background' : '/api/settings/user-avatar'
    deps.mioProfileBusy.value = true
    deps.errorMessage.value = ''
    try {
      await deps.request(endpoint, { method: 'DELETE' })
      if (isBackground) {
        deps.chatBackgroundCustom.value = false
        deps.chatBackgroundNonce.value = Date.now()
      } else {
        deps.userAvatarCustom.value = false
        deps.userAvatarNonce.value = Date.now()
      }
      deps.showSettingsFeedback('appearance', 'success', isBackground ? '已恢复默认对话背景' : '已恢复默认用户头像')
    } catch (error) {
      deps.errorMessage.value = error.message
      deps.showSettingsFeedback('appearance', 'error', `恢复默认失败：${error.message}`)
    } finally {
      deps.mioProfileBusy.value = false
    }
  }
  async function saveConversationSettings() {
    deps.selectedModel.value = deps.chatSettingsDraft.value.model_id || 'auto'
    deps.reasoningLevel.value = deps.chatSettingsDraft.value.reasoning_level || 'auto'
    deps.ensureReasoningForActiveModel()
    deps.persistSelectedModel()
    deps.persistReasoning()
    const saved = await deps.syncSharedChatSettings(deps.chatSettingsDraft.value.voice_language)
    if (!saved) {
      deps.showSettingsFeedback('conversation', 'error', '对话与语音设置保存失败')
      return false
    }
    deps.chatSettingsDraft.value = {
      model_id: deps.selectedModel.value,
      reasoning_level: deps.reasoningLevel.value,
      voice_language: saved?.voice_language || deps.chatSettingsDraft.value.voice_language || 'auto',
    }
    deps.savedChatSettings.value = deps.serializeSettings(deps.chatSettingsDraft.value)
    deps.showSettingsFeedback('conversation', 'success', '对话与模型设置已保存')
    return true
  }
  async function saveActiveSettings() {
    const section = deps.activeSettingsSection.value
    if (section === 'general') {
      const desktopSaved = await deps.saveDesktopPreferences()
      if (!desktopSaved) return false
      const greetingSaved = await deps.saveStartupGreetingSetting()
      if (!greetingSaved) {
        deps.showSettingsFeedback('general', 'error', '桌面设置已保存，但启动打招呼保存失败')
        return false
      }
      saveAppearanceSettings('general')
      const runtimeSaved = await saveRuntimeSettings()
      if (!runtimeSaved) {
        deps.showSettingsFeedback('general', 'error', '桌面和启动设置已保存，但主动联系设置保存失败')
        return false
      }
      deps.showSettingsFeedback('general', 'success', '基础与启动设置已全部保存')
      return true
    }
    if (section === 'profile') return saveMioProfileSettings()
    if (section === 'appearance') return saveAppearanceSettings()
    if (section === 'conversation') {
      const conversationSaved = await saveConversationSettings()
      if (!conversationSaved) return false
      return saveRuntimeSettings()
    }
    if (section === 'diary') return saveRuntimeSettings()
    if (section === 'qq') {
      const startupSaved = await deps.saveQqStartupSetting()
      if (!startupSaved) return false
      const groupSaved = await deps.saveGroupChatSettings()
      if (!groupSaved) {
        deps.showSettingsFeedback('qq', 'error', 'QQ 启动设置已保存，但群聊设置保存失败')
        return false
      }
      const runtimeSaved = await saveRuntimeSettings()
      if (!runtimeSaved) {
        deps.showSettingsFeedback('qq', 'error', 'QQ 启动和群聊设置已保存，但通道参数保存失败')
        return false
      }
      deps.showSettingsFeedback('qq', 'success', 'QQ 设置已全部保存')
      return true
    }
    if (section === 'advanced') return saveRuntimeSettings()
    if (section === 'pet') return deps.saveCompanionSettings(section)
    return false
  }
  async function resetActiveSettings() {
    const section = deps.activeSettingsSection.value
    deps.settingsFeedback.value = { section: '', type: '', message: '' }
    if (section === 'general' || section === 'appearance') {
      deps.appPreferencesDraft.value = deps.cloneAppPreferences(deps.savedAppPreferences.value)
    }
    if (section === 'general') {
      deps.startupGreetingEnabled.value = deps.savedStartupGreeting.value
      if (deps.desktopPreferencesReady.value) {
        try { deps.desktopPreferencesDraft.value = JSON.parse(deps.savedDesktopPreferences.value) } catch (_) {}
      }
    }
    if (section === 'profile') {
      // Cancel uses the last saved snapshot, including while a background
      // profile read is pending. The busy guard must not silently eat cancel.
      const saved = JSON.parse(deps.savedMioProfile.value)
      deps.mioProfileDraft.value = saved.profile
      deps.mioProfileNotesDraft.value = saved.notes
      deps.mioProfileAvoidDraft.value = saved.avoid
      return
    }
    if (section === 'conversation') {
      try { deps.chatSettingsDraft.value = JSON.parse(deps.savedChatSettings.value || '{}') } catch (_) {}
    }
    if (['general', 'conversation', 'diary', 'qq', 'advanced'].includes(section)) {
      deps.runtimeSettingsDraft.value = deps.savedRuntimeSettingsSource()
    }
    if (section === 'qq') {
      deps.qqStartupEnabled.value = deps.savedQqStartupEnabled.value
      try {
        const saved = JSON.parse(deps.savedGroupChatSettings.value || '{}')
        deps.groupChatSettings.value.enabled = Boolean(saved.enabled)
        deps.groupChatSettings.value.mention_required = Boolean(saved.mention_required)
        deps.groupIdsDraft.value = (saved.group_ids || []).join(', ')
      } catch (_) {}
    }
    if (section === 'pet') await deps.loadCompanionStatus({ quiet: true, preserveSettings: false })
  }
  async function openSettingsSection(sectionId) {
    const enteringSettings = deps.activeView.value !== 'settings'
    if (!enteringSettings && sectionId === deps.activeSettingsSection.value) {
      if (sectionId === 'profile' && !deps.mioProfileReady.value) await loadMioProfileSettings({ quiet: true })
      if (['general', 'conversation', 'diary', 'qq', 'advanced'].includes(sectionId)) await loadRuntimeSettings()
      return
    }
    if (!enteringSettings && deps.isSettingsSectionDirty(deps.activeSettingsSection.value)) {
      const discard = await deps.showAppConfirm({ title: '放弃未保存的修改？', message: '当前分类的修改尚未保存，切换后会丢失。', confirmText: '放弃并切换', danger: true })
      if (!discard) return
      await resetActiveSettings()
    }
    if (enteringSettings) {
      deps.settingsReturnView.value = deps.activeView.value
      deps.activeView.value = 'settings'
    }
    deps.activeSettingsSection.value = sectionId
    localStorage.setItem(new URLSearchParams(window.location.search).get('workspace') === 'agent' ? 'mio_agent_settings_section' : 'mio_settings_section', sectionId)
    if (sectionId === 'profile') await loadMioProfileSettings({ quiet: true })
    if (sectionId === 'data') deps.refreshDataPrivacy()
    if (['general', 'conversation', 'diary', 'qq', 'advanced'].includes(sectionId)) await loadRuntimeSettings()
  }
  return { focusComposer, openMemoryTab, handleSettingsModelChange, handlePetModelChange, applyDisplayMode, saveAppearanceSettings, loadRuntimeSettings, reloadRuntimeSettings, saveRuntimeSettings, loadMioProfileSettings, saveMioProfileSettings, uploadProfileAvatar, resetProfileAvatar, uploadAppearanceImage, resetAppearanceImage, saveConversationSettings, saveActiveSettings, resetActiveSettings, openSettingsSection }
}
