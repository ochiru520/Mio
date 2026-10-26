import { ref } from 'vue'

// Per-window state: factories never start I/O and never share mutable defaults.
export function createQqState() {
  const qqBusy = ref('')
  const qqQrImageUrl = ref('')
  const qqQrLoading = ref(false)
  const qqQrError = ref('')
  const qqAccountDraft = ref('')
  const qqTestTargetDraft = ref('')
  const qqSetupResult = ref(null)
  const startupGreetingEnabled = ref(true)
  const groupChatSettings = ref({
    enabled: false,
    group_ids: [],
    mention_required: true,
    context_message_count: 0,
    active_group_count: 0,
  })
  const groupIdsDraft = ref('')
  return {
    qqBusy,
    qqQrImageUrl,
    qqQrLoading,
    qqQrError,
    qqAccountDraft,
    qqTestTargetDraft,
    qqSetupResult,
    startupGreetingEnabled,
    groupChatSettings,
    groupIdsDraft,
  }
}
