import { ref } from 'vue'

// Per-window state: factories never start I/O and never share mutable defaults.
export function createPrivacyState() {
  const bootstrap = ref(null)
  const onboardingBusy = ref(false)
  const onboardingError = ref('')
  const onboardingEnvironment = ref(null)
  const onboardingEnvironmentBusy = ref(false)
  const dataPrivacyState = ref({ backups: [], backupStorage: {}, privacy: { paused: false, capabilities: [] }, migrations: {} })
  const dataPrivacyLoading = ref(false)
  const dataPrivacyBusy = ref('')
  return {
    bootstrap,
    onboardingBusy,
    onboardingError,
    onboardingEnvironment,
    onboardingEnvironmentBusy,
    dataPrivacyState,
    dataPrivacyLoading,
    dataPrivacyBusy,
  }
}
