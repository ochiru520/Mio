import { ref } from 'vue'
import { DEFAULT_CONTEXT_MAX_TOKENS } from '../contextUsageState.js'

// Per-window state: factories never start I/O and never share mutable defaults.
export function createConversationState() {
  const latestTaskHandoff = ref(null)
  const messages = ref([])
  const conversations = ref([])
  const draft = ref('')
  const sending = ref(false)
  const loading = ref(true)
  const errorMessage = ref('')
  const chatScroll = ref(null)
  const fileInput = ref(null)
  const modelPicker = ref(null)
  const tokenUsageDialog = ref(null)
  const providerDialog = ref(null)
  const chatVoiceLanguage = ref('auto')
  const showModelMenu = ref(false)
  const modelMenuSection = ref('root')
  const contextUsage = ref({ used_tokens: 0, max_tokens: DEFAULT_CONTEXT_MAX_TOKENS, percent: 0, has_summary: false })
  const attachments = ref([])
  const isFileDragging = ref(false)
  const copiedTurnId = ref('')
  const speakingPartId = ref('')
  const voiceLoadingPartId = ref('')
  const showTokenUsagePanel = ref(false)
  const tokenUsageLoading = ref(false)
  const tokenUsageError = ref('')
  const tokenUsageData = ref({ today: {}, total: {}, days: [], logical_day_boundary_hour: 4 })
  const stateAnalyzeBusy = ref(false)
  return {
    latestTaskHandoff,
    messages,
    conversations,
    draft,
    sending,
    loading,
    errorMessage,
    chatScroll,
    fileInput,
    modelPicker,
    tokenUsageDialog,
    providerDialog,
    chatVoiceLanguage,
    showModelMenu,
    modelMenuSection,
    contextUsage,
    attachments,
    isFileDragging,
    copiedTurnId,
    speakingPartId,
    voiceLoadingPartId,
    showTokenUsagePanel,
    tokenUsageLoading,
    tokenUsageError,
    tokenUsageData,
    stateAnalyzeBusy,
  }
}
