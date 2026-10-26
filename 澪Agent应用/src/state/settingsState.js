import { ref } from 'vue'
import { DEFAULT_CONTEXT_MAX_TOKENS } from '../contextUsageState.js'

// Per-window state: factories never start I/O and never share mutable defaults.
export function createSettingsState({ initialAppPreferences, cloneAppPreferences, isAgentWorkspace, settingsSectionFromUrl, storage = localStorage }) {
  const activeSettingsSection = ref(settingsSectionFromUrl || storage.getItem(isAgentWorkspace ? 'mio_agent_settings_section' : 'mio_settings_section') || (isAgentWorkspace ? 'budget' : 'general'))
  const settingsSearch = ref('')
  const settingsFeedback = ref({ section: '', type: '', message: '' })
  const savedAppPreferences = ref(cloneAppPreferences(initialAppPreferences))
  const appPreferencesDraft = ref(cloneAppPreferences(savedAppPreferences.value))
  const chatSettingsDraft = ref({ model_id: '', reasoning_level: '', voice_language: 'auto' })
  const savedChatSettings = ref('')
  const savedStartupGreeting = ref(true)
  const desktopPreferencesDraft = ref({ close_to_background: true, background_notifications: true, windows_startup: false })
  const savedDesktopPreferences = ref('')
  const desktopPreferencesReady = ref(false)
  const desktopPreferencesBusy = ref(false)
  const qqStartupEnabled = ref(false)
  const savedQqStartupEnabled = ref(false)
  const savedGroupChatSettings = ref('')
  const savedCompanionSettings = ref({ pet: '', voice: '', observation: '' })
  const runtimeSettingsDraft = ref({ chat_context_max_tokens: DEFAULT_CONTEXT_MAX_TOKENS })
  const savedRuntimeSettings = ref('')
  const runtimeSettingsRevision = ref('')
  const runtimeSettingsReady = ref(false)
  const runtimeSettingsBusy = ref(false)
  const mioProfileDraft = ref(null)
  const mioProfileNotesDraft = ref('')
  const mioProfileAvoidDraft = ref('')
  const savedMioProfile = ref('')
  const mioProfileReady = ref(false)
  const mioProfileBusy = ref(false)
  const profileAvatarNonce = ref(Date.now())
  const profileAvatarCustom = ref(false)
  const userAvatarNonce = ref(Date.now())
  const userAvatarCustom = ref(false)
  const chatBackgroundNonce = ref(Date.now())
  const chatBackgroundCustom = ref(false)
  const leftSidebarVisible = ref(!savedAppPreferences.value.focus_mode && savedAppPreferences.value.left_sidebar_visible !== false)
  const rightSidebarVisible = ref(isAgentWorkspace
    ? storage.getItem('mio_agent_right_sidebar_visible') !== 'false'
    : !savedAppPreferences.value.focus_mode && savedAppPreferences.value.right_sidebar_visible !== false)
  const leftSidebarPinned = ref(savedAppPreferences.value.remember_sidebar_state !== false && storage.getItem('mio_left_sidebar_pinned') === 'true')
  const rightSidebarPinned = ref((isAgentWorkspace || savedAppPreferences.value.remember_sidebar_state !== false) && storage.getItem(isAgentWorkspace ? 'mio_agent_right_sidebar_pinned' : 'mio_right_sidebar_pinned') === 'true')
  const agentRightSidebarHoverExpand = ref(storage.getItem('mio_agent_right_sidebar_hover_expand') !== 'false')
  const leftSidebarHovered = ref(false)
  const rightSidebarHovered = ref(false)
  return {
    activeSettingsSection,
    settingsSearch,
    settingsFeedback,
    savedAppPreferences,
    appPreferencesDraft,
    chatSettingsDraft,
    savedChatSettings,
    savedStartupGreeting,
    desktopPreferencesDraft,
    savedDesktopPreferences,
    desktopPreferencesReady,
    desktopPreferencesBusy,
    qqStartupEnabled,
    savedQqStartupEnabled,
    savedGroupChatSettings,
    savedCompanionSettings,
    runtimeSettingsDraft,
    savedRuntimeSettings,
    runtimeSettingsRevision,
    runtimeSettingsReady,
    runtimeSettingsBusy,
    mioProfileDraft,
    mioProfileNotesDraft,
    mioProfileAvoidDraft,
    savedMioProfile,
    mioProfileReady,
    mioProfileBusy,
    profileAvatarNonce,
    profileAvatarCustom,
    userAvatarNonce,
    userAvatarCustom,
    chatBackgroundNonce,
    chatBackgroundCustom,
    leftSidebarVisible,
    rightSidebarVisible,
    leftSidebarPinned,
    rightSidebarPinned,
    agentRightSidebarHoverExpand,
    leftSidebarHovered,
    rightSidebarHovered,
  }
}
