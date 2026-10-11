/** DesktopNavigation operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import { normalizeAgentView } from '../workspaceState.js'
import { beginWindowMove } from '../desktopWindowDrag.js'

export function useDesktopNavigation(deps) {
  async function navigatePrimaryView(view) {
    if (deps.isAgentWorkspace) view = normalizeAgentView(view)
    if (!deps.isAgentWorkspace && !deps.leftSidebarPinned.value) deps.leftSidebarHovered.value = false
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
    if (deps.savedAppPreferences.value.remember_sidebar_state !== false) localStorage.setItem('mio_left_sidebar_visible', 'true')
    await deps.refreshConversations()
    await deps.selectConversation('desktop_pet')
    if (window.location.hash === '#desktop-pet-chat') {
      window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
    }
  }
  function toggleLeftSidebar() {
    deps.leftSidebarVisible.value = !deps.leftSidebarVisible.value
    if (deps.savedAppPreferences.value.remember_sidebar_state === false) return
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
    if (deps.savedAppPreferences.value.remember_sidebar_state === false) return
    localStorage.setItem('mio_right_sidebar_visible', String(deps.rightSidebarVisible.value))
    deps.savedAppPreferences.value = { ...deps.savedAppPreferences.value, right_sidebar_visible: deps.rightSidebarVisible.value }
    deps.appPreferencesDraft.value = { ...deps.appPreferencesDraft.value, right_sidebar_visible: deps.rightSidebarVisible.value }
    localStorage.setItem('mio_app_preferences', JSON.stringify(deps.savedAppPreferences.value))
  }
  function toggleLeftSidebarPinned() {
    deps.leftSidebarPinned.value = !deps.leftSidebarPinned.value
    if (deps.savedAppPreferences.value.remember_sidebar_state === false) return
    localStorage.setItem('mio_left_sidebar_pinned', String(deps.leftSidebarPinned.value))
  }
  function toggleRightSidebarPinned() {
    deps.rightSidebarPinned.value = !deps.rightSidebarPinned.value
    if (!deps.isAgentWorkspace && deps.savedAppPreferences.value.remember_sidebar_state === false) return
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
  function beginDesktopWindowDrag(event) {
    if (event?.button !== undefined && event.button !== 0) return
    if (window.pywebview?.api?.window_move_by) {
      beginWindowMove(event, message => { deps.errorMessage.value = message })
      return
    }
    if (window.pywebview?._jsApiCallback) return
    const drag = window.pywebview?.api?.window_drag
    if (!drag) return
    event?.preventDefault?.()
    void drag()
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
    next.searchParams.delete('view')
    next.searchParams.delete('settings-section')
    if (target === 'agent') next.searchParams.set('workspace', 'agent')
    else next.searchParams.delete('workspace')
    window.location.href = next.toString()
  }
  function resizeDesktopWindow(direction, event) {
    if (event?.button !== undefined && event.button !== 0) return
    event?.preventDefault?.()
    const resize = window.pywebview?.api?.window_resize_by
    if (!resize || !event?.target?.setPointerCapture) {
      window.pywebview?.api?.window_resize?.(direction)
      return
    }
    const target = event.target, pointer = event.pointerId
    const startX = event.screenX, startY = event.screenY, id = crypto.randomUUID()
    let x = 0, y = 0, finished = false, sending = false, dirty = false
    target.setPointerCapture(pointer)
    async function send() {
      dirty = true
      if (sending) return
      sending = true
      try {
        while (dirty) {
          dirty = false
          const result = await resize(direction, x, y, id, finished)
          if (result?.ok === false) throw new Error(result.error || '调整窗口大小失败')
        }
      } catch (error) { deps.errorMessage.value = `窗口操作失败：${error.message}` }
      finally { sending = false }
    }
    function move(next) {
      if (next.pointerId !== pointer) return
      x = next.screenX - startX; y = next.screenY - startY
      void send()
    }
    function end(next) {
      if (next.pointerId !== pointer || finished) return
      if (next.type === 'pointerup') { x = next.screenX - startX; y = next.screenY - startY }
      finished = true
      target.removeEventListener('pointermove', move)
      target.removeEventListener('pointerup', end)
      target.removeEventListener('pointercancel', end)
      target.removeEventListener('lostpointercapture', end)
      if (target.hasPointerCapture(pointer)) target.releasePointerCapture(pointer)
      void send()
    }
    target.addEventListener('pointermove', move)
    target.addEventListener('pointerup', end)
    target.addEventListener('pointercancel', end)
    target.addEventListener('lostpointercapture', end)
  }
  return { navigatePrimaryView, returnToApp, openCompanionSection, focusDesktopPetChat, toggleLeftSidebar, toggleRightSidebar, toggleLeftSidebarPinned, toggleRightSidebarPinned, applyAgentPreferences, controlDesktopWindow, beginDesktopWindowDrag, switchDesktopWorkspace, resizeDesktopWindow }
}
