/** Bootstrap operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import * as chatApi from '../services/chatApi.js'
import * as diaryApi from '../services/diaryApi.js'
import { filterConversationsForWorkspace } from '../workspaceState.js'
import * as qqApi from '../services/qqApi.js'

export function useBootstrap(deps) {
  async function refreshContextUsage() {
    const conversationId = deps.selectedConversationId.value
    if (!conversationId) return
    try {
      const usage = await deps.request(`/api/agent/context-usage?conversation_id=${encodeURIComponent(conversationId)}`)
      if (deps.selectedConversationId.value === conversationId) deps.contextUsage.value = usage
    } catch {
      // Context usage is supplementary and must not block chatting.
    }
  }
  async function refreshDayDashboard() {
    try {
      const data = await diaryApi.loadDiaryDashboard()
      deps.bootstrap.value = { ...(deps.bootstrap.value || {}), ...data }
      if (!deps.diarySearch.value.trim()) {
        const merged = new Map(deps.diaries.value.map((item) => [item.date, item]))
        for (const diary of data.diaries || []) merged.set(diary.date, diary)
        deps.diaries.value = [...merged.values()].sort((a, b) => b.date.localeCompare(a.date))
      }
    } catch {
      // Dashboard polling must not interrupt chatting.
    }
  }
  async function openTokenUsagePanel() {
    deps.showTokenUsagePanel.value = true
    deps.tokenUsageLoading.value = true
    try {
      deps.tokenUsageData.value = await deps.request('/api/agent/token-usage?days=30')
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.tokenUsageLoading.value = false
    }
  }
  function formatTokenCount(value) {
    return Number(value || 0).toLocaleString('zh-CN')
  }
  async function analyzeTodayState() {
    if (deps.stateAnalyzeBusy.value) return
    deps.stateAnalyzeBusy.value = true
    deps.errorMessage.value = ''
    try {
      await deps.request('/api/state/analyze-today', { method: 'POST', body: '{}' })
      await refreshDayDashboard()
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.stateAnalyzeBusy.value = false
    }
  }
  async function refreshQqStatus() {
    try {
      const qq = await qqApi.loadQqStatus()
      deps.bootstrap.value = { ...(deps.bootstrap.value || {}), qq }
      if (qq.logged_in) {
        deps.clearQqQrImage()
      } else if (qq.qrcode_available && !deps.qqQrImageUrl.value && !deps.qqQrLoading.value) {
        void deps.loadQqQrCode()
      }
    } catch {
      // A transient NapCat status failure must not interrupt desktop chatting.
    }
  }
  async function loadBootstrap({ quiet = false } = {}) {
    if (!quiet) deps.loading.value = true
    try {
      const data = await chatApi.loadBootstrap()
      deps.bootstrap.value = data
      if (!deps.qqAccountDraft.value && data.qq?.napcat_account) deps.qqAccountDraft.value = String(data.qq.napcat_account)
      if (!deps.qqTestTargetDraft.value && data.settings?.qq_allowed_user_ids) {
        deps.qqTestTargetDraft.value = String(data.settings.qq_allowed_user_ids).split(',')[0].trim()
      }
      if (data.qq?.logged_in) {
        deps.clearQqQrImage()
      } else if (data.qq?.qrcode_available && !deps.qqQrImageUrl.value && !deps.qqQrLoading.value) {
        void deps.loadQqQrCode()
      }
      if (data.qq?.group_chat) {
        const preserveGroupDraft = deps.activeView.value === 'settings'
          && deps.activeSettingsSection.value === 'qq'
          && deps.isSettingsSectionDirty('qq')
        if (!preserveGroupDraft) {
          deps.groupChatSettings.value = { ...deps.groupChatSettings.value, ...data.qq.group_chat }
          deps.groupIdsDraft.value = (data.qq.group_chat.group_ids || []).join(', ')
          deps.savedGroupChatSettings.value = deps.normalizedGroupChatSnapshot()
        }
      }
      deps.conversations.value = filterConversationsForWorkspace(data.conversations, deps.workspaceMode)
      if (deps.isAgentWorkspace && !deps.conversations.value.length) {
        const created = await chatApi.createConversation('Agent 创作对话', 'agent')
        deps.conversations.value = [created]
      }
      const availableModelIds = new Set(['auto', ...(data.models || []).map((item) => item.id)])
      if (!availableModelIds.has(deps.selectedModel.value)) deps.selectedModel.value = 'auto'
      deps.ensureReasoningForActiveModel()
      if (!(deps.activeView.value === 'settings' && deps.activeSettingsSection.value === 'conversation' && deps.isSettingsSectionDirty('conversation'))) {
        deps.chatSettingsDraft.value = {
          model_id: deps.selectedModel.value,
          reasoning_level: deps.reasoningLevel.value,
          voice_language: deps.chatVoiceLanguage.value,
        }
        deps.savedChatSettings.value = deps.serializeSettings(deps.chatSettingsDraft.value)
      }
      const availableConversationIds = new Set(deps.conversations.value.map((item) => item.id))
      if (!availableConversationIds.has(deps.selectedConversationId.value)) {
        deps.selectedConversationId.value = deps.isAgentWorkspace ? deps.conversations.value[0]?.id || '' : data.conversation_id
        localStorage.setItem(deps.conversationStorageKey, deps.selectedConversationId.value)
      }
      if (deps.selectedConversationId.value === data.conversation_id && !deps.isAgentWorkspace) {
        deps.messages.value = data.messages || []
        deps.contextUsage.value = data.context_usage || deps.contextUsage.value
      } else {
        const [selectedMessages, selectedUsage] = await Promise.all([
          deps.request(`/api/agent/messages?limit=120&conversation_id=${encodeURIComponent(deps.selectedConversationId.value)}`),
          deps.request(`/api/agent/context-usage?conversation_id=${encodeURIComponent(deps.selectedConversationId.value)}`),
        ])
        deps.messages.value = selectedMessages
        deps.contextUsage.value = selectedUsage
      }
      deps.diaries.value = data.diaries || []
      if (!deps.selectedDiary.value && deps.diaries.value.length) {
        deps.selectedDiary.value = deps.diaries.value[0]
      }
      deps.errorMessage.value = ''
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.loading.value = false
    }
    await deps.settleChatScrollToBottom()
  }
  async function loadStartupGreetingSetting() {
    try {
      const data = await deps.request('/api/companion/startup-greeting')
      deps.startupGreetingEnabled.value = Boolean(data.enabled)
      deps.savedStartupGreeting.value = deps.startupGreetingEnabled.value
    } catch {
      // 旧后端没有此设置时保留默认开启状态。
    }
  }
  async function loadDesktopPreferences({ quiet = false } = {}) {
    const getter = window.pywebview?.api?.get_desktop_preferences
    if (!getter) return
    deps.desktopPreferencesBusy.value = true
    try {
      const result = await getter()
      if (!result?.ok) throw new Error(result?.error || '桌面设置读取失败')
      deps.desktopPreferencesDraft.value = {
        close_to_background: Boolean(result.close_to_background),
        background_notifications: Boolean(result.background_notifications),
        windows_startup: Boolean(result.windows_startup),
      }
      deps.savedDesktopPreferences.value = deps.serializeSettings(deps.desktopPreferencesDraft.value)
      deps.desktopPreferencesReady.value = true
    } catch (error) {
      if (!quiet) deps.errorMessage.value = `桌面设置读取失败：${error.message}`
    } finally {
      deps.desktopPreferencesBusy.value = false
    }
  }
  async function saveDesktopPreferences() {
    const setter = window.pywebview?.api?.set_desktop_preferences
    if (!setter || !deps.desktopPreferencesReady.value) return true
    deps.desktopPreferencesBusy.value = true
    try {
      const result = await setter({ ...deps.desktopPreferencesDraft.value })
      if (!result?.ok) throw new Error(result?.error || '桌面设置保存失败')
      deps.desktopPreferencesDraft.value = {
        close_to_background: Boolean(result.close_to_background),
        background_notifications: Boolean(result.background_notifications),
        windows_startup: Boolean(result.windows_startup),
      }
      deps.savedDesktopPreferences.value = deps.serializeSettings(deps.desktopPreferencesDraft.value)
      return true
    } catch (error) {
      deps.errorMessage.value = `桌面设置保存失败：${error.message}`
      deps.showSettingsFeedback('general', 'error', '桌面设置保存失败')
      return false
    } finally {
      deps.desktopPreferencesBusy.value = false
    }
  }
  async function loadQqStartupSetting() {
    try {
      const data = await deps.request('/api/companion/qq-startup')
      deps.qqStartupEnabled.value = Boolean(data.enabled)
      deps.savedQqStartupEnabled.value = deps.qqStartupEnabled.value
    } catch {
      // 旧后端没有此设置时保留默认关闭状态。
    }
  }
  async function saveStartupGreetingSetting() {
    try {
      const greeting = await deps.request('/api/companion/startup-greeting', {
        method: 'PATCH',
        body: JSON.stringify({ enabled: deps.startupGreetingEnabled.value }),
      })
      deps.startupGreetingEnabled.value = Boolean(greeting.enabled)
      deps.savedStartupGreeting.value = deps.startupGreetingEnabled.value
      return true
    } catch (error) {
      deps.errorMessage.value = `启动打招呼设置保存失败：${error.message}`
      deps.showSettingsFeedback('general', 'error', '启动打招呼设置保存失败')
      return false
    }
  }
  async function saveQqStartupSetting() {
    try {
      const qqStartup = await deps.request('/api/companion/qq-startup', {
        method: 'PATCH',
        body: JSON.stringify({ enabled: deps.qqStartupEnabled.value }),
      })
      deps.qqStartupEnabled.value = Boolean(qqStartup.enabled)
      deps.savedQqStartupEnabled.value = deps.qqStartupEnabled.value
      return true
    } catch (error) {
      deps.errorMessage.value = `QQ 随 Mio 启动设置保存失败：${error.message}`
      deps.showSettingsFeedback('qq', 'error', 'QQ 随 Mio 启动设置保存失败')
      return false
    }
  }
  return { refreshContextUsage, refreshDayDashboard, openTokenUsagePanel, formatTokenCount, analyzeTodayState, refreshQqStatus, loadBootstrap, loadStartupGreetingSetting, loadDesktopPreferences, saveDesktopPreferences, loadQqStartupSetting, saveStartupGreetingSetting, saveQqStartupSetting }
}
