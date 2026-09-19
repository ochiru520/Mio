/** ModelProviders operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import * as settingsApi from '../services/settingsApi.js'

export function useModelProviders(deps) {
  function resetProviderForm() {
    deps.providerForm.value = {
      preset_id: '',
      provider_kind: 'relay',
      provider_protocol: 'openai',
      default_api_mode: 'auto',
      provider_name: '',
      model: '',
      base_url: '',
      api_key: '',
      supports_vision: false,
      cached_input_price_cny_per_million: 0,
      input_price_cny_per_million: 0,
      output_price_cny_per_million: 0,
    }
    deps.discoveredModels.value = []
    deps.providerDiscoveryWarning.value = ''
    deps.providerDiscoveryMeta.value = null
  }
  function selectProviderKind(kind) {
    deps.providerForm.value.provider_kind = kind
    deps.providerForm.value.preset_id = ''
    deps.providerForm.value.provider_protocol = kind === 'official' ? 'openai' : 'openai'
    deps.providerForm.value.default_api_mode = 'auto'
    deps.providerForm.value.base_url = kind === 'official' ? 'https://api.openai.com/v1' : ''
    if (kind === 'official') deps.providerForm.value.provider_name = 'OpenAI 官方'
    deps.discoveredModels.value = []
    deps.providerDiscoveryWarning.value = ''
  }
  function selectProviderPreset(presetId) {
    const preset = deps.providerPresets.value.find((item) => item.id === presetId)
    if (!preset) return
    deps.providerForm.value.preset_id = preset.id
    deps.providerForm.value.provider_kind = preset.kind || 'official'
    deps.providerForm.value.provider_protocol = preset.protocol || 'openai'
    deps.providerForm.value.default_api_mode = preset.default_api_mode || 'auto'
    deps.providerForm.value.provider_name = preset.name || ''
    deps.providerForm.value.base_url = preset.base_url || ''
    deps.discoveredModels.value = []
    deps.providerDiscoveryWarning.value = preset.note || ''
    deps.providerDiscoveryMeta.value = null
  }
  function selectOfficialProvider(protocol) {
    deps.providerForm.value.provider_kind = 'official'
    deps.providerForm.value.provider_protocol = protocol
    deps.providerForm.value.base_url = protocol === 'deepseek'
      ? 'https://api.deepseek.com/v1'
      : 'https://api.openai.com/v1'
    deps.providerForm.value.provider_name = protocol === 'deepseek' ? 'DeepSeek 官方' : 'OpenAI 官方'
    deps.discoveredModels.value = []
    deps.providerDiscoveryWarning.value = ''
  }
  async function openProviderPanel() {
    deps.activeView.value = 'settings'
    deps.activeSettingsSection.value = 'models'
    localStorage.setItem('mio_settings_section', 'models')
    resetProviderForm()
    deps.showProviderPanel.value = true
  }
  async function saveProvider() {
    if (deps.providerBusy.value) return
    deps.providerBusy.value = true
    deps.errorMessage.value = ''
    try {
      const selected = deps.discoveredModels.value.filter((item) => item.selected)
      if (deps.discoveredModels.value.length && !selected.length && !deps.providerForm.value.model.trim()) {
        throw new Error('至少选择一个模型版本。')
      }
      const requestedModels = selected.length ? selected : [{
        model: deps.providerForm.value.model,
        display_name: deps.providerForm.value.model,
        family_name: deps.providerForm.value.model,
        variant_name: '',
        cached_input_price_cny_per_million: deps.providerForm.value.cached_input_price_cny_per_million,
        input_price_cny_per_million: deps.providerForm.value.input_price_cny_per_million,
        output_price_cny_per_million: deps.providerForm.value.output_price_cny_per_million,
      }]
      const created = await deps.request('/api/agent/providers', {
        method: 'POST',
        body: JSON.stringify({
          provider_name: deps.providerForm.value.provider_name,
          provider_kind: deps.providerForm.value.provider_kind,
          provider_protocol: deps.providerForm.value.provider_protocol,
          default_api_mode: deps.providerForm.value.default_api_mode,
          preset_id: deps.providerForm.value.preset_id,
          auth_scheme: deps.providerDiscoveryMeta.value?.auth_scheme || 'auto',
          base_url: deps.providerDiscoveryMeta.value?.resolved_api_base_url || deps.providerForm.value.base_url,
          api_key: deps.providerForm.value.api_key,
          models: requestedModels.map((item) => ({
            display_name: item.display_name || item.model,
            family_name: item.family_name || item.model,
            variant_name: item.variant_name || '',
            model: item.model,
            supports_vision: Boolean(item.supports_vision ?? deps.providerForm.value.supports_vision),
            cached_input_price_cny_per_million: item.cached_input_price_cny_per_million || 0,
            input_price_cny_per_million: item.input_price_cny_per_million || 0,
            output_price_cny_per_million: item.output_price_cny_per_million || 0,
            pricing_source: item.pricing_source || '',
            api_mode: item.api_mode || '',
          })),
        }),
      })
      const lastModel = created.models?.at(-1)
      if (!lastModel) throw new Error('供应商没有保存任何模型。')
      await deps.loadBootstrap({ quiet: true })
      deps.selectedModel.value = lastModel.id
      deps.persistSelectedModel()
      deps.showProviderPanel.value = false
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.providerBusy.value = false
    }
  }
  async function discoverProviderModels() {
    if (deps.providerDiscoveryBusy.value) return
    deps.providerDiscoveryBusy.value = true
    deps.errorMessage.value = ''
    deps.providerDiscoveryWarning.value = ''
    deps.providerDiscoveryMeta.value = null
    try {
      const data = await deps.request('/api/agent/models/discover', {
        method: 'POST',
        body: JSON.stringify({
          base_url: deps.providerForm.value.base_url,
          api_key: deps.providerForm.value.api_key,
          provider_kind: deps.providerForm.value.provider_kind,
          provider_protocol: deps.providerForm.value.provider_protocol,
          default_api_mode: deps.providerForm.value.default_api_mode,
          preset_id: deps.providerForm.value.preset_id,
        }),
      })
      deps.discoveredModels.value = (data.models || []).map((model) => ({
        ...model,
        selected: model.api_supported !== false && /[-_](flash|pro|sol|luna)$/i.test(model.model),
      }))
      deps.providerDiscoveryWarning.value = data.warning || ''
      deps.providerDiscoveryMeta.value = {
        resolved_api_base_url: data.resolved_api_base_url || data.resolved_base_url || deps.providerForm.value.base_url,
        models_endpoint: data.models_endpoint || '',
        auth_scheme: data.auth_scheme || 'bearer',
        default_api_mode: data.default_api_mode || deps.providerForm.value.default_api_mode,
        attempts: data.attempts || [],
      }
      if (!deps.discoveredModels.value.length) throw new Error('这个供应商没有返回可用模型。')
    } catch (error) {
      deps.providerDiscoveryMeta.value = null
      const manualHint = '仍可在下方手动填写模型 ID；实际聊天需要有效的 API Key。'
      deps.providerDiscoveryWarning.value = `${error.message} ${manualHint}`
    } finally {
      deps.providerDiscoveryBusy.value = false
    }
  }
  async function testWebSearch() {
    if (deps.webSearchTestBusy.value) return
    deps.webSearchTestBusy.value = true
    deps.webSearchTestResult.value = null
    try {
      deps.webSearchTestResult.value = await settingsApi.testWebSearch(deps.webSearchTestQuery.value)
    } catch (error) {
      deps.webSearchTestResult.value = { ok: false, message: error.message, sources: [], attempts: [] }
    } finally {
      deps.webSearchTestBusy.value = false
    }
  }
  async function deleteProvider(model) {
    if (!model.is_custom || deps.providerBusy.value) return
    deps.providerBusy.value = true
    deps.errorMessage.value = ''
    try {
      await deps.request(`/api/agent/models/${encodeURIComponent(model.id)}`, { method: 'DELETE' })
      if (deps.selectedModel.value === model.id) deps.selectedModel.value = 'auto'
      deps.persistSelectedModel()
      await deps.loadBootstrap({ quiet: true })
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.providerBusy.value = false
    }
  }
  async function deleteProviderGroup(group) {
    if (deps.providerBusy.value || !group?.provider_id) return
    const hasBuiltInModels = group.models.some((model) => !model.is_custom)
    const confirmed = await deps.showAppConfirm({
      title: `删除供应商“${group.provider}”？`,
      message: hasBuiltInModels
        ? `将隐藏这个内置供应商及其 ${group.models.length} 个模型，并清理失效引用。之后可以在这里恢复。`
        : `将删除这个供应商及其 ${group.models.length} 个本机模型配置。此操作不能撤销。`,
      confirmText: '删除供应商',
      danger: true,
    })
    if (!confirmed) return
    deps.providerBusy.value = true
    deps.errorMessage.value = ''
    try {
      const result = await deps.request(`/api/agent/providers/${encodeURIComponent(group.provider_id)}`, { method: 'DELETE' })
      const deletedIds = new Set(result.deleted_model_ids || group.models.map((model) => model.id))
      if (deletedIds.has(deps.selectedModel.value)) deps.selectedModel.value = 'auto'
      deps.persistSelectedModel()
      if (deletedIds.has(deps.chatSettingsDraft.value.model_id)) deps.chatSettingsDraft.value.model_id = 'auto'
      await deps.loadBootstrap({ quiet: true })
      deps.showSettingsFeedback('models', 'success', `已删除供应商“${group.provider}”`)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.providerBusy.value = false
    }
  }
  async function restoreProvider(provider) {
    if (deps.providerBusy.value || !provider?.provider_id) return
    deps.providerBusy.value = true
    deps.errorMessage.value = ''
    try {
      await deps.request(`/api/agent/providers/${encodeURIComponent(provider.provider_id)}/restore`, {
        method: 'POST',
        body: '{}',
      })
      await deps.loadBootstrap({ quiet: true })
      deps.showSettingsFeedback('models', 'success', `已恢复内置供应商“${provider.display_name}”`)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.providerBusy.value = false
    }
  }
  async function testModel(model) {
    if (deps.modelTestBusy.value) return
    deps.modelTestBusy.value = model.id
    deps.modelTestStatus.value = { ...deps.modelTestStatus.value, [model.id]: { state: 'testing', text: '测试中' } }
    try {
      const result = await deps.request(`/api/agent/models/${encodeURIComponent(model.id)}/test`, {
        method: 'POST',
        body: '{}',
      })
      deps.modelTestStatus.value = { ...deps.modelTestStatus.value, [model.id]: { state: 'success', text: result.message } }
    } catch (error) {
      deps.modelTestStatus.value = { ...deps.modelTestStatus.value, [model.id]: { state: 'error', text: deps.modelRequestError(error) } }
    } finally {
      deps.modelTestBusy.value = ''
    }
  }
  return { resetProviderForm, selectProviderKind, selectProviderPreset, selectOfficialProvider, openProviderPanel, saveProvider, discoverProviderModels, testWebSearch, deleteProvider, deleteProviderGroup, restoreProvider, testModel }
}
