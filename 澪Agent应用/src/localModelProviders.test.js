import test from 'node:test'
import assert from 'node:assert/strict'
import { useModelProviders } from './composables/useModelProviders.js'

const ref = value => ({ value })
function setup() {
  const requests = []
  const deps = Object.fromEntries(['providerForm', 'discoveredModels', 'providerDiscoveryWarning', 'providerDiscoveryMeta',
    'providerBusy', 'providerDiscoveryBusy', 'errorMessage', 'selectedModel', 'showProviderPanel'].map(key => [key, ref(null)]))
  deps.providerPresets = ref([{ id: 'lm_studio', name: 'LM Studio', kind: 'local', protocol: 'openai',
    default_api_mode: 'chat_completions', base_url: 'http://127.0.0.1:1234/v1' }])
  deps.request = async (url, options) => {
    const body = JSON.parse(options.body)
    requests.push({ url, body })
    if (url.endsWith('/discover')) return { models: [{ model: 'qwen/qwen3.5-9b', supports_tool_calls: true },
      { model: 'second-model' }], resolved_api_base_url: 'http://127.0.0.1:1234/v1' }
    return { models: [{ id: 'local-model-id' }] }
  }
  deps.loadBootstrap = async () => {}
  deps.persistSelectedModel = () => {}
  const api = useModelProviders(deps)
  api.resetProviderForm()
  return { api, deps, requests }
}

test('local kind selects LM Studio without demanding a fake API key', async () => {
  const { api, deps, requests } = setup()
  api.selectProviderKind('local')
  assert.equal(deps.providerForm.value.base_url, 'http://127.0.0.1:1234/v1')
  assert.equal(deps.providerForm.value.default_api_mode, 'chat_completions')
  assert.equal(deps.providerForm.value.api_key, '')
  await api.discoverProviderModels()
  assert.equal(requests[0].body.provider_kind, 'local')
  assert.equal(requests[0].body.api_key, '')
  assert.deepEqual(deps.discoveredModels.value.map(item => item.selected), [true, false])
})

test('local save preserves explicit tool opt-out and local privacy metadata', async () => {
  const previousWindow = globalThis.window
  globalThis.window = { dispatchEvent() {} }
  try {
    const { api, deps, requests } = setup()
    api.selectProviderPreset('lm_studio')
    await api.discoverProviderModels()
    deps.providerForm.value.supports_tool_calls = false
    await api.saveProvider()
    const saved = requests.find(item => item.url.endsWith('/providers')).body
    assert.equal(saved.api_key, '')
    assert.equal(saved.provider_kind, 'local')
    assert.equal(saved.models[0].supports_tool_calls, false)
    assert.equal(saved.models[0].privacy_location, 'local_device')
    assert.equal(deps.selectedModel.value, 'local-model-id')
    assert.equal(deps.errorMessage.value, '')
  } finally {
    globalThis.window = previousWindow
  }
})

test('local discovery failure offers server guidance while retaining manual model entry', async () => {
  const { api, deps } = setup()
  api.selectProviderKind('local')
  deps.request = async () => { throw new Error('connection refused') }
  await api.discoverProviderModels()
  assert.match(deps.providerDiscoveryWarning.value, /本地服务器已启动/)
  assert.match(deps.providerDiscoveryWarning.value, /Key 可留空/)
  assert.equal(deps.providerDiscoveryMeta.value, null)
})
