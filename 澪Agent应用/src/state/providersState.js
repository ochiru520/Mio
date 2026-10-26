import { ref } from 'vue'

// Per-window state: factories never start I/O and never share mutable defaults.
export function createProvidersState() {
  const webSearchTestQuery = ref('帮我查一下 DeepSeek 最新消息')
  const webSearchTestBusy = ref(false)
  const webSearchTestResult = ref(null)
  const modelTestBusy = ref('')
  const modelTestStatus = ref({})
  const showProviderPanel = ref(false)
  const providerBusy = ref(false)
  const providerDiscoveryBusy = ref(false)
  const providerDiscoveryWarning = ref('')
  const providerDiscoveryMeta = ref(null)
  const discoveredModels = ref([])
  const providerForm = ref({
    preset_id: '',
    provider_kind: 'relay',
    provider_protocol: 'openai',
    default_api_mode: 'auto',
    provider_name: '',
    model: '',
    base_url: '',
    api_key: '',
    supports_vision: true,
    cached_input_price_cny_per_million: 0,
    input_price_cny_per_million: 0,
    output_price_cny_per_million: 0,
  })
  return {
    webSearchTestQuery,
    webSearchTestBusy,
    webSearchTestResult,
    modelTestBusy,
    modelTestStatus,
    showProviderPanel,
    providerBusy,
    providerDiscoveryBusy,
    providerDiscoveryWarning,
    providerDiscoveryMeta,
    discoveredModels,
    providerForm,
  }
}
