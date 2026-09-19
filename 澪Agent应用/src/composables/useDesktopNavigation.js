/** DesktopNavigation operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import { normalizeAgentView } from '../workspaceState.js'

export function useDesktopNavigation(deps) {
  async function navigatePrimaryView(view) {
    if (deps.isAgentWorkspace) view = normalizeAgentView(view)
    if (view === 'settings') {
      if (deps.isAgentWorkspace) {
        deps.activeView.value = 'settings'
        return
      }
      await deps.openSettingsSection('general')
      return
    }
    deps.activeView.value = view
    if (view === 'diaries') {
      const date = deps.selectedDiary.value?.date || deps.diaries.value[0]?.date
      if (date) await deps.openDiary(date)
    }
  }
  async function returnToApp() {
    if (deps.isSettingsSectionDirty(deps.activeSettingsSection.value)) {
      const discard = await deps.showAppConfirm({ title: '放弃未保存的修改？', message: '当前分类的修改尚未保存，返回后会丢失。', confirmText: '放弃并返回', danger: true })
      if (!discard) return
      await deps.resetActiveSettings()
    }
    const returnView = deps.validInitialViews.has(deps.settingsReturnView.value) ? deps.settingsReturnView.value : 'home'
    deps.activeView.value = returnView
  }
  function openCompanionSection(sectionId) {
    deps.activeView.value = 'companion'
    deps.activeCompanionSection.value = sectionId
  }
  async function focusDesktopPetChat() {
    deps.activeView.value = 'chat'
    deps.leftSidebarVisible.value = true
    localStorage.setItem('mio_left_sidebar_visible', 'true')
    await deps.refreshConversations()
    await deps.selectConversation('desktop_pet')
    if (window.location.hash === '#desktop-pet-chat') {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
    }
  }
  function toggleLeftSidebar() {
    deps.leftSidebarVisible.value = !deps.leftSidebarVisible.value
    localStorage.setItem('mio_left_sidebar_visible', String(deps.leftSidebarVisible.value))
    deps.savedAppPreferences.value = { ...deps.savedAppPreferences.value, left_sidebar_visible: deps.leftSidebarVisible.value }
    deps.appPreferencesDraft.value = { ...deps.appPreferencesDraft.value, left_sidebar_visible: deps.leftSidebarVisible.value }
    localStorage.setItem('mio_app_preferences', JSON.stringify(deps.savedAppPreferences.value))
  }
  function toggleRightSidebar() {
    deps.rightSidebarVisible.value = !deps.rightSidebarVisible.value
    if (deps.isAgentWorkspace) {
      localStorage.setItem('mio_agent_right_sidebar_visible', String(deps.rightSidebarVisible.value))
      return
    }
    localStorage.setItem('mio_right_sidebar_visible', String(deps.rightSidebarVisible.value))
    deps.savedAppPreferences.value = { ...deps.savedAppPreferences.value, right_sidebar_visible: deps.rightSidebarVisible.value }
    deps.appPreferencesDraft.value = { ...deps.appPreferencesDraft.value, right_sidebar_visible: deps.rightSidebarVisible.value }
    localStorage.setItem('mio_app_preferences', JSON.stringify(deps.savedAppPreferences.value))
  }
  function toggleLeftSidebarPinned() {
    deps.leftSidebarPinned.value = !deps.leftSidebarPinned.value
    localStorage.setItem('mio_left_sidebar_pinned', String(deps.leftSidebarPinned.value))
  }
  function toggleRightSidebarPinned() {
    deps.rightSidebarPinned.value = !deps.rightSidebarPinned.value
    localStorage.setItem(deps.isAgentWorkspace ? 'mio_agent_right_sidebar_pinned' : 'mio_right_sidebar_pinned', String(deps.rightSidebarPinned.value))
  }
  function applyAgentPreferences(preferences = {}) {
    if (!deps.isAgentWorkspace) return
    if (typeof preferences.rightSidebarVisible === 'boolean') deps.rightSidebarVisible.value = preferences.rightSidebarVisible
    if (typeof preferences.rightSidebarHoverExpand === 'boolean') deps.agentRightSidebarHoverExpand.value = preferences.rightSidebarHoverExpand
  }
  async function controlDesktopWindow(action) {
    const control = window.pywebview?.api?.window_control
    if (!control) return
    try {
      await control(action)
    } catch (error) {
      deps.errorMessage.value = `窗口操作失败：${error.message}`
    }
  }
  async function switchDesktopWorkspace(target) {
    const switcher = window.pywebview?.api?.switch_workspace
    if (switcher) {
      try {
        const result = await switcher(target)
        if (result?.ok === false) throw new Error(result.error || '窗口切换失败')
        return
      } catch (error) {
        deps.errorMessage.value = `窗口切换失败：${error.message}`
        return
      }
    }
    const next = new URL(window.location.href)
    if (target === 'agent') next.searchParams.set('workspace', 'agent')
    else next.searchParams.delete('workspace')
    window.location.href = next.toString()
  }
  function resizeDesktopWindow(direction, event) {
    if (event?.button !== undefined && event.button !== 0) return
    event?.preventDefault?.()
    window.pywebview?.api?.window_resize?.(direction)
  }
  return { navigatePrimaryView, returnToApp, openCompanionSection, focusDesktopPetChat, toggleLeftSidebar, toggleRightSidebar, toggleLeftSidebarPinned, toggleRightSidebarPinned, applyAgentPreferences, controlDesktopWindow, switchDesktopWorkspace, resizeDesktopWindow }
}
